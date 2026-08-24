"""Plantillas de URL RTSP por tipo de dispositivo, para precargar el
formulario de alta manual (sin ONVIF). Son un punto de partida editable,
no una garantia -- cada fabricante/firmware puede variar. La forma
confiable de obtener la URL exacta sigue siendo el descubrimiento ONVIF
(OnvifDiscoveryDialog), que la consulta directo al dispositivo.

Patrones:
- IPC (camara IP standalone): confirmado contra hardware Uniview real
  durante el desarrollo (rtsp://ip:puerto/media/videoN).
- NVR/XVR (grabador con canales): patron unicast generico por canal.
"""

from __future__ import annotations

DEVICE_TYPES = ["ipc", "nvr", "xvr"]

DEVICE_TYPE_LABELS = {
    "ipc": "Cámara IP (IPC)",
    "nvr": "NVR",
    "xvr": "XVR",
}


def build_rtsp_urls(device_type: str, ip: str, port: int, channel: int = 1) -> tuple[str, str]:
    """Devuelve (url_principal, url_sub) segun el tipo de dispositivo."""
    if device_type == "ipc":
        return (
            f"rtsp://{ip}:{port}/media/video1",
            f"rtsp://{ip}:{port}/media/video2",
        )
    # NVR / XVR: patron unicast por canal
    return (
        f"rtsp://{ip}:{port}/unicast/c{channel}/s0/live",
        f"rtsp://{ip}:{port}/unicast/c{channel}/s1/live",
    )
