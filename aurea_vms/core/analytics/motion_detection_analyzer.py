"""Deteccion de movimiento por sustraccion de fondo (MOG2). No usa IA/YOLO:
es liviano y corre bien en CPU incluso con varias camaras.

Falsos positivos / eficiencia:

- El fondo se calcula sobre una copia reescalada (ver `resize_for_inference`
  en `base.py`, mismo limite de 640px que usan los demas analizadores):
  MOG2 + morfologia son mas baratos cuanto mas chico el cuadro, y el
  resultado (bbox/silueta) se reescala de vuelta al frame nativo.
- `detectShadows=True`: MOG2 marca las sombras con un gris distinto (127)
  en vez de blanco (255) -- se descartan esos pixeles antes de buscar
  contornos, asi la sombra de una persona/auto en movimiento no infla ni
  duplica la region detectada.
- Las regiones pasan por un `CentroidTracker` con histeresis (igual que
  Conteo de Personas / Cruce de Linea): una region nueva no se reporta
  hasta sostenerse un par de cuadros seguidos, lo que filtra el ruido de
  un solo frame (parpadeo de IR, compresion) sin agregar latencia
  perceptible."""

from __future__ import annotations

import cv2
import numpy as np

from aurea_vms.core.analytics.base import (
    AnalysisResult,
    Analyzer,
    crop_to_roi,
    resize_for_inference,
)
from aurea_vms.core.analytics.tracker import CentroidTracker
from aurea_vms.core.events import Detection

SHADOW_VALUE = 127  # valor que MOG2 le asigna a un pixel de sombra detectada
FOREGROUND_THRESHOLD = 200  # umbral para quedarse solo con primer plano solido


class MotionDetectionAnalyzer(Analyzer):
    name = "motion_detection"

    def __init__(
        self,
        sensitivity: int = 50,
        min_area_percent: float = 0.5,
        roi: tuple[int, int, int, int] | None = None,
        confirmation_frames: int = 2,
        track_max_age_s: float = 0.6,
    ) -> None:
        # sensitivity 1-100 (mayor = detecta cambios mas sutiles) se mapea
        # inversamente al varThreshold de MOG2 (menor = mas sensible).
        sensitivity = max(1, min(100, sensitivity))
        var_threshold = max(4, 120 - sensitivity)
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=200, varThreshold=var_threshold, detectShadows=True
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
        self._tracker = CentroidTracker(
            max_age_s=track_max_age_s, min_hits=max(1, confirmation_frames)
        )

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        crop, offset_x, offset_y = crop_to_roi(frame, self._roi)
        small, scale = resize_for_inference(crop)
        inv_scale = 1.0 / scale
        small_area = small.shape[0] * small.shape[1]
        min_area_px = max(9.0, small_area * self._min_area_percent / 100.0)

        mask = self._bg_subtractor.apply(small)
        # Sombras (gris 127) fuera: solo primer plano solido (blanco 255).
        _, mask = cv2.threshold(mask, FOREGROUND_THRESHOLD, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._open_kernel)
        mask = cv2.dilate(mask, self._merge_kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._merge_kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        raw_detections: list[Detection] = []
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
            polygon = tuple(
                (
                    round(pt[0][0] * inv_scale) + offset_x,
                    round(pt[0][1] * inv_scale) + offset_y,
                )
                for pt in simplified
            )

            raw_detections.append(
                Detection(
                    label="movimiento",
                    confidence=1.0,
                    bbox=(
                        round(x * inv_scale) + offset_x,
                        round(y * inv_scale) + offset_y,
                        round(w * inv_scale),
                        round(h * inv_scale),
                    ),
                    polygon=polygon,
                )
            )

        self._tracker.update(raw_detections, timestamp)
        detections = [
            Detection(
                label=track.label,
                confidence=track.confidence,
                bbox=track.bbox,
                polygon=track.polygon,
            )
            for track in self._tracker.confirmed_tracks()
        ]

        return AnalysisResult(detections=tuple(detections), metrics={"regiones": len(detections)})
