"""MainWindow (Fase V2, 2026-10-07): el registro de ventanas y modulos, los
atajos, y el cierre ordenado. Es el primer test que arma la ventana entera."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QWidget
from qfluentwidgets import TabWidget
from shiboken6 import isValid

from aurea_vms.core import auth
from aurea_vms.models.user import User
from aurea_vms.ui import main_window as mw
from aurea_vms.ui.modules.analytics_config import AnalyticsConfigModule
from aurea_vms.ui.modules.live_view import LiveViewModule
from aurea_vms.ui.modules.system_module import SystemModule


def _borrados_pendientes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture()
def ventana(qapp, temp_db):
    """Sin qtbot.addWidget: la ventana se borra al cerrarse (WA_DeleteOnClose)
    y el fixture se asegura de que se vaya ANTES de soltar la base. Si queda
    viva, sus timers siguen consultando la base de los tests que vienen
    (paso: 85 fallas y 202 errores en el resto de la suite)."""
    auth.current_user = User(username="admin", password_hash="h", salt="s", role="admin")
    window = mw.MainWindow()
    yield window
    if isValid(window):
        window.close()
    _borrados_pendientes()


def _indice(module_cls) -> int:
    return mw.module_index(module_cls)


def test_arma_la_ventana(ventana):
    assert ventana.tabs.count() == 1  # Inicio
    assert ventana.ventanas.ventanas() == [ventana]


class _Secundaria(QWidget):
    """Doble de una ventana secundaria (las reales llegan en V3): cualquier
    QWidget con `tabs` entra al registro."""

    def __init__(self) -> None:
        super().__init__()
        self.tabs = TabWidget(self)
        self.cerrada = False

    def closeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.cerrada = True
        super().closeEvent(event)


@pytest.fixture()
def secundaria(qtbot, ventana):
    otra = _Secundaria()
    qtbot.addWidget(otra)
    ventana.ventanas.registrar(otra)
    return otra


def _mover(ventana, widget, destino) -> None:
    """Lo que hara el menu de V3: la misma pagina pasa a otra ventana."""
    ventana.tabs.removeTab(ventana.tabs.stackedWidget.indexOf(widget))
    destino.tabs.addTab(widget, "x", routeKey=widget.property("routeKey"))


class TestAbrirModulos:
    def test_abrir_dos_veces_enfoca_el_existente(self, ventana):
        primero = ventana.open_module_by_index(_indice(SystemModule))
        ventana.tabs.setCurrentIndex(0)
        segundo = ventana.open_module_by_index(_indice(SystemModule))

        assert segundo is primero
        assert ventana.tabs.count() == 2
        assert ventana.tabs.currentWidget() is primero

    def test_la_clave_es_la_clase_no_la_posicion(self, ventana):
        widget = ventana.open_module_by_index(_indice(SystemModule))
        assert widget.property("routeKey") == "module-SystemModule"

    def test_sin_permiso_no_abre_y_devuelve_none(self, ventana, monkeypatch):
        monkeypatch.setattr(mw, "warn", lambda *a, **k: None)
        auth.current_user = User(username="op", password_hash="h", salt="s", role="operador")

        assert ventana.open_module_by_index(_indice(SystemModule)) is None
        assert ventana.tabs.count() == 1

    def test_encuentra_el_modulo_aunque_viva_en_otra_ventana(self, ventana, secundaria):
        sistema = ventana.open_module_by_index(_indice(SystemModule))
        _mover(ventana, sistema, secundaria)
        secundaria.tabs.addTab(QWidget(), "otra")
        secundaria.tabs.setCurrentIndex(1)

        otra_vez = ventana.open_module_by_index(_indice(SystemModule))

        assert otra_vez is sistema  # no se crea uno nuevo en la principal
        assert ventana.tabs.count() == 1
        assert secundaria.tabs.currentWidget() is sistema

    def test_module_index_de_algo_que_no_es_modulo(self):
        with pytest.raises(ValueError):
            mw.module_index(QWidget)


class TestAtajos:
    def test_ajustes_avanzados_abre_analizadores(self, ventana, monkeypatch):
        """Abria el indice 2 (Dispositivos) y focus_device no existia ahi:
        el atajo no hacia nada."""
        enfocados: list[int] = []
        monkeypatch.setattr(
            AnalyticsConfigModule, "focus_device", lambda _s, d: enfocados.append(d)
        )

        ventana._on_open_analytics_config_requested(7)

        assert isinstance(ventana.tabs.currentWidget(), AnalyticsConfigModule)
        assert enfocados == [7]

    def test_vista_rapida_abre_vista_en_vivo(self, ventana, monkeypatch):
        enfocadas: list[int] = []
        monkeypatch.setattr(LiveViewModule, "focus_camera", lambda _s, d: enfocadas.append(d))

        ventana._on_open_live_view_requested(3)

        assert isinstance(ventana.tabs.currentWidget(), LiveViewModule)
        assert enfocadas == [3]

    def test_el_atajo_de_inicio_llega_al_modulo_en_otra_ventana(
        self, ventana, secundaria, monkeypatch
    ):
        secciones: list[tuple] = []
        monkeypatch.setattr(
            SystemModule, "focus_section", lambda _s, g, h: secciones.append((g, h))
        )
        sistema = ventana.open_module_by_index(_indice(SystemModule))
        _mover(ventana, sistema, secundaria)

        ventana._on_home_shortcut(_indice(SystemModule), "Sistema", "Registro")

        assert secciones == [("Sistema", "Registro")]


class TestFiltroDeSitio:
    def test_llega_a_las_pestañas_de_todas_las_ventanas(self, ventana, secundaria):
        class _Arbol:
            def __init__(self) -> None:
                self.sitios: list = []

            def set_site_filter(self, site_id) -> None:
                self.sitios.append(site_id)

        en_principal, en_secundaria = QWidget(), QWidget()
        en_principal.device_tree, en_secundaria.device_tree = _Arbol(), _Arbol()
        ventana.tabs.addTab(en_principal, "a")
        secundaria.tabs.addTab(en_secundaria, "b")

        ventana._on_site_filter_changed(4)

        assert en_principal.device_tree.sitios == [4]
        assert en_secundaria.device_tree.sitios == [4]


class TestCierre:
    def test_cerrar_la_principal_cierra_las_secundarias_y_suelta_los_modulos(
        self, ventana, secundaria, monkeypatch
    ):
        soltados: list = []
        monkeypatch.setattr(LiveViewModule, "on_window_closed", lambda s: soltados.append(s))
        vivo = ventana.open_module_by_index(_indice(LiveViewModule))
        inteligente = ventana.open_module_by_index(_indice(mw.IntelligentViewModule))
        _mover(ventana, inteligente, secundaria)

        ventana.close()

        assert secundaria.cerrada
        assert soltados == [vivo, inteligente]  # el de la secundaria tambien

    def test_cerrar_borra_la_ventana_y_sus_modulos(self, ventana):
        """Antes la ventana vieja sobrevivia a cada logout (un ciclo por C++
        con la lambda del combo de sitio) con sus timers y sus conexiones al
        event_bus vivos."""
        vivo = ventana.open_module_by_index(_indice(LiveViewModule))
        ventana.show()

        ventana.close()
        _borrados_pendientes()

        assert not isValid(ventana)
        assert not isValid(vivo)
        assert ventana.logout_requested is False  # main.py lo lee despues


class TestRegistro:
    def test_la_ventana_activa_es_la_principal_si_ninguna_tiene_foco(self, ventana, secundaria):
        assert ventana.ventanas.ventana_activa() is ventana

    def test_la_principal_no_se_quita(self, ventana, secundaria):
        ventana.ventanas.quitar(ventana)
        ventana.ventanas.quitar(secundaria)

        assert ventana.ventanas.ventanas() == [ventana]
