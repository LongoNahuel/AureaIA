from __future__ import annotations

import pytest

from aurea_vms.core.analytics.motion_detection_analyzer import MotionDetectionAnalyzer
from aurea_vms.core.analytics.registry import (
    ANALYZER_DISPLAY_NAMES,
    AVAILABLE_ANALYZERS,
    _roi_from_config,
    create_analyzer,
)
from aurea_vms.models.analytics_config import AnalyticsConfig


def _config(analyzer_name: str, **overrides) -> AnalyticsConfig:
    fields = {
        "device_id": 1,
        "analyzer_name": analyzer_name,
        "enabled": True,
        "confidence_threshold": 0.5,
        "object_classes": [],
        "params": {},
    }
    fields.update(overrides)
    return AnalyticsConfig(**fields)


def test_todos_los_analizadores_tienen_display_name():
    assert set(AVAILABLE_ANALYZERS) == set(ANALYZER_DISPLAY_NAMES)


def test_roi_completo_e_incompleto():
    completo = _config("motion_detection", roi_x=1, roi_y=2, roi_w=3, roi_h=4)
    assert _roi_from_config(completo) == (1, 2, 3, 4)

    incompleto = _config("motion_detection", roi_x=1, roi_y=2, roi_w=None, roi_h=4)
    assert _roi_from_config(incompleto) is None


def test_crea_motion_detection_con_params():
    analyzer = create_analyzer(
        _config("motion_detection", params={"sensitivity": 80, "min_area_percent": 2.0})
    )
    assert isinstance(analyzer, MotionDetectionAnalyzer)
    assert analyzer.name == "motion_detection"


def test_line_crossing_sin_linea_configurada_falla():
    with pytest.raises(ValueError, match="línea"):
        create_analyzer(_config("line_crossing"))


def test_analizador_desconocido_falla():
    with pytest.raises(ValueError, match="desconocido"):
        create_analyzer(_config("no_existe"))


@pytest.mark.integration
def test_crea_los_cuatro_analizadores_reales():
    """Carga los modelos .tflite reales (los pesa el repo) -- marcado como
    integracion porque tarda y necesita mediapipe funcional. Los detectores
    se cierran explicitamente: los destructores nativos de MediaPipe
    corriendo al cierre del interprete pueden segfaultear en runners
    headless aunque el test haya pasado."""
    analyzers = [
        create_analyzer(_config("motion_detection")),
        create_analyzer(_config("people_counting")),
        create_analyzer(_config("face_detection")),
        create_analyzer(_config("line_crossing", params={"line": [[0, 0], [100, 100]]})),
    ]
    for analyzer in analyzers:
        analyzer.close()
