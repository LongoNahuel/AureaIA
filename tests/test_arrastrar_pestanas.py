"""Arrastrar una pestaña afuera de la barra (Fase V5, 2026-10-08).

Dos partes que se prueban por separado:
- el gesto (PestanasMovibles): eventos de mouse armados a mano sobre la
  pestaña, como los manda Qt mientras el boton tiene el mouse agarrado;
- adonde va (MainWindow.soltar_pestana): a otra ventana de la app si se
  suelta encima, o a una ventana nueva bajo el cursor.

Decisiones de Daniel: soltar sobre la propia ventana tambien desacopla (con
la principal maximizada no hay "afuera"), y la unica pestaña de una
secundaria se lleva la ventana en vez de abrir otra.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from aurea_vms.core import auth
from aurea_vms.models.user import User
from aurea_vms.ui import main_window as mw
from aurea_vms.ui import pestanas
from aurea_vms.ui.modules.alarm_module import AlarmModule
from aurea_vms.ui.modules.system_module import SystemModule
from aurea_vms.ui.ventana_secundaria import VentanaSecundaria

# Los eventos de mouse armados a mano pasan por el codigo de qfluentwidgets,
# que usa APIs de Qt deprecadas (QMouseEvent.pos(), QHoverEvent): no es
# nuestro y ensuciaba la salida con ~55 avisos.
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning:qfluentwidgets")


def _borrados_pendientes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture()
def principal(qtbot, temp_db):
    auth.current_user = User(username="admin", password_hash="h", salt="s", role="admin")
    window = mw.MainWindow()
    window.resize(1000, 700)
    window.show()
    qtbot.waitExposed(window)
    yield window
    QApplication.restoreOverrideCursor()  # por si un test corto a mitad de arrastre
    if isValid(window):
        window.close()
    _borrados_pendientes()


def _abrir(principal, module_cls):
    return principal.open_module_by_index(mw.module_index(module_cls))


def _item(ventana, widget):
    return ventana.tabs.tabBar.tabItem(ventana.tabs.indice_de(widget))


def _mouse(item, tipo, pos_global: QPoint, boton, botones) -> None:
    local = item.mapFromGlobal(pos_global)
    evento = QMouseEvent(
        tipo, QPointF(local), QPointF(pos_global), boton, botones, Qt.KeyboardModifier.NoModifier
    )
    QApplication.sendEvent(item, evento)


def _arrastrar(item, hasta: QPoint) -> None:
    """Presionar en el centro de la pestaña, moverse en dos pasos y soltar."""
    izq = Qt.MouseButton.LeftButton
    nada = Qt.MouseButton.NoButton
    inicio = item.mapToGlobal(item.rect().center())
    _mouse(item, QEvent.Type.MouseButtonPress, inicio, izq, izq)
    _mouse(item, QEvent.Type.MouseMove, (inicio + hasta) / 2, nada, izq)
    _mouse(item, QEvent.Type.MouseMove, hasta, nada, izq)
    _mouse(item, QEvent.Type.MouseButtonRelease, hasta, izq, nada)


def _debajo_de_la_barra(ventana, px: int) -> QPoint:
    barra = ventana.tabs.tabBar
    return barra.mapToGlobal(QPoint(barra.width() // 2, barra.height() + px))


@pytest.fixture()
def soltados(principal):
    """Lo que emite la barra de la principal al soltar afuera."""
    emitidos: list[tuple[object, QPoint]] = []
    principal.tabs.arrastre_soltado.connect(lambda w, pos: emitidos.append((w, pos)))
    return emitidos


@pytest.fixture()
def sin_soltar(monkeypatch):
    """El gesto solo: soltar_pestana no hace nada (se prueba aparte)."""
    monkeypatch.setattr(mw.MainWindow, "soltar_pestana", lambda *_a: None)


class TestElGesto:
    def test_soltar_lejos_de_la_barra_emite_la_pagina_y_el_punto(
        self, principal, soltados, sin_soltar
    ):
        sistema = _abrir(principal, SystemModule)
        destino = _debajo_de_la_barra(principal, 200)

        _arrastrar(_item(principal, sistema), destino)

        assert soltados == [(sistema, destino)]

    def test_dentro_de_la_barra_no_desacopla(self, principal, soltados, sin_soltar):
        sistema = _abrir(principal, SystemModule)
        item = _item(principal, sistema)

        _arrastrar(item, item.mapToGlobal(item.rect().center()) + QPoint(60, 0))

        assert soltados == []

    def test_apenas_debajo_de_la_barra_tampoco(self, principal, soltados, sin_soltar):
        """Un clic que se corre unos pixeles no saca la pestaña."""
        sistema = _abrir(principal, SystemModule)

        _arrastrar(
            _item(principal, sistema),
            _debajo_de_la_barra(principal, pestanas.MARGEN_SALIDA_PX - 10),
        )

        assert soltados == []

    def test_inicio_no_se_arrastra(self, principal, soltados, sin_soltar):
        _arrastrar(_item(principal, principal.launcher), _debajo_de_la_barra(principal, 200))

        assert soltados == []

    def test_afuera_cambia_el_cursor_y_al_soltar_vuelve(self, principal, sin_soltar):
        sistema = _abrir(principal, SystemModule)
        item = _item(principal, sistema)
        izq = Qt.MouseButton.LeftButton
        inicio = item.mapToGlobal(item.rect().center())
        afuera = _debajo_de_la_barra(principal, 200)

        _mouse(item, QEvent.Type.MouseButtonPress, inicio, izq, izq)
        _mouse(item, QEvent.Type.MouseMove, afuera, Qt.MouseButton.NoButton, izq)
        assert QApplication.overrideCursor() is not None

        _mouse(item, QEvent.Type.MouseButtonRelease, afuera, izq, Qt.MouseButton.NoButton)
        assert QApplication.overrideCursor() is None

    def test_clic_derecho_sigue_pidiendo_el_menu(self, principal):
        """El menu ya no va por una lambda conectada a cada pestaña sino
        por el mismo filtro de eventos."""
        sistema = _abrir(principal, SystemModule)
        item = _item(principal, sistema)
        pedidos: list[object] = []
        principal.tabs.menu_pedido.connect(lambda w, _pos: pedidos.append(w))

        centro = item.rect().center()
        QApplication.sendEvent(
            item,
            QContextMenuEvent(QContextMenuEvent.Reason.Mouse, centro, item.mapToGlobal(centro)),
        )

        assert pedidos == [sistema]


def _soltar(principal, widget, pos: QPoint) -> None:
    principal.soltar_pestana(widget, pos)
    QCoreApplication.processEvents()


@pytest.fixture()
def debajo(monkeypatch):
    """Que widget hay bajo el cursor al soltar (en offscreen no hay un
    escritorio real que consultar)."""
    estado = {"widget": None}
    monkeypatch.setattr(mw.QApplication, "widgetAt", lambda _pos: estado["widget"])
    return estado


class TestAdondeVa:
    def test_afuera_de_toda_ventana_abre_una_nueva_bajo_el_cursor(self, principal, debajo):
        sistema = _abrir(principal, SystemModule)
        pos = QPoint(700, 500)

        _soltar(principal, sistema, pos)

        nueva = principal.ventanas.ventana_de(sistema)
        assert isinstance(nueva, VentanaSecundaria)
        assert nueva.pos() == pos - QPoint(nueva.width() // 2, pestanas.MARGEN_SALIDA_PX)

    def test_sobre_su_misma_ventana_tambien_desacopla(self, principal, debajo):
        sistema = _abrir(principal, SystemModule)
        debajo["widget"] = principal.tabs

        _soltar(principal, sistema, QPoint(400, 400))

        assert isinstance(principal.ventanas.ventana_de(sistema), VentanaSecundaria)

    def test_sobre_otra_ventana_va_ahi(self, principal, debajo):
        alarmas = _abrir(principal, AlarmModule)
        otra = pestanas.desacoplar(alarmas, principal, principal.nueva_ventana)
        sistema = _abrir(principal, SystemModule)
        debajo["widget"] = otra.tabs

        _soltar(principal, sistema, QPoint(400, 400))

        assert principal.ventanas.ventana_de(sistema) is otra
        assert principal.ventanas.secundarias() == [otra]  # no abrio otra

    def test_la_unica_pestaña_de_una_secundaria_se_lleva_la_ventana(self, principal, debajo):
        sistema = _abrir(principal, SystemModule)
        otra = pestanas.desacoplar(sistema, principal, principal.nueva_ventana)
        pos = QPoint(650, 420)

        _soltar(principal, sistema, pos)

        assert principal.ventanas.ventana_de(sistema) is otra
        assert principal.ventanas.secundarias() == [otra]
        assert otra.pos() == pos - QPoint(otra.width() // 2, pestanas.MARGEN_SALIDA_PX)

    def test_desde_una_secundaria_a_la_principal(self, principal, debajo):
        sistema = _abrir(principal, SystemModule)
        alarmas = _abrir(principal, AlarmModule)
        otra = pestanas.desacoplar(sistema, principal, principal.nueva_ventana)
        pestanas.llevar_a(alarmas, principal, otra)
        debajo["widget"] = principal.tabs

        _soltar(principal, sistema, QPoint(300, 300))

        assert principal.ventanas.ventana_de(sistema) is principal
        assert principal.ventanas.ventana_de(alarmas) is otra

    def test_es_diferido(self, principal, debajo):
        """El boton de la pestaña es el que esta entregando el soltar: si se
        la quita en el momento, Qt sigue usando un widget borrado."""
        sistema = _abrir(principal, SystemModule)

        principal.soltar_pestana(sistema, QPoint(700, 500))
        assert principal.ventanas.ventana_de(sistema) is principal

        QCoreApplication.processEvents()
        assert isinstance(principal.ventanas.ventana_de(sistema), VentanaSecundaria)

    def test_si_la_pagina_se_cerro_antes_no_rompe(self, principal, debajo):
        sistema = _abrir(principal, SystemModule)
        principal.soltar_pestana(sistema, QPoint(700, 500))
        principal.cerrar_pestana(principal, principal.tabs.indice_de(sistema))
        _borrados_pendientes()

        QCoreApplication.processEvents()

        assert principal.ventanas.secundarias() == []


class TestDePuntaAPunta:
    def test_arrastrar_afuera_abre_la_ventana_con_la_pagina(self, principal, debajo):
        sistema = _abrir(principal, SystemModule)

        _arrastrar(_item(principal, sistema), _debajo_de_la_barra(principal, 250))
        QCoreApplication.processEvents()

        nueva = principal.ventanas.ventana_de(sistema)
        assert isinstance(nueva, VentanaSecundaria)
        assert nueva.tabs.currentWidget() is sistema
        assert principal.tabs.count() == 1  # quedo Inicio

    def test_desde_una_secundaria_sobre_la_principal(self, qtbot, principal, debajo):
        """La barra de una secundaria tambien avisa a la principal."""
        sistema = _abrir(principal, SystemModule)
        alarmas = _abrir(principal, AlarmModule)
        otra = pestanas.desacoplar(sistema, principal, principal.nueva_ventana)
        pestanas.llevar_a(alarmas, principal, otra)
        qtbot.waitExposed(otra)
        debajo["widget"] = principal.tabs

        _arrastrar(_item(otra, sistema), _debajo_de_la_barra(otra, 250))
        QCoreApplication.processEvents()

        assert principal.ventanas.ventana_de(sistema) is principal
        assert principal.ventanas.ventana_de(alarmas) is otra
