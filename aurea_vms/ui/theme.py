"""Tema visual global: paleta oscura o clara aplicada a nivel QApplication
(sidebar, botones, tablas, inputs, dialogos, scrollbars). El toggle vive en
Sistema > Sistema > Apariencia."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

ACCENT = "#3b82f6"

# Escala de severidad de alarmas -- UNICA fuente de verdad (antes vivia
# duplicada en alarm_module y global_alert_popup). Cualquier badge, borde
# de popup o dot que comunique severidad sale de aca. Candidata a
# evolucionar a los tokens del prototipo NOVA (crit #ff4d5e / high
# #ff9f43 / med #ffd166 / info #4f8cff + variantes soft): cambiar SOLO
# estos valores re-pinta toda la app.
SEVERITY_COLORS = {
    "critico": "#e5534b",
    "alto": "#f0a020",
    "medio": "#3b82f6",
    "info": "#6e7681",
}

# Colores de estado de camara (online/offline/sin probar) -- reservados
# para estado, no reciclarlos para otra semantica.
STATUS_COLORS = {"online": "#3fb950", "offline": "#e5534b", "unknown": "#6e7681"}

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


def apply_theme(dark: bool) -> None:
    """Aplica el tema (qfluentwidgets + QSS propio) a la QApplication ya
    creada -- usado tanto al arrancar como desde el toggle de Apariencia."""
    setTheme(Theme.DARK if dark else Theme.LIGHT)
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(build_stylesheet(dark))


STYLESHEET = build_stylesheet(dark=True)
