from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer
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
from aurea_vms.ui.dialogs.face_detection_config_dialog import FaceDetectionConfigDialog
from aurea_vms.ui.dialogs.line_crossing_config_dialog import LineCrossingConfigDialog
from aurea_vms.ui.dialogs.monitor_tamper_config_dialog import MonitorTamperConfigDialog
from aurea_vms.ui.dialogs.people_counting_config_dialog import PeopleCountingConfigDialog
from aurea_vms.ui.notify import warn
from aurea_vms.ui.theme import enable_tabular_numbers
from aurea_vms.ui.widgets.analytics_visuals import (
    ANALYTIC_ACCENTS,
    GRID_LINE,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    MetricHistory,
    Sparkline,
    draw_heatmap,
)
from aurea_vms.ui.widgets.people_count_panel import capacity_status

DIALOG_BY_ANALYZER = {
    "monitor_tamper": MonitorTamperConfigDialog,
    "people_counting": PeopleCountingConfigDialog,
    "line_crossing": LineCrossingConfigDialog,
    "face_detection": FaceDetectionConfigDialog,
}


class _HeatmapWidget(QWidget):
    """Mapa de calor acumulado de Conteo de Personas (misma grilla que se
    superpone al video), con la proporcion del cuadro de la camara."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid: np.ndarray | None = None
        self.setMinimumHeight(120)

    def set_grid(self, grid: np.ndarray | None) -> None:
        self._grid = grid if isinstance(grid, np.ndarray) else None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(SURFACE))
        painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        if self._grid is None:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Mapa de calor: sin actividad registrada",
            )
            return
        rows, cols = self._grid.shape
        available = QRectF(self.rect()).adjusted(8, 8, -8, -22)
        scale = min(available.width() / cols, available.height() / rows)
        target = QRectF(0, 0, cols * scale, rows * scale)
        target.moveCenter(available.center())
        painter.setPen(QPen(QColor(GRID_LINE), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(target)
        draw_heatmap(painter, self._grid, target)
        painter.setPen(QColor(TEXT_SECONDARY))
        painter.drawText(
            QRectF(0, self.height() - 20, self.width(), 18),
            Qt.AlignmentFlag.AlignCenter,
            "Dónde se para la gente · más intenso = más permanencia (últimos minutos pesan más)",
        )


class _DashboardCard(QFrame):
    def __init__(self, title: str, accent: str, unit: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("analyticsDashboardCard")
        self.setStyleSheet(
            f"#analyticsDashboardCard {{ background: #1b2330; border: 1px solid #2b3748; "
            f"border-left: 4px solid {accent}; border-radius: 6px; }}"
        )
        self.title = CaptionLabel(title, self)
        self.title.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self.value = QLabel("—", self)
        self.value.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {TEXT_PRIMARY};")
        enable_tabular_numbers(self.value)
        self.detail = CaptionLabel("Esperando datos", self)
        self.detail.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self.chart = Sparkline(accent, unit, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(3)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)
        layout.addWidget(self.chart)

    def update_card(self, value: str, detail: str) -> None:
        self.value.setText(value)
        self.detail.setText(detail)


# Como se resume cada analitica en su tendencia: (metrica, modo, unidad).
TREND_SPEC = {
    "monitor_tamper": ("alerta", "max", " alerta"),
    "people_counting": ("occupancy", "max", " personas"),
    "line_crossing": ("total", "sum", " cruces"),
    "face_detection": ("caras", "max", " caras"),
}


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
        self._history: dict[str, MetricHistory] = {
            name: MetricHistory(bucket_s=10, buckets=60, mode=spec[1])
            for name, spec in TREND_SPEC.items()
        }
        # Ultimo total de Cruce de Linea: la tendencia registra cruces por
        # intervalo (la diferencia), no el acumulado.
        self._last_crossing_total: int | None = None

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
                ANALYTIC_ACCENTS.get(analyzer_name, "#64748b"),
                TREND_SPEC.get(analyzer_name, ("", "max", ""))[2],
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
        layout.addWidget(BodyLabel("Dashboard en vivo · últimos 10 minutos:"))
        layout.addWidget(self.dashboard_card)

        event_bus.detection.connect(self._on_detection, Qt.ConnectionType.QueuedConnection)

        # Refresco de 1 s: tendencias y telemetria de los workers
        # (fps/latencia/errores reales) en la columna Estado. Repintar en
        # cada DetectionEvent era redibujar 4 tarjetas por cuadro analizado.
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)

        self._reload_devices()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._reload_devices()
        self._tick_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._tick_timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        now = time.time()
        for name, card in self._dashboard_cards.items():
            history = self._history.get(name)
            card.chart.set_series(history.series(now) if history else [])
        self._refresh_status_column()

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
        for history in self._history.values():
            history.clear()
        self._last_crossing_total = None
        self.dashboard_status.setText("Sin datos aún.")
        for card in self._dashboard_cards.values():
            card.update_card("—", "Esperando datos")
            card.chart.set_series([])
        self.heatmap_widget.set_grid(None)
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
                status = self._running_status(config.id) if config.enabled else "Deshabilitada"
            switch.blockSignals(False)
            self.table.setItem(row, 2, QTableWidgetItem(status))

    @staticmethod
    def _running_status(config_id: int) -> str:
        stats = analytics_engine.stats(config_id)
        if stats is None:
            return "Habilitada (detenida)"
        if stats.frames < 2:
            return "Habilitada · iniciando…"
        text = f"Corriendo · {stats.fps:.1f} fps · {stats.latency_ms:.0f} ms"
        if stats.errors:
            text += f" · {stats.errors} errores"
        return text

    def _refresh_status_column(self) -> None:
        device_id = self._current_device_id()
        if device_id is None:
            return
        configs = {c.analyzer_name: c for c in repository.list_analytics_configs(device_id)}
        for row, analyzer_name in enumerate(AVAILABLE_ANALYZERS):
            config = configs.get(analyzer_name)
            if config is None or not config.enabled:
                continue
            item = self.table.item(row, 2)
            text = self._running_status(config.id)
            if item is None or item.text() != text:
                self.table.setItem(row, 2, QTableWidgetItem(text))

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
        name = event.analyzer_name
        self._metrics[name] = event.metrics
        self._record_trend(name, event.metrics, event.timestamp)
        card = self._dashboard_cards.get(name)
        if card is not None:
            card.update_card(*self._card_content(name, event.metrics))
        if name == "people_counting":
            self.heatmap_widget.set_grid(event.metrics.get("heatmap"))
        self.dashboard_status.setText("Actualización en vivo")

    def _record_trend(self, name: str, metrics: dict, timestamp: float) -> None:
        spec = TREND_SPEC.get(name)
        history = self._history.get(name)
        if spec is None or history is None:
            return
        value = metrics.get(spec[0])
        if name == "monitor_tamper":
            value = 1.0 if metrics.get("estado") == "alerta" else 0.0
        if not isinstance(value, (int, float)):
            return
        if name == "line_crossing":
            previous = self._last_crossing_total
            self._last_crossing_total = int(value)
            value = int(value) - previous if previous is not None and value >= previous else 0
        history.add(float(value), timestamp)

    @staticmethod
    def _card_content(analyzer_name: str, metrics: dict) -> tuple[str, str]:
        if analyzer_name == "monitor_tamper":
            alert = metrics.get("estado") == "alerta"
            zones = metrics.get("zonas") or []
            return ("INCIDENTE DETECTADO" if alert else "Sin incidentes"), (
                f"{metrics.get('incidentes', 0)} incidentes · {len(zones)} pantallas vigiladas"
            )
        if analyzer_name == "people_counting":
            occupancy = int(metrics.get("occupancy", 0))
            maximum = int(metrics.get("max_people", 0) or 0)
            status, _ = capacity_status(occupancy, maximum)
            value = f"{occupancy} / {maximum}" if maximum else str(occupancy)
            return value, f"Personas en zona · {status} · pico {metrics.get('peak', occupancy)}"
        if analyzer_name == "line_crossing":
            total = int(metrics.get("total", 0))
            if metrics.get("direction_enabled", True) is False:
                return str(total), "Cruces en ambas direcciones"
            incoming = int(metrics.get("count_in", 0))
            outgoing = int(metrics.get("count_out", 0))
            return str(total), (
                f"Entradas {incoming} · Salidas {outgoing} · balance {incoming - outgoing:+d}"
            )
        if analyzer_name == "face_detection":
            count = int(metrics.get("caras", 0))
            return str(count), "Rostros detectados"
        return "—", "Sin métricas visuales"
