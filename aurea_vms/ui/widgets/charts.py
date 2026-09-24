"""Graficos del dashboard analitico (QPainter, sin dependencias nuevas).

Reglas comunes (guia de visualizacion del proyecto):
- un solo eje Y, grilla y ejes tenues; el dato manda;
- barras finas con el extremo superior redondeado y anclado a la base,
  separadas 2 px;
- con dos series hay leyenda (muestra + nombre) y cada serie conserva su
  color siempre (el color sigue a la entidad, no al orden);
- texto en tinta neutra; el color solo en las marcas;
- tooltip al pasar el mouse en todos los graficos.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from aurea_vms.ui.widgets.analytics_visuals import (
    GRID_LINE,
    STATUS_CRITICAL,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    format_elapsed,
)

AXIS_FONT_PX = 10
# Serie categorica validada sobre la superficie oscura (azul / naranja).
SERIES_BLUE = "#3987e5"
SERIES_ORANGE = "#d95926"
NEUTRAL_STATE = "#3a4658"


def nice_ceiling(value: float) -> float:
    """Tope "redondo" del eje (1, 2, 5 x 10^n) para que las marcas se lean."""
    if value <= 0:
        return 1.0
    exponent = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        if value <= step * exponent:
            return float(step * exponent)
    return float(10 * exponent)


def _fmt(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def _clock(timestamp: float) -> str:
    return dt.datetime.fromtimestamp(timestamp).strftime("%H:%M")


@dataclass
class Series:
    label: str
    color: str
    points: list[tuple[float, float]]  # (inicio del intervalo, valor)


def _paint_font(painter: QPainter, px: int = AXIS_FONT_PX, bold: bool = False) -> None:
    font = painter.font()
    font.setPixelSize(px)
    font.setBold(bold)
    painter.setFont(font)


class TimeSeriesChart(QWidget):
    """Serie temporal: "area" (una serie) o "bars" (una o dos series
    agrupadas). `group` junta N intervalos por barra (ej. 5 de 1 min)."""

    def __init__(
        self,
        kind: str = "area",
        unit: str = "",
        group: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._kind = kind
        self._unit = unit
        self._group = max(1, group)
        self._series: list[Series] = []
        self._hover: int | None = None
        self.setMinimumHeight(130)
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:  # noqa: N802 - override de Qt
        return QSize(360, 150)

    def set_series(self, series: list[Series]) -> None:
        self._series = [self._grouped(s) for s in series]
        self.update()

    def _grouped(self, series: Series) -> Series:
        if self._group == 1:
            return series
        points = series.points
        grouped = []
        for index in range(0, len(points), self._group):
            chunk = points[index : index + self._group]
            if not chunk:
                continue
            value = sum(v for _, v in chunk) if self._kind == "bars" else max(v for _, v in chunk)
            grouped.append((chunk[0][0], value))
        return Series(series.label, series.color, grouped)

    # --- geometria --------------------------------------------------------

    def _plot_rect(self) -> QRectF:
        top = 22.0 if len(self._series) > 1 else 8.0
        return QRectF(
            30.0, top, max(10.0, self.width() - 38.0), max(10.0, self.height() - top - 20.0)
        )

    def _count(self) -> int:
        return max((len(s.points) for s in self._series), default=0)

    def _top(self) -> float:
        peak = max((v for s in self._series for _, v in s.points), default=0.0)
        return nice_ceiling(peak)

    # --- pintura ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_font(painter)
        count = self._count()
        if count < 2 or all(v == 0 for s in self._series for _, v in s.points):
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Sin actividad en la última hora"
            )
            if count < 2:
                return
        plot = self._plot_rect()
        top = self._top()

        # grilla y eje Y (0, mitad, tope). La marca del medio solo se
        # rotula si es entera: son conteos, "2.5 personas" no se lee.
        for fraction in (0.0, 0.5, 1.0):
            y = plot.bottom() - plot.height() * fraction
            painter.setPen(QPen(QColor(GRID_LINE), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            tick = top * fraction
            if not float(tick).is_integer():
                continue
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                QRectF(0, y - 7, plot.left() - 5, 14),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _fmt(tick),
            )

        # eje X: inicio (alineado al borde del area), mitad y "ahora"
        points = self._series[0].points
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 3, 80, 14),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            _clock(points[0][0]),
        )
        middle = count // 2
        x = self._x(middle, plot, count)
        painter.drawText(
            QRectF(x - 40, plot.bottom() + 3, 80, 14),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            _clock(points[middle][0]),
        )
        painter.drawText(
            QRectF(plot.right() - 60, plot.bottom() + 3, 60, 14),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
            "ahora",
        )

        if self._kind == "area":
            self._paint_area(painter, plot, top, count)
        else:
            self._paint_bars(painter, plot, top, count)
        if len(self._series) > 1:
            self._paint_legend(painter)

    def _x(self, index: int, plot: QRectF, count: int) -> float:
        if self._kind == "bars":
            slot = plot.width() / count
            return plot.left() + slot * (index + 0.5)
        return plot.left() + plot.width() * index / max(1, count - 1)

    def _paint_area(self, painter: QPainter, plot: QRectF, top: float, count: int) -> None:
        for series in self._series:
            color = QColor(series.color)
            pts = [
                QPointF(self._x(i, plot, count), plot.bottom() - plot.height() * (v / top))
                for i, (_, v) in enumerate(series.points)
            ]
            area = QPainterPath(QPointF(pts[0].x(), plot.bottom()))
            for point in pts:
                area.lineTo(point)
            area.lineTo(QPointF(pts[-1].x(), plot.bottom()))
            area.closeSubpath()
            fill = QColor(color)
            fill.setAlpha(46)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawPath(area)
            line = QPainterPath(pts[0])
            for point in pts[1:]:
                line.lineTo(point)
            pen = QPen(color, 2)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(pen)
            painter.drawPath(line)
            painter.setPen(QPen(QColor(SURFACE), 2))
            painter.setBrush(color)
            painter.drawEllipse(pts[-1], 4, 4)
            if self._hover is not None and self._hover < len(pts):
                point = pts[self._hover]
                painter.setPen(QPen(QColor(TEXT_MUTED), 1, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(point.x(), plot.top()), QPointF(point.x(), plot.bottom()))
                painter.setPen(QPen(QColor(SURFACE), 2))
                painter.setBrush(color)
                painter.drawEllipse(point, 4, 4)

    def _paint_bars(self, painter: QPainter, plot: QRectF, top: float, count: int) -> None:
        slot = plot.width() / count
        groups = len(self._series)
        gap = 2.0
        bar = max(2.0, (slot - gap * (groups + 1)) / groups)
        for index in range(count):
            if index == self._hover:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 14))
                painter.drawRect(
                    QRectF(plot.left() + slot * index, plot.top(), slot, plot.height())
                )
            for g, series in enumerate(self._series):
                if index >= len(series.points):
                    continue
                value = series.points[index][1]
                if value <= 0:
                    continue
                height = max(2.0, plot.height() * value / top)
                x = plot.left() + slot * index + gap + g * (bar + gap)
                rect = QRectF(x, plot.bottom() - height, bar, height)
                radius = min(4.0, bar / 2, height / 2)
                path = QPainterPath()
                # extremo superior redondeado, base recta sobre el eje
                path.moveTo(rect.bottomLeft())
                path.lineTo(rect.left(), rect.top() + radius)
                path.quadTo(rect.topLeft(), QPointF(rect.left() + radius, rect.top()))
                path.lineTo(rect.right() - radius, rect.top())
                path.quadTo(rect.topRight(), QPointF(rect.right(), rect.top() + radius))
                path.lineTo(rect.bottomRight())
                path.closeSubpath()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(series.color))
                painter.drawPath(path)

    def _paint_legend(self, painter: QPainter) -> None:
        _paint_font(painter, 11)
        metrics = painter.fontMetrics()
        x = self.width() - 8.0
        for series in reversed(self._series):
            text_width = metrics.horizontalAdvance(series.label)
            x -= text_width
            painter.setPen(QColor(TEXT_SECONDARY))
            painter.drawText(
                QRectF(x, 2, text_width, 16), int(Qt.AlignmentFlag.AlignVCenter), series.label
            )
            x -= 14
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(series.color))
            painter.drawRoundedRect(QRectF(x, 6, 9, 9), 2, 2)
            x -= 14

    # --- interaccion ------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        count = self._count()
        if count < 2:
            return
        plot = self._plot_rect()
        x = event.position().x()
        if self._kind == "bars":
            index = int((x - plot.left()) / (plot.width() / count))
        else:
            index = round((x - plot.left()) / plot.width() * (count - 1))
        index = max(0, min(count - 1, index))
        if index != self._hover:
            self._hover = index
            self.update()
        start = self._series[0].points[index][0]
        span = (self._series[0].points[1][0] - self._series[0].points[0][0]) if count > 1 else 60
        lines = [f"{_clock(start)}–{_clock(start + span)}"]
        for series in self._series:
            value = series.points[index][1] if index < len(series.points) else 0
            lines.append(f"{series.label}: {_fmt(value)}{self._unit}")
        QToolTip.showText(event.globalPosition().toPoint(), "\n".join(lines), self)

    def leaveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._hover = None
        self.update()
        super().leaveEvent(event)


class StateTimeline(QWidget):
    """Una fila por elemento (ej. cada pantalla) con sus tramos de estado en
    la ventana. `states`: (clave, rotulo de leyenda, color), en el orden en
    que se muestran en la leyenda."""

    ROW_H = 18
    LABEL_W = 96

    def __init__(
        self,
        parent: QWidget | None = None,
        states: tuple[tuple[str, str, str], ...] = (
            ("normal", "Normal", NEUTRAL_STATE),
            ("alerta", "Alerta", STATUS_CRITICAL),
        ),
        empty_text: str = "Sin datos",
    ) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, list[tuple[float, float, str]]]] = []
        self._window = (0.0, 1.0)
        self._legend = states
        self._colors = {key: color for key, _, color in states}
        self._empty_text = empty_text
        self.setMouseTracking(True)
        self.setMinimumHeight(90)

    def set_rows(self, rows, window: tuple[float, float]) -> None:
        self._rows = rows
        self._window = window
        self.setMinimumHeight(max(90, 44 + len(rows) * (self.ROW_H + 8)))
        self.update()

    def _track(self) -> QRectF:
        return QRectF(self.LABEL_W, 24, max(10.0, self.width() - self.LABEL_W - 8), 0)

    def _x(self, timestamp: float, track: QRectF) -> float:
        t0, t1 = self._window
        return track.left() + track.width() * (timestamp - t0) / max(1.0, t1 - t0)

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_font(painter, 11)
        if not self._rows:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return
        # leyenda (estado = color + texto)
        x = self.width() - 8.0
        metrics = painter.fontMetrics()
        for state, label, _color in reversed(self._legend):
            width = metrics.horizontalAdvance(label)
            x -= width
            painter.setPen(QColor(TEXT_SECONDARY))
            painter.drawText(QRectF(x, 2, width, 16), int(Qt.AlignmentFlag.AlignVCenter), label)
            x -= 14
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._colors[state]))
            painter.drawRoundedRect(QRectF(x, 6, 9, 9), 2, 2)
            x -= 14

        track = self._track()
        for row, (label, segments) in enumerate(self._rows):
            y = track.top() + row * (self.ROW_H + 8)
            painter.setPen(QColor(TEXT_SECONDARY))
            painter.drawText(
                QRectF(0, y, self.LABEL_W - 8, self.ROW_H),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                metrics.elidedText(label, Qt.TextElideMode.ElideRight, self.LABEL_W - 10),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(GRID_LINE))
            painter.drawRoundedRect(QRectF(track.left(), y, track.width(), self.ROW_H), 4, 4)
            for start, end, state in segments:
                x0, x1 = self._x(start, track), self._x(end, track)
                if x1 - x0 < 1:
                    x1 = x0 + 1
                painter.setBrush(QColor(self._colors.get(state, NEUTRAL_STATE)))
                # 1 px de la superficie entre tramos contiguos
                painter.drawRoundedRect(QRectF(x0 + 0.5, y, x1 - x0 - 1, self.ROW_H), 3, 3)
        # eje de tiempo
        bottom = track.top() + len(self._rows) * (self.ROW_H + 8)
        painter.setPen(QColor(TEXT_MUTED))
        _paint_font(painter)
        painter.drawText(
            QRectF(track.left(), bottom, 60, 14),
            int(Qt.AlignmentFlag.AlignLeft),
            _clock(self._window[0]),
        )
        painter.drawText(
            QRectF(track.right() - 60, bottom, 60, 14), int(Qt.AlignmentFlag.AlignRight), "ahora"
        )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        track = self._track()
        pos = event.position()
        row = int((pos.y() - track.top()) // (self.ROW_H + 8))
        if not 0 <= row < len(self._rows) or pos.x() < track.left():
            QToolTip.hideText()
            return
        t0, t1 = self._window
        moment = t0 + (pos.x() - track.left()) / track.width() * (t1 - t0)
        label, segments = self._rows[row]
        for start, end, state in segments:
            if start <= moment <= end:
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"{label}\n{state.capitalize()} {_clock(start)}–{_clock(end)}"
                    f" ({format_elapsed(end - start)})",
                    self,
                )
                return
        QToolTip.hideText()


class CameraBars(QWidget):
    """Barras horizontales por camara (ej. ocupacion contra aforo)."""

    ROW_H = 22

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, float, float | None, str]] = []
        self.setMinimumHeight(30)

    def set_rows(self, rows: list[tuple[str, float, float | None, str]]) -> None:
        """(nombre, valor, maximo o None, color)."""
        self._rows = rows
        self.setMinimumHeight(max(30, len(rows) * self.ROW_H + 4))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_font(painter, 11)
        metrics = painter.fontMetrics()
        if not self._rows:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "Sin cámaras con esta analítica"
            )
            return
        label_w = 104.0
        value_w = 64.0
        scale = max([r[2] or r[1] for r in self._rows] + [1.0])
        for index, (name, value, maximum, color) in enumerate(self._rows):
            y = index * self.ROW_H + 2
            painter.setPen(QColor(TEXT_SECONDARY))
            painter.drawText(
                QRectF(0, y, label_w - 8, self.ROW_H - 4),
                int(Qt.AlignmentFlag.AlignVCenter),
                metrics.elidedText(name, Qt.TextElideMode.ElideRight, int(label_w - 10)),
            )
            track = QRectF(label_w, y + 6, max(10.0, self.width() - label_w - value_w), 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(GRID_LINE))
            painter.drawRoundedRect(track, 4, 4)
            top = maximum or scale
            fraction = max(0.0, min(1.0, value / top)) if top else 0.0
            if fraction > 0:
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(
                    QRectF(track.left(), track.top(), max(8.0, track.width() * fraction), 8), 4, 4
                )
            text = f"{_fmt(value)} / {_fmt(maximum)}" if maximum else _fmt(value)
            painter.setPen(QColor(TEXT_PRIMARY))
            painter.drawText(
                QRectF(track.right() + 6, y, value_w - 6, self.ROW_H - 4),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                text,
            )
