"""Panel de la Vista Inteligente: indicador en vivo de Detección de
Movimiento para la camara enfocada."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, HeaderCardWidget

from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent


class MotionPanel(HeaderCardWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Detección de Movimiento")
        self._device_id: int | None = None

        content = QWidget(self)
        self.viewLayout.addWidget(content)

        self.count_label = CaptionLabel("—", content)
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setStyleSheet("font-size: 44px; font-weight: 700; color: #3b82f6;")

        self.hint_label = CaptionLabel("Regiones con movimiento", content)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(content)
        layout.addWidget(self.count_label)
        layout.addWidget(self.hint_label)

        event_bus.detection.connect(self._on_detection, Qt.ConnectionType.QueuedConnection)

    def set_device(self, device_id: int | None) -> None:
        self._device_id = device_id
        self.count_label.setText("—")

    def _on_detection(self, event: DetectionEvent) -> None:
        if event.device_id != self._device_id or event.analyzer_name != "motion_detection":
            return
        regions = event.metrics.get("regiones")
        if regions is not None:
            self.count_label.setText(str(regions))
