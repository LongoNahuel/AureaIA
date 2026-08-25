"""Deteccion facial (sin reconocimiento) via la Tasks API de MediaPipe.

Nota: los wheels de mediapipe para Windows no incluyen la API legacy
`mediapipe.solutions` (fue removida por completo, incluso en 0.10.x) --
solo viene la Tasks API nueva, que necesita un modelo .tflite descargado
aparte. Se cachea en `data/models/` la primera vez que se usa.

Se usa el modelo "full_range" en vez de "short_range": este ultimo esta
pensado para camara selfie (caras grandes y cerca, tipo videollamada);
"full_range" cubre caras chicas y lejanas tambien, que es el caso real de
una camara de seguridad mirando una escena completa."""

from __future__ import annotations

import cv2
import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer, crop_to_roi
from aurea_vms.core.analytics.model_assets import ensure_model
from aurea_vms.core.events import Detection

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_full_range/float16/latest/blaze_face_full_range.tflite"
)
MODEL_FILENAME = "blaze_face_full_range.tflite"

# Orden fijo de los 6 keypoints que devuelve BlazeFace short-range.
RIGHT_EYE, LEFT_EYE = 0, 1
NOSE_TIP = 2
MOUTH_CENTER = 3
RIGHT_EAR, LEFT_EAR = 4, 5

ANGLE_SYMMETRY_MIN = 0.45  # por debajo de esto se descarta como muy de perfil


def _ensure_model() -> str:
    return ensure_model(MODEL_FILENAME, MODEL_URL)


class FaceDetectionAnalyzer(Analyzer):
    name = "face_detection"

    def __init__(
        self,
        confidence_threshold: float = 0.5,
        roi: tuple[int, int, int, int] | None = None,
        min_pupillary_distance_px: int = 40,
        filter_by_angle: bool = False,
    ) -> None:
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions

        self._mp = mp
        options = vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=_ensure_model()),
            min_detection_confidence=confidence_threshold,
        )
        self._detector = vision.FaceDetector.create_from_options(options)
        self._roi = roi
        self._min_pupillary_distance_px = max(0, min_pupillary_distance_px)
        self._filter_by_angle = filter_by_angle

    def close(self) -> None:
        self._detector.close()

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        crop, offset_x, offset_y = crop_to_roi(frame, self._roi)
        crop_h, crop_w = crop.shape[:2]
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._detector.detect(mp_image)

        detections: list[Detection] = []
        for face in result.detections:
            keypoints = face.keypoints or []
            if not self._passes_pupillary_filter(keypoints, crop_w, crop_h):
                continue
            if self._filter_by_angle and not self._passes_angle_filter(keypoints):
                continue

            box = face.bounding_box
            confidence = face.categories[0].score if face.categories else 0.0
            detections.append(
                Detection(
                    label="cara",
                    confidence=float(confidence),
                    bbox=(box.origin_x + offset_x, box.origin_y + offset_y, box.width, box.height),
                )
            )

        return AnalysisResult(detections=tuple(detections), metrics={"caras": len(detections)})

    def _passes_pupillary_filter(self, keypoints, crop_w: int, crop_h: int) -> bool:
        """Descarta caras demasiado chicas/lejanas: la distancia entre ojos
        (en pixeles del cuadro) tiene que superar el minimo configurado."""
        if self._min_pupillary_distance_px <= 0 or len(keypoints) <= LEFT_EYE:
            return True
        right_eye, left_eye = keypoints[RIGHT_EYE], keypoints[LEFT_EYE]
        dx = (right_eye.x - left_eye.x) * crop_w
        dy = (right_eye.y - left_eye.y) * crop_h
        distance = (dx * dx + dy * dy) ** 0.5
        return distance >= self._min_pupillary_distance_px

    @staticmethod
    def _passes_angle_filter(keypoints) -> bool:
        """Heuristica de frontalidad (no es una pose 3D real): compara la
        distancia horizontal nariz-oreja de cada lado. De frente son
        parecidas; girando la cabeza, un lado se achica mucho respecto al
        otro -- eso se usa para descartar perfiles marcados."""
        if len(keypoints) <= max(RIGHT_EAR, LEFT_EAR):
            return True
        nose = keypoints[NOSE_TIP]
        right_ear, left_ear = keypoints[RIGHT_EAR], keypoints[LEFT_EAR]
        d_right = abs(nose.x - right_ear.x)
        d_left = abs(nose.x - left_ear.x)
        if d_right + d_left == 0:
            return True
        symmetry = min(d_right, d_left) / max(d_right, d_left)
        return symmetry >= ANGLE_SYMMETRY_MIN
