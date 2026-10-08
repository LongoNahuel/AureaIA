"""Pestañas que se pueden llevar a otra ventana (Fase V3, 2026-10-07).

`PestanasMovibles` es el TabWidget de qfluentwidgets con un menu contextual
por pestaña. Las operaciones (`mover`, `desacoplar`, `llevar_a`) mueven la
MISMA pagina de una ventana a otra: removeTab no la borra y addTab la
reparenta, asi que el modulo no se recrea y sus streams no se cortan (los
recuadros solo paran de pintar mientras quedan ocultos, ver
video_tile.hideEvent).

Arrastrar una pestaña afuera de la barra (Fase V5, 2026-10-08) emite
`arrastre_soltado(pagina, posicion_global)` al soltar; la ventana principal
decide adonde va (ver MainWindow.soltar_pestana). Se sigue el mouse a mano y
no con QDrag: asi la ventana nueva queda bajo el cursor en X11 y Windows. En
Wayland una ventana no puede ubicarse sola; el menu sigue estando.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, Signal
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import Action, FluentIcon, RoundMenu, TabCloseButtonDisplayMode, TabWidget

if TYPE_CHECKING:
    from aurea_vms.ui.window_manager import VentanaConPestanas, WindowManager

# La pestaña fija de la principal: no se cierra ni se lleva a otra ventana.
HOME_ROUTE_KEY = "home"
# Cuanto hay que alejarse de la barra (arriba o abajo) para que soltar
# desacople: un arrastre que se pasa unos pixeles sigue siendo un clic.
MARGEN_SALIDA_PX = 30


class PestanasMovibles(TabWidget):
    """Emite `menu_pedido(pagina, posicion_global)` con clic derecho sobre
    una pestaña; la ventana arma el menu (ella conoce al WindowManager). Y
    `arrastre_soltado(pagina, posicion_global)` al soltar una pestaña
    arrastrada afuera de la barra."""

    menu_pedido = Signal(object, QPoint)
    arrastre_soltado = Signal(object, QPoint)
    # Entro, salio o se eligio otra pestaña: la disposicion guardada cambio.
    cambio = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(self.cambio)
        self.setMovable(False)
        self.setTabShadowEnabled(True)
        self.setCloseButtonDisplayMode(TabCloseButtonDisplayMode.ON_HOVER)
        self.setTabsClosable(True)
        # El arrastre en curso: la pestaña presionada y donde empezo (global).
        self._arrastre: tuple[QWidget, QPoint] | None = None
        self._afuera = False

    def insertTab(self, index, widget, label, icon=None, routeKey=None) -> int:  # noqa: N802, N803
        nuevo = super().insertTab(index, widget, label, icon, routeKey)
        if nuevo >= 0:
            # Menu y arrastre por un filtro de eventos, no por una lambda que
            # capture `self` (arma un ciclo por C++ que el GC no ve).
            self.tabBar.tabItem(nuevo).installEventFilter(self)
            self.cambio.emit()
        return nuevo

    def _pagina_de(self, item: QObject) -> QWidget | None:
        """La pagina de una pestaña de la barra (None si ya no esta)."""
        items = self.tabBar.items
        return self.widget(items.index(item)) if item in items else None

    def fuera_de_la_barra(self, pos: QPoint) -> bool:
        """Si `pos` (global) quedo lejos de la barra de pestañas."""
        barra = QRect(self.tabBar.mapToGlobal(QPoint(0, 0)), self.tabBar.size())
        return not barra.adjusted(0, -MARGEN_SALIDA_PX, 0, MARGEN_SALIDA_PX).contains(pos)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - override de Qt
        tipo = event.type()
        if tipo == QEvent.Type.ContextMenu:
            pagina = self._pagina_de(obj)
            if pagina is not None:
                self.menu_pedido.emit(pagina, event.globalPos())
                return True
        elif tipo == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                pagina = self._pagina_de(obj)
                movible = pagina is not None and pagina.property("routeKey") != HOME_ROUTE_KEY
                self._arrastre = (obj, event.globalPosition().toPoint()) if movible else None
                self._afuera = False
        elif tipo == QEvent.Type.MouseMove:
            self._al_mover(obj, event.globalPosition().toPoint(), event.buttons())
        elif tipo == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self._al_soltar(obj, event.globalPosition().toPoint())
        return super().eventFilter(obj, event)

    def _al_mover(self, obj: QObject, pos: QPoint, botones) -> None:
        if self._arrastre is None or self._arrastre[0] is not obj:
            return
        if not botones & Qt.MouseButton.LeftButton:
            self._terminar_arrastre()
            return
        lejos = (pos - self._arrastre[1]).manhattanLength() >= QApplication.startDragDistance()
        afuera = lejos and self.fuera_de_la_barra(pos)
        if afuera != self._afuera:
            self._afuera = afuera
            # Que se vea que soltar ahi saca la pestaña.
            if afuera:
                QApplication.setOverrideCursor(Qt.CursorShape.DragMoveCursor)
            else:
                QApplication.restoreOverrideCursor()

    def _al_soltar(self, obj: QObject, pos: QPoint) -> None:
        if self._arrastre is None or self._arrastre[0] is not obj:
            return
        afuera = self._afuera
        self._terminar_arrastre()
        pagina = self._pagina_de(obj)
        if afuera and pagina is not None:
            self.arrastre_soltado.emit(pagina, pos)

    def _terminar_arrastre(self) -> None:
        if self._afuera:
            QApplication.restoreOverrideCursor()
        self._arrastre = None
        self._afuera = False

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
    abrir.triggered.connect(lambda: desacoplar(widget, origen, nueva_ventana))
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
            lambda _c=False, destino=ventana: llevar_a(widget, origen, destino)
        )
        menu.addAction(accion)
    return menu


def desacoplar(
    widget: QWidget, origen: VentanaConPestanas, nueva_ventana, pos: QPoint | None = None
) -> QWidget:
    """La pagina a una ventana nueva. Con `pos` (global, al arrastrar) la
    ventana queda bajo el cursor; sin ella, corrida de la principal."""
    destino = nueva_ventana()
    mover(widget, origen, destino)
    if pos is not None:
        poner_bajo_el_cursor(destino, pos)
    destino.show()
    destino.raise_()
    destino.activateWindow()
    return destino


def poner_bajo_el_cursor(ventana: QWidget, pos: QPoint) -> None:
    """La ventana con el cursor arriba al centro, como si se la tuviera
    agarrada de la barra de pestañas."""
    ventana.move(pos - QPoint(ventana.width() // 2, MARGEN_SALIDA_PX))


def llevar_a(widget: QWidget, origen: VentanaConPestanas, destino: VentanaConPestanas) -> None:
    mover(widget, origen, destino)
    destino.raise_()
    destino.activateWindow()
