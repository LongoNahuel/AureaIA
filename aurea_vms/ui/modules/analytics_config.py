from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    PushButton,
    SimpleCardWidget,
    SwitchButton,
    TableWidget,
)

from aurea_vms.core.analytics.registry import ANALYZER_DISPLAY_NAMES, AVAILABLE_ANALYZERS
from aurea_vms.core.analytics_engine import analytics_engine
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent
from aurea_vms.models import repository
from aurea_vms.ui.dialogs.door_state_config_dialog import DoorStateConfigDialog
from aurea_vms.ui.dialogs.face_detection_config_dialog import FaceDetectionConfigDialog
from aurea_vms.ui.dialogs.line_crossing_config_dialog import LineCrossingConfigDialog
from aurea_vms.ui.dialogs.people_counting_config_dialog import PeopleCountingConfigDialog
from aurea_vms.ui.notify import warn

DIALOG_BY_ANALYZER = {
    "door_state": DoorStateConfigDialog,
    "people_counting": PeopleCountingConfigDialog,
    "line_crossing": LineCrossingConfigDialog,
    "face_detection": FaceDetectionConfigDialog,
}


class _TrendChart(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self.setMinimumHeight(56)

    def set_values(self, values: list[float]) -> None:
        self._values = values[-30:]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#171d27"))
        if len(self._values) < 2:
            return
        maximum = max(max(self._values), 1.0)
        minimum = min(self._values)
        span = max(maximum - minimum, 1.0)
        points = []
        width = max(1, self.width() - 12)
        height = max(1, self.height() - 12)
        for index, value in enumerate(self._values):
            x = 6 + width * index / (len(self._values) - 1)
            y = 6 + height * (1 - (value - minimum) / span)
            points.append((x, y))
        painter.setPen(QPen(QColor("#60a5fa"), 2))
        for first, second in zip(points, points[1:], strict=True):
            painter.drawLine(QPointF(*first), QPointF(*second))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#60a5fa"))
        for x, y in points[-3:]:
            painter.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))


class _HeatmapWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[float, float]] = []
        self.setMinimumHeight(112)

    def set_points(self, points: list[tuple[float, float]]) -> None:
        self._points = points[-200:]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#171d27"))
        if not self._points:
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Sin actividad registrada")
            return
        max_x = max((point[0] for point in self._points), default=1.0) or 1.0
        max_y = max((point[1] for point in self._points), default=1.0) or 1.0
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y in self._points:
            px = 8 + (self.width() - 16) * x / max_x
            py = 8 + (self.height() - 16) * y / max_y
            painter.setBrush(QColor(239, 68, 68, 65))
            painter.drawEllipse(QRectF(px - 10, py - 10, 20, 20))
            painter.setBrush(QColor(251, 191, 36, 155))
            painter.drawEllipse(QRectF(px - 4, py - 4, 8, 8))


class _DashboardCard(QFrame):
    def __init__(self, title: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("analyticsDashboardCard")
        self.setStyleSheet(
            f"#analyticsDashboardCard {{ background: #1b2330; border: 1px solid #2b3748; "
            f"border-left: 4px solid {accent}; border-radius: 6px; }}"
        )
        self.title = CaptionLabel(title, self)
        self.value = QLabel("—", self)
        self.value.setStyleSheet("font-size: 24px; font-weight: 700; color: #f8fafc;")
        self.detail = CaptionLabel("Esperando datos", self)
        self.chart = _TrendChart(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(3)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        layout.addWidget(self.chart)

    def update_card(self, value: str, detail: str, history: list[float]) -> None:
        self.value.setText(value)
        self.detail.setText(detail)
        self.chart.set_values(history)


class AnalyticsConfigModule(QWidget):
    """Activar/configurar cada tipo de analítica por cámara, con un
    dashboard embebido que muestra las métricas en vivo (ej. ocupación de
    Conteo de Personas, entradas/salidas de Cruce de Línea).

    El switch de "Habilitado" prende/apaga una analítica YA configurada
    sin pasar por el diálogo completo (que ademas pide una captura nueva
    de la cámara cada vez que se abre); solo queda deshabilitado para una
    analítica que nunca se configuró -- ahi hace falta "Configurar" al
    menos una vez (ROI/línea, parámetros)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metrics: dict[str, dict] = {}
        self._history: dict[str, list[float]] = {}

        self.device_selector = ComboBox(self)
        self.device_selector.currentIndexChanged.connect(self._on_device_changed)

        self.table = TableWidget(self)
        self.table.setRowCount(len(AVAILABLE_ANALYZERS))
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Analizador", "Habilitado", "Estado", ""])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        # La columna del boton lleva ancho fijo: ResizeToContents mide los
        # items, no los cellWidget, y recortaba el "Configurar".
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(6)

        for row, analyzer_name in enumerate(AVAILABLE_ANALYZERS):
            self.table.setItem(row, 0, QTableWidgetItem(ANALYZER_DISPLAY_NAMES[analyzer_name]))

            switch = SwitchButton()
            switch.setOnText("")
            switch.setOffText("")
            switch.checkedChanged.connect(
                lambda checked, name=analyzer_name: self._on_toggle(name, checked)
            )
            self.table.setCellWidget(row, 1, switch)

            self.table.setItem(row, 2, QTableWidgetItem("—"))

            button = PushButton("Configurar")
            button.clicked.connect(lambda _checked=False, name=analyzer_name: self._configure(name))
            self.table.setCellWidget(row, 3, button)
            self.table.setColumnWidth(3, button.sizeHint().width() + 24)

        self.dashboard_card = SimpleCardWidget(self)
        dashboard_layout = QVBoxLayout(self.dashboard_card)
        dashboard_layout.setContentsMargins(10, 10, 10, 10)
        self.dashboard_status = BodyLabel("Sin datos aún.", self.dashboard_card)
        dashboard_layout.addWidget(self.dashboard_status)
        self.dashboard_grid = QGridLayout()
        self.dashboard_grid.setSpacing(8)
        dashboard_layout.addLayout(self.dashboard_grid)
        self._dashboard_cards: dict[str, _DashboardCard] = {}
        for index, analyzer_name in enumerate(AVAILABLE_ANALYZERS):
            card = _DashboardCard(
                ANALYZER_DISPLAY_NAMES[analyzer_name],
                {"door_state": "#f59e0b", "people_counting": "#3b82f6",
                 "line_crossing": "#22c55e", "face_detection": "#a855f7"}.get(
                    analyzer_name, "#64748b"
                ),
                self.dashboard_card,
            )
            self._dashboard_cards[analyzer_name] = card
            self.dashboard_grid.addWidget(card, index // 2, index % 2)
        self.heatmap_widget = _HeatmapWidget(self.dashboard_card)
        self.dashboard_grid.addWidget(self.heatmap_widget, 2, 0, 1, 2)

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
        self._history.clear()
        self.dashboard_status.setText("Sin datos aún.")
        for card in self._dashboard_cards.values():
            card.update_card("—", "Esperando datos", [])
        self.heatmap_widget.set_points([])
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
            switch: SwitchButton = self.table.cellWidget(row, 1)
            switch.blockSignals(True)
            if config is None:
                switch.setChecked(False)
                switch.setEnabled(False)
                switch.setToolTip("Configurala primero para poder habilitarla.")
                status = "No configurada"
            else:
                switch.setEnabled(True)
                switch.setToolTip("")
                switch.setChecked(config.enabled)
                if config.enabled:
                    running = " (corriendo)" if analytics_engine.is_running(config.id) else ""
                    status = f"Habilitada{running}"
                else:
                    status = "Deshabilitada"
            switch.blockSignals(False)
            self.table.setItem(row, 2, QTableWidgetItem(status))

    def _on_toggle(self, analyzer_name: str, checked: bool) -> None:
        """Prende/apaga una analítica YA configurada sin abrir el diálogo
        completo. El switch queda deshabilitado (ver _refresh_table)
        mientras no exista un AnalyticsConfig, asi que aca siempre hay
        uno."""
        device_id = self._current_device_id()
        if device_id is None:
            return
        config = repository.get_analytics_config_for(device_id, analyzer_name)
        if config is None:
            return
        repository.set_analytics_config_enabled(config.id, checked)
        if checked:
            device = repository.get_device(device_id)
            if device is not None:
                config = repository.get_analytics_config_for(device_id, analyzer_name)
                try:
                    analytics_engine.start(config, device)
                except Exception as exc:  # noqa: BLE001 - config inválido o modelo no descargable
                    # Si quedara enabled=True en la DB, el próximo arranque
                    # repetiría este fallo en _start_enabled_analytics.
                    repository.set_analytics_config_enabled(config.id, False)
                    warn(
                        self,
                        ANALYZER_DISPLAY_NAMES.get(analyzer_name, analyzer_name),
                        f"No se pudo iniciar la analítica: {exc}",
                    )
        else:
            analytics_engine.stop(config.id)
        event_bus.analytics_config_changed.emit(device_id)
        self._refresh_table()

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
            event_bus.analytics_config_changed.emit(device_id)
            self._refresh_table()

    def _on_detection(self, event: DetectionEvent) -> None:
        if event.device_id != self._current_device_id() or not event.metrics:
            return
        self._metrics[event.analyzer_name] = event.metrics
        value = self._metric_value(event.analyzer_name, event.metrics)
        if value is not None:
            self._history.setdefault(event.analyzer_name, []).append(value)
        self._render_dashboard()

    def _render_dashboard(self) -> None:
        if not self._metrics:
            self.dashboard_status.setText("Sin datos aún.")
            return
        self.dashboard_status.setText("Actualización en vivo")
        for analyzer_name, metrics in self._metrics.items():
            card = self._dashboard_cards.get(analyzer_name)
            if card is None:
                continue
            value, detail = self._card_content(analyzer_name, metrics)
            card.update_card(value, detail, self._history.get(analyzer_name, []))
            if analyzer_name == "people_counting":
                self.heatmap_widget.set_points(metrics.get("heatmap", []))

    @staticmethod
    def _metric_value(analyzer_name: str, metrics: dict) -> float | None:
        key = {
            "door_state": "cambio",
            "people_counting": "occupancy",
            "line_crossing": "total",
            "face_detection": "caras",
        }.get(analyzer_name)
        value = metrics.get(key) if key else None
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _card_content(analyzer_name: str, metrics: dict) -> tuple[str, str]:
        if analyzer_name == "door_state":
            state = str(metrics.get("estado", "—")).replace("_", " ").title()
            change = float(metrics.get("cambio", 0)) * 100
            return state, f"Variación: {change:.1f}%"
        if analyzer_name == "people_counting":
            occupancy = int(metrics.get("occupancy", 0))
            alert = "Alerta máxima" if metrics.get("alerta_maxima") else "Nivel normal"
            return str(occupancy), f"Personas detectadas · {alert}"
        if analyzer_name == "line_crossing":
            total = int(metrics.get("total", 0))
            incoming = int(metrics.get("count_in", 0))
            outgoing = int(metrics.get("count_out", 0))
            return str(total), f"Entradas: {incoming} · Salidas: {outgoing}"
        if analyzer_name == "face_detection":
            count = int(metrics.get("caras", 0))
            return str(count), "Rostros detectados"
        return "—", "Sin métricas visuales"
