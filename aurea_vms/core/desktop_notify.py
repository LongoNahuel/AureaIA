"""Notificaciones de escritorio (bandeja del sistema de Windows) para
reglas de alarma con "notificar por escritorio" activado.

Es el unico canal de notificacion que implementamos de verdad: no hay
infraestructura de email/SMS en este proyecto, y fabricar checkboxes para
canales que no envian nada seria mentirle al usuario. QSystemTrayIcon ya
viene con Qt, no suma una dependencia nueva."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

_tray_icon: QSystemTrayIcon | None = None


def _ensure_tray_icon() -> QSystemTrayIcon | None:
    global _tray_icon
    app = QApplication.instance()
    if app is None or not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    if _tray_icon is None:
        _tray_icon = QSystemTrayIcon(app.windowIcon())
    return _tray_icon


def notify(title: str, message: str) -> None:
    tray_icon = _ensure_tray_icon()
    if tray_icon is None:
        return
    if not tray_icon.isVisible():
        tray_icon.show()
    tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Warning, 8000)
