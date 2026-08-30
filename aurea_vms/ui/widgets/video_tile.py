"""Tile de la grilla de Vista en Vivo: renderiza el ultimo frame de su
StreamWorker, con overlay OSD (nombre de camara + hora) y las detecciones
del analizador activo. La camara se asigna arrastrando un item desde
DeviceTreeWidget o con doble click (al tile seleccionado); un click en el
tile lo marca como "seleccionado" (borde de acento) para ese flujo."""

from __future__ import annotations

import datetime as dt

import cv2
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import Action, FluentIcon, RoundMenu

from aurea_vms.config.settings import settings
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import Detection, DetectionEvent
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository
from aurea_vms.models.device import Device
from aurea_vms.ui import icons
from aurea_vms.ui.labels import display_class
from aurea_vms.ui.theme import STATUS_COLORS
from aurea_vms.ui.widgets.device_tree import DEVICE_ID_MIME

BORDER_IDLE = "#2a3441"
BORDER_SELECTED = "#3b82f6"

# Marca inteligente: siempre verde, pero con dos tratamientos distintos --
# movimiento dibuja la silueta real (contorno simplificado) porque un
# rectangulo es poco preciso para una forma irregular; el resto de los
# analizadores (personas/rostros/cruce de linea) dibuja una caja con
# esquinas redondeadas + chip de etiqueta, mas prolijo que texto suelto.
MOTION_STROKE = QColor("#22c55e")
MOTION_FILL = QColor(34, 197, 94, 55)
DETECTION_STROKE = QColor("#22c55e")
DETECTION_FILL = QColor(34, 197, 94, 40)
LABEL_CHIP_BG = QColor(12, 20, 15, 220)
LABEL_CHIP_TEXT = QColor("#eafff2")


class _VideoDisplay(QLabel):
    """QLabel cuyo sizeHint NO depende del pixmap actual.

    Por default, QLabel.sizeHint()/minimumSizeHint() devuelven el tamano
    del pixmap cargado. Como el pixmap se reemplaza en cada frame (a veces
    desde resizeEvent, para el estado vacio), eso arma un loop de
    realimentacion: resize -> nuevo pixmap -> nuevo sizeHint -> el layout
    vuelve a resizear el label -> ... y la ventana "crece sola" de a poco.
    Fijar un sizeHint constante corta el loop; el tamano real lo sigue
    determinando el layout (grilla/QSizePolicy.Expanding), no el video.
    """

    def sizeHint(self) -> QSize:  # noqa: N802 - override de Qt
        return QSize(160, 90)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - override de Qt
        return QSize(160, 90)


class VideoTile(QWidget):
    clicked = Signal(object)  # emite self
    doubleClicked = Signal(object)  # emite self
    device_assigned = Signal(object)  # emite device_id o None

    def __init__(self, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = index
        self._device: Device | None = None
        self._latest_detections: tuple[Detection, ...] = ()
        self._selected = False
        # True mientras el pixmap actual es el estado "sin señal": evita
        # redibujar las rayas en cada tick del timer de display.
        self._offline_rendered = False
        # Tiles de grilla (index >= 0) arrancan en sub-flujo (liviano, para
        # miniaturas); el tile de Vista Inteligente (index < 0) y cualquier
        # tile expandido con doble click usan el flujo principal.
        self._stream_kind = "main" if index < 0 else "sub"

        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        self.video_label = _VideoDisplay(self)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(160, 90)
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.addWidget(self.video_label)

        self._timer = QTimer(self)
        self._timer.setInterval(max(1, int(1000 / settings.display_fps)))
        self._timer.timeout.connect(self._refresh_frame)
        self._timer.start()

        event_bus.detection.connect(self._on_detection, Qt.ConnectionType.QueuedConnection)

        self._apply_border()
        self._render_empty_state()

    # --- asignacion de camara -------------------------------------------------

    def assign_device(self, device_id: int | None) -> None:
        self.release()
        self._latest_detections = ()

        device = repository.get_device(device_id) if device_id is not None else None
        if device is None:
            self._render_empty_state()
            self.device_assigned.emit(None)
            return

        self._device = device
        stream_manager.acquire(device, self._stream_kind)
        self.device_assigned.emit(device.id)

    def release(self) -> None:
        if self._device is not None:
            stream_manager.release(self._device.id, self._stream_kind)
            self._device = None

    def has_device(self) -> bool:
        return self._device is not None

    @property
    def device_id(self) -> int | None:
        return self._device.id if self._device is not None else None

    def set_stream_kind(self, kind: str) -> None:
        """Cambia entre flujo "main" y "sub" para la camara ya asignada
        (usado al expandir/colapsar un tile con doble click)."""
        if kind == self._stream_kind:
            return
        if self._device is not None:
            stream_manager.release(self._device.id, self._stream_kind)
            self._stream_kind = kind
            stream_manager.acquire(self._device, self._stream_kind)
        else:
            self._stream_kind = kind

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_border()

    def _apply_border(self) -> None:
        color = BORDER_SELECTED if self._selected else BORDER_IDLE
        width = 2 if self._selected else 1
        self.video_label.setStyleSheet(
            f"background-color: #10151c; border: {width}px solid {color};"
        )

    # --- interaccion -------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.clicked.emit(self)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.doubleClicked.emit(self)
        super().mouseDoubleClickEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasFormat(DEVICE_ID_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        data = bytes(event.mimeData().data(DEVICE_ID_MIME))
        try:
            device_id = int(data.decode())
        except ValueError:
            return
        self.clicked.emit(self)
        self.assign_device(device_id)
        event.acceptProposedAction()

    def resizeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if self._device is None:
            self._render_empty_state()
        self._offline_rendered = False  # el proximo tick redibuja al tamaño nuevo
        super().resizeEvent(event)

    def _show_context_menu(self, pos) -> None:
        if self._device is None:
            return
        menu = RoundMenu(parent=self)
        action = Action(FluentIcon.CLOSE, "Quitar cámara")
        action.triggered.connect(lambda: self.assign_device(None))
        menu.addAction(action)
        menu.exec(self.mapToGlobal(pos))

    # --- render -------------------------------------------------

    def _on_detection(self, event: DetectionEvent) -> None:
        if self._device is not None and event.device_id == self._device.id:
            self._latest_detections = event.detections

    def _render_empty_state(self) -> None:
        size = self.video_label.size()
        if size.width() < 10 or size.height() < 10:
            return

        pixmap = QPixmap(size)
        pixmap.fill(QColor("#10151c"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        logo_size = max(32, min(72, min(size.width(), size.height()) // 3))
        icon_pixmap = icons.app_logo_pixmap(logo_size)
        icon_x = (size.width() - icon_pixmap.width()) // 2
        icon_y = (size.height() - icon_pixmap.height()) // 2 - 10
        painter.setOpacity(0.85)
        painter.drawPixmap(icon_x, icon_y, icon_pixmap)
        painter.setOpacity(1.0)

        painter.setPen(QColor("#565f6c"))
        text_rect = QRectF(0, icon_y + icon_pixmap.height() + 8, size.width(), 20)
        label = f"Ventana {self._index + 1}" if self._index >= 0 else "Sin cámara"
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

        painter.end()
        self.video_label.setPixmap(pixmap)

    def _render_offline_state(self) -> None:
        """Camara asignada sin señal: patron rayado diagonal (spec NOVA)
        para distinguir "stream caido" de "video oscuro" de un vistazo."""
        size = self.video_label.size()
        if size.width() < 10 or size.height() < 10:
            return

        pixmap = QPixmap(size)
        pixmap.fill(QColor("#10151c"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(QColor(110, 118, 129, 22))
        pen.setWidth(5)
        painter.setPen(pen)
        step = 18
        for x in range(-size.height(), size.width(), step):
            painter.drawLine(x, size.height(), x + size.height(), 0)

        center_y = size.height() // 2
        painter.setPen(QColor(STATUS_COLORS["offline"]))
        painter.drawText(
            QRectF(0, center_y - 22, size.width(), 20), Qt.AlignmentFlag.AlignCenter, "Sin señal"
        )
        painter.setPen(QColor("#9aa3af"))
        name = self._device.name if self._device is not None else ""
        painter.drawText(
            QRectF(0, center_y + 2, size.width(), 20),
            Qt.AlignmentFlag.AlignCenter,
            f"{name} — reconectando…",
        )
        painter.end()
        self.video_label.setPixmap(pixmap)

    def _refresh_frame(self) -> None:
        if self._device is None:
            return

        worker = stream_manager.get_worker(self._device.id, self._stream_kind)
        frame = worker.get_latest_frame() if worker else None
        if frame is None or worker.is_stale():
            # Antes quedaba el ultimo frame congelado, que parece en vivo --
            # exactamente lo que is_stale() existia para evitar.
            if not self._offline_rendered:
                self._offline_rendered = True
                self._render_offline_state()
            return
        self._offline_rendered = False

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, _ = rgb.shape
        image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image).scaled(
            self.video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        pixmap = self._draw_overlay(pixmap, width, height, self._latest_detections)
        self.video_label.setPixmap(pixmap)

    def _draw_overlay(
        self, pixmap: QPixmap, frame_w: int, frame_h: int, detections: tuple[Detection, ...]
    ) -> QPixmap:
        result = QPixmap(pixmap)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        self._draw_osd_text(
            painter,
            result.width(),
            8,
            Qt.AlignmentFlag.AlignLeft,
            self._device.name if self._device else "",
        )
        timestamp = dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self._draw_osd_text(painter, result.width(), 8, Qt.AlignmentFlag.AlignRight, timestamp)

        if detections:
            scale_x = result.width() / frame_w
            scale_y = result.height() / frame_h
            for det in detections:
                if det.polygon:
                    self._draw_motion_mark(painter, det.polygon, scale_x, scale_y)
                else:
                    self._draw_detection_box(painter, det, scale_x, scale_y)

        painter.end()
        return result

    @staticmethod
    def _draw_motion_mark(
        painter: QPainter, polygon: tuple[tuple[int, int], ...], scale_x: float, scale_y: float
    ) -> None:
        if len(polygon) < 3:
            return
        path = QPainterPath()
        points = [QPointF(px * scale_x, py * scale_y) for px, py in polygon]
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        path.closeSubpath()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(MOTION_FILL)
        painter.drawPath(path)

        pen = QPen(MOTION_STROKE)
        pen.setWidthF(1.6)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawPath(path)

    def _draw_detection_box(
        self, painter: QPainter, det: Detection, scale_x: float, scale_y: float
    ) -> None:
        x, y, w, h = det.bbox
        rect = QRectF(x * scale_x, y * scale_y, w * scale_x, h * scale_y)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(DETECTION_FILL)
        painter.drawRoundedRect(rect, 4, 4)

        pen = QPen(DETECTION_STROKE)
        pen.setWidthF(1.8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 4, 4)

        self._draw_label_chip(
            painter, rect.topLeft(), f"{display_class(det.label)} {det.confidence:.0%}"
        )

    @staticmethod
    def _draw_label_chip(painter: QPainter, top_left: QPointF, text: str) -> None:
        metrics = painter.fontMetrics()
        chip_w = metrics.horizontalAdvance(text) + 10
        chip_h = metrics.height() + 4
        chip_rect = QRectF(top_left.x(), top_left.y() - chip_h - 2, chip_w, chip_h)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(LABEL_CHIP_BG)
        painter.drawRoundedRect(chip_rect, 3, 3)

        painter.setPen(LABEL_CHIP_TEXT)
        painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, text)

    @staticmethod
    def _draw_osd_text(
        painter: QPainter, width: int, y: int, alignment: Qt.AlignmentFlag, text: str
    ) -> None:
        if not text:
            return
        rect = QRectF(6, y, width - 12, 16)
        shadow_rect = rect.translated(1, 1)
        painter.setPen(QColor(0, 0, 0, 200))
        painter.drawText(shadow_rect, int(alignment), text)
        painter.setPen(QColor("#f2f2f2"))
        painter.drawText(rect, int(alignment), text)
