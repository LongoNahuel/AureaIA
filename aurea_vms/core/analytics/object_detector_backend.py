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

from aurea_vms.core.analytics.model_assets import ensure_model
from aurea_vms.core.events import Detection

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/object_detector/"
    "efficientdet_lite2/float16/latest/efficientdet_lite2.tflite"
)
MODEL_FILENAME = "efficientdet_lite2.tflite"

# Generoso a proposito: Conteo de Personas y Cruce de Linea comparten este
# backend pero cubren clases con formas muy distintas (personas, autos,
# motos). No es un chequeo de "forma de persona", solo descarta cajas
# degeneradas (una tira casi sin ancho o sin alto) tipicas de un glitch
# del detector, no una clase real.
BOX_ASPECT_RATIO_RANGE = (0.08, 8.0)


def passes_box_shape_filter(box_w: float, box_h: float) -> bool:
    if box_w <= 0 or box_h <= 0:
        return False
    ratio = box_w / box_h
    return BOX_ASPECT_RATIO_RANGE[0] <= ratio <= BOX_ASPECT_RATIO_RANGE[1]


def passes_min_area_filter(
    box_w: float, box_h: float, frame_area: float, min_area_percent: float
) -> bool:
    """min_area_percent<=0 desactiva el filtro. frame_area es el area (en
    pixeles del frame original) contra la que se calcula el %, resolucion
    de camara aparte."""
    if min_area_percent <= 0 or frame_area <= 0:
        return True
    return (box_w * box_h) >= frame_area * min_area_percent / 100.0


def _iou(
    box_a: tuple[float, float, float, float], box_b: tuple[float, float, float, float]
) -> float:
    ax, ay, aw, ah = box_a
    bx, by, bw, bh = box_b
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0:
        return 0.0
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


def deduplicate_by_iou(detections: list[Detection], iou_threshold: float = 0.5) -> list[Detection]:
    """El detector ya aplica NMS interno, pero puede dejar pasar dos cajas
    casi superpuestas sobre el MISMO objeto (variantes de escala/offset
    del mismo blob). Sin este filtro cada una termina creando un track
    separado en el CentroidTracker -- ej. una persona contada dos veces
    en el conteo de ocupacion. Se queda con la de mayor confianza de cada
    grupo solapado (misma clase, IoU >= iou_threshold)."""
    ordered = sorted(detections, key=lambda d: d.confidence, reverse=True)
    kept: list[Detection] = []
    for det in ordered:
        if any(
            other.label == det.label and _iou(other.bbox, det.bbox) >= iou_threshold
            for other in kept
        ):
            continue
        kept.append(det)
    return kept


def _ensure_model() -> str:
    return ensure_model(MODEL_FILENAME, MODEL_URL)


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
