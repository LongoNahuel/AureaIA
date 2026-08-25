from __future__ import annotations

import os
import sys

# RTSP sobre TCP para el backend FFmpeg de OpenCV: en wifi/redes con perdida
# el transporte UDP por defecto produce artifacting (bloques grises, frames
# rotos). Debe estar seteado antes de abrir cualquier VideoCapture; se usa
# setdefault para que un despliegue pueda overridearlo sin tocar codigo.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog
from qfluentwidgets import setThemeColor

from aurea_vms.config.settings import settings
from aurea_vms.core import app_prefs, auth, clip_recorder, retention
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


# Un thread no puede re-arrancarse: el ciclo logout->login crea un
# RetentionWorker nuevo en cada _start_background_engines().
_retention_worker: retention.RetentionWorker | None = None


def _start_background_engines() -> None:
    global _retention_worker
    _start_enabled_analytics()
    alarm_engine.start()
    _retention_worker = retention.RetentionWorker()
    _retention_worker.start()


def _stop_background_engines() -> None:
    # Orden importa: los clips en curso necesitan el stream vivo para su
    # post-buffer, asi que se espera ANTES de cortar streams/analiticas.
    clip_recorder.wait_for_pending(15.0)
    stream_manager.stop_all()
    analytics_engine.stop_all()
    alarm_engine.stop()
    if _retention_worker is not None:
        _retention_worker.stop()
        _retention_worker.join(timeout=2.0)


def _smoke_test() -> int:
    """Verificacion minima de que el entorno (o el .exe empaquetado) esta
    completo: directorios, DB, Qt y -- clave -- los CUATRO analizadores con
    sus modelos nativos reales. Las DLL de MediaPipe cargan recien al
    crear el primer detector (leccion del CI: importar mediapipe no
    alcanza), asi que el smoke crea los detectores y procesa un frame.

    Uso: AureaVMS.exe --smoke  (idealmente con AUREA_DATA_DIR a un tmp)."""
    import numpy as np

    from aurea_vms.core.analytics.registry import AVAILABLE_ANALYZERS, create_analyzer
    from aurea_vms.models.analytics_config import AnalyticsConfig

    settings.ensure_dirs()
    setup_logging()
    init_db()

    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    for name in AVAILABLE_ANALYZERS:
        params = {"line": [[0, 180], [640, 180]]} if name == "line_crossing" else {}
        config = AnalyticsConfig(
            device_id=0, analyzer_name=name, confidence_threshold=0.5, params=params
        )
        analyzer = create_analyzer(config)
        analyzer.process_frame(frame, 0.0)
        analyzer.close()
        print(f"smoke: {name} OK")

    app = QApplication(sys.argv[:1])
    app.processEvents()
    print("SMOKE OK")
    return 0


def main() -> int:
    if "--smoke" in sys.argv:
        return _smoke_test()

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
