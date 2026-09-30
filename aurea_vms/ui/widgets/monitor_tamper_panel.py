"""Panel de la Vista Inteligente: Detección de incidentes en la camara
enfocada, en sus dos modos: golpes a pantallas y consumo de sustancias.

Estado general (sin incidentes / alerta previa / incidente, siempre con
texto), cuantos incidentes y alertas previas hubo, el ultimo (hace cuanto,
en que pantalla o puesto y por que), el estado de cada zona y la tendencia
de alertas de los ultimos minutos."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from aurea_vms.core.events import DetectionEvent
from aurea_vms.ui.labels import display_rotor_event
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
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    MetricHistory,
    Sparkline,
    format_elapsed,
)

# Ruleta: manos en una zona de fichas (mismo celeste que en el video).
ROULETTE_ACTIVITY = "#38bdf8"

MOTIVE_LABELS = {
    "fichas": "movimiento de fichas",
    "patada": "patada",
    "golpe_mano": "golpe con la mano",
    "consumo": "consumo (mano a la nariz o boca)",
    "preparacion": "preparación",
    "no_va_mas": "fichas tras el no va más",
}
# Cuanto se muestra "Nueva jugada" despues de que la rueda la abrio.
NEW_GAME_S = 4.0
# Ronda de la ruleta en la pastilla de estado (texto, color).
ROUND_PILL = {
    "apuestas": ("Apuestas abiertas", STATUS_OK),
    "bola": ("Bola en juego", ROULETTE_ACTIVITY),
    "no_va_mas": ("NO VA MÁS · paño armado", STATUS_WARNING),
    "resultado": ("Resultado · esperando la marca", STATUS_WARNING),
}


def motive_label(motive: str | None) -> str:
    return MOTIVE_LABELS.get(motive or "", "golpe")


class MonitorTamperPanel(AnalyticsPanelBase):
    analyzer_name = "monitor_tamper"
    panel_title = "Incidentes en casinos"

    def build(self, content: QWidget) -> None:
        self._history = MetricHistory(bucket_s=10, buckets=60, mode="max")
        self._last: dict | None = None
        self._zones: list[dict] = []
        self._noun = "Pantalla"

        self.state_pill = StatusPill(content, font_px=18)
        self.body.addWidget(self.state_pill, alignment=Qt.AlignmentFlag.AlignLeft)
        self.last_label = caption("", content)
        self.body.addWidget(self.last_label)

        count_row = QHBoxLayout()
        column = QVBoxLayout()
        column.setSpacing(0)
        self.incidents_label = big_number(content, 30)
        column.addWidget(self.incidents_label)
        self.incidents_caption = caption("incidentes desde que arrancó la analítica", content)
        column.addWidget(self.incidents_caption)
        count_row.addLayout(column)
        pre_column = QVBoxLayout()
        pre_column.setSpacing(0)
        self.pre_alerts_label = big_number(content, 30)
        pre_column.addWidget(self.pre_alerts_label)
        self.pre_alerts_caption = caption("alertas previas", content)
        pre_column.addWidget(self.pre_alerts_caption)
        count_row.addSpacing(24)
        count_row.addLayout(pre_column)
        count_row.addStretch(1)
        self.body.addLayout(count_row)

        self.zones_title = caption("Pantallas", content, TEXT_MUTED)
        self.body.addWidget(self.zones_title)
        self.zones_label = caption("", content)
        self.zones_label.setTextFormat(Qt.TextFormat.RichText)
        self.body.addWidget(self.zones_label)
        self.contact_label = caption("", content, TEXT_MUTED)
        self.body.addWidget(self.contact_label)

        self.trend_title = caption("Alertas · últimos 10 min", content)
        self.body.addWidget(self.trend_title)
        self.trend = Sparkline(ANALYTIC_ACCENTS["monitor_tamper"], " alerta", content)
        self.body.addWidget(self.trend)
        # Ruleta: la velocidad de la rueda en lugar de las alertas.
        self._speed_history = MetricHistory(bucket_s=10, buckets=60, mode="max")
        self.speed_title = caption("Velocidad de la rueda · últimos 10 min", content)
        self.body.addWidget(self.speed_title)
        self.speed_trend = Sparkline(ANALYTIC_ACCENTS["monitor_tamper"], " °/s", content)
        self.body.addWidget(self.speed_trend)
        self._roulette = False
        self._show_roulette(False)

    def reset(self) -> None:
        self._history.clear()
        self._speed_history.clear()
        self.speed_trend.set_series([])
        self._show_roulette(False)
        self._last = None
        self._zones = []
        self.state_pill.set_status("Sin lectura", STATUS_OK)
        self.last_label.setText("")
        self.incidents_label.setText("—")
        self.pre_alerts_label.setText("—")
        self._set_pre_alerts_visible(False)
        self.zones_label.setText("")
        self.contact_label.setText("")
        self.trend.set_series([])

    def on_event(self, event: DetectionEvent) -> None:
        metrics = event.metrics
        if "estado" not in metrics:
            return
        if metrics.get("modo") == "ruleta":
            self._on_roulette(metrics, event.timestamp)
            return
        self._show_roulette(False)
        self.incidents_caption.setText("incidentes desde que arrancó la analítica")
        status = metrics["estado"]
        consumption = metrics.get("modo") == "consumo"
        self._noun = "Puesto" if metrics.get("zona_tipo") == "puesto" else "Pantalla"
        self.zones_title.setText(f"{self._noun}s")
        self._zones = list(metrics.get("zonas") or [])
        self._last = metrics.get("ultimo_incidente") or metrics.get("ultimo_golpe")
        if status == "alerta":
            self.state_pill.set_status("INCIDENTE DETECTADO", STATUS_CRITICAL)
        elif status == "previa":
            self.state_pill.set_status("ALERTA PREVIA · preparación", STATUS_WARNING)
        else:
            self.state_pill.set_status("Sin incidentes", STATUS_OK)
        self.incidents_label.setText(str(metrics.get("incidentes", 0)))
        self._set_pre_alerts_visible(consumption)
        self.pre_alerts_label.setText(str(metrics.get("previas", 0)))
        self.contact_label.setText(
            "Manos sobre la pantalla (uso normal)" if metrics.get("contacto") else ""
        )
        # En la tendencia el incidente vale 1 y la alerta previa 0,5.
        weight = {"alerta": 1.0, "previa": 0.5}.get(status, 0.0)
        self._history.add(weight, event.timestamp)
        self._render_zones()

    def _show_roulette(self, roulette: bool) -> None:
        self._roulette = roulette
        self.trend_title.setVisible(not roulette)
        self.trend.setVisible(not roulette)
        self.speed_title.setVisible(roulette)
        self.speed_trend.setVisible(roulette)

    def _on_roulette(self, metrics: dict, timestamp: float) -> None:
        self._show_roulette(True)
        ronda = metrics.get("ronda") or {}
        speed = metrics.get("velocidad_deg_s")
        if metrics.get("estado") == "alerta":
            self.state_pill.set_status("ALERTA · fichas tras el no va más", STATUS_CRITICAL)
        elif ronda.get("estado") in ROUND_PILL:
            text, color = ROUND_PILL[ronda["estado"]]
            game = ronda.get("ultima_jugada")
            if ronda.get("bola_deg_s") and ronda["estado"] in ("bola", "no_va_mas"):
                text = f"{text} · bola {ronda['bola_deg_s']:.0f} °/s"
            elif ronda["estado"] == "apuestas" and game and timestamp - game["t"] <= NEW_GAME_S:
                what = display_rotor_event(game.get("tipo"))
                text, color = f"Nueva jugada · la rueda {what}", ROULETTE_ACTIVITY
            self.state_pill.set_status(text, color)
        else:
            self.state_pill.set_status("Buscando la rueda", STATUS_WARNING)
        if speed is not None:
            self._speed_history.add(float(speed), timestamp)
        # El primer numero son las alertas (fichas tras el no va mas); el
        # segundo, todos los movimientos de fichas.
        self.incidents_label.setText(str(metrics.get("incidentes", 0)))
        self.incidents_caption.setText("fichas tras el no va más")
        self._set_pre_alerts_visible(True)
        self.pre_alerts_label.setText(str(metrics.get("fichas_movidas", 0)))
        self.pre_alerts_caption.setText("movimientos de fichas")
        self.zones_title.setText("Zonas de fichas")
        wheel = "Rueda buscándose"
        if speed is not None:
            arrow = "↻" if metrics.get("sentido") == "horario" else "↺"
            wheel = f"Rueda {arrow} {speed:.0f} °/s · {metrics.get('rpm', 0):.1f} rpm"
        felt = "paño armado" if ronda.get("pano_armado") else "paño libre"
        if ronda.get("jugadas"):
            felt = f"{felt} · {ronda['jugadas']} jugadas"
        hands = "manos en el paño" if metrics.get("contacto") else "sin manos"
        self.contact_label.setText(f"{wheel} · {felt} · {hands}")
        incident = metrics.get("ultimo_incidente")
        last = metrics.get("ultimo_movimiento")
        if incident:
            self._last = incident
        else:
            self._last = (
                {"t": last["t"], "zona": last["zona"], "motivo": "fichas"} if last else None
            )
        lines = []
        for index, zone in enumerate(metrics.get("zonas") or [], start=1):
            active = zone.get("estado") == "actividad"
            color = ROULETTE_ACTIVITY if active else STATUS_OK
            state = "manos" if active else "quieta"
            if zone.get("estado") == "alerta":
                color, state = STATUS_CRITICAL, "ALERTA · fichas tras el no va más"
            lines.append(
                f"<span style='color:{color}'>●</span>&nbsp;"
                f"<span style='color:{TEXT_PRIMARY}'>Zona {index}</span>"
                f"<span style='color:{TEXT_SECONDARY}'> · {state}"
                f" · {zone.get('cambios', 0)} movimientos</span>"
            )
        self.zones_label.setText("<br>".join(lines))
        self._noun = "Zona"

    def _set_pre_alerts_visible(self, visible: bool) -> None:
        self.pre_alerts_label.setVisible(visible)
        self.pre_alerts_caption.setVisible(visible)

    def _render_zones(self) -> None:
        lines = []
        for index, zone in enumerate(self._zones, start=1):
            status = zone.get("estado")
            if status == "alerta":
                color, state = STATUS_CRITICAL, f"ALERTA · {motive_label(zone.get('motivo'))}"
            elif status == "previa":
                color, state = STATUS_WARNING, "ALERTA PREVIA · preparación"
            else:
                color, state = STATUS_OK, "Normal"
            lines.append(
                f"<span style='color:{color}'>●</span>&nbsp;"
                f"<span style='color:{TEXT_PRIMARY}'>{self._noun} {index}</span>"
                f"<span style='color:{TEXT_SECONDARY}'> · {state}"
                f" · {zone.get('incidentes', 0)} incidentes</span>"
            )
        self.zones_label.setText("<br>".join(lines))

    def on_tick(self, now: float) -> None:
        self.trend.set_series(self._history.series(now))
        if self._roulette:
            self.speed_trend.set_series(self._speed_history.series(now))
        if self._last:
            self.last_label.setText(
                f"Último: {motive_label(self._last.get('motivo'))} en {self._noun.lower()} "
                f"{int(self._last.get('zona', 0)) + 1} · hace {format_elapsed(now - self._last['t'])}"
            )
        else:
            self.last_label.setText("Todavía no hubo incidentes" if self._zones else "")
