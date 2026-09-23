"""Conteo de personas: ocupacion actual dentro de una zona (ROI).

Usa YOLOX-Tiny (ONNX via onnxruntime, ver `object_detector_backend.py`)
filtrado a la clase "person". Cada deteccion cruda pasa por una cadena
de filtros baratos, independientes del `confidence_threshold` del
modelo, antes de llegar al tracker:

1. Proporcion ancho/alto plausible para una persona parada/sentada (mas
   estricto que el filtro generico compartido con Cruce de Linea -- aca
   TODA deteccion dice ser "person", no hace falta ser generoso con
   autos/motos).
2. Tamaño minimo (% del cuadro, descarta detecciones chiquitas/lejanas
   que suelen ser ruido).
3. Deduplicacion por IoU (ver `object_detector_backend.py`): dos cajas
   casi superpuestas sobre la MISMA persona no deben contarse como dos.

Las que pasan van a un CentroidTracker con histeresis: una persona nueva
no se suma a la ocupacion hasta que la deteccion se sostiene un par de
muestras seguidas (evita que un falso positivo de 1 frame infle el
conteo), y una persona detectada de forma intermitente (oclusion
momentanea, configurable via `track_max_age_s`) no desaparece del conteo
hasta perderse varios frames seguidos (evita el parpadeo del numero)."""

from __future__ import annotations

import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer, crop_to_roi
from aurea_vms.core.analytics.object_detector_backend import (
    YoloxDetector,
    deduplicate_by_iou,
    passes_min_area_filter,
)
from aurea_vms.core.analytics.tracker import CentroidTracker
from aurea_vms.core.events import Detection

# Mas estricto que el BOX_ASPECT_RATIO_RANGE generico de
# object_detector_backend.py: una persona parada, agachada o sentada cae
# ancha comodamente en este rango; una caja mucho mas ancha que alta (un
# umbral de puerta entero, un cartel) no es una persona.
PERSON_ASPECT_RATIO_RANGE = (0.2, 1.6)
HEAD_SHOULDERS_WIDTH_RATIO = 0.72
HEAD_SHOULDERS_HEIGHT_RATIO = 0.55


def _head_shoulders_bbox(
    bbox: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    """Devuelve solo el ancla superior de cabeza y hombros de una persona."""
    x, y, width, height = bbox
    anchor_width = round(width * HEAD_SHOULDERS_WIDTH_RATIO)
    anchor_height = round(height * HEAD_SHOULDERS_HEIGHT_RATIO)
    if anchor_width < 8 or anchor_height < 8:
        return None
    return (x + (width - anchor_width) // 2, y, anchor_width, anchor_height)


def _passes_person_shape_filter(box_w: float, box_h: float) -> bool:
    if box_w <= 0 or box_h <= 0:
        return False
    ratio = box_w / box_h
    return PERSON_ASPECT_RATIO_RANGE[0] <= ratio <= PERSON_ASPECT_RATIO_RANGE[1]


class PeopleCountingAnalyzer(Analyzer):
    name = "people_counting"

    def __init__(
        self,
        confidence_threshold: float = 0.5,
        roi: tuple[int, int, int, int] | None = None,
        confirmation_frames: int = 2,
        track_max_age_s: float = 1.5,
        min_area_percent: float = 0.15,
        zones: list[tuple[int, int, int, int]] | None = None,
        max_people_alert: int = 0,
        head_shoulders_detection: bool = True,
        heatmap_enabled: bool = False,
    ) -> None:
        self._detector = YoloxDetector()
        self._confidence_threshold = confidence_threshold
        self._roi = roi
        self._rois = zones or ([roi] if roi is not None else [None])
        self._max_people_alert = max(0, int(max_people_alert))
        # El conteo siempre usa el ancla cabeza-hombros; no se permite volver
        # a una identidad basada en la caja corporal completa.
        self._head_shoulders_detection = True
        self._heatmap_enabled = heatmap_enabled
        self._heatmap_points: list[tuple[int, int]] = []
        self._min_area_percent = max(0.0, min_area_percent)
        self._tracker = CentroidTracker(
            max_distance=140.0,
            max_age_s=track_max_age_s,
            min_hits=max(1, confirmation_frames),
            min_iou=0.12,
        )

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        raw_detections: list[Detection] = []
        for roi in self._rois:
            crop, offset_x, offset_y = crop_to_roi(frame, roi)
            crop_area = crop.shape[0] * crop.shape[1]
            for det in self._detector.detect(crop, ["person"], self._confidence_threshold):
                x, y, w, h = det.bbox
                if not _passes_person_shape_filter(w, h):
                    continue
                if not passes_min_area_filter(w, h, crop_area, self._min_area_percent):
                    continue
                # YOLOX no expone keypoints; cabeza y hombros se usan como
                # ancla geometrica para descartar cajas degeneradas.
                if self._head_shoulders_detection and (h < 8 or w > h * 1.6):
                    continue
                tracking_bbox = _head_shoulders_bbox((x, y, w, h))
                if tracking_bbox is None:
                    continue
                raw_detections.append(
                    Detection(
                        label=det.label,
                        confidence=det.confidence,
                        bbox=(
                            tracking_bbox[0] + offset_x,
                            tracking_bbox[1] + offset_y,
                            tracking_bbox[2],
                            tracking_bbox[3],
                        ),
                    )
                )

        self._tracker.update(deduplicate_by_iou(raw_detections), timestamp)
        confirmed = [
            Detection(label=track.label, confidence=track.confidence, bbox=track.bbox)
            for track in self._tracker.confirmed_tracks()
        ]

        metrics = {"occupancy": len(confirmed)}
        if self._max_people_alert:
            metrics["alerta_maxima"] = len(confirmed) >= self._max_people_alert
        if self._heatmap_enabled:
            self._heatmap_points.extend(
                (det.bbox[0] + det.bbox[2] // 2, det.bbox[1] + det.bbox[3]) for det in confirmed
            )
            self._heatmap_points = self._heatmap_points[-200:]
            metrics["heatmap"] = self._heatmap_points
        return AnalysisResult(detections=tuple(confirmed), metrics=metrics)

    def close(self) -> None:
        """Suelta la sesion ONNX compartida. Sin esto el contrato de
        Analyzer.close() era un hook vacio y el modelo quedaba vivo hasta
        que lo juntara el GC -- con los destructores nativos corriendo
        recien al cierre del interprete, que es el escenario que
        core/analytics/base.py señala como riesgo de crash en headless."""
        self._detector.close()
