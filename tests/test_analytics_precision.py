"""Correcciones de precision de las analiticas (2026-09-23): asociacion del
tracker, cruce contra el segmento con histeresis, aforo que dispara
alarma, mapa de calor acumulado y un worker que sobrevive a un cuadro que
falla. (La analitica de puerta se reemplazo por Detección de incidentes: sus tests
estan en test_monitor_tamper.py.)"""

from __future__ import annotations

import numpy as np
import pytest

import aurea_vms.core.alarm_engine as alarm_engine_mod
import aurea_vms.core.analytics.line_crossing_analyzer as lc_module
import aurea_vms.core.analytics.people_counting_analyzer as pc_module
from aurea_vms.core.alarm_engine import AlarmEngine
from aurea_vms.core.analytics.heatmap import OccupancyHeatmap
from aurea_vms.core.analytics.line_crossing_analyzer import (
    LineCrossingAnalyzer,
    _crosses_segment,
)
from aurea_vms.core.analytics.people_counting_analyzer import PeopleCountingAnalyzer
from aurea_vms.core.analytics.tracker import CentroidTracker
from aurea_vms.core.events import Detection, DetectionEvent
from aurea_vms.models.alarm_rule import AlarmRule

FRAME = np.zeros((200, 200, 3), dtype=np.uint8)


class ScriptedDetector:
    def __init__(self) -> None:
        self.script: list[list[Detection]] = []

    def detect(self, _frame, _classes, _threshold):
        return self.script.pop(0) if self.script else []


@pytest.fixture()
def fake_backend(monkeypatch):
    detector = ScriptedDetector()
    for module in (lc_module, pc_module):
        monkeypatch.setattr(module, "YoloxDetector", lambda: detector)
    return detector


def _box(cx: float, cy: float, w: int = 20, h: int = 20, label: str = "person") -> Detection:
    return Detection(label=label, confidence=0.9, bbox=(round(cx - w / 2), round(cy - h / 2), w, h))


class TestTracker:
    def test_la_asociacion_no_depende_del_orden_de_las_detecciones(self):
        """La deteccion mas cercana a un track se lo queda aunque el
        detector la devuelva segunda."""
        tracker = CentroidTracker(max_distance=80.0)
        tracker.update([_box(100, 100)], 0.0)
        (track_id,) = tracker.tracks

        # La cercana (105) viene despues de una que tambien cae dentro del
        # radio (170): antes la primera de la lista se robaba el track.
        assigned = tracker.update([_box(170, 100), _box(105, 100)], 0.2)

        assert assigned[track_id].centroid == (105.0, 100.0)

    def test_objeto_grande_y_rapido_no_pierde_el_track(self):
        """A pocos fps, una persona cerca de la camara se mueve mas que el
        piso fijo de distancia entre muestras."""
        tracker = CentroidTracker(max_distance=80.0)
        tracker.update([_box(100, 300, w=120, h=300)], 0.0)
        tracker.update([_box(230, 300, w=120, h=300)], 0.5)  # 130 px > 80

        assert len(tracker.tracks) == 1

    def test_la_velocidad_predice_la_proxima_posicion(self):
        tracker = CentroidTracker(max_distance=30.0)
        tracker.update([_box(100, 100)], 0.0)
        tracker.update([_box(125, 100)], 1.0)
        # Sin prediccion, 150 queda a 25 px de 125 (asocia igual); con un
        # objeto que sigue a velocidad constante, 175 esta a 50 del ultimo
        # centroide pero cerca del predicho.
        tracker.update([_box(160, 100)], 2.0)
        tracker.update([_box(195, 100)], 3.0)

        assert len(tracker.tracks) == 1


class TestSegmento:
    LINE = ((50.0, 100.0), (150.0, 100.0))

    def test_cruza_dentro_del_segmento(self):
        assert _crosses_segment((100, 120), (100, 80), self.LINE)

    def test_cruzar_la_recta_fuera_del_segmento_no_cuenta(self):
        assert not _crosses_segment((190, 120), (190, 80), self.LINE)


class TestCruceDeLinea:
    def _analyzer(self, **kwargs) -> LineCrossingAnalyzer:
        return LineCrossingAnalyzer(line=((50, 100), (150, 100)), confirmation_frames=1, **kwargs)

    def test_pasar_al_costado_de_la_linea_no_cuenta(self, fake_backend):
        analyzer = self._analyzer()
        fake_backend.script = [[_box(185, 130)], [_box(185, 70)]]
        analyzer.process_frame(FRAME, 0.0)
        result = analyzer.process_frame(FRAME, 0.2)

        assert result.metrics["total"] == 0

    def test_oscilar_sobre_la_linea_no_recuenta(self, fake_backend):
        """El jitter de la caja de alguien parado sobre la linea no puede
        contar entrada/salida en loop."""
        analyzer = self._analyzer()
        fake_backend.script = [[_box(100, 130)]] + [
            [_box(100, 100 + (1 if i % 2 else -1))] for i in range(8)
        ]
        for i in range(9):
            result = analyzer.process_frame(FRAME, i * 0.2)

        assert result.metrics["total"] == 0

    def test_cruce_real_dispara_trigger_y_solo_en_esa_muestra(self, fake_backend):
        analyzer = self._analyzer()
        fake_backend.script = [[_box(100, 130)], [_box(100, 70)], [_box(100, 60)]]
        first = analyzer.process_frame(FRAME, 0.0)
        crossing = analyzer.process_frame(FRAME, 0.2)
        after = analyzer.process_frame(FRAME, 0.4)

        assert first.triggers == ()
        assert len(crossing.triggers) == 1 and crossing.triggers[0].label == "person"
        assert after.triggers == ()
        assert after.metrics["count_in"] == 1

    def test_sin_sentido_igual_cuenta(self, fake_backend):
        """Antes, desactivar "Aplicar sentido del cruce" dejaba de contar."""
        analyzer = self._analyzer(direction_enabled=False, smart_mark_enabled=True)
        fake_backend.script = [[_box(100, 130)], [_box(100, 70)]]
        analyzer.process_frame(FRAME, 0.0)
        result = analyzer.process_frame(FRAME, 0.2)

        assert result.metrics["total"] == 1
        assert result.metrics["direction_enabled"] is False
        assert result.metrics["last_crossing"] == "Cruce"

    def test_filtro_mejorado_descarta_personas_con_forma_imposible(self, fake_backend):
        wide = _box(100, 130, w=90, h=20)
        analyzer = self._analyzer(enhanced_filter=True)
        fake_backend.script = [[wide]]
        assert analyzer.process_frame(FRAME, 0.0).detections == ()

        analyzer = self._analyzer(enhanced_filter=False)
        fake_backend.script = [[wide]]
        assert len(analyzer.process_frame(FRAME, 0.0).detections) == 1


class TestAforo:
    def test_alarma_solo_al_superar_el_aforo(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1, max_people_alert=2)
        fake_backend.script = [
            [_box(30, 50, 20, 40)],
            [_box(30, 50, 20, 40), _box(150, 50, 20, 40)],
            [_box(30, 50, 20, 40), _box(150, 50, 20, 40)],
        ]
        one = analyzer.process_frame(FRAME, 0.0)
        two = analyzer.process_frame(FRAME, 0.2)
        still_two = analyzer.process_frame(FRAME, 0.4)

        assert one.triggers == ()  # una persona con aforo 2: no alarma
        assert len(two.triggers) == 1 and two.metrics["alerta_maxima"] is True
        assert still_two.triggers == ()  # flanco: no repite mientras siga lleno
        assert still_two.metrics["peak"] == 2

    def test_sin_aforo_cualquier_persona_alarma_intrusion(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1)
        fake_backend.script = [[_box(30, 50, 20, 40)]]
        result = analyzer.process_frame(FRAME, 0.0)

        assert result.triggers is None  # el motor usa las detecciones

    def test_ocupacion_por_zona(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(
            confirmation_frames=1, zones=[(0, 0, 100, 200), (100, 0, 100, 200)]
        )
        # Una llamada a detect() por zona; coordenadas relativas al recorte.
        fake_backend.script = [[_box(30, 50, 20, 40)], [_box(30, 50, 20, 40)]]
        result = analyzer.process_frame(FRAME, 0.0)

        assert result.metrics["zones"] == [1, 1]


class TestMapaDeCalor:
    def test_acumula_y_normaliza(self):
        heatmap = OccupancyHeatmap()
        heatmap.set_frame_size(640, 360)
        heatmap.add([(320, 180)] * 3 + [(10, 10)], timestamp=0.0)
        grid = heatmap.snapshot()

        assert grid.dtype == np.uint8 and grid.shape == (36, 64)
        assert grid.max() == 255
        assert grid[18, 32] > grid[1, 1]

    def test_olvida_con_la_vida_media(self):
        heatmap = OccupancyHeatmap(half_life_s=10.0)
        heatmap.set_frame_size(640, 360)
        heatmap.add([(320, 180)], timestamp=0.0)
        heatmap.decay_to(10.0)

        assert heatmap._grid.max() == pytest.approx(0.5)

    def test_vacio_devuelve_none(self):
        heatmap = OccupancyHeatmap()
        heatmap.set_frame_size(640, 360)
        assert heatmap.snapshot() is None

    def test_conteo_publica_la_grilla(self, fake_backend):
        analyzer = PeopleCountingAnalyzer(confirmation_frames=1, heatmap_enabled=True)
        fake_backend.script = [[_box(30, 50, 20, 40)]]
        result = analyzer.process_frame(FRAME, 0.0)

        assert isinstance(result.metrics["heatmap"], np.ndarray)


class TestTriggersEnElMotor:
    def test_usa_triggers_y_no_las_detecciones(self, monkeypatch):
        engine = AlarmEngine()
        rule = AlarmRule(
            device_id=None,
            analyzer_name="line_crossing",
            object_classes=["person"],
            min_confidence=0.5,
            cooldown_seconds=0,
            severity="medio",
            actions={},
        )
        rule.id = 7
        monkeypatch.setattr(alarm_engine_mod.repository, "list_alarm_rules_for", lambda *_: [rule])
        triggered: list = []
        monkeypatch.setattr(engine, "_trigger", lambda *args: triggered.append(args))
        person = Detection(label="person", confidence=0.9, bbox=(0, 0, 10, 10))

        # Una persona visible que no cruzo: no alarma.
        engine._on_detection(
            DetectionEvent(1, "line_crossing", 0.0, detections=(person,), triggers=())
        )
        assert triggered == []

        engine._on_detection(
            DetectionEvent(1, "line_crossing", 1.0, detections=(person,), triggers=(person,))
        )
        assert len(triggered) == 1


class TestWorkerResiliente:
    def test_un_cuadro_que_falla_no_mata_el_worker(self, monkeypatch, caplog):
        import aurea_vms.core.analytics_engine as engine_mod

        class Broken:
            calls = 0

            def process_frame(self, _frame, _ts):
                Broken.calls += 1
                raise RuntimeError("onnx roto")

            def close(self):
                pass

        monkeypatch.setattr(engine_mod, "create_analyzer", lambda _config: Broken())

        class Config:
            id = 1
            analyzer_name = "people_counting"
            params = {}

        class Device:
            id = 1

        worker = engine_mod.AnalyticsWorker(Config(), Device())
        worker._analyze(FRAME)
        worker._analyze(FRAME)

        assert worker.stats.errors == 2
        assert "onnx roto" in caplog.text
