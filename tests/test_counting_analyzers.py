from __future__ import annotations

import numpy as np
import pytest

import aurea_vms.core.analytics.line_crossing_analyzer as lc_module
import aurea_vms.core.analytics.people_counting_analyzer as pc_module
from aurea_vms.core.analytics.line_crossing_analyzer import LineCrossingAnalyzer, _side_of_line
from aurea_vms.core.analytics.people_counting_analyzer import PeopleCountingAnalyzer
from aurea_vms.core.events import Detection


class ScriptedDetector:
    """Doble de YoloxDetector: devuelve detecciones pre-armadas, una lista
    por llamada a detect()."""

    def __init__(self) -> None:
        self.script: list[list[Detection]] = []

    def detect(self, _frame, _category_allowlist, _confidence_threshold):
        return self.script.pop(0) if self.script else []


@pytest.fixture()
def fake_backend(monkeypatch):
    detector = ScriptedDetector()
    for module in (lc_module, pc_module):
        monkeypatch.setattr(module, "YoloxDetector", lambda: detector)
    return detector


def _det(cx: float, cy: float, label: str = "person", score: float = 0.9) -> Detection:
    """Deteccion con el centro del bbox (20x20) en (cx, cy)."""
    size = 20
    return Detection(
        label=label,
        confidence=score,
        bbox=(round(cx - size / 2), round(cy - size / 2), size, size),
    )


FRAME = np.zeros((200, 200, 3), dtype=np.uint8)


class TestSideOfLine:
    LINE = (0.0, 100.0, 200.0, 100.0)  # horizontal en y=100

    def test_lados_opuestos_y_sobre_la_linea(self):
        assert _side_of_line(50, 150, *self.LINE) == 1  # debajo
        assert _side_of_line(50, 50, *self.LINE) == -1  # arriba
        assert _side_of_line(50, 100, *self.LINE) == 0  # exactamente sobre


class TestLineCrossing:
    def _analyzer(self) -> LineCrossingAnalyzer:
        return LineCrossingAnalyzer(
            line=((0, 100), (200, 100)), confirmation_frames=2, confidence_threshold=0.5
        )

    def test_cruce_confirmado_cuenta_en_el_sentido_correcto(self, fake_backend):
        analyzer = self._analyzer()
        fake_backend.script = [
            [_det(100, 120)],  # debajo (hit 1: aun no confirmado)
            [_det(100, 112)],  # debajo (confirmado, side=+1)
            [_det(100, 60)],  # arriba -> cruce IN
        ]
        analyzer.process_frame(FRAME, 0.0)
        analyzer.process_frame(FRAME, 0.2)
        result = analyzer.process_frame(FRAME, 0.4)

        assert result.metrics == {"count_in": 1, "count_out": 0, "total": 1}

    def test_cruce_inverso_cuenta_out(self, fake_backend):
        analyzer = self._analyzer()
        fake_backend.script = [
            [_det(100, 80)],
            [_det(100, 88)],
            [_det(100, 140)],
        ]
        for i in range(3):
            result = analyzer.process_frame(FRAME, i * 0.2)

        assert result.metrics == {"count_in": 0, "count_out": 1, "total": 1}

    def test_deteccion_espuria_no_cuenta(self, fake_backend):
        """Un objeto que 'aparece' del otro lado sin historial confirmado
        no puede disparar un cruce (histeresis del tracker)."""
        analyzer = self._analyzer()
        fake_backend.script = [
            [_det(100, 120)],  # un solo frame abajo
            [_det(100, 60)],  # ya arriba: recien aqui se confirma
        ]
        analyzer.process_frame(FRAME, 0.0)
        result = analyzer.process_frame(FRAME, 0.2)

        assert result.metrics["total"] == 0

    def test_quedarse_del_mismo_lado_no_cuenta(self, fake_backend):
        analyzer = self._analyzer()
        fake_backend.script = [[_det(100, 120)] for _ in range(4)]
        for i in range(4):
            result = analyzer.process_frame(FRAME, i * 0.2)

        assert result.metrics["total"] == 0

    def test_dos_cajas_superpuestas_no_duplican_el_track(self, fake_backend):
        analyzer = self._analyzer()
        fake_backend.script = [
            [_det(100, 120), _det(102, 121)],
        ]
        result = analyzer.process_frame(FRAME, 0.0)

        assert len(result.detections) == 1


class TestPeopleCounting:
    def test_ocupacion_requiere_confirmacion(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=2)
        fake_backend.script = [
            [_det(50, 50)],
            [_det(52, 50)],
        ]
        primero = analyzer.process_frame(FRAME, 0.0)
        segundo = analyzer.process_frame(FRAME, 0.2)

        assert primero.metrics["occupancy"] == 0  # hit 1: aun no cuenta
        assert segundo.metrics["occupancy"] == 1

    def test_dos_personas_separadas(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1)
        fake_backend.script = [[_det(30, 30), _det(170, 170)]]
        result = analyzer.process_frame(FRAME, 0.0)

        assert result.metrics["occupancy"] == 2
        assert all(d.label == "person" for d in result.detections)

    def test_persona_que_se_va_expira(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1, track_max_age_s=1.0)
        fake_backend.script = [[_det(50, 50)], []]
        analyzer.process_frame(FRAME, 0.0)
        result = analyzer.process_frame(FRAME, 5.0)  # mucho despues

        assert result.metrics["occupancy"] == 0

    def test_dos_cajas_superpuestas_de_la_misma_persona_cuentan_una_vez(self, fake_backend):
        """El detector a veces deja pasar dos cajas casi iguales sobre el
        mismo blob -- sin deduplicar por IoU, cada una arma su propio
        track y la ocupacion queda inflada."""
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1)
        fake_backend.script = [[_det(50, 50), _det(52, 51)]]
        result = analyzer.process_frame(FRAME, 0.0)

        assert result.metrics["occupancy"] == 1

    def test_caja_con_forma_de_persona_imposible_se_descarta(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1)
        wide_box = Detection(label="person", confidence=0.9, bbox=(40, 40, 100, 20))
        fake_backend.script = [[wide_box]]
        result = analyzer.process_frame(FRAME, 0.0)

        assert result.metrics["occupancy"] == 0
