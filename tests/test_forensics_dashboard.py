"""Visor forense de rostros, registro compartido y dashboard analitico
(2026-09-23)."""

from __future__ import annotations

import hashlib
import json

import cv2
import numpy as np
import pytest

from aurea_vms.core.events import DetectionEvent, FaceShot
from aurea_vms.ui.analytics_hub import AnalyticsHub
from aurea_vms.ui.face_registry import FaceRegistry
from aurea_vms.ui.widgets.charts import nice_ceiling
from aurea_vms.ui.widgets.face_forensics import (
    context_box,
    enhance,
    evidence_sheet,
    export_evidence,
)


def _shot(track_id=1, quality=0.6) -> FaceShot:
    rng = np.random.default_rng(track_id)
    frame = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", cv2.resize(frame, (320, 180)))
    return FaceShot(
        track_id=track_id,
        image=frame[100:196, 200:296].copy(),
        quality=quality,
        confidence=0.9,
        bbox=(220, 120, 60, 60),
        forensic=frame[60:240, 160:340].copy(),
        context_jpeg=jpeg.tobytes(),
        context_scale=0.5,
        frame_size=(640, 360),
        details={"frontal": 0.9, "nitidez": 0.7, "tamaño": 0.5, "confianza": 0.9,
                 "total": quality, "distancia_ojos_px": 32.0},
    )  # fmt: skip


def _event(device_id, *shots, timestamp=1000.0):
    return DetectionEvent(
        device_id, "face_detection", timestamp, metrics={"caras": 1, "face_shots": list(shots)}
    )


@pytest.fixture()
def registry(qtbot):
    registry = FaceRegistry()
    for device_id in (1, 2):
        registry._names[device_id] = f"Cam {device_id}"
    return registry


class TestRegistroDeCapturas:
    def test_una_toma_crea_una_captura_con_evidencia(self, registry):
        registry.process(_event(1, _shot()))
        (record,) = registry.records(1)

        assert record.track_id == 1
        assert record.forensic.shape == (180, 180, 3)
        assert record.evidence is record.forensic
        assert registry.capture_count() == 1

    def test_una_toma_mejor_del_mismo_paso_reemplaza_a_la_anterior(self, registry):
        registry.process(_event(1, _shot(1, quality=0.5)))
        registry.process(_event(1, _shot(1, quality=0.8), timestamp=1001.0))
        (record,) = registry.records(1)

        assert record.quality == 0.8
        # el visor forense conserva tambien la toma previa
        assert [take.quality for take in registry.history(record)] == [0.8, 0.5]

    def test_nunca_cambia_una_toma_por_una_peor(self, registry):
        registry.process(_event(1, _shot(1, quality=0.8)))
        registry.process(_event(1, _shot(1, quality=0.5), timestamp=1001.0))

        assert registry.records(1)[0].quality == 0.8

    def test_pasos_distintos_son_capturas_distintas(self, registry):
        """Sin identidad: dos tracks son dos capturas, aunque fueran la
        misma persona."""
        registry.process(_event(1, _shot(1), _shot(2)))

        assert registry.capture_count(1) == 2

    def test_un_track_renumerado_tras_reiniciar_el_analizador_es_otra_captura(self, registry):
        registry.process(_event(1, _shot(1), timestamp=1000.0))
        registry.process(_event(1, _shot(1), timestamp=1000.0 + 120))

        assert registry.capture_count(1) == 2

    def test_capturas_por_camara_y_total(self, registry):
        registry.process(_event(1, _shot(1)))
        registry.process(_event(2, _shot(1)))

        assert registry.capture_count(1) == 1
        assert registry.capture_count(2) == 1
        assert registry.capture_count() == 2

    def test_tope_de_capturas_por_camara(self, registry, monkeypatch):
        import aurea_vms.ui.face_registry as fr_module

        monkeypatch.setattr(fr_module, "MAX_CAPTURES_PER_DEVICE", 3)
        for track in range(1, 6):
            registry.process(_event(1, _shot(track), timestamp=1000.0 + track))

        assert [r.track_id for r in registry.records(1)] == [5, 4, 3]

    def test_vaciar_una_camara(self, registry):
        registry.process(_event(1, _shot(1)))
        registry.process(_event(2, _shot(1)))
        registry.clear(1)

        assert registry.capture_count(1) == 0
        assert registry.capture_count(2) == 1

    def test_avisa_a_las_vistas_cuando_cambia(self, registry, qtbot):
        with qtbot.waitSignal(registry.captures_changed) as signal:
            registry.process(_event(2, _shot()))
        assert signal.args == [2]


class TestEvidenciaForense:
    def test_la_caja_se_escala_al_cuadro_de_contexto(self, registry):
        registry.process(_event(1, _shot()))
        (record,) = registry.records(1)

        assert context_box(record) == (110.0, 60.0, 30.0, 30.0)

    def test_la_ficha_tiene_lo_necesario(self, registry):
        registry.process(_event(1, _shot()))
        record = registry.records(1)[0]
        sheet = evidence_sheet(record, "Cam 1")

        assert sheet["captura_id"] == record.capture_id
        assert sheet["camara"] == "Cam 1"
        assert sheet["distancia_ojos_px"] == 32.0
        assert sheet["resolucion_recorte"] == {"w": 180, "h": 180}
        assert "id_persona" not in sheet and "veces_vista" not in sheet

    def test_exporta_original_sin_perdida_y_hashes_verificables(self, registry, tmp_path):
        registry.process(_event(1, _shot()))
        record = registry.records(1)[0]
        folder = export_evidence(record, tmp_path, "Cam 1")

        names = sorted(path.name for path in folder.iterdir())
        assert names == ["escena.jpg", "escena_marcada.png", "ficha.json", "rostro_original.png"]
        exported = cv2.imread(str(folder / "rostro_original.png"))
        assert np.array_equal(exported, record.forensic)  # PNG: sin perdida
        sheet = json.loads((folder / "ficha.json").read_text("utf-8"))
        for name, digest in sheet["archivos_sha256"].items():
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest

    def test_los_realces_no_modifican_el_original(self):
        image = np.random.default_rng(0).integers(0, 255, (50, 50, 3), dtype=np.uint8)
        copy = image.copy()
        shown = enhance(image, contrast=True, sharpen=True, gray=True)

        assert np.array_equal(image, copy)
        assert not np.array_equal(shown, image)


class TestConcentrador:
    def test_cruces_por_minuto_y_reinicio_del_analizador(self):
        hub = AnalyticsHub()
        hub.process(
            DetectionEvent(1, "line_crossing", 0.0, metrics={"count_in": 5, "count_out": 2})
        )
        hub.process(
            DetectionEvent(1, "line_crossing", 10.0, metrics={"count_in": 7, "count_out": 2})
        )
        # el analizador se reinicio: sus acumulados vuelven a cero
        hub.process(
            DetectionEvent(1, "line_crossing", 20.0, metrics={"count_in": 1, "count_out": 0})
        )

        state = hub.crossing[1]
        assert state.session_in == 5 + 2 + 1
        assert state.session_out == 2

    def test_tramos_e_incidentes_de_monitor(self):
        hub = AnalyticsHub()

        def evento(t, estado, incidentes, ultimo=None):
            return DetectionEvent(
                3,
                "monitor_tamper",
                t,
                metrics={
                    "estado": estado,
                    "zonas": [{"estado": estado, "incidentes": incidentes}],
                    "incidentes": incidentes,
                    "ultimo_golpe": ultimo,
                },
            )

        hub.process(evento(100.0, "normal", 0))
        hub.process(evento(160.0, "alerta", 1, {"t": 160.0, "zona": 0, "motivo": "patada"}))
        hub.process(evento(161.0, "alerta", 1, {"t": 161.0, "zona": 0, "motivo": "patada"}))

        state = hub.monitors[3]
        segments = hub.screen_segments(state.screens[0], now=200.0)
        assert segments == [(100.0, 160.0, "normal"), (160.0, 200.0, "alerta")]
        assert state.incidents == 1
        # un incidente que sigue abierto no se registra dos veces
        assert list(state.events) == [(160.0, 0, "patada")]

    def test_suma_camaras_intervalo_a_intervalo(self):
        hub = AnalyticsHub()
        hub.process(DetectionEvent(1, "people_counting", 120.0, metrics={"occupancy": 3}))
        hub.process(DetectionEvent(2, "people_counting", 125.0, metrics={"occupancy": 4}))
        merged = hub.merge([s.history for s in hub.people.values()], now=150.0)

        assert merged[-1] == (120.0, 7.0)
        assert len(merged) == 60


@pytest.mark.parametrize(
    ("value", "expected"), [(0, 1.0), (3, 5.0), (7, 10.0), (19, 20.0), (51, 100.0)]
)
def test_tope_redondo_del_eje(value, expected):
    assert nice_ceiling(value) == expected
