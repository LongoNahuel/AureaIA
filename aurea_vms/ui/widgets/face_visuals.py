"""Helpers de imagen de rostros compartidos por la galeria, la tira del
dashboard y el visor forense."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap


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


def _rounded(pixmap: QPixmap, radius: float) -> QPixmap:
    """Esquinas redondeadas con un filo de 1 px de la superficie: la foto
    se lee como una tarjeta, no como un recorte pegado."""
    result = QPixmap(pixmap.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(result.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.setClipping(False)
    painter.setPen(QColor(255, 255, 255, 28))
    painter.drawPath(path)
    painter.end()
    return result


def _bgr_to_pixmap(image: np.ndarray) -> QPixmap:
    rgb = np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    height, width = rgb.shape[:2]
    qimage = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


def record_pixmap(record, size: QSize, radius: float) -> QPixmap:
    """Miniatura redondeada de una toma, cacheada en el registro por
    tamaño: las vistas se redibujan seguido y reescalar en cada pasada no
    tiene sentido."""
    key = (size.width(), size.height(), radius)
    pixmap = record.pixmaps.get(key)
    if pixmap is None:
        source = _bgr_to_pixmap(record.image)
        pixmap = _rounded(_cover_scaled(source, size), radius)
        record.pixmaps[key] = pixmap
    return pixmap


def quality_label(quality: float | None) -> str:
    if quality is None:
        return "Calidad sin medir"
    if quality >= 0.7:
        return f"Calidad alta · {quality:.0%}"
    if quality >= 0.5:
        return f"Calidad media · {quality:.0%}"
    return f"Calidad baja · {quality:.0%}"
