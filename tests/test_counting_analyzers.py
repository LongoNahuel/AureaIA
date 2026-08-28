from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import aurea_vms.core.analytics.line_crossing_analyzer as lc_module
import aurea_vms.core.analytics.people_counting_analyzer as pc_module
from aurea_vms.core.analytics.line_crossing_analyzer import LineCrossingAnalyzer, _side_of_line
from aurea_vms.core.analytics.people_counting_analyzer import PeopleCountingAnalyzer


class FakeMp:
    """Doble del modulo mediapipe: solo lo que usan los analizadores."""

    ImageFormat = SimpleNamespace(SRGB="SRGB")

    @staticmethod
    def Image(image_format, data):  # noqa: N802 - imita la API de mediapipe
        return data


class ScriptedDetector:
    """Devuelve detecciones pre-armadas, una lista por llamada a detect()."""

    def __init__(self) -> None:
        self.script: list[list] = []
        self.closed = False

    def detect(self, _image):
        detections = self.script.pop(0) if self.script else []
        return SimpleNamespace(detections=detections)

    def close(self) -> None:
        self.closed = True


def _mp_detection(cx: float, cy: float, label: str = "person", score: float = 0.9):
    """Deteccion estilo mediapipe con el centro del bbox en (cx, cy)."""
    size = 20
    return SimpleNamespace(
        bounding_box=SimpleNamespace(
            origin_x=int(cx - size / 2), origin_y=int(cy - size / 2), width=size, height=size
        ),
        categories=[SimpleNamespace(category_name=label, score=score)],
    )


@pytest.fixture()
def fake_backend(monkeypatch):
    detector = ScriptedDetector()
    for module in (lc_module, pc_module):
        monkeypatch.setattr(module, "create_object_detector", lambda *_a, **_k: (FakeMp, detector))
    return detector


FRAME = np.zeros((200, 200, 3), dtype=np.uint8)  # chico: sin resize (escala 1.0)


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
            [_mp_detection(100, 120)],  # debajo (hit 1: aun no confirmado)
            [_mp_detection(100, 112)],  # debajo (confirmado, side=+1)
            [_mp_detection(100, 60)],  # arriba -> cruce IN
        ]
        analyzer.process_frame(FRAME, 0.0)
        analyzer.process_frame(FRAME, 0.2)
        result = analyzer.process_frame(FRAME, 0.4)

        assert result.metrics == {"count_in": 1, "count_out": 0, "total": 1}

    def test_cruce_inverso_cuenta_out(self, fake_backend):
        analyzer = self._analyzer()
        fake_backend.script = [
            [_mp_detection(100, 80)],
            [_mp_detection(100, 88)],
            [_mp_detection(100, 140)],
        ]
        for i in range(3):
            result = analyzer.process_frame(FRAME, i * 0.2)

        assert result.metrics == {"count_in": 0, "count_out": 1, "total": 1}

    def test_deteccion_espuria_no_cuenta(self, fake_backend):
        """Un objeto que 'aparece' del otro lado sin historial confirmado
        no puede disparar un cruce (histeresis del tracker)."""
        analyzer = self._analyzer()
        fake_backend.script = [
            [_mp_detection(100, 120)],  # un solo frame abajo
            [_mp_detection(100, 60)],  # ya arriba: recien aqui se confirma
        ]
        analyzer.process_frame(FRAME, 0.0)
        result = analyzer.process_frame(FRAME, 0.2)

        assert result.metrics["total"] == 0

    def test_quedarse_del_mismo_lado_no_cuenta(self, fake_backend):
        analyzer = self._analyzer()
        fake_backend.script = [[_mp_detection(100, 120)] for _ in range(4)]
        for i in range(4):
            result = analyzer.process_frame(FRAME, i * 0.2)

        assert result.metrics["total"] == 0

    def test_close_libera_el_detector(self, fake_backend):
        analyzer = self._analyzer()
        analyzer.close()
        assert fake_backend.closed

    def test_dos_cajas_superpuestas_no_duplican_el_track(self, fake_backend):
        analyzer = self._analyzer()
        fake_backend.script = [
            [_mp_detection(100, 120), _mp_detection(102, 121)],
        ]
        result = analyzer.process_frame(FRAME, 0.0)

        assert len(result.detections) == 1


class TestPeopleCounting:
    def test_ocupacion_requiere_confirmacion(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=2)
        fake_backend.script = [
            [_mp_detection(50, 50)],
            [_mp_detection(52, 50)],
        ]
        primero = analyzer.process_frame(FRAME, 0.0)
        segundo = analyzer.process_frame(FRAME, 0.2)

        assert primero.metrics == {"occupancy": 0}  # hit 1: aun no cuenta
        assert segundo.metrics == {"occupancy": 1}

    def test_dos_personas_separadas(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1)
        fake_backend.script = [[_mp_detection(30, 30), _mp_detection(170, 170)]]
        result = analyzer.process_frame(FRAME, 0.0)

        assert result.metrics == {"occupancy": 2}
        assert all(d.label == "person" for d in result.detections)

    def test_persona_que_se_va_expira(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1, track_max_age_s=1.0)
        fake_backend.script = [[_mp_detection(50, 50)], []]
        analyzer.process_frame(FRAME, 0.0)
        result = analyzer.process_frame(FRAME, 5.0)  # mucho despues

        assert result.metrics == {"occupancy": 0}

    def test_dos_cajas_superpuestas_de_la_misma_persona_cuentan_una_vez(self, fake_backend):
        """El detector a veces deja pasar dos cajas casi iguales sobre el
        mismo blob -- sin deduplicar por IoU, cada una arma su propio
        track y la ocupacion queda inflada."""
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1)
        fake_backend.script = [[_mp_detection(50, 50), _mp_detection(52, 51)]]
        result = analyzer.process_frame(FRAME, 0.0)

        assert result.metrics == {"occupancy": 1}

    def test_caja_con_forma_de_persona_imposible_se_descarta(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1)
        wide_box = SimpleNamespace(
            bounding_box=SimpleNamespace(origin_x=40, origin_y=40, width=100, height=20),
            categories=[SimpleNamespace(category_name="person", score=0.9)],
        )
        fake_backend.script = [[wide_box]]
        result = analyzer.process_frame(FRAME, 0.0)

        assert result.metrics == {"occupancy": 0}
