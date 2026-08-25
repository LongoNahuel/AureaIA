"""Carga perezosa del modelo de deteccion de objetos por caja (MediaPipe
Tasks, EfficientDet-Lite2 entrenado sobre COCO), compartida por los
analizadores que necesitan detectar personas/vehiculos: Conteo de
Personas y Cruce de Linea.

Reemplaza al YOLOv8n (ultralytics) que se usaba antes: mismo catalogo de
clases (COCO), corre sobre el mismo runtime que ya se usa para Deteccion
Facial (Tasks API), y evita la licencia AGPL-3.0 de ultralytics.

Se usa Lite2 (variante float16) en vez de Lite0: mas preciso -- mas capas,
mejor recall en objetos chicos/lejanos -- a costa de mas latencia por
frame. Vale la pena a la tasa de muestreo de analiticas de este proyecto
(pocas camaras, unos pocos frames por segundo, no video en tiempo real
cuadro a cuadro)."""

from __future__ import annotations

import urllib.request

from aurea_vms.config.settings import settings

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/object_detector/"
    "efficientdet_lite2/float16/latest/efficientdet_lite2.tflite"
)
MODEL_FILENAME = "efficientdet_lite2.tflite"


def _ensure_model() -> str:
    model_path = settings.data_dir / "models" / MODEL_FILENAME
    if not model_path.exists():
        model_path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, model_path)
    return str(model_path)


def create_object_detector(
    category_allowlist: list[str], confidence_threshold: float, max_results: int = 20
):
    """Devuelve (modulo mediapipe, ObjectDetector ya configurado). Cada
    analizador arma su propia instancia porque el allowlist/umbral se fija
    al crear el detector, no por llamada -- distinto al viejo backend YOLO
    donde un mismo modelo se reusaba filtrando clases despues."""
    import mediapipe as mp
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions

    options = vision.ObjectDetectorOptions(
        base_options=BaseOptions(model_asset_path=_ensure_model()),
        running_mode=vision.RunningMode.IMAGE,
        score_threshold=confidence_threshold,
        max_results=max_results,
        category_allowlist=category_allowlist,
    )
    return mp, vision.ObjectDetector.create_from_options(options)
