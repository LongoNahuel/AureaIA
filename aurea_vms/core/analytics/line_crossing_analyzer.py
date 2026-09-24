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

import math

import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer
from aurea_vms.core.analytics.object_detector_backend import (
    YoloxDetector,
    deduplicate_by_iou,
    passes_box_shape_filter,
    passes_min_area_filter,
)
from aurea_vms.core.analytics.people_counting_analyzer import PERSON_ASPECT_RATIO_RANGE
from aurea_vms.core.analytics.tracker import CentroidTracker
from aurea_vms.core.events import Detection

Point = tuple[float, float]


# Banda muerta alrededor de la linea (ver LineCrossingAnalyzer._stable_side).
DEAD_BAND_HEIGHT_RATIO = 0.1
MIN_DEAD_BAND_PX = 2.0
# Tolerancia en los extremos del segmento (fraccion de su largo): un cruce
# rozando la punta de la linea dibujada sigue contando.
SEGMENT_END_TOLERANCE = 0.05


def _signed_distance(point: Point, line: tuple[Point, Point]) -> float:
    (x1, y1), (x2, y2) = line
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return 0.0
    return ((x2 - x1) * (point[1] - y1) - (y2 - y1) * (point[0] - x1)) / length


def _crosses_segment(start: Point, end: Point, line: tuple[Point, Point]) -> bool:
    """El desplazamiento start->end corta el SEGMENTO dibujado (no la recta
    infinita que lo contiene). Antes bastaba con cambiar de lado de la
    recta: alguien caminando al costado de una puerta, lejos de la linea,
    contaba como entrada."""
    (x1, y1), (x2, y2) = line
    dx, dy = x2 - x1, y2 - y1
    mx, my = end[0] - start[0], end[1] - start[1]
    denominator = dx * my - dy * mx
    if denominator == 0:
        return False
    # Parametro sobre la linea dibujada del punto donde la corta la
    # trayectoria (0 = un extremo, 1 = el otro).
    t = ((start[0] - x1) * my - (start[1] - y1) * mx) / denominator
    return -SEGMENT_END_TOLERANCE <= t <= 1 + SEGMENT_END_TOLERANCE


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
        self._line = ((float(self._x1), float(self._y1)), (float(self._x2), float(self._y2)))
        self._count_in = 0
        self._count_out = 0
        # Ultimo punto de cada track del lado estable de la linea: el
        # desplazamiento desde ahi es el que se cruza contra el segmento.
        self._anchors: dict[int, Point] = {}

    def _passes_filters(self, det: Detection, frame_area: int) -> bool:
        w, h = det.bbox[2], det.bbox[3]
        if not passes_box_shape_filter(w, h):
            return False
        if not passes_min_area_filter(w, h, frame_area, self._min_area_percent):
            return False
        # "Filtro mejorado": a las personas se les exige ademas una
        # proporcion de cuerpo plausible (la misma que Conteo de Personas);
        # el filtro generico es deliberadamente laxo porque cubre autos.
        return not (
            self._enhanced_filter
            and det.label == "person"
            and not PERSON_ASPECT_RATIO_RANGE[0] <= w / h <= PERSON_ASPECT_RATIO_RANGE[1]
        )

    def _stable_side(self, point: Point, bbox: tuple[int, int, int, int]) -> int:
        """Lado de la linea, o 0 si el punto esta dentro de la banda muerta.

        La banda (una fraccion del alto del objeto) es la histeresis del
        cruce: sin ella, alguien parado SOBRE la linea hace oscilar el
        centroide un par de pixeles por el jitter de la caja y cuenta
        entrada/salida/entrada... en loop."""
        distance = _signed_distance(point, self._line)
        margin = max(MIN_DEAD_BAND_PX, bbox[3] * DEAD_BAND_HEIGHT_RATIO)
        if abs(distance) <= margin:
            return 0
        return 1 if distance > 0 else -1

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        frame_area = frame.shape[0] * frame.shape[1]
        raw_detections = [
            det
            for det in self._detector.detect(frame, self._classes, self._confidence_threshold)
            if self._passes_filters(det, frame_area)
        ]

        # Dos cajas casi superpuestas sobre el MISMO objeto no deben crear
        # dos tracks (contaria un solo cruce dos veces).
        detections = deduplicate_by_iou(raw_detections)
        self._tracker.update(detections, timestamp)

        live_ids = set(self._tracker.tracks)
        for stale_id in set(self._anchors) - live_ids:
            del self._anchors[stale_id]

        last_crossing = None
        crossings: list[Detection] = []
        for track in self._tracker.confirmed_tracks():
            new_side = self._stable_side(track.centroid, track.bbox)
            if new_side == 0:
                continue
            previous_anchor = self._anchors.get(track.track_id)
            if (
                track.side is not None
                and track.side != new_side
                and previous_anchor is not None
                and _crosses_segment(previous_anchor, track.centroid, self._line)
            ):
                if track.side > 0:
                    self._count_in += 1
                    last_crossing = self.label_in if self._direction_enabled else "Cruce"
                else:
                    self._count_out += 1
                    last_crossing = self.label_out if self._direction_enabled else "Cruce"
                crossings.append(
                    Detection(label=track.label, confidence=track.confidence, bbox=track.bbox)
                )
            track.side = new_side
            self._anchors[track.track_id] = track.centroid

        metrics = {
            "count_in": self._count_in,
            "count_out": self._count_out,
            "total": self._count_in + self._count_out,
        }
        if not self._direction_enabled:
            metrics["direction_enabled"] = False
        if self._smart_mark_enabled and last_crossing:
            metrics["last_crossing"] = last_crossing
        return AnalysisResult(
            detections=tuple(detections), metrics=metrics, triggers=tuple(crossings)
        )

    def close(self) -> None:
        """Suelta la sesion ONNX compartida. Sin esto el contrato de
        Analyzer.close() era un hook vacio y el modelo quedaba vivo hasta
        que lo juntara el GC -- con los destructores nativos corriendo
        recien al cierre del interprete, que es el escenario que
        core/analytics/base.py señala como riesgo de crash en headless."""
        self._detector.close()
