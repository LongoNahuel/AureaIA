"""Panel de la Vista Inteligente: entradas/salidas de Cruce de Linea de la
camara enfocada, el balance (cuantos quedaron del lado de adentro), el
ultimo cruce y el ritmo de cruces por minuto."""

from __future__ import annotations

import time

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from aurea_vms.core.events import DetectionEvent
from aurea_vms.ui.widgets.analytics_panel_base import AnalyticsPanelBase, big_number, caption
from aurea_vms.ui.widgets.analytics_visuals import (
    ANALYTIC_ACCENTS,
    TEXT_MUTED,
    MetricHistory,
    Sparkline,
    format_elapsed,
)


class LineCrossingPanel(AnalyticsPanelBase):
    analyzer_name = "line_crossing"
    panel_title = "Cruce de Línea"

    def build(self, content: QWidget) -> None:
        self._history = MetricHistory(bucket_s=60, buckets=30, mode="sum")
        self._label_in = "Entrada"
        self._label_out = "Salida"
        self._last_total: int | None = None
        self._last_crossing_at = 0.0
        self._last_crossing_label = ""

        numbers_row = QHBoxLayout()
        self.in_label, self.in_caption = self._build_counter(content, numbers_row)
        self.out_label, self.out_caption = self._build_counter(content, numbers_row)
        self.total_label, self.total_caption = self._build_counter(content, numbers_row)
        self.in_caption.setText(self._label_in)
        self.out_caption.setText(self._label_out)
        self.total_caption.setText("Total")
        self.body.addLayout(numbers_row)

        self.balance_label = caption("", content)
        self.body.addWidget(self.balance_label)
        self.last_label = caption("", content)
        self.body.addWidget(self.last_label)

        self.body.addWidget(caption("Cruces por minuto · últimos 30 min", content))
        self.trend = Sparkline(ANALYTIC_ACCENTS["line_crossing"], " cruces", content)
        self.body.addWidget(self.trend)
        self.body.addWidget(
            caption("Los contadores arrancan de cero al iniciar la analítica.", content, TEXT_MUTED)
        )

    @staticmethod
    def _build_counter(parent: QWidget, row: QHBoxLayout):
        column = QVBoxLayout()
        column.setSpacing(0)
        number_label = big_number(parent, 30)
        caption_label = caption("", parent)
        column.addWidget(number_label)
        column.addWidget(caption_label)
        row.addLayout(column)
        return number_label, caption_label

    def apply_config(self, config) -> None:
        params = (config.params if config is not None else None) or {}
        self._label_in = params.get("label_in") or "Entrada"
        self._label_out = params.get("label_out") or "Salida"
        self.in_caption.setText(self._label_in)
        self.out_caption.setText(self._label_out)

    def reset(self) -> None:
        self._history.clear()
        self._last_total = None
        self._last_crossing_at = 0.0
        for label in (self.in_label, self.out_label, self.total_label):
            label.setText("—")
        self.balance_label.setText("")
        self.last_label.setText("Todavía no hubo cruces")
        self.trend.set_series([])

    def on_event(self, event: DetectionEvent) -> None:
        metrics = event.metrics
        if "count_in" not in metrics:
            return
        incoming = int(metrics.get("count_in", 0))
        outgoing = int(metrics.get("count_out", 0))
        total = int(metrics.get("total", incoming + outgoing))
        directional = metrics.get("direction_enabled", True)

        if self._last_total is not None and total > self._last_total:
            self._history.add(total - self._last_total, event.timestamp)
            self._last_crossing_at = time.time()
            self._last_crossing_label = str(metrics.get("last_crossing") or "Cruce")
        elif self._last_total is None or total < self._last_total:
            # Primera lectura o analizador reiniciado: arranca la serie en 0.
            self._history.add(0, event.timestamp)
        self._last_total = total

        self.in_label.setText(str(incoming))
        self.out_label.setText(str(outgoing))
        self.total_label.setText(str(total))
        for widget in (self.in_label, self.in_caption, self.out_label, self.out_caption):
            widget.setVisible(directional)
        if directional:
            balance = incoming - outgoing
            self.balance_label.setText(
                f"Balance {balance:+d} · {self._label_in.lower()} menos {self._label_out.lower()}"
            )
        else:
            self.balance_label.setText("Sin sentido: se cuentan los cruces en ambas direcciones")

    def on_tick(self, now: float) -> None:
        self.trend.set_series(self._history.series(now))
        if self._last_crossing_at:
            self.last_label.setText(
                f"Último cruce: {self._last_crossing_label} · "
                f"hace {format_elapsed(now - self._last_crossing_at)}"
            )
