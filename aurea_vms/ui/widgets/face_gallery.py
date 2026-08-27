"""Panel de la Vista Inteligente: galeria en vivo de rostros detectados,
recortados del frame en el momento de la deteccion. Es efimera (solo en
memoria durante la sesion, no se persiste a disco) -- para eso ya existen
los snapshots de alarma cuando hay una regla de Deteccion Facial activa.

Un ID catalogado por rostro distinto, una sola captura por ID: cada
deteccion se compara contra las ya capturadas con dos firmas combinadas
-- una chica en escala de grises (apariencia) y otra geometrica, a partir
de las distancias entre los 6 puntos de referencia que ya da el detector
(ojos/nariz/boca/orejas), normalizadas por la distancia entre ojos para
que no dependa de que tan cerca este la cara. Ninguna de las dos es
reconocimiento real (no hay un embedding aprendido), pero combinar forma
+ apariencia es bastante mas robusto a cambios de luz o gesto que
comparar pixeles solos.

Cada ID guarda hasta "Capturas por rostro" miniaturas (1 = una sola toma
por persona, configurable hasta 5). Mientras no se llega al tope, una
deteccion nueva que coincide con un ID ya catalogado suma una miniatura
mas a esa identidad; al llegar al tope, la miniatura MAS CHICA de esa
identidad se reemplaza por la nueva SOLO si el recorte entrante es mas
grande -- asi con tope 1 la galeria termina mostrando la mejor toma de
cada persona (nunca la primera que se vio, tipicamente chica y lejana), y
con un tope mayor guarda varias buenas tomas en vez de una sola. El
umbral de que tan distinto tiene que verse un rostro para catalogarlo
como un ID nuevo es configurable por camara desde Analizadores >
Detección Facial, junto con un contador acumulado de IDs (con reinicio
diario programable).

Cada captura lleva quemada una franja inferior con hora y porcentaje de
certeza de la deteccion -- igual que un sello de metadata en video de
seguridad -- para que la miniatura sea autocontenida al exportarla o
inspeccionarla sin depender del tooltip/texto de la lista."""

from __future__ import annotations

import datetime as dt

import cv2
import numpy as np
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon, HeaderCardWidget, TransparentToolButton

from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository

THUMB_SIZE = QSize(220, 220)
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


def _geometry_signature(keypoints: tuple[tuple[float, float], ...] | None) -> np.ndarray | None:
    """Distancias entre cada par de los 6 puntos de referencia (ojo der,
    ojo izq, nariz, boca, oreja der, oreja izq), normalizadas por la
    distancia entre ojos -- da una firma de "forma" de la cara que no
    depende de que tan cerca/lejos este de la camara."""
    if not keypoints or len(keypoints) < 6:
        return None
    points = np.array(keypoints, dtype=np.float32)
    eye_distance = float(np.linalg.norm(points[0] - points[1]))
    if eye_distance < 1e-3:
        return None
    pairs = [(i, j) for i in range(len(points)) for j in range(i + 1, len(points))]
    return np.array(
        [np.linalg.norm(points[i] - points[j]) / eye_distance for i, j in pairs], dtype=np.float32
    )


def _difference(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


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


def _combined_difference(a: dict, b: dict) -> float:
    """Combina apariencia (firma de pixeles) y forma (firma geometrica,
    si ambas capturas la tienen) tomando el MAXIMO de las dos, no un
    promedio: si cualquiera de las dos señales ya muestra una diferencia
    clara, tiene que pesar como tal -- promediar dejaba que una firma
    parecida "diluyera" a la otra aunque fuera claramente distinta (caso
    real: dos personas con recortes de piel/fondo similares por
    casualidad, pero geometria facial bien distinta, terminaban
    matcheando como el mismo ID)."""
    pixel_diff = _difference(a["signature"], b["signature"])
    geo_a, geo_b = a.get("geometry"), b.get("geometry")
    if geo_a is not None and geo_b is not None:
        geo_diff = _difference(geo_a, geo_b)
        return max(pixel_diff, geo_diff)
    return pixel_diff


class FaceGallery(HeaderCardWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Detecciones Faciales")
        self._device_id: int | None = None
        self._total_count = 0
        self._next_track_id = 1
        self._last_reset_date: dt.date | None = None

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
        self._total_count = 0
        self._next_track_id = 1
        self._last_reset_date = None
        self.counter_label.setText("IDs catalogados: 0")

    def _clear_counter(self) -> None:
        self._total_count = 0
        self._last_reset_date = dt.date.today()
        self.counter_label.setText("IDs catalogados: 0")

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
        reset_moment_today = now.replace(
            hour=reset_hour, minute=reset_minute, second=0, microsecond=0
        )
        if now >= reset_moment_today and self._last_reset_date != now.date():
            self._total_count = 0
            self._last_reset_date = now.date()
            self.counter_label.setText("IDs catalogados: 0")

    def _find_matches(self, candidate: dict, threshold: float) -> list[int]:
        """Filas de la galería que ya pertenecen a la misma identidad que
        este candidato (vacío si no coincide con ninguna -- ID nuevo; puede
        haber varias si "Capturas por rostro" > 1)."""
        return [
            i
            for i in range(self.list_widget.count())
            if (data := self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)) is not None
            and _combined_difference(candidate, data) < threshold
        ]

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
            candidate = {
                "signature": _face_signature(crop),
                "geometry": _geometry_signature(det.keypoints),
                "area": (x1 - x0) * (y1 - y0),
                "confidence": det.confidence,
            }

            match_rows = self._find_matches(candidate, threshold)
            if match_rows:
                matches = [
                    (i, self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)) for i in match_rows
                ]
                track_id = matches[0][1]["track_id"]
                if len(matches) < max_captures:
                    pass  # todavia no llegamos al tope: se suma como ranura nueva
                else:
                    smallest_row, smallest = min(matches, key=lambda pair: pair[1]["area"])
                    if candidate["area"] <= smallest["area"]:
                        continue  # ya tenemos "max_captures" tomas iguales o mejores de este ID
                    self.list_widget.takeItem(smallest_row)
            else:
                track_id = self._next_track_id
                self._next_track_id += 1
                if counting_enabled:
                    self._total_count += 1
                    self.counter_label.setText(f"IDs catalogados: {self._total_count}")

            candidate["track_id"] = track_id
            self._insert_capture(crop, candidate, track_id, when)

        while self.list_widget.count() > MAX_ITEMS:
            self.list_widget.takeItem(self.list_widget.count() - 1)

    def _insert_capture(self, crop: np.ndarray, candidate: dict, track_id: int, when: str) -> None:
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        ch, cw = rgb.shape[:2]
        image = QImage(rgb.data, cw, ch, 3 * cw, QImage.Format.Format_RGB888)
        pixmap = _cover_scaled(QPixmap.fromImage(image), THUMB_SIZE)
        pixmap = _with_metadata_overlay(pixmap, when, candidate.get("confidence", 0.0))
        item = QListWidgetItem(QIcon(pixmap), f"ID #{track_id}")
        item.setData(Qt.ItemDataRole.UserRole, candidate)
        self.list_widget.insertItem(0, item)
