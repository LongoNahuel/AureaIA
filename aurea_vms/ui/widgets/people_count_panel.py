"""Panel de la Vista Inteligente: ocupacion actual de la camara enfocada
(analizador Conteo de Personas), contra el aforo si esta configurado, con
el desglose por zona, el pico y la tendencia de los ultimos minutos."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from aurea_vms.core.events import DetectionEvent
from aurea_vms.ui.widgets.analytics_panel_base import (
    AnalyticsPanelBase,
    StatusPill,
    big_number,
    caption,
)
from aurea_vms.ui.widgets.analytics_visuals import (
    ANALYTIC_ACCENTS,
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_WARNING,
    LevelBar,
    MetricHistory,
    Sparkline,
)

# Desde que fraccion del aforo se avisa "cerca del limite".
NEAR_CAPACITY_RATIO = 0.8


def capacity_status(occupancy: int, maximum: int) -> tuple[str, str]:
    """(texto, color de estado) de la ocupacion contra el aforo."""
    if maximum <= 0:
        return "Sin aforo definido", STATUS_OK
    if occupancy >= maximum:
        return "Aforo alcanzado", STATUS_CRITICAL
    if occupancy >= maximum * NEAR_CAPACITY_RATIO:
        return "Cerca del aforo", STATUS_WARNING
    return "Normal", STATUS_OK


class PeopleCountPanel(AnalyticsPanelBase):
    analyzer_name = "people_counting"
    panel_title = "Conteo de Personas"

    def build(self, content: QWidget) -> None:
        self._history = MetricHistory(bucket_s=10, buckets=60, mode="max")
        self._occupancy = 0

        top = QHBoxLayout()
        number_column = QVBoxLayout()
        number_column.setSpacing(0)
        self.count_label = big_number(content, 44)
        self.hint_label = caption("personas en zona", content)
        number_column.addWidget(self.count_label)
        number_column.addWidget(self.hint_label)
        top.addLayout(number_column)
        top.addStretch(1)
        self.status_pill = StatusPill(content)
        top.addWidget(self.status_pill, alignment=Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(top)

        self.capacity_bar = LevelBar(content)
        self.body.addWidget(self.capacity_bar)
        self.capacity_label = caption("", content)
        self.body.addWidget(self.capacity_label)
        self.zones_label = caption("", content)
        self.body.addWidget(self.zones_label)

        self.body.addWidget(caption("Ocupación · últimos 10 min", content))
        self.trend = Sparkline(ANALYTIC_ACCENTS["people_counting"], " personas", content)
        self.body.addWidget(self.trend)

    def reset(self) -> None:
        self._history.clear()
        self._occupancy = 0
        self.count_label.setText("—")
        self.status_pill.set_status("Sin lectura", STATUS_OK)
        self.capacity_bar.setVisible(False)
        self.capacity_label.setText("")
        self.zones_label.setVisible(False)
        self.trend.set_series([])

    def on_event(self, event: DetectionEvent) -> None:
        metrics = event.metrics
        occupancy = metrics.get("occupancy")
        if occupancy is None:
            return
        self._occupancy = int(occupancy)
        self._history.add(self._occupancy, event.timestamp)
        self.count_label.setText(str(self._occupancy))

        maximum = int(metrics.get("max_people", 0) or 0)
        self.status_pill.set_status(*capacity_status(self._occupancy, maximum))
        peak = metrics.get("peak")
        peak_text = f"Pico: {peak}" if peak is not None else ""
        if maximum:
            text, color = capacity_status(self._occupancy, maximum)
            self.capacity_bar.setVisible(True)
            self.capacity_bar.set_level(self._occupancy / maximum, color)
            self.capacity_label.setText(f"{self._occupancy} de {maximum} permitidas · {peak_text}")
        else:
            self.capacity_bar.setVisible(False)
            self.capacity_label.setText(peak_text)

        zones = metrics.get("zones")
        self.zones_label.setVisible(bool(zones))
        if zones:
            self.zones_label.setText(
                "  ·  ".join(f"Zona {index + 1}: {count}" for index, count in enumerate(zones))
            )

    def on_tick(self, now: float) -> None:
        self.trend.set_series(self._history.series(now))
