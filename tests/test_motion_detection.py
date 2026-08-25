from __future__ import annotations

import numpy as np

from aurea_vms.core.analytics.motion_detection_analyzer import MotionDetectionAnalyzer


def _static_frame() -> np.ndarray:
    return np.full((240, 320, 3), 30, dtype=np.uint8)


def _frame_with_object() -> np.ndarray:
    frame = _static_frame()
    frame[60:180, 100:220] = 220  # "objeto" grande y brillante
    return frame


def _warmup(analyzer: MotionDetectionAnalyzer, frames: int = 30) -> None:
    for i in range(frames):
        analyzer.process_frame(_static_frame(), timestamp=float(i))


class TestMotionDetection:
    def test_escena_estatica_sin_detecciones(self):
        analyzer = MotionDetectionAnalyzer(sensitivity=50, min_area_percent=0.5)
        _warmup(analyzer)

        result = analyzer.process_frame(_static_frame(), timestamp=100.0)
        assert result.detections == ()
        assert result.metrics == {"regiones": 0}

    def test_objeto_nuevo_dispara_deteccion_con_poligono(self):
        analyzer = MotionDetectionAnalyzer(sensitivity=50, min_area_percent=0.5)
        _warmup(analyzer)

        result = analyzer.process_frame(_frame_with_object(), timestamp=100.0)

        assert len(result.detections) >= 1
        det = result.detections[0]
        assert det.label == "movimiento"
        assert det.polygon is not None
        x, y, w, h = det.bbox
        # El bbox tiene que solaparse con la region del objeto (100..220, 60..180),
        # con margen por la dilatacion morfologica.
        assert x < 220 and x + w > 100
        assert y < 180 and y + h > 60

    def test_area_minima_filtra_objetos_chicos(self):
        # min_area_percent altisimo: ni un objeto grande alcanza.
        analyzer = MotionDetectionAnalyzer(sensitivity=50, min_area_percent=50.0)
        _warmup(analyzer)

        result = analyzer.process_frame(_frame_with_object(), timestamp=100.0)
        assert result.detections == ()

    def test_roi_desplaza_las_coordenadas_al_frame_nativo(self):
        roi = (80, 40, 200, 160)  # contiene al objeto
        analyzer = MotionDetectionAnalyzer(sensitivity=50, min_area_percent=0.5, roi=roi)
        _warmup(analyzer)

        result = analyzer.process_frame(_frame_with_object(), timestamp=100.0)

        assert len(result.detections) >= 1
        x, y, _w, _h = result.detections[0].bbox
        # Coordenadas en pixeles del frame completo (>= offset del ROI).
        assert x >= 80 and y >= 40

    def test_sensitivity_se_acota_a_rango_valido(self):
        # No debe explotar con valores fuera de 1-100.
        MotionDetectionAnalyzer(sensitivity=-5)
        MotionDetectionAnalyzer(sensitivity=500)
