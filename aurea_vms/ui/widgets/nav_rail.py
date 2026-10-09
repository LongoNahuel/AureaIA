"""Riel de navegacion a la izquierda de la ventana principal (Fase 2 de la
interfaz, 2026-10-08).

Antes se navegaba con las tarjetas de Inicio y las pestañas: para cambiar de
modulo habia que volver a Inicio. El riel queda siempre a la vista, con los
modulos de operacion arriba y los de configuracion abajo, el activo marcado
con el color de acento y, sobre Alarmas, cuantos incidentes hay sin
reconocer. Abre los modulos con la misma API que las tarjetas
(MainWindow.open_module_by_index), asi que las pestañas y las ventanas
secundarias siguen igual.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter
from PySide6.QtWidgets import QAbstractButton, QFrame, QVBoxLayout, QWidget

from aurea_vms.ui.theme import ACCENT, TONES

HOME = -1
RAIL_WIDTH = 80
_BACKGROUND = "#10151d"
_BORDER = "#222b37"
_HOVER = QColor("#1a212c")
_ACTIVE = QColor("#1b2638")
_MUTED = QColor("#8a93a0")
_TEXT = QColor("#e5e7eb")
ICON_SIZE = 22

# (indice en MODULES o HOME, rotulo corto, fabrica de icono)
RailItem = tuple[int, str, Callable[..., QIcon]]


class _RailButton(QAbstractButton):
    def __init__(self, index: int, label: str, icon_factory, parent: QWidget | None = None):
        super().__init__(parent)
        self.index = index
        self.label = label
        self.setToolTip(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(RAIL_WIDTH - 12, 58)
        self._icons = {
            "idle": icon_factory(_MUTED.name(), ICON_SIZE),
            "active": icon_factory(ACCENT, ICON_SIZE),
        }
        self.active = False
        self.badge = 0
        self._hover = False

    def sizeHint(self) -> QSize:  # noqa: N802 - override de Qt
        return QSize(RAIL_WIDTH - 12, 58)

    def enterEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self.active or self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_ACTIVE if self.active else _HOVER)
            painter.drawRoundedRect(rect, 10, 10)
        if self.active:
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(QRectF(0, rect.top() + 14, 3, rect.height() - 28), 1.5, 1.5)

        icon = self._icons["active" if self.active else "idle"]
        x = (self.width() - ICON_SIZE) / 2
        icon.paint(painter, int(x), 9, ICON_SIZE, ICON_SIZE)

        font = QFont(self.font())
        font.setPixelSize(10)
        font.setWeight(QFont.Weight.DemiBold if self.active else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(_TEXT if self.active else _MUTED)
        text = painter.fontMetrics().elidedText(
            self.label, Qt.TextElideMode.ElideRight, self.width() - 6
        )
        painter.drawText(
            QRectF(3, 35, self.width() - 6, 16),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            text,
        )

        if self.badge > 0:
            label = "99+" if self.badge > 99 else str(self.badge)
            font.setPixelSize(10)
            font.setWeight(QFont.Weight.Bold)
            painter.setFont(font)
            width = max(16, painter.fontMetrics().horizontalAdvance(label) + 8)
            pill = QRectF(x + ICON_SIZE - 6, 4, width, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(TONES["alert"]))
            painter.drawRoundedRect(pill, 8, 8)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), label)
        painter.end()


class NavRail(QFrame):
    requested = Signal(int)  # indice en MODULES, o HOME

    def __init__(
        self,
        operation: list[RailItem],
        configuration: list[RailItem],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("navRail")
        self.setFixedWidth(RAIL_WIDTH)
        self.setStyleSheet(
            f"#navRail {{ background: {_BACKGROUND}; border-right: 1px solid {_BORDER}; }}"
        )
        self.buttons: dict[int, _RailButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 10, 6, 10)
        layout.setSpacing(4)
        for item in operation:
            layout.addWidget(self._button(*item), 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        if configuration:
            separator = QFrame(self)
            separator.setFixedHeight(1)
            separator.setStyleSheet(f"background: {_BORDER};")
            layout.addWidget(separator)
            layout.addSpacing(4)
        for item in configuration:
            layout.addWidget(self._button(*item), 0, Qt.AlignmentFlag.AlignHCenter)

    def _button(self, index: int, label: str, icon_factory) -> _RailButton:
        button = _RailButton(index, label, icon_factory, self)
        button.clicked.connect(lambda _checked=False, i=index: self.requested.emit(i))
        self.buttons[index] = button
        return button

    def set_active(self, index: int | None) -> None:
        for each, button in self.buttons.items():
            active = each == index
            if button.active != active:
                button.active = active
                button.update()

    def active_index(self) -> int | None:
        return next((i for i, b in self.buttons.items() if b.active), None)

    def set_badge(self, index: int, count: int) -> None:
        button = self.buttons.get(index)
        if button is not None and button.badge != count:
            button.badge = count
            button.setToolTip(f"{button.label} · {count} sin reconocer" if count else button.label)
            button.update()
