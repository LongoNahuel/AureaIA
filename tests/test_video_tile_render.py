"""Render del tile (2026-09-23): el cuadro se achica con OpenCV antes de
pasar a Qt, conservando proporcion y colores."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor

import aurea_vms.ui.widgets.video_tile as vt_module
from aurea_vms.ui.widgets.video_tile import VideoTile, frame_to_pixmap


def _frame(width: int, height: int) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :] = (255, 0, 0)  # azul en BGR
    return frame


def test_frame_to_pixmap_achica_y_respeta_los_colores(qtbot):
    pixmap = frame_to_pixmap(_frame(2048, 1536), QSize(400, 300))

    assert pixmap.size() == QSize(400, 300)
    pixel = QColor(pixmap.toImage().pixel(200, 150))
    assert (pixel.red(), pixel.green(), pixel.blue()) == (0, 0, 255)


def test_frame_to_pixmap_tambien_agranda(qtbot):
    assert frame_to_pixmap(_frame(320, 240), QSize(640, 480)).size() == QSize(640, 480)


class _Worker:
    def __init__(self, frame):
        self.frame = frame

    def get_latest_frame_with_timestamp(self):
        return self.frame, 1.0

    def is_stale(self):
        return False


@pytest.fixture()
def tile(qtbot, monkeypatch):
    widget = VideoTile(0)
    qtbot.addWidget(widget)
    widget._device = SimpleNamespace(name="Cam", id=5)
    widget._analytics_configs = []
    worker = _Worker(_frame(2048, 1536))
    monkeypatch.setattr(vt_module.stream_manager, "get_worker", lambda *_a, **_k: worker)
    return widget


def test_el_tile_no_convierte_el_cuadro_completo(tile, monkeypatch):
    tamaños: list[tuple[int, int]] = []
    original = vt_module.cv2.cvtColor

    def espiar(image, code):
        tamaños.append(image.shape[:2])
        return original(image, code)

    monkeypatch.setattr(vt_module.cv2, "cvtColor", espiar)
    tile.video_label.resize(640, 400)
    tile._refresh_frame()

    esperado = QSize(2048, 1536).scaled(QSize(640, 400), Qt.AspectRatioMode.KeepAspectRatio)
    assert tile.video_label.pixmap().size() == esperado
    assert tamaños == [(esperado.height(), esperado.width())]


def test_un_tile_sin_tamaño_no_dibuja(tile, monkeypatch):
    # En la app el layout le da minimo 160x90; antes de mostrarse puede
    # medir 0x0 y un pixmap nulo haria que QPainter se queje en la terminal.
    monkeypatch.setattr(tile.video_label, "size", lambda: QSize(0, 0))
    convertidos: list = []
    monkeypatch.setattr(vt_module, "frame_to_pixmap", lambda *a: convertidos.append(a))
    tile._refresh_frame()

    assert convertidos == []
