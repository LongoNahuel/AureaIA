"""Detector de objetos por caja: YOLOX-Tiny (ONNX, via onnxruntime),
compartido por los analizadores que necesitan detectar personas/vehiculos:
Conteo de Personas y Cruce de Linea.

Reemplaza a EfficientDet-Lite2 (MediaPipe Tasks): YOLOX es una arquitectura
mas moderna (anchor-free, cabezas de clasificacion/regresion desacopladas,
2021) con mejor exactitud a una resolucion de entrada mas chica -- 416x416
en vez de 640x640 -- lo que la hace ademas mas eficiente en CPU. Con este
cambio mediapipe queda sin usar en todo el proyecto y se saca como
dependencia (ver pyproject.toml): menos superficie de DLLs nativas
cargando en runtime, uno de los focos recurrentes de problemas en Windows
y Linux durante este proyecto.

Antes de EfficientDet el proyecto uso YOLOv8 (ultralytics) y lo saco por
la licencia AGPL-3.0 (copyleft fuerte: usarla en un producto que corre en
red obliga a liberar el codigo fuente completo, o pagar una licencia
comercial). YOLOX (Megvii-BaseDetection) es Apache 2.0 -- sin esa
restriccion -- y sigue siendo familia YOLO: mismo catalogo COCO, sin
depender de pytorch en runtime (onnxruntime alcanza para inferencia).

El pre/post-procesamiento (letterbox con relleno gris 114 alineado
arriba-izquierda, decodificacion de las 3 cabezas por stride 8/16/32, NMS
class-agnostic) replica exactamente el demo oficial de ONNXRuntime de
YOLOX (yolox/data/data_augment.py::preproc y
yolox/utils/demo_utils.py::demo_postprocess/multiclass_nms) -- un detalle
mal portado ahi decodifica cajas en cualquier lado silenciosamente, sin
ningun error que lo delate."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import cv2
import numpy as np
import onnxruntime

from aurea_vms.core.analytics.model_assets import ensure_model
from aurea_vms.core.events import Detection

logger = logging.getLogger(__name__)

MODEL_URL = (
    "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx"
)
MODEL_FILENAME = "yolox_tiny.onnx"
INPUT_SIZE = (416, 416)  # (alto, ancho) -- test size oficial de YOLOX-Tiny
STRIDES = (8, 16, 32)
NMS_THRESHOLD = 0.45

COCO_CLASSES = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)  # yolox/data/datasets/coco_classes.py -- orden fijo, no reordenar
_CLASS_INDEX = {name: i for i, name in enumerate(COCO_CLASSES)}

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


def _preprocess(frame: np.ndarray) -> tuple[np.ndarray, float]:
    """Letterbox: reescala manteniendo la proporcion y rellena con gris
    (114,114,114) alineado arriba-izquierda -- sin centrar, la MISMA
    convencion que usa el export oficial de YOLOX. Devuelve (CHW float32
    sin normalizar 0-1 -- el modelo espera 0-255, escala aplicada)."""
    h, w = frame.shape[:2]
    padded = np.full((INPUT_SIZE[0], INPUT_SIZE[1], 3), 114, dtype=np.uint8)
    scale = min(INPUT_SIZE[0] / h, INPUT_SIZE[1] / w)
    resized = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LINEAR)
    padded[: int(h * scale), : int(w * scale)] = resized
    chw = padded.transpose(2, 0, 1).astype(np.float32)
    return chw, scale


def _decode_outputs(outputs: np.ndarray) -> np.ndarray:
    """Decodifica las 3 cabezas (stride 8/16/32) a cajas cx,cy,w,h en
    pixeles de la imagen de entrada (416x416) -- logica identica al
    postprocess oficial de YOLOX (demo_utils.py::demo_postprocess)."""
    grids = []
    strides_col = []
    for stride in STRIDES:
        hsize, wsize = INPUT_SIZE[0] // stride, INPUT_SIZE[1] // stride
        xv, yv = np.meshgrid(np.arange(wsize), np.arange(hsize))
        grid = np.stack((xv, yv), 2).reshape(1, -1, 2)
        grids.append(grid)
        strides_col.append(np.full((1, grid.shape[1], 1), stride))
    grids = np.concatenate(grids, 1)
    strides_col = np.concatenate(strides_col, 1)
    outputs[..., :2] = (outputs[..., :2] + grids) * strides_col
    outputs[..., 2:4] = np.exp(outputs[..., 2:4]) * strides_col
    return outputs


def _nms(boxes: np.ndarray, scores: np.ndarray, nms_thr: float) -> list[int]:
    """NMS de una sola clase (greedy, por IoU) -- yolox/utils/demo_utils.py::nms."""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        overlap = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(overlap <= nms_thr)[0] + 1]
    return keep


def _multiclass_nms(
    boxes: np.ndarray, scores: np.ndarray, nms_thr: float, score_thr: float
) -> np.ndarray | None:
    """NMS class-agnostic (una clase por caja, la de mayor score) --
    yolox/utils/demo_utils.py::multiclass_nms_class_agnostic."""
    cls_inds = scores.argmax(1)
    cls_scores = scores[np.arange(len(cls_inds)), cls_inds]

    valid = cls_scores > score_thr
    if not valid.any():
        return None
    valid_scores = cls_scores[valid]
    valid_boxes = boxes[valid]
    valid_cls_inds = cls_inds[valid]
    keep = _nms(valid_boxes, valid_scores, nms_thr)
    if not keep:
        return None
    return np.concatenate(
        [valid_boxes[keep], valid_scores[keep, None], valid_cls_inds[keep, None]], 1
    )


@dataclass
class _SesionCompartida:
    sesion: onnxruntime.InferenceSession
    usuarios: int = 1


# Una sesion de inferencia por ARCHIVO de modelo, no por analizador. Antes
# cada YoloxDetector creaba la suya: con N camaras x 2 analiticas (conteo y
# cruce de linea comparten este backend) eran 2N copias del mismo modelo de
# 20MB en RAM.
_sesiones: dict[str, _SesionCompartida] = {}
_lock = threading.Lock()


def _crear_sesion(ruta_modelo: str) -> onnxruntime.InferenceSession:
    options = onnxruntime.SessionOptions()
    # El pool intra-op ahora lo comparten TODOS los analizadores que usen
    # este modelo, no uno cada uno. Se mantiene en 2, y MEDIDO sobre 4
    # camaras x 2 analiticas (2026-09-22, 12 cores) compartir resulto ademas
    # mas rapido: 17.8 contra 14.2 inferencias/s, +25%. La razon es que 8
    # sesiones x 2 threads = 16 threads peleando por 12 cores, mas el hilo
    # de captura de cada camara y la UI; una sola sesion saca esa contencion.
    # O sea que no hubo trade-off RAM/CPU: se gano en las dos.
    options.intra_op_num_threads = 2
    return onnxruntime.InferenceSession(
        ruta_modelo, sess_options=options, providers=["CPUExecutionProvider"]
    )


def adquirir_sesion(ruta_modelo: str) -> onnxruntime.InferenceSession:
    """Devuelve la sesion de ese modelo, creandola si es la primera vez.

    Con lock porque los analizadores se construyen desde hilos distintos
    (AnalyticsWorker), y dos adquisiciones simultaneas sin proteger crearian
    dos sesiones para el mismo archivo y romperian el conteo.
    """
    with _lock:
        compartida = _sesiones.get(ruta_modelo)
        if compartida is None:
            _sesiones[ruta_modelo] = _SesionCompartida(_crear_sesion(ruta_modelo))
            logger.info("Sesión ONNX creada para %s", ruta_modelo)
            return _sesiones[ruta_modelo].sesion
        compartida.usuarios += 1
        return compartida.sesion


def soltar_sesion(ruta_modelo: str) -> None:
    """Libera la sesion cuando se va el ultimo usuario."""
    with _lock:
        compartida = _sesiones.get(ruta_modelo)
        if compartida is None:
            return
        compartida.usuarios -= 1
        if compartida.usuarios <= 0:
            del _sesiones[ruta_modelo]
            logger.info("Sesión ONNX liberada: %s", ruta_modelo)


def sesiones_vivas() -> dict[str, int]:
    """{ruta del modelo: cuantos la usan}. Para tests y diagnostico."""
    with _lock:
        return {ruta: compartida.usuarios for ruta, compartida in _sesiones.items()}


class YoloxDetector:
    """Envuelve la sesion de onnxruntime + pre/post-procesamiento.

    La sesion se COMPARTE entre los analizadores que usan el mismo modelo
    (ver adquirir_sesion). `run()` de onnxruntime es re-entrante, asi que
    varios hilos de analitica pueden inferir sobre la misma sesion. Medido
    con 4 camaras x 2 analiticas: 50MB en vez de 150MB, y 25% MAS rapido
    (ver el comentario de _crear_sesion).

    El allowlist/umbral de confianza se aplican por LLAMADA a `detect()`, no
    al crear la sesion -- el modelo ONNX siempre corre inferencia sobre las
    80 clases de COCO, se filtra despues."""

    def __init__(self) -> None:
        self._model_path = _ensure_model()
        self._session = adquirir_sesion(self._model_path)
        self._input_name = self._session.get_inputs()[0].name
        self._cerrado = False

    def close(self) -> None:
        """Suelta la sesion. Idempotente: `Analyzer.close()` puede llamarse
        mas de una vez durante un apagado desprolijo, y un doble decremento
        liberaria una sesion que todavia esta en uso."""
        if self._cerrado:
            return
        self._cerrado = True
        self._session = None
        soltar_sesion(self._model_path)

    def detect(
        self, frame: np.ndarray, category_allowlist: list[str], confidence_threshold: float
    ) -> list[Detection]:
        chw, scale = _preprocess(frame)
        outputs = self._session.run(None, {self._input_name: chw[None, :, :, :]})[0]
        predictions = _decode_outputs(outputs)[0]

        boxes = predictions[:, :4]
        scores = predictions[:, 4:5] * predictions[:, 5:]

        boxes_xyxy = np.empty_like(boxes)
        boxes_xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
        boxes_xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
        boxes_xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2.0
        boxes_xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2.0
        boxes_xyxy /= scale  # de vuelta a pixeles del frame de entrada

        dets = _multiclass_nms(boxes_xyxy, scores, NMS_THRESHOLD, confidence_threshold)
        if dets is None:
            return []

        allowed = {_CLASS_INDEX[name] for name in category_allowlist if name in _CLASS_INDEX}
        detections = []
        for x1, y1, x2, y2, score, cls_idx in dets:
            cls_idx = int(cls_idx)
            if cls_idx not in allowed:
                continue
            detections.append(
                Detection(
                    label=COCO_CLASSES[cls_idx],
                    confidence=float(score),
                    bbox=(round(x1), round(y1), round(x2 - x1), round(y2 - y1)),
                )
            )
        return detections
