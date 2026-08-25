from __future__ import annotations

import sys

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog
from qfluentwidgets import setThemeColor

from aurea_vms.config.settings import settings
from aurea_vms.core import app_prefs, auth, clip_recorder
from aurea_vms.core.alarm_engine import alarm_engine
from aurea_vms.core.analytics_engine import analytics_engine
from aurea_vms.core.logging_setup import setup_logging
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository
from aurea_vms.models.db import init_db
from aurea_vms.ui.dialogs.login_dialog import LoginDialog
from aurea_vms.ui.dialogs.setup_wizard_dialog import SetupWizardDialog
from aurea_vms.ui.main_window import MainWindow
from aurea_vms.ui.theme import ACCENT, apply_theme


def _start_enabled_analytics() -> None:
    for config in repository.list_analytics_configs():
        if not config.enabled:
            continue
        device = repository.get_device(config.device_id)
        if device is not None:
            analytics_engine.start(config, device)


def _start_background_engines() -> None:
    _start_enabled_analytics()
    alarm_engine.start()


def _stop_background_engines() -> None:
    # Orden importa: los clips en curso necesitan el stream vivo para su
    # post-buffer, asi que se espera ANTES de cortar streams/analiticas.
    clip_recorder.wait_for_pending(15.0)
    stream_manager.stop_all()
    analytics_engine.stop_all()
    alarm_engine.stop()


def main() -> int:
    settings.ensure_dirs()
    setup_logging()
    init_db()

    app = QApplication(sys.argv)
    app.setApplicationName("AureaIA VMS")
    setThemeColor(QColor(ACCENT))
    apply_theme(app_prefs.get_theme() == "dark")

    # Primer arranque (sin usuarios todavia): alta del Super Administrador.
    # Si se cierra el wizard sin completarlo, la app no llega a abrir.
    if not auth.has_admin_user() and SetupWizardDialog().exec() != QDialog.DialogCode.Accepted:
        return 0

    # Login <-> MainWindow: "Cerrar sesión" cierra la ventana principal y
    # vuelve a mostrar el login (sin salir del proceso); cerrar la ventana
    # con la X, o cancelar el login, termina la app.
    while True:
        if LoginDialog().exec() != QDialog.DialogCode.Accepted:
            return 0

        _start_background_engines()
        window = MainWindow()
        window.show()
        app.exec()
        _stop_background_engines()

        if not window.logout_requested:
            return 0


if __name__ == "__main__":
    sys.exit(main())
