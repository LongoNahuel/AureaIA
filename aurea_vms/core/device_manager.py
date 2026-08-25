"""Gestion de dispositivos: prueba de conexion RTSP y descubrimiento/consulta ONVIF.

Todas las funciones de este modulo son bloqueantes (hacen I/O de red) y deben
llamarse desde un worker thread, nunca desde el hilo principal de Qt.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

import cv2
import numpy as np

from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DeviceStatusEvent
from aurea_vms.models import repository
from aurea_vms.models.device import Device

logger = logging.getLogger(__name__)

OPEN_TIMEOUT_MS = 5000
READ_TIMEOUT_MS = 5000
CONNECTION_TEST_TIMEOUT_S = 6.0


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


def _open_and_read(rtsp_url: str, result_queue: queue.Queue[tuple[bool, str]]) -> None:
    cap = cv2.VideoCapture()
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS)
    try:
        if not cap.open(rtsp_url, cv2.CAP_FFMPEG):
            result_queue.put((False, "No se pudo abrir el stream"))
            return
        ok, frame = cap.read()
        if ok and frame is not None:
            result_queue.put((True, f"OK ({frame.shape[1]}x{frame.shape[0]})"))
        else:
            result_queue.put((False, "Se conecto pero no llego ningun frame"))
    except cv2.error as exc:
        result_queue.put((False, f"Error de OpenCV: {exc}"))
    finally:
        cap.release()


def test_rtsp_connection(
    rtsp_url: str, timeout_s: float = CONNECTION_TEST_TIMEOUT_S
) -> tuple[bool, str]:
    """Abre el stream y espera un primer frame.

    CAP_PROP_OPEN_TIMEOUT_MSEC/READ_TIMEOUT_MSEC del backend FFmpeg de OpenCV
    no son confiables para hosts inalcanzables (se observo que igual tarda
    ~30s pese a configurarlos en 5s). Por eso el intento real corre en un
    thread daemon aparte y esta funcion devuelve apenas se cumple
    `timeout_s`, sin esperar a que ese thread termine.
    """
    result_queue: queue.Queue[tuple[bool, str]] = queue.Queue(maxsize=1)
    thread = threading.Thread(target=_open_and_read, args=(rtsp_url, result_queue), daemon=True)
    thread.start()

    try:
        return result_queue.get(timeout=timeout_s)
    except queue.Empty:
        return False, f"Timeout: no respondió en {timeout_s:.0f}s"


def _open_and_grab(rtsp_url: str, result_queue: queue.Queue[tuple[np.ndarray | None, str]]) -> None:
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


def grab_snapshot(
    device: Device, timeout_s: float = CONNECTION_TEST_TIMEOUT_S
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

    url = build_authenticated_url(device.rtsp_main_url, device.username, device.password)
    result_queue: queue.Queue[tuple[np.ndarray | None, str]] = queue.Queue(maxsize=1)
    thread = threading.Thread(target=_open_and_grab, args=(url, result_queue), daemon=True)
    thread.start()

    try:
        return result_queue.get(timeout=timeout_s)
    except queue.Empty:
        return None, f"Timeout: no respondió en {timeout_s:.0f}s"


def refresh_device_status(device_id: int) -> bool:
    """Prueba la conexion de un dispositivo guardado, persiste el estado y
    publica el resultado en el event bus. Devuelve True si quedo online."""
    device = repository.get_device(device_id)
    if device is None:
        return False

    url = build_authenticated_url(device.rtsp_main_url, device.username, device.password)
    online, detail = test_rtsp_connection(url)
    repository.update_device_status(device_id, "online" if online else "offline")
    logger.info(
        "Prueba de conexión cámara %s (%s): %s - %s", device_id, device.name, online, detail
    )
    event_bus.device_status.emit(
        DeviceStatusEvent(device_id=device_id, online=online, detail=detail)
    )
    return online


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

    cam = ONVIFCamera(ip, port, username, password)
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

    main_url = stream_uri(profiles[0])
    sub_url = stream_uri(profiles[1]) if len(profiles) > 1 else None
    has_ptz = getattr(profiles[0], "PTZConfiguration", None) is not None

    return OnvifProfileInfo(rtsp_main_url=main_url, rtsp_sub_url=sub_url, has_ptz=has_ptz)


def reboot_device(ip: str, port: int, username: str, password: str) -> None:
    """Reinicia el dispositivo via ONVIF (SystemReboot). Operacion estandar
    del servicio de gestion de dispositivos -- el equipo va a estar fuera
    de linea unos segundos/minutos mientras arranca de nuevo."""
    from onvif import ONVIFCamera

    cam = ONVIFCamera(ip, port, username, password)
    devicemgmt = cam.create_devicemgmt_service()
    devicemgmt.SystemReboot()
    logger.info("Dispositivo %s: reinicio solicitado via ONVIF", ip)
