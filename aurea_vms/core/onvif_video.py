"""Parametros de video de la camara por ONVIF (servicio Media, Profile S):
codec, resolucion, FPS, bitrate, cuadros entre dos I (GOP) y calidad de
cada perfil (principal, secundario...). Sistema > Audio y Video > Video los
muestra y los cambia.

Solo Media 1, que es lo que trae onvif-zeep: H.264, MJPEG y MPEG-4. H.265 y
el tipo de bitrate (CBR/VBR) son del servicio Media 2 y quedan afuera: una
camara en H.265 informa por Media 1 lo que ella decida.

Todas las funciones hacen I/O de red: se llaman desde un worker, nunca desde
el hilo de Qt (igual que device_manager y ptz_control).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from aurea_vms.config import resources

logger = logging.getLogger(__name__)

# Codec de Media 1 -> nombre para la UI, en el orden en que se ofrecen.
CODECS = {"H264": "H.264", "JPEG": "MJPEG", "MPEG4": "MPEG-4"}
# Los que tienen cuadros I y P (GOP); MJPEG son todos cuadros completos.
GOP_CODECS = ("H264", "MPEG4")
# Perfil del codec que se pone si la camara no tenia ese codec configurado.
_DEFAULT_CODEC_PROFILE = {"H264": ("H264Profile", "Main"), "MPEG4": ("Mpeg4Profile", "SP")}


@dataclass(frozen=True)
class CodecOptions:
    """Lo que la camara acepta para un codec. Un rango None es que la
    camara no lo informa."""

    resolutions: tuple[tuple[int, int], ...]
    fps: tuple[int, int] | None = None
    gov_length: tuple[int, int] | None = None
    bitrate_kbps: tuple[int, int] | None = None


@dataclass(frozen=True)
class VideoStream:
    """Un perfil de video tal como esta configurado en la camara."""

    profile_token: str
    profile_name: str
    config_token: str
    codec: str  # clave de CODECS
    width: int
    height: int
    fps: int | None
    bitrate_kbps: int | None
    gov_length: int | None  # solo GOP_CODECS
    quality: float | None
    quality_range: tuple[float, float] | None
    options: dict[str, CodecOptions] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoSettings:
    """Lo que se le pide a la camara para un perfil."""

    codec: str
    width: int
    height: int
    fps: int
    bitrate_kbps: int
    gov_length: int | None = None
    quality: float | None = None


def _media_service(ip: str, port: int, username: str, password: str):
    from onvif import ONVIFCamera

    cam = ONVIFCamera(ip, port, username, password, **resources.onvif_camera_kwargs())
    return cam.create_media_service()


def fetch_video_streams(
    ip: str, port: int, username: str, password: str, channel: int = 1
) -> list[VideoStream]:
    """Los perfiles con video del canal `channel`, en el orden de la camara:
    el primero es el principal, el que mira el VMS (ver
    device_manager.fetch_onvif_profiles). En una camara IP el canal es 1; en
    un NVR, la fuente de video numero `channel` en el orden en que la
    reporta. Lanza RuntimeError si no hay perfiles de video o ese canal."""
    media = _media_service(ip, port, username, password)
    groups: dict[str, list] = {}
    for profile in media.GetProfiles() or []:
        if getattr(profile, "VideoEncoderConfiguration", None) is None:
            continue
        source = getattr(getattr(profile, "VideoSourceConfiguration", None), "SourceToken", None)
        groups.setdefault(str(source), []).append(profile)
    channels = list(groups.values())
    if not channels:
        raise RuntimeError("La cámara no reportó perfiles de video")
    if not 1 <= channel <= len(channels):
        raise RuntimeError(f"La cámara no tiene el canal {channel} (reporta {len(channels)})")
    return [_stream(media, profile) for profile in channels[channel - 1]]


def _stream(media, profile) -> VideoStream:
    config = profile.VideoEncoderConfiguration
    options = media.GetVideoEncoderConfigurationOptions(
        {"ConfigurationToken": config.token, "ProfileToken": profile.token}
    )
    codec = str(config.Encoding)
    rate = getattr(config, "RateControl", None)
    gov = getattr(getattr(config, codec, None), "GovLength", None) if codec in GOP_CODECS else None
    quality = getattr(config, "Quality", None)
    return VideoStream(
        profile_token=str(profile.token),
        profile_name=str(getattr(profile, "Name", None) or profile.token),
        config_token=str(config.token),
        codec=codec,
        width=int(config.Resolution.Width),
        height=int(config.Resolution.Height),
        fps=_int_or_none(getattr(rate, "FrameRateLimit", None)),
        bitrate_kbps=_int_or_none(getattr(rate, "BitrateLimit", None)),
        gov_length=_int_or_none(gov),
        quality=float(quality) if quality is not None else None,
        quality_range=_range(getattr(options, "QualityRange", None), float),
        options=_codec_options(options),
    )


def _codec_options(options) -> dict[str, CodecOptions]:
    """Por codec: las resoluciones y los rangos. El bitrate esta en la
    extension de las opciones (JpegOptions2, H264Options2...)."""
    extension = getattr(options, "Extension", None)
    result: dict[str, CodecOptions] = {}
    for codec in CODECS:
        base = getattr(options, codec, None)
        if base is None:
            continue
        extra = getattr(extension, codec, None)
        result[codec] = CodecOptions(
            resolutions=tuple(
                (int(r.Width), int(r.Height))
                for r in getattr(base, "ResolutionsAvailable", None) or []
            ),
            fps=_range(getattr(base, "FrameRateRange", None)),
            gov_length=_range(getattr(base, "GovLengthRange", None)),
            bitrate_kbps=_range(getattr(extra, "BitrateRange", None)),
        )
    return result


def _range(value, cast=int):
    low, high = getattr(value, "Min", None), getattr(value, "Max", None)
    if low is None or high is None:
        return None
    return cast(low), cast(high)


def _int_or_none(value) -> int | None:
    return int(value) if value is not None else None


def apply_video_settings(
    ip: str, port: int, username: str, password: str, config_token: str, settings: VideoSettings
) -> None:
    """Cambia el encoder `config_token` y lo deja guardado en la camara
    (ForcePersistence). Se parte de la configuracion actual, asi lo que no
    se toca (multicast, timeout de sesion...) queda como estaba. Lo que la
    camara no acepte vuelve como excepcion de ONVIF, con su mensaje."""
    media = _media_service(ip, port, username, password)
    config = media.GetVideoEncoderConfiguration({"ConfigurationToken": config_token})
    config.Encoding = settings.codec
    config.Resolution.Width = settings.width
    config.Resolution.Height = settings.height
    if getattr(config, "RateControl", None) is None:
        config.RateControl = {
            "FrameRateLimit": settings.fps,
            "EncodingInterval": 1,
            "BitrateLimit": settings.bitrate_kbps,
        }
    else:
        config.RateControl.FrameRateLimit = settings.fps
        config.RateControl.BitrateLimit = settings.bitrate_kbps
    if settings.quality is not None:
        config.Quality = settings.quality
    if settings.codec in GOP_CODECS and settings.gov_length is not None:
        codec_config = getattr(config, settings.codec, None)
        if codec_config is None:
            profile_field, profile = _DEFAULT_CODEC_PROFILE[settings.codec]
            setattr(
                config,
                settings.codec,
                {"GovLength": settings.gov_length, profile_field: profile},
            )
        else:
            codec_config.GovLength = settings.gov_length
    media.SetVideoEncoderConfiguration({"Configuration": config, "ForcePersistence": True})
    logger.info(
        "Video %s (%s): %s %dx%d %d fps %d kbps GOP %s",
        ip,
        config_token,
        settings.codec,
        settings.width,
        settings.height,
        settings.fps,
        settings.bitrate_kbps,
        settings.gov_length,
    )
