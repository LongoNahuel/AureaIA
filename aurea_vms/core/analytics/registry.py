"""Registro de analizadores disponibles + factory desde un AnalyticsConfig
persistido en la base."""

from __future__ import annotations

from aurea_vms.core.analytics.base import Analyzer
from aurea_vms.core.analytics.face_detection_analyzer import FaceDetectionAnalyzer
from aurea_vms.core.analytics.line_crossing_analyzer import LineCrossingAnalyzer
from aurea_vms.core.analytics.motion_detection_analyzer import MotionDetectionAnalyzer
from aurea_vms.core.analytics.people_counting_analyzer import PeopleCountingAnalyzer
from aurea_vms.models.analytics_config import AnalyticsConfig

ANALYZER_DISPLAY_NAMES: dict[str, str] = {
    "motion_detection": "Detección de Movimiento",
    "people_counting": "Conteo de Personas",
    "line_crossing": "Cruce de Línea",
    "face_detection": "Detección Facial",
}

AVAILABLE_ANALYZERS: list[str] = list(ANALYZER_DISPLAY_NAMES.keys())


def _roi_from_config(config: AnalyticsConfig) -> tuple[int, int, int, int] | None:
    if None in (config.roi_x, config.roi_y, config.roi_w, config.roi_h):
        return None
    return (config.roi_x, config.roi_y, config.roi_w, config.roi_h)


def create_analyzer(config: AnalyticsConfig) -> Analyzer:
    params = config.params or {}

    if config.analyzer_name == "motion_detection":
        return MotionDetectionAnalyzer(
            sensitivity=params.get("sensitivity", 50),
            min_area_percent=params.get("min_area_percent", 0.5),
            roi=_roi_from_config(config),
        )

    if config.analyzer_name == "people_counting":
        return PeopleCountingAnalyzer(
            confidence_threshold=config.confidence_threshold,
            roi=_roi_from_config(config),
            confirmation_frames=params.get("confirmation_frames", 2),
        )

    if config.analyzer_name == "line_crossing":
        line = params.get("line")
        if not line:
            raise ValueError("El analizador de cruce de línea necesita una línea configurada")
        return LineCrossingAnalyzer(
            line=(tuple(line[0]), tuple(line[1])),
            object_classes=config.object_classes or ["person"],
            confidence_threshold=config.confidence_threshold,
            label_in=params.get("label_in", "Entrada"),
            label_out=params.get("label_out", "Salida"),
            confirmation_frames=params.get("confirmation_frames", 2),
        )

    if config.analyzer_name == "face_detection":
        return FaceDetectionAnalyzer(
            confidence_threshold=config.confidence_threshold,
            roi=_roi_from_config(config),
            min_pupillary_distance_px=params.get("min_pupillary_distance_px", 40),
            filter_by_angle=params.get("filter_by_angle", False),
            confirmation_frames=params.get("confirmation_frames", 2),
        )

    raise ValueError(f"Analizador desconocido: {config.analyzer_name}")
