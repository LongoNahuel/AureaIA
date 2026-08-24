"""Notificaciones y confirmaciones estandar (Fluent) para reemplazar
QMessageBox en toda la app: toast no bloqueante para avisos, dialogo
modal solo para confirmaciones que de verdad lo ameritan (borrar algo)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget
from qfluentwidgets import InfoBar, InfoBarPosition, MessageBox


def warn(parent: QWidget, title: str, content: str) -> None:
    InfoBar.warning(
        title=title,
        content=content,
        duration=3500,
        position=InfoBarPosition.TOP,
        parent=parent.window(),
    )


def notify(parent: QWidget, title: str, content: str) -> None:
    InfoBar.success(
        title=title,
        content=content,
        duration=3000,
        position=InfoBarPosition.TOP,
        parent=parent.window(),
    )


def confirm(parent: QWidget, title: str, content: str) -> bool:
    box = MessageBox(title, content, parent.window())
    return bool(box.exec())
