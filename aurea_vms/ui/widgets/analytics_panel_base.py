"""Base de los paneles de analiticas de la Vista Inteligente.

Resuelve lo que antes repetia (o le faltaba a) cada panel: filtrar los
DetectionEvent de la camara enfocada, decir si los datos estan frescos
("Actualizado hace 2 s" / "Sin datos del analizador") y mostrar a cuantos
fps y con que latencia corre de verdad el analizador -- el primer dato que
hace falta para diagnosticar un conteo raro en una PC con poco CPU.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, HeaderCardWidget

from aurea_vms.core.analytics_engine import analytics_engine
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent
from aurea_vms.models import repository
from aurea_vms.ui.theme import enable_tabular_numbers
from aurea_vms.ui.widgets.analytics_visuals import (
    STALE_AFTER_S,
    STATUS_WARNING,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    format_elapsed,
)

TICK_MS = 1000


def big_number(parent: QWidget, size_px: int = 40) -> QLabel:
    label = QLabel("—", parent)
    label.setStyleSheet(
        f"font-size: {size_px}px; font-weight: 700; color: {TEXT_PRIMARY}; background: transparent;"
    )
    enable_tabular_numbers(label)
    return label


def caption(text: str, parent: QWidget, color: str = TEXT_SECONDARY) -> CaptionLabel:
    label = CaptionLabel(text, parent)
    label.setStyleSheet(f"color: {color}; background: transparent;")
    label.setWordWrap(True)
    return label


class StatusPill(QLabel):
    """Estado = punto de color + texto (nunca el color solo)."""

    def __init__(self, parent: QWidget | None = None, font_px: int = 13) -> None:
        super().__init__(parent)
        self._font_px = font_px
        self.set_status("—", TEXT_MUTED)

    def set_status(self, text: str, color: str) -> None:
        self.setText(f"<span style='color:{color}'>●</span>&nbsp;{text}")
        self.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-weight: 600; font-size: {self._font_px}px;"
            "background: rgba(255,255,255,0.06); border-radius: 9px; padding: 2px 9px;"
        )


class AnalyticsPanelBase(HeaderCardWidget):
    analyzer_name = ""
    panel_title = ""
    # True si el panel tiene un widget que debe ocupar el alto sobrante
    # (ej. la grilla de rostros): entonces la base no agrega su espaciador.
    fills_height = False

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(self.panel_title)
        self._device_id: int | None = None
        self._config_id: int | None = None
        self._last_event_at = 0.0

        content = QWidget(self)
        content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.viewLayout.addWidget(content)
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        self.build(content)

        footer = QHBoxLayout()
        self.freshness_label = caption("Sin datos del analizador", content, TEXT_MUTED)
        footer.addWidget(self.freshness_label, stretch=1)
        if not self.fills_height:
            self.body.addStretch(1)
        self.body.addLayout(footer)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

        event_bus.detection.connect(self._on_detection_event, Qt.ConnectionType.QueuedConnection)

    # --- a implementar por cada panel ------------------------------------

    def build(self, content: QWidget) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        """Vuelve el panel a "sin datos" (cambio de camara)."""

    def on_event(self, event: DetectionEvent) -> None:
        raise NotImplementedError

    def on_tick(self, now: float) -> None:
        """Refresco periodico (tiempos relativos, tendencias)."""

    # --- comun ------------------------------------------------------------

    def set_device(self, device_id: int | None) -> None:
        self._device_id = device_id
        self._last_event_at = 0.0
        config = (
            repository.get_analytics_config_for(device_id, self.analyzer_name)
            if device_id is not None
            else None
        )
        self._config_id = config.id if config is not None else None
        self.apply_config(config)
        self.reset()
        self._tick()

    def apply_config(self, config) -> None:
        """Hook: parametros de la config que el panel necesita mostrar
        (ej. las etiquetas de Entrada/Salida de Cruce de Linea)."""

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._timer.start()
        self._tick()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._timer.stop()
        super().hideEvent(event)

    def _on_detection_event(self, event: DetectionEvent) -> None:
        if event.device_id != self._device_id or event.analyzer_name != self.analyzer_name:
            return
        self._last_event_at = time.time()
        self.on_event(event)

    def _tick(self) -> None:
        now = time.time()
        self._update_freshness(now)
        self.on_tick(now)

    def _update_freshness(self, now: float) -> None:
        if self._device_id is None:
            self.freshness_label.setText("")
            return
        if not self._last_event_at:
            self.freshness_label.setText("Esperando al analizador…")
            self.freshness_label.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent;")
            return
        age = now - self._last_event_at
        stats = analytics_engine.stats(self._config_id) if self._config_id is not None else None
        perf = ""
        if stats is not None and stats.frames > 1:
            perf = f" · {stats.fps:.1f} fps · {stats.latency_ms:.0f} ms"
            if stats.errors:
                perf += f" · {stats.errors} errores"
        if age > STALE_AFTER_S:
            self.freshness_label.setText(f"Sin lecturas hace {format_elapsed(age)}")
            self.freshness_label.setStyleSheet(f"color: {STATUS_WARNING}; background: transparent;")
        else:
            self.freshness_label.setText(f"En vivo{perf}")
            self.freshness_label.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent;")
