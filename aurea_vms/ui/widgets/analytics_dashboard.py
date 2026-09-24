"""Dashboard analitico de la Vista Inteligente: una seccion por analitica,
separadas y con su propio grafico, para TODAS las camaras del sitio.

- Personas: ocupacion total de la ultima hora + barras por camara contra
  su aforo (en rojo, con la palabra "excedido", la que lo supera).
- Cruce de linea: entradas y salidas cada 5 minutos + totales por camara.
- Incidentes: golpes a las pantallas (patadas y golpes con la
  mano) y la linea de tiempo alerta/normal de cada pantalla en la ultima hora.
- Rostros: grilla con la mejor captura de cada rostro que se vio bien, de
  todas las camaras (clic = visor forense). Sin identidad ni conteo de
  personas unicas.

Los datos los junta ui/analytics_hub.py desde que arranca la app, asi que
el dashboard ya tiene historia al abrirse. Se refresca cada 2 s solo
mientras esta visible.
"""

from __future__ import annotations

import datetime as dt
import time

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import FluentIcon, PushButton

from aurea_vms.core import app_state
from aurea_vms.models import repository
from aurea_vms.ui.analytics_hub import WINDOW_S, analytics_hub
from aurea_vms.ui.face_registry import face_registry
from aurea_vms.ui.theme import enable_tabular_numbers
from aurea_vms.ui.widgets.analytics_visuals import (
    ANALYTIC_ACCENTS,
    STATUS_CRITICAL,
    STATUS_OK,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    format_elapsed,
)
from aurea_vms.ui.widgets.charts import (
    NEUTRAL_STATE,
    SERIES_BLUE,
    SERIES_ORANGE,
    CameraBars,
    Series,
    StateTimeline,
    TimeSeriesChart,
)
from aurea_vms.ui.widgets.face_forensics import open_forensics
from aurea_vms.ui.widgets.face_visuals import quality_label, record_pixmap
from aurea_vms.ui.widgets.monitor_tamper_panel import motive_label

REFRESH_MS = 2000
# Incidentes (golpes a pantallas) listados bajo la linea de tiempo.
MAX_INCIDENT_LINES = 6
# Capturas en la grilla del dashboard (las mas recientes) y su tamaño.
MAX_DASHBOARD_CAPTURES = 48
CAPTURE_THUMB = QSize(112, 112)


class _Kpi(QWidget):
    """Cifra grande + rotulo. `set_status` agrega un punto de estado con
    texto (nunca color solo)."""

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.value = QLabel("—", self)
        self.value.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 26px; font-weight: 700; background: transparent;"
        )
        enable_tabular_numbers(self.value)
        self.caption = QLabel(caption, self)
        self.caption.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11px; background: transparent;"
        )
        layout.addWidget(self.value)
        layout.addWidget(self.caption)

    def set(self, value: str, status: tuple[str, str] | None = None) -> None:
        if status is None:
            self.value.setText(value)
            return
        color, _ = status
        self.value.setText(f"<span style='color:{color}; font-size:14px'>●</span> {value}")


class _Section(QFrame):
    """Tarjeta de una analitica: franja de color de identidad, titulo,
    fila de indicadores y cuerpo (grafico + desglose)."""

    def __init__(self, title: str, subtitle: str, accent: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dashSection")
        self.setStyleSheet(
            "#dashSection { background: rgba(10, 18, 30, 225);"
            " border: 1px solid rgba(86, 171, 224, 45);"
            f" border-top: 3px solid {accent}; border-radius: 12px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        head = QHBoxLayout()
        dot = QLabel("●", self)
        dot.setStyleSheet(f"color: {accent}; font-size: 14px; background: transparent;")
        head.addWidget(dot)
        name = QLabel(title, self)
        name.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 15px; font-weight: 700; background: transparent;"
        )
        head.addWidget(name)
        head.addStretch(1)
        self.subtitle = QLabel(subtitle, self)
        self.subtitle.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;"
        )
        head.addWidget(self.subtitle)
        self.head = head
        layout.addLayout(head)
        self.kpis = QHBoxLayout()
        self.kpis.setSpacing(22)
        layout.addLayout(self.kpis)
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        layout.addLayout(self.body, stretch=1)

    def add_action(self, widget: QWidget) -> None:
        """Una accion (boton) a la derecha del encabezado de la seccion."""
        self.head.addSpacing(8)
        self.head.addWidget(widget)

    def add_kpi(self, caption: str) -> _Kpi:
        kpi = _Kpi(caption, self)
        self.kpis.addWidget(kpi)
        return kpi

    def finish_kpis(self) -> None:
        self.kpis.addStretch(1)

    def label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
        return label


class AnalyticsDashboard(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        analytics_hub.connect_bus()
        face_registry.connect_bus()

        # --- Personas ---------------------------------------------------------
        self.people = _Section(
            "Personas", "Conteo en zona · última hora", ANALYTIC_ACCENTS["people_counting"]
        )
        self.people_now = self.people.add_kpi("en zona ahora")
        self.people_peak = self.people.add_kpi("pico de la sesión")
        self.people_over = self.people.add_kpi("cámaras con aforo excedido")
        self.people.finish_kpis()
        self.people_chart = TimeSeriesChart("area", " personas")
        self.people.body.addWidget(self.people_chart, stretch=1)
        self.people.body.addWidget(self.people.label("Por cámara"))
        self.people_bars = CameraBars()
        self.people.body.addWidget(self.people_bars)

        # --- Cruce de linea -----------------------------------------------------
        self.crossing = _Section(
            "Cruce de línea", "Entradas y salidas cada 5 min", ANALYTIC_ACCENTS["line_crossing"]
        )
        self.crossing_in = self.crossing.add_kpi("entradas")
        self.crossing_out = self.crossing.add_kpi("salidas")
        self.crossing_balance = self.crossing.add_kpi("balance")
        self.crossing_last = self.crossing.add_kpi("último cruce")
        self.crossing.finish_kpis()
        self.crossing_chart = TimeSeriesChart("bars", " cruces", group=5)
        self.crossing.body.addWidget(self.crossing_chart, stretch=1)
        self.crossing.body.addWidget(self.crossing.label("Cruces por cámara (sesión)"))
        self.crossing_bars = CameraBars()
        self.crossing.body.addWidget(self.crossing_bars)

        # --- Incidentes --------------------------------------------------------------
        self.monitors = _Section(
            "Detección de incidentes",
            "Golpes a las pantallas · última hora",
            ANALYTIC_ACCENTS["monitor_tamper"],
        )
        self.monitors_alert = self.monitors.add_kpi("pantallas en alerta ahora")
        self.monitors_incidents = self.monitors.add_kpi("incidentes")
        self.monitors_last = self.monitors.add_kpi("último golpe")
        self.monitors.finish_kpis()
        self.monitors_timeline = StateTimeline(
            states=(("normal", "Normal", NEUTRAL_STATE), ("alerta", "Golpe", STATUS_CRITICAL)),
            empty_text="Sin pantallas vigiladas",
        )
        self.monitors.body.addWidget(self.monitors_timeline)
        self.monitors.body.addWidget(self.monitors.label("Últimos incidentes"))
        self.monitors_detail = QLabel(self.monitors)
        self.monitors_detail.setTextFormat(Qt.TextFormat.RichText)
        self.monitors_detail.setStyleSheet("background: transparent;")
        self.monitors_detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.monitors.body.addWidget(self.monitors_detail, stretch=1)

        # --- Rostros ----------------------------------------------------------------
        # Las capturas son el contenido: una grilla grande con la mejor toma
        # de cada rostro que se vio bien, la mas reciente primero.
        self.faces = _Section(
            "Rostros",
            "Mejor captura de cada rostro visible · clic para ampliar",
            ANALYTIC_ACCENTS["face_detection"],
        )
        self.faces_clear = PushButton(FluentIcon.BROOM, "Limpiar", self.faces)
        self.faces_clear.setToolTip(
            "Vacía las capturas mostradas de todas las cámaras; las nuevas aparecen a medida "
            "que se detectan. No borra las capturas guardadas por las alarmas."
        )
        self.faces_clear.clicked.connect(self._clear_faces)
        self.faces.add_action(self.faces_clear)
        self.faces_total = self.faces.add_kpi("capturas")
        self.faces_now = self.faces.add_kpi("caras en cuadro ahora")
        self.faces_quality = self.faces.add_kpi("calidad media")
        self.faces.finish_kpis()
        self.faces_empty = self.faces.label(
            "Sin capturas todavía. Se guarda la mejor toma de cada rostro que se vea bien "
            "(de frente, nítido y con suficiente tamaño)."
        )
        self.faces.body.addWidget(self.faces_empty)
        self.faces_grid = QListWidget(self.faces)
        self.faces_grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.faces_grid.setFlow(QListWidget.Flow.LeftToRight)
        self.faces_grid.setWrapping(True)
        self.faces_grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.faces_grid.setMovement(QListWidget.Movement.Static)
        self.faces_grid.setUniformItemSizes(True)
        self.faces_grid.setIconSize(CAPTURE_THUMB)
        self.faces_grid.setGridSize(QSize(CAPTURE_THUMB.width() + 18, CAPTURE_THUMB.height() + 42))
        self.faces_grid.setMinimumHeight(2 * (CAPTURE_THUMB.height() + 42) + 8)
        self.faces_grid.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.faces_grid.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.faces_grid.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; outline: none;"
            f" color: {TEXT_SECONDARY}; font-size: 11px; }}"
            "QListWidget::item { border-radius: 10px; padding: 3px; }"
            "QListWidget::item:hover { background: rgba(255,255,255,0.06); }"
            "QScrollBar:vertical { width: 6px; background: transparent; margin: 0; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,0.16);"
            " border-radius: 3px; min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background: transparent; }"
        )
        self.faces_grid.itemClicked.connect(self._open_face)
        self.faces.body.addWidget(self.faces_grid, stretch=1)

        # Personas y Cruce arriba; Incidentes y Rostros a lo ancho (la linea de
        # tiempo y la grilla de capturas aprovechan todo el ancho).
        grid = QGridLayout()
        grid.setSpacing(12)
        grid.addWidget(self.people, 0, 0)
        grid.addWidget(self.crossing, 0, 1)
        grid.addWidget(self.monitors, 1, 0, 1, 2)
        grid.addWidget(self.faces, 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        inner = QWidget()
        inner.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        inner.setLayout(grid)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(inner)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._timer.stop()
        super().hideEvent(event)

    # --- refresco ----------------------------------------------------------------

    def _site_devices(self) -> dict[int, str]:
        return {
            device.id: device.name
            for device in repository.list_devices(site_id=app_state.current_site_id)
        }

    def refresh(self) -> None:
        now = time.time()
        devices = self._site_devices()
        self._refresh_people(devices, now)
        self._refresh_crossing(devices, now)
        self._refresh_monitors(devices, now)
        self._refresh_faces(devices, now)

    def _refresh_people(self, devices: dict[int, str], now: float) -> None:
        states = {d: s for d, s in analytics_hub.people.items() if d in devices}
        self.people_now.set(str(sum(s.occupancy for s in states.values())) if states else "—")
        self.people_peak.set(
            str(max((s.peak for s in states.values()), default=0)) if states else "—"
        )
        over = sum(1 for s in states.values() if s.over)
        self.people_over.set(
            str(over) if states else "—",
            (STATUS_CRITICAL, "") if over else ((STATUS_OK, "") if states else None),
        )
        self.people_chart.set_series(
            [
                Series(
                    "Personas",
                    ANALYTIC_ACCENTS["people_counting"],
                    analytics_hub.merge([s.history for s in states.values()], now),
                )
            ]
        )
        self.people_bars.set_rows(
            [
                (
                    devices[d] + (" · excedido" if s.over else ""),
                    s.occupancy,
                    s.max_people or None,
                    STATUS_CRITICAL if s.over else ANALYTIC_ACCENTS["people_counting"],
                )
                for d, s in sorted(states.items(), key=lambda item: -item[1].occupancy)
            ]
        )

    def _refresh_crossing(self, devices: dict[int, str], now: float) -> None:
        states = {d: s for d, s in analytics_hub.crossing.items() if d in devices}
        total_in = sum(s.session_in for s in states.values())
        total_out = sum(s.session_out for s in states.values())
        self.crossing_in.set(str(total_in) if states else "—")
        self.crossing_out.set(str(total_out) if states else "—")
        self.crossing_balance.set(f"{total_in - total_out:+d}" if states else "—")
        last = max((s.last_crossing for s in states.values()), default=0.0)
        self.crossing_last.set(f"hace {format_elapsed(now - last)}" if last else "—")
        self.crossing_last.value.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 18px; font-weight: 700; background: transparent;"
        )
        self.crossing_chart.set_series(
            [
                Series(
                    "Entradas",
                    SERIES_BLUE,
                    analytics_hub.merge([s.history_in for s in states.values()], now),
                ),
                Series(
                    "Salidas",
                    SERIES_ORANGE,
                    analytics_hub.merge([s.history_out for s in states.values()], now),
                ),
            ]
        )
        self.crossing_bars.set_rows(
            [
                (devices[d], s.session_in + s.session_out, None, ANALYTIC_ACCENTS["line_crossing"])
                for d, s in sorted(
                    states.items(), key=lambda item: -(item[1].session_in + item[1].session_out)
                )
            ]
        )

    def _refresh_monitors(self, devices: dict[int, str], now: float) -> None:
        states = {d: s for d, s in analytics_hub.monitors.items() if d in devices}
        in_alert = sum(
            1 for s in states.values() for screen in s.screens.values() if screen.state == "alerta"
        )
        self.monitors_alert.set(
            str(in_alert) if states else "—",
            ((STATUS_CRITICAL, "") if in_alert else (STATUS_OK, "")) if states else None,
        )
        self.monitors_incidents.set(
            str(sum(s.incidents for s in states.values())) if states else "—"
        )
        events = sorted(
            ((t, d, zone, motive) for d, s in states.items() for t, zone, motive in s.events),
            reverse=True,
        )
        self.monitors_last.set(f"hace {format_elapsed(now - events[0][0])}" if events else "—")
        self.monitors_last.value.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 18px; font-weight: 700; background: transparent;"
        )
        self.monitors_timeline.set_rows(
            [
                (
                    # la pantalla primero: si el nombre se corta, el numero queda
                    f"P{index + 1} · {devices[d]}",
                    analytics_hub.screen_segments(screen, now),
                )
                for d, s in sorted(states.items())
                for index, screen in sorted(s.screens.items())
            ],
            (now - WINDOW_S, now),
        )
        lines = []
        for t, d, zone, motive in events[:MAX_INCIDENT_LINES]:
            clock = dt.datetime.fromtimestamp(t).strftime("%H:%M:%S")
            lines.append(
                f"<span style='color:{STATUS_CRITICAL}'>●</span>&nbsp;"
                f"<span style='color:{TEXT_PRIMARY}; font-weight:600'>{clock}</span>"
                f"<span style='color:{TEXT_SECONDARY}'> · {devices[d]} · pantalla {zone + 1}"
                f" · {motive_label(motive)}</span>"
            )
        self.monitors_detail.setText(
            "<br>".join(lines)
            if lines
            else f"<span style='color:{TEXT_MUTED}'>Sin incidentes en esta sesión</span>"
        )

    def _refresh_faces(self, devices: dict[int, str], now: float) -> None:
        states = {d: s for d, s in analytics_hub.faces.items() if d in devices}
        records = sorted(
            (r for d in devices for r in face_registry.records(d)),
            key=lambda record: record.timestamp,
            reverse=True,
        )[:MAX_DASHBOARD_CAPTURES]
        qualities = [r.quality for r in records if r.quality is not None]
        self.faces_total.set(str(len(records)) if states or records else "—")
        self.faces_now.set(str(sum(s.in_frame for s in states.values())) if states else "—")
        self.faces_quality.set(f"{sum(qualities) / len(qualities):.0%}" if qualities else "—")
        self.faces_empty.setVisible(not records)
        self.faces_grid.setVisible(bool(records))
        # Se reconstruye solo si cambio algo (la grilla se refresca cada 2 s).
        signature = [id(r) for r in records]
        if signature == getattr(self, "_capture_signature", None):
            return
        self._capture_signature = signature
        self.faces_grid.clear()
        for record in records:
            camera = face_registry.camera_name(record.device_id)
            item = QListWidgetItem(
                record_pixmap(record, CAPTURE_THUMB, 12), f"{record.when}\n{camera[:18]}"
            )
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setToolTip(
                f"{camera} · {record.when}\n{quality_label(record.quality)}"
                f" · detección {record.confidence:.0%}\nClic: análisis forense"
            )
            self.faces_grid.addItem(item)

    def _clear_faces(self) -> None:
        face_registry.clear()
        self._capture_signature = None
        self.refresh()

    def _open_face(self, item: QListWidgetItem) -> None:
        record = item.data(Qt.ItemDataRole.UserRole)
        if record is not None:
            open_forensics(record, self)
