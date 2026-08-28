"""Cruce de linea: cuenta cuantos objetos trackeados cruzan una linea
virtual, discriminando el sentido del cruce (in/out).

Cada deteccion cruda pasa primero por dos filtros baratos, independientes
del `confidence_threshold` del modelo (ver `object_detector_backend.py`):
forma de caja plausible y tamaño minimo (% del cuadro completo, descarta
detecciones chiquitas/lejanas). Histeresis: un track solo empieza a
evaluarse contra la linea (y solo puede disparar un cruce) una vez
"confirmado" -- sostenido varias muestras seguidas por el CentroidTracker
-- para no contar un cruce falso a partir de una deteccion espuria de un
unico frame."""

from __future__ import annotations

import cv2
import numpy as np

from aurea_vms.core.analytics.base import (
    AnalysisResult,
    Analyzer,
    rescale_bbox,
    resize_for_inference,
)
from aurea_vms.core.analytics.object_detector_backend import (
    create_object_detector,
    deduplicate_by_iou,
    passes_box_shape_filter,
    passes_min_area_filter,
)
from aurea_vms.core.analytics.tracker import CentroidTracker
from aurea_vms.core.events import Detection

Point = tuple[float, float]


def _side_of_line(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> int:
    value = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


class LineCrossingAnalyzer(Analyzer):
    name = "line_crossing"

    def __init__(
        self,
        line: tuple[Point, Point],
        object_classes: list[str] | None = None,
        confidence_threshold: float = 0.5,
        label_in: str = "Entrada",
        label_out: str = "Salida",
        confirmation_frames: int = 2,
        track_max_age_s: float = 1.5,
        min_area_percent: float = 0.15,
    ) -> None:
        classes = object_classes or ["person"]
        self._mp, self._detector = create_object_detector(classes, confidence_threshold)
        (self._x1, self._y1), (self._x2, self._y2) = line
        self.label_in = label_in
        self.label_out = label_out
        self._min_area_percent = max(0.0, min_area_percent)
        self._tracker = CentroidTracker(
            max_age_s=track_max_age_s, min_hits=max(1, confirmation_frames)
        )
        self._count_in = 0
        self._count_out = 0

    def close(self) -> None:
        self._detector.close()

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        frame_area = frame.shape[0] * frame.shape[1]
        small, scale = resize_for_inference(frame)
        inv_scale = 1.0 / scale
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._detector.detect(mp_image)

        raw_detections = []
        for det in result.detections:
            detection = self._to_detection(det, inv_scale)
            if not passes_box_shape_filter(det.bounding_box.width, det.bounding_box.height):
                continue
            w, h = detection.bbox[2], detection.bbox[3]
            if not passes_min_area_filter(w, h, frame_area, self._min_area_percent):
                continue
            raw_detections.append(detection)

        # Dos cajas casi superpuestas sobre el MISMO objeto no deben crear
        # dos tracks (contaria un solo cruce dos veces).
        detections = deduplicate_by_iou(raw_detections)
        self._tracker.update(detections, timestamp)

        for track in self._tracker.confirmed_tracks():
            new_side = _side_of_line(
                track.centroid[0], track.centroid[1], self._x1, self._y1, self._x2, self._y2
            )
            if new_side == 0:
                continue
            if track.side is not None and track.side != new_side:
                if track.side > 0 and new_side < 0:
                    self._count_in += 1
                elif track.side < 0 and new_side > 0:
                    self._count_out += 1
            track.side = new_side

        return AnalysisResult(
            detections=tuple(detections),
            metrics={
                "count_in": self._count_in,
                "count_out": self._count_out,
                "total": self._count_in + self._count_out,
            },
        )

    @staticmethod
    def _to_detection(det, inv_scale: float) -> Detection:
        box = det.bounding_box
        category = det.categories[0]
        raw_bbox = (box.origin_x, box.origin_y, box.width, box.height)
        return Detection(
            label=category.category_name,
            confidence=float(category.score),
            bbox=rescale_bbox(raw_bbox, inv_scale),
        )
