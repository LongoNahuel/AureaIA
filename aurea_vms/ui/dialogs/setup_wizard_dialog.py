"""Primer inicio (sin usuarios en la base todavia): alta del Super
Administrador, al estilo del wizard de primer arranque de EZStation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    ProgressBar,
    StrongBodyLabel,
)

from aurea_vms.core import auth
from aurea_vms.ui import icons
from aurea_vms.ui.widgets.branded_background import BrandedBackground

STRENGTH_STYLE = {
    "": ("#3a4353", ""),
    "Débil": ("#e5534b", "Débil"),
    "Media": ("#f0a020", "Media"),
    "Fuerte": ("#3fb950", "Fuerte"),
}
STRENGTH_VALUE = {"": 0, "Débil": 33, "Media": 66, "Fuerte": 100}


class SetupWizardDialog(QDialog):
    """Se muestra una unica vez, cuando la base no tiene ningun usuario
    todavia. Al aceptar, ya existe el Super Administrador en la DB."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuración inicial — AureaIA VMS")
        self.setFixedSize(620, 420)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        overlay = QColor(8, 11, 18, 175)
        brand_panel = BrandedBackground(icons.algorithm_background_pixmap(), overlay, self)
        brand_panel.setFixedWidth(220)
        brand_layout = QVBoxLayout(brand_panel)
        brand_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout.setSpacing(12)

        logo_label = CaptionLabel(brand_panel)
        logo_label.setPixmap(icons.app_logo_pixmap(72))
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout.addWidget(logo_label)

        name_label = StrongBodyLabel("AureaIA VMS", brand_panel)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet("font-size: 17px;")
        brand_layout.addWidget(name_label)

        tagline_label = CaptionLabel("Software VMS de vigilancia por RTSP", brand_panel)
        tagline_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tagline_label.setWordWrap(True)
        brand_layout.addWidget(tagline_label)

        layout.addWidget(brand_panel)

        form_panel = QWidget(self)
        form_layout = QVBoxLayout(form_panel)
        form_layout.setContentsMargins(36, 36, 36, 36)
        form_layout.setSpacing(10)

        title = StrongBodyLabel("Crear Super Administrador", form_panel)
        title.setStyleSheet("font-size: 18px;")
        form_layout.addWidget(title)

        subtitle = CaptionLabel(
            "Esta cuenta va a tener acceso completo al sistema. Es la única vez que se pide.",
            form_panel,
        )
        subtitle.setWordWrap(True)
        form_layout.addWidget(subtitle)
        form_layout.addSpacing(8)

        self.username_edit = LineEdit(form_panel)
        self.username_edit.setText("admin")
        self.username_edit.setPlaceholderText("Usuario")
        form_layout.addWidget(self.username_edit)

        self.password_edit = PasswordLineEdit(form_panel)
        self.password_edit.setPlaceholderText("Contraseña")
        self.password_edit.textChanged.connect(self._on_password_changed)
        form_layout.addWidget(self.password_edit)

        self.strength_bar = ProgressBar(form_panel)
        self.strength_bar.setRange(0, 100)
        self.strength_bar.setValue(0)
        self.strength_bar.setFixedHeight(4)
        form_layout.addWidget(self.strength_bar)

        self.strength_label = CaptionLabel(
            "Al menos 9 caracteres, combinando letras, números y símbolos.", form_panel
        )
        self.strength_label.setWordWrap(True)
        form_layout.addWidget(self.strength_label)

        self.confirm_edit = PasswordLineEdit(form_panel)
        self.confirm_edit.setPlaceholderText("Confirmar contraseña")
        form_layout.addWidget(self.confirm_edit)

        self.error_label = CaptionLabel("", form_panel)
        self.error_label.setStyleSheet("color: #e5534b;")
        self.error_label.setWordWrap(True)
        form_layout.addWidget(self.error_label)

        form_layout.addStretch(1)

        self.next_button = PrimaryPushButton("Crear cuenta", form_panel)
        self.next_button.clicked.connect(self._on_accept)
        form_layout.addWidget(self.next_button)

        layout.addWidget(form_panel, stretch=1)

    def _on_password_changed(self, password: str) -> None:
        strength = auth.password_strength(password)
        color, text = STRENGTH_STYLE[strength]
        self.strength_bar.setValue(STRENGTH_VALUE[strength])
        self.strength_bar.setCustomBarColor(QColor(color), QColor(color))
        if text:
            self.strength_label.setText(f"Fortaleza: {text}")
            self.strength_label.setStyleSheet(f"color: {color};")
        else:
            self.strength_label.setText(
                "Al menos 9 caracteres, combinando letras, números y símbolos."
            )
            self.strength_label.setStyleSheet("")

    def _on_accept(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        confirm = self.confirm_edit.text()

        if not username:
            self.error_label.setText("El usuario es obligatorio.")
            return
        error = auth.validate_password(password)
        if error:
            self.error_label.setText(error)
            return
        if password != confirm:
            self.error_label.setText("Las contraseñas no coinciden.")
            return

        auth.create_admin_user(username, password)
        self.accept()
