"""Panel de estado general del sistema, en la pantalla de Inicio: cuantas
camaras estan online, alarmas sin reconocer y analiticas corriendo -- para
ver "como esta todo" de un vistazo, sin entrar a cada modulo. Se
actualiza solo cada pocos segundos mientras la pestaña este visible."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, SimpleCardWidget, StrongBodyLabel, TitleLabel

from aurea_vms.core.analytics_engine import analytics_engine
from aurea_vms.models import alarm_event, repository

# Mismos colores de estado que Dispositivos (online/offline/desconocido) --
# reservados para estado, no se reciclan para otra cosa.
COLOR_ONLINE = QColor("#3fb950")
COLOR_OFFLINE = QColor("#e5534b")
COLOR_UNKNOWN = QColor("#6e7681")
REFRESH_MS = 5000


class _DonutChart(QWidget):
    """Donut liviano dibujado a mano (sin sumar una libreria de graficos
    solo para esto) -- proporciones de camaras por estado, con el total en
    el centro."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[tuple[float, QColor]] = []
        self._center_text = "0"
        self.setFixedSize(96, 96)

    def sizeHint(self) -> QSize:  # noqa: N802 - override de Qt
        return QSize(96, 96)

    def set_data(self, segments: list[tuple[float, QColor]], center_text: str) -> None:
        self._segments = [s for s in segments if s[0] > 0]
        self._center_text = center_text
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen_width = 12
        rect = QRectF(
            pen_width / 2, pen_width / 2, self.width() - pen_width, self.height() - pen_width
        )
        total = sum(value for value, _ in self._segments)

        pen = QPen()
        pen.setWidth(pen_width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)

        if total <= 0:
            pen.setColor(QColor("#2a3441"))
            painter.setPen(pen)
            painter.drawArc(rect, 0, 360 * 16)
        else:
            start_angle = 90 * 16
            for value, color in self._segments:
                span = -round(360 * 16 * (value / total))
                pen.setColor(color)
                painter.setPen(pen)
                painter.drawArc(rect, start_angle, span)
                start_angle += span

        painter.setPen(QColor("#e5e7eb"))
        font = painter.font()
        font.setPointSize(15)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._center_text)


class _StatTile(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.value_label = TitleLabel("—", self)
        layout.addWidget(self.value_label)

        caption = CaptionLabel(title, self)
        caption.setWordWrap(True)
        layout.addWidget(caption)

    def set_value(self, text: str) -> None:
        self.value_label.setText(text)


class DashboardPanel(SimpleCardWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("SimpleCardWidget { background-color: rgba(16, 21, 30, 225); }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)
        outer.addWidget(StrongBodyLabel("Estado del sistema"))

        body = QHBoxLayout()
        body.setSpacing(24)

        self.donut = _DonutChart(self)
        body.addWidget(self.donut)

        legend = QVBoxLayout()
        legend.setSpacing(4)
        self.online_caption = CaptionLabel("● En línea: —", self)
        self.online_caption.setStyleSheet(f"color: {COLOR_ONLINE.name()};")
        self.offline_caption = CaptionLabel("● Desconectadas: —", self)
        self.offline_caption.setStyleSheet(f"color: {COLOR_OFFLINE.name()};")
        self.unknown_caption = CaptionLabel("● Sin probar: —", self)
        self.unknown_caption.setStyleSheet(f"color: {COLOR_UNKNOWN.name()};")
        legend.addWidget(self.online_caption)
        legend.addWidget(self.offline_caption)
        legend.addWidget(self.unknown_caption)
        legend.addStretch(1)
        body.addLayout(legend)

        body.addSpacing(12)

        self.alarms_tile = _StatTile("Alarmas sin reconocer")
        body.addWidget(self.alarms_tile)

        self.analytics_tile = _StatTile("Analíticas corriendo")
        body.addWidget(self.analytics_tile)

        body.addStretch(1)
        outer.addLayout(body)

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

    def refresh(self) -> None:
        devices = repository.list_devices()
        online = sum(1 for d in devices if d.status == "online")
        offline = sum(1 for d in devices if d.status == "offline")
        unknown = len(devices) - online - offline

        self.donut.set_data(
            [(online, COLOR_ONLINE), (offline, COLOR_OFFLINE), (unknown, COLOR_UNKNOWN)],
            str(len(devices)),
        )
        self.online_caption.setText(f"● En línea: {online}")
        self.offline_caption.setText(f"● Desconectadas: {offline}")
        self.unknown_caption.setText(f"● Sin probar: {unknown}")

        pending_alarms = sum(
            1
            for event in repository.list_alarm_events(limit=500)
            if event.status != alarm_event.STATUS_RESOLVED
        )
        self.alarms_tile.set_value(str(pending_alarms))
        self.analytics_tile.set_value(str(analytics_engine.running_count()))
