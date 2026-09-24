"""Estimacion de pose 2D: RTMPose-s (OpenMMLab, Apache 2.0) via onnxruntime.

Devuelve los 17 puntos COCO de una persona (muñecas, rodillas, tobillos...)
dentro de una caja dada. Es "top-down": no detecta personas, estima la pose
de lo que haya en la caja. Para Detección de incidentes eso es justo lo que sirve: en
las camaras cenitales de casino YOLOX casi no detecta personas vistas desde
arriba (medido sobre el clip de la demo: 1 deteccion en 8 cuadros con dos
jugadores en escena), pero RTMPose sobre el recorte de cada puesto
localiza bien manos y piernas.

Se eligio RTMPose por licencia (Apache 2.0; YOLOv8-pose es AGPL, la misma
razon por la que el proyecto saco YOLOv8) y porque la variante -s corre en
~20 ms por recorte en CPU. La -t (mas chica) no detecto las patadas del
clip de prueba.

Pre/post-procesamiento del export oficial (mmdeploy, "onnx_sdk"):
- recorte afin a 192x256 conservando la proporcion de la caja;
- RGB normalizado con la media/desvio de ImageNet;
- salida SimCC: por punto, un vector para X (384) y otro para Y (512) a
  resolucion x2; la posicion es el argmax / 2 y la confianza el menor de
  los dos maximos.
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from aurea_vms.core.analytics.model_assets import ensure_model
from aurea_vms.core.analytics.object_detector_backend import adquirir_sesion, soltar_sesion

logger = logging.getLogger(__name__)

MODEL_FILENAME = "rtmpose-s_simcc-body7_256x192.onnx"
# El release oficial viene zipeado (end2end.onnx adentro); solo se baja si
# el modelo no esta en data/models ni en el bundle (ver model_assets).
MODEL_ZIP_URL = (
    "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
    "rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip"
)
INPUT_W, INPUT_H = 192, 256
SIMCC_SPLIT = 2.0
MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)  # RGB
STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)

# Indices COCO-17.
LEFT_WRIST, RIGHT_WRIST = 9, 10
LEFT_KNEE, RIGHT_KNEE = 13, 14
LEFT_ANKLE, RIGHT_ANKLE = 15, 16
WRISTS = (LEFT_WRIST, RIGHT_WRIST)
KNEES = (LEFT_KNEE, RIGHT_KNEE)
ANKLES = (LEFT_ANKLE, RIGHT_ANKLE)
LEG_JOINTS = KNEES + ANKLES


def _ensure_model() -> str:
    path = ensure_model(MODEL_FILENAME, MODEL_ZIP_URL)
    if zipfile.is_zipfile(path):
        # Ultimo recurso de model_assets: se bajo el zip del release con el
        # nombre del .onnx. Se extrae el modelo en el mismo lugar.
        with zipfile.ZipFile(path) as bundle:
            member = next(name for name in bundle.namelist() if name.endswith("end2end.onnx"))
            data = bundle.read(member)
        Path(path).write_bytes(data)
        logger.info("Modelo de pose extraido del release: %s", path)
    return path


@dataclass(frozen=True, eq=False)
class Pose:
    points: np.ndarray  # (17, 2) float32, pixeles del cuadro completo
    scores: np.ndarray  # (17,) float32, confianza 0-1 aprox.


def crop_transform(box: tuple[float, float, float, float]) -> np.ndarray:
    """Matriz afin que lleva la caja (x, y, w, h) a la entrada 192x256,
    agrandando el lado corto para conservar la proporcion del modelo."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    if w / h > INPUT_W / INPUT_H:
        h = w * INPUT_H / INPUT_W
    else:
        w = h * INPUT_W / INPUT_H
    src = np.float32([[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx - w / 2, cy + h / 2]])
    dst = np.float32([[0, 0], [INPUT_W, 0], [0, INPUT_H]])
    return cv2.getAffineTransform(src, dst)


def decode_simcc(
    simcc_x: np.ndarray, simcc_y: np.ndarray, inverse: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """(K, 384) y (K, 512) -> puntos (K, 2) en pixeles del cuadro y scores (K,)."""
    xs = simcc_x.argmax(axis=1) / SIMCC_SPLIT
    ys = simcc_y.argmax(axis=1) / SIMCC_SPLIT
    scores = np.minimum(simcc_x.max(axis=1), simcc_y.max(axis=1))
    homogeneous = np.stack([xs, ys, np.ones_like(xs)], axis=1)
    return (homogeneous @ inverse.T).astype(np.float32), scores.astype(np.float32)


class PoseEstimator:
    """Sesion compartida entre analizadores (ver object_detector_backend:
    una sesion por archivo de modelo; `run()` es re-entrante)."""

    def __init__(self) -> None:
        self._model_path = _ensure_model()
        self._session = adquirir_sesion(self._model_path)
        try:
            self._input_name = self._session.get_inputs()[0].name
        except Exception:
            soltar_sesion(self._model_path)  # A15, igual que YoloxDetector
            raise
        self._cerrado = False

    def close(self) -> None:
        if self._cerrado:
            return
        self._cerrado = True
        self._session = None
        soltar_sesion(self._model_path)

    def estimate(self, frame: np.ndarray, box: tuple[float, float, float, float]) -> Pose:
        transform = crop_transform(box)
        crop = cv2.warpAffine(frame, transform, (INPUT_W, INPUT_H), flags=cv2.INTER_LINEAR)
        rgb = crop[:, :, ::-1].astype(np.float32)
        tensor = ((rgb - MEAN) / STD).transpose(2, 0, 1)[None]
        simcc_x, simcc_y = self._session.run(None, {self._input_name: tensor})
        points, scores = decode_simcc(simcc_x[0], simcc_y[0], cv2.invertAffineTransform(transform))
        return Pose(points=points, scores=scores)
