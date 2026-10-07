"""Detección de incidentes, modo consumo (2026-09-24): alerta previa por
preparacion y consumo por mano a la nariz o boca. Las reglas se testean con
un estimador falso; la calibracion contra video real esta en el docstring de
core/analytics/consumption_analyzer.py."""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtGui import QColor, QPixmap

import aurea_vms.core.analytics.consumption_analyzer as ca_module
from aurea_vms.core.analytics.consumption_analyzer import (
    LABEL_CONSUMPTION,
    LABEL_PREPARATION,
    ConsumptionAnalyzer,
    read_pose,
)
from aurea_vms.core.analytics.monitor_tamper_analyzer import MonitorTamperAnalyzer
from aurea_vms.core.analytics.pose_backend import (
    FACE_POINTS,
    LEFT_HAND,
    LEFT_SHOULDER,
    NOSE,
    RIGHT_HAND,
    RIGHT_SHOULDER,
    Pose,
)
from aurea_vms.core.analytics.registry import create_analyzer
from aurea_vms.core.events import DetectionEvent
from aurea_vms.models import repository
from aurea_vms.ui.dialogs.monitor_tamper_config_dialog import MonitorTamperConfigDialog
from aurea_vms.ui.widgets.video_tile import VideoTile

SEAT = (600, 460, 340, 360)
FRAME = np.zeros((1080, 1920, 3), dtype=np.uint8)
NOSE_AT = np.array([700.0, 500.0])
SHOULDER_W = 100.0


def _pose(face_score=0.7, left=None, right=None, shoulders=True) -> Pose:
    """Hombros a 100 px, nariz en NOSE_AT y cada mano (21 puntos) alrededor
    del centro dado, o ausente si es None."""
    points = np.full((133, 2), -1000.0, dtype=np.float32)
    scores = np.full(133, 0.05, dtype=np.float32)
    if shoulders:
        points[LEFT_SHOULDER] = (650.0, 560.0)
        points[RIGHT_SHOULDER] = (750.0, 560.0)
        scores[[LEFT_SHOULDER, RIGHT_SHOULDER]] = 0.8
    points[NOSE] = NOSE_AT
    scores[NOSE] = max(face_score, 0.5)
    scores[list(FACE_POINTS)] = face_score
    for hand, center in ((LEFT_HAND, left), (RIGHT_HAND, right)):
        if center is None:
            continue
        spread = np.linspace(-4.0, 4.0, len(hand), dtype=np.float32)
        points[list(hand), 0] = center[0] + spread
        points[list(hand), 1] = center[1] + spread
        scores[list(hand)] = 0.8
    return Pose(points=points, scores=scores)


def _preparing() -> Pose:
    """Manos juntas a ~0,6 hombros por debajo de la nariz: el armado."""
    return _pose(left=(690.0, 560.0), right=(715.0, 562.0))


def _gesture() -> Pose:
    """Dedos sobre la nariz."""
    return _pose(left=(690.0, 560.0), right=(702.0, 503.0))


def _playing() -> Pose:
    """Manos separadas sobre la botonera, lejos de la cara."""
    return _pose(left=(620.0, 720.0), right=(800.0, 730.0))


class FakeEstimator:
    def __init__(self, poses) -> None:
        self.poses = list(poses)
        self.closed = False

    def estimate(self, _frame, _box):
        return self.poses.pop(0) if self.poses else _playing()

    def close(self):
        self.closed = True


def _run(poses, fps=5.0, **kwargs):
    analyzer = ConsumptionAnalyzer(zones=[SEAT], estimator=FakeEstimator(poses), **kwargs)
    results = [analyzer.process_frame(FRAME, i / fps) for i in range(len(poses))]
    return analyzer, results


def _triggers(results, label):
    return [i for i, result in enumerate(results) for t in result.triggers if t.label == label]


class TestLecturaDePose:
    def test_mide_en_anchos_de_hombro(self):
        reading = read_pose(*_astuple(_gesture()))
        assert reading.face_visible
        assert reading.hand_to_face < 0.2
        assert reading.hands_gap == pytest.approx(0.57, abs=0.05)

    def test_sin_hombros_no_hay_escala(self):
        assert read_pose(*_astuple(_pose(shoulders=False, right=(702.0, 503.0)))) is None

    def test_de_espaldas_la_cara_no_cuenta_como_visible(self):
        """El falso positivo de la Sala ESP SUB: mano en la botonera que por
        perspectiva cae sobre la cabeza, con la cara a 0,23-0,37."""
        reading = read_pose(*_astuple(_pose(face_score=0.3, right=(702.0, 503.0))))
        assert not reading.face_visible


def _astuple(pose: Pose):
    return pose.points, pose.scores


class TestAlertaPrevia:
    def test_preparacion_sostenida_da_una_sola_alerta_previa(self):
        _, results = _run([_preparing()] * 40, preparation_min_s=5.0)  # 8 s a 5 fps

        assert len(_triggers(results, LABEL_PREPARATION)) == 1
        assert results[-1].metrics["estado"] == "previa"
        assert results[-1].metrics["zonas"][0]["motivo"] == "preparacion"
        assert results[-1].metrics["previas"] == 1

    def test_una_preparacion_corta_no_alcanza(self):
        _, results = _run([_preparing()] * 15 + [_playing()] * 10, preparation_min_s=5.0)

        assert _triggers(results, LABEL_PREPARATION) == []
        assert results[-1].metrics["estado"] == "normal"

    def test_jugar_con_las_manos_en_la_botonera_no_es_preparacion(self):
        _, results = _run([_playing()] * 60)

        assert results[-1].metrics["previas"] == 0


class TestConsumo:
    def test_gesto_despues_de_preparar_es_incidente(self):
        analyzer, results = _run([_preparing()] * 35 + [_gesture()] * 3)

        (index,) = _triggers(results, LABEL_CONSUMPTION)
        assert index == 35 + 1  # la segunda muestra seguida del gesto
        metrics = results[-1].metrics
        assert metrics["estado"] == "alerta"
        assert metrics["incidentes"] == 1
        assert metrics["ultimo_incidente"]["motivo"] == "consumo"
        assert metrics["zona_tipo"] == "puesto"

    def test_sin_preparacion_previa_no_alarma(self):
        _, results = _run([_playing()] * 10 + [_gesture()] * 5)

        assert _triggers(results, LABEL_CONSUMPTION) == []

    def test_se_puede_no_exigir_la_preparacion(self):
        _, results = _run([_playing()] * 10 + [_gesture()] * 5, require_preparation=False)

        assert len(_triggers(results, LABEL_CONSUMPTION)) == 1

    def test_una_sola_muestra_no_alcanza(self):
        poses = [_preparing()] * 35 + [_gesture(), _preparing(), _gesture(), _preparing()]
        _, results = _run(poses)

        assert _triggers(results, LABEL_CONSUMPTION) == []

    def test_de_espaldas_no_alarma(self):
        back = _pose(face_score=0.3, left=(690.0, 560.0), right=(702.0, 503.0))
        _, results = _run([_preparing()] * 35 + [back] * 5)

        assert _triggers(results, LABEL_CONSUMPTION) == []

    def test_fumar_seguido_es_un_solo_incidente(self):
        smoking = [_gesture(), _gesture(), _preparing()] * 10
        _, results = _run([_preparing()] * 35 + smoking)

        assert len(_triggers(results, LABEL_CONSUMPTION)) == 1

    def test_tras_una_pausa_larga_hay_otro_incidente(self):
        poses = [_preparing()] * 35 + [_gesture()] * 3 + [_preparing()] * 60 + [_gesture()] * 3
        _, results = _run(poses, release_s=10.0)

        assert len(_triggers(results, LABEL_CONSUMPTION)) == 2

    def test_close_suelta_el_estimador(self):
        analyzer, _ = _run([])
        analyzer.close()
        assert analyzer._estimator.closed


class TestRegistro:
    def test_modo_consumo_crea_el_analizador_de_consumo(self, monkeypatch):
        monkeypatch.setattr(ca_module, "PoseEstimator", lambda _model: FakeEstimator([]))
        config = SimpleNamespace(
            analyzer_name="monitor_tamper",
            params={"modo": "consumo", "zones": [list(SEAT)], "preparation_min_s": 8},
            roi_x=None,
            roi_y=None,
            roi_w=None,
            roi_h=None,
        )
        analyzer = create_analyzer(config)

        assert isinstance(analyzer, ConsumptionAnalyzer)
        assert analyzer._preparation_min_s == 8

    def test_sin_modo_sigue_siendo_golpes(self, monkeypatch):
        import aurea_vms.core.analytics.monitor_tamper_analyzer as mt_module

        monkeypatch.setattr(mt_module, "PoseEstimator", lambda: FakeEstimator([]))
        config = SimpleNamespace(
            analyzer_name="monitor_tamper",
            params={"zones": [[600, 560, 280, 310]]},
            roi_x=None,
            roi_y=None,
            roi_w=None,
            roi_h=None,
        )
        assert isinstance(create_analyzer(config), MonitorTamperAnalyzer)


class TestDialogo:
    @pytest.fixture()
    def dialog(self, qtbot, temp_db):
        device = repository.add_device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
        repository.upsert_analytics_config(
            device.id,
            "monitor_tamper",
            params={"modo": "consumo", "zones": [list(SEAT)], "preparation_min_s": 7},
        )
        widget = MonitorTamperConfigDialog(device)
        qtbot.addWidget(widget)
        return widget

    def test_abre_en_el_modo_guardado(self, dialog):
        assert dialog.mode() == "consumo"
        assert dialog.preparation_spin.value() == 7
        params = dialog.build_params()
        assert params["modo"] == "consumo"
        assert params["preparation_min_s"] == 7
        assert "hand_strike_speed" not in params

    def test_cambiar_a_golpes_guarda_los_parametros_de_golpes(self, dialog):
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("golpes"))

        params = dialog.build_params()
        assert params["modo"] == "golpes"
        assert "hand_strike_speed" in params
        assert "require_preparation" not in params
        assert "PANTALLA" in dialog.intro_label.text()


class TestMarcaEnElVideo:
    def test_la_alerta_previa_pinta_el_marco_ambar(self, qtbot):
        tile = VideoTile(0)
        qtbot.addWidget(tile)
        tile._device = SimpleNamespace(name="Cam", id=8)
        tile._analytics_configs = [
            SimpleNamespace(
                analyzer_name="monitor_tamper",
                params={"modo": "consumo", "zones": [list(SEAT)]},
                roi_x=None,
                roi_y=None,
                roi_w=None,
                roi_h=None,
            )
        ]
        tile._latest_events = {
            "monitor_tamper": DetectionEvent(
                8,
                "monitor_tamper",
                time.time(),
                metrics={
                    "modo": "consumo",
                    "zona_tipo": "puesto",
                    "estado": "previa",
                    "zonas": [{"estado": "previa", "motivo": "preparacion"}],
                    "incidentes": 0,
                    "previas": 1,
                },
            )
        }
        pixmap = QPixmap(640, 360)
        pixmap.fill(QColor("#303030"))

        border = QColor(tile._draw_overlay(pixmap, 1920, 1080).toImage().pixel(4, 180))

        # Ambar (#f59e0b) latiendo entre 45% y 100%: rojo y verde sobre el azul.
        assert border.red() > border.blue() + 60
        assert border.green() > border.blue() + 30
