"""Dashboard minimalista de incidentes, analiticas y clips."""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    PushButton,
    StrongBodyLabel,
    TableWidget,
    TitleLabel,
)

from aurea_vms.core import app_state, media_store
from aurea_vms.core.analytics.registry import ANALYZER_DISPLAY_NAMES
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import AlarmEvent as AlarmEventDTO
from aurea_vms.core.events import ClipReadyEvent
from aurea_vms.models import repository
from aurea_vms.models.media_asset import KIND_CLIP
from aurea_vms.ui.labels import display_class
from aurea_vms.ui.theme import severity_soft_qcolor, severity_text_qcolor

REFRESH_MS = 5000
# Filas que se pintan en la tabla. Los contadores de las tarjetas ya NO
# salen de esta pagina: son COUNT agregados sobre toda la tabla.
ROW_LIMIT = 200
SEVERITY_LABELS = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "info": "Info"}
COLUMNS = ["Hora", "Cámara", "Analítica", "Incidente", "Severidad", "Estado", "Clip"]
ANALYTIC_COLORS = {
    "door_state": "#f59e0b",
    "people_counting": "#22c55e",
    "line_crossing": "#38bdf8",
    "face_detection": "#c084fc",
    "motion_detection": "#22c55e",
    "unknown": "#64748b",
}


class _MetricCard(QWidget):
    def __init__(self, title: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QWidget {{ background-color: rgba(16, 21, 30, 225);"
            f" border: 1px solid {accent}55; border-radius: 10px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self.value = TitleLabel("—", self)
        self.value.setStyleSheet(f"color: {accent}; background: transparent;")
        layout.addWidget(self.value)
        caption = CaptionLabel(title, self)
        caption.setStyleSheet("background: transparent;")
        layout.addWidget(caption)

    def set_value(self, value: str) -> None:
        self.value.setText(value)


class _IncidentGraph(QWidget):
    """Barras apiladas de incidentes por hora y tipo de analitica."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buckets: list[tuple[str, dict[str, int]]] = []
        self.setMinimumHeight(150)
        self.setStyleSheet(
            "background-color: rgba(16, 21, 30, 225); border: 1px solid #2a3441;"
            " border-radius: 10px;"
        )

    def set_data(self, buckets: list[tuple[str, dict[str, int]]]) -> None:
        self._buckets = buckets
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        chart = self.rect().adjusted(42, 12, -12, -28)
        painter.setPen(QPen(QColor("#334155"), 1))
        painter.drawLine(chart.bottomLeft(), chart.bottomRight())
        painter.drawLine(chart.topLeft(), chart.bottomLeft())
        if not self._buckets:
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Sin incidentes")
            painter.end()
            return

        maximum = max((sum(values.values()) for _, values in self._buckets), default=1) or 1
        bucket_width = chart.width() / len(self._buckets)
        for index, (label, values) in enumerate(self._buckets):
            x = chart.left() + index * bucket_width + bucket_width * 0.2
            bar_width = bucket_width * 0.6
            y = chart.bottom()
            for analyzer, count in values.items():
                height = chart.height() * count / maximum
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(ANALYTIC_COLORS.get(analyzer, "#64748b")))
                painter.drawRoundedRect(x, y - height, bar_width, height, 3, 3)
                y -= height
            if index in {0, len(self._buckets) - 1} or index % 3 == 0:
                painter.setPen(QColor("#94a3b8"))
                painter.drawText(
                    int(x - 12),
                    int(chart.bottom() + 20),
                    int(bar_width + 24),
                    16,
                    Qt.AlignmentFlag.AlignCenter,
                    label,
                )
        painter.end()


class EventDashboardModule(QWidget):
    """Vista de supervision con incidentes agrupados y acceso a evidencia."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._events = []
        self._media_by_event = {}
        self._device_names: dict[int, str] = {}
        self._analytic_by_rule: dict[int, str] = {}

        title_row = QHBoxLayout()
        title = StrongBodyLabel("Centro inteligente de incidentes")
        title.setStyleSheet("font-size: 18px;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        live = QLabel("● EN VIVO")
        live.setStyleSheet("color: #22c55e; font-weight: 700;")
        title_row.addWidget(live)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.total_card = _MetricCard("Incidentes", "#60a5fa", self)
        self.active_card = _MetricCard("Pendientes", "#f59e0b", self)
        self.clip_card = _MetricCard("Clips disponibles", "#22c55e", self)
        self.type_card = _MetricCard("Analítica dominante", "#c084fc", self)
        for card in (self.total_card, self.active_card, self.clip_card, self.type_card):
            metrics.addWidget(card, stretch=1)

        self.graph = _IncidentGraph(self)
        self.table = TableWidget(self)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addLayout(title_row)
        layout.addWidget(
            CaptionLabel("Incidentes por cámara y analítica, con acceso directo a la evidencia.")
        )
        layout.addLayout(metrics)
        layout.addWidget(self.graph)
        layout.addWidget(self.table, stretch=1)

        event_bus.alarm.connect(self._on_alarm, Qt.ConnectionType.QueuedConnection)
        event_bus.clip_ready.connect(self._on_clip_ready, Qt.ConnectionType.QueuedConnection)
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

    def _on_clip_ready(self, _event: ClipReadyEvent) -> None:
        self.refresh()

    def refresh(self) -> None:
        site_id = app_state.current_site_id
        events = repository.list_alarm_events(limit=ROW_LIMIT, site_id=site_id)
        devices = repository.list_devices(site_id=site_id)
        self._device_names = {device.id: device.name for device in devices}
        self._events = events
        self._analytic_by_rule = {
            rule.id: rule.analyzer_name
            for rule in repository.list_alarm_rules()
            if rule.id is not None
        }
        self._media_by_event = repository.list_media_for_events([event.id for event in events])
        self._refresh_summary(site_id)
        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            self._set_row(row, event)

    def _analytics_name(self, event) -> str:
        return self._analytic_by_rule.get(event.rule_id, "unknown")

    def _clip_path(self, event_id: int) -> str | None:
        for asset in self._media_by_event.get(event_id, []):
            if asset.kind == KIND_CLIP:
                path = media_store.absolute_path(asset.rel_path)
                return str(path) if path.exists() else None
        return None

    def _refresh_summary(self, site_id: int | None) -> None:
        counts = Counter(self._analytics_name(event) for event in self._events)
        self.total_card.set_value(str(repository.count_alarm_events(site_id=site_id)))
        self.active_card.set_value(str(repository.count_pending_alarm_events(site_id=site_id)))
        self.clip_card.set_value(
            str(sum(self._clip_path(event.id) is not None for event in self._events))
        )
        dominant = counts.most_common(1)
        self.type_card.set_value(
            ANALYZER_DISPLAY_NAMES.get(dominant[0][0], dominant[0][0]) if dominant else "—"
        )

        now = dt.datetime.now().replace(minute=0, second=0, microsecond=0)
        grouped: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for event in self._events:
            hour = dt.datetime.fromtimestamp(event.timestamp).replace(
                minute=0, second=0, microsecond=0
            )
            delta = int((now - hour).total_seconds() // 3600)
            if 0 <= delta < 12:
                grouped[delta][self._analytics_name(event)] += 1
        buckets = [
            ((now - dt.timedelta(hours=delta)).strftime("%H:%M"), dict(grouped.get(delta, {})))
            for delta in reversed(range(12))
        ]
        self.graph.set_data(buckets)

    def _set_row(self, row: int, event) -> None:
        analyzer = self._analytics_name(event)
        values = [
            dt.datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S"),
            self._device_names.get(event.device_id, f"Cámara #{event.device_id}"),
            ANALYZER_DISPLAY_NAMES.get(analyzer, "Sin clasificar"),
            display_class(event.object_class),
        ]
        for column, value in enumerate(values):
            self.table.setItem(row, column, QTableWidgetItem(value))

        severity = QTableWidgetItem(SEVERITY_LABELS.get(event.severity, event.severity))
        severity.setForeground(severity_text_qcolor(event.severity, True))
        severity.setBackground(severity_soft_qcolor(event.severity))
        self.table.setItem(row, 4, severity)

        status = QTableWidgetItem("Resuelta" if event.status == "resuelta" else "Pendiente")
        status.setForeground(QColor("#22c55e" if event.status == "resuelta" else "#f59e0b"))
        self.table.setItem(row, 5, status)

        path = self._clip_path(event.id)
        button = PushButton(FluentIcon.PLAY, "Ver")
        button.setToolTip("Abrir clip del incidente")
        button.setEnabled(path is not None)
        if path:
            button.clicked.connect(lambda _checked=False, clip=path: self._open_clip(clip))
        self.table.setCellWidget(row, 6, button)

    @staticmethod
    def _open_clip(path: str) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
