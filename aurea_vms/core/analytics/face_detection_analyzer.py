"""Deteccion facial (sin reconocimiento) via la Tasks API de MediaPipe.

Nota: los wheels de mediapipe para Windows no incluyen la API legacy
`mediapipe.solutions` (fue removida por completo, incluso en 0.10.x) --
solo viene la Tasks API nueva, que necesita un modelo .tflite descargado
aparte. Se cachea en `data/models/` la primera vez que se usa.

Se usa el modelo "full_range" en vez de "short_range": este ultimo esta
pensado para camara selfie (caras grandes y cerca, tipo videollamada);
"full_range" cubre caras chicas y lejanas tambien, que es el caso real de
una camara de seguridad mirando una escena completa.

Estabilidad / falsos disparos: cada deteccion cruda pasa primero por dos
puntos de validacion, antes de llegar al tracker de histeresis o a la
galeria:

1. `_passes_box_shape_filter`: la caja tiene que tener una proporcion
   ancho/alto plausible para una cara.
2. `_passes_geometry_filter`: los 4 puntos de referencia centrales tienen
   que guardar la disposicion de una cara real (orden vertical
   ojos-nariz-boca, linea entre ojos mas horizontal que vertical, nariz
   centrada entre los ojos en X, boca a distancia comparable de cada
   ojo) -- no solo existir.

Ambos descartan detecciones con puntos/caja degenerados, tipico de una
textura o patron que dispara el modelo por casualidad, no una cara real.
Las que pasan se acumulan en un `CentroidTracker` con histeresis (igual
que Conteo de Personas / Cruce de Linea): una cara nueva no se reporta
como deteccion "real" hasta sostenerse un par de cuadros seguidos, lo que
filtra el ruido de un solo frame sin agregar un modelo nuevo."""

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
EYE_MOUTH_SYMMETRY_MIN = 0.25  # por debajo, los puntos no guardan forma de cara
BOX_ASPECT_RATIO_RANGE = (0.35, 2.5)  # ancho/alto plausible para una cara real


def _distance(a, b) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


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
            box = face.bounding_box
            if not self._passes_box_shape_filter(box):
                continue
            if not self._passes_geometry_filter(keypoints):
                continue
            if not self._passes_pupillary_filter(keypoints, crop_w, crop_h):
                continue
            if self._filter_by_angle and not self._passes_angle_filter(keypoints):
                continue

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
        """Punto de validacion geometrica: descarta detecciones espurias
        (textura, patron u objeto que por casualidad dispara el modelo,
        no una cara real) chequeando que los 4 puntos de referencia
        centrales guarden la disposicion de una cara real, no solo que
        existan:

        1. Orden vertical ojos-nariz-boca (de arriba a abajo).
        2. La linea entre los dos ojos es mas horizontal que vertical --
           tolera cabeza inclinada hasta 45 grados; un par de "ojos" casi
           en vertical es geometricamente imposible en una cara.
        3. La nariz cae entre los dos ojos en X (con margen) -- una
           deteccion espuria tipicamente tiene la nariz descentrada.
        4. La boca queda a distancia comparable de cada ojo -- un
           triangulo ojo-ojo-boca muy asimetrico no es una cara (ver
           EYE_MOUTH_SYMMETRY_MIN; el umbral es laxo a proposito para no
           rechazar caras de perfil, que ya tienen su propio filtro
           opcional en `_passes_angle_filter`).

        Cualquiera de las cuatro que falle es una fuerte señal de que no
        es una cara real. Se descarta aca, antes de que llegue al tracker
        de histeresis o a la galeria."""
        if len(keypoints) <= MOUTH_CENTER:
            return True
        right_eye, left_eye = keypoints[RIGHT_EYE], keypoints[LEFT_EYE]
        nose, mouth = keypoints[NOSE_TIP], keypoints[MOUTH_CENTER]

        eye_dx = abs(left_eye.x - right_eye.x)
        eye_dy = abs(left_eye.y - right_eye.y)
        if eye_dx < 1e-6 or eye_dy > eye_dx:
            return False

        eyes_y = (right_eye.y + left_eye.y) / 2
        if not (eyes_y < nose.y < mouth.y):
            return False

        eyes_x_min, eyes_x_max = sorted((right_eye.x, left_eye.x))
        slack = eye_dx * 0.5
        if not (eyes_x_min - slack) <= nose.x <= (eyes_x_max + slack):
            return False

        d_right_mouth = _distance(right_eye, mouth)
        d_left_mouth = _distance(left_eye, mouth)
        if d_right_mouth + d_left_mouth == 0:
            return False
        symmetry = min(d_right_mouth, d_left_mouth) / max(d_right_mouth, d_left_mouth)
        return symmetry >= EYE_MOUTH_SYMMETRY_MIN

    @staticmethod
    def _passes_box_shape_filter(box) -> bool:
        """Ultima red de seguridad, independiente de los puntos de
        referencia: descarta cajas con una proporcion ancho/alto
        imposible para una cara real (una tira angosta o un rectangulo
        muy chato), tipico de una deteccion espuria sobre un borde o
        patron repetitivo de la escena."""
        if box.height <= 0:
            return False
        ratio = box.width / box.height
        return BOX_ASPECT_RATIO_RANGE[0] <= ratio <= BOX_ASPECT_RATIO_RANGE[1]

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
