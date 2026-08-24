"""Panel de la Vista Inteligente: contadores en vivo de entradas/salidas
de Cruce de Línea para la camara enfocada."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, HeaderCardWidget

from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent


class LineCrossingPanel(HeaderCardWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Cruce de Línea")
        self._device_id: int | None = None

        content = QWidget(self)
        self.viewLayout.addWidget(content)

        numbers_row = QHBoxLayout()
        self.in_label = self._build_counter(content, numbers_row, "Entradas")
        self.out_label = self._build_counter(content, numbers_row, "Salidas")

        layout = QVBoxLayout(content)
        layout.addLayout(numbers_row)

        event_bus.detection.connect(self._on_detection, Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _build_counter(parent: QWidget, row: QHBoxLayout, caption: str) -> CaptionLabel:
        column = QVBoxLayout()
        number_label = CaptionLabel("—", parent)
        number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        number_label.setStyleSheet("font-size: 32px; font-weight: 700; color: #3b82f6;")
        caption_label = CaptionLabel(caption, parent)
        caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(number_label)
        column.addWidget(caption_label)
        row.addLayout(column)
        return number_label

    def set_device(self, device_id: int | None) -> None:
        self._device_id = device_id
        self.in_label.setText("—")
        self.out_label.setText("—")

    def _on_detection(self, event: DetectionEvent) -> None:
        if event.device_id != self._device_id or event.analyzer_name != "line_crossing":
            return
        if "count_in" in event.metrics:
            self.in_label.setText(str(event.metrics["count_in"]))
        if "count_out" in event.metrics:
            self.out_label.setText(str(event.metrics["count_out"]))
