"""Registro de analizadores disponibles + factory desde un AnalyticsConfig
persistido en la base."""

from __future__ import annotations

from aurea_vms.core.analytics.base import Analyzer
from aurea_vms.core.analytics.face_detection_analyzer import FaceDetectionAnalyzer
from aurea_vms.core.analytics.line_crossing_analyzer import LineCrossingAnalyzer
from aurea_vms.core.analytics.monitor_tamper_analyzer import MonitorTamperAnalyzer
from aurea_vms.core.analytics.motion_detection_analyzer import MotionDetectionAnalyzer
from aurea_vms.core.analytics.people_counting_analyzer import PeopleCountingAnalyzer
from aurea_vms.models.analytics_config import AnalyticsConfig

ANALYZER_DISPLAY_NAMES: dict[str, str] = {
    "monitor_tamper": "Detección de incidentes",
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

    # Compatibilidad de API para configuraciones antiguas: la UI ya no expone
    # movimiento, pero integraciones/tests que aún construyen el nombre
    # legado siguen pudiendo crear su analizador explícitamente.
    if config.analyzer_name == "motion_detection":
        return MotionDetectionAnalyzer(
            sensitivity=params.get("sensitivity", 50),
            min_area_percent=params.get("min_area_percent", 0.5),
            roi=_roi_from_config(config),
            confirmation_frames=params.get("confirmation_frames", 2),
        )

    if config.analyzer_name == "monitor_tamper":
        # Cada zona es una pantalla. Sin zonas se usa el ROI simple, si hay.
        zones = [
            tuple(zone)
            for zone in params.get("zones", [])
            if isinstance(zone, (list, tuple)) and len(zone) == 4
        ]
        roi = _roi_from_config(config)
        if not zones and roi is not None:
            zones = [roi]
        return MonitorTamperAnalyzer(
            zones=zones,
            crop_expansion=params.get("crop_expansion", 2.2),
            keypoint_min_score=params.get("keypoint_min_score", 0.30),
            hand_strikes_enabled=params.get("hand_strikes_enabled", True),
            hand_strike_speed=params.get("hand_strike_speed", 2.0),
            confirmation_frames=params.get("confirmation_frames", 1),
            alert_hold_s=params.get("alert_hold_s", 4.0),
        )

    if config.analyzer_name == "people_counting":
        zones = [
            tuple(zone)
            for zone in params.get("zones", [])
            if isinstance(zone, (list, tuple)) and len(zone) == 4
        ]
        return PeopleCountingAnalyzer(
            confidence_threshold=config.confidence_threshold,
            roi=_roi_from_config(config),
            confirmation_frames=params.get("confirmation_frames", 2),
            min_area_percent=params.get("min_area_percent", 0.15),
            track_max_age_s=params.get("track_max_age_s", 1.5),
            zones=zones or None,
            max_people_alert=params.get("max_people_alert", 0),
            head_shoulders_detection=params.get("head_shoulders_detection", True),
            heatmap_enabled=params.get("heatmap_enabled", True),
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
            min_area_percent=params.get("min_area_percent", 0.15),
            direction_enabled=params.get("direction_enabled", True),
            smart_mark_enabled=params.get("smart_mark_enabled", False),
            enhanced_filter=params.get("enhanced_filter", True),
        )

    if config.analyzer_name == "face_detection":
        return FaceDetectionAnalyzer(
            confidence_threshold=config.confidence_threshold,
            roi=_roi_from_config(config),
            min_pupillary_distance_px=params.get("min_pupillary_distance_px", 40),
            confirmation_frames=params.get("confirmation_frames", 2),
            tilted_faces_filter=params.get("tilted_faces_filter", True),
        )

    raise ValueError(f"Analizador desconocido: {config.analyzer_name}")
