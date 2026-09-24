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


@dataclass(frozen=True, eq=False)
class FaceShot:
    """Una toma de rostro lista para la galeria, armada por el analizador
    sobre el MISMO cuadro en el que detecto la cara. Antes la galeria
    recortaba en el hilo de la UI sobre el ultimo cuadro del stream -- uno
    posterior: si la persona se habia movido, la miniatura salia corrida o
    mostraba fondo.

    eq=False: lleva arrays de numpy, que no se comparan con ==."""

    track_id: int  # id del track del analizador: un paso de una cara por la camara
    image: object  # np.ndarray BGR, recorte cuadrado con margen alrededor de la cara
    quality: float  # 0-1: nitidez, frontalidad, tamaño y confianza combinados
    confidence: float
    bbox: tuple[int, int, int, int]  # pixeles del cuadro completo
    # --- evidencia para el visor forense ---
    # Recorte en resolucion NATIVA con margen amplio (cabeza y hombros), sin
    # reescalar ni realzar: es lo que se exporta como evidencia.
    forensic: object | None = None
    # Cuadro completo en JPEG (lado mayor <= CONTEXT_MAX_SIDE): donde estaba
    # la persona en la escena. Comprimido porque cada toma vive en memoria.
    context_jpeg: bytes | None = None
    context_scale: float = 1.0  # pixeles del contexto = pixeles del cuadro * escala
    frame_size: tuple[int, int] = (0, 0)  # (ancho, alto) del cuadro original
    # Desglose del puntaje: frontal, nitidez, tamaño, confianza, distancia
    # entre ojos en px.
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DetectionEvent:
    device_id: int
    analyzer_name: str
    timestamp: float
    detections: tuple[Detection, ...] = field(default_factory=tuple)
    # Metricas tipo dashboard (ej. {"occupancy": 4} o {"count_in": 12, "count_out": 9}),
    # separadas de `detections` porque no todas representan un objeto individual.
    metrics: dict = field(default_factory=dict)
    # Lo que puede disparar una regla de alarma, cuando difiere de lo que
    # se dibuja: Cruce de Linea dibuja todos los objetos trackeados pero
    # solo alarma por los que CRUZARON en esta muestra; Conteo de Personas
    # con aforo configurado alarma recien al superarlo. None = usar
    # `detections` (comportamiento historico del resto de las analiticas).
    triggers: tuple[Detection, ...] | None = None


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
    # Accion "notify_desktop" de la regla: la UI dispara el globo de la
    # bandeja del sistema -- los engines no tocan widgets. QSystemTrayIcon
    # es un widget de Qt y construirlo desde el hilo de una analitica es
    # comportamiento indefinido (ver core/desktop_notify.py).
    notify_desktop: bool = False


@dataclass(frozen=True)
class DeviceStatusEvent:
    device_id: int
    online: bool
    detail: str = ""


@dataclass(frozen=True)
class ClipReadyEvent:
    alarm_event_id: int
    clip_path: str
