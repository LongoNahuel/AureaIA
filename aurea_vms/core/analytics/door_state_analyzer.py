"""Detección de transiciones de una puerta dentro de un ROI estático."""

from __future__ import annotations

import cv2
import numpy as np

from aurea_vms.core.analytics.base import (
    AnalysisResult,
    Analyzer,
    crop_to_roi,
    resize_for_inference,
)
from aurea_vms.core.events import Detection


class DoorStateAnalyzer(Analyzer):
    name = "door_state"

    def __init__(
        self,
        change_threshold: float = 0.10,
        confirmation_frames: int = 3,
        roi: tuple[int, int, int, int] | None = None,
    ) -> None:
        # El umbral representa la fraccion minima del ROI ocupada por el
        # cambio morfologico. No depende de la resolucion de la camara.
        self._threshold = max(0.01, min(0.8, float(change_threshold)))
        self._confirmation_frames = max(1, int(confirmation_frames))
        self._roi = roi
        self._baseline: np.ndarray | None = None
        self._morph_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._candidate = "cerrada"
        self._candidate_hits = 0
        self._state = "cerrada"

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        crop, offset_x, offset_y = crop_to_roi(frame, self._roi)
        small, scale = resize_for_inference(crop)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)

        if self._baseline is None:
            self._baseline = blurred.copy()
            return self._result("cerrada", 0.0, None, offset_x, offset_y, scale, small.shape)

        difference = cv2.absdiff(blurred, self._baseline)
        # La diferencia se convierte en una silueta binaria y se limpia
        # morfologicamente: OPEN quita ruido de compresion y CLOSE rellena
        # huecos producidos por barrotes, reflejos o la manija.
        difference_u8 = np.clip(difference * 255.0, 0, 255).astype(np.uint8)
        _, mask = cv2.threshold(difference_u8, 24, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._morph_kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._morph_kernel, iterations=2)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        changed_area = 0.0
        if contours:
            # Se ignoran componentes diminutos; la puerta debe formar una
            # region conectada significativa dentro del ROI.
            min_component_area = mask.size * 0.002
            changed_area = (
                sum(
                    cv2.contourArea(contour)
                    for contour in contours
                    if cv2.contourArea(contour) >= min_component_area
                )
                / mask.size
            )
        score = float(min(1.0, changed_area))
        candidate = "abierta" if score >= self._threshold else "cerrada"
        if candidate == self._candidate:
            self._candidate_hits += 1
        else:
            self._candidate = candidate
            self._candidate_hits = 1

        transition: str | None = None
        if self._candidate_hits >= self._confirmation_frames and candidate != self._state:
            previous = self._state
            self._state = candidate
            transition = f"{previous}_a_{candidate}"

        return self._result(self._state, score, transition, offset_x, offset_y, scale, small.shape)

    def _result(
        self,
        state: str,
        score: float,
        transition: str | None,
        offset_x: int,
        offset_y: int,
        scale: float,
        shape: tuple[int, ...],
    ) -> AnalysisResult:
        if self._roi is not None:
            _, _, width, height = self._roi
        else:
            height, width = shape[:2]
            if scale != 1.0:
                width = round(width / scale)
                height = round(height / scale)
        label = "puerta_abierta" if state == "abierta" else "puerta_cerrada"
        detections = (
            (
                Detection(
                    label=label,
                    confidence=min(1.0, score / max(self._threshold, 0.001)),
                    bbox=(offset_x, offset_y, width, height),
                ),
            )
            if transition is not None
            else ()
        )
        return AnalysisResult(
            detections=detections,
            metrics={
                "estado": state,
                "cambio": round(score, 4),
                "transicion": transition,
            },
        )
