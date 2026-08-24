"""Bus de eventos central: unico punto de comunicacion entre engines (threads)
y modulos de UI (main thread).

Los engines corren fuera del hilo principal de Qt y nunca deben tocar widgets
directamente. En su lugar publican en este bus (event_bus.<signal>.emit(...)).
Los modulos de UI se conectan con Qt.QueuedConnection para que Qt marshalee
el cruce de hilos de forma segura.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from aurea_vms.core.events import (
    AlarmEvent,
    ClipReadyEvent,
    DetectionEvent,
    DeviceStatusEvent,
)


class EventBus(QObject):
    detection = Signal(DetectionEvent)
    alarm = Signal(AlarmEvent)
    device_status = Signal(DeviceStatusEvent)
    clip_ready = Signal(ClipReadyEvent)
    # UI-only: pedido de "Vista rapida" desde Dispositivos -> abrir/enfocar
    # Vista en Vivo con esta camara asignada. Lo emite un modulo (hilo
    # principal), lo escucha MainWindow (tambien hilo principal).
    open_live_view_requested = Signal(int)
    # UI-only: pedido de "Ajustes avanzados" desde Dispositivos -> abrir/
    # enfocar Analizadores con esta camara seleccionada.
    open_analytics_config_requested = Signal(int)


event_bus = EventBus()
