"""Panel de la Vista Inteligente: galeria en vivo de rostros detectados,
recortados del frame en el momento de la deteccion. Es efimera (solo en
memoria durante la sesion, no se persiste a disco) -- para eso ya existen
los snapshots de alarma cuando hay una regla de Deteccion Facial activa.

Quien es quien, cuantos van y que toma se guarda de cada identidad lo
decide core/face_catalog.py: este widget solo refleja en su QListWidget lo
que el catalogo le devuelve. Las dos listas van en el mismo orden (mas
reciente primero) y por eso los indices coinciden. Configurable por camara
desde Analizadores > Detección Facial.

Cada captura lleva quemada una franja inferior con hora y porcentaje de
certeza de la deteccion -- igual que un sello de metadata en video de
seguridad -- para que la miniatura sea autocontenida al exportarla o
inspeccionarla sin depender del tooltip/texto de la lista."""

from __future__ import annotations

import datetime as dt

import cv2
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, HeaderCardWidget, TransparentToolButton

from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent
from aurea_vms.core.face_catalog import (
    FaceCapture,
    FaceCatalog,
    FaceCatalogSettings,
    clamp_bbox,
    face_signature,
    geometry_signature,
)
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository

THUMB_SIZE = QSize(220, 220)


def _cover_scaled(pixmap: QPixmap, size: QSize) -> QPixmap:
    """Escala tipo "cover" (llena el cuadro sin deformar) y recorta el
    centro al tamaño exacto pedido. Un recorte de cara casi nunca es
    cuadrado -- si solo se escala con KeepAspectRatioByExpanding sin
    recortar, el pixmap resultante queda mas ancho o mas alto que
    `size`, y el icono del ListWidget lo vuelve a achicar para que entre
    en el iconSize cuadrado: el resultado visual es una tira angosta y
    deformada en vez de una cara reconocible."""
    scaled = pixmap.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - size.width()) // 2)
    y = max(0, (scaled.height() - size.height()) // 2)
    return scaled.copy(x, y, size.width(), size.height())


def _with_metadata_overlay(pixmap: QPixmap, when: str, confidence: float) -> QPixmap:
    """Quema una franja inferior semitransparente con hora y % de certeza
    sobre la miniatura, tipo sello de metadata de video de seguridad."""
    stamped = QPixmap(pixmap)
    bar_height = max(20, stamped.height() // 6)
    bar_rect = QRect(0, stamped.height() - bar_height, stamped.width(), bar_height)

    painter = QPainter(stamped)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.fillRect(bar_rect, QColor(0, 0, 0, 170))
    font = painter.font()
    font.setPixelSize(max(11, bar_height - 8))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    text = f"{when}  ·  {confidence:.0%}"
    painter.drawText(
        bar_rect.adjusted(6, 0, -6, 0),
        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
        text,
    )
    painter.end()
    return stamped


class FaceGallery(HeaderCardWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Detecciones Faciales")
        self._device_id: int | None = None
        self._catalog = FaceCatalog()

        content = QWidget(self)
        self.viewLayout.addWidget(content)

        counter_row = QHBoxLayout()
        self.counter_label = CaptionLabel("IDs catalogados: 0")
        counter_row.addWidget(self.counter_label)
        counter_row.addStretch(1)
        clear_button = TransparentToolButton(FluentIcon.BROOM)
        clear_button.setToolTip("Limpiar contador")
        clear_button.clicked.connect(self._clear_counter)
        counter_row.addWidget(clear_button)

        # QListWidget liso, NO el ListWidget de qfluentwidgets: ese trae una
        # hoja de estilo propia que fija "height: 35px" en cada item (pensada
        # para filas de menu compactas, no para una grilla de fotos), y
        # pisaba el iconSize sin importar que tan grande se pidiera --
        # resultado, miniaturas achatadas en una tira angosta.
        self.list_widget = QListWidget(content)
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setIconSize(THUMB_SIZE)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setSpacing(6)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { border: none; }"
        )

        layout = QVBoxLayout(content)
        layout.addLayout(counter_row)
        layout.addWidget(self.list_widget)

        event_bus.detection.connect(self._on_detection, Qt.ConnectionType.QueuedConnection)

    def set_device(self, device_id: int | None) -> None:
        self._device_id = device_id
        self.list_widget.clear()
        self._catalog.reset()
        self._refresh_counter()

    def _clear_counter(self) -> None:
        self._catalog.clear_counter()
        self._refresh_counter()

    def _refresh_counter(self) -> None:
        self.counter_label.setText(f"IDs catalogados: {self._catalog.total_count}")

    def _face_params(self) -> dict:
        if self._device_id is None:
            return {}
        config = repository.get_analytics_config_for(self._device_id, "face_detection")
        return (config.params if config else {}) or {}

    def _on_detection(self, event: DetectionEvent) -> None:
        """Slot con QueuedConnection: corre en el hilo de la GUI. Acá solo
        queda recortar el frame y pintar; quién es quién lo decide el
        catálogo."""
        if event.device_id != self._device_id or event.analyzer_name != "face_detection":
            return

        faces = [d for d in event.detections if d.label == "cara"]
        if not faces:
            return

        worker = stream_manager.get_worker(event.device_id)
        frame = worker.get_latest_frame() if worker else None
        if frame is None:
            return

        settings = FaceCatalogSettings.from_params(self._face_params())
        if self._catalog.apply_daily_reset(settings):
            self._refresh_counter()

        height, width = frame.shape[:2]
        when = dt.datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S")

        for det in faces:
            box = clamp_bbox(det.bbox, width, height)
            if box is None:
                continue
            x0, y0, x1, y1 = box
            crop = frame[y0:y1, x0:x1]

            update = self._catalog.observe(
                signature=face_signature(crop),
                geometry=geometry_signature(det.keypoints),
                area=(x1 - x0) * (y1 - y0),
                confidence=det.confidence,
                settings=settings,
            )
            # El orden importa: el catálogo saca la fila vieja ANTES de
            # insertar la nueva en la posición 0, y las dos listas tienen
            # que quedar con los mismos índices.
            if update.removed_index is not None:
                self.list_widget.takeItem(update.removed_index)
            if update.capture is None:
                continue
            self._insert_capture(crop, update.capture, when)
            if update.is_new_identity:
                self._refresh_counter()

        for _ in range(self._catalog.prune()):
            self.list_widget.takeItem(self.list_widget.count() - 1)

    def _insert_capture(self, crop, capture: FaceCapture, when: str) -> None:
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        ch, cw = rgb.shape[:2]
        image = QImage(rgb.data, cw, ch, 3 * cw, QImage.Format.Format_RGB888)
        pixmap = _cover_scaled(QPixmap.fromImage(image), THUMB_SIZE)
        pixmap = _with_metadata_overlay(pixmap, when, capture.confidence)
        self.list_widget.insertItem(0, QListWidgetItem(QIcon(pixmap), f"ID #{capture.track_id}"))
