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


def _mesa(manos: list[dict]) -> DetectionEvent:
    return DetectionEvent(
        5,
        "monitor_tamper",
        time.time(),
        metrics={
            "modo": "ruleta",
            "estado": "normal",
            "zonas": [],
            "personas": [
                {"caja": [700, 300, 400, 600], "rol": "crupier"},
                {"caja": [1400, 100, 300, 500], "rol": "jugador"},
            ],
            "manos": manos,
        },
    )


def test_cada_mano_es_un_recuadro_del_color_de_su_rol(tile):
    # Cuadro de 1920x1080 sobre un pixmap de 640x360: un tercio.
    tile._latest_events = {
        "monitor_tamper": _mesa(
            [
                {
                    "x": 900,
                    "y": 540,
                    "rol": "crupier",
                    "persona": 0,
                    "caja": [840, 480, 120, 120],
                },
                {
                    "x": 1500,
                    "y": 300,
                    "rol": "jugador",
                    "persona": 1,
                    "caja": [1440, 240, 120, 120],
                },
            ]
        )
    }

    imagen = tile._draw_overlay(_pixmap(), 1920, 1080).toImage()

    # El borde del recuadro (x 280 en el tile), a media altura.
    crupier = QColor(imagen.pixel(280, 180))
    jugador = QColor(imagen.pixel(480, 100))
    assert crupier.blue() > crupier.green() + 60 and crupier.red() > crupier.green() + 30  # violeta
    assert jugador.blue() > jugador.red() + 90  # celeste
    # Adentro solo un tinte tenue: la mano se sigue viendo.
    adentro = QColor(imagen.pixel(300, 180))
    assert abs(adentro.green() - 48) < 12
    fondo = QColor(imagen.pixel(400, 300))
    assert (fondo.red(), fondo.green(), fondo.blue()) == (48, 48, 48)


def test_sin_recuadro_de_la_analitica_se_marca_alrededor_de_la_palma(tile):
    tile._latest_events = {
        "monitor_tamper": _mesa([{"x": 900, "y": 540, "rol": "crupier", "persona": 0}])
    }

    imagen = tile._draw_overlay(_pixmap(), 1920, 1080).toImage()

    borde = QColor(imagen.pixel(300 - 12, 180))  # HAND_BOX_PX = 24 alrededor de (300, 180)
    assert borde.blue() > borde.green() + 60


def test_el_rol_se_rotula_una_vez_por_persona(tile, monkeypatch):
    rotulos: list[str] = []
    original = tile._draw_guide_label
    monkeypatch.setattr(
        tile,
        "_draw_guide_label",
        lambda painter, point, text, *a, **k: (
            rotulos.append(text),
            original(painter, point, text, *a, **k),
        ),
    )
    tile._latest_events = {
        "monitor_tamper": _mesa(
            [
                {"x": 800, "y": 500, "rol": "crupier", "persona": 0},
                {"x": 950, "y": 620, "rol": "crupier", "persona": 0},
                {"x": 1500, "y": 300, "rol": "jugador", "persona": 1},
                {"x": 300, "y": 900, "rol": "persona", "persona": 2},
            ]
        )
    }

    tile._draw_overlay(_pixmap(), 1920, 1080)

    assert sorted(t for t in rotulos if t in ("Crupier", "Jugador")) == ["Crupier", "Jugador"]


def test_sin_marcas_no_se_dibujan_manos(tile, monkeypatch):
    manos = _espiar(monkeypatch, tile, "_draw_hands")
    tile._latest_events = {"monitor_tamper": _mesa([{"x": 900, "y": 540, "rol": "crupier"}])}

    tile.set_smart_marks(False)
    tile._draw_overlay(_pixmap(), 1920, 1080)

    assert manos == []


def test_la_preferencia_de_marca_no_se_lee_en_cada_cuadro(tile, monkeypatch):
    lecturas: list = []
    monkeypatch.setattr(
        vt_module.app_prefs, "intelligent_branding_enabled", lambda: (lecturas.append(1), True)[1]
    )
    for _ in range(20):
        tile._draw_overlay(_pixmap(), 1920, 1080)

    assert len(lecturas) <= 1


def _ruleta(ronda: dict, zona: dict, **extra) -> DetectionEvent:
    return DetectionEvent(
        5,
        "monitor_tamper",
        time.time(),
        metrics={
            "modo": "ruleta",
            "zona_tipo": "zona",
            "estado": "alerta" if zona.get("estado") == "alerta" else "normal",
            "zonas": [zona],
            "rueda": [1300.0, 800.0, 400.0, 400.0, 0.0],
            "velocidad_deg_s": 170.0,
            "rpm": 28.3,
            "sentido": "horario",
            "ronda": ronda,
            **extra,
        },
    )


def _rotulos(tile, monkeypatch) -> list[str]:
    rotulos: list[str] = []
    original = tile._draw_guide_label
    monkeypatch.setattr(
        tile,
        "_draw_guide_label",
        lambda painter, point, text, *a, **k: (
            rotulos.append(text),
            original(painter, point, text, *a, **k),
        ),
    )
    return rotulos


def test_el_no_va_mas_se_rotula_junto_a_la_rueda_y_arma_las_zonas(tile, monkeypatch):
    rotulos = _rotulos(tile, monkeypatch)
    tile._latest_events = {
        "monitor_tamper": _ruleta(
            {"estado": "no_va_mas", "bola_deg_s": 185.0, "pano_armado": True},
            {"estado": "normal"},
            bola=[1300.0, 530.0],
        )
    }

    tile._draw_overlay(_pixmap(), 1920, 1080)

    assert "NO VA MÁS · bola 185 °/s" in rotulos
    assert "Zona 1 · ARMADA" in rotulos


def test_las_fichas_tras_el_no_va_mas_se_marcan_en_rojo_donde_cambiaron(tile, monkeypatch):
    rotulos = _rotulos(tile, monkeypatch)
    ficha = [690, 650, 30, 30]  # dentro de ZONA
    tile._latest_events = {
        "monitor_tamper": _ruleta(
            {"estado": "resultado", "pano_armado": True},
            {
                "estado": "alerta",
                "motivo": "no_va_mas",
                "posiciones": [ficha],
                "ultimo_cambio": time.time(),
            },
        )
    }

    imagen = tile._draw_overlay(_pixmap(), 1920, 1080).toImage()

    assert "Zona 1 · FICHAS TRAS NO VA MÁS" in rotulos
    # El anillo alrededor de la ficha (un tercio de escala): rojo.
    cx, cy = (ficha[0] + 15) / 3, (ficha[1] + 15) / 3
    borde = QColor(imagen.pixel(int(cx + 9), int(cy)))
    assert borde.red() > borde.green() + 60 and borde.red() > borde.blue() + 60


def test_la_jugada_nueva_de_la_rueda_se_avisa_junto_a_la_rueda(tile, monkeypatch):
    rotulos = _rotulos(tile, monkeypatch)
    tile._latest_events = {
        "monitor_tamper": _ruleta(
            {
                "estado": "apuestas",
                "pano_armado": False,
                "jugadas": 1,
                "ultima_jugada": {"t": time.time() - 1.0, "tipo": "sentido"},
            },
            {"estado": "normal"},
        )
    }

    tile._draw_overlay(_pixmap(), 1920, 1080)

    assert "Nueva jugada · la rueda cambió de sentido" in rotulos
