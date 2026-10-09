"""Tema visual global: paleta oscura aplicada a nivel QApplication
(sidebar, botones, tablas, inputs, dialogos, scrollbars). La app es siempre
oscura (pedido de Nahuel, 2026-10-08): ya no hay toggle en Sistema."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

ACCENT = "#3b82f6"

# Escala de severidad de alarmas -- UNICA fuente de verdad (antes vivia
# duplicada en alarm_module y global_alert_popup). Cualquier badge, borde
# de popup o dot que comunique severidad sale de aca. Valores tomados de
# los tokens del prototipo NOVA (design system de referencia del
# proyecto): cambiar SOLO estos valores re-pinta toda la app.
SEVERITY_COLORS = {
    "critico": "#ff4d5e",
    "alto": "#ff9f43",
    "medio": "#ffd166",
    "info": "#4f8cff",
}


def severity_qcolor(severity: str) -> QColor:
    return QColor(SEVERITY_COLORS.get(severity, SEVERITY_COLORS["info"]))


def severity_soft_qcolor(severity: str) -> QColor:
    """Variante "soft" NOVA (12% de alpha) -- fondo de chips y badges."""
    color = severity_qcolor(severity)
    color.setAlphaF(0.12)
    return color


def severity_text_qcolor(severity: str, dark: bool) -> QColor:
    """Color de texto del chip: el token pleno sobre fondo oscuro; sobre
    tema claro se oscurece (el medio #ffd166 es ilegible sobre blanco)."""
    color = severity_qcolor(severity)
    return color if dark else color.darker(140)


def enable_tabular_numbers(widget) -> None:
    """Cifras tabulares (todas del mismo ancho) en un widget cuyo numero
    se refresca en el lugar: sin esto el valor "baila" al cambiar de
    digitos. QFont.setFeature existe desde Qt 6.7; si no esta, se degrada
    a la fuente normal sin romper."""
    try:
        from PySide6.QtGui import QFont

        font = widget.font()
        font.setFeature(QFont.Tag("tnum"), 1)
        widget.setFont(font)
    except (AttributeError, TypeError):
        pass


# Colores de estado de camara (online/offline/sin probar) -- reservados
# para estado, no reciclarlos para otra semantica.
STATUS_COLORS = {"online": "#3fb950", "offline": "#e5534b", "unknown": "#6e7681"}

# Fase 1 de la interfaz (2026-10-08): el color dice estado y nada mas. Tonos
# de las tarjetas de numeros (widgets/kpi_tile.py): neutro, bien, atencion,
# actuar ya.
TONES = {
    "neutral": "#e5e7eb",
    "ok": STATUS_COLORS["online"],
    "warn": "#f59e0b",
    "alert": SEVERITY_COLORS["critico"],
}
# Escalas de espaciado y de tipografia (px) de los componentes nuevos.
SPACE = {"xs": 4, "s": 8, "m": 12, "l": 16, "xl": 24}
FONT_PX = {"caption": 12, "body": 13, "title": 18, "kpi": 28}


def rgba(color: str, alpha: float) -> str:
    """Un color con transparencia para hojas de estilo. Qt lee "#RRGGBBAA"
    como "#AARRGGBB": "#f59e0b22" (ambar al 13 %) salia rojo oscuro."""
    qcolor = QColor(color)
    return f"rgba({qcolor.red()}, {qcolor.green()}, {qcolor.blue()}, {alpha:.2f})"


def dot_icon(color: str | QColor, size: int = 10) -> QIcon:
    """Un punto de color como icono (estado en tablas, chips y listas)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return QIcon(pixmap)


def severity_dot(severity: str, size: int = 10) -> QIcon:
    """Un punto del color de la severidad, para el icono de la celda: marca
    la fila sin pintar la celda entera. Cuando todo era critico, la tabla
    quedaba roja de punta a punta y el color ya no distinguia nada."""
    return dot_icon(severity_qcolor(severity), size)


_DARK_PALETTE = {
    "BG_PRIMARY": "#161c26",
    "BG_SECONDARY": "#1e2530",
    "BG_ELEVATED": "#232b38",
    "BG_INPUT": "#10151c",
    "BORDER": "#2a3441",
    "TEXT_PRIMARY": "#e5e7eb",
    "TEXT_SECONDARY": "#9aa3af",
    "ALT_ROW": "#1a2029",
    "SELECTION": "#2a3f5f",
    "SCROLL_HANDLE": "#3a4557",
    "SCROLL_HANDLE_HOVER": "#47536a",
    "SIDEBAR_HOVER": "#263040",
    "DISABLED_TEXT": "#565f6c",
    "DISABLED_BG": "#1c222c",
    "SELECTED_TEXT": "#ffffff",
}

_LIGHT_PALETTE = {
    "BG_PRIMARY": "#f3f4f6",
    "BG_SECONDARY": "#ffffff",
    "BG_ELEVATED": "#e9ebef",
    "BG_INPUT": "#ffffff",
    "BORDER": "#d7dbe0",
    "TEXT_PRIMARY": "#1f2430",
    "TEXT_SECONDARY": "#5b6472",
    "ALT_ROW": "#f7f8fa",
    "SELECTION": "#c7d9fb",
    "SCROLL_HANDLE": "#c3c9d1",
    "SCROLL_HANDLE_HOVER": "#aab0ba",
    "SIDEBAR_HOVER": "#e4e7ec",
    "DISABLED_TEXT": "#a4aab3",
    "DISABLED_BG": "#eceef1",
    "SELECTED_TEXT": "#0b1220",
}


def build_stylesheet(dark: bool = True) -> str:
    p = _DARK_PALETTE if dark else _LIGHT_PALETTE
    return f"""
QWidget {{
    background-color: {p["BG_PRIMARY"]};
    color: {p["TEXT_PRIMARY"]};
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {p["BG_PRIMARY"]};
}}

QListWidget#sidebar {{
    background-color: {p["BG_SECONDARY"]};
    border: none;
    outline: none;
    padding: 6px 0;
}}
QListWidget#sidebar::item {{
    color: {p["TEXT_SECONDARY"]};
    padding: 12px 16px;
    border-left: 3px solid transparent;
}}
QListWidget#sidebar::item:selected {{
    background-color: {p["BG_ELEVATED"]};
    color: {p["SELECTED_TEXT"]};
    border-left: 3px solid {ACCENT};
}}
QListWidget#sidebar::item:hover:!selected {{
    background-color: {p["SIDEBAR_HOVER"]};
}}

QPushButton {{
    background-color: {p["BG_ELEVATED"]};
    color: {p["TEXT_PRIMARY"]};
    border: 1px solid {p["BORDER"]};
    border-radius: 4px;
    padding: 6px 14px;
}}
QPushButton:hover {{
    background-color: {p["SIDEBAR_HOVER"]};
    border-color: {p["SCROLL_HANDLE"]};
}}
QPushButton:pressed {{
    background-color: {p["ALT_ROW"]};
}}
QPushButton:disabled {{
    color: {p["DISABLED_TEXT"]};
    background-color: {p["DISABLED_BG"]};
}}
QPushButton:checked {{
    background-color: {p["SELECTION"]};
    border-color: {ACCENT};
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: {p["BG_INPUT"]};
    color: {p["TEXT_PRIMARY"]};
    border: 1px solid {p["BORDER"]};
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {p["BG_ELEVATED"]};
    color: {p["TEXT_PRIMARY"]};
    selection-background-color: {ACCENT};
    border: 1px solid {p["BORDER"]};
    outline: none;
}}

QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {p["BORDER"]};
    border-radius: 3px;
    background-color: {p["BG_INPUT"]};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

QGroupBox {{
    border: 1px solid {p["BORDER"]};
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 14px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {p["TEXT_SECONDARY"]};
}}

QTableWidget, QTreeWidget {{
    background-color: {p["BG_PRIMARY"]};
    alternate-background-color: {p["ALT_ROW"]};
    gridline-color: {p["BORDER"]};
    border: 1px solid {p["BORDER"]};
    border-radius: 4px;
    selection-background-color: {p["SELECTION"]};
    selection-color: {p["SELECTED_TEXT"]};
    outline: none;
}}
QTableWidget::item, QTreeWidget::item {{
    padding: 6px;
    border-bottom: 1px solid {p["BORDER"]};
}}
QHeaderView::section {{
    background-color: {p["BG_ELEVATED"]};
    color: {p["TEXT_SECONDARY"]};
    padding: 8px;
    border: none;
    border-bottom: 1px solid {p["BORDER"]};
    font-weight: 600;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p["SCROLL_HANDLE"]};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p["SCROLL_HANDLE_HOVER"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {p["SCROLL_HANDLE"]};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

QToolTip {{
    background-color: {p["BG_ELEVATED"]};
    color: {p["TEXT_PRIMARY"]};
    border: 1px solid {p["BORDER"]};
    padding: 4px 6px;
}}
"""


def apply_theme(_dark: bool = True) -> None:
    """Aplica el tema oscuro (qfluentwidgets + QSS propio) a la QApplication
    ya creada. Siempre oscuro: main.py todavia pasa la preferencia vieja
    ("theme" en preferences.json), y una base de antes puede traer "light"
    guardado sin forma de volver desde la UI. Por eso se ignora."""
    setTheme(Theme.DARK)
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(build_stylesheet(dark=True))


STYLESHEET = build_stylesheet(dark=True)
