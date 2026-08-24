"""Control PTZ real via ONVIF (mover continuo + stop). Todas las funciones
son bloqueantes (hacen I/O de red) y deben llamarse desde un worker
thread, nunca desde el hilo principal de Qt -- igual que device_manager.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_SPEED = 0.5


def _ptz_service_and_token(ip: str, port: int, username: str, password: str):
    from onvif import ONVIFCamera

    cam = ONVIFCamera(ip, port, username, password)
    media = cam.create_media_service()
    profiles = media.GetProfiles()
    if not profiles:
        raise RuntimeError("El dispositivo no reportó perfiles de medios")
    return cam.create_ptz_service(), profiles[0].token


def continuous_move(
    ip: str, port: int, username: str, password: str, pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0
) -> None:
    """Empieza a mover la camara a velocidad constante en la direccion
    dada (-1.0 a 1.0 por eje). Hay que llamar a stop() para detenerla."""
    ptz, token = _ptz_service_and_token(ip, port, username, password)
    request = ptz.create_type("ContinuousMove")
    request.ProfileToken = token
    request.Velocity = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}
    ptz.ContinuousMove(request)
    logger.info("PTZ %s: continuous move pan=%.2f tilt=%.2f zoom=%.2f", ip, pan, tilt, zoom)


def stop(ip: str, port: int, username: str, password: str) -> None:
    ptz, token = _ptz_service_and_token(ip, port, username, password)
    request = ptz.create_type("Stop")
    request.ProfileToken = token
    request.PanTilt = True
    request.Zoom = True
    ptz.Stop(request)
    logger.info("PTZ %s: stop", ip)
