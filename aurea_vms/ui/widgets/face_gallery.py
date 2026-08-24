"""Panel de la Vista Inteligente: galeria en vivo de rostros detectados,
recortados del frame en el momento de la deteccion. Es efimera (solo en
memoria durante la sesion, no se persiste a disco) -- para eso ya existen
los snapshots de alarma cuando hay una regla de Deteccion Facial activa.

Una sola captura por rostro distinto (o hasta "Capturas por rostro" si se
configura mas de una): cada deteccion se compara contra las ya
capturadas (via una firma chica en escala de grises -- no es
reconocimiento real, solo "se parece o no a algo que ya esta en la
galeria"). El umbral de diferencia y el maximo de capturas por rostro son
configurables por camara desde Analizadores > Detección Facial, junto con
un contador acumulado de capturas (con reinicio diario programable)."""

from __future__ import annotations

import datetime as dt

import cv2
import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, HeaderCardWidget, ListWidget, TransparentToolButton

from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository

THUMB_SIZE = QSize(72, 72)
MAX_ITEMS = 24
SIGNATURE_SIZE = 24
DEFAULT_DIFF_THRESHOLD = 0.35
DEFAULT_MAX_CAPTURES_PER_FACE = 1
DEFAULT_COUNTING_ENABLED = True
DEFAULT_COUNTING_RESET_TIME = "00:00"


def _face_signature(crop_bgr: np.ndarray) -> np.ndarray:
    """Firma chica y liviana de un recorte de cara: escala de grises,
    24x24, ecualizada (para amortiguar diferencias de iluminación). No es
    un embedding de reconocimiento facial, solo alcanza para comparar
    "se parece a una captura ya guardada" contra las pocas que hay en la
    galería."""
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (SIGNATURE_SIZE, SIGNATURE_SIZE))
    equalized = cv2.equalizeHist(resized)
    return equalized.astype(np.float32) / 255.0


def _difference(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


class FaceGallery(HeaderCardWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Detecciones Faciales")
        self._device_id: int | None = None
        self._total_count = 0
        self._last_reset_date: dt.date | None = None

        content = QWidget(self)
        self.viewLayout.addWidget(content)

        counter_row = QHBoxLayout()
        self.counter_label = CaptionLabel("Capturas: 0")
        counter_row.addWidget(self.counter_label)
        counter_row.addStretch(1)
        clear_button = TransparentToolButton(FluentIcon.BROOM)
        clear_button.setToolTip("Limpiar contador")
        clear_button.clicked.connect(self._clear_counter)
        counter_row.addWidget(clear_button)

        self.list_widget = ListWidget(content)
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setIconSize(THUMB_SIZE)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setSpacing(4)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)

        layout = QVBoxLayout(content)
        layout.addLayout(counter_row)
        layout.addWidget(self.list_widget)

        event_bus.detection.connect(self._on_detection, Qt.ConnectionType.QueuedConnection)

    def set_device(self, device_id: int | None) -> None:
        self._device_id = device_id
        self.list_widget.clear()
        self._total_count = 0
        self._last_reset_date = None
        self.counter_label.setText("Capturas: 0")

    def _clear_counter(self) -> None:
        self._total_count = 0
        self._last_reset_date = dt.date.today()
        self.counter_label.setText("Capturas: 0")

    def _face_params(self) -> dict:
        if self._device_id is None:
            return {}
        config = repository.get_analytics_config_for(self._device_id, "face_detection")
        return (config.params if config else {}) or {}

    def _apply_daily_reset(self, params: dict) -> None:
        if not params.get("counting_enabled", DEFAULT_COUNTING_ENABLED):
            return
        reset_time_text = params.get("counting_reset_time", DEFAULT_COUNTING_RESET_TIME)
        try:
            reset_hour, reset_minute = (int(part) for part in reset_time_text.split(":")[:2])
        except (ValueError, AttributeError):
            reset_hour, reset_minute = 0, 0

        now = dt.datetime.now()
        reset_moment_today = now.replace(hour=reset_hour, minute=reset_minute, second=0, microsecond=0)
        if now >= reset_moment_today and self._last_reset_date != now.date():
            self._total_count = 0
            self._last_reset_date = now.date()
            self.counter_label.setText("Capturas: 0")

    def _find_cluster(self, signature: np.ndarray, threshold: float) -> int:
        """Cuantas capturas ya existen en la galería lo bastante parecidas
        a esta firma (0 si no hay ninguna -- rostro nuevo)."""
        count = 0
        for i in range(self.list_widget.count()):
            data = self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            if data is not None and _difference(signature, data["signature"]) < threshold:
                count = max(count, data["count"])
        return count

    def _on_detection(self, event: DetectionEvent) -> None:
        if event.device_id != self._device_id or event.analyzer_name != "face_detection":
            return

        faces = [d for d in event.detections if d.label == "cara"]
        if not faces:
            return

        worker = stream_manager.get_worker(event.device_id)
        frame = worker.get_latest_frame() if worker else None
        if frame is None:
            return

        params = self._face_params()
        self._apply_daily_reset(params)
        threshold = params.get("capture_diff_threshold", DEFAULT_DIFF_THRESHOLD)
        max_captures = params.get("max_captures_per_face", DEFAULT_MAX_CAPTURES_PER_FACE)
        counting_enabled = params.get("counting_enabled", DEFAULT_COUNTING_ENABLED)

        height, width = frame.shape[:2]
        when = dt.datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S")

        for det in faces:
            x, y, w, h = det.bbox
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(x + w, width), min(y + h, height)
            if x1 <= x0 or y1 <= y0:
                continue

            crop = frame[y0:y1, x0:x1]
            signature = _face_signature(crop)
            already_captured = self._find_cluster(signature, threshold)
            if already_captured >= max_captures:
                continue

            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            ch, cw = rgb.shape[:2]
            image = QImage(rgb.data, cw, ch, 3 * cw, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(image).scaled(
                THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            item = QListWidgetItem(QIcon(pixmap), when)
            item.setData(Qt.ItemDataRole.UserRole, {"signature": signature, "count": already_captured + 1})
            self.list_widget.insertItem(0, item)

            if counting_enabled:
                self._total_count += 1
                self.counter_label.setText(f"Capturas: {self._total_count}")

        while self.list_widget.count() > MAX_ITEMS:
            self.list_widget.takeItem(self.list_widget.count() - 1)
