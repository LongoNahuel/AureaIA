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
        zones: list[tuple[int, int, int, int]] | None = None,
        opening_percent: float = 10.0,
        threshold_seconds: float | None = None,
    ) -> None:
        # El umbral representa la fraccion minima del ROI ocupada por el
        # cambio morfologico. No depende de la resolucion de la camara.
        self._threshold = max(0.01, min(0.8, float(change_threshold)))
        self._opening_percent = max(1.0, min(100.0, float(opening_percent))) / 100.0
        self._confirmation_frames = max(1, int(confirmation_frames))
        self._threshold_seconds = max(0.0, float(threshold_seconds or 0.0))
        self._rois = zones or ([roi] if roi is not None else [None])
        self._baselines: list[np.ndarray | None] = [None] * len(self._rois)
        self._morph_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._candidate = "cerrada"
        self._candidate_hits = 0
        self._candidate_since = 0.0
        self._state = "cerrada"

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        scores: list[tuple[float, int, int, float, tuple[int, ...]]] = []
        for index, roi in enumerate(self._rois):
            crop, offset_x, offset_y = crop_to_roi(frame, roi)
            small, scale = resize_for_inference(crop)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            if self._baselines[index] is None:
                self._baselines[index] = blurred.copy()
                continue

            difference = cv2.absdiff(blurred, self._baselines[index])
            difference_u8 = np.clip(difference * 255.0, 0, 255).astype(np.uint8)
            _, mask = cv2.threshold(difference_u8, 24, 255, cv2.THRESH_BINARY)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._morph_kernel, iterations=1)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._morph_kernel, iterations=2)
            changed_area = float(cv2.countNonZero(mask) / mask.size)
            scores.append((changed_area, offset_x, offset_y, scale, small.shape))

        score, offset_x, offset_y, scale, shape = max(scores, default=(0.0, 0, 0, 1.0, frame.shape))
        candidate = "abierta" if score >= max(self._threshold, self._opening_percent) else "cerrada"
        if candidate == self._candidate:
            self._candidate_hits += 1
        else:
            self._candidate = candidate
            self._candidate_hits = 1
            self._candidate_since = timestamp

        transition: str | None = None
        confirmed = self._candidate_hits >= self._confirmation_frames
        if self._threshold_seconds:
            confirmed = confirmed and timestamp - self._candidate_since >= self._threshold_seconds
        if confirmed and candidate != self._state:
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
        roi = self._rois[0] if self._rois and self._rois[0] is not None else None
        if roi is not None:
            _, _, width, height = roi
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
