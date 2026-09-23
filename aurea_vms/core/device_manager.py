"""Gestion de dispositivos: captura de un frame de referencia por RTSP y
descubrimiento/consulta ONVIF.

Todas las funciones de este modulo son bloqueantes (hacen I/O de red) y deben
llamarse desde un worker thread, nunca desde el hilo principal de Qt.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

import cv2
import numpy as np

from aurea_vms.config import resources
from aurea_vms.models.device import Device

logger = logging.getLogger(__name__)

OPEN_TIMEOUT_MS = 5000
READ_TIMEOUT_MS = 5000
SNAPSHOT_TIMEOUT_S = 6.0

# Techo de sondas RTSP concurrentes. Cada grab_snapshot deja un thread
# daemon vivo hasta que su VideoCapture se rinde solo, y eso puede tardar
# ~30s contra un host inalcanzable pese a los timeouts del backend (ver el
# comentario de _open_and_grab) -- mucho despues de que grab_snapshot ya
# devolvio por timeout. Sin techo, cada reintento del usuario suma uno y no
# los cuenta nadie: apretando "actualizar vista previa" contra una camara
# muerta se juntan decenas.
MAX_CONCURRENT_PROBES = 4
_probe_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PROBES)


@dataclass(frozen=True)
class OnvifDiscoveryResult:
    ip: str
    port: int
    xaddr: str
    manufacturer: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None


@dataclass(frozen=True)
class OnvifProfileInfo:
    rtsp_main_url: str
    rtsp_sub_url: str | None
    has_ptz: bool
    channels: tuple[OnvifChannelInfo, ...] = ()


@dataclass(frozen=True)
class OnvifChannelInfo:
    channel: int
    name: str
    rtsp_main_url: str
    rtsp_sub_url: str | None
    has_ptz: bool = False


def build_authenticated_url(rtsp_url: str, username: str, password: str) -> str:
    """Inserta usuario:contraseña (url-encodeados) en la URL RTSP si hay
    username y la URL todavia no trae credenciales embebidas. Los perfiles
    ONVIF devuelven la URL sin credenciales, pero OpenCV/FFmpeg solo aceptan
    autenticacion embebida en la URL (rtsp://user:pass@host/...)."""
    if not username:
        return rtsp_url
    parts = urlsplit(rtsp_url)
    if "@" in parts.netloc:
        return rtsp_url

    credentials = quote(username, safe="")
    if password:
        credentials += f":{quote(password, safe='')}"
    netloc = f"{credentials}@{parts.netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _open_and_grab(rtsp_url: str, result_queue: queue.Queue[tuple[np.ndarray | None, str]]) -> None:
    """Corre en un thread daemon. CAP_PROP_OPEN_TIMEOUT_MSEC/READ_TIMEOUT_MSEC
    del backend FFmpeg de OpenCV no son confiables para hosts inalcanzables
    (se observo que igual tarda ~30s pese a configurarlos en 5s), asi que
    este thread puede seguir vivo un buen rato despues de que el caller ya
    se rindio -- por eso la ranura se libera aca y no alla."""
    try:
        cap = cv2.VideoCapture()
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS)
        try:
            if not cap.open(rtsp_url, cv2.CAP_FFMPEG):
                result_queue.put((None, "No se pudo abrir el stream"))
                return
            ok, frame = cap.read()
            if ok and frame is not None:
                result_queue.put((frame, "OK"))
            else:
                result_queue.put((None, "Se conecto pero no llego ningun frame"))
        except cv2.error as exc:
            result_queue.put((None, f"Error de OpenCV: {exc}"))
        finally:
            cap.release()
    finally:
        _probe_slots.release()


def grab_snapshot(
    device: Device, timeout_s: float = SNAPSHOT_TIMEOUT_S
) -> tuple[np.ndarray | None, str]:
    """Devuelve un frame de referencia para dibujar ROI/lineas de config.

    Si la camara ya esta siendo transmitida (Vista en Vivo u otra analitica
    activa), reusa ese frame al instante sin abrir una conexion nueva.
    """
    from aurea_vms.core.stream_manager import stream_manager

    worker = stream_manager.get_worker(device.id)
    if worker is not None:
        frame = worker.get_latest_frame()
        if frame is not None:
            return frame, "OK (stream activo)"

    if not _probe_slots.acquire(blocking=False):
        logger.warning(
            "Cámara %s: hay %d sondas RTSP en curso, no se abre otra",
            device.id,
            MAX_CONCURRENT_PROBES,
        )
        return None, "Hay demasiadas pruebas en curso, esperá unos segundos"

    url = build_authenticated_url(device.rtsp_main_url, device.username, device.password)
    result_queue: queue.Queue[tuple[np.ndarray | None, str]] = queue.Queue(maxsize=1)
    # La ranura la libera el propio thread (ver _open_and_grab), no este
    # bloque: el thread sobrevive al timeout de abajo.
    threading.Thread(target=_open_and_grab, args=(url, result_queue), daemon=True).start()

    try:
        return result_queue.get(timeout=timeout_s)
    except queue.Empty:
        return None, f"Timeout: no respondió en {timeout_s:.0f}s"


def _parse_onvif_scopes(scopes) -> tuple[str | None, str | None, str | None, str | None]:
    """Extrae fabricante/modelo/version/serie de los scopes WS-Discovery
    (ej. "onvif://www.onvif.org/hardware/IPC3232SB-ADZK-I0") -- no hace
    falta autenticarse contra el dispositivo para esto, ya viene en el
    anuncio de WS-Discovery."""
    fields = {"manufacturer": None, "model": None, "firmware_version": None, "serial_number": None}
    prefixes = {
        "onvif://www.onvif.org/hardware/": "model",
        "onvif://www.onvif.org/manufacturer/": "manufacturer",
        "onvif://www.onvif.org/version/": "firmware_version",
        "onvif://www.onvif.org/serial/": "serial_number",
    }
    for scope in scopes or []:
        text = unquote(str(scope))
        for prefix, field in prefixes.items():
            if text.startswith(prefix):
                fields[field] = text[len(prefix) :] or None
    return (
        fields["manufacturer"],
        fields["model"],
        fields["firmware_version"],
        fields["serial_number"],
    )


def discover_onvif(timeout: float = 3.0) -> list[OnvifDiscoveryResult]:
    """Escanea la LAN por WS-Discovery. Devuelve [] si no aparece nadie o si
    la red no permite multicast (comun en VPN o algunas LAN corporativas) —
    en ese caso el dispositivo se puede seguir cargando a mano."""
    from wsdiscovery.discovery import ThreadedWSDiscovery

    wsd = ThreadedWSDiscovery()
    results: list[OnvifDiscoveryResult] = []
    seen_ips: set[str] = set()

    wsd.start()
    try:
        services = wsd.searchServices(timeout=timeout)
        for service in services:
            manufacturer, model, firmware_version, serial_number = _parse_onvif_scopes(
                service.getScopes()
            )
            for xaddr in service.getXAddrs():
                parsed = urlparse(xaddr)
                if parsed.hostname and parsed.hostname not in seen_ips:
                    seen_ips.add(parsed.hostname)
                    results.append(
                        OnvifDiscoveryResult(
                            ip=parsed.hostname,
                            port=parsed.port or 80,
                            xaddr=xaddr,
                            manufacturer=manufacturer,
                            model=model,
                            firmware_version=firmware_version,
                            serial_number=serial_number,
                        )
                    )
    finally:
        wsd.stop()

    logger.info("Descubrimiento ONVIF: %d dispositivo(s) encontrados", len(results))
    return results


def fetch_onvif_profiles(ip: str, port: int, username: str, password: str) -> OnvifProfileInfo:
    """Consulta el servicio de medios ONVIF y devuelve la URL RTSP principal
    (primer perfil) y sub (segundo perfil, si existe). Lanza excepcion si
    las credenciales son invalidas o el dispositivo no responde."""
    from onvif import ONVIFCamera

    cam = ONVIFCamera(ip, port, username, password, **resources.onvif_camera_kwargs())
    media = cam.create_media_service()
    profiles = media.GetProfiles()
    if not profiles:
        raise RuntimeError("El dispositivo no reporto perfiles de medios")

    def stream_uri(profile) -> str:
        request = media.create_type("GetStreamUri")
        request.StreamSetup = {
            "Stream": "RTP-Unicast",
            "Transport": {"Protocol": "RTSP"},
        }
        request.ProfileToken = profile.token
        return media.GetStreamUri(request).Uri

    grouped_profiles: dict[str, list] = {}
    group_order: list[str] = []
    for profile in profiles:
        configuration = getattr(profile, "VideoSourceConfiguration", None)
        parts = [
            getattr(profile, "Name", None),
            getattr(configuration, "Name", None),
            getattr(configuration, "SourceToken", None),
        ]
        text = " ".join(str(part) for part in parts if part)
        key = str(getattr(configuration, "SourceToken", None) or text or len(group_order))
        if key not in grouped_profiles:
            grouped_profiles[key] = []
            group_order.append(key)
        grouped_profiles[key].append(profile)

    profile_rows = []
    used_channels: set[int] = set()
    for index, key in enumerate(group_order, start=1):
        channel_profiles = grouped_profiles[key]
        profile = channel_profiles[0]
        configuration = getattr(profile, "VideoSourceConfiguration", None)
        text = " ".join(
            str(part)
            for part in (
                getattr(profile, "Name", None),
                getattr(configuration, "Name", None),
                getattr(configuration, "SourceToken", None),
            )
            if part
        )
        match = re.search(
            r"(?<!\d)(?:channel|ch|cam|camera|input|video)?[\s_-]*(\d{1,3})(?!\d)",
            text,
            re.I,
        )
        channel = int(match.group(1)) if match else index
        while channel in used_channels:
            channel += 1
        used_channels.add(channel)
        urls = [stream_uri(row) for row in channel_profiles[:2]]
        profile_rows.append(
            OnvifChannelInfo(
                channel=channel,
                name=str(getattr(profile, "Name", None) or f"Canal {channel}"),
                rtsp_main_url=urls[0],
                rtsp_sub_url=urls[1] if len(urls) > 1 else None,
                has_ptz=getattr(profile, "PTZConfiguration", None) is not None,
            )
        )

    first = profile_rows[0]
    return OnvifProfileInfo(
        rtsp_main_url=first.rtsp_main_url,
        rtsp_sub_url=first.rtsp_sub_url,
        has_ptz=first.has_ptz,
        channels=tuple(profile_rows),
    )


def remap_onvif_stream_host(
    info: OnvifProfileInfo, host: str, rtsp_port: int | None = None
) -> OnvifProfileInfo:
    """Adapta las URI RTSP ONVIF a un host/puerto publicados por NAT.

    Muchos NVR devuelven desde ONVIF una URI con su IP privada (por ejemplo
    192.168.x.x). Esa URI funciona dentro de la LAN, pero no desde el VMS
    remoto aunque el puerto haya sido redirigido. Se conserva el path y se
    reemplaza solo host/puerto.
    """
    host = host.strip()
    if not host:
        raise ValueError("El host remoto no puede estar vacío")

    def remap(url: str | None) -> str | None:
        if not url:
            return None
        parts = urlsplit(url)
        netloc = host
        if rtsp_port:
            netloc = f"{host}:{rtsp_port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    channels = tuple(
        OnvifChannelInfo(
            channel=row.channel,
            name=row.name,
            rtsp_main_url=remap(row.rtsp_main_url) or row.rtsp_main_url,
            rtsp_sub_url=remap(row.rtsp_sub_url),
            has_ptz=row.has_ptz,
        )
        for row in info.channels
    )
    return OnvifProfileInfo(
        rtsp_main_url=remap(info.rtsp_main_url) or info.rtsp_main_url,
        rtsp_sub_url=remap(info.rtsp_sub_url),
        has_ptz=info.has_ptz,
        channels=channels,
    )


def reboot_device(ip: str, port: int, username: str, password: str) -> None:
    """Reinicia el dispositivo via ONVIF (SystemReboot). Operacion estandar
    del servicio de gestion de dispositivos -- el equipo va a estar fuera
    de linea unos segundos/minutos mientras arranca de nuevo."""
    from onvif import ONVIFCamera

    cam = ONVIFCamera(ip, port, username, password, **resources.onvif_camera_kwargs())
    devicemgmt = cam.create_devicemgmt_service()
    devicemgmt.SystemReboot()
    logger.info("Dispositivo %s: reinicio solicitado via ONVIF", ip)
