"""Render del tile (2026-09-23): el cuadro se achica con OpenCV antes de
pasar a Qt, conservando proporcion y colores."""

from __future__ import annotations

import time
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


class _Stream:
    """Worker de prueba: el cuadro que se le diga, con su captured_at."""

    def __init__(self):
        self.frame, self.ts = None, 0.0

    def push(self, value: int, ts: float) -> None:
        self.frame = np.full((1536, 2048, 3), value, dtype=np.uint8)
        self.ts = ts

    def get_latest_frame_with_timestamp(self):
        return self.frame, self.ts

    def is_stale(self):
        return False


def _shown(tile) -> int:
    pixmap = tile.video_label.pixmap()
    return QColor(pixmap.toImage().pixel(pixmap.width() // 2, pixmap.height() // 2)).red()


class TestSincroniaConLasMarcas:
    """2026-09-30: las marcas de una analitica que corre a los fps del stream
    se dibujan sobre el cuadro en que se calcularon, no sobre el siguiente."""

    @pytest.fixture()
    def synced(self, qtbot, monkeypatch):
        widget = VideoTile(0)
        qtbot.addWidget(widget)
        widget._device = SimpleNamespace(name="Cam", id=5)
        widget._analytics_configs = []
        widget.video_label.resize(640, 480)
        stream = _Stream()
        monkeypatch.setattr(vt_module.stream_manager, "get_worker", lambda *_a, **_k: stream)
        return widget, stream

    @staticmethod
    def _marks(frame_ts: float):
        from aurea_vms.core.events import DetectionEvent

        return DetectionEvent(5, "monitor_tamper", time.time(), metrics={}, frame_ts=frame_ts)

    def test_espera_las_marcas_del_cuadro_nuevo_y_lo_dibuja_al_llegar(self, synced):
        tile, stream = synced
        stream.push(10, 1.00)
        tile._on_detection(self._marks(1.00))
        assert _shown(tile) == 10

        stream.push(20, 1.04)  # llega el cuadro nuevo, todavia sin sus marcas
        tile._refresh_frame()
        assert _shown(tile) == 10  # sigue el cuadro de las marcas

        tile._on_detection(self._marks(1.04))  # llegan sus marcas: se dibuja ya
        assert _shown(tile) == 20

    def test_si_la_analitica_se_atrasa_manda_el_video(self, synced):
        tile, stream = synced
        stream.push(10, 1.00)
        tile._on_detection(self._marks(1.00))
        stream.push(20, 1.04)
        tile._refresh_frame()
        stream.push(30, 1.08)  # dos cuadros sin marcas nuevas
        tile._refresh_frame()

        assert _shown(tile) == 30

    def test_nunca_vuelve_a_un_cuadro_mas_viejo(self, synced):
        tile, stream = synced
        stream.push(10, 1.00)
        tile._on_detection(self._marks(1.00))
        stream.push(20, 1.04)
        tile._refresh_frame()
        stream.push(30, 1.08)
        tile._refresh_frame()  # mostro el 30 (las marcas quedaron atras)
        tile._on_detection(self._marks(1.04))  # marcas tardias del 20

        assert _shown(tile) == 30


def test_en_una_pestaña_que_no_se_ve_no_dibuja(qtbot, monkeypatch):
    """Con Vista en Vivo de fondo, sus camaras redibujaban cada cuadro para
    nadie (2026-10-07). Al volver a la pestaña, dibuja el ultimo cuadro."""
    from PySide6.QtWidgets import QStackedWidget

    stack = QStackedWidget()
    qtbot.addWidget(stack)
    visible, hidden = VideoTile(0), VideoTile(1)
    stack.addWidget(visible)
    stack.addWidget(hidden)
    stack.resize(640, 480)
    stack.show()
    worker = _Worker(_frame(640, 480))
    monkeypatch.setattr(vt_module.stream_manager, "get_worker", lambda *_a, **_k: worker)
    for tile in (visible, hidden):
        tile._device = SimpleNamespace(name="Cam", id=5)
        tile._analytics_configs = []
    drawn: list = []
    original = vt_module.frame_to_pixmap
    monkeypatch.setattr(vt_module, "frame_to_pixmap", lambda *a: (drawn.append(a), original(*a))[1])

    hidden._refresh_frame()
    assert drawn == []

    stack.setCurrentWidget(hidden)
    hidden._refresh_frame()
    assert len(drawn) == 1
