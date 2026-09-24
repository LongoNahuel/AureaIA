"""Piezas visuales compartidas por los paneles de analiticas (Vista
Inteligente, modulo Analizadores y overlay del video): historial de una
metrica en intervalos de tiempo, sparkline con tooltip, render del mapa de
calor y formato de duraciones.

Criterios (iguales en todos los paneles):
- El texto va en tinta neutra (TEXT_*); el color de la analitica solo
  marca identidad (punto, trazo), nunca el numero en si.
- Los colores de estado (ok / atencion / critico) quedan reservados para
  estado y siempre van con una etiqueta de texto, nunca solos.
- El mapa de calor usa una rampa secuencial de UN tono (naranja, de claro
  y transparente a intenso y opaco): mas color = mas permanencia. Nada de
  arcoiris tipo JET, que inventa fronteras que no existen en los datos.
"""

from __future__ import annotations

import time
from collections import deque

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

# Tokens sobre superficie oscura (mismos valores que _DARK_PALETTE de theme).
TEXT_PRIMARY = "#e5e7eb"
TEXT_SECONDARY = "#9aa3af"
TEXT_MUTED = "#6e7681"
SURFACE = "#171d27"
GRID_LINE = "#2a3441"

# Estado: reservados, siempre acompañados de una etiqueta.
STATUS_OK = "#3fb950"
STATUS_WARNING = "#f59e0b"
STATUS_CRITICAL = "#e5534b"

# Identidad de cada analitica (mismo orden fijo en toda la app).
ANALYTIC_ACCENTS = {
    "monitor_tamper": "#f59e0b",
    "people_counting": "#22c55e",
    "line_crossing": "#38bdf8",
    "face_detection": "#c084fc",
}

# Si la ultima lectura de una analitica tiene mas de esto, se considera
# vieja: la UI deja de dibujar sus cajas y la marca "sin datos".
STALE_AFTER_S = 3.0


def format_elapsed(seconds: float) -> str:
    """12 s / 4 min / 1 h 05 min."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


class MetricHistory:
    """Serie de una metrica agrupada en intervalos fijos de tiempo.

    Reemplaza a las listas que crecian con CADA muestra (una por cuadro
    analizado, sin tope de memoria) y cuyo grafico mostraba los ultimos
    30 cuadros: unos segundos, no una tendencia. `mode` decide como se
    resume un intervalo: "max" (pico de ocupacion en ese lapso) o "sum"
    (eventos ocurridos en ese lapso, ej. cruces)."""

    def __init__(self, bucket_s: float = 10.0, buckets: int = 60, mode: str = "max") -> None:
        self.bucket_s = bucket_s
        self.mode = mode
        self._buckets: deque[tuple[int, float]] = deque(maxlen=buckets)

    def clear(self) -> None:
        self._buckets.clear()

    def add(self, value: float, timestamp: float | None = None) -> None:
        now = time.time() if timestamp is None else timestamp
        key = int(now // self.bucket_s)
        if self._buckets and self._buckets[-1][0] == key:
            previous = self._buckets[-1][1]
            merged = previous + value if self.mode == "sum" else max(previous, value)
            self._buckets[-1] = (key, merged)
        else:
            self._buckets.append((key, value))

    def series(self, now: float | None = None) -> list[tuple[float, float]]:
        """[(inicio del intervalo, valor)] continuo hasta `now`: los
        intervalos sin lecturas valen 0 en "sum" y repiten el ultimo valor
        en "max" (la ocupacion no cae a cero porque no llegaron datos)."""
        if not self._buckets:
            return []
        now = time.time() if now is None else now
        maxlen = self._buckets.maxlen or len(self._buckets)
        last_key = max(int(now // self.bucket_s), self._buckets[-1][0])
        first_key = max(self._buckets[0][0], last_key - maxlen + 1)
        values = dict(self._buckets)
        series: list[tuple[float, float]] = []
        carry = 0.0
        for key in range(first_key, last_key + 1):
            if key in values:
                carry = values[key]
                series.append((key * self.bucket_s, carry))
            else:
                series.append((key * self.bucket_s, 0.0 if self.mode == "sum" else carry))
        return series


class Sparkline(QWidget):
    """Tendencia de una sola serie: trazo de 2 px, area suave, punto en la
    ultima lectura y tooltip con el valor del intervalo bajo el mouse. Sin
    leyenda (una sola serie: el titulo del panel la nombra)."""

    def __init__(
        self, color: str, unit: str = "", parent: QWidget | None = None, height: int = 44
    ) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._unit = unit
        self._series: list[tuple[float, float]] = []
        self._empty_text = "Sin datos todavía"
        self.setMinimumHeight(height)
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:  # noqa: N802 - override de Qt
        return QSize(220, self.minimumHeight())

    def set_series(self, series: list[tuple[float, float]]) -> None:
        self._series = series
        self.update()

    def _points(self) -> list[QPointF]:
        values = [value for _, value in self._series]
        top = max(max(values), 1.0)
        width = max(1.0, self.width() - 10.0)
        height = max(1.0, self.height() - 10.0)
        count = len(values)
        return [
            QPointF(
                5 + (width * index / (count - 1) if count > 1 else width),
                5 + height * (1 - value / top),
            )
            for index, value in enumerate(values)
        ]

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(SURFACE))
        painter.drawRoundedRect(QRectF(self.rect()), 6, 6)

        if len(self._series) < 2:
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return

        points = self._points()
        baseline_y = self.height() - 5.0
        painter.setPen(QPen(QColor(GRID_LINE), 1))
        painter.drawLine(QPointF(5, baseline_y), QPointF(self.width() - 5, baseline_y))

        area = QPainterPath(QPointF(points[0].x(), baseline_y))
        for point in points:
            area.lineTo(point)
        area.lineTo(QPointF(points[-1].x(), baseline_y))
        area.closeSubpath()
        fill = QColor(self._color)
        fill.setAlpha(40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPath(area)

        line = QPainterPath(points[0])
        for point in points[1:]:
            line.lineTo(point)
        pen = QPen(self._color, 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawPath(line)

        # Anillo del color de la superficie alrededor del punto final para
        # separarlo del trazo.
        painter.setPen(QPen(QColor(SURFACE), 2))
        painter.setBrush(self._color)
        painter.drawEllipse(points[-1], 4, 4)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if len(self._series) < 2:
            return
        points = self._points()
        x = event.position().x()
        index = min(range(len(points)), key=lambda i: abs(points[i].x() - x))
        started, value = self._series[index]
        ago = time.time() - started
        when = "ahora" if ago < self._bucket_width() else f"hace {format_elapsed(ago)}"
        number = f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"
        QToolTip.showText(event.globalPosition().toPoint(), f"{number}{self._unit} · {when}", self)

    def _bucket_width(self) -> float:
        if len(self._series) < 2:
            return 10.0
        return self._series[1][0] - self._series[0][0]


class LevelBar(QWidget):
    """Barra horizontal de nivel contra un maximo (ej. ocupacion vs aforo,
    cambio vs umbral). El relleno usa el color de estado que le pasen; la
    marca vertical señala el umbral."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fraction = 0.0
        self._marker: float | None = None
        self._color = QColor(STATUS_OK)
        self.setFixedHeight(8)

    def set_level(self, fraction: float, color: str, marker: float | None = None) -> None:
        self._fraction = max(0.0, min(1.0, fraction))
        self._marker = marker
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        painter.setBrush(QColor(GRID_LINE))
        painter.drawRoundedRect(rect, 4, 4)
        if self._fraction > 0:
            painter.setBrush(self._color)
            painter.drawRoundedRect(
                QRectF(0, 0, max(8.0, rect.width() * self._fraction), rect.height()), 4, 4
            )
        if self._marker is not None:
            x = rect.width() * max(0.0, min(1.0, self._marker))
            painter.setPen(QPen(QColor(TEXT_PRIMARY), 2))
            painter.drawLine(QPointF(x, -1), QPointF(x, rect.height() + 1))


def _build_heat_lut() -> np.ndarray:
    """LUT 256x4 (B, G, R, A) premultiplicada: naranja claro casi
    transparente -> naranja intenso opaco. Los valores bajos (< ~6%) quedan
    transparentes para no velar toda la escena."""
    t = np.linspace(0.0, 1.0, 256)
    light = np.array([253, 186, 116], dtype=np.float64)  # #fdba74
    deep = np.array([234, 88, 12], dtype=np.float64)  # #ea580c
    rgb = light[None, :] * (1 - t[:, None]) + deep[None, :] * t[:, None]
    alpha = np.clip((t - 0.06) / 0.94, 0.0, 1.0) ** 0.8 * 190
    lut = np.zeros((256, 4), dtype=np.uint8)
    lut[:, 0] = np.round(rgb[:, 2] * alpha / 255)
    lut[:, 1] = np.round(rgb[:, 1] * alpha / 255)
    lut[:, 2] = np.round(rgb[:, 0] * alpha / 255)
    lut[:, 3] = np.round(alpha)
    return lut


_HEAT_LUT = _build_heat_lut()


def heatmap_image(grid: np.ndarray) -> QImage | None:
    """Grilla uint8 de densidad (ver core/analytics/heatmap.py) -> QImage
    ARGB premultiplicada del mismo tamaño; se dibuja estirada sobre el
    cuadro con suavizado. Un solo drawImage en vez de cientos de elipses."""
    if grid is None or not isinstance(grid, np.ndarray) or grid.ndim != 2 or grid.size == 0:
        return None
    rgba = np.ascontiguousarray(_HEAT_LUT[grid])
    height, width = grid.shape
    image = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_ARGB32_Premultiplied)
    return image.copy()  # la QImage no puede seguir apuntando al buffer de numpy


def draw_heatmap(painter: QPainter, grid: np.ndarray, target: QRectF) -> bool:
    image = heatmap_image(grid)
    if image is None:
        return False
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(target, image)
    painter.restore()
    return True
