from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, ComboBox, PushButton, SimpleCardWidget, TableWidget

from aurea_vms.core.analytics.registry import ANALYZER_DISPLAY_NAMES, AVAILABLE_ANALYZERS
from aurea_vms.core.analytics_engine import analytics_engine
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent
from aurea_vms.models import repository
from aurea_vms.ui.dialogs.face_detection_config_dialog import FaceDetectionConfigDialog
from aurea_vms.ui.dialogs.line_crossing_config_dialog import LineCrossingConfigDialog
from aurea_vms.ui.dialogs.motion_detection_config_dialog import MotionDetectionConfigDialog
from aurea_vms.ui.dialogs.people_counting_config_dialog import PeopleCountingConfigDialog
from aurea_vms.ui.notify import warn

DIALOG_BY_ANALYZER = {
    "motion_detection": MotionDetectionConfigDialog,
    "people_counting": PeopleCountingConfigDialog,
    "line_crossing": LineCrossingConfigDialog,
    "face_detection": FaceDetectionConfigDialog,
}


class AnalyticsConfigModule(QWidget):
    """Activar/configurar cada tipo de analítica por cámara, con un
    dashboard embebido que muestra las métricas en vivo (ej. ocupación de
    Conteo de Personas, entradas/salidas de Cruce de Línea)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metrics: dict[str, dict] = {}

        self.device_selector = ComboBox(self)
        self.device_selector.currentIndexChanged.connect(self._on_device_changed)

        self.table = TableWidget(self)
        self.table.setRowCount(len(AVAILABLE_ANALYZERS))
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Analizador", "Estado", ""])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(6)

        for row, analyzer_name in enumerate(AVAILABLE_ANALYZERS):
            self.table.setItem(row, 0, QTableWidgetItem(ANALYZER_DISPLAY_NAMES[analyzer_name]))
            self.table.setItem(row, 1, QTableWidgetItem("—"))
            button = PushButton("Configurar")
            button.clicked.connect(lambda _checked=False, name=analyzer_name: self._configure(name))
            self.table.setCellWidget(row, 2, button)

        self.dashboard_card = SimpleCardWidget(self)
        dashboard_layout = QVBoxLayout(self.dashboard_card)
        self.dashboard_label = BodyLabel("Sin datos aún.", self.dashboard_card)
        self.dashboard_label.setWordWrap(True)
        dashboard_layout.addWidget(self.dashboard_label)

        top_row = QHBoxLayout()
        top_row.addWidget(BodyLabel("Cámara:"))
        top_row.addWidget(self.device_selector, stretch=1)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self.table)
        layout.addWidget(BodyLabel("Dashboard en vivo:"))
        layout.addWidget(self.dashboard_card)

        event_bus.detection.connect(self._on_detection, Qt.ConnectionType.QueuedConnection)

        self._reload_devices()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._reload_devices()
        super().showEvent(event)

    def focus_device(self, device_id: int) -> None:
        """API publica para otros modulos (ej. boton "Ajustes avanzados" de
        Dispositivos): selecciona esta camara en el combo."""
        index = self.device_selector.findData(device_id)
        if index >= 0:
            self.device_selector.setCurrentIndex(index)

    def _reload_devices(self) -> None:
        current_id = self.device_selector.currentData()
        self.device_selector.blockSignals(True)
        self.device_selector.clear()
        for device in repository.list_devices():
            self.device_selector.addItem(device.name, userData=device.id)
        if current_id is not None:
            index = self.device_selector.findData(current_id)
            if index >= 0:
                self.device_selector.setCurrentIndex(index)
        self.device_selector.blockSignals(False)
        self._refresh_table()

    def _current_device_id(self) -> int | None:
        return self.device_selector.currentData()

    def _on_device_changed(self, _index: int) -> None:
        self._metrics.clear()
        self.dashboard_label.setText("Sin datos aún.")
        self._refresh_table()

    def _refresh_table(self) -> None:
        device_id = self._current_device_id()
        configs = (
            {c.analyzer_name: c for c in repository.list_analytics_configs(device_id)}
            if device_id is not None
            else {}
        )

        for row, analyzer_name in enumerate(AVAILABLE_ANALYZERS):
            config = configs.get(analyzer_name)
            if config is None:
                status = "No configurada"
            elif config.enabled:
                running = " (corriendo)" if analytics_engine.is_running(config.id) else ""
                status = f"Habilitada{running}"
            else:
                status = "Deshabilitada"
            self.table.setItem(row, 1, QTableWidgetItem(status))

    def _configure(self, analyzer_name: str) -> None:
        device_id = self._current_device_id()
        if device_id is None:
            warn(self, "Configurar analizador", "Seleccioná una cámara primero.")
            return
        device = repository.get_device(device_id)
        if device is None:
            return

        dialog = DIALOG_BY_ANALYZER[analyzer_name](device, self)
        if dialog.exec():
            config = dialog.save()
            if config.enabled:
                analytics_engine.start(config, device)
            else:
                analytics_engine.stop(config.id)
            self._refresh_table()

    def _on_detection(self, event: DetectionEvent) -> None:
        if event.device_id != self._current_device_id() or not event.metrics:
            return
        self._metrics[event.analyzer_name] = event.metrics
        self._render_dashboard()

    def _render_dashboard(self) -> None:
        if not self._metrics:
            self.dashboard_label.setText("Sin datos aún.")
            return
        lines = []
        for analyzer_name, metrics in self._metrics.items():
            display = ANALYZER_DISPLAY_NAMES.get(analyzer_name, analyzer_name)
            metrics_text = "  ·  ".join(f"{key}: {value}" for key, value in metrics.items())
            lines.append(f"{display} — {metrics_text}")
        self.dashboard_label.setText("\n".join(lines))
