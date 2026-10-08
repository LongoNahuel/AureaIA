"""Fase R2 (2026-10-08): la retencion se confirma con un boton y el admin
se entera de lo que hace (o de que no hace nada).

Sin R2, la R1 dejaba la retencion sin forma de activarse: las spinbox de
Sistema > Audio y Video > Grabando grababan con los setters sueltos, que no
confirman, y el disco se llenaba sin que nadie lo supiera.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid

from aurea_vms.core import app_prefs, auth, retention
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import (
    RETENCION_AL_TOPE,
    RETENCION_EVIDENCIA_PROTEGIDA,
    RETENCION_PREFS_ILEGIBLES,
    RETENCION_SIN_CONFIGURAR,
    RetentionStatus,
)
from aurea_vms.models.user import User
from aurea_vms.ui import main_window as mw
from aurea_vms.ui.modules import system_module
from aurea_vms.ui.modules.system_module import SystemModule


def _usuario(rol: str) -> None:
    auth.current_user = User(username=rol, password_hash="h", salt="s", role=rol)


# --- La pagina Grabando ------------------------------------------------------


@pytest.fixture()
def sistema(qtbot, temp_db, monkeypatch):
    _usuario("admin")
    monkeypatch.setattr(system_module, "notify", lambda *a, **k: None)
    modulo = SystemModule()
    qtbot.addWidget(modulo)
    return modulo


def _responder(monkeypatch, respuesta: bool) -> list[str]:
    preguntas: list[str] = []

    def confirm_falso(_parent, _titulo, texto):
        preguntas.append(texto)
        return respuesta

    monkeypatch.setattr(system_module, "confirm", confirm_falso)
    return preguntas


class TestPaginaGrabando:
    def test_sin_configurar_lo_dice(self, sistema):
        assert "Sin configurar" in sistema.retencion_estado.text()

    def test_mover_los_valores_no_graba(self, sistema):
        """Antes cada valueChanged escribia el archivo."""
        sistema.retencion_dias.setValue(90)
        sistema.retencion_gb.setValue(500)

        assert not app_prefs._PREFS_PATH.exists()
        assert app_prefs.retencion_configurada() is False

    def test_guardar_confirma_y_graba(self, sistema, monkeypatch):
        preguntas = _responder(monkeypatch, True)
        sistema.retencion_dias.setValue(90)
        sistema.retencion_gb.setValue(500)

        sistema.guardar_retencion.click()

        assert app_prefs.leer_retencion() == (90.0, 500.0)
        assert "90 días" in preguntas[0] and "500.0 GB" in preguntas[0]
        assert "Configurada el" in sistema.retencion_estado.text()

    def test_cancelar_no_graba(self, sistema, monkeypatch):
        _responder(monkeypatch, False)

        sistema.guardar_retencion.click()

        assert app_prefs.retencion_configurada() is False
        assert "Sin configurar" in sistema.retencion_estado.text()

    def test_abre_con_lo_guardado(self, qtbot, temp_db):
        _usuario("admin")
        app_prefs.confirmar_retencion(30, 250)

        modulo = SystemModule()
        qtbot.addWidget(modulo)

        assert modulo.retencion_dias.value() == 30
        assert modulo.retencion_gb.value() == 250.0
        assert "Configurada el" in modulo.retencion_estado.text()


# --- Los avisos en la ventana ------------------------------------------------


def _borrados_pendientes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture()
def avisos(monkeypatch):
    """Los avisos que se mostraron: (titulo, texto)."""
    mostrados: list[tuple[str, str]] = []

    def warn_falso(_parent, titulo, texto, _accion, _callback):
        mostrados.append((titulo, texto))

    monkeypatch.setattr(mw, "warn_con_accion", warn_falso)
    return mostrados


@pytest.fixture()
def abrir_ventana(qapp, temp_db):
    abiertas = []

    def abrir(rol: str = "admin"):
        _usuario(rol)
        ventana = mw.MainWindow()
        abiertas.append(ventana)
        return ventana

    yield abrir
    for ventana in abiertas:
        if isValid(ventana):
            ventana.close()
    _borrados_pendientes()


class TestAvisoAlAbrir:
    def test_sin_configurar_avisa_al_admin(self, qtbot, abrir_ventana, avisos):
        abrir_ventana("admin")

        qtbot.waitUntil(lambda: len(avisos) == 1)
        assert avisos[0][0] == "Retención sin configurar"

    def test_configurada_no_avisa(self, qtbot, abrir_ventana, avisos):
        app_prefs.confirmar_retencion(7, 5)
        abrir_ventana("admin")

        qtbot.wait(50)
        assert avisos == []

    @pytest.mark.parametrize("rol", ["supervisor", "operador", "auditor"])
    def test_a_los_demas_roles_no(self, qtbot, abrir_ventana, avisos, rol):
        ventana = abrir_ventana(rol)

        qtbot.wait(50)
        ventana.avisar_retencion(RetentionStatus(RETENCION_AL_TOPE, 50, 1024**2))
        assert avisos == []

    def test_cerrar_antes_del_aviso_no_rompe(self, qtbot, abrir_ventana, avisos):
        """El aviso sale con un singleShot: si la ventana se borra antes
        (logout inmediato), el timer no puede llamar sobre un objeto muerto.
        Lo encontraron test_layout_store.py, que cierra enseguida."""
        ventana = abrir_ventana("admin")
        ventana.close()
        _borrados_pendientes()

        qtbot.wait(50)
        assert avisos == []


class TestAvisosDelWorker:
    def test_uno_por_tipo_y_por_sesion(self, qtbot, abrir_ventana, avisos):
        app_prefs.confirmar_retencion(7, 5)
        ventana = abrir_ventana("admin")

        for _ in range(3):
            ventana.avisar_retencion(RetentionStatus(RETENCION_AL_TOPE, 50, 3 * 1024**2))
        ventana.avisar_retencion(RetentionStatus(RETENCION_EVIDENCIA_PROTEGIDA))
        ventana.avisar_retencion(RetentionStatus(RETENCION_PREFS_ILEGIBLES))

        assert [titulo for titulo, _ in avisos] == [
            "Retención: borrado grande",
            "Media sobre el tope",
            "Retención suspendida",
        ]
        assert "50 archivos (3.0 MB)" in avisos[0][1]

    def test_la_señal_del_bus_llega_a_la_ventana(self, qtbot, abrir_ventana, avisos):
        app_prefs.confirmar_retencion(7, 5)
        abrir_ventana("admin")

        event_bus.retention_status.emit(RetentionStatus(RETENCION_EVIDENCIA_PROTEGIDA))

        qtbot.waitUntil(lambda: len(avisos) == 1)
        assert avisos[0][0] == "Media sobre el tope"

    def test_configurar_lleva_a_grabando(self, abrir_ventana):
        ventana = abrir_ventana("admin")

        ventana.abrir_retencion()

        modulo = ventana.tabs.currentWidget()
        assert isinstance(modulo, SystemModule)
        assert modulo.nav_tree.currentItem().text(0) == "Grabando"


class TestElWorkerEmite:
    def test_sin_configurar_emite_una_vez(self, qtbot):
        worker = retention.RetentionWorker()

        with qtbot.waitSignal(event_bus.retention_status, timeout=1000) as señal:
            worker.pasada()
        assert señal.args[0].tipo == RETENCION_SIN_CONFIGURAR

        with qtbot.assertNotEmitted(event_bus.retention_status):
            worker.pasada()

    def test_al_tope_emite_con_los_numeros(self, qtbot, temp_db, monkeypatch):
        app_prefs.confirmar_retencion(7, 5)
        monkeypatch.setattr(
            retention,
            "prune",
            lambda **_k: {"deleted": 50, "freed_bytes": 2048, "al_tope": True, "protegida": False},
        )

        with qtbot.waitSignal(event_bus.retention_status, timeout=1000) as señal:
            retention.RetentionWorker().pasada()

        assert señal.args[0] == RetentionStatus(RETENCION_AL_TOPE, 50, 2048)

    def test_prefs_ilegibles_emite(self, qtbot):
        app_prefs._PREFS_PATH.write_text("{roto", encoding="utf-8")

        with qtbot.waitSignal(event_bus.retention_status, timeout=1000) as señal:
            retention.RetentionWorker().pasada()

        assert señal.args[0].tipo == RETENCION_PREFS_ILEGIBLES


class TestAvisoConAccion:
    def test_el_boton_hace_la_accion_y_cierra(self, qtbot):
        from PySide6.QtWidgets import QWidget
        from qfluentwidgets import PushButton

        from aurea_vms.ui.notify import warn_con_accion

        ventana = QWidget()
        qtbot.addWidget(ventana)
        ventana.resize(800, 600)
        ventana.show()
        llamadas: list[bool] = []

        barra = warn_con_accion(ventana, "T", "texto", "Configurar", lambda: llamadas.append(True))
        boton = next(b for b in barra.findChildren(PushButton) if b.text() == "Configurar")
        boton.click()

        assert llamadas == [True]
        qtbot.waitUntil(lambda: not isValid(barra) or not barra.isVisible())
