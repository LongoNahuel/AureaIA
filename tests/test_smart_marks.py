"""Marcas inteligentes sobre el video (2026-09-23): se dibujan en Vista en
Vivo y en Vista Inteligente, se pueden apagar, y la preferencia de marca ya
no se lee del disco en cada cuadro."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QColor, QPixmap

import aurea_vms.ui.widgets.video_tile as vt_module
from aurea_vms.core.events import DetectionEvent
from aurea_vms.ui.widgets.video_tile import VideoTile

ZONA = [605, 565, 281, 311]


@pytest.fixture()
def tile(qtbot):
    widget = VideoTile(0)
    qtbot.addWidget(widget)
    widget._device = SimpleNamespace(name="Cam", id=5)
    widget._analytics_configs = [
        SimpleNamespace(
            analyzer_name="monitor_tamper",
            params={"zones": [ZONA]},
            roi_x=None,
            roi_y=None,
            roi_w=None,
            roi_h=None,
        )
    ]
    return widget


def _pixmap() -> QPixmap:
    pixmap = QPixmap(640, 360)
    pixmap.fill(QColor("#303030"))
    return pixmap


def _incidente() -> DetectionEvent:
    return DetectionEvent(
        5,
        "monitor_tamper",
        time.time(),
        metrics={
            "estado": "alerta",
            "zonas": [{"estado": "alerta", "motivo": "patada", "incidentes": 1}],
            "incidentes": 1,
            "ultimo_golpe": {"t": time.time(), "zona": 0, "motivo": "patada"},
        },
    )


def _espiar(monkeypatch, tile, nombre) -> list:
    llamadas: list = []
    original = getattr(tile, nombre)
    monkeypatch.setattr(tile, nombre, lambda *a, **k: (llamadas.append(1), original(*a, **k))[1])
    return llamadas


def test_las_marcas_se_ven_en_vista_en_vivo(tile, monkeypatch):
    """Antes zonas, lineas y estado solo existian en la Vista Inteligente."""
    assert tile._intelligent_mode is False
    guias = _espiar(monkeypatch, tile, "_draw_analytics_guides")
    incidente = _espiar(monkeypatch, tile, "_draw_incident_mark")
    tile._latest_events = {"monitor_tamper": _incidente()}

    tile._draw_overlay(_pixmap(), 1920, 1080)

    assert guias and incidente


def test_se_pueden_apagar(tile, monkeypatch):
    guias = _espiar(monkeypatch, tile, "_draw_analytics_guides")
    tile._latest_events = {"monitor_tamper": _incidente()}

    tile.set_smart_marks(False)
    tile._draw_overlay(_pixmap(), 1920, 1080)

    assert guias == []


def test_el_incidente_pinta_el_marco_rojo_del_video(tile):
    tile._latest_events = {"monitor_tamper": _incidente()}

    resultado = tile._draw_overlay(_pixmap(), 1920, 1080).toImage()
    borde = QColor(resultado.pixel(4, 180))

    # El marco late entre 45% y 100% de opacidad, siempre rojo dominante.
    assert borde.red() > borde.green() + 40 and borde.red() > borde.blue() + 40


def test_la_preferencia_de_marca_no_se_lee_en_cada_cuadro(tile, monkeypatch):
    lecturas: list = []
    monkeypatch.setattr(
        vt_module.app_prefs, "intelligent_branding_enabled", lambda: (lecturas.append(1), True)[1]
    )
    for _ in range(20):
        tile._draw_overlay(_pixmap(), 1920, 1080)

    assert len(lecturas) <= 1
