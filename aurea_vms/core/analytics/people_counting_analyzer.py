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
    ) -> None:
        self._detector = YoloxDetector()
        self._confidence_threshold = confidence_threshold
        self._roi = roi
        self._min_area_percent = max(0.0, min_area_percent)
        self._tracker = CentroidTracker(
            max_age_s=track_max_age_s, min_hits=max(1, confirmation_frames)
        )

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        crop, offset_x, offset_y = crop_to_roi(frame, self._roi)
        crop_area = crop.shape[0] * crop.shape[1]

        raw_detections: list[Detection] = []
        for det in self._detector.detect(crop, ["person"], self._confidence_threshold):
            x, y, w, h = det.bbox
            if not _passes_person_shape_filter(w, h):
                continue
            if not passes_min_area_filter(w, h, crop_area, self._min_area_percent):
                continue
            raw_detections.append(
                Detection(
                    label=det.label,
                    confidence=det.confidence,
                    bbox=(x + offset_x, y + offset_y, w, h),
                )
            )

        self._tracker.update(deduplicate_by_iou(raw_detections), timestamp)
        confirmed = [
            Detection(label=track.label, confidence=track.confidence, bbox=track.bbox)
            for track in self._tracker.confirmed_tracks()
        ]

        return AnalysisResult(detections=tuple(confirmed), metrics={"occupancy": len(confirmed)})
