"""Boton de icono para usar como accion rapida dentro de una fila de
tabla (ej. Editar/Eliminar en una columna "Operacion").

Ojo: TransparentToolButton (qfluentwidgets) dispara crashes nativos
intermitentes cuando se usa como cell widget dentro de un TableWidget (el
delegate de hover/seleccion de la libreria pelea con el widget embebido
-- reproducido de forma aislada, confirmado que no ocurre con QToolButton
plano). Por eso este helper usa QToolButton en vez de TransparentToolButton
para cualquier boton que vaya a vivir dentro de una celda de tabla."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton
from qfluentwidgets import FluentIcon

_ROW_BUTTON_QSS = """
QToolButton { border: none; border-radius: 4px; padding: 4px; background: transparent; }
QToolButton:hover { background: rgba(255, 255, 255, 30); }
QToolButton:pressed { background: rgba(255, 255, 255, 50); }
"""


def row_icon_button(icon: FluentIcon, tooltip: str) -> QToolButton:
    button = QToolButton()
    button.setIcon(icon.icon())
    button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(_ROW_BUTTON_QSS)
    return button
