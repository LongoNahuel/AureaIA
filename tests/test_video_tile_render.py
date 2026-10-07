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
    # Sin mostrar el widget (el layout pisaria el tamaño del label): se
    # declara en pantalla. La visibilidad se prueba en TestSoloLoQueSeVe.
    monkeypatch.setattr(widget, "_en_pantalla", lambda: True)
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
        monkeypatch.setattr(widget, "_en_pantalla", lambda: True)
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


class TestSoloLoQueSeVe:
    """Fase V1 (2026-10-07): un recuadro que nadie ve no achica, convierte ni
    pinta. Medido antes del cambio: 16 recuadros 1080p en una pestaña oculta
    ocupaban el 99 % del hilo de la GUI."""

    @pytest.fixture()
    def visible(self, qtbot, monkeypatch):
        from PySide6.QtWidgets import QTabWidget, QWidget

        tabs = QTabWidget()
        qtbot.addWidget(tabs)
        widget = VideoTile(0)
        widget._device = SimpleNamespace(name="Cam", id=5)
        widget._analytics_configs = []
        tabs.addTab(widget, "vivo")
        tabs.addTab(QWidget(), "otra")
        tabs.resize(640, 480)
        tabs.show()
        qtbot.waitExposed(tabs)
        worker = _Worker(_frame(1280, 720))
        monkeypatch.setattr(vt_module.stream_manager, "get_worker", lambda *_a, **_k: worker)
        # El espia es de ESTE tile (no frame_to_pixmap, que es del modulo):
        # los tiles vivos de otros tests tambien tickean y se contarian.
        convertidos: list = []
        original = widget._draw_overlay

        def contar(*args):
            convertidos.append(args)
            return original(*args)

        monkeypatch.setattr(widget, "_draw_overlay", contar)
        # Los ticks del timer quedan afuera: cada test llama a
        # _refresh_frame a mano. showEvent lo vuelve a arrancar.
        widget._timer.stop()
        return tabs, widget, convertidos

    def test_visible_dibuja(self, visible):
        _tabs, tile, convertidos = visible
        tile._refresh_frame()
        assert len(convertidos) == 1

    def test_en_una_pestaña_oculta_no_dibuja_ni_sondea(self, visible):
        tabs, tile, convertidos = visible
        tile._timer.start()
        tabs.setCurrentIndex(1)

        tile._refresh_frame()

        assert convertidos == []
        assert not tile._timer.isActive()  # lo paro el hideEvent

    def test_al_volver_a_la_pestaña_redibuja_y_sondea(self, visible):
        tabs, tile, convertidos = visible
        tile._refresh_frame()
        tabs.setCurrentIndex(1)
        tabs.setCurrentIndex(0)

        assert tile._timer.isActive()
        tile._timer.stop()
        tile._refresh_frame()  # mismo cuadro, pero hay que pintarlo de nuevo
        assert len(convertidos) == 2

    def test_minimizada_no_dibuja(self, visible, monkeypatch):
        """Minimizar no siempre manda hideEvent a los hijos (depende de la
        plataforma): la guarda de _refresh_frame lo cubre igual."""
        tabs, tile, convertidos = visible
        monkeypatch.setattr(tabs, "isMinimized", lambda: True)

        tile._refresh_frame()

        assert convertidos == []

    def test_una_deteccion_en_un_tile_oculto_no_dibuja(self, visible):
        from aurea_vms.core.events import DetectionEvent

        tabs, tile, convertidos = visible
        tabs.setCurrentIndex(1)
        tile._on_detection(DetectionEvent(5, "monitor_tamper", time.time(), frame_ts=1.0))

        assert convertidos == []


def test_el_nombre_de_marca_no_se_lee_en_cada_cuadro(tile, monkeypatch):
    lecturas: list = []
    monkeypatch.setattr(vt_module.app_prefs, "get_brand_name", lambda: lecturas.append(1) or "X")
    monkeypatch.setattr(tile, "_branding", True)
    monkeypatch.setattr(tile, "_branding_checked_at", time.monotonic())
    monkeypatch.setattr(tile, "_branding_enabled", lambda: True)
    tile.video_label.resize(640, 400)
    for _ in range(3):
        tile._last_rendered_ts = 0.0
        tile._refresh_frame()

    assert lecturas == []
