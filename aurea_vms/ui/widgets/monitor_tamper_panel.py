"""Panel de la Vista Inteligente: Detección de incidentes (golpes a pantallas)
en la camara enfocada.

Estado general (sin incidentes / golpe detectado, siempre con texto), cuantos
incidentes hubo, el ultimo (hace cuanto, en que pantalla y si fue patada o
golpe con la mano), el estado de cada pantalla y la tendencia de alertas de
los ultimos minutos."""

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
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    MetricHistory,
    Sparkline,
    format_elapsed,
)

MOTIVE_LABELS = {"patada": "patada", "golpe_mano": "golpe con la mano"}


def motive_label(motive: str | None) -> str:
    return MOTIVE_LABELS.get(motive or "", "golpe")


class MonitorTamperPanel(AnalyticsPanelBase):
    analyzer_name = "monitor_tamper"
    panel_title = "Detección de incidentes"

    def build(self, content: QWidget) -> None:
        self._history = MetricHistory(bucket_s=10, buckets=60, mode="max")
        self._last: dict | None = None
        self._zones: list[dict] = []

        self.state_pill = StatusPill(content, font_px=18)
        self.body.addWidget(self.state_pill, alignment=Qt.AlignmentFlag.AlignLeft)
        self.last_label = caption("", content)
        self.body.addWidget(self.last_label)

        count_row = QHBoxLayout()
        column = QVBoxLayout()
        column.setSpacing(0)
        self.incidents_label = big_number(content, 30)
        column.addWidget(self.incidents_label)
        column.addWidget(caption("incidentes desde que arrancó la analítica", content))
        count_row.addLayout(column)
        count_row.addStretch(1)
        self.body.addLayout(count_row)

        self.body.addWidget(caption("Pantallas", content, TEXT_MUTED))
        self.zones_label = caption("", content)
        self.zones_label.setTextFormat(Qt.TextFormat.RichText)
        self.body.addWidget(self.zones_label)
        self.contact_label = caption("", content, TEXT_MUTED)
        self.body.addWidget(self.contact_label)

        self.body.addWidget(caption("Alertas · últimos 10 min", content))
        self.trend = Sparkline(ANALYTIC_ACCENTS["monitor_tamper"], " alerta", content)
        self.body.addWidget(self.trend)

    def reset(self) -> None:
        self._history.clear()
        self._last = None
        self._zones = []
        self.state_pill.set_status("Sin lectura", STATUS_OK)
        self.last_label.setText("")
        self.incidents_label.setText("—")
        self.zones_label.setText("")
        self.contact_label.setText("")
        self.trend.set_series([])

    def on_event(self, event: DetectionEvent) -> None:
        metrics = event.metrics
        if "estado" not in metrics:
            return
        alert = metrics["estado"] == "alerta"
        self._zones = list(metrics.get("zonas") or [])
        self._last = metrics.get("ultimo_golpe")
        self.state_pill.set_status(
            "INCIDENTE DETECTADO" if alert else "Sin incidentes",
            STATUS_CRITICAL if alert else STATUS_OK,
        )
        self.incidents_label.setText(str(metrics.get("incidentes", 0)))
        self.contact_label.setText(
            "Manos sobre la pantalla (uso normal)" if metrics.get("contacto") else ""
        )
        self._history.add(1.0 if alert else 0.0, event.timestamp)
        self._render_zones()

    def _render_zones(self) -> None:
        lines = []
        for index, zone in enumerate(self._zones, start=1):
            alert = zone.get("estado") == "alerta"
            color = STATUS_CRITICAL if alert else STATUS_OK
            state = f"ALERTA · {motive_label(zone.get('motivo'))}" if alert else "Normal"
            lines.append(
                f"<span style='color:{color}'>●</span>&nbsp;"
                f"<span style='color:{TEXT_PRIMARY}'>Pantalla {index}</span>"
                f"<span style='color:{TEXT_SECONDARY}'> · {state}"
                f" · {zone.get('incidentes', 0)} incidentes</span>"
            )
        self.zones_label.setText("<br>".join(lines))

    def on_tick(self, now: float) -> None:
        self.trend.set_series(self._history.series(now))
        if self._last:
            self.last_label.setText(
                f"Último: {motive_label(self._last.get('motivo'))} en la pantalla "
                f"{int(self._last.get('zona', 0)) + 1} · hace {format_elapsed(now - self._last['t'])}"
            )
        else:
            self.last_label.setText("Todavía no hubo golpes" if self._zones else "")
