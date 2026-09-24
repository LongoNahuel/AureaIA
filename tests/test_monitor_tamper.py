"""Detección de incidentes (ex Monitor roto, 2026-09-23): reglas de patada y
golpe con la mano sobre la pantalla, incidentes y alarma solo al empezar. Las reglas se testean con
un estimador de pose falso; la calibracion contra video real esta en el
docstring de core/analytics/monitor_tamper_analyzer.py."""

from __future__ import annotations

import numpy as np
import pytest

from aurea_vms.core.analytics.monitor_tamper_analyzer import (
    LABEL_HAND,
    LABEL_KICK,
    MonitorTamperAnalyzer,
    expand_rect,
)
from aurea_vms.core.analytics.pose_backend import (
    INPUT_H,
    INPUT_W,
    LEFT_ANKLE,
    LEFT_KNEE,
    LEFT_WRIST,
    RIGHT_ANKLE,
    RIGHT_KNEE,
    Pose,
    crop_transform,
    decode_simcc,
)

ZONE = (600, 560, 280, 310)  # la pantalla (como la de la demo)
INSIDE = (740.0, 700.0)
FRAME = np.zeros((1080, 1920, 3), dtype=np.uint8)


def _pose(**joints) -> Pose:
    """Pose con todo fuera de la pantalla y confianza baja, salvo las
    articulaciones indicadas: nombre=(x, y, score)."""
    points = np.full((17, 2), -500.0, dtype=np.float32)
    scores = np.full(17, 0.05, dtype=np.float32)
    index = {
        "rodilla_i": LEFT_KNEE,
        "rodilla_d": RIGHT_KNEE,
        "tobillo_i": LEFT_ANKLE,
        "tobillo_d": RIGHT_ANKLE,
        "muñeca": LEFT_WRIST,
    }
    for name, (x, y, score) in joints.items():
        points[index[name]] = (x, y)
        scores[index[name]] = score
    return Pose(points=points, scores=scores)


class FakeEstimator:
    """Devuelve una pose por llamada (una llamada por pantalla y cuadro)."""

    def __init__(self, poses: list[Pose]) -> None:
        self.poses = list(poses)
        self.boxes: list[tuple] = []
        self.closed = False

    def estimate(self, _frame, box):
        self.boxes.append(box)
        return self.poses.pop(0) if self.poses else _pose()

    def close(self):
        self.closed = True


def _analyzer(poses, **kwargs) -> tuple[MonitorTamperAnalyzer, FakeEstimator]:
    estimator = FakeEstimator(poses)
    return MonitorTamperAnalyzer(zones=[ZONE], estimator=estimator, **kwargs), estimator


def _patada(score: float = 0.4) -> Pose:
    return _pose(tobillo_i=(*INSIDE, score), rodilla_d=(760.0, 820.0, score))


class TestPatada:
    def test_pie_y_rodilla_en_la_pantalla_es_patada(self):
        analyzer, _ = _analyzer([_patada()])
        result = analyzer.process_frame(FRAME, 0.0)

        (trigger,) = result.triggers
        assert trigger.label == LABEL_KICK
        assert trigger.bbox == ZONE
        assert result.metrics["estado"] == "alerta"
        assert result.metrics["zonas"][0]["motivo"] == "patada"
        assert result.metrics["incidentes"] == 1

    def test_rodillas_solas_no_son_patada(self):
        """El caso de los 49 s del clip: piernas cruzadas con las dos
        rodillas en el borde de abajo del rectangulo (la mesa)."""
        analyzer, _ = _analyzer(
            [_pose(rodilla_i=(700.0, 860.0, 0.37), rodilla_d=(720.0, 865.0, 0.35))]
        )
        result = analyzer.process_frame(FRAME, 0.0)

        assert result.triggers == ()
        assert result.metrics["estado"] == "normal"

    def test_un_solo_tobillo_dudoso_no_alcanza_pero_uno_seguro_si(self):
        dudoso, _ = _analyzer([_pose(tobillo_i=(*INSIDE, 0.32))])
        seguro, _ = _analyzer([_pose(tobillo_i=(*INSIDE, 0.50))])

        assert dudoso.process_frame(FRAME, 0.0).triggers == ()
        assert len(seguro.process_frame(FRAME, 0.0).triggers) == 1

    def test_una_pierna_fuera_de_la_pantalla_no_cuenta(self):
        analyzer, _ = _analyzer(
            [_pose(tobillo_i=(300.0, 900.0, 0.9), rodilla_i=(320.0, 850.0, 0.9))]
        )
        assert analyzer.process_frame(FRAME, 0.0).triggers == ()


class TestIncidentes:
    def test_varias_patadas_seguidas_son_un_incidente(self):
        analyzer, _ = _analyzer([_patada(), _patada(), _pose(), _patada()])
        triggers = [analyzer.process_frame(FRAME, t).triggers for t in (0.0, 0.2, 0.4, 0.6)]

        assert [len(t) for t in triggers] == [1, 0, 0, 0]

    def test_tras_una_pausa_hay_un_incidente_nuevo(self):
        analyzer, _ = _analyzer([_patada(), _pose(), _patada()], release_s=1.0)
        analyzer.process_frame(FRAME, 0.0)
        analyzer.process_frame(FRAME, 2.0)  # sin golpe: cierra el incidente
        result = analyzer.process_frame(FRAME, 3.0)

        assert len(result.triggers) == 1
        assert result.metrics["incidentes"] == 2

    def test_la_alerta_se_mantiene_visible_y_despues_vuelve_a_normal(self):
        analyzer, _ = _analyzer([_patada(), _pose(), _pose()], alert_hold_s=4.0)
        analyzer.process_frame(FRAME, 0.0)

        assert analyzer.process_frame(FRAME, 3.0).metrics["estado"] == "alerta"
        despues = analyzer.process_frame(FRAME, 5.0).metrics
        assert despues["estado"] == "normal"
        assert despues["ultimo_golpe"] == {"t": 0.0, "zona": 0, "motivo": "patada"}

    def test_confirmacion_exige_muestras_seguidas(self):
        analyzer, _ = _analyzer([_patada(), _patada()], confirmation_frames=2)

        assert analyzer.process_frame(FRAME, 0.0).triggers == ()
        assert len(analyzer.process_frame(FRAME, 0.2).triggers) == 1


class TestGolpeConLaMano:
    def test_mano_que_entra_rapido_es_golpe(self):
        # diagonal de la pantalla ~418 px: 250 px en 0,2 s son ~3 pantallas/s
        analyzer, _ = _analyzer(
            [_pose(muñeca=(740.0, 450.0, 0.8)), _pose(muñeca=(740.0, 700.0, 0.8))]
        )
        analyzer.process_frame(FRAME, 0.0)
        result = analyzer.process_frame(FRAME, 0.2)

        (trigger,) = result.triggers
        assert trigger.label == LABEL_HAND
        assert result.metrics["zonas"][0]["motivo"] == "golpe_mano"

    def test_un_toque_normal_de_pantalla_tactil_no_es_golpe(self):
        analyzer, _ = _analyzer(
            [_pose(muñeca=(740.0, 700.0, 0.8)), _pose(muñeca=(745.0, 705.0, 0.8))]
        )
        analyzer.process_frame(FRAME, 0.0)
        result = analyzer.process_frame(FRAME, 0.2)

        assert result.triggers == ()
        assert result.metrics["contacto"] is True

    def test_una_muñeca_vista_hace_mucho_no_sirve_para_medir_velocidad(self):
        analyzer, _ = _analyzer(
            [_pose(muñeca=(740.0, 450.0, 0.8)), _pose(muñeca=(740.0, 700.0, 0.8))]
        )
        analyzer.process_frame(FRAME, 0.0)

        assert analyzer.process_frame(FRAME, 2.0).triggers == ()

    def test_se_puede_desactivar(self):
        analyzer, _ = _analyzer(
            [_pose(muñeca=(740.0, 450.0, 0.8)), _pose(muñeca=(740.0, 700.0, 0.8))],
            hand_strikes_enabled=False,
        )
        analyzer.process_frame(FRAME, 0.0)

        assert analyzer.process_frame(FRAME, 0.2).triggers == ()


class TestGeometria:
    def test_hace_falta_al_menos_una_pantalla(self):
        with pytest.raises(ValueError, match="pantalla"):
            MonitorTamperAnalyzer(zones=[], estimator=FakeEstimator([]))

    def test_el_recorte_del_puesto_agranda_la_pantalla_y_respeta_el_cuadro(self):
        assert expand_rect((100, 100, 100, 100), 2.0, 1920, 1080) == (50.0, 50.0, 200.0, 200.0)
        x, y, w, h = expand_rect((0, 0, 100, 100), 3.0, 1920, 1080)
        assert (x, y) == (0.0, 0.0) and (w, h) == (200.0, 200.0)

    def test_estima_una_pose_por_pantalla(self):
        estimator = FakeEstimator([])
        analyzer = MonitorTamperAnalyzer(zones=[ZONE, (1000, 630, 220, 240)], estimator=estimator)
        result = analyzer.process_frame(FRAME, 0.0)

        assert len(estimator.boxes) == 2
        assert len(result.metrics["zonas"]) == 2

    def test_close_suelta_el_estimador(self):
        analyzer, estimator = _analyzer([])
        analyzer.close()
        assert estimator.closed


class TestPoseBackend:
    def test_la_caja_conserva_la_proporcion_del_modelo(self):
        transform = crop_transform((100.0, 100.0, 192.0, 256.0))
        corners = np.array([[100, 100, 1], [292, 356, 1]], dtype=np.float64) @ transform.T
        assert np.allclose(corners, [[0, 0], [INPUT_W, INPUT_H]])

    def test_decodifica_simcc_a_pixeles_del_cuadro(self):
        simcc_x = np.zeros((17, INPUT_W * 2), dtype=np.float32)
        simcc_y = np.zeros((17, INPUT_H * 2), dtype=np.float32)
        simcc_x[LEFT_ANKLE, 100] = 0.9  # x = 50 en la entrada
        simcc_y[LEFT_ANKLE, 60] = 0.7  # y = 30 en la entrada
        inverse = np.array([[2.0, 0.0, 10.0], [0.0, 2.0, 20.0]])  # escala x2 y corre
        points, scores = decode_simcc(simcc_x, simcc_y, inverse)

        assert tuple(points[LEFT_ANKLE]) == (110.0, 80.0)
        assert scores[LEFT_ANKLE] == pytest.approx(0.7)
