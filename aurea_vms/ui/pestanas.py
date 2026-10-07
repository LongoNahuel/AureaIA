"""Pestañas que se pueden llevar a otra ventana (Fase V3, 2026-10-07).

`PestanasMovibles` es el TabWidget de qfluentwidgets con un menu contextual
por pestaña. Las operaciones (`mover`, `desacoplar`) mueven la MISMA pagina
de una ventana a otra: removeTab no la borra y addTab la reparenta, asi que
el modulo no se recrea y sus streams no se cortan (los recuadros solo paran
de pintar mientras quedan ocultos, ver video_tile.hideEvent).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QWidget
from qfluentwidgets import Action, FluentIcon, RoundMenu, TabCloseButtonDisplayMode, TabWidget

if TYPE_CHECKING:
    from aurea_vms.ui.window_manager import VentanaConPestanas, WindowManager

# La pestaña fija de la principal: no se cierra ni se lleva a otra ventana.
HOME_ROUTE_KEY = "home"


class PestanasMovibles(TabWidget):
    """Emite `menu_pedido(pagina, posicion_global)` con clic derecho sobre
    una pestaña; la ventana arma el menu (ella conoce al WindowManager)."""

    menu_pedido = Signal(object, QPoint)
    # Entro, salio o se eligio otra pestaña: la disposicion guardada cambio.
    cambio = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(self.cambio)
        self.setMovable(False)
        self.setTabShadowEnabled(True)
        self.setCloseButtonDisplayMode(TabCloseButtonDisplayMode.ON_HOVER)
        self.setTabsClosable(True)

    def insertTab(self, index, widget, label, icon=None, routeKey=None) -> int:  # noqa: N802, N803
        nuevo = super().insertTab(index, widget, label, icon, routeKey)
        if nuevo >= 0:
            item = self.tabBar.tabItem(nuevo)
            item.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            item.customContextMenuRequested.connect(
                lambda pos, it=item, pagina=widget: self.menu_pedido.emit(
                    pagina, it.mapToGlobal(pos)
                )
            )
            self.cambio.emit()
        return nuevo

    def removeTab(self, index: int) -> None:  # noqa: N802 - override de qfluentwidgets
        super().removeTab(index)
        self.cambio.emit()

    def indice_de(self, widget: QWidget) -> int:
        return self.stackedWidget.indexOf(widget)


def titulo_de(ventana: VentanaConPestanas, widget: QWidget) -> tuple[str, object]:
    tabs = ventana.tabs
    indice = tabs.indice_de(widget)
    return tabs.tabText(indice), tabs.tabIcon(indice)


def mover(widget: QWidget, origen: VentanaConPestanas, destino: VentanaConPestanas) -> None:
    """La misma pagina pasa de `origen` a `destino` y queda enfocada."""
    if origen is destino:
        return
    texto, icono = titulo_de(origen, widget)
    route_key = widget.property("routeKey")
    origen.tabs.removeTab(origen.tabs.indice_de(widget))
    destino.tabs.addTab(widget, texto, icono, routeKey=route_key)
    destino.tabs.setCurrentWidget(widget)
    if hasattr(origen, "pestana_salio"):
        origen.pestana_salio()


def armar_menu(
    manager: WindowManager,
    origen: VentanaConPestanas,
    widget: QWidget,
    nueva_ventana,
    parent: QWidget,
) -> RoundMenu | None:
    """Menu de una pestaña: abrirla en una ventana nueva, llevarla a otra de
    las abiertas, o devolverla a la principal. `nueva_ventana()` crea y
    registra una secundaria vacia."""
    if widget.property("routeKey") == HOME_ROUTE_KEY:
        return None
    menu = RoundMenu(parent=parent)
    abrir = Action(FluentIcon.LINK, "Abrir en ventana nueva")
    abrir.triggered.connect(lambda: _a_nueva(widget, origen, nueva_ventana))
    menu.addAction(abrir)
    for ventana in manager.ventanas():
        if ventana is origen:
            continue
        nombre = (
            "Volver a la principal"
            if ventana is manager.principal
            else f"Mover a {getattr(ventana, 'nombre', ventana.windowTitle())}"
        )
        accion = Action(FluentIcon.RIGHT_ARROW, nombre)
        accion.triggered.connect(
            lambda _c=False, destino=ventana: _a_existente(widget, origen, destino)
        )
        menu.addAction(accion)
    return menu


def _a_nueva(widget: QWidget, origen: VentanaConPestanas, nueva_ventana) -> None:
    destino = nueva_ventana()
    mover(widget, origen, destino)
    destino.show()
    destino.raise_()
    destino.activateWindow()


def _a_existente(widget: QWidget, origen: VentanaConPestanas, destino: VentanaConPestanas) -> None:
    mover(widget, origen, destino)
    destino.raise_()
    destino.activateWindow()
