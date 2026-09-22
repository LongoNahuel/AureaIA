from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest

import aurea_vms.core.device_manager as dm_module
import aurea_vms.core.stream_manager as sm_module
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


class TestTechoDeSondas:
    """Cada grab_snapshot contra un host inalcanzable deja un thread daemon
    vivo hasta que su VideoCapture se rinde solo (~30s pese a los timeouts
    del backend). Sin techo, cada reintento del usuario sumaba uno."""

    @pytest.fixture(autouse=True)
    def _semaforo_limpio(self, monkeypatch):
        """El semáforo es un global de módulo: no puede filtrarse entre tests."""
        monkeypatch.setattr(
            dm_module, "_probe_slots", threading.BoundedSemaphore(dm_module.MAX_CONCURRENT_PROBES)
        )

    def _device(self) -> SimpleNamespace:
        return SimpleNamespace(id=1, rtsp_main_url="rtsp://10.0.0.1/live", username="", password="")

    def test_una_sonda_normal_libera_su_ranura(self, monkeypatch):
        frame = np.zeros((4, 4, 3), dtype=np.uint8)

        def sonda_instantanea(_url, result_queue):
            try:
                result_queue.put((frame, "OK"))
            finally:
                dm_module._probe_slots.release()

        monkeypatch.setattr(dm_module, "_open_and_grab", sonda_instantanea)
        monkeypatch.setattr(sm_module.stream_manager, "get_worker", lambda _id: None)

        for _ in range(dm_module.MAX_CONCURRENT_PROBES + 3):
            resultado, detalle = dm_module.grab_snapshot(self._device(), timeout_s=1.0)
            assert detalle == "OK"
            assert resultado is not None

    def test_con_las_ranuras_tomadas_no_abre_otra_conexion(self, monkeypatch):
        """El caso real: N sondas colgadas contra una cámara muerta."""
        lanzadas: list[int] = []
        monkeypatch.setattr(
            dm_module,
            "_open_and_grab",
            lambda *_a: lanzadas.append(1),  # nunca libera
        )

        for _ in range(dm_module.MAX_CONCURRENT_PROBES):
            dm_module.grab_snapshot(self._device(), timeout_s=0.05)
        assert len(lanzadas) == dm_module.MAX_CONCURRENT_PROBES

        frame, detalle = dm_module.grab_snapshot(self._device(), timeout_s=0.05)

        assert frame is None
        assert "demasiadas" in detalle.lower()
        assert len(lanzadas) == dm_module.MAX_CONCURRENT_PROBES  # no se lanzó una más

    def test_el_stream_activo_no_consume_ranura(self, monkeypatch):
        """Si la cámara ya se está transmitiendo se reusa su frame, sin abrir
        conexión: eso no puede gastar una ranura."""
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        monkeypatch.setattr(
            sm_module.stream_manager,
            "get_worker",
            lambda _id: SimpleNamespace(get_latest_frame=lambda: frame),
        )
        monkeypatch.setattr(dm_module, "_open_and_grab", lambda *_a: pytest.fail("abrió conexión"))

        for _ in range(dm_module.MAX_CONCURRENT_PROBES + 5):
            _frame, detalle = dm_module.grab_snapshot(self._device())
            assert detalle == "OK (stream activo)"


class TestCodigoMuerto:
    """T4: refresh_device_status no tenía un solo caller en el repo, y con él
    se iban test_rtsp_connection y _open_and_read, que sólo usaba él."""

    @pytest.mark.parametrize(
        "nombre", ["refresh_device_status", "test_rtsp_connection", "_open_and_read"]
    )
    def test_la_cadena_muerta_no_volvio(self, nombre):
        assert not hasattr(dm_module, nombre)

    def test_device_manager_ya_no_conoce_la_db_ni_el_bus(self):
        """Efecto estructural de borrarla: el módulo quedó siendo I/O de red
        pura. Si vuelve a importar repository o el event_bus, alguien le está
        metiendo persistencia adentro."""
        assert not hasattr(dm_module, "repository")
        assert not hasattr(dm_module, "event_bus")
