from __future__ import annotations

import numpy as np
import pytest

from aurea_vms.core.analytics.base import AnalysisResult
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


# Configs de los analizadores que cargan modelo real, para los tests de
# integracion. line_crossing y monitor_tamper no arrancan sin geometria.
_CONFIGS_REALES = [
    ("monitor_tamper", {"zones": [[220, 90, 200, 150]]}),
    ("people_counting", {}),
    ("face_detection", {}),
    ("line_crossing", {"line": [[0, 180], [640, 180]]}),
    ("motion_detection", {}),  # legacy, sigue construible desde el registry
]


def _escena_sintetica() -> np.ndarray:
    """Frame con algo de estructura: gradiente de fondo y dos rectangulos.
    No busca disparar detecciones (un modelo real no ve personas aca), sino
    que el pipeline corra sobre datos que no son todo ceros."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:, :] = np.linspace(20, 200, 640, dtype=np.uint8)[None, :, None]
    frame[120:360, 200:280] = 240
    frame[300:420, 420:560] = 15
    return frame


@pytest.mark.integration
@pytest.mark.parametrize(("nombre", "params"), _CONFIGS_REALES)
def test_los_analizadores_reales_procesan_un_frame(nombre, params):
    """Carga los modelos .onnx reales y **corre inferencia**.

    Hasta la Fase 6 este test construia los analizadores y llamaba close()
    sin llamar nunca a process_frame: una regresion en el letterbox, la
    decodificacion de cabezas, el NMS o YuNet no la detectaba nadie (los
    unitarios reemplazan el detector entero por un doble). Las aserciones
    son estructurales a proposito: fijar detecciones concretas seria fragil
    entre versiones de onnxruntime, pero una caja fuera del frame o una
    confianza fuera de rango es inequivocamente un bug nuestro.
    """
    frame = _escena_sintetica()
    alto, ancho = frame.shape[:2]
    analyzer = create_analyzer(_config(nombre, params=params))
    try:
        result = analyzer.process_frame(frame, 0.0)
    finally:
        analyzer.close()

    assert isinstance(result, AnalysisResult)
    assert isinstance(result.metrics, dict)
    for det in result.detections:
        assert isinstance(det.label, str) and det.label
        assert 0.0 <= det.confidence <= 1.0
        x, y, w, h = det.bbox
        assert w > 0 and h > 0
        # Una regresion del letterbox se manifiesta justo aca: coordenadas
        # del espacio 416x416 filtradas al frame original.
        assert 0 <= x <= ancho and 0 <= y <= alto
        assert x + w <= ancho and y + h <= alto


@pytest.mark.integration
@pytest.mark.parametrize(("nombre", "params"), _CONFIGS_REALES)
def test_la_inferencia_es_determinista(nombre, params):
    """Dos analizadores recien creados, el mismo frame: el mismo resultado.
    Los analizadores guardan estado entre llamadas (fondo de MOG2, tracks,
    histeresis de puerta), por eso se compara la PRIMERA llamada de dos
    instancias limpias y no dos llamadas a la misma."""
    frame = _escena_sintetica()
    salidas = []
    for _ in range(2):
        analyzer = create_analyzer(_config(nombre, params=params))
        try:
            salidas.append(analyzer.process_frame(frame, 0.0))
        finally:
            analyzer.close()

    assert salidas[0] == salidas[1]
