"""Dataclasses de los eventos que circulan por el EventBus.

Estos objetos cruzan threads (StreamWorker / Analyzer / AlarmEngine -> UI),
por eso son inmutables y no contienen referencias a widgets de Qt.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]  # x, y, w, h en pixeles del frame original
    # Contorno simplificado (solo lo completa deteccion de movimiento) para
    # dibujar la silueta real en vez de un rectangulo -- None en el resto
    # de los analizadores, que solo tienen bbox.
    polygon: tuple[tuple[int, int], ...] | None = None
    # Puntos de referencia en pixeles del frame original (solo Detección
    # Facial: ojo derecho, ojo izquierdo, nariz, boca, oreja derecha,
    # oreja izquierda, en ese orden) -- se usan para una firma geometrica
    # mas robusta que comparar pixeles crudos al decidir si dos capturas
    # son la misma cara.
    keypoints: tuple[tuple[float, float], ...] | None = None


@dataclass(frozen=True)
class DetectionEvent:
    device_id: int
    analyzer_name: str
    timestamp: float
    detections: tuple[Detection, ...] = field(default_factory=tuple)
    # Metricas tipo dashboard (ej. {"occupancy": 4} o {"count_in": 12, "count_out": 9}),
    # separadas de `detections` porque no todas representan un objeto individual.
    metrics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AlarmEvent:
    alarm_event_id: int  # id de la fila persistida en la tabla alarm_events
    rule_id: int
    device_id: int
    timestamp: float
    object_class: str
    confidence: float
    severity: str = "medio"
    snapshot_path: str | None = None
    clip_path: str | None = None
    # Accion "play_sound" de la regla: la UI (hilo principal) reproduce el
    # beep -- los engines no tocan audio.
    play_sound: bool = False


@dataclass(frozen=True)
class DeviceStatusEvent:
    device_id: int
    online: bool
    detail: str = ""


@dataclass(frozen=True)
class ClipReadyEvent:
    alarm_event_id: int
    clip_path: str
