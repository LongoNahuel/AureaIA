"""Cruce de linea: cuenta cuantos objetos trackeados cruzan una linea
virtual, discriminando el sentido del cruce (in/out).

Usa YOLOX-Tiny (ONNX via onnxruntime, ver `object_detector_backend.py`).
Cada deteccion cruda pasa primero por tres filtros baratos, independientes
del `confidence_threshold` del modelo: forma de caja plausible, tamaño
minimo (% del cuadro completo, descarta detecciones chiquitas/lejanas) y
deduplicacion por IoU (dos cajas casi superpuestas sobre el MISMO objeto
no deben contar dos cruces). Histeresis: un track solo empieza a
evaluarse contra la linea (y solo puede disparar un cruce) una vez
"confirmado" -- sostenido varias muestras seguidas por el CentroidTracker
-- para no contar un cruce falso a partir de una deteccion espuria de un
unico frame."""

from __future__ import annotations

import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer
from aurea_vms.core.analytics.object_detector_backend import (
    YoloxDetector,
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
        direction_enabled: bool = True,
        smart_mark_enabled: bool = False,
        enhanced_filter: bool = True,
    ) -> None:
        self._detector = YoloxDetector()
        self._classes = object_classes or ["person"]
        self._confidence_threshold = confidence_threshold
        (self._x1, self._y1), (self._x2, self._y2) = line
        self.label_in = label_in
        self.label_out = label_out
        self._min_area_percent = max(0.0, min_area_percent)
        self._direction_enabled = direction_enabled
        self._smart_mark_enabled = smart_mark_enabled
        self._enhanced_filter = enhanced_filter
        self._tracker = CentroidTracker(
            max_age_s=track_max_age_s, min_hits=max(1, confirmation_frames)
        )
        self._count_in = 0
        self._count_out = 0

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        frame_area = frame.shape[0] * frame.shape[1]

        raw_detections: list[Detection] = []
        for det in self._detector.detect(frame, self._classes, self._confidence_threshold):
            w, h = det.bbox[2], det.bbox[3]
            if not passes_box_shape_filter(w, h):
                continue
            if not passes_min_area_filter(w, h, frame_area, self._min_area_percent):
                continue
            raw_detections.append(det)

        # Dos cajas casi superpuestas sobre el MISMO objeto no deben crear
        # dos tracks (contaria un solo cruce dos veces).
        detections = deduplicate_by_iou(raw_detections)
        self._tracker.update(detections, timestamp)

        last_crossing = None
        for track in self._tracker.confirmed_tracks():
            new_side = _side_of_line(
                track.centroid[0], track.centroid[1], self._x1, self._y1, self._x2, self._y2
            )
            if new_side == 0:
                continue
            if self._direction_enabled and track.side is not None and track.side != new_side:
                if track.side > 0 and new_side < 0:
                    self._count_in += 1
                    last_crossing = self.label_in
                elif track.side < 0 and new_side > 0:
                    self._count_out += 1
                    last_crossing = self.label_out
            track.side = new_side

        metrics = {
            "count_in": self._count_in,
            "count_out": self._count_out,
            "total": self._count_in + self._count_out,
        }
        if self._smart_mark_enabled and last_crossing:
            metrics["last_crossing"] = last_crossing
        return AnalysisResult(detections=tuple(detections), metrics=metrics)

    def close(self) -> None:
        """Suelta la sesion ONNX compartida. Sin esto el contrato de
        Analyzer.close() era un hook vacio y el modelo quedaba vivo hasta
        que lo juntara el GC -- con los destructores nativos corriendo
        recien al cierre del interprete, que es el escenario que
        core/analytics/base.py señala como riesgo de crash en headless."""
        self._detector.close()
