"""Zoom digital en la interfaz (2026-10-03): el zoom de la vista en el tile,
"analizar este zoom" / "analizar el cuadro completo", y el zoom en el
dialogo de Incidentes en casinos."""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from qfluentwidgets import RoundMenu

import aurea_vms.ui.analysis_zoom as az_module
import aurea_vms.ui.widgets.video_tile as vt_module
from aurea_vms.core.analytics.digital_zoom import OUTSIDE_ZOOM
from aurea_vms.core.events import DetectionEvent
from aurea_vms.ui.analysis_zoom import set_analysis_zoom, zoom_check
from aurea_vms.ui.widgets.frame_selector import FrameSelectorWidget
from aurea_vms.ui.widgets.video_tile import MAX_ZOOM, VideoTile

ZONA = [1200, 900, 300, 200]
REGION = (1024, 768, 1024, 768)


def _config(params) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        device_id=5,
        analyzer_name="monitor_tamper",
        params=params,
        roi_x=None,
        roi_y=None,
        roi_w=None,
        roi_h=None,
    )


class _Worker:
    def __init__(self, frame):
        self.frame = frame

    def get_latest_frame_with_timestamp(self):
        return self.frame, 1.0

    def is_stale(self):
        return False


def _frame() -> np.ndarray:
    """2048x1536: la mitad izquierda azul y la derecha roja (BGR)."""
    frame = np.zeros((1536, 2048, 3), np.uint8)
    frame[:, :1024] = (255, 0, 0)
    frame[:, 1024:] = (0, 0, 255)
    return frame


@pytest.fixture()
def tile(qtbot, monkeypatch):
    widget = VideoTile(0)
    qtbot.addWidget(widget)
    widget._device = SimpleNamespace(name="Cam", id=5)
    widget._analytics_configs = [_config({"zones": [ZONA]})]
    worker = _Worker(_frame())
    monkeypatch.setattr(vt_module.stream_manager, "get_worker", lambda *_a, **_k: worker)
    widget.video_label.resize(640, 480)
    widget._refresh_frame()
    return widget


def _pixmap() -> QPixmap:
    pixmap = QPixmap(640, 480)
    pixmap.fill(QColor("#303030"))
    return pixmap


def _spy_labels(monkeypatch, tile) -> list[str]:
    texts: list[str] = []
    original = tile._draw_guide_label

    def spy(painter, point, text, color, centered=False):
        texts.append(text)
        return original(painter, point, text, color, centered)

    monkeypatch.setattr(tile, "_draw_guide_label", spy)
    return texts


class TestZoomDeLaVista:
    def test_la_rueda_acerca_sobre_el_punto_del_mouse(self, tile):
        rect = tile._video_rect
        corner = QPointF(rect.right(), rect.bottom())

        tile.zoom_by(2.0, corner)

        # El rincon de abajo a la derecha queda quieto.
        assert tile._view_pixels(2048, 1536) == REGION
        assert tile.zoom_level() == pytest.approx(2.0)
        assert tile.is_zoomed()

    def test_alejar_hasta_ver_todo_quita_el_zoom(self, tile):
        tile.zoom_by(2.0)
        tile.zoom_by(0.4)

        assert not tile.is_zoomed()

    def test_el_zoom_tiene_tope(self, tile):
        tile.zoom_by(100.0)

        assert tile.zoom_level() == pytest.approx(MAX_ZOOM)

    def test_se_ve_solo_la_parte_acercada(self, tile):
        # Un cuarto del ancho: sin zoom cae en la mitad azul del cuadro.
        before = QColor(tile.video_label.pixmap().toImage().pixel(160, 240))
        tile.zoom_by(2.0, QPointF(tile._video_rect.right(), tile._video_rect.center().y()))

        after = QColor(tile.video_label.pixmap().toImage().pixel(160, 240))
        assert before.blue() > 200 and before.red() < 50
        # Con zoom sobre la mitad derecha, todo es rojo.
        assert after.red() > 200 and after.blue() < 50

    def test_arrastrar_mueve_la_vista(self, tile, qtbot):
        tile.zoom_by(2.0)  # centro: (0.25, 0.25, 0.5, 0.5)
        start = tile._video_rect.center().toPoint()

        qtbot.mousePress(tile, Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseMove(tile, start + QPoint(-int(tile._video_rect.width() / 4), 0))
        qtbot.mouseRelease(tile, Qt.MouseButton.LeftButton, pos=start)

        # Arrastrar a la izquierda un cuarto del video corre la vista a la
        # derecha un cuarto de su ancho.
        assert tile._view.x() == pytest.approx(0.375, abs=0.01)
        assert tile._view.y() == pytest.approx(0.25, abs=0.01)

    def test_cambiar_de_camara_quita_el_zoom(self, tile, monkeypatch):
        monkeypatch.setattr(vt_module.repository, "get_device", lambda _id: None)
        tile.zoom_by(2.0)

        tile.assign_device(None)

        assert not tile.is_zoomed()

    def test_ver_el_zoom_analizado(self, tile):
        tile.show_region(REGION)

        assert tile._view_pixels(2048, 1536) == REGION


class TestMarcasConZoom:
    def _hand_event(self) -> DetectionEvent:
        hand = {"x": 1520, "y": 1120, "rol": "crupier", "caja": [1500, 1100, 40, 40]}
        hand["en_rueda"] = True
        return DetectionEvent(5, "monitor_tamper", time.time(), metrics={"manos": [hand]})

    def test_la_mano_se_dibuja_donde_esta_en_la_vista(self, tile):
        tile._latest_events = {"monitor_tamper": self._hand_event()}

        image = tile._draw_overlay(_pixmap(), 2048, 1536, REGION).toImage()

        # (1500 - 1024) * 0,625 = 297,5: el borde izquierdo del recuadro.
        edge = QColor(image.pixel(298, 225))
        assert edge != QColor("#303030")
        assert edge.red() > 100 and edge.blue() > 150  # violeta del crupier

    def test_la_etiqueta_no_se_sale_de_la_vista(self, qtbot):
        pixmap = _pixmap()
        painter = QPainter(pixmap)
        painter.translate(-1000, -500)
        VideoTile._draw_guide_label(painter, QPointF(980, 520), "Zona 1", QColor("#22c55e"))
        painter.end()

        # Sin acotar en pixeles del tile quedaba a la izquierda del borde.
        label = QColor(pixmap.toImage().pixel(10, 32))
        assert label != QColor("#303030")

    def test_las_zonas_fuera_del_zoom_se_marcan(self, tile, monkeypatch):
        tile._analytics_configs = [_config({"zones": [ZONA, [10, 10, 100, 100]]})]
        zones = [{"estado": "normal"}, {"estado": OUTSIDE_ZOOM}]
        tile._latest_events = {
            "monitor_tamper": DetectionEvent(
                5, "monitor_tamper", time.time(), metrics={"zonas": zones, "estado": "normal"}
            )
        }
        texts = _spy_labels(monkeypatch, tile)

        tile._draw_overlay(_pixmap(), 2048, 1536)

        assert "Zona 2 · fuera del zoom" in texts
        assert "Pantalla 1 · OK" in texts

    def test_el_zoom_analizado_se_ve_desde_el_cuadro_completo(self, tile, monkeypatch):
        tile._analytics_configs = [
            _config({"zones": [ZONA], "zoom_digital": list(REGION), "analizar_zoom": True})
        ]
        texts = _spy_labels(monkeypatch, tile)

        tile._draw_overlay(_pixmap(), 2048, 1536)
        assert "Zoom analizado" in texts

        # Mirando el mismo zoom ya no hace falta marcarlo.
        texts.clear()
        tile._draw_overlay(_pixmap(), 2048, 1536, REGION)
        assert "Zoom analizado" not in texts


class TestMenu:
    def _menu_texts(self, tile, monkeypatch, allowed=True) -> dict[str, bool]:
        monkeypatch.setattr(vt_module, "can", lambda _perm: allowed)
        menu = RoundMenu(parent=tile)
        for config in tile._zoom_configs():
            tile._add_analysis_zoom_actions(menu, config)
        return {action.text(): action.isEnabled() for action in menu.actions()}

    def test_con_zoom_se_ofrece_analizarlo(self, tile, monkeypatch):
        tile.zoom_by(2.0)

        assert self._menu_texts(tile, monkeypatch) == {"Incidentes: analizar este zoom": True}

    def test_sin_permiso_no_se_puede_cambiar(self, tile, monkeypatch):
        tile.zoom_by(2.0)

        assert self._menu_texts(tile, monkeypatch, allowed=False) == {
            "Incidentes: analizar este zoom": False
        }

    def test_con_la_analitica_en_el_zoom(self, tile, monkeypatch):
        tile._analytics_configs = [
            _config({"zones": [ZONA], "zoom_digital": list(REGION), "analizar_zoom": True})
        ]

        assert self._menu_texts(tile, monkeypatch) == {
            "Incidentes: ver el zoom analizado": True,
            "Incidentes: analizar el cuadro completo": True,
        }


class TestAnalizarElZoom:
    @pytest.fixture()
    def setup(self, temp_db, qtbot, monkeypatch):
        from aurea_vms.models import repository

        device = repository.add_device(name="Mesa", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
        config = repository.upsert_analytics_config(
            device.id, "monitor_tamper", enabled=True, params={"modo": "golpes", "zones": [ZONA]}
        )
        calls = SimpleNamespace(started=[], warned=[], notified=[])
        monkeypatch.setattr(az_module, "confirm", lambda *_a: True)
        monkeypatch.setattr(az_module, "warn", lambda _p, _t, text: calls.warned.append(text))
        monkeypatch.setattr(az_module, "notify", lambda _p, _t, text: calls.notified.append(text))
        monkeypatch.setattr(az_module.incident_rules, "ensure_incident_rule", lambda _c: None)
        monkeypatch.setattr(
            az_module.analytics_engine, "start", lambda c, _d: calls.started.append(c.params)
        )
        # El parent (None) no se usa: confirm, warn y notify estan reemplazados.
        return SimpleNamespace(repository=repository, device=device, config=config, calls=calls)

    def test_guarda_el_zoom_y_reinicia(self, setup):
        assert set_analysis_zoom(None, setup.config, REGION)

        params = setup.repository.get_analytics_config_for(setup.device.id, "monitor_tamper").params
        assert params["zoom_digital"] == list(REGION) and params["analizar_zoom"] is True
        assert params["zones"] == [ZONA]  # lo demas no cambia
        assert setup.calls.started == [params]

    def test_volver_al_cuadro_completo_guarda_el_zoom(self, setup):
        set_analysis_zoom(None, setup.config, REGION)
        config = setup.repository.get_analytics_config_for(setup.device.id, "monitor_tamper")

        assert set_analysis_zoom(None, config, None)

        params = setup.repository.get_analytics_config_for(setup.device.id, "monitor_tamper").params
        assert params["analizar_zoom"] is False and params["zoom_digital"] == list(REGION)

    def test_si_no_arranca_queda_como_estaba(self, setup, monkeypatch):
        def fail_once(config, _device):
            if config.params.get("analizar_zoom"):
                raise ValueError("modelo roto")
            setup.calls.started.append(config.params)

        monkeypatch.setattr(az_module.analytics_engine, "start", fail_once)

        assert not set_analysis_zoom(None, setup.config, REGION)

        params = setup.repository.get_analytics_config_for(setup.device.id, "monitor_tamper").params
        assert "zoom_digital" not in params
        assert setup.calls.started == [params]  # volvio a arrancar la anterior
        assert "modelo roto" in setup.calls.warned[0]

    def test_sin_zonas_adentro_no_se_guarda(self, setup):
        assert not set_analysis_zoom(None, setup.config, (0, 0, 400, 300))

        params = setup.repository.get_analytics_config_for(setup.device.id, "monitor_tamper").params
        assert "zoom_digital" not in params
        assert "Ninguna zona" in setup.calls.warned[0]


class TestAvisos:
    def test_zonas_fuera_del_zoom(self):
        params = {"zones": [ZONA, [0, 0, 50, 50], [10, 10, 50, 50]]}

        error, notes = zoom_check(params, REGION)

        assert error is None
        assert notes == ["Las zonas 2 y 3 quedan fuera del zoom."]

    def test_la_rueda_fuera_del_zoom(self):
        params = {"modo": "ruleta", "zones": [ZONA], "rueda": [100, 100, 200, 200, 0]}

        _error, notes = zoom_check(params, REGION)

        assert "rueda" in notes[0]


class TestSelector:
    @pytest.fixture()
    def selector(self, qtbot):
        widget = FrameSelectorWidget("rects", max_rects=6)
        qtbot.addWidget(widget)
        widget.resize(640, 480)
        widget.set_frame(np.zeros((1536, 2048, 3), np.uint8))
        widget.grab()  # paintEvent: ubica el cuadro dentro del widget
        return widget

    def test_marcar_el_zoom_no_agrega_una_zona(self, selector, qtbot):
        drawn: list = []
        selector.zoom_changed.connect(drawn.append)
        selector.capture_zoom(True)

        qtbot.mousePress(selector, Qt.MouseButton.LeftButton, pos=QPoint(320, 240))
        qtbot.mouseMove(selector, QPoint(640 - 1, 480 - 1))
        qtbot.mouseRelease(selector, Qt.MouseButton.LeftButton, pos=QPoint(639, 479))

        assert selector.get_rects() == []
        x, y, w, h = selector.zoom_rect()
        assert (x, y) == (1024, 768) and w == pytest.approx(1024, abs=4)
        assert drawn == [selector.zoom_rect()]
        assert not selector.is_capturing_zoom()

    def test_viendo_el_zoom_las_zonas_quedan_en_pixeles_del_cuadro(self, selector, qtbot):
        selector.set_view(REGION)
        selector.grab()

        # QPoint(0, 0) es "el centro del widget" para QTest.
        qtbot.mousePress(selector, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
        qtbot.mouseMove(selector, QPoint(320, 240))
        qtbot.mouseRelease(selector, Qt.MouseButton.LeftButton, pos=QPoint(320, 240))

        x, y, w, h = selector.get_rects()[0]
        assert x == pytest.approx(1024, abs=4) and y == pytest.approx(768, abs=4)
        assert w == pytest.approx(512, abs=4) and h == pytest.approx(384, abs=4)


class TestDialogo:
    @pytest.fixture()
    def dialog(self, qtbot, temp_db):
        from aurea_vms.models import repository
        from aurea_vms.ui.dialogs.monitor_tamper_config_dialog import MonitorTamperConfigDialog

        device = repository.add_device(name="Mesa", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
        repository.upsert_analytics_config(
            device.id,
            "monitor_tamper",
            params={
                "modo": "golpes",
                "zones": [ZONA],
                "zoom_digital": list(REGION),
                "analizar_zoom": True,
            },
        )
        widget = MonitorTamperConfigDialog(device)
        qtbot.addWidget(widget)
        return widget

    def test_carga_y_guarda_el_zoom(self, dialog):
        assert dialog.analyzes_zoom()
        assert dialog.selector_widget.zoom_rect() == REGION

        params = dialog.build_params()

        assert params["zoom_digital"] == list(REGION) and params["analizar_zoom"] is True
        assert "1024×768" in dialog.zoom_caption.text()

    def test_el_cuadro_completo_conserva_el_zoom(self, dialog):
        dialog.analysis_area_combo.setCurrentIndex(0)

        params = dialog.build_params()

        assert params["analizar_zoom"] is False and params["zoom_digital"] == list(REGION)

    def test_quitar_el_zoom(self, dialog):
        dialog.clear_zoom_button.click()

        params = dialog.build_params()

        assert "zoom_digital" not in params and params["analizar_zoom"] is False
        assert dialog.selector_widget.zoom_rect() is None

    def test_analizar_el_zoom_pide_un_zoom(self, dialog):
        dialog.clear_zoom_button.click()
        dialog.analysis_area_combo.setCurrentIndex(1)

        assert "Marcá el zoom" in dialog.validate()

    def test_ninguna_zona_adentro_no_se_puede_guardar(self, dialog):
        dialog._on_zoom_drawn((0, 0, 400, 300))

        assert dialog.analyzes_zoom()
        assert "Ninguna zona" in dialog.validate()

    def test_ver_solo_el_zoom(self, dialog):
        dialog.view_zoom_button.click()
        assert dialog.selector_widget.view() == REGION
        assert dialog.view_zoom_button.text() == "Ver todo el cuadro"

        dialog.view_zoom_button.click()
        assert dialog.selector_widget.view() is None
