"""Calidad de una toma de rostro y puerta de "rostro visible".

Cada toma recibe un puntaje 0-1 que combina lo que hace util a una captura
para un operador de seguridad:

- frontalidad: la nariz centrada entre los ojos (de perfil no se reconoce);
- nitidez: varianza del laplaciano sobre la cara (movido = bajo);
- tamaño: distancia entre ojos en pixeles (una cara de 10 px no sirve);
- confianza del detector.

El puntaje solo ELIGE la mejor toma entre caras reales: no dice si algo es
una cara. Medido sobre los clips de casino de la demo (2026-09-23), las
tomas con mejor puntaje eran falsos positivos: el centro de una ruleta y las
pantallas de las tragamonedas, nitidos y simetricos (puntaje 0,92) pero con
confianza del detector de 0,52-0,64; las caras reales daban 0,88-0,95. Por
eso antes de competir por "mejor toma" una cara tiene que pasar
`is_face_visible`, con umbrales calibrados sobre esos mismos clips.
"""

from __future__ import annotations

import cv2
import numpy as np

# Pesos del puntaje (suman 1).
WEIGHT_FRONTAL = 0.35
WEIGHT_SHARPNESS = 0.30
WEIGHT_SIZE = 0.20
WEIGHT_CONFIDENCE = 0.15
# Distancia entre ojos (px) desde la que el tamaño deja de sumar / suma todo.
EYE_DISTANCE_RANGE = (18.0, 60.0)
# Desvio de la nariz respecto del centro de los ojos (en unidades de
# distancia entre ojos) a partir del cual la cara se considera de perfil.
MAX_YAW_OFFSET = 0.35
# Lado mayor del cuadro de contexto que se guarda con cada toma.
CONTEXT_MAX_SIDE = 1280
# Varianza del laplaciano (cara reescalada a 112x112) que da nitidez 0.5.
SHARPNESS_HALF = 120.0

# Puerta de "rostro visible" (ver docstring del modulo). Sobre los 4 clips
# de la demo deja: 0 tomas en los dos que no muestran caras (nuca, pantallas,
# ruleta) y 19 y 43 caras reales en los otros dos. Es independiente de la
# sensibilidad configurada en el analizador: esa decide que se DETECTA y se
# dibuja; esta, que se GUARDA como captura.
VISIBLE_MIN_CONFIDENCE = 0.80
VISIBLE_MIN_FRONTAL = 0.60
VISIBLE_MIN_EYE_PX = 18.0
VISIBLE_MIN_SHARPNESS = 0.30


def _eye_distance(face: np.ndarray) -> float:
    return float(np.hypot(face[6] - face[4], face[7] - face[5]))


def frontal_score(face: np.ndarray) -> float:
    eye_distance = _eye_distance(face)
    if eye_distance < 1e-6:
        return 0.0
    eyes_mid_x = (face[4] + face[6]) / 2
    offset = abs(face[8] - eyes_mid_x) / eye_distance
    return float(np.clip(1.0 - offset / MAX_YAW_OFFSET, 0.0, 1.0))


def sharpness_score(gray: np.ndarray) -> float:
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return variance / (variance + SHARPNESS_HALF)


def size_score(face: np.ndarray) -> float:
    low, high = EYE_DISTANCE_RANGE
    return float(np.clip((_eye_distance(face) - low) / (high - low), 0.0, 1.0))


def quality_breakdown(face: np.ndarray, face_bgr: np.ndarray) -> dict:
    """Cada componente del puntaje (0-1) mas la distancia entre ojos en px:
    el visor forense muestra por que una toma vale lo que vale."""
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    parts = {
        "frontal": frontal_score(face),
        "nitidez": sharpness_score(gray),
        "tamaño": size_score(face),
        "confianza": float(np.clip(face[14], 0.0, 1.0)),
    }
    parts["total"] = float(
        WEIGHT_FRONTAL * parts["frontal"]
        + WEIGHT_SHARPNESS * parts["nitidez"]
        + WEIGHT_SIZE * parts["tamaño"]
        + WEIGHT_CONFIDENCE * parts["confianza"]
    )
    parts["distancia_ojos_px"] = _eye_distance(face)
    return parts


def face_quality(face: np.ndarray, face_bgr: np.ndarray) -> float:
    """Puntaje 0-1 de una toma. `face` es la fila de 15 valores de YuNet."""
    return quality_breakdown(face, face_bgr)["total"]


def is_face_visible(details: dict) -> bool:
    """La toma muestra un rostro que un operador puede ver bien: detector
    seguro de que es una cara, de frente, con tamaño y nitidez suficientes."""
    return (
        details.get("confianza", 0.0) >= VISIBLE_MIN_CONFIDENCE
        and details.get("frontal", 0.0) >= VISIBLE_MIN_FRONTAL
        and details.get("distancia_ojos_px", 0.0) >= VISIBLE_MIN_EYE_PX
        and details.get("nitidez", 0.0) >= VISIBLE_MIN_SHARPNESS
    )


def context_jpeg(frame: np.ndarray) -> tuple[bytes | None, float]:
    """Cuadro completo reducido y comprimido para el visor forense."""
    height, width = frame.shape[:2]
    scale = min(1.0, CONTEXT_MAX_SIDE / max(height, width))
    small = (
        cv2.resize(
            frame, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
        )
        if scale < 1.0
        else frame
    )
    ok, buffer = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return (buffer.tobytes() if ok else None), scale


def display_crop(
    image: np.ndarray, bbox: tuple[float, float, float, float], margin: float = 0.45
) -> np.ndarray | None:
    """Recorte CUADRADO centrado en la cara, con margen (frente, mentón y
    algo de pelo): una miniatura cortada al ras de la caja de YuNet se ve
    como una mascara, no como una persona."""
    x, y, w, h = bbox
    side = max(w, h) * (1 + margin)
    cx, cy = x + w / 2, y + h / 2
    height, width = image.shape[:2]
    x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    x1, y1 = int(min(width, cx + side / 2)), int(min(height, cy + side / 2))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return image[y0:y1, x0:x1].copy()


def face_patch(image: np.ndarray, face: np.ndarray) -> np.ndarray | None:
    """La caja de la cara reescalada a 112x112: tamaño fijo para que la
    nitidez sea comparable entre caras grandes y chicas."""
    x, y, w, h = (int(round(v)) for v in face[:4])
    height, width = image.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(width, x + w), min(height, y + h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return cv2.resize(image[y0:y1, x0:x1], (112, 112))
