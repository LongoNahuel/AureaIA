"""Zoom digital de la analitica (2026-10-03): la analitica corre sobre un
recorte del cuadro y sus resultados vuelven en pixeles del cuadro completo;
la configuracion se guarda siempre sobre el cuadro completo."""

from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer
from aurea_vms.core.analytics.digital_zoom import (
    OUTSIDE_ZOOM,
    ZoomedAnalyzer,
    active_zoom,
    clip_rect,
    wheel_inside,
    zones_outside,
    zoom_region,
    zoomed_config,
)
from aurea_vms.core.analytics.registry import create_analyzer
from aurea_vms.core.analytics.roulette_analyzer import RouletteAnalyzer, detect_table
from aurea_vms.core.analytics.table_hands import PERSON_MAX_AREA, TableHands
from aurea_vms.core.events import Detection

REGION = (400, 300, 640, 480)


def _config(params, analyzer_name="monitor_tamper", roi=(None, None, None, None)):
    return SimpleNamespace(
        analyzer_name=analyzer_name,
        params=params,
        roi_x=roi[0],
        roi_y=roi[1],
        roi_w=roi[2],
        roi_h=roi[3],
        confidence_threshold=0.5,
        object_classes=None,
    )


class _Recorder(Analyzer):
    """Guarda el cuadro que recibe y devuelve resultados fijos, en pixeles
    del recorte."""

    name = "monitor_tamper"

    def __init__(self, result: AnalysisResult) -> None:
        self.result = result
        self.frames: list[np.ndarray] = []
        self.resets = 0
        self.closed = False

    def process_frame(self, frame, timestamp):
        self.frames.append(frame)
        return self.result

    def reset_counters(self):
        self.resets += 1

    def close(self):
        self.closed = True


class TestConfiguracion:
    def test_el_zoom_valido(self):
        assert zoom_region({"zoom_digital": [10, 20, 300, 200]}) == (10, 20, 300, 200)
        assert zoom_region({"zoom_digital": [10.4, 20.6, 300, 200]}) == (10, 21, 300, 200)
        assert zoom_region({}) is None
        assert zoom_region({"zoom_digital": [10, 20, 8, 200]}) is None  # demasiado chico
        assert zoom_region({"zoom_digital": [-5, 20, 300, 200]}) is None
        assert zoom_region({"zoom_digital": "x"}) is None

    def test_solo_se_analiza_el_zoom_si_se_pide(self):
        params = {"zoom_digital": list(REGION)}
        assert active_zoom("monitor_tamper", params) is None
        assert active_zoom("monitor_tamper", {**params, "analizar_zoom": True}) == REGION
        # Las analiticas ocultas no saben trasladar sus metricas.
        assert active_zoom("people_counting", {**params, "analizar_zoom": True}) is None

    def test_recorte_de_un_rectangulo(self):
        assert clip_rect((350, 350, 100, 100), REGION) == (400, 350, 50, 100)
        assert clip_rect((0, 0, 100, 100), REGION) is None

    def test_las_coordenadas_pasan_al_recorte(self):
        params = {
            "modo": "ruleta",
            "zones": [[500, 400, 100, 50], [0, 0, 50, 50], [1000, 700, 100, 100]],
            "rueda_zona": [600, 350, 200, 200],
            "rueda": [700.0, 450.0, 180.0, 170.0, 5.0],
            "zoom_digital": list(REGION),
            "analizar_zoom": True,
        }
        zoomed, kept, count = zoomed_config(_config(params), REGION)

        # La zona 2 queda afuera; la 3 se recorta al borde del zoom.
        assert kept == [0, 2] and count == 3
        assert zoomed.params["zones"] == [[100, 100, 100, 50], [600, 400, 40, 80]]
        assert zoomed.params["rueda_zona"] == [200, 50, 200, 200]
        assert zoomed.params["rueda"] == [300.0, 150.0, 180.0, 170.0, 5.0]
        assert zoomed.params["modo"] == "ruleta"
        # La configuracion guardada no cambia.
        assert params["zones"][0] == [500, 400, 100, 50]

    def test_la_rueda_fuera_del_zoom_se_busca_sola(self):
        params = {"rueda": [100.0, 100.0, 180.0, 170.0, 0.0], "rueda_zona": [10, 10, 180, 180]}
        zoomed, _, _ = zoomed_config(_config(params), REGION)

        assert "rueda" not in zoomed.params and zoomed.params["rueda_zona"] is None
        assert wheel_inside(params, REGION) is False
        assert wheel_inside({}, REGION) is True

    def test_sin_zonas_vale_el_roi(self):
        zoomed, kept, count = zoomed_config(_config({}, roi=(450, 320, 60, 60)), REGION)

        assert zoomed.params["zones"] == [[50, 20, 60, 60]] and zoomed.roi_x is None
        assert kept == [0] and count == 1

    def test_zonas_fuera_del_zoom(self):
        params = {"zones": [[500, 400, 100, 50], [0, 0, 50, 50]]}
        assert zones_outside(params, REGION) == [1]

    def test_golpes_sin_ninguna_zona_adentro_no_arranca(self):
        params = {
            "modo": "golpes",
            "zones": [[0, 0, 50, 50]],
            "zoom_digital": list(REGION),
            "analizar_zoom": True,
        }
        with pytest.raises(ValueError, match="zoom digital"):
            create_analyzer(_config(params))


class TestAnalizadorConZoom:
    def _zoomed(self, result, kept=(0,), count=1):
        inner = _Recorder(result)
        return ZoomedAnalyzer(inner, REGION, list(kept), count), inner

    def test_recibe_el_recorte_y_el_tamano_del_cuadro(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        frame[300:780, 400:1040] = 200
        analyzer, inner = self._zoomed(AnalysisResult())

        analyzer.process_frame(frame, 1.0)

        assert inner.frames[0].shape == (480, 640, 3)
        assert inner.frames[0].min() == 200
        assert inner.frames[0].flags["C_CONTIGUOUS"]
        assert inner.source_size == (1920, 1080)

    def test_las_detecciones_vuelven_al_cuadro_completo(self):
        hit = Detection("fichas_tras_no_va_mas", 1.0, (10, 20, 30, 40), keypoints=((5.0, 6.0),))
        moved = Detection("fichas_movidas", 1.0, (0, 0, 5, 5))
        analyzer, _ = self._zoomed(AnalysisResult(detections=(hit, moved), triggers=(hit,)))

        result = analyzer.process_frame(np.zeros((1080, 1920, 3), np.uint8), 1.0)

        assert [d.bbox for d in result.detections] == [(410, 320, 30, 40), (400, 300, 5, 5)]
        assert result.detections[0].keypoints == ((405.0, 306.0),)
        # El trigger sigue siendo la misma deteccion.
        assert result.triggers[0] is result.detections[0]

    def test_sin_triggers_sigue_sin_triggers(self):
        analyzer, _ = self._zoomed(AnalysisResult(detections=()))
        result = analyzer.process_frame(np.zeros((1080, 1920, 3), np.uint8), 1.0)
        assert result.triggers is None

    def test_las_marcas_de_la_ruleta_vuelven_al_cuadro_completo(self):
        metrics = {
            "modo": "ruleta",
            "bola": [100.0, 50.0],
            "rueda": [300.0, 150.0, 180.0, 170.0, 5.0],
            "personas": [{"caja": [10, 10, 100, 200], "rol": "crupier", "id": 0}],
            "manos": [{"x": 50.0, "y": 60.0, "caja": [40, 50, 20, 20], "zona": 0}],
            "zonas": [{"estado": "alerta", "posiciones": [[5, 5, 10, 10]], "indice": 0}],
        }
        analyzer, _ = self._zoomed(AnalysisResult(metrics=metrics))

        out = analyzer.process_frame(np.zeros((1080, 1920, 3), np.uint8), 1.0).metrics

        assert out["bola"] == [500.0, 350.0]
        assert out["rueda"] == [700.0, 450.0, 180.0, 170.0, 5.0]
        assert out["personas"][0]["caja"] == [410, 310, 100, 200]
        assert out["manos"][0] == {"x": 450.0, "y": 360.0, "caja": [440, 350, 20, 20], "zona": 0}
        assert out["zonas"][0]["posiciones"] == [[405, 305, 10, 10]]
        assert out["zoom_digital"] == list(REGION)
        # Las metricas del analizador no se tocan.
        assert metrics["bola"] == [100.0, 50.0]

    def test_las_zonas_fuera_del_zoom_quedan_en_su_lugar(self):
        metrics = {
            "zonas": [{"estado": "normal", "indice": 0}, {"estado": "alerta", "indice": 1}],
            "ultimo_incidente": {"t": 1.0, "zona": 1, "motivo": "no_va_mas"},
            "ultimo_movimiento": {"t": 1.0, "zona": 0},
            "manos": [{"x": 1.0, "y": 1.0, "zona": 1}, {"x": 1.0, "y": 1.0, "zona": None}],
        }
        # La configuracion tiene 3 zonas; la del medio quedo fuera del zoom.
        analyzer, _ = self._zoomed(AnalysisResult(metrics=metrics), kept=(0, 2), count=3)

        out = analyzer.process_frame(np.zeros((1080, 1920, 3), np.uint8), 1.0).metrics

        assert [zone["estado"] for zone in out["zonas"]] == ["normal", OUTSIDE_ZOOM, "alerta"]
        assert out["zonas"][2]["indice"] == 2
        assert out["ultimo_incidente"]["zona"] == 2
        assert out["ultimo_movimiento"]["zona"] == 0
        assert [hand["zona"] for hand in out["manos"]] == [2, None]

    def test_limpiar_y_cerrar_llegan_al_analizador(self):
        analyzer, inner = self._zoomed(AnalysisResult())
        analyzer.reset_counters()
        analyzer.close()
        assert inner.resets == 1 and inner.closed

    def test_un_zoom_fuera_del_cuadro_es_un_error(self):
        analyzer, _ = self._zoomed(AnalysisResult())
        with pytest.raises(ValueError, match="fuera del cuadro"):
            analyzer.process_frame(np.zeros((240, 320, 3), np.uint8), 1.0)


def _wheel_frame(angle_deg: float, offset=(0, 0)) -> np.ndarray:
    """Un plato de 37 bolsillos girado `angle_deg`, de radio 150, con su
    centro en (320, 240) + `offset`, sobre un cuadro de 1080x1920."""
    frame = np.full((1080, 1920, 3), 90, dtype=np.uint8)
    center = (320 + offset[0], 240 + offset[1])
    cv2.circle(frame, center, 175, (40, 60, 90), -1)
    for pocket in range(37):
        start = angle_deg + pocket * 360 / 37
        color = (60, 170, 40) if pocket == 0 else ((30, 30, 200) if pocket % 2 else (25, 25, 25))
        cv2.ellipse(frame, center, (150, 150), 0, start, start + 360 / 37, color, -1)
        mid = np.deg2rad(start + 180 / 37)
        spot = (int(center[0] + 127 * np.cos(mid)), int(center[1] + 127 * np.sin(mid)))
        cv2.circle(frame, spot, 1 + pocket % 3, (230, 230, 230), -1)
    cv2.circle(frame, center, 67, (15, 15, 15), -1)
    return frame


class TestRuletaConZoom:
    def test_la_rueda_se_ubica_y_se_mide_en_el_zoom(self):
        params = {
            "modo": "ruleta",
            "zones": [[REGION[0] + 500, REGION[1] + 50, 100, 100]],
            "manos": False,
            "zoom_digital": list(REGION),
            "analizar_zoom": True,
        }
        analyzer = create_analyzer(_config(params))
        assert isinstance(analyzer, ZoomedAnalyzer)
        assert isinstance(analyzer.inner, RouletteAnalyzer)

        metrics = None
        for i in range(30):
            frame = _wheel_frame(26.0 * i, offset=REGION[:2])
            metrics = analyzer.process_frame(frame, i / 5.0).metrics
        analyzer.close()

        # La rueda, en pixeles del cuadro completo.
        cx, cy = metrics["rueda"][:2]
        assert cx == pytest.approx(REGION[0] + 320, abs=4)
        assert cy == pytest.approx(REGION[1] + 240, abs=4)
        assert metrics["velocidad_deg_s"] == pytest.approx(130.0, rel=0.08)
        assert metrics["zoom_digital"] == list(REGION)

    def test_sin_zoom_analiza_el_cuadro_completo(self):
        params = {"modo": "ruleta", "zones": [[0, 0, 100, 100]], "manos": False}
        analyzer = create_analyzer(_config({**params, "zoom_digital": list(REGION)}))
        assert isinstance(analyzer, RouletteAnalyzer)
        analyzer.close()


class TestMedidasDelCuadroCompleto:
    def test_el_tope_de_persona_es_del_cuadro_completo(self):
        # Una persona de 200x300 en un recorte de 320x480 ocupa el 39%: sin
        # el tamaño del cuadro completo se descartaba como "varias personas".
        box = (60, 100, 200, 300)

        class _Detector:
            def detect(self, _frame, _classes, _min_confidence):
                return [Detection("person", 0.9, box)]

            def close(self):
                pass

        class _Estimator:
            def estimate(self, _frame, _box):
                from aurea_vms.core.analytics.pose_backend import Pose

                return Pose(points=np.zeros((17, 2), np.float32), scores=np.zeros(17, np.float32))

            def close(self):
                pass

        crop = np.zeros((480, 320, 3), np.uint8)
        assert box[2] * box[3] > PERSON_MAX_AREA * 320 * 480
        hands = TableHands(lambda x, y: False, detector=_Detector(), estimator=_Estimator())
        hands.people(crop)
        assert hands._tracks == []

        hands = TableHands(lambda x, y: False, detector=_Detector(), estimator=_Estimator())
        hands.reference_size = (1920, 1080)
        hands.people(crop)
        assert len(hands._tracks) == 1

    def test_el_margen_de_la_mesa_es_del_cuadro_completo(self):
        # Paño crema (claro y poco saturado) en el medio de un recorte oscuro.
        crop = np.full((400, 400, 3), 60, np.uint8)
        crop[150:250, 150:250] = (190, 200, 205)
        own = detect_table(crop)
        full = detect_table(crop, reference_width=1600)

        # La baranda mide lo mismo en pixeles que sobre el cuadro completo:
        # 5% de 1600 px, no de los 400 del recorte.
        assert own is not None and full is not None
        assert full.sum() > own.sum()
