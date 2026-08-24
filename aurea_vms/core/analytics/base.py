"""Interfaz pluggable de analizadores.

Cada instancia de Analyzer se crea para UNA configuracion (AnalyticsConfig)
activa de UNA camara y vive mientras esa configuracion este habilitada —
puede guardar estado propio entre llamadas (ej. contadores acumulados de
cruce de linea, el modelo de fondo de deteccion de movimiento).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import cv2
import numpy as np

from aurea_vms.core.events import Detection

# Lado mas largo maximo que se le pasa al detector/modelo: entrada mas
# chica y pareja para todas las camaras (independiente de a que
# resolucion este transmitiendo cada una) -- menos costo de preprocesado
# (conversion de color, marshaling a la imagen del modelo) por muestra,
# sin perder precision real (los modelos livianos que usamos ya
# reescalan internamente a algo similar o mas chico).
MAX_ANALYSIS_DIMENSION = 640


@dataclass(frozen=True)
class AnalysisResult:
    detections: tuple[Detection, ...] = ()
    # Metricas tipo dashboard, ej. {"occupancy": 4} o {"count_in": 12, "count_out": 9}
    metrics: dict = field(default_factory=dict)


class Analyzer(ABC):
    name: str

    @abstractmethod
    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult: ...


def crop_to_roi(
    frame: np.ndarray, roi: tuple[int, int, int, int] | None
) -> tuple[np.ndarray, int, int]:
    """Devuelve (recorte, offset_x, offset_y). roi=None devuelve el frame entero."""
    if roi is None:
        return frame, 0, 0
    x, y, w, h = roi
    return frame[y : y + h, x : x + w], x, y


def bbox_center_in_roi(bbox: tuple[int, int, int, int], roi: tuple[int, int, int, int]) -> bool:
    x, y, w, h = bbox
    rx, ry, rw, rh = roi
    cx, cy = x + w / 2, y + h / 2
    return rx <= cx <= rx + rw and ry <= cy <= ry + rh


def resize_for_inference(image: np.ndarray) -> tuple[np.ndarray, float]:
    """Reescala (solo hacia abajo) una imagen ya recortada al ROI -- si se
    hace antes de recortar, las coordenadas de ROI guardadas (en pixeles
    de la imagen nativa) quedarian mal alineadas contra un frame mas
    chico. Devuelve (imagen para el modelo, escala aplicada; 1.0 si no
    hizo falta reescalar)."""
    height, width = image.shape[:2]
    largest = max(height, width)
    if largest <= MAX_ANALYSIS_DIMENSION:
        return image, 1.0
    scale = MAX_ANALYSIS_DIMENSION / largest
    resized = cv2.resize(
        image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
    )
    return resized, scale


def rescale_bbox(
    bbox: tuple[int, int, int, int], inv_scale: float, offset_x: int = 0, offset_y: int = 0
) -> tuple[int, int, int, int]:
    """Convierte un bbox en pixeles de la imagen reescalada de vuelta a
    pixeles del frame nativo (deshace resize_for_inference) y le suma el
    offset del recorte de ROI si corresponde."""
    x, y, w, h = bbox
    return (
        round(x * inv_scale) + offset_x,
        round(y * inv_scale) + offset_y,
        round(w * inv_scale),
        round(h * inv_scale),
    )
