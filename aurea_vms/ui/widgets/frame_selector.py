"""Widget que muestra un snapshot de una camara y permite dibujar con el
mouse un rectangulo (ROI) o una linea (cruce de linea) sobre el frame.
Traduce coordenadas del widget escalado a pixeles del frame original."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

Point = tuple[int, int]
Rect = tuple[int, int, int, int]

SELECTION_COLOR = QColor("#3b82f6")
DRAG_COLOR = QColor("#facc15")


class FrameSelectorWidget(QLabel):
    def __init__(self, mode: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if mode not in ("rect", "line"):
            raise ValueError("mode debe ser 'rect' o 'line'")
        self._mode = mode
        self._frame: np.ndarray | None = None
        self._pixmap_rect = QRect()
        self._drag_start: QPoint | None = None
        self._drag_current: QPoint | None = None
        self._selection: Rect | None = None
        self._line: tuple[Point, Point] | None = None

        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background-color: #10151c; color: #6b7280; border: 1px solid #2a3441;"
        )
        self.setText("Sin captura")

    def set_frame(self, frame: np.ndarray) -> None:
        self._frame = frame
        self.setText("")
        self.update()

    def set_initial_rect(self, rect: Rect | None) -> None:
        self._selection = rect
        self.update()

    def set_initial_line(self, line: tuple[Point, Point] | None) -> None:
        self._line = line
        self.update()

    def get_rect(self) -> Rect | None:
        return self._selection

    def get_line(self) -> tuple[Point, Point] | None:
        return self._line

    def clear_selection(self) -> None:
        self._selection = None
        self._line = None
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._frame is None:
            super().paintEvent(event)
            return

        painter = QPainter(self)

        rgb = cv2.cvtColor(self._frame, cv2.COLOR_BGR2RGB)
        height, width, _ = rgb.shape
        image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        offset_x = (self.width() - pixmap.width()) // 2
        offset_y = (self.height() - pixmap.height()) // 2
        self._pixmap_rect = QRect(offset_x, offset_y, pixmap.width(), pixmap.height())
        painter.drawPixmap(self._pixmap_rect, pixmap)

        pen = QPen(SELECTION_COLOR, 2)
        painter.setPen(pen)

        if self._mode == "rect" and self._selection is not None:
            rect = self._frame_rect_to_widget(self._selection)
            if rect is not None:
                painter.drawRect(rect)
        elif self._mode == "line" and self._line is not None:
            p1 = self._frame_point_to_widget(self._line[0])
            p2 = self._frame_point_to_widget(self._line[1])
            if p1 is not None and p2 is not None:
                painter.drawLine(p1, p2)

        if self._drag_start is not None and self._drag_current is not None:
            painter.setPen(QPen(DRAG_COLOR, 2))
            if self._mode == "rect":
                painter.drawRect(QRect(self._drag_start, self._drag_current).normalized())
            else:
                painter.drawLine(self._drag_start, self._drag_current)

        painter.end()

    def _widget_point_to_frame(self, point: QPoint) -> Point | None:
        if self._frame is None or self._pixmap_rect.isNull():
            return None
        clamped = QPoint(
            min(max(point.x(), self._pixmap_rect.left()), self._pixmap_rect.right()),
            min(max(point.y(), self._pixmap_rect.top()), self._pixmap_rect.bottom()),
        )
        height, width = self._frame.shape[:2]
        rel_x = (clamped.x() - self._pixmap_rect.x()) / self._pixmap_rect.width()
        rel_y = (clamped.y() - self._pixmap_rect.y()) / self._pixmap_rect.height()
        return int(rel_x * width), int(rel_y * height)

    def _frame_point_to_widget(self, point: Point) -> QPoint | None:
        if self._frame is None or self._pixmap_rect.isNull():
            return None
        height, width = self._frame.shape[:2]
        x = self._pixmap_rect.x() + (point[0] / width) * self._pixmap_rect.width()
        y = self._pixmap_rect.y() + (point[1] / height) * self._pixmap_rect.height()
        return QPoint(int(x), int(y))

    def _frame_rect_to_widget(self, rect: Rect) -> QRectF | None:
        x, y, w, h = rect
        top_left = self._frame_point_to_widget((x, y))
        bottom_right = self._frame_point_to_widget((x + w, y + h))
        if top_left is None or bottom_right is None:
            return None
        return QRectF(QPointF(top_left), QPointF(bottom_right))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._frame is None:
            return
        self._drag_start = event.position().toPoint()
        self._drag_current = self._drag_start
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is None:
            return
        self._drag_current = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is None:
            return
        end_point = event.position().toPoint()
        start_frame = self._widget_point_to_frame(self._drag_start)
        end_frame = self._widget_point_to_frame(end_point)

        self._drag_start = None
        self._drag_current = None

        if start_frame is not None and end_frame is not None:
            if self._mode == "rect":
                x1, y1 = start_frame
                x2, y2 = end_frame
                x, y = min(x1, x2), min(y1, y2)
                w, h = abs(x2 - x1), abs(y2 - y1)
                if w > 4 and h > 4:
                    self._selection = (x, y, w, h)
            elif start_frame != end_frame:
                self._line = (start_frame, end_frame)

        self.update()
