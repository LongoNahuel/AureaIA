"""Deteccion facial (sin reconocimiento) via la Tasks API de MediaPipe.

Nota: los wheels de mediapipe para Windows no incluyen la API legacy
`mediapipe.solutions` (fue removida por completo, incluso en 0.10.x) --
solo viene la Tasks API nueva, que necesita un modelo .tflite descargado
aparte. Se cachea en `data/models/` la primera vez que se usa.

Se usa el modelo "full_range" en vez de "short_range": este ultimo esta
pensado para camara selfie (caras grandes y cerca, tipo videollamada);
"full_range" cubre caras chicas y lejanas tambien, que es el caso real de
una camara de seguridad mirando una escena completa.

Estabilidad / falsos disparos: cada deteccion cruda pasa primero por un
punto de validacion geometrico (orden vertical ojos-nariz-boca, ver
`_passes_geometry_filter`) que descarta detecciones con puntos de
referencia degenerados -- tipico de una textura o patron que dispara el
modelo por casualidad, no una cara real. Las que pasan se acumulan en un
`CentroidTracker` con histeresis (igual que Conteo de Personas / Cruce de
Linea): una cara nueva no se reporta como deteccion "real" hasta
sostenerse un par de cuadros seguidos, lo que filtra el ruido de un solo
frame sin agregar un modelo nuevo."""

from __future__ import annotations

import cv2
import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer, crop_to_roi
from aurea_vms.core.analytics.model_assets import ensure_model
from aurea_vms.core.analytics.tracker import CentroidTracker
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
        confirmation_frames: int = 2,
        track_max_age_s: float = 0.6,
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
        self._tracker = CentroidTracker(
            max_age_s=track_max_age_s, min_hits=max(1, confirmation_frames)
        )

    def close(self) -> None:
        self._detector.close()

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        crop, offset_x, offset_y = crop_to_roi(frame, self._roi)
        crop_h, crop_w = crop.shape[:2]
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._detector.detect(mp_image)

        raw_detections: list[Detection] = []
        for face in result.detections:
            keypoints = face.keypoints or []
            if not self._passes_geometry_filter(keypoints):
                continue
            if not self._passes_pupillary_filter(keypoints, crop_w, crop_h):
                continue
            if self._filter_by_angle and not self._passes_angle_filter(keypoints):
                continue

            box = face.bounding_box
            confidence = face.categories[0].score if face.categories else 0.0
            pixel_keypoints = None
            if keypoints:
                pixel_keypoints = tuple(
                    (kp.x * crop_w + offset_x, kp.y * crop_h + offset_y) for kp in keypoints
                )
            raw_detections.append(
                Detection(
                    label="cara",
                    confidence=float(confidence),
                    bbox=(box.origin_x + offset_x, box.origin_y + offset_y, box.width, box.height),
                    keypoints=pixel_keypoints,
                )
            )

        self._tracker.update(raw_detections, timestamp)
        detections = [
            Detection(
                label=track.label,
                confidence=track.confidence,
                bbox=track.bbox,
                keypoints=track.keypoints,
            )
            for track in self._tracker.confirmed_tracks()
        ]

        return AnalysisResult(detections=tuple(detections), metrics={"caras": len(detections)})

    @staticmethod
    def _passes_geometry_filter(keypoints) -> bool:
        """Punto de validacion geometrica: en una cara real los ojos estan
        arriba de la nariz y la nariz arriba de la boca. Una deteccion
        espuria (textura, patron u objeto que por casualidad dispara el
        modelo) suele dar puntos de referencia desordenados -- se descarta
        aca, antes de que llegue al tracker de histeresis o a la galeria."""
        if len(keypoints) <= MOUTH_CENTER:
            return True
        eyes_y = (keypoints[RIGHT_EYE].y + keypoints[LEFT_EYE].y) / 2
        nose_y = keypoints[NOSE_TIP].y
        mouth_y = keypoints[MOUTH_CENTER].y
        return eyes_y < nose_y < mouth_y

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
