"""Graficos de Inicio, dibujados a mano con QPainter (Fase 2 de la interfaz,
2026-10-09): torta (dona), lineas por dia u hora y barras horizontales, mas
la leyenda. Los del dashboard analitico (ultima hora, medidores contra un
maximo) estan en charts.py; tinta y grilla son las mismas
(analytics_visuals).

Siguen la guia de visualizacion:
- trazos finos: lineas de 2 px, barras de hasta 12 px con el extremo
  redondeado y la base recta, area al 10 % solo con una serie;
- separacion en el color del fondo: 2 px entre segmentos de la torta y un
  anillo de 2 px alrededor del punto final de cada linea;
- grilla y ejes en linea fina continua, un tono sobre el fondo;
- el texto nunca va en el color de la serie: la identidad la da la muestra
  de color al lado del rotulo, y hay leyenda con dos o mas series;
- al pasar el mouse: en la linea, una vertical que se engancha al punto mas
  cercano con todas las series; en la torta y las barras, el segmento o la
  barra se resalta y muestra su valor. La leyenda tambien trae los valores:
  el tooltip suma, no es la unica forma de leerlos.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGridLayout, QLabel, QToolTip, QWidget

from aurea_vms.ui.widgets import analytics_visuals as ink

# El fondo de las tarjetas de Inicio: el separador de 2 px entre segmentos y
# el anillo de los puntos van en este color.
SURFACE = QColor("#1a2029")
GRID = QColor(ink.GRID_LINE)
AXIS = QColor("#3a4453")
TEXT_PRIMARY = QColor(ink.TEXT_PRIMARY)
TEXT_SECONDARY = QColor(ink.TEXT_SECONDARY)
TEXT_MUTED = QColor(ink.TEXT_MUTED)
EMPTY_TRACK = QColor(ink.GRID_LINE)
SURFACE_GAP_PX = 2.0


def _font(widget: QWidget, px: int, bold: bool = False) -> QFont:
    font = QFont(widget.font())
    font.setPixelSize(px)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def _pct(value: int, total: int) -> str:
    return f"{round(100 * value / total)} %" if total else "0 %"


def nice_max(value: float, max_ticks: int = 5) -> tuple[float, float]:
    """Tope del eje y paso redondos (1, 2, 2,5 o 5 x 10^n), el tope mas bajo
    que contenga `value` con hasta `max_ticks` divisiones: 22 -> 25 de a 5
    (no 40 de a 10). Cantidades enteras: el paso minimo es 1."""
    if value <= 0:
        return 4.0, 1.0
    magnitude = 10 ** math.floor(math.log10(value / max_ticks))
    for factor in (1, 2, 2.5, 5, 10, 20, 25, 50):
        step = max(1.0, factor * magnitude)
        ticks = math.ceil(value / step)
        if ticks <= max_ticks:
            return ticks * step, step
    return value, value


# --- leyenda ---------------------------------------------------------------


class ChartLegend(QWidget):
    """Muestra de color + rotulo + valor (+ porcentaje). `marker`: "dot" para
    tortas y barras, "line" para lineas (la leyenda imita la marca)."""

    def __init__(
        self, parent: QWidget | None = None, marker: str = "dot", inline: bool = False
    ) -> None:
        super().__init__(parent)
        self.marker = marker
        # En fila (debajo de la linea): muestra, rotulo y valor juntos, uno
        # al lado del otro; en columna, el valor alineado a la derecha.
        self.inline = inline
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(6)
        self.rows: list[tuple[str, str]] = []

    def set_items(self, items: list[tuple[str, str, str]], percent_of: int | None = None) -> None:
        """`items`: (rotulo, valor, color). Con `percent_of`, suma el % del total."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.rows = []
        for row, (label, value, color) in enumerate(items):
            swatch = QLabel(self)
            if self.marker == "line":
                swatch.setFixedSize(14, 3)
                swatch.setStyleSheet(f"background: {color}; border-radius: 1px;")
            else:
                swatch.setFixedSize(9, 9)
                swatch.setStyleSheet(f"background: {color}; border-radius: 4px;")
            name = QLabel(label, self)
            name.setStyleSheet(f"color: {TEXT_SECONDARY.name()}; font-size: 12px;")
            amount = QLabel(str(value), self)
            amount.setStyleSheet(
                f"color: {TEXT_PRIMARY.name()}; font-size: 12px; font-weight: 600;"
            )
            amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if self.inline:
                column = row * 4
                self._grid.addWidget(swatch, 0, column, Qt.AlignmentFlag.AlignVCenter)
                self._grid.addWidget(name, 0, column + 1)
                self._grid.addWidget(amount, 0, column + 2)
                self._grid.setColumnMinimumWidth(column + 3, 18)
                self.rows.append((label, str(value)))
                continue
            self._grid.addWidget(swatch, row, 0, Qt.AlignmentFlag.AlignVCenter)
            self._grid.addWidget(name, row, 1)
            self._grid.addWidget(amount, row, 2)
            if percent_of is not None:
                share = QLabel(_pct(int(value), percent_of), self)
                share.setStyleSheet(f"color: {TEXT_MUTED.name()}; font-size: 11px;")
                share.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self._grid.addWidget(share, row, 3)
            self.rows.append((label, str(value)))
        if self.inline:
            self._grid.setColumnStretch(len(items) * 4, 1)
        else:
            self._grid.setColumnStretch(1, 1)


# --- torta (dona) ----------------------------------------------------------


class DonutChart(QWidget):
    THICKNESS = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.segments: list[tuple[str, int, str]] = []
        self.center_value = "0"
        self.center_caption = ""
        self.hovered: int | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(170, 170)

    def sizeHint(self) -> QSize:  # noqa: N802 - override de Qt
        return QSize(180, 180)

    def set_data(self, segments, center_value: str, center_caption: str = "") -> None:
        self.segments = [s for s in segments if s[1] > 0]
        self.center_value = center_value
        self.center_caption = center_caption
        self.hovered = None
        self.update()

    @property
    def total(self) -> int:
        return sum(value for _label, value, _color in self.segments)

    def _ring(self) -> tuple[QPointF, float]:
        side = min(self.width(), self.height())
        return QPointF(self.width() / 2, self.height() / 2), side / 2 - 6

    def segment_at(self, pos: QPointF) -> int | None:
        center, radius = self._ring()
        dx, dy = pos.x() - center.x(), pos.y() - center.y()
        distance = math.hypot(dx, dy)
        if not (radius - self.THICKNESS - 4 <= distance <= radius + 4) or not self.total:
            return None
        # Angulo desde las 12, en sentido horario, en [0, 360).
        angle = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
        start = 0.0
        for index, (_label, value, _color) in enumerate(self.segments):
            span = 360 * value / self.total
            if start <= angle < start + span:
                return index
            start += span
        return None

    def paintEvent(self, _event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center, radius = self._ring()
        total = self.total
        if not total:
            pen = QPen(EMPTY_TRACK, self.THICKNESS)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            r = radius - self.THICKNESS / 2
            painter.drawEllipse(center, r, r)
        else:
            gap = math.degrees(SURFACE_GAP_PX / radius) if len(self.segments) > 1 else 0.0
            start = 0.0
            for index, (_label, value, color) in enumerate(self.segments):
                span = 360 * value / total
                grow = 3 if index == self.hovered else 0
                path = self._arc_path(center, radius + grow, start + gap / 2, span - gap)
                painter.fillPath(path, QColor(color))
                start += span

        painter.setPen(TEXT_PRIMARY)
        painter.setFont(_font(self, 24, bold=True))
        value_rect = QRectF(0, center.y() - 22, self.width(), 30)
        painter.drawText(value_rect, int(Qt.AlignmentFlag.AlignCenter), self.center_value)
        if self.center_caption:
            painter.setPen(TEXT_MUTED)
            painter.setFont(_font(self, 11))
            caption_rect = QRectF(0, center.y() + 8, self.width(), 16)
            painter.drawText(caption_rect, int(Qt.AlignmentFlag.AlignCenter), self.center_caption)
        painter.end()

    def _arc_path(self, center: QPointF, radius: float, start: float, span: float) -> QPainterPath:
        """Segmento de anillo. Angulos en grados desde las 12, horario (Qt
        mide antihorario desde las 3)."""
        outer = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
        inner_r = radius - self.THICKNESS
        inner = QRectF(center.x() - inner_r, center.y() - inner_r, 2 * inner_r, 2 * inner_r)
        qt_start = 90 - start
        path = QPainterPath()
        path.arcMoveTo(outer, qt_start)
        path.arcTo(outer, qt_start, -max(span, 0.1))
        path.arcTo(inner, qt_start - max(span, 0.1), max(span, 0.1))
        path.closeSubpath()
        return path

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        index = self.segment_at(event.position())
        if index != self.hovered:
            self.hovered = index
            self.update()
        if index is None:
            QToolTip.hideText()
            return
        label, value, _color = self.segments[index]
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{value}  ·  {label} ({_pct(value, self.total)})",
            self,
        )

    def leaveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.hovered = None
        self.update()
        super().leaveEvent(event)


# --- lineas ----------------------------------------------------------------


class LineChart(QWidget):
    MARGIN_LEFT = 34
    MARGIN_RIGHT = 14
    MARGIN_TOP = 12
    MARGIN_BOTTOM = 26

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.labels: list[str] = []
        self.series: list[tuple[str, list[int], str]] = []
        self.hovered: int | None = None
        self.empty_text = "Sin incidentes en el período"
        self.setMouseTracking(True)
        self.setMinimumHeight(220)

    def sizeHint(self) -> QSize:  # noqa: N802 - override de Qt
        return QSize(600, 240)

    def set_data(self, labels: list[str], series) -> None:
        self.labels = list(labels)
        self.series = list(series)
        self.hovered = None
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(
            self.MARGIN_LEFT,
            self.MARGIN_TOP,
            max(1, self.width() - self.MARGIN_LEFT - self.MARGIN_RIGHT),
            max(1, self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM),
        )

    def _x(self, index: int, plot: QRectF) -> float:
        count = max(len(self.labels) - 1, 1)
        return plot.left() + plot.width() * index / count

    def peak(self) -> int:
        return max((max(values, default=0) for _n, values, _c in self.series), default=0)

    def paintEvent(self, _event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        top, step = nice_max(self.peak())
        font = _font(self, 11)
        painter.setFont(font)

        # Grilla y eje Y (lineas finas continuas, un tono sobre el fondo).
        ticks = round(top / step)
        for tick in range(ticks + 1):
            y = plot.bottom() - plot.height() * tick / ticks
            painter.setPen(QPen(AXIS if tick == 0 else GRID, 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(TEXT_MUTED)
            painter.drawText(
                QRectF(0, y - 8, self.MARGIN_LEFT - 8, 16),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{int(tick * step)}",
            )

        # Eje X: unas 6 etiquetas repartidas.
        if self.labels:
            every = max(1, math.ceil(len(self.labels) / 6))
            for index, label in enumerate(self.labels):
                if index % every and index != len(self.labels) - 1:
                    continue
                x = self._x(index, plot)
                painter.setPen(TEXT_MUTED)
                painter.drawText(
                    QRectF(x - 30, plot.bottom() + 6, 60, 16),
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                    label,
                )

        if not self.peak():
            painter.setPen(TEXT_MUTED)
            painter.setFont(_font(self, 12))
            painter.drawText(plot, int(Qt.AlignmentFlag.AlignCenter), self.empty_text)
            painter.end()
            return

        def point(index: int, value: int) -> QPointF:
            return QPointF(self._x(index, plot), plot.bottom() - plot.height() * value / top)

        for name, values, color in self.series:
            del name
            qcolor = QColor(color)
            path = QPainterPath(point(0, values[0]))
            for index, value in enumerate(values[1:], start=1):
                path.lineTo(point(index, value))
            if len(self.series) == 1:  # area: un lavado al 10 %, solo con una serie
                area = QPainterPath(path)
                area.lineTo(QPointF(self._x(len(values) - 1, plot), plot.bottom()))
                area.lineTo(QPointF(plot.left(), plot.bottom()))
                area.closeSubpath()
                wash = QColor(qcolor)
                wash.setAlphaF(0.10)
                painter.fillPath(area, wash)
            pen = QPen(qcolor, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            # Punto final: 8 px con anillo del color del fondo.
            end = point(len(values) - 1, values[-1])
            painter.setPen(QPen(SURFACE, 2))
            painter.setBrush(qcolor)
            painter.drawEllipse(end, 5, 5)

        if self.hovered is not None:
            x = self._x(self.hovered, plot)
            painter.setPen(QPen(TEXT_MUTED, 1))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            for _name, values, color in self.series:
                painter.setPen(QPen(SURFACE, 2))
                painter.setBrush(QColor(color))
                painter.drawEllipse(point(self.hovered, values[self.hovered]), 4.5, 4.5)
        painter.end()

    def index_at(self, x: float) -> int | None:
        if not self.labels:
            return None
        plot = self._plot()
        count = max(len(self.labels) - 1, 1)
        ratio = (x - plot.left()) / plot.width()
        return max(0, min(len(self.labels) - 1, round(ratio * count)))

    def tooltip_text(self, index: int) -> str:
        rows = [f"{self.labels[index]}"]
        rows += [f"{values[index]}  ·  {name}" for name, values, _c in self.series]
        return "\n".join(rows)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        index = self.index_at(event.position().x()) if self.peak() else None
        if index != self.hovered:
            self.hovered = index
            self.update()
        if index is not None:
            QToolTip.showText(event.globalPosition().toPoint(), self.tooltip_text(index), self)

    def leaveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.hovered = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)


# --- barras horizontales -----------------------------------------------------


class BarList(QWidget):
    """Una barra por fila: rotulo a la izquierda, valor en la punta. Una sola
    serie, un solo color."""

    ROW_HEIGHT = 34
    BAR = 12
    LABEL_WIDTH = 0.36
    MAX_LABEL_PX = 230

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = QColor(color)
        self.rows: list[tuple[str, int]] = []
        self.hovered: int | None = None
        self.empty_text = "Sin incidentes en el período"
        self.setMouseTracking(True)
        self.setMinimumHeight(self.ROW_HEIGHT * 3)

    def set_data(self, rows: list[tuple[str, int]]) -> None:
        self.rows = list(rows)
        self.hovered = None
        self.setMinimumHeight(self.ROW_HEIGHT * max(3, len(self.rows)))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.rows:
            painter.setPen(TEXT_MUTED)
            painter.setFont(_font(self, 12))
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self.empty_text)
            painter.end()
            return
        peak = max(value for _label, value in self.rows) or 1
        label_w = min(self.width() * self.LABEL_WIDTH, self.MAX_LABEL_PX)
        value_w = 40
        track_w = max(1.0, self.width() - label_w - value_w - 12)
        for index, (label, value) in enumerate(self.rows):
            y = index * self.ROW_HEIGHT
            middle = y + self.ROW_HEIGHT / 2
            if index == self.hovered:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#212a37"))
                painter.drawRoundedRect(QRectF(0, y + 2, self.width(), self.ROW_HEIGHT - 4), 6, 6)
            painter.setFont(_font(self, 12))
            painter.setPen(TEXT_SECONDARY)
            text = painter.fontMetrics().elidedText(
                label, Qt.TextElideMode.ElideRight, int(label_w - 12)
            )
            painter.drawText(
                QRectF(8, y, label_w - 12, self.ROW_HEIGHT),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                text,
            )
            width = max(4.0, track_w * value / peak)
            bar = QRectF(label_w, middle - self.BAR / 2, width, self.BAR)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.color)
            # Punta redondeada de 4 px y base recta (la esquina izquierda se
            # cubre con un rectangulo del mismo color).
            painter.drawRoundedRect(bar, 4, 4)
            painter.fillRect(
                QRectF(bar.left(), bar.top(), min(4.0, width), bar.height()), self.color
            )
            painter.setPen(TEXT_PRIMARY)
            painter.setFont(_font(self, 12, bold=True))
            painter.drawText(
                QRectF(bar.right() + 8, y, value_w, self.ROW_HEIGHT),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                str(value),
            )
        painter.end()

    def row_at(self, y: float) -> int | None:
        index = int(y // self.ROW_HEIGHT)
        return index if 0 <= index < len(self.rows) else None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        index = self.row_at(event.position().y())
        if index != self.hovered:
            self.hovered = index
            self.update()
        if index is not None:
            label, value = self.rows[index]
            QToolTip.showText(event.globalPosition().toPoint(), f"{value}  ·  {label}", self)

    def leaveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.hovered = None
        self.update()
        super().leaveEvent(event)
