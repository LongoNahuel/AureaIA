"""Iconos de la app: SVGs de Lucide (lucide.dev, licencia ISC) coloreados
en runtime segun el tema, mas un par de glifos propios para lo que Lucide
no cubre (grillas de layout NxN, indicador de estado)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from qfluentwidgets import FluentIcon

ASSETS_DIR = Path(__file__).parent / "assets" / "icons"
IMAGES_DIR = Path(__file__).parent / "assets" / "images"
VIDEOS_DIR = Path(__file__).parent / "assets" / "videos"
DEFAULT_COLOR = "#c7ccd4"
DEFAULT_SIZE = 22


@cache
def _load_svg_template(name: str) -> str:
    return (ASSETS_DIR / f"{name}.svg").read_text(encoding="utf-8")


def load_icon(name: str, color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    """Carga assets/icons/<name>.svg (formato Lucide) y lo recolorea
    reemplazando stroke="currentColor" por el color pedido.

    Rasteriza al devicePixelRatio de la pantalla: a escala fraccional o 2x
    un pixmap DPR-1 se reescala y todos los iconos salen borrosos."""
    svg = _load_svg_template(name).replace("currentColor", color)
    renderer = QSvgRenderer(svg.encode("utf-8"))
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap(round(size * dpr), round(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # Rect logico explicito: sin el, render() usa el viewport fisico del
    # pixmap (size*dpr) sobre coordenadas logicas y el glifo sale al doble.
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QIcon(pixmap)


# --- iconos de sidebar / modulos -------------------------------------------------


def icon_live_view(color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    return load_icon("camera", color, size)


def icon_devices(color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    return load_icon("server", color, size)


def icon_analyzers(color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    return load_icon("cpu", color, size)


def icon_alarms(color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    return load_icon("bell", color, size)


def icon_alerts(color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    return load_icon("shield-alert", color, size)


def icon_system(color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    return load_icon("settings", color, size)


def icon_users(color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    """Gestion de Usuarios -- no esta en el set de SVGs Lucide vendorizados
    localmente, se usa el icono equivalente de qfluentwidgets (ya es una
    dependencia del proyecto). QIcon es resolucion-independiente, `size`
    se acepta solo para mantener la misma firma que el resto de icon_*."""
    return FluentIcon.PEOPLE.icon(color=QColor(color))


def icon_history(color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    """Registro de operaciones -- mismo motivo que icon_users."""
    return FluentIcon.HISTORY.icon(color=QColor(color))


def icon_sites(color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    """Sitios y Zonas -- mismo motivo que icon_users."""
    return FluentIcon.PIN.icon(color=QColor(color))


# --- iconos de acciones / toolbar -------------------------------------------------


def icon_search(color: str = DEFAULT_COLOR) -> QIcon:
    return load_icon("search", color)


def icon_fullscreen(color: str = DEFAULT_COLOR) -> QIcon:
    return load_icon("maximize", color)


def icon_add(color: str = DEFAULT_COLOR) -> QIcon:
    return load_icon("plus", color)


def icon_edit(color: str = DEFAULT_COLOR) -> QIcon:
    return load_icon("pencil", color)


def icon_delete(color: str = DEFAULT_COLOR) -> QIcon:
    return load_icon("trash-2", color)


def icon_refresh(color: str = DEFAULT_COLOR) -> QIcon:
    return load_icon("refresh-cw", color)


def icon_discover(color: str = DEFAULT_COLOR) -> QIcon:
    return load_icon("radar", color)


def icon_play(color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    return load_icon("circle-play", color, size)


def icon_check(color: str = DEFAULT_COLOR) -> QIcon:
    return load_icon("check", color)


def icon_layout_grid(color: str = DEFAULT_COLOR) -> QIcon:
    return load_icon("layout-grid", color)


def icon_camera_placeholder(color: str = "#3a4353", size: int = 40) -> QIcon:
    return load_icon("camera", color, size)


# --- imagenes de marca (assets/images) -------------------------------------------


@cache
def _load_image(filename: str) -> QPixmap:
    return QPixmap(str(IMAGES_DIR / filename))


def app_logo_pixmap(size: int = 64) -> QPixmap:
    """Isotipo de AureaIA, escalado manteniendo proporcion -- placeholder
    de tiles vacios en Vista en Vivo antes de cargar el flujo de video."""
    base = _load_image("logo.png")
    return base.scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def background_pixmap() -> QPixmap:
    """Fondo de marca (data/ui/assets/images/background.jpg) para el
    lanzador principal."""
    return _load_image("background.jpg")


def algorithm_background_pixmap() -> QPixmap:
    """Fondo de red neuronal para la Vista Inteligente."""
    return _load_image("algorithm_bg.jpg")


def launcher_background_video_path() -> str:
    """Video de fondo (en loop) de la pantalla de Inicio (launcher de modulos)."""
    return str(VIDEOS_DIR / "launcher_bg.mp4")


def login_background_video_path() -> str:
    """Video de fondo (en loop) de la pantalla de inicio de sesión."""
    return str(VIDEOS_DIR / "login_bg.mp4")


# --- glifos propios (Lucide no cubre grillas NxN parametrizadas) ----------------


def _pen(color: str, width: float = 1.6) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def icon_grid(rows: int, cols: int, color: str = DEFAULT_COLOR, size: int = DEFAULT_SIZE) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(_pen(color, 1.3))
    margin = size * 0.14
    span = size - margin * 2
    painter.drawRoundedRect(QRectF(margin, margin, span, span), 1.5, 1.5)
    for i in range(1, cols):
        x = margin + span * i / cols
        painter.drawLine(QPointF(x, margin), QPointF(x, margin + span))
    for i in range(1, rows):
        y = margin + span * i / rows
        painter.drawLine(QPointF(margin, y), QPointF(margin + span, y))
    painter.end()
    return QIcon(pixmap)


def status_dot_pixmap(color: str, size: int = 9) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return pixmap
