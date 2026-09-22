"""Dashboard operativo de eventos: métricas y feed en tiempo real."""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CaptionLabel, StrongBodyLabel, TableWidget, TitleLabel

from aurea_vms.core import app_state
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import AlarmEvent as AlarmEventDTO
from aurea_vms.models import repository
from aurea_vms.models.alarm_rule import SEVERITY_CRITICAL
from aurea_vms.ui.labels import display_class
from aurea_vms.ui.theme import severity_soft_qcolor, severity_text_qcolor

REFRESH_MS = 5000
# Filas que se pintan en la tabla. Los contadores de las tarjetas ya NO
# salen de esta pagina: son COUNT agregados sobre toda la tabla.
ROW_LIMIT = 200
SEVERITY_LABELS = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "info": "Info"}
COLUMNS = ["Hora", "Cámara", "Evento", "Severidad", "Confianza", "Estado"]


class _MetricCard(QWidget):
    def __init__(self, title: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QWidget {{ background-color: rgba(16, 21, 30, 225);"
            f" border: 1px solid {accent}55; border-radius: 10px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        self.value = TitleLabel("—", self)
        self.value.setStyleSheet(f"color: {accent}; background: transparent;")
        layout.addWidget(self.value)
        caption = CaptionLabel(title, self)
        caption.setStyleSheet("background: transparent;")
        layout.addWidget(caption)

    def set_value(self, value: str) -> None:
        self.value.setText(value)


class EventDashboardModule(QWidget):
    """Vista de supervisión para detectar rápidamente actividad y tendencia."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device_names: dict[int, str] = {}

        title_row = QHBoxLayout()
        title = StrongBodyLabel("Centro de eventos")
        title.setStyleSheet("font-size: 18px;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.live_status = QLabel("● EN VIVO")
        self.live_status.setStyleSheet("color: #22c55e; font-weight: 700;")
        title_row.addWidget(self.live_status)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.total_card = _MetricCard("Eventos registrados", "#60a5fa", self)
        self.active_card = _MetricCard("Pendientes de atención", "#f59e0b", self)
        self.critical_card = _MetricCard("Críticos", "#ff4d5e", self)
        self.last_card = _MetricCard("Último evento", "#22c55e", self)
        for card in (self.total_card, self.active_card, self.critical_card, self.last_card):
            metrics.addWidget(card, stretch=1)

        self.table = TableWidget(self)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addLayout(title_row)
        layout.addWidget(
            CaptionLabel("Monitoreo en tiempo real de las detecciones que generan alarmas.")
        )
        layout.addLayout(metrics)
        layout.addWidget(self.table, stretch=1)

        event_bus.alarm.connect(self._on_alarm, Qt.ConnectionType.QueuedConnection)
        event_bus.site_filter_changed.connect(self._on_site_filter_changed)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def _on_site_filter_changed(self, _site_id: object) -> None:
        self.refresh()

    def _on_alarm(self, _event: AlarmEventDTO) -> None:
        self.refresh()

    def refresh(self) -> None:
        site_id = app_state.current_site_id
        # El filtro va en la consulta, no despues: filtrando en Python sobre
        # la pagina de 200 globales, con un sitio seleccionado la tabla
        # mostraba un subconjunto arbitrario en vez de los ultimos 200 de
        # ese sitio.
        events = repository.list_alarm_events(limit=ROW_LIMIT, site_id=site_id)
        self._device_names = {
            device.id: device.name for device in repository.list_devices(site_id=site_id)
        }

        # Contadores agregados en SQL: len(events) era el largo de la pagina,
        # o sea que la tarjeta de totales se clavaba en 200.
        self.total_card.set_value(str(repository.count_alarm_events(site_id=site_id)))
        self.active_card.set_value(str(repository.count_pending_alarm_events(site_id=site_id)))
        self.critical_card.set_value(
            str(repository.count_alarm_events(site_id=site_id, severity=SEVERITY_CRITICAL))
        )
        self.last_card.set_value(
            dt.datetime.fromtimestamp(events[0].timestamp).strftime("%H:%M:%S") if events else "—"
        )

        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            self._set_row(row, event)

    def _set_row(self, row: int, event) -> None:
        when = dt.datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S")
        values = [
            when,
            self._device_names.get(event.device_id, f"Cámara #{event.device_id}"),
            display_class(event.object_class),
        ]
        for column, value in enumerate(values):
            self.table.setItem(row, column, QTableWidgetItem(value))

        severity = QTableWidgetItem(SEVERITY_LABELS.get(event.severity, event.severity))
        dark = True
        severity.setForeground(severity_text_qcolor(event.severity, dark))
        severity.setBackground(severity_soft_qcolor(event.severity))
        self.table.setItem(row, 3, severity)
        self.table.setItem(row, 4, QTableWidgetItem(f"{event.confidence:.0%}"))
        status = "Resuelta" if event.status == "resuelta" else "Pendiente"
        status_item = QTableWidgetItem(status)
        status_item.setForeground(QColor("#22c55e" if status == "Resuelta" else "#f59e0b"))
        self.table.setItem(row, 5, status_item)
