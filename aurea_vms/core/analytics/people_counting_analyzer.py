"""Conteo de personas: ocupacion actual dentro de una zona (ROI).

Usa el detector de objetos liviano de MediaPipe (EfficientDet-Lite2,
filtrado a la clase "person"), pasado por un CentroidTracker con
histeresis: una persona nueva no se suma a la ocupacion hasta que la
deteccion se sostiene un par de muestras seguidas (evita que un falso
positivo de 1 frame infle el conteo), y una persona detectada de forma
intermitente (oclusion momentanea) no desaparece del conteo hasta
perderse varios frames seguidos (evita el parpadeo del numero)."""

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
from aurea_vms.core.analytics.object_detector_backend import create_object_detector
from aurea_vms.core.analytics.tracker import CentroidTracker
from aurea_vms.core.events import Detection


class PeopleCountingAnalyzer(Analyzer):
    name = "people_counting"

    def __init__(
        self,
        confidence_threshold: float = 0.5,
        roi: tuple[int, int, int, int] | None = None,
        confirmation_frames: int = 2,
        track_max_age_s: float = 1.5,
    ) -> None:
        self._mp, self._detector = create_object_detector(["person"], confidence_threshold)
        self._roi = roi
        self._tracker = CentroidTracker(
            max_age_s=track_max_age_s, min_hits=max(1, confirmation_frames)
        )

    def close(self) -> None:
        self._detector.close()

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        crop, offset_x, offset_y = crop_to_roi(frame, self._roi)
        small, scale = resize_for_inference(crop)
        inv_scale = 1.0 / scale
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._detector.detect(mp_image)

        raw_detections: list[Detection] = []
        for det in result.detections:
            box = det.bounding_box
            category = det.categories[0]
            raw_bbox = (box.origin_x, box.origin_y, box.width, box.height)
            raw_detections.append(
                Detection(
                    label=category.category_name,
                    confidence=float(category.score),
                    bbox=rescale_bbox(raw_bbox, inv_scale, offset_x, offset_y),
                )
            )

        self._tracker.update(raw_detections, timestamp)
        confirmed = [
            Detection(label=track.label, confidence=track.confidence, bbox=track.bbox)
            for track in self._tracker.confirmed_tracks()
        ]

        return AnalysisResult(detections=tuple(confirmed), metrics={"occupancy": len(confirmed)})
