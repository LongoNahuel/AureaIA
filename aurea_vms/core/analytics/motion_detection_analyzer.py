"""Deteccion de movimiento por sustraccion de fondo (MOG2). No usa IA/YOLO:
es liviano y corre bien en CPU incluso con varias camaras."""

from __future__ import annotations

import cv2
import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer, crop_to_roi
from aurea_vms.core.events import Detection


class MotionDetectionAnalyzer(Analyzer):
    name = "motion_detection"

    def __init__(
        self,
        sensitivity: int = 50,
        min_area_percent: float = 0.5,
        roi: tuple[int, int, int, int] | None = None,
    ) -> None:
        # sensitivity 1-100 (mayor = detecta cambios mas sutiles) se mapea
        # inversamente al varThreshold de MOG2 (menor = mas sensible).
        sensitivity = max(1, min(100, sensitivity))
        var_threshold = max(4, 120 - sensitivity)
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=200, varThreshold=var_threshold, detectShadows=False
        )
        # % del area del cuadro (o del ROI) en vez de px^2 crudos: el mismo
        # numero tiene sentido sin importar la resolucion de la camara.
        self._min_area_percent = max(0.05, min(50.0, min_area_percent))
        self._roi = roi
        self._open_kernel = np.ones((3, 3), np.uint8)
        # Kernel grande para MORPH_CLOSE + dilate: junta fragmentos cercanos
        # (brazos, piernas, sombras partidas) de la MISMA persona/objeto en
        # una sola region, en vez de varios rectangulos sueltos y ruidosos.
        self._merge_kernel = np.ones((15, 15), np.uint8)

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        crop, offset_x, offset_y = crop_to_roi(frame, self._roi)
        crop_area = crop.shape[0] * crop.shape[1]
        min_area_px = max(64.0, crop_area * self._min_area_percent / 100.0)

        mask = self._bg_subtractor.apply(crop)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._open_kernel)
        mask = cv2.dilate(mask, self._merge_kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._merge_kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections: list[Detection] = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area_px:
                continue
            x, y, w, h = cv2.boundingRect(contour)

            # Contorno simplificado (menos vertices, sin perder la forma
            # real) para dibujar la silueta en vez de un rectangulo --
            # mucho mas preciso para marcar donde esta el movimiento real.
            perimeter = cv2.arcLength(contour, True)
            simplified = cv2.approxPolyDP(contour, 0.01 * perimeter, True)
            polygon = tuple((int(pt[0][0] + offset_x), int(pt[0][1] + offset_y)) for pt in simplified)

            detections.append(
                Detection(
                    label="movimiento",
                    confidence=1.0,
                    bbox=(x + offset_x, y + offset_y, w, h),
                    polygon=polygon,
                )
            )

        return AnalysisResult(detections=tuple(detections), metrics={"regiones": len(detections)})
