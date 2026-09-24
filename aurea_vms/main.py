from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

# RTSP sobre TCP para el backend FFmpeg de OpenCV: en wifi/redes con perdida
# el transporte UDP por defecto produce artifacting (bloques grises, frames
# rotos). Debe estar seteado antes de abrir cualquier VideoCapture; se usa
# setdefault para que un despliegue pueda overridearlo sin tocar codigo.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    # El flujo principal del NVR es HEVC y necesita margen para reordenar
    # paquetes al comenzar en mitad de un GOP. Una cola demasiado agresiva
    # (nobuffer/reorder_queue_size=1) deja el decoder sin VPS/PPS y bloquea
    # esperando un keyframe que nunca llega.
    "rtsp_transport;tcp|fflags;discardcorrupt|flags;low_delay|max_delay;1500000|reorder_queue_size;4",
)

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from qfluentwidgets import setThemeColor

from aurea_vms.config.settings import settings
from aurea_vms.core import app_prefs, auth, clip_recorder, credential_store, retention
from aurea_vms.core.alarm_engine import alarm_engine
from aurea_vms.core.analytics_engine import analytics_engine
from aurea_vms.core.logging_setup import setup_logging
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.migrations.resguardo import BACKUPS_DIRNAME
from aurea_vms.models import repository
from aurea_vms.models.db import init_db
from aurea_vms.ui.dialogs.login_dialog import LoginDialog
from aurea_vms.ui.dialogs.setup_wizard_dialog import SetupWizardDialog
from aurea_vms.ui.main_window import MainWindow
from aurea_vms.ui.theme import ACCENT, apply_theme


def _ensure_linux_qt_plugin_path() -> None:
    """El wheel Linux de opencv-python trae su propia copia de Qt y deja a
    Qt buscando plugins de plataforma en site-packages/cv2/qt/plugins, donde
    el xcb no carga -> la app aborta antes de mostrar nada. Se apunta a los
    plugins de PySide6, salvo que el usuario ya la haya seteado a mano.

    Debe llamarse despues de los imports del modulo (cv2 ya quedo cargado
    via aurea_vms.core) y antes de crear la QApplication."""
    if not sys.platform.startswith("linux"):
        return
    current = os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH", "")
    if current and "/cv2/" not in current:
        return
    import PySide6

    platforms = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins" / "platforms"
    if platforms.is_dir():
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platforms)


def _warn_if_missing_xcb_cursor() -> None:
    """Qt >= 6.5 exige libxcb-cursor0 para el plugin xcb; si falta, Qt
    aborta con core dump sin nombrar el paquete. Avisar de antemano."""
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("QT_QPA_PLATFORM") in {"offscreen", "minimal", "wayland"}:
        return
    import ctypes.util

    if ctypes.util.find_library("xcb-cursor") is None:
        print(
            "AVISO: falta libxcb-cursor0 (obligatoria para el plugin xcb de Qt >= 6.5). "
            "Si la app aborta al arrancar: sudo apt install libxcb-cursor0",
            file=sys.stderr,
        )


def _start_enabled_analytics() -> None:
    for config in repository.list_analytics_configs():
        if not config.enabled:
            continue
        device = repository.get_device(config.device_id)
        if device is None:
            continue
        try:
            analytics_engine.start(config, device)
        except Exception:  # noqa: BLE001 - un config roto no debe impedir el arranque
            logging.getLogger(__name__).exception(
                "No se pudo iniciar la analítica %s de la cámara %s (config %s)",
                config.analyzer_name,
                device.name,
                config.id,
            )


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
    clip_recorder.wait_for_pending()
    stream_manager.stop_all()
    analytics_engine.stop_all()
    alarm_engine.stop()
    if _retention_worker is not None:
        _retention_worker.stop()
        _retention_worker.join(timeout=2.0)


def _smoke_test() -> int:
    """Verificacion de que el entorno (o el .exe empaquetado) esta completo.
    Los chequeos viven en aurea_vms/smoke.py.

    Uso: AureaVMS.exe --smoke  (con AUREA_DATA_DIR a un directorio VACIO)."""
    from aurea_vms import smoke

    return smoke.run()


CONTINUAR, REGENERAR, SALIR = "continuar", "regenerar", "salir"


def _texto_clave_perdida() -> str:
    ruta = settings.data_dir / credential_store.KEY_FILENAME
    backups = sorted(
        (settings.db_path.parent / BACKUPS_DIRNAME).glob(f"*.{credential_store.KEY_FILENAME}"),
        reverse=True,
    )
    lineas = [
        "No se pueden descifrar las contraseñas de cámara guardadas.",
        "",
        f"Motivo: {credential_store.motivo()}.",
        f"Cámaras afectadas: {credential_store.credenciales_ilegibles()}. "
        "Quedan sin conectar hasta resolverlo; las demás funcionan normalmente.",
        "",
        f"Para recuperarlas, cerrá la app y copiá la clave original a:\n{ruta}",
    ]
    if backups:
        lineas += ["", "Copias de la clave junto a los backups de la base:"]
        lineas += [f"  • {b}" for b in backups[:3]]
    lineas += [
        "",
        "Regenerar crea una clave nueva: las contraseñas guardadas se pierden y hay "
        "que volver a cargarlas en cada cámara.",
    ]
    return "\n".join(lineas)


def _preguntar_clave_perdida(texto: str) -> str:
    caja = QMessageBox(QMessageBox.Icon.Warning, "Clave de credenciales perdida", texto)
    continuar = caja.addButton("Continuar sin esas cámaras", QMessageBox.ButtonRole.AcceptRole)
    regenerar = caja.addButton("Regenerar clave…", QMessageBox.ButtonRole.DestructiveRole)
    caja.addButton("Salir", QMessageBox.ButtonRole.RejectRole)
    caja.setDefaultButton(continuar)
    caja.exec()
    elegido = caja.clickedButton()
    if elegido is continuar:
        return CONTINUAR
    return REGENERAR if elegido is regenerar else SALIR


def _confirmar_regenerar(afectadas: int) -> bool:
    respuesta = QMessageBox.warning(
        None,
        "Regenerar la clave",
        f"Las contraseñas guardadas de {afectadas} cámara(s) se pierden para siempre "
        "y hay que volver a cargarlas. La clave actual, si existe, se renombra (no se "
        "borra).\n\n¿Regenerar?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return respuesta == QMessageBox.StandardButton.Yes


def _resolver_clave_perdida(
    preguntar=_preguntar_clave_perdida, confirmar=_confirmar_regenerar
) -> bool:
    """Aviso de bootstrap si `init_db` encontro la clave perdida (ver
    core/credential_store.py). Devuelve False si hay que salir.

    Salir del estado degradado es explicito: regenerar pide una segunda
    confirmacion que dice cuanto se pierde. Si no se confirma, la app sigue
    degradada: las camaras con contraseña no conectan y guardar una
    contraseña nueva da error, hasta restaurar la clave."""
    if credential_store.estado() != credential_store.ESTADO_CLAVE_PERDIDA:
        return True
    eleccion = preguntar(_texto_clave_perdida())
    if eleccion == SALIR:
        return False
    if eleccion == REGENERAR and confirmar(credential_store.credenciales_ilegibles()):
        apartada = credential_store.regenerar_clave()
        logging.getLogger(__name__).warning(
            "Clave de credenciales regenerada por el operador (la anterior: %s)", apartada
        )
    return True


def _instalar_excepthooks() -> None:
    """Una excepcion no capturada en un hilo (threading.excepthook) o en el
    hilo principal fuera de Qt (sys.excepthook) se imprimia en stderr, y en
    el .exe sin consola stderr no existe: el hilo moria sin dejar rastro.
    Ahora va al log, con traza. Se conserva el hook anterior para el caso de
    desarrollo con consola."""
    log = logging.getLogger("aurea_vms.excepciones")
    hook_de_hilos = threading.excepthook
    hook_de_sys = sys.excepthook

    def en_hilo(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is not SystemExit:
            nombre = args.thread.name if args.thread is not None else "?"
            log.critical(
                "Excepción no capturada en el hilo %s",
                nombre,
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        hook_de_hilos(args)

    def en_proceso(exc_type, exc_value, exc_traceback) -> None:
        if not issubclass(exc_type, KeyboardInterrupt):
            log.critical("Excepción no capturada", exc_info=(exc_type, exc_value, exc_traceback))
        hook_de_sys(exc_type, exc_value, exc_traceback)

    threading.excepthook = en_hilo
    sys.excepthook = en_proceso


def main() -> int:
    _ensure_linux_qt_plugin_path()
    _warn_if_missing_xcb_cursor()

    if "--smoke" in sys.argv:
        return _smoke_test()

    settings.ensure_dirs()
    setup_logging()
    _instalar_excepthooks()
    init_db()

    app = QApplication(sys.argv)
    app.setApplicationName("AureaIA VMS")
    setThemeColor(QColor(ACCENT))
    apply_theme(app_prefs.get_theme() == "dark")

    if not _resolver_clave_perdida():
        return 0

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
