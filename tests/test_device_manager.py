from __future__ import annotations

from aurea_vms.core.device_manager import _parse_onvif_scopes, build_authenticated_url


class TestBuildAuthenticatedUrl:
    def test_sin_usuario_no_toca_la_url(self):
        url = "rtsp://192.168.1.10:554/media/video1"
        assert build_authenticated_url(url, "", "loquesea") == url

    def test_credenciales_ya_embebidas_no_se_duplican(self):
        url = "rtsp://admin:clave@192.168.1.10:554/media/video1"
        assert build_authenticated_url(url, "otro", "user") == url

    def test_inserta_usuario_y_password(self):
        url = build_authenticated_url("rtsp://192.168.1.10:554/live", "admin", "clave123")
        assert url == "rtsp://admin:clave123@192.168.1.10:554/live"

    def test_usuario_sin_password(self):
        url = build_authenticated_url("rtsp://192.168.1.10:554/live", "admin", "")
        assert url == "rtsp://admin@192.168.1.10:554/live"

    def test_caracteres_especiales_quedan_url_encodeados(self):
        # "@" y ":" en la password romperian el parseo del netloc si no se escapan.
        url = build_authenticated_url("rtsp://192.168.1.10:554/live", "admin", "p@ss:w/1")
        assert url == "rtsp://admin:p%40ss%3Aw%2F1@192.168.1.10:554/live"

    def test_preserva_query_string(self):
        url = build_authenticated_url("rtsp://10.0.0.1/live?channel=1", "u", "p")
        assert url == "rtsp://u:p@10.0.0.1/live?channel=1"


class TestParseOnvifScopes:
    def test_extrae_los_cuatro_campos(self):
        scopes = [
            "onvif://www.onvif.org/manufacturer/UNIVIEW",
            "onvif://www.onvif.org/hardware/IPC3232SB-ADZK-I0",
            "onvif://www.onvif.org/version/B3223P30",
            "onvif://www.onvif.org/serial/210235C3EJ1234",
            "onvif://www.onvif.org/location/city/unknown",  # ignorado
        ]
        manufacturer, model, firmware, serial = _parse_onvif_scopes(scopes)
        assert manufacturer == "UNIVIEW"
        assert model == "IPC3232SB-ADZK-I0"
        assert firmware == "B3223P30"
        assert serial == "210235C3EJ1234"

    def test_scopes_url_encodeados_se_decodifican(self):
        scopes = ["onvif://www.onvif.org/manufacturer/Marca%20Con%20Espacios"]
        manufacturer, *_ = _parse_onvif_scopes(scopes)
        assert manufacturer == "Marca Con Espacios"

    def test_sin_scopes_devuelve_none(self):
        assert _parse_onvif_scopes([]) == (None, None, None, None)
        assert _parse_onvif_scopes(None) == (None, None, None, None)

    def test_valor_vacio_es_none(self):
        scopes = ["onvif://www.onvif.org/serial/"]
        *_, serial = _parse_onvif_scopes(scopes)
        assert serial is None
