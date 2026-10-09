""" "Requiere atención" de Inicio (Fase 2 de la interfaz, 2026-10-08): los
incidentes abiertos mas recientes (sin reconocer y en investigacion) del
sitio elegido, con su captura. Un clic abre el incidente en Alarmas.

Si no hay nada abierto, lo dice en grande: "Todo en orden". Es lo primero
que un operador quiere saber al llegar al puesto.
"""

from __future__ import annotations

import datetime as dt
import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from aurea_vms.core import app_state, media_store
from aurea_vms.core.event_bus import event_bus
from aurea_vms.models import repository
from aurea_vms.models.alarm_event import STATUS_INVESTIGATING, STATUS_NEW
from aurea_vms.models.media_asset import KIND_SNAPSHOT
from aurea_vms.ui import icons
from aurea_vms.ui.labels import ALARM_STATUS_LABELS, display_class
from aurea_vms.ui.theme import TONES, rgba, severity_qcolor

MAX_ROWS = 6
REFRESH_MS = 5000
THUMB = QSize(96, 54)
_MUTED = "#8a93a0"
_ROW_BG = "#222a36"
_ROW_HOVER = "#2a3442"
STATUS_TONES = {STATUS_NEW: "alert", STATUS_INVESTIGATING: "warn"}


def time_ago(timestamp: float, now: float | None = None) -> str:
    """ "hace 3 min", "hace 2 h"; mas de un dia, la fecha."""
    seconds = max(0, (now if now is not None else time.time()) - timestamp)
    if seconds < 60:
        return "recién"
    if seconds < 3600:
        return f"hace {int(seconds // 60)} min"
    if seconds < 86400:
        return f"hace {int(seconds // 3600)} h"
    return dt.datetime.fromtimestamp(timestamp).strftime("%d/%m %H:%M")


def open_incidents(site_id: int | None, limit: int = MAX_ROWS) -> list:
    """Sin reconocer y en investigacion, los mas recientes primero."""
    events = [
        event
        for status in (STATUS_NEW, STATUS_INVESTIGATING)
        for event in repository.list_alarm_events(limit=limit, site_id=site_id, status=status)
    ]
    return sorted(events, key=lambda event: event.timestamp, reverse=True)[:limit]


class _IncidentRow(QFrame):
    clicked = Signal(int)

    def __init__(self, event, camera: str, snapshot: str | None, parent=None) -> None:
        super().__init__(parent)
        self.event_id = event.id
        self.status = event.status
        self.setObjectName("incidentRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Abrir en Alarmas")
        self.setStyleSheet(
            f"#incidentRow {{ background: {_ROW_BG}; border-radius: 10px; }}"
            f"#incidentRow:hover {{ background: {_ROW_HOVER}; }}"
            "QLabel { background: transparent; }"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 14, 8)
        row.setSpacing(12)

        thumb = QLabel(self)
        thumb.setFixedSize(THUMB)
        pixmap = QPixmap(snapshot) if snapshot else QPixmap()
        if pixmap.isNull():
            thumb.setStyleSheet("background: #10151d; border-radius: 6px;")
        else:
            thumb.setPixmap(
                pixmap.scaled(
                    THUMB,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            thumb.setStyleSheet("border-radius: 6px;")
        row.addWidget(thumb)

        text = QVBoxLayout()
        text.setSpacing(2)
        severity = severity_qcolor(event.severity).name()
        title = QLabel(
            f"<span style='color:{severity}'>●</span>&nbsp; {display_class(event.object_class)}",
            self,
        )
        title.setStyleSheet("color: #f3f4f6; font-size: 13px; font-weight: 600;")
        text.addWidget(title)
        where = QLabel(f"#{event.id} · {camera}", self)
        where.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
        text.addWidget(where)
        row.addLayout(text, stretch=1)

        side = QVBoxLayout()
        side.setSpacing(4)
        tone = TONES[STATUS_TONES.get(event.status, "neutral")]
        status = QLabel(ALARM_STATUS_LABELS.get(event.status, event.status), self)
        status.setStyleSheet(
            f"color: {tone}; background: {rgba(tone, 0.14)}; border-radius: 9px;"
            " padding: 2px 9px; font-size: 11px; font-weight: 600;"
        )
        side.addWidget(status, 0, Qt.AlignmentFlag.AlignRight)
        when = QLabel(time_ago(event.timestamp), self)
        when.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        side.addWidget(when, 0, Qt.AlignmentFlag.AlignRight)
        row.addLayout(side)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.event_id)
        super().mouseReleaseEvent(event)


class AttentionList(QWidget):
    incident_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self.rows: list[_IncidentRow] = []
        self.empty_state: QWidget | None = None

        event_bus.alarm.connect(self._on_alarm, Qt.ConnectionType.QueuedConnection)
        event_bus.site_filter_changed.connect(self._on_site_filter_changed)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._timer.stop()
        super().hideEvent(event)

    def _on_alarm(self, _event) -> None:
        self.refresh()

    def _on_site_filter_changed(self, _site_id: object) -> None:
        self.refresh()

    def refresh(self) -> None:
        events = open_incidents(app_state.current_site_id)
        shown = [(row.event_id, row.status) for row in self.rows]
        if shown == [(e.id, e.status) for e in events] and (events or self.empty_state):
            return  # nada cambio: no se rearma (evita parpadeo cada 5 s)
        self._clear()
        if not events:
            self.empty_state = self._build_empty_state()
            self._layout.addWidget(self.empty_state)
            return
        names = {device.id: device.name for device in repository.list_devices()}
        media = repository.list_media_for_events([event.id for event in events])
        for event in events:
            snapshot = next(
                (
                    str(media_store.absolute_path(asset.rel_path))
                    for asset in media.get(event.id, [])
                    if asset.kind == KIND_SNAPSHOT
                ),
                None,
            )
            row = _IncidentRow(
                event, names.get(event.device_id, f"Cámara #{event.device_id}"), snapshot, self
            )
            row.clicked.connect(self.incident_requested)
            self._layout.addWidget(row)
            self.rows.append(row)
        self._layout.addStretch(1)

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.rows = []
        self.empty_state = None

    def _build_empty_state(self) -> QWidget:
        box = QFrame(self)
        box.setObjectName("allClear")
        box.setStyleSheet(
            "#allClear { background: transparent; }QLabel { background: transparent; }"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(24, 36, 24, 36)
        layout.setSpacing(8)
        check = QLabel(box)
        check.setPixmap(icons.icon_check(TONES["ok"]).pixmap(36, 36))
        check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(check)
        title = QLabel("Todo en orden", box)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #f3f4f6; font-size: 17px; font-weight: 700;")
        layout.addWidget(title)
        caption = QLabel("No hay incidentes sin reconocer ni en investigación.", box)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
        layout.addWidget(caption)
        return box
