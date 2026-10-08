"""Parametros de video por ONVIF (2026-10-08): leer los perfiles de un canal
con sus opciones y cambiar un encoder dejandolo guardado en la camara."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from aurea_vms.core import onvif_video
from aurea_vms.core.onvif_video import CodecOptions, VideoSettings


def _config(token, encoding="H264", width=1920, height=1080, fps=25, kbps=4096, gov=50):
    return NS(
        token=token,
        Name=token,
        Encoding=encoding,
        Resolution=NS(Width=width, Height=height),
        Quality=5.0,
        RateControl=NS(FrameRateLimit=fps, EncodingInterval=1, BitrateLimit=kbps),
        H264=NS(GovLength=gov, H264Profile="Main") if encoding == "H264" else None,
        MPEG4=None,
        Multicast=NS(Port=0),
    )


def _profile(token, config, source="fuente1"):
    return NS(
        token=token,
        Name=f"Perfil {token}",
        VideoSourceConfiguration=NS(SourceToken=source),
        VideoEncoderConfiguration=config,
    )


OPTIONS = NS(
    QualityRange=NS(Min=1, Max=6),
    H264=NS(
        ResolutionsAvailable=[NS(Width=1920, Height=1080), NS(Width=1280, Height=720)],
        FrameRateRange=NS(Min=1, Max=30),
        GovLengthRange=NS(Min=1, Max=150),
    ),
    JPEG=NS(ResolutionsAvailable=[NS(Width=1280, Height=720)], FrameRateRange=NS(Min=1, Max=15)),
    MPEG4=None,
    Extension=NS(H264=NS(BitrateRange=NS(Min=64, Max=8192)), JPEG=None),
)


class _Media:
    def __init__(self, profiles=(), configs=None):
        self.profiles = list(profiles)
        self.configs = configs or {}
        self.option_requests: list[dict] = []
        self.set_requests: list[dict] = []

    def GetProfiles(self):  # noqa: N802 - nombre de ONVIF
        return self.profiles

    def GetVideoEncoderConfigurationOptions(self, request):  # noqa: N802
        self.option_requests.append(request)
        return OPTIONS

    def GetVideoEncoderConfiguration(self, request):  # noqa: N802
        return self.configs[request["ConfigurationToken"]]

    def SetVideoEncoderConfiguration(self, request):  # noqa: N802
        self.set_requests.append(request)


@pytest.fixture()
def media(monkeypatch):
    servicio = _Media()
    monkeypatch.setattr(onvif_video, "_media_service", lambda *_a: servicio)
    return servicio


class TestLeer:
    def test_principal_y_secundario_con_sus_opciones(self, media):
        media.profiles = [
            _profile("p1", _config("enc1")),
            _profile("p2", _config("enc2", "JPEG", 640, 360, fps=10, kbps=512)),
            # Un perfil solo de audio no es un stream de video.
            NS(token="audio", VideoSourceConfiguration=None, VideoEncoderConfiguration=None),
        ]

        principal, secundario = onvif_video.fetch_video_streams("10.0.0.5", 80, "u", "c")

        assert (principal.codec, principal.width, principal.height) == ("H264", 1920, 1080)
        assert (principal.fps, principal.bitrate_kbps, principal.gov_length) == (25, 4096, 50)
        assert principal.config_token == "enc1" and principal.profile_name == "Perfil p1"
        assert principal.quality == 5.0 and principal.quality_range == (1.0, 6.0)
        assert principal.options["H264"] == CodecOptions(
            resolutions=((1920, 1080), (1280, 720)),
            fps=(1, 30),
            gov_length=(1, 150),
            bitrate_kbps=(64, 8192),
        )
        assert principal.options["JPEG"].bitrate_kbps is None
        assert "MPEG4" not in principal.options
        assert (secundario.codec, secundario.gov_length) == ("JPEG", None)
        assert media.option_requests[0] == {"ConfigurationToken": "enc1", "ProfileToken": "p1"}

    def test_en_un_nvr_cada_canal_es_una_fuente(self, media):
        media.profiles = [
            _profile("c1", _config("e1"), source="A"),
            _profile("c2", _config("e2", width=1280, height=720), source="B"),
        ]

        (stream,) = onvif_video.fetch_video_streams("nvr", 80, "u", "c", channel=2)

        assert stream.config_token == "e2" and stream.width == 1280

    def test_un_canal_que_no_existe(self, media):
        media.profiles = [_profile("p1", _config("enc1"))]

        with pytest.raises(RuntimeError, match="canal 3"):
            onvif_video.fetch_video_streams("ip", 80, "u", "c", channel=3)

    def test_sin_perfiles_de_video(self, media):
        with pytest.raises(RuntimeError, match="perfiles de video"):
            onvif_video.fetch_video_streams("ip", 80, "u", "c")


class TestAplicar:
    def test_cambia_lo_pedido_y_lo_deja_guardado(self, media):
        config = _config("enc1")
        media.configs = {"enc1": config}

        onvif_video.apply_video_settings(
            "ip", 80, "u", "c", "enc1", VideoSettings("H264", 1280, 720, 15, 2048, 30, 4.0)
        )

        (request,) = media.set_requests
        assert request["ForcePersistence"] is True and request["Configuration"] is config
        assert (config.Resolution.Width, config.Resolution.Height) == (1280, 720)
        assert (config.RateControl.FrameRateLimit, config.RateControl.BitrateLimit) == (15, 2048)
        assert config.H264.GovLength == 30 and config.Quality == 4.0
        assert config.Multicast == NS(Port=0)  # lo que no se toca queda

    def test_de_mjpeg_a_h264_arma_el_gop(self, media):
        config = _config("enc1", "JPEG")
        media.configs = {"enc1": config}

        onvif_video.apply_video_settings(
            "ip", 80, "u", "c", "enc1", VideoSettings("H264", 1920, 1080, 25, 4096, 50)
        )

        assert config.Encoding == "H264"
        assert config.H264 == {"GovLength": 50, "H264Profile": "Main"}
        assert config.Quality == 5.0  # sin calidad pedida, no se toca

    def test_mjpeg_no_manda_gop(self, media):
        config = _config("enc1")
        media.configs = {"enc1": config}

        onvif_video.apply_video_settings(
            "ip", 80, "u", "c", "enc1", VideoSettings("JPEG", 1280, 720, 10, 1024)
        )

        assert config.Encoding == "JPEG" and config.H264.GovLength == 50

    def test_sin_control_de_tasa_lo_crea(self, media):
        config = _config("enc1")
        config.RateControl = None
        media.configs = {"enc1": config}

        onvif_video.apply_video_settings(
            "ip", 80, "u", "c", "enc1", VideoSettings("H264", 1920, 1080, 20, 3000, 40)
        )

        assert config.RateControl == {
            "FrameRateLimit": 20,
            "EncodingInterval": 1,
            "BitrateLimit": 3000,
        }


def test_se_conecta_con_las_credenciales_de_la_camara(monkeypatch):
    import onvif

    conexiones: list[tuple] = []

    class _Camara:
        def __init__(self, *args, **kwargs):
            conexiones.append(args)

        def create_media_service(self):
            return "media"

    monkeypatch.setattr(onvif, "ONVIFCamera", _Camara)

    assert onvif_video._media_service("10.0.0.5", 8080, "admin", "clave") == "media"
    assert conexiones == [("10.0.0.5", 8080, "admin", "clave")]
