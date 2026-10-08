"""Pestañas en otras ventanas (Fase V3, 2026-10-07): desacoplar por menu,
mover sin recrear, reacoplar al cerrar, varias Vistas en Vivo, el "+", y las
alarmas en la ventana que se esta mirando."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QDialog
from shiboken6 import isValid

import aurea_vms.ui.widgets.video_tile as vt_module
from aurea_vms.core import auth
from aurea_vms.core.events import AlarmEvent
from aurea_vms.models.user import User
from aurea_vms.ui import main_window as mw
from aurea_vms.ui import pestanas
from aurea_vms.ui.modules.live_view import LiveViewModule
from aurea_vms.ui.modules.system_module import SystemModule
from aurea_vms.ui.ventana_secundaria import VentanaSecundaria


def _borrados_pendientes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture()
def principal(qapp, temp_db):
    auth.current_user = User(username="admin", password_hash="h", salt="s", role="admin")
    window = mw.MainWindow()
    yield window
    if isValid(window):
        window.close()
    _borrados_pendientes()


def _abrir(principal, module_cls):
    return principal.open_module_by_index(mw.module_index(module_cls))


def _accion(menu, texto: str):
    for accion in menu.actions():
        if accion.text() == texto:
            return accion
    raise AssertionError(f"no hay '{texto}' en {[a.text() for a in menu.actions()]}")


def _menu(principal, ventana, widget):
    return pestanas.armar_menu(
        principal.ventanas, ventana, widget, principal.nueva_ventana, ventana
    )


def _desacoplar(principal, widget) -> VentanaSecundaria:
    origen = principal.ventanas.ventana_de(widget)
    _accion(_menu(principal, origen, widget), "Abrir en ventana nueva").trigger()
    return principal.ventanas.ventana_de(widget)


class TestDesacoplar:
    def test_abrir_en_ventana_nueva_mueve_la_misma_pagina(self, principal):
        sistema = _abrir(principal, SystemModule)

        nueva = _desacoplar(principal, sistema)

        assert isinstance(nueva, VentanaSecundaria)
        assert nueva.nombre == "Ventana 2"
        assert nueva.tabs.currentWidget() is sistema  # no se recreo
        assert principal.tabs.count() == 1  # quedo Inicio
        assert principal.ventanas.secundarias() == [nueva]
        assert nueva.isVisible()

    def test_inicio_no_tiene_menu(self, principal):
        assert _menu(principal, principal, principal.launcher) is None

    def test_el_menu_ofrece_las_otras_ventanas(self, principal):
        sistema = _abrir(principal, SystemModule)
        vivo = _abrir(principal, LiveViewModule)
        segunda = _desacoplar(principal, sistema)

        en_principal = [a.text() for a in _menu(principal, principal, vivo).actions()]
        en_segunda = [a.text() for a in _menu(principal, segunda, sistema).actions()]

        assert en_principal == ["Abrir en ventana nueva", "Mover a Ventana 2"]
        assert en_segunda == ["Abrir en ventana nueva", "Volver a la principal"]

    def test_mover_no_suelta_los_streams_ni_cierra_el_modulo(self, principal, monkeypatch):
        """Lo que hace posible llevar video a otro monitor: la grilla viaja
        entera, con sus camaras conectadas."""
        tomados: list[int] = []
        soltados: list[int] = []
        monkeypatch.setattr(
            vt_module.stream_manager, "acquire", lambda d, _k="main": tomados.append(d.id)
        )
        monkeypatch.setattr(
            vt_module.stream_manager, "release", lambda i, _k="main": soltados.append(i)
        )
        monkeypatch.setattr(
            vt_module.repository, "get_device", lambda i: SimpleNamespace(id=i, name=f"Cam {i}")
        )
        monkeypatch.setattr(vt_module.repository, "list_analytics_configs", lambda _i: [])
        cerrados: list = []
        monkeypatch.setattr(LiveViewModule, "on_window_closed", lambda s: cerrados.append(s))
        vivo = _abrir(principal, LiveViewModule)
        vivo.tiles[0].assign_device(9)

        _desacoplar(principal, vivo)

        assert tomados == [9]
        assert soltados == []
        assert cerrados == []
        assert vivo.tiles[0].device_id == 9


class TestCerrarSecundaria:
    def test_la_x_devuelve_las_pestañas_a_la_principal(self, principal, monkeypatch):
        cerrados: list = []
        monkeypatch.setattr(LiveViewModule, "on_window_closed", lambda s: cerrados.append(s))
        vivo = _abrir(principal, LiveViewModule)
        sistema = _abrir(principal, SystemModule)
        segunda = _desacoplar(principal, vivo)
        pestanas.mover(sistema, principal, segunda)

        segunda.close()
        _borrados_pendientes()

        assert principal.ventanas.secundarias() == []
        assert not isValid(segunda)
        devueltos = {principal.tabs.widget(i) for i in range(principal.tabs.count())}
        assert {vivo, sistema} <= devueltos
        assert cerrados == []  # volvieron vivos, no se cerraron

    def test_una_secundaria_vacia_se_cierra(self, principal):
        sistema = _abrir(principal, SystemModule)
        segunda = _desacoplar(principal, sistema)

        _accion(_menu(principal, segunda, sistema), "Volver a la principal").trigger()
        _borrados_pendientes()

        assert principal.ventanas.secundarias() == []
        assert not isValid(segunda)
        assert principal.tabs.currentWidget() is sistema

    def test_cerrar_una_pestaña_de_la_secundaria_la_suelta(self, principal, monkeypatch):
        cerrados: list = []
        monkeypatch.setattr(LiveViewModule, "on_window_closed", lambda s: cerrados.append(s))
        vivo = _abrir(principal, LiveViewModule)
        segunda = _desacoplar(principal, vivo)

        principal.cerrar_pestana(segunda, 0)
        _borrados_pendientes()

        assert cerrados == [vivo]
        assert not isValid(segunda)  # se quedo vacia


class TestCerrarLaPrincipal:
    def test_suelta_los_modulos_de_las_secundarias_una_vez_y_no_los_devuelve(
        self, principal, monkeypatch
    ):
        cerrados: list = []
        monkeypatch.setattr(LiveViewModule, "on_window_closed", lambda s: cerrados.append(s))
        vivo = _abrir(principal, LiveViewModule)
        segunda = _desacoplar(principal, vivo)
        movidos: list = []
        original = pestanas.mover
        monkeypatch.setattr(pestanas, "mover", lambda *a: movidos.append(a) or original(*a))

        principal.close()
        _borrados_pendientes()

        assert cerrados == [vivo]
        assert movidos == []  # la sesion se va: no se reacopla nada
        assert not isValid(segunda)
        assert not isValid(principal)


class TestVariasInstancias:
    def test_el_mas_abre_otra_vista_en_vivo(self, principal):
        primera = principal.nueva_vista_en_vivo(principal)
        principal.tabs.tabAddRequested.emit()
        segunda = principal.tabs.currentWidget()

        assert isinstance(segunda, LiveViewModule) and segunda is not primera
        assert primera.property("routeKey") == "module-LiveViewModule"
        assert segunda.property("routeKey") == "module-LiveViewModule-2"
        indice = principal.tabs.indice_de(segunda)
        assert principal.tabs.tabText(indice) == "Vista en Vivo 2"

    def test_el_mas_de_una_secundaria_abre_ahi(self, principal):
        sistema = _abrir(principal, SystemModule)
        segunda = _desacoplar(principal, sistema)

        segunda.tabs.tabAddRequested.emit()

        assert isinstance(segunda.tabs.currentWidget(), LiveViewModule)
        assert principal.tabs.count() == 1

    def test_los_modulos_de_configuracion_siguen_siendo_uno(self, principal):
        sistema = _abrir(principal, SystemModule)

        otra = principal.nueva_instancia(mw.module_index(SystemModule), principal)

        assert otra is sistema

    def test_el_launcher_enfoca_una_vista_en_vivo_aunque_sea_la_segunda(self, principal):
        primera = principal.nueva_vista_en_vivo(principal)
        segunda = principal.nueva_vista_en_vivo(principal)
        principal.cerrar_pestana(principal, principal.tabs.indice_de(primera))

        assert _abrir(principal, LiveViewModule) is segunda

    def test_el_mas_no_aparece_sin_permiso_de_video(self, qapp, temp_db):
        auth.current_user = User(username="aud", password_hash="h", salt="s", role="auditor")
        window = mw.MainWindow()
        try:
            assert not window.tabs.tabBar.addButton.isVisibleTo(window)
        finally:
            window.close()
            _borrados_pendientes()


class TestAlarmas:
    def test_el_popup_sale_en_la_ventana_activa_y_suena_una_vez(self, principal, monkeypatch):
        sistema = _abrir(principal, SystemModule)
        segunda = _desacoplar(principal, sistema)
        monkeypatch.setattr(principal.ventanas, "ventana_activa", lambda: segunda)
        en_principal: list = []
        en_segunda: list = []
        monkeypatch.setattr(principal.alert_layer, "show_alarm", lambda *a: en_principal.append(a))
        monkeypatch.setattr(segunda.alert_layer, "show_alarm", lambda *a: en_segunda.append(a))
        sonidos: list = []
        monkeypatch.setattr(mw.sound, "play_alarm", lambda: sonidos.append(1))

        principal._on_global_alarm(
            AlarmEvent(
                alarm_event_id=1,
                rule_id=1,
                device_id=1,
                timestamp=0.0,
                object_class="person",
                confidence=0.9,
                severity="critico",
                play_sound=True,
            )
        )

        assert len(en_segunda) == 1 and en_principal == []
        assert sonidos == [1]


class TestPaleta:
    def test_ctrl_k_en_una_secundaria_abre_ahi(self, principal, monkeypatch):
        sistema = _abrir(principal, SystemModule)
        segunda = _desacoplar(principal, sistema)

        class _Paleta:
            def __init__(self, _modules, parent) -> None:
                self.parent = parent
                self.action = (mw.ACTION_OPEN_MODULE, mw.module_index(LiveViewModule))

            def exec(self):
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(mw, "CommandPaletteDialog", _Paleta)

        principal.abrir_paleta(segunda)

        assert isinstance(segunda.tabs.currentWidget(), LiveViewModule)


class TestPantallaCompleta:
    def test_la_vista_en_vivo_desacoplada_agranda_su_ventana(self, principal):
        """live_view usa self.window(): en una secundaria, la pantalla
        completa es la del monitor donde esta, no la de la principal."""
        vivo = _abrir(principal, LiveViewModule)
        segunda = _desacoplar(principal, vivo)
        principal.show()

        vivo._enter_fullscreen()
        assert segunda.isFullScreen()
        assert not principal.isFullScreen()

        vivo._exit_fullscreen()
        assert not segunda.isFullScreen()


class TestClicEnElPopupConVariasVentanas:
    """Merge de zoom-digital (2026-10-08): el clic en un popup abre Alarmas
    con el incidente. La version de la rama leia `self.tabs.currentWidget()`
    de la principal, y con Alarmas en otra ventana enfocaba otro modulo; y
    solo la capa de popups de la principal estaba conectada."""

    @pytest.fixture()
    def enfocados(self, monkeypatch):
        from aurea_vms.ui.modules.alarm_module import AlarmModule

        llamados: list[tuple[object, int]] = []

        def focus_event(modulo, alarm_event_id):
            llamados.append((modulo, alarm_event_id))
            return True

        monkeypatch.setattr(AlarmModule, "focus_event", focus_event)
        return llamados

    def test_alarmas_en_otra_ventana_recibe_el_incidente(self, principal, enfocados):
        from aurea_vms.ui.modules.alarm_module import AlarmModule

        alarmas = _abrir(principal, AlarmModule)
        _desacoplar(principal, alarmas)
        _abrir(principal, SystemModule)  # la pestaña actual de la principal

        principal.alert_layer.open_alarm_requested.emit(42)

        assert enfocados == [(alarmas, 42)]

    def test_el_popup_de_una_secundaria_abre_alarmas_ahi(self, principal, enfocados):
        from aurea_vms.ui.modules.alarm_module import AlarmModule

        otra = _desacoplar(principal, _abrir(principal, SystemModule))

        otra.alert_layer.open_alarm_requested.emit(7)

        ventana, alarmas = principal.ventanas.buscar_modulo(AlarmModule)
        assert isinstance(alarmas, AlarmModule)
        assert ventana is otra
        assert enfocados == [(alarmas, 7)]

    def test_ajustes_avanzados_con_analizadores_en_otra_ventana(self, principal, monkeypatch):
        from aurea_vms.ui.modules.analytics_config import AnalyticsConfigModule

        enfocados: list[tuple[object, int]] = []
        monkeypatch.setattr(
            AnalyticsConfigModule,
            "focus_device",
            lambda modulo, device_id: enfocados.append((modulo, device_id)),
        )
        analizadores = _abrir(principal, AnalyticsConfigModule)
        _desacoplar(principal, analizadores)
        _abrir(principal, SystemModule)

        principal._on_open_analytics_config_requested(8)

        assert enfocados == [(analizadores, 8)]
