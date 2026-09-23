"""Deteccion facial (sin reconocimiento) via YuNet (`cv2.FaceDetectorYN`,
OpenCV Zoo, modelo 2023mar).

Reemplaza a BlazeFace (MediaPipe Tasks, usado hasta esta version): pese a
varias rondas de filtros geometricos post-hoc, BlazeFace seguia
disparando sobre objetos planos (cajas, carteles) -- es una red muy
liviana, pensada para velocidad antes que precision, con solo 6
keypoints crudos y sin un score de confianza demasiado discriminante.

YuNet es una red mas moderna (anchor-free, entrenada sobre WIDER FACE con
mineria de negativos dificiles) que ya viene con OpenCV -- no suma
dependencias nuevas al proyecto, que ya usa cv2 en todos los
analizadores. Da 5 puntos de referencia (ojo derecho, ojo izquierdo,
nariz, comisura de boca derecha, comisura de boca izquierda -- sin
puntos de oreja, a diferencia de BlazeFace) mas un score de confianza
propio del modelo, no una heuristica post-hoc adivinando si "parece" una
cara.

Estabilidad / falsos disparos: cada deteccion cruda pasa igual por tres
puntos de validacion baratos, mas livianos que antes porque el modelo de
por si ya filtra mucho mejor:

1. `_passes_box_shape_filter`: la caja tiene que tener una proporcion
   ancho/alto plausible para una cara.
2. `_passes_geometry_filter`: los 5 puntos guardan la disposicion de una
   cara real ENTRE SI (orden vertical ojos-nariz-boca, linea de ojos no
   demasiado vertical, nariz cerca de los ojos y de las comisuras de
   boca en X, ancho de boca proporcional a la distancia entre ojos) --
   umbrales calibrados contra deteccion real (no a ojo): una primera
   version, mas estricta, rechazaba el 84% de las caras reales de alta
   confianza en capturas guardadas del proyecto.
3. `_passes_head_alignment_filter`: cruza esos mismos puntos contra la
   CABEZA (la caja) -- ojos cerca de la mitad superior, boca cerca de la
   inferior, nariz cerca del centro horizontal.

Las que pasan se acumulan en un `CentroidTracker` con histeresis (igual
que Conteo de Personas / Cruce de Linea): una cara nueva no se reporta
como deteccion "real" hasta sostenerse un par de cuadros seguidos, lo que
filtra el ruido de un solo frame."""

from __future__ import annotations

import cv2
import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer, crop_to_roi
from aurea_vms.core.analytics.model_assets import ensure_model
from aurea_vms.core.analytics.tracker import CentroidTracker
from aurea_vms.core.events import Detection

MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
MODEL_FILENAME = "face_detection_yunet_2023mar.onnx"

# Columnas del array Nx15 que devuelve YuNet.detect(): bbox (4) + 5
# landmarks (10) + score (1), todo en pixeles de la imagen de entrada.
BOX_X, BOX_Y, BOX_W, BOX_H = 0, 1, 2, 3
RIGHT_EYE_X, RIGHT_EYE_Y = 4, 5
LEFT_EYE_X, LEFT_EYE_Y = 6, 7
NOSE_X, NOSE_Y = 8, 9
MOUTH_R_X, MOUTH_R_Y = 10, 11
MOUTH_L_X, MOUTH_L_Y = 12, 13
SCORE = 14

# Calibrados contra deteccion real de YuNet sobre capturas guardadas (no
# a ojo): los puntos de una cara chica/lejana en una camara de seguridad
# traen ruido de estimacion real, y normalizar ese ruido por eye_dx (a
# veces de pocos pixeles) amplifica cualquier margen ajustado. La primera
# calibracion (a ojo) rechazaba el 84% de las caras reales de alta
# confianza -- estos umbrales son deliberadamente laxos.
EYE_TILT_MAX_RATIO = 2.5  # eye_dy/eye_dx maximo tolerado (cabeza inclinada + angulo de camara)
NOSE_SLACK_RATIO = 3.0  # margen (x eye_dx) para nariz vs. ojos/comisuras en X
MOUTH_WIDTH_RATIO_RANGE = (0.15, 3.0)  # ancho de boca plausible, en unidades de eye_dx
BOX_ASPECT_RATIO_RANGE = (0.35, 2.5)  # ancho/alto plausible para una cara real
HEAD_EYES_Y_RANGE = (-0.10, 0.70)  # posicion esperada de los ojos dentro de la caja (ver docstring)
HEAD_MOUTH_Y_RANGE = (0.30, 1.10)
# La caja de YuNet no queda centrada en los puntos tan ajustado como se
# asumio al principio (dato real: nariz observada entre -0.05 y 1.12 del
# ancho de caja, practicamente de punta a punta); margen generoso.
HEAD_NOSE_X_RANGE = (-0.30, 1.30)

NMS_THRESHOLD = 0.3
TOP_K = 500
# Tamaño inicial dummy: se pisa con el tamaño real del crop en el primer
# process_frame (setInputSize es obligatorio antes de detect()).
_INIT_INPUT_SIZE = (320, 320)


def _ensure_model() -> str:
    return ensure_model(MODEL_FILENAME, MODEL_URL)


class FaceDetectionAnalyzer(Analyzer):
    name = "face_detection"

    def __init__(
        self,
        confidence_threshold: float = 0.5,
        roi: tuple[int, int, int, int] | None = None,
        min_pupillary_distance_px: int = 40,
        confirmation_frames: int = 2,
        track_max_age_s: float = 0.6,
        tilted_faces_filter: bool = True,
    ) -> None:
        self._detector = cv2.FaceDetectorYN.create(
            _ensure_model(),
            "",
            _INIT_INPUT_SIZE,
            score_threshold=confidence_threshold,
            nms_threshold=NMS_THRESHOLD,
            top_k=TOP_K,
        )
        self._roi = roi
        self._min_pupillary_distance_px = max(0, min_pupillary_distance_px)
        self._tilted_faces_filter = tilted_faces_filter
        self._tracker = CentroidTracker(
            max_age_s=track_max_age_s, min_hits=max(1, confirmation_frames)
        )
        self._input_size: tuple[int, int] | None = None

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        crop, offset_x, offset_y = crop_to_roi(frame, self._roi)
        crop_h, crop_w = crop.shape[:2]
        size = (crop_w, crop_h)
        if size != self._input_size:
            self._detector.setInputSize(size)
            self._input_size = size

        # YuNet espera BGR (el formato nativo de OpenCV) -- a diferencia
        # de MediaPipe, no hace falta convertir a RGB.
        _, faces = self._detector.detect(crop)

        raw_detections: list[Detection] = []
        for face in faces if faces is not None else []:
            if not self._passes_box_shape_filter(face):
                continue
            if self._tilted_faces_filter and not self._passes_geometry_filter(face):
                continue
            if not self._passes_head_alignment_filter(face):
                continue
            if not self._passes_pupillary_filter(face):
                continue

            x, y, w, h = face[BOX_X], face[BOX_Y], face[BOX_W], face[BOX_H]
            pixel_keypoints = (
                (face[RIGHT_EYE_X] + offset_x, face[RIGHT_EYE_Y] + offset_y),
                (face[LEFT_EYE_X] + offset_x, face[LEFT_EYE_Y] + offset_y),
                (face[NOSE_X] + offset_x, face[NOSE_Y] + offset_y),
                (face[MOUTH_R_X] + offset_x, face[MOUTH_R_Y] + offset_y),
                (face[MOUTH_L_X] + offset_x, face[MOUTH_L_Y] + offset_y),
            )
            raw_detections.append(
                Detection(
                    label="cara",
                    confidence=float(face[SCORE]),
                    bbox=(
                        round(x) + offset_x,
                        round(y) + offset_y,
                        round(w),
                        round(h),
                    ),
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
    def _passes_box_shape_filter(face) -> bool:
        """Descarta cajas con una proporcion ancho/alto imposible para una
        cara real (una tira angosta o un rectangulo muy chato), tipico de
        una deteccion espuria sobre un borde o patron repetitivo."""
        w, h = face[BOX_W], face[BOX_H]
        if h <= 0:
            return False
        ratio = w / h
        return BOX_ASPECT_RATIO_RANGE[0] <= ratio <= BOX_ASPECT_RATIO_RANGE[1]

    @staticmethod
    def _passes_geometry_filter(face) -> bool:
        """Los 5 puntos tienen que guardar la disposicion de una cara real
        ENTRE SI -- umbrales laxos a proposito (ver comentario de las
        constantes): esto es una red de seguridad barata contra
        detecciones degeneradas, no un segundo clasificador compitiendo
        con el score del modelo.

        1. Orden vertical ojos-nariz-boca.
        2. La linea entre los dos ojos no es mucho mas vertical que
           horizontal (tolera inclinacion real de camara + cabeza).
        3. La nariz cae cerca de los dos ojos en X, y cerca de las dos
           comisuras de boca en X (no asume cual comisura es "derecha"/
           "izquierda", solo que la nariz no queda lejos de ambas).
        4. El ancho de boca es proporcional a la distancia entre ojos --
           una boca muchisimo mas angosta/ancha que los ojos no es una
           cara."""
        right_eye = (face[RIGHT_EYE_X], face[RIGHT_EYE_Y])
        left_eye = (face[LEFT_EYE_X], face[LEFT_EYE_Y])
        nose = (face[NOSE_X], face[NOSE_Y])
        mouth_r = (face[MOUTH_R_X], face[MOUTH_R_Y])
        mouth_l = (face[MOUTH_L_X], face[MOUTH_L_Y])

        eye_dx = abs(left_eye[0] - right_eye[0])
        eye_dy = abs(left_eye[1] - right_eye[1])
        if eye_dx < 1e-6 or eye_dy > eye_dx * EYE_TILT_MAX_RATIO:
            return False

        eyes_y = (right_eye[1] + left_eye[1]) / 2
        mouth_y = (mouth_r[1] + mouth_l[1]) / 2
        if not (eyes_y < nose[1] < mouth_y):
            return False

        slack = eye_dx * NOSE_SLACK_RATIO
        eyes_x_min, eyes_x_max = sorted((right_eye[0], left_eye[0]))
        if not (eyes_x_min - slack) <= nose[0] <= (eyes_x_max + slack):
            return False

        mouth_x_min, mouth_x_max = sorted((mouth_r[0], mouth_l[0]))
        if not (mouth_x_min - slack) <= nose[0] <= (mouth_x_max + slack):
            return False

        mouth_dx = mouth_x_max - mouth_x_min
        ratio = mouth_dx / eye_dx
        return MOUTH_WIDTH_RATIO_RANGE[0] <= ratio <= MOUTH_WIDTH_RATIO_RANGE[1]

    @staticmethod
    def _passes_head_alignment_filter(face) -> bool:
        """Cruza los puntos contra la CABEZA (la caja): ojos cerca de la
        mitad superior, boca cerca de la inferior, nariz cerca del centro
        horizontal. Puntos autoconsistentes entre si (ver
        `_passes_geometry_filter`) pero amontonados en una esquina de una
        caja mucho mas grande no describen una cabeza real."""
        box_x, box_y, box_w, box_h = face[BOX_X], face[BOX_Y], face[BOX_W], face[BOX_H]
        if box_w <= 0 or box_h <= 0:
            return True

        eyes_y = (face[RIGHT_EYE_Y] + face[LEFT_EYE_Y]) / 2
        eyes_rel_y = (eyes_y - box_y) / box_h
        if not (HEAD_EYES_Y_RANGE[0] <= eyes_rel_y <= HEAD_EYES_Y_RANGE[1]):
            return False

        mouth_y = (face[MOUTH_R_Y] + face[MOUTH_L_Y]) / 2
        mouth_rel_y = (mouth_y - box_y) / box_h
        if not (HEAD_MOUTH_Y_RANGE[0] <= mouth_rel_y <= HEAD_MOUTH_Y_RANGE[1]):
            return False

        nose_rel_x = (face[NOSE_X] - box_x) / box_w
        return HEAD_NOSE_X_RANGE[0] <= nose_rel_x <= HEAD_NOSE_X_RANGE[1]

    def _passes_pupillary_filter(self, face) -> bool:
        """Descarta caras demasiado chicas/lejanas: la distancia entre
        ojos (en pixeles del cuadro, YuNet ya devuelve todo en pixeles)
        tiene que superar el minimo configurado."""
        if self._min_pupillary_distance_px <= 0:
            return True
        dx = face[RIGHT_EYE_X] - face[LEFT_EYE_X]
        dy = face[RIGHT_EYE_Y] - face[LEFT_EYE_Y]
        distance = (dx * dx + dy * dy) ** 0.5
        return distance >= self._min_pupillary_distance_px
