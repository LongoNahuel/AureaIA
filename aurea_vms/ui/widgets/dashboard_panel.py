"""Panel de estado general del sistema, en la pantalla de Inicio: camaras en
linea, incidentes sin reconocer y en investigacion, y analiticas activas --
para ver "como esta todo" de un vistazo, sin entrar a cada modulo. Respeta
el sitio elegido arriba y se actualiza solo mientras la pestaña se ve.

Fase 1 de la interfaz (2026-10-08): las cuatro cifras en la misma tarjeta
(KpiTile) que Vista Inteligente y el Dashboard de Eventos. "Sin reconocer"
cuenta los incidentes nuevos; antes contaba todo lo no resuelto (tambien los
ya reconocidos) y no seguia el filtro de sitio.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import StrongBodyLabel

from aurea_vms.core import app_state
from aurea_vms.core.analytics_engine import analytics_engine
from aurea_vms.core.event_bus import event_bus
from aurea_vms.models import repository
from aurea_vms.models.alarm_event import STATUS_INVESTIGATING, STATUS_NEW
from aurea_vms.ui.theme import SPACE
from aurea_vms.ui.widgets.kpi_tile import KpiTile

REFRESH_MS = 5000


def cameras_summary(site_id: int | None) -> tuple[str, str, str]:
    """(valor, tono, detalle) de la tarjeta de camaras: "6/7" en rojo si
    alguna esta desconectada."""
    by_status = repository.count_devices_by_status(site_id)
    total = sum(by_status.values())
    online = by_status.get("online", 0)
    offline = by_status.get("offline", 0)
    unknown = total - online - offline
    if offline:
        tone, detail = "alert", f"{offline} desconectada{'s' if offline > 1 else ''}"
    elif unknown:
        tone, detail = "neutral", f"{unknown} sin probar"
    else:
        tone, detail = ("ok" if total else "neutral"), ""
    return f"{online}/{total}", tone, detail


def alarm_summary(site_id: int | None) -> tuple[int, int]:
    """(sin reconocer, en investigacion) del sitio."""
    return (
        repository.count_alarm_events(site_id=site_id, status=STATUS_NEW),
        repository.count_alarm_events(site_id=site_id, status=STATUS_INVESTIGATING),
    )


class DashboardPanel(QWidget):
    def __init__(
        self, parent: QWidget | None = None, title: str | None = "Estado del sistema"
    ) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE["s"])
        if title:
            outer.addWidget(StrongBodyLabel(title, self))

        row = QHBoxLayout()
        row.setSpacing(SPACE["m"])
        self.cameras_tile = KpiTile("Cámaras en línea", self)
        self.unacknowledged_tile = KpiTile("Sin reconocer", self)
        self.investigating_tile = KpiTile("En investigación", self)
        self.analytics_tile = KpiTile("Analíticas activas", self)
        for tile in (
            self.cameras_tile,
            self.unacknowledged_tile,
            self.investigating_tile,
            self.analytics_tile,
        ):
            row.addWidget(tile, stretch=1)
        outer.addLayout(row)

        event_bus.site_filter_changed.connect(self._on_site_filter_changed)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.refresh()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._timer.stop()
        super().hideEvent(event)

    def _on_site_filter_changed(self, _site_id: object) -> None:
        self.refresh()

    def refresh(self) -> None:
        site_id = app_state.current_site_id
        self.cameras_tile.set_value(*cameras_summary(site_id))
        unacknowledged, investigating = alarm_summary(site_id)
        self.unacknowledged_tile.set_value(unacknowledged, "alert" if unacknowledged else "ok")
        self.investigating_tile.set_value(investigating, "warn" if investigating else "neutral")
        self.analytics_tile.set_value(analytics_engine.running_count())
