"""Widget generico que pinta una imagen de fondo (recortada para llenar
el area, tipo "background-size: cover") con un velo oscuro encima para
que el contenido que se apoye arriba siga siendo legible."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

DEFAULT_OVERLAY = QColor(10, 14, 22, 165)


class BrandedBackground(QWidget):
    def __init__(
        self,
        pixmap: QPixmap,
        overlay: QColor = DEFAULT_OVERLAY,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._overlay = overlay

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
