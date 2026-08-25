"""Fondo con un video local en loop (usado en el launcher de Inicio) --
misma composicion "cover" + velo oscuro que BrandedBackground, pero
pintando frames de video en vez de una imagen fija.

El archivo es local (no RTSP): leer un frame es cuestion de milisegundos,
asi que a diferencia de StreamWorker no hace falta un thread aparte --
alcanza con un QTimer en el hilo principal. El timer se pausa cuando el
widget no esta visible (ej. otra pestaña activa) para no gastar CPU
decodificando un video que nadie ve."""

from __future__ import annotations

import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

DEFAULT_OVERLAY = QColor(10, 14, 22, 165)


class VideoBackground(QWidget):
    def __init__(
        self,
        video_path: str,
        overlay: QColor = DEFAULT_OVERLAY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._overlay = overlay
        self._pixmap = QPixmap()

        self._capture = cv2.VideoCapture(video_path)
        fps = self._capture.get(cv2.CAP_PROP_FPS) or 30.0

        self._timer = QTimer(self)
        self._timer.setInterval(max(1, int(1000 / fps)))
        self._timer.timeout.connect(self._advance_frame)
        self._advance_frame()
        if self._capture.isOpened():
            self._timer.start()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if self._capture.isOpened():
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._timer.stop()
        super().hideEvent(event)

    def _advance_frame(self) -> None:
        ok, frame = self._capture.read()
        if not ok:
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._capture.read()
            if not ok:
                return

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, _ = rgb.shape
        image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.fillRect(self.rect(), self._overlay)
        else:
            painter.fillRect(self.rect(), QColor("#161c26"))
        super().paintEvent(event)
