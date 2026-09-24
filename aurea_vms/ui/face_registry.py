"""Capturas de rostro de la sesion, compartidas por toda la UI.

Una captura = un paso de una cara por la camara (un track del analizador),
representado por su MEJOR toma: el analizador solo publica tomas donde el
rostro se ve bien (`face_quality.is_face_visible`) y, del mismo track, solo
las que mejoran a la anterior; aca la toma nueva reemplaza a la vieja. No
hay identidad entre pasos ni conteo de personas unicas: la misma persona que
sale y vuelve a entrar es otra captura.

La galeria del panel lateral, la tira del dashboard y el visor forense son
espejos de este registro (`captures_changed`). Guarda tambien las tomas
previas de cada captura para el visor forense, acotadas en memoria.

Vive en el hilo de la GUI: el bus entrega los DetectionEvent con
QueuedConnection, asi que las QPixmap que se cachean en cada registro se
crean en el hilo correcto.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal

from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent, FaceShot
from aurea_vms.models import repository

# Capturas guardadas por camara (las mas recientes).
MAX_CAPTURES_PER_DEVICE = 60
# Tomas previas guardadas por captura para el visor forense.
HISTORY_PER_CAPTURE = 8
# Un track del analizador no pasa mas de ~1 s sin ser visto (su max_age es
# 0,6 s). Si llega una toma con un track_id ya conocido pero la captura
# lleva mas que esto sin actualizarse, el analizador se reinicio y volvio a
# numerar desde 1: es una captura nueva, no una mejora de la vieja.
TRACK_REUSE_S = 30.0

_capture_ids = itertools.count(1)


@dataclass(eq=False)
class FaceRecord:
    """Una toma de un rostro con toda su evidencia."""

    device_id: int
    track_id: int
    timestamp: float
    image: np.ndarray  # recorte de galeria (cuadrado, margen moderado)
    quality: float | None
    confidence: float
    bbox: tuple[int, int, int, int]
    forensic: np.ndarray | None = None
    context_jpeg: bytes | None = None
    context_scale: float = 1.0
    frame_size: tuple[int, int] = (0, 0)
    details: dict = field(default_factory=dict)
    # Identificador de la captura (el paso), estable entre sus tomas.
    capture_id: int = 0
    # Cache de pixmaps por tamaño (se crean en el hilo de la GUI).
    pixmaps: dict = field(default_factory=dict)

    @property
    def when(self) -> str:
        return dt.datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")

    @property
    def evidence(self) -> np.ndarray:
        """La imagen de mayor detalle disponible (la forense si existe)."""
        return self.forensic if isinstance(self.forensic, np.ndarray) else self.image


class FaceRegistry(QObject):
    captures_changed = Signal(int)  # device_id

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Por camara, la mejor toma de cada captura, la mas reciente primero.
        self._records: dict[int, list[FaceRecord]] = {}
        self._history: dict[int, deque[FaceRecord]] = {}  # capture_id -> tomas
        self._names: dict[int, str] = {}
        self._connected = False

    def connect_bus(self) -> None:
        """Se conecta al bus la primera vez que una vista lo pide (y no al
        importar el modulo: los tests arman el registro sin bus)."""
        if self._connected:
            return
        self._connected = True
        event_bus.detection.connect(self.process, Qt.ConnectionType.QueuedConnection)
        event_bus.analytics_config_changed.connect(
            self._forget_name, Qt.ConnectionType.QueuedConnection
        )

    # --- consultas ----------------------------------------------------------

    def records(self, device_id: int | None) -> list[FaceRecord]:
        return list(self._records.get(device_id, [])) if device_id is not None else []

    def all_devices(self) -> list[int]:
        return list(self._records)

    def capture_count(self, device_id: int | None = None) -> int:
        if device_id is not None:
            return len(self._records.get(device_id, []))
        return sum(len(records) for records in self._records.values())

    def history(self, record: FaceRecord) -> list[FaceRecord]:
        """Todas las tomas guardadas de esa captura, la mas reciente primero."""
        return list(reversed(self._history.get(record.capture_id, ())))

    def camera_name(self, device_id: int) -> str:
        name = self._names.get(device_id)
        if name is None:
            device = repository.get_device(device_id)
            name = device.name if device is not None else f"Cámara {device_id}"
            self._names[device_id] = name
        return name

    # --- mutaciones ---------------------------------------------------------

    def _forget_name(self, device_id: int) -> None:
        self._names.pop(device_id, None)

    def clear(self, device_id: int | None = None) -> None:
        """Vacia las capturas de una camara (o de todas)."""
        devices = [device_id] if device_id is not None else list(self._records)
        for device in devices:
            for record in self._records.pop(device, []):
                self._history.pop(record.capture_id, None)
            self.captures_changed.emit(device)

    def reset(self) -> None:
        self._records.clear()
        self._history.clear()
        self._names.clear()

    def process(self, event: DetectionEvent) -> None:
        if event.analyzer_name != "face_detection":
            return
        shots = event.metrics.get("face_shots")
        if not shots:
            return
        changed = False
        for shot in shots:
            changed = self._observe(event.device_id, shot, event.timestamp) or changed
        if changed:
            self.captures_changed.emit(event.device_id)

    def _observe(self, device_id: int, shot: FaceShot, timestamp: float) -> bool:
        image = shot.image
        if not isinstance(image, np.ndarray) or image.size == 0:
            return False
        records = self._records.setdefault(device_id, [])
        previous_index = next(
            (
                index
                for index, record in enumerate(records)
                if record.track_id == shot.track_id
                and timestamp - record.timestamp <= TRACK_REUSE_S
            ),
            None,
        )
        previous = records[previous_index] if previous_index is not None else None
        record = FaceRecord(
            device_id=device_id,
            track_id=shot.track_id,
            timestamp=timestamp,
            image=image,
            quality=shot.quality,
            confidence=shot.confidence,
            bbox=shot.bbox,
            forensic=shot.forensic if isinstance(shot.forensic, np.ndarray) else None,
            context_jpeg=shot.context_jpeg,
            context_scale=shot.context_scale,
            frame_size=shot.frame_size,
            details=dict(shot.details),
            capture_id=previous.capture_id if previous is not None else next(_capture_ids),
        )
        history = self._history.setdefault(record.capture_id, deque(maxlen=HISTORY_PER_CAPTURE))
        history.append(record)
        if previous is not None:
            # El analizador ya solo manda tomas que mejoran; se chequea igual
            # para que el registro nunca cambie una toma por una peor.
            if (record.quality or 0.0) <= (previous.quality or 0.0):
                return False
            del records[previous_index]
        records.insert(0, record)
        for dropped in records[MAX_CAPTURES_PER_DEVICE:]:
            self._history.pop(dropped.capture_id, None)
        del records[MAX_CAPTURES_PER_DEVICE:]
        return True


face_registry = FaceRegistry()
