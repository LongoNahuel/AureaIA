"""Concentrador de metricas de TODAS las camaras para el dashboard analitico.

Los paneles laterales muestran una sola camara y solo mientras estan
visibles. El dashboard necesita lo contrario: todas las camaras, separadas
por analitica, con la ultima hora de historia aunque nadie haya estado
mirando. Este objeto escucha el bus desde que arranca la app y acumula, por
camara:

- Conteo de Personas: ocupacion (pico por minuto), aforo y si esta excedido;
- Cruce de Linea: entradas y salidas por minuto (derivadas de los
  acumulados del analizador, tolerando que se reinicie);
- Detección de incidentes: por pantalla, los tramos alerta/normal de la ultima hora,
  los incidentes (patadas y golpes) y su historial;
- Deteccion Facial: caras en cuadro (pico por minuto) y tomas nuevas.

Vive en el hilo de la GUI (el bus entrega con QueuedConnection).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Qt

from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent
from aurea_vms.ui.widgets.analytics_visuals import MetricHistory

WINDOW_S = 3600.0
BUCKET_S = 60.0
BUCKETS = int(WINDOW_S / BUCKET_S)


def _history(mode: str) -> MetricHistory:
    return MetricHistory(bucket_s=BUCKET_S, buckets=BUCKETS, mode=mode)


@dataclass
class PeopleState:
    occupancy: int = 0
    peak: int = 0
    max_people: int = 0
    over: bool = False
    updated: float = 0.0
    history: MetricHistory = field(default_factory=lambda: _history("max"))


@dataclass
class CrossingState:
    count_in: int = 0
    count_out: int = 0
    session_in: int = 0  # acumulado del hub (sobrevive a reinicios del analizador)
    session_out: int = 0
    last_crossing: float = 0.0
    updated: float = 0.0
    history_in: MetricHistory = field(default_factory=lambda: _history("sum"))
    history_out: MetricHistory = field(default_factory=lambda: _history("sum"))


@dataclass
class ScreenState:
    state: str = ""
    # (inicio, fin, estado); el ultimo tramo queda abierto (fin = None).
    segments: deque = field(default_factory=lambda: deque(maxlen=400))


@dataclass
class MonitorState:
    screens: dict[int, ScreenState] = field(default_factory=dict)
    incidents: int = 0
    in_alert: bool = False
    updated: float = 0.0
    # (momento, pantalla, motivo) de cada incidente, el mas reciente al final.
    events: deque = field(default_factory=lambda: deque(maxlen=50))


@dataclass
class FaceState:
    in_frame: int = 0
    shots: int = 0
    updated: float = 0.0
    history_faces: MetricHistory = field(default_factory=lambda: _history("max"))
    history_shots: MetricHistory = field(default_factory=lambda: _history("sum"))


class AnalyticsHub(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.people: dict[int, PeopleState] = {}
        self.crossing: dict[int, CrossingState] = {}
        self.monitors: dict[int, MonitorState] = {}
        self.faces: dict[int, FaceState] = {}
        self._connected = False

    def connect_bus(self) -> None:
        if self._connected:
            return
        self._connected = True
        event_bus.detection.connect(self.process, Qt.ConnectionType.QueuedConnection)

    def reset(self) -> None:
        self.people.clear()
        self.crossing.clear()
        self.monitors.clear()
        self.faces.clear()

    def process(self, event: DetectionEvent) -> None:
        handler = {
            "people_counting": self._people,
            "line_crossing": self._crossing,
            "monitor_tamper": self._monitor,
            "face_detection": self._face,
        }.get(event.analyzer_name)
        if handler is not None:
            handler(event)

    # --- por analitica ------------------------------------------------------

    def _people(self, event: DetectionEvent) -> None:
        metrics = event.metrics
        if "occupancy" not in metrics:
            return
        state = self.people.setdefault(event.device_id, PeopleState())
        state.occupancy = int(metrics["occupancy"])
        state.peak = max(state.peak, int(metrics.get("peak", state.occupancy)))
        state.max_people = int(metrics.get("max_people", 0) or 0)
        state.over = bool(metrics.get("alerta_maxima"))
        state.updated = event.timestamp
        state.history.add(state.occupancy, event.timestamp)

    def _crossing(self, event: DetectionEvent) -> None:
        metrics = event.metrics
        if "count_in" not in metrics:
            return
        state = self.crossing.setdefault(event.device_id, CrossingState())
        count_in = int(metrics.get("count_in", 0))
        count_out = int(metrics.get("count_out", 0))
        # Si el analizador se reinicio, sus acumulados vuelven a 0: el delta
        # se toma desde cero en vez de dar negativo.
        delta_in = count_in - state.count_in if count_in >= state.count_in else count_in
        delta_out = count_out - state.count_out if count_out >= state.count_out else count_out
        if state.updated == 0.0:
            delta_in = delta_out = 0  # primera lectura: no es "lo que paso recien"
            state.session_in, state.session_out = count_in, count_out
        state.count_in, state.count_out = count_in, count_out
        state.session_in += delta_in
        state.session_out += delta_out
        if delta_in or delta_out:
            state.last_crossing = event.timestamp
        state.updated = event.timestamp
        state.history_in.add(delta_in, event.timestamp)
        state.history_out.add(delta_out, event.timestamp)

    def _monitor(self, event: DetectionEvent) -> None:
        metrics = event.metrics
        zones = metrics.get("zonas")
        if zones is None:
            return
        state = self.monitors.setdefault(event.device_id, MonitorState())
        now = event.timestamp
        for index, zone in enumerate(zones):
            screen = state.screens.setdefault(index, ScreenState())
            current = str(zone.get("estado", "normal"))
            if current != screen.state:
                if screen.segments:
                    start, _, previous = screen.segments[-1]
                    screen.segments[-1] = (start, now, previous)
                screen.segments.append((now, None, current))
                screen.state = current
        incidents = int(metrics.get("incidentes", state.incidents))
        last = metrics.get("ultimo_golpe")
        if incidents > state.incidents and last:
            state.events.append((float(last["t"]), int(last.get("zona", 0)), last.get("motivo")))
        state.incidents = incidents
        state.in_alert = metrics.get("estado") == "alerta"
        state.updated = now

    def _face(self, event: DetectionEvent) -> None:
        state = self.faces.setdefault(event.device_id, FaceState())
        state.in_frame = int(event.metrics.get("caras", len(event.detections)))
        shots = len(event.metrics.get("face_shots") or ())
        state.shots += shots
        state.updated = event.timestamp
        state.history_faces.add(state.in_frame, event.timestamp)
        state.history_shots.add(shots, event.timestamp)

    # --- agregados para el dashboard ------------------------------------------

    @staticmethod
    def merge(
        histories: list[MetricHistory], now: float | None = None
    ) -> list[tuple[float, float]]:
        """Suma varias series (una por camara) intervalo a intervalo sobre
        la ventana completa de la ultima hora."""
        now = time.time() if now is None else now
        last = int(now // BUCKET_S)
        keys = list(range(last - BUCKETS + 1, last + 1))
        totals = dict.fromkeys(keys, 0.0)
        for history in histories:
            for start, value in history.series(now):
                key = int(start // BUCKET_S)
                if key in totals:
                    totals[key] += value
        return [(key * BUCKET_S, totals[key]) for key in keys]

    @staticmethod
    def screen_segments(screen: ScreenState, now: float) -> list[tuple[float, float, str]]:
        start_window = now - WINDOW_S
        result = []
        for start, end, value in screen.segments:
            end = now if end is None else end
            if end < start_window:
                continue
            result.append((max(start, start_window), end, value))
        return result


analytics_hub = AnalyticsHub()
