"""Barra superior de la ventana principal (Fase 2 de la interfaz, 2026-10-08):
la marca, el estado global y la sesion en una sola fila.

- Estado global, a la vista desde cualquier modulo: camaras en linea y
  alertas sin reconocer. La de alertas lleva a Alarmas.
- Hora, sitio (filtra toda la app, como antes) y el usuario, con
  "Cerrar sesión" en su menu.

Antes la cabecera solo tenia usuario, sitio y "Cerrar sesión", y el estado
del sistema estaba unicamente en Inicio.
"""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget
from qfluentwidgets import Action, ComboBox, RoundMenu

from aurea_vms.ui import icons
from aurea_vms.ui.theme import ACCENT, TONES, dot_icon, rgba

BAR_HEIGHT = 52
_BACKGROUND = "#10151d"
_BORDER = "#222b37"
_MUTED = "#8a93a0"
CLOCK_REFRESH_MS = 15000


class _Avatar(QWidget):
    """Circulo con las iniciales del usuario."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.initials = "".join(part[0] for part in name.split()[:2]).upper() or "?"
        self.setFixedSize(30, 30)

    def paintEvent(self, _event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT))
        painter.drawEllipse(self.rect())
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self.initials[:2])
        painter.end()


class _StatusChip(QPushButton):
    """Punto de color + texto, en una pildora. Clickeable si `clickable`."""

    def __init__(self, parent: QWidget | None = None, *, clickable: bool = False) -> None:
        super().__init__(parent)
        self.tone = "neutral"
        self.setFlat(True)
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.set_status("—", "neutral")

    def set_status(self, text: str, tone: str) -> None:
        self.tone = tone
        color = TONES.get(tone, TONES["neutral"])
        alert = tone == "alert"
        background = rgba(color, 0.14) if alert else "#18202b"
        border = rgba(color, 0.45) if alert else _BORDER
        # El punto lleva el color del estado (verde, ambar, rojo); el texto
        # queda claro salvo cuando hay que actuar.
        self.setIcon(dot_icon(color if tone != "neutral" else _MUTED, 8))
        self.setText(text)
        self.setStyleSheet(
            f"QPushButton {{ color: {color if alert else '#d6dae0'}; background: {background};"
            f" border: 1px solid {border}; border-radius: 13px; padding: 4px 12px;"
            f" font-size: 12px; font-weight: {'700' if alert else '500'}; }}"
            f"QPushButton:hover {{ border-color: {color}; }}"
        )


class TopBar(QFrame):
    alerts_clicked = Signal()
    logout_requested = Signal()

    def __init__(self, username: str, role_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setFixedHeight(BAR_HEIGHT)
        self.setStyleSheet(
            f"#topBar {{ background: {_BACKGROUND}; border-bottom: 1px solid {_BORDER}; }}"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 14, 0)
        row.setSpacing(10)

        logo = QLabel(self)
        logo.setPixmap(icons.app_logo_pixmap(26))
        row.addWidget(logo)
        brand = QLabel("AureaIA", self)
        brand.setStyleSheet("color: #f3f4f6; font-size: 15px; font-weight: 700;")
        row.addWidget(brand)
        product = QLabel("VMS", self)
        product.setStyleSheet(f"color: {_MUTED}; font-size: 12px; font-weight: 600;")
        row.addWidget(product)

        row.addSpacing(18)
        self.cameras_chip = _StatusChip(self)
        self.alerts_chip = _StatusChip(self, clickable=True)
        self.alerts_chip.setToolTip("Ver los incidentes sin reconocer en Alarmas")
        self.alerts_chip.clicked.connect(self.alerts_clicked)
        row.addWidget(self.cameras_chip)
        row.addWidget(self.alerts_chip)
        row.addStretch(1)

        self.clock = QLabel(self)
        self.clock.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
        row.addWidget(self.clock)
        row.addSpacing(6)

        # Selector global de sitio: filtra Vista en Vivo, Dispositivos y
        # Alarmas en toda la app (MainWindow lo conecta a app_state).
        self.site_combo = ComboBox(self)
        self.site_combo.setMinimumWidth(170)
        row.addWidget(self.site_combo)
        row.addSpacing(6)

        self.user_button = QPushButton(self)
        self.user_button.setFlat(True)
        self.user_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.user_button.setStyleSheet(
            "QPushButton { border: none; padding: 2px 4px; border-radius: 8px; }"
            "QPushButton:hover { background: #1a212c; }"
        )
        user_row = QHBoxLayout(self.user_button)
        user_row.setContentsMargins(6, 2, 8, 2)
        user_row.setSpacing(8)
        user_row.addWidget(_Avatar(username, self.user_button))
        who = QLabel(
            f"<span style='color:#e5e7eb; font-weight:600'>{username}</span><br>"
            f"<span style='color:{_MUTED}; font-size:11px'>{role_label}</span>",
            self.user_button,
        )
        who.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        user_row.addWidget(who)
        self.user_button.setMinimumSize(QSize(150, 40))
        self.user_button.clicked.connect(self._show_user_menu)
        row.addWidget(self.user_button)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(CLOCK_REFRESH_MS)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start()
        self._tick()

    def _tick(self) -> None:
        now = dt.datetime.now()
        self.clock.setText(now.strftime("%d/%m  %H:%M"))

    def _show_user_menu(self) -> None:
        menu = RoundMenu(parent=self)
        logout = Action(icons.icon_logout(), "Cerrar sesión")
        logout.triggered.connect(self.logout_requested)
        menu.addAction(logout)
        menu.exec(self.user_button.mapToGlobal(self.user_button.rect().bottomLeft()))

    def set_status(self, cameras: tuple[str, str, str], unacknowledged: int) -> None:
        """`cameras`: (valor, tono, detalle) de dashboard_panel.cameras_summary."""
        value, tone, detail = cameras
        self.cameras_chip.set_status(f"Cámaras {value}" + (f" · {detail}" if detail else ""), tone)
        self.cameras_chip.setToolTip(detail or "Todas las cámaras en línea")
        if unacknowledged:
            self.alerts_chip.set_status(f"{unacknowledged} sin reconocer", "alert")
        else:
            self.alerts_chip.set_status("Sin alertas pendientes", "ok")
