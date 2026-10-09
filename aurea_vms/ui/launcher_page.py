"""Pantalla de Inicio: el resumen operativo (Fase 2 de la interfaz,
2026-10-08/09).

- Las cifras del sitio (widgets/dashboard_panel.py): el estado de ahora.
- Los graficos del periodo elegido (24 h, 7 dias o 30 dias): incidentes en
  el tiempo por tipo, por tipo, por estado y las camaras con mas incidentes
  (ui/home_stats.py calcula, widgets/home_charts.py dibuja). El selector de
  periodo va en una sola fila sobre los graficos y vale para todos.
- "Requiere atención": los incidentes abiertos mas recientes, con su
  captura; un clic los abre en Alarmas.
- Las camaras con su estado; un clic abre la camara en Vista en Vivo.

Antes Inicio era un launcher de tarjetas de colores por modulo mas una lista
de accesos ("Base") que repetia lo mismo con otros nombres. La navegacion
ahora esta en el riel de la izquierda (widgets/nav_rail.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from qfluentwidgets import ScrollArea, SegmentedWidget

from aurea_vms.core import app_state
from aurea_vms.core.event_bus import event_bus
from aurea_vms.ui import home_stats
from aurea_vms.ui.theme import ACCENT, SPACE
from aurea_vms.ui.widgets.attention_list import AttentionList
from aurea_vms.ui.widgets.camera_status_list import CameraStatusList
from aurea_vms.ui.widgets.dashboard_panel import DashboardPanel
from aurea_vms.ui.widgets.home_charts import BarList, ChartLegend, DonutChart, LineChart

_CARD_BG = "#1a2029"
_BORDER = "#252e3b"
_MUTED = "#8a93a0"
CHARTS_REFRESH_MS = 15000


@dataclass
class _Card:
    frame: QFrame
    layout: QVBoxLayout
    subtitle: QLabel


def _card(title: str, parent: QWidget, action: QPushButton | None = None) -> _Card:
    card = QFrame(parent)
    card.setObjectName("homeCard")
    card.setStyleSheet(
        f"#homeCard {{ background: {_CARD_BG}; border: 1px solid {_BORDER}; border-radius: 12px; }}"
        "#homeCard QLabel { background: none; }"
    )
    layout = QVBoxLayout(card)
    layout.setContentsMargins(SPACE["l"], SPACE["l"], SPACE["l"], SPACE["l"])
    layout.setSpacing(SPACE["m"])
    header = QHBoxLayout()
    titles = QVBoxLayout()
    titles.setSpacing(2)
    label = QLabel(title, card)
    label.setStyleSheet("color: #f3f4f6; font-size: 14px; font-weight: 700;")
    titles.addWidget(label)
    subtitle = QLabel("", card)
    subtitle.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
    subtitle.hide()
    titles.addWidget(subtitle)
    header.addLayout(titles)
    header.addStretch(1)
    if action is not None:
        header.addWidget(action, 0, Qt.AlignmentFlag.AlignTop)
    layout.addLayout(header)
    return _Card(card, layout, subtitle)


def _set_subtitle(card: _Card, text: str) -> None:
    card.subtitle.setText(text)
    card.subtitle.setVisible(bool(text))


def _link(text: str, parent: QWidget) -> QPushButton:
    button = QPushButton(text, parent)
    button.setFlat(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(
        "QPushButton { color: #60a5fa; border: none; font-size: 12px; background: none; }"
        "QPushButton:hover { color: #93c5fd; }"
    )
    return button


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


class LauncherPage(QWidget):
    module_requested = Signal(int)
    incident_requested = Signal(int)
    camera_requested = Signal(int)

    def __init__(
        self,
        modules: list[tuple[str, object, type]],
        categories: dict[str, list[str]],
        is_admin: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        del categories, is_admin  # la navegacion esta en el riel
        labels = [label for label, _icon, _cls in modules]
        alarms_index = labels.index("Alarmas") if "Alarmas" in labels else None
        self.range_key = home_stats.DEFAULT_RANGE

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        container = QWidget(scroll)
        container.setObjectName("homeSurface")
        container.setStyleSheet("#homeSurface { background: transparent; }")
        scroll.setWidget(container)

        outer = QVBoxLayout(container)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(SPACE["l"])

        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("Resumen operativo", container)
        title.setStyleSheet("color: #f9fafb; font-size: 22px; font-weight: 700;")
        heading.addWidget(title)
        subtitle = QLabel("Lo que pasa ahora en el sitio elegido.", container)
        subtitle.setStyleSheet(f"color: {_MUTED}; font-size: 13px;")
        heading.addWidget(subtitle)
        outer.addLayout(heading)

        self.dashboard = DashboardPanel(container, title=None)
        outer.addWidget(self.dashboard)

        # --- graficos del periodo ---
        period_row = QHBoxLayout()
        period_label = QLabel("Período", container)
        period_label.setStyleSheet(f"color: {_MUTED}; font-size: 12px; font-weight: 600;")
        period_row.addWidget(period_label)
        self.period = SegmentedWidget(container)
        for key, (_points, label, _unit) in home_stats.RANGES.items():
            self.period.addItem(key, label, onClick=lambda _=False, k=key: self.set_range(k))
        self.period.setCurrentItem(self.range_key)
        period_row.addWidget(self.period)
        period_row.addStretch(1)
        outer.addSpacing(SPACE["s"])
        outer.addLayout(period_row)

        charts_top = QHBoxLayout()
        charts_top.setSpacing(SPACE["l"])
        self.timeline_card = _card("Incidentes en el tiempo", container)
        self.timeline = LineChart(self.timeline_card.frame)
        self.timeline_legend = ChartLegend(self.timeline_card.frame, marker="line", inline=True)
        self.timeline_card.layout.addWidget(self.timeline, stretch=1)
        self.timeline_card.layout.addWidget(self.timeline_legend)
        charts_top.addWidget(self.timeline_card.frame, stretch=2)

        self.types_card = _card("Por tipo", container)
        self.types_donut = DonutChart(self.types_card.frame)
        self.types_legend = ChartLegend(self.types_card.frame)
        self.types_card.layout.addWidget(self.types_donut, 0, Qt.AlignmentFlag.AlignHCenter)
        self.types_card.layout.addWidget(self.types_legend)
        self.types_card.layout.addStretch(1)
        charts_top.addWidget(self.types_card.frame, stretch=1)
        outer.addLayout(charts_top)

        charts_bottom = QHBoxLayout()
        charts_bottom.setSpacing(SPACE["l"])
        self.status_card = _card("Por estado", container)
        status_body = QHBoxLayout()
        status_body.setSpacing(SPACE["xl"])
        self.status_donut = DonutChart(self.status_card.frame)
        self.status_legend = ChartLegend(self.status_card.frame)
        self.status_legend.setMaximumWidth(300)
        status_body.addWidget(self.status_donut)
        status_body.addWidget(self.status_legend, 0, Qt.AlignmentFlag.AlignVCenter)
        status_body.addStretch(1)
        self.status_card.layout.addLayout(status_body)
        charts_bottom.addWidget(self.status_card.frame, stretch=1)

        self.cameras_chart_card = _card("Cámaras con más incidentes", container)
        self.camera_bars = BarList(ACCENT, self.cameras_chart_card.frame)
        self.cameras_chart_card.layout.addWidget(self.camera_bars)
        self.cameras_chart_card.layout.addStretch(1)
        charts_bottom.addWidget(self.cameras_chart_card.frame, stretch=1)
        outer.addLayout(charts_bottom)

        # --- lo abierto y las camaras ---
        columns = QHBoxLayout()
        columns.setSpacing(SPACE["l"])
        see_all = None
        if alarms_index is not None:
            see_all = _link("Ver todo en Alarmas  →", container)
            see_all.clicked.connect(lambda: self.module_requested.emit(alarms_index))
        attention_card = _card("Requiere atención", container, see_all)
        self.attention = AttentionList(attention_card.frame)
        self.attention.incident_requested.connect(self.incident_requested)
        attention_card.layout.addWidget(self.attention)
        columns.addWidget(attention_card.frame, stretch=3)

        cameras_card = _card("Cámaras", container)
        self.cameras = CameraStatusList(cameras_card.frame)
        self.cameras.camera_requested.connect(self.camera_requested)
        cameras_card.layout.addWidget(self.cameras)
        columns.addWidget(cameras_card.frame, stretch=2, alignment=Qt.AlignmentFlag.AlignTop)
        outer.addSpacing(SPACE["s"])
        outer.addLayout(columns)
        outer.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        event_bus.alarm.connect(self._on_alarm, Qt.ConnectionType.QueuedConnection)
        event_bus.site_filter_changed.connect(self._on_site_filter_changed)
        self._charts_timer = QTimer(self)
        self._charts_timer.setInterval(CHARTS_REFRESH_MS)
        self._charts_timer.timeout.connect(self.refresh_charts)
        self.refresh_charts()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.refresh_charts()
        self._charts_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._charts_timer.stop()
        super().hideEvent(event)

    def _on_alarm(self, _event) -> None:
        self.refresh_charts()

    def _on_site_filter_changed(self, _site_id: object) -> None:
        self.refresh_charts()

    def set_range(self, key: str) -> None:
        if key not in home_stats.RANGES:
            return
        self.range_key = key
        self.period.setCurrentItem(key)
        self.refresh_charts()

    def refresh_charts(self) -> None:
        stats = home_stats.compute(app_state.current_site_id, self.range_key)
        per = f"por {stats.unit}"
        _set_subtitle(
            self.timeline_card,
            f"{_plural(stats.total, 'incidente')} en {stats.range_label} · {per}",
        )
        self.timeline.set_data(stats.labels, stats.series)
        self.timeline_legend.set_items(
            [(label, sum(values), color) for label, values, color in stats.series]
        )
        self.timeline_legend.setVisible(len(stats.series) > 1)

        self.types_donut.set_data(stats.by_type, str(stats.total), "incidentes")
        self.types_legend.set_items(stats.by_type, percent_of=stats.total or None)
        self.status_donut.set_data(stats.by_status, str(stats.open_total), "abiertos")
        self.status_legend.set_items(stats.by_status, percent_of=stats.total or None)
        self.camera_bars.set_data(stats.by_camera)
        _set_subtitle(self.cameras_chart_card, f"En {stats.range_label}")
