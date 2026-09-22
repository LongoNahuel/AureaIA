"""Notificaciones de escritorio (bandeja del sistema) para reglas de alarma
con "notificar por escritorio" activado.

Es el unico canal de notificacion que implementamos de verdad: no hay
infraestructura de email/SMS en este proyecto, y fabricar checkboxes para
canales que no envian nada seria mentirle al usuario. QSystemTrayIcon ya
viene con Qt, no suma una dependencia nueva.

SOLO se puede llamar desde el hilo de la GUI. QSystemTrayIcon es un widget
de Qt: construirlo o tocarlo fuera del hilo que corre el event loop es
comportamiento indefinido -- segun la plataforma, un crash o una
notificacion que nunca aparece. Hasta hoy lo llamaba AlarmEngine._trigger,
que corre en el hilo del AnalyticsWorker; ahora la accion viaja como el
flag `notify_desktop` del AlarmEvent y la dispara MainWindow._on_global_alarm
(QueuedConnection, hilo principal), el mismo camino que ya usaba
`play_sound`. El guard de `notify()` esta para que el error no pueda volver
en silencio: una llamada desde otro hilo se loguea y no hace nada, en vez de
construir el widget igual.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

logger = logging.getLogger(__name__)

NOTIFICATION_TIMEOUT_MS = 8000

# Un unico icono de bandeja para toda la app. No lleva lock a proposito:
# despues del guard de notify() solo lo toca el hilo de la GUI, asi que el
# doble-check no tiene con quien correr una carrera.
_tray_icon: QSystemTrayIcon | None = None


def _is_gui_thread() -> bool:
    """True si el hilo actual es el que corre el event loop de Qt.

    Con la QApplication viva se compara contra su thread, que es la
    definicion exacta de Qt. Sin ella -- tests unitarios, o un import
    temprano antes de main() -- se cae al hilo principal de Python, que es
    donde main() termina creandola.
    """
    app = QApplication.instance()
    if app is not None:
        return QThread.currentThread() is app.thread()
    return threading.current_thread() is threading.main_thread()


def _ensure_tray_icon() -> QSystemTrayIcon | None:
    global _tray_icon
    app = QApplication.instance()
    if app is None:
        logger.debug("Notificación de escritorio omitida: todavía no hay QApplication")
        return None
    if not QSystemTrayIcon.isSystemTrayAvailable():
        # Pasa en escritorios Linux sin bandeja y en sesiones headless.
        # Antes era un no-op mudo: la regla decia "notificar" y no
        # notificaba nada, sin una sola linea en el log.
        logger.warning(
            "Notificación de escritorio omitida: el escritorio no expone bandeja del sistema"
        )
        return None
    if _tray_icon is None:
        # Con la app como padre: el icono se destruye con ella, en vez de
        # quedar como global sin dueño hasta que muere el interprete.
        _tray_icon = QSystemTrayIcon(app.windowIcon(), app)
    return _tray_icon


def notify(title: str, message: str) -> None:
    if not _is_gui_thread():
        logger.error(
            "desktop_notify.notify() llamado desde el hilo '%s': se ignora. "
            "QSystemTrayIcon solo se puede tocar desde el hilo de la GUI -- la "
            "acción notify_desktop viaja en el AlarmEvent y la dispara "
            "MainWindow._on_global_alarm.",
            threading.current_thread().name,
        )
        return
    tray_icon = _ensure_tray_icon()
    if tray_icon is None:
        return
    if not tray_icon.isVisible():
        tray_icon.show()
    tray_icon.showMessage(
        title, message, QSystemTrayIcon.MessageIcon.Warning, NOTIFICATION_TIMEOUT_MS
    )
