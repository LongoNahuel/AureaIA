"""Pantalla de inicio de sesion: se muestra al arrancar la app cuando ya
existe un Super Administrador dado de alta (ver setup_wizard_dialog.py
para el primer arranque)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    StrongBodyLabel,
)

from aurea_vms.core import auth
from aurea_vms.ui import icons
from aurea_vms.ui.widgets.video_background import VideoBackground

CARD_STYLE = "background-color: rgba(18, 23, 33, 235); border-radius: 10px;"


class LoginDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Iniciar sesión — AureaIA VMS")
        self.setFixedSize(380, 440)

        overlay = QColor(8, 11, 18, 175)
        background = VideoBackground(icons.login_background_video_path(), overlay, self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(background)

        outer_bg = QVBoxLayout(background)
        outer_bg.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QWidget(background)
        card.setFixedWidth(280)
        card.setStyleSheet(CARD_STYLE)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(10)

        logo_label = CaptionLabel(card)
        logo_label.setPixmap(icons.app_logo_pixmap(56))
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(logo_label)

        title = StrongBodyLabel("AureaIA VMS", card)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 17px;")
        card_layout.addWidget(title)
        card_layout.addSpacing(8)

        self.username_edit = LineEdit(card)
        self.username_edit.setPlaceholderText("Usuario")
        card_layout.addWidget(self.username_edit)

        self.password_edit = PasswordLineEdit(card)
        self.password_edit.setPlaceholderText("Contraseña")
        self.password_edit.returnPressed.connect(self._on_login)
        card_layout.addWidget(self.password_edit)

        self.error_label = CaptionLabel("", card)
        self.error_label.setStyleSheet("color: #e5534b;")
        self.error_label.setWordWrap(True)
        card_layout.addWidget(self.error_label)

        login_button = PrimaryPushButton("Ingresar", card)
        login_button.clicked.connect(self._on_login)
        card_layout.addWidget(login_button)

        outer_bg.addWidget(card)
        self.username_edit.setFocus()

    def _on_login(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if auth.login(username, password) is not None:
            self.accept()
            return
        self.error_label.setText("Usuario o contraseña incorrectos.")
        self.password_edit.clear()
        self.password_edit.setFocus()
