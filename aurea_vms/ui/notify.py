"""Notificaciones y confirmaciones estandar (Fluent) para reemplazar
QMessageBox en toda la app: toast no bloqueante para avisos, dialogo
modal solo para confirmaciones que de verdad lo ameritan (borrar algo)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QWidget
from qfluentwidgets import InfoBar, InfoBarPosition, MessageBox, PushButton


def warn(parent: QWidget, title: str, content: str) -> None:
    InfoBar.warning(
        title=title,
        content=content,
        duration=3500,
        position=InfoBarPosition.TOP,
        parent=parent.window(),
    )


def warn_con_accion(
    parent: QWidget, title: str, content: str, texto_accion: str, accion: Callable[[], None]
) -> InfoBar:
    """Aviso que se queda hasta que lo cierren, con un boton que lleva a
    resolverlo (ej. "Configurar"). Para lo que el operador no puede dejar
    pasar: un toast de 3,5 s con un boton no da tiempo a usarlo."""
    bar = InfoBar.warning(
        title=title,
        content=content,
        duration=-1,
        position=InfoBarPosition.TOP,
        parent=parent.window(),
    )
    boton = PushButton(texto_accion, bar)
    boton.clicked.connect(accion)
    boton.clicked.connect(bar.close)
    bar.addWidget(boton)
    return bar


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
