"""Gestion de Usuarios: alta/edicion/borrado de cuentas locales, con rol
(Administrador / Operador). MainWindow ya filtra el acceso a este modulo
a usuarios admin antes de que llegue aca."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
)

from aurea_vms.core import auth
from aurea_vms.models import repository
from aurea_vms.models.user import ROLE_LABELS, ROLES, User
from aurea_vms.ui.notify import confirm, notify, warn
from aurea_vms.ui.widgets.row_icon_button import row_icon_button

COLUMNS = ["Usuario", "Rol", "Operación"]


class _UserDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nuevo usuario")
        self.resize(340, 0)

        self.username_edit = LineEdit()
        self.password_edit = PasswordLineEdit()
        self.confirm_edit = PasswordLineEdit()
        self.role_combo = ComboBox()
        for role in ROLES:
            self.role_combo.addItem(ROLE_LABELS[role], userData=role)

        form = QFormLayout()
        form.addRow("Usuario:", self.username_edit)
        form.addRow("Contraseña:", self.password_edit)
        form.addRow("Confirmar contraseña:", self.confirm_edit)
        form.addRow("Rol:", self.role_combo)
        form.addRow(BodyLabel("Al menos 9 caracteres, combinando letras, números y símbolos."))

        cancel_button = PushButton("Cancelar")
        save_button = PrimaryPushButton(FluentIcon.SAVE, "Crear")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self._on_accept)
        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(cancel_button)
        buttons_row.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons_row)

    def _on_accept(self) -> None:
        username = self.username_edit.text().strip()
        if not username:
            warn(self, "Datos incompletos", "El usuario es obligatorio.")
            return
        if repository.get_user_by_username(username) is not None:
            warn(self, "Datos incompletos", "Ya existe un usuario con ese nombre.")
            return
        if self.password_edit.text() != self.confirm_edit.text():
            warn(self, "Datos incompletos", "Las contraseñas no coinciden.")
            return
        error = auth.validate_password(self.password_edit.text())
        if error:
            warn(self, "Datos incompletos", error)
            return
        self.accept()

    def values(self) -> dict:
        return {
            "username": self.username_edit.text().strip(),
            "password": self.password_edit.text(),
            "role": self.role_combo.currentData(),
        }


class _ResetPasswordDialog(QDialog):
    def __init__(self, username: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Restablecer contraseña — {username}")
        self.resize(320, 0)

        self.password_edit = PasswordLineEdit()
        self.confirm_edit = PasswordLineEdit()
        form = QFormLayout()
        form.addRow("Contraseña nueva:", self.password_edit)
        form.addRow("Confirmar:", self.confirm_edit)
        form.addRow(BodyLabel("Al menos 9 caracteres, combinando letras, números y símbolos."))

        cancel_button = PushButton("Cancelar")
        save_button = PrimaryPushButton(FluentIcon.SAVE, "Restablecer")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self._on_accept)
        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(cancel_button)
        buttons_row.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons_row)

    def _on_accept(self) -> None:
        if self.password_edit.text() != self.confirm_edit.text():
            warn(self, "Datos incompletos", "Las contraseñas no coinciden.")
            return
        self.accept()

    def password(self) -> str:
        return self.password_edit.text()


class UserManagementModule(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._users: list[User] = []

        header_row = QHBoxLayout()
        header_row.addWidget(StrongBodyLabel("Usuarios"))
        header_row.addStretch(1)
        add_button = PrimaryPushButton(FluentIcon.ADD, "Agregar usuario")
        add_button.clicked.connect(self._on_add)
        header_row.addWidget(add_button)

        self.table = TableWidget(self)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(6)

        layout = QVBoxLayout(self)
        layout.addLayout(header_row)
        layout.addWidget(self.table)

        self._reload()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._reload()
        super().showEvent(event)

    def _reload(self) -> None:
        self._users = repository.list_users()
        self.table.setRowCount(len(self._users))
        for row, user in enumerate(self._users):
            self.table.setItem(row, 0, QTableWidgetItem(user.username))
            self.table.setItem(row, 1, QTableWidgetItem(ROLE_LABELS.get(user.role, user.role)))
            self.table.setCellWidget(row, 2, self._operation_widget(user))

    def _operation_widget(self, user: User) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(2)

        reset_button = row_icon_button(FluentIcon.SYNC, "Restablecer contraseña")
        reset_button.clicked.connect(lambda _checked=False, u=user: self._on_reset_password(u))
        row.addWidget(reset_button)

        is_self = auth.current_user is not None and auth.current_user.id == user.id
        delete_button = row_icon_button(FluentIcon.DELETE, "Eliminar")
        delete_button.setEnabled(not is_self)
        delete_button.clicked.connect(lambda _checked=False, u=user: self._on_delete(u))
        row.addWidget(delete_button)

        return widget

    def _on_add(self) -> None:
        dialog = _UserDialog(self)
        if dialog.exec():
            values = dialog.values()
            auth.create_user(values["username"], values["password"], values["role"])
            notify(self, "Usuarios", f'Usuario "{values["username"]}" creado.')
            self._reload()

    def _on_reset_password(self, user: User) -> None:
        dialog = _ResetPasswordDialog(user.username, self)
        if dialog.exec():
            error = auth.admin_reset_password(user.id, dialog.password())
            if error:
                warn(self, "Restablecer contraseña", error)
                return
            notify(self, "Restablecer contraseña", f'Contraseña de "{user.username}" actualizada.')

    def _on_delete(self, user: User) -> None:
        if confirm(self, "Eliminar usuario", f'¿Eliminar el usuario "{user.username}"?'):
            repository.delete_user(user.id)
            self._reload()
