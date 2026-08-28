"""Conteo de personas: ocupacion actual dentro de una zona (ROI).

Usa el detector de objetos liviano de MediaPipe (EfficientDet-Lite2,
filtrado a la clase "person"). Cada deteccion cruda pasa por una cadena
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

import cv2
import numpy as np

from aurea_vms.core.analytics.base import (
    AnalysisResult,
    Analyzer,
    crop_to_roi,
    rescale_bbox,
    resize_for_inference,
)
from aurea_vms.core.analytics.object_detector_backend import (
    create_object_detector,
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
        self._mp, self._detector = create_object_detector(["person"], confidence_threshold)
        self._roi = roi
        self._min_area_percent = max(0.0, min_area_percent)
        self._tracker = CentroidTracker(
            max_age_s=track_max_age_s, min_hits=max(1, confirmation_frames)
        )

    def close(self) -> None:
        self._detector.close()

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        crop, offset_x, offset_y = crop_to_roi(frame, self._roi)
        crop_area = crop.shape[0] * crop.shape[1]
        small, scale = resize_for_inference(crop)
        inv_scale = 1.0 / scale
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._detector.detect(mp_image)

        raw_detections: list[Detection] = []
        for det in result.detections:
            box = det.bounding_box
            if not _passes_person_shape_filter(box.width, box.height):
                continue
            category = det.categories[0]
            raw_bbox = (box.origin_x, box.origin_y, box.width, box.height)
            bbox = rescale_bbox(raw_bbox, inv_scale, offset_x, offset_y)
            if not passes_min_area_filter(bbox[2], bbox[3], crop_area, self._min_area_percent):
                continue
            raw_detections.append(
                Detection(
                    label=category.category_name,
                    confidence=float(category.score),
                    bbox=bbox,
                )
            )

        self._tracker.update(deduplicate_by_iou(raw_detections), timestamp)
        confirmed = [
            Detection(label=track.label, confidence=track.confidence, bbox=track.bbox)
            for track in self._tracker.confirmed_tracks()
        ]

        return AnalysisResult(detections=tuple(confirmed), metrics={"occupancy": len(confirmed)})
