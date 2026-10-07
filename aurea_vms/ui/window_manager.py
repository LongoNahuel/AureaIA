"""Registro de las ventanas de la sesion y de los modulos abiertos en ellas.

Fase V2 (2026-10-07), base de las pestañas en varias ventanas: todo lo que
antes recorria `MainWindow.tabs` (buscar un modulo ya abierto, propagar el
filtro de sitio, cerrar ordenado) pasa por aca y ve las pestañas de TODAS
las ventanas. Una "ventana" es cualquier QWidget con un atributo `tabs` (el
TabWidget de qfluentwidgets): la principal y, desde V3, las secundarias.

Hay un registro por MainWindow, no uno global: logout -> login arma una
MainWindow nueva y el registro viejo se va con la anterior.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from PySide6.QtWidgets import QWidget


class VentanaConPestanas(Protocol):
    tabs: QWidget

    def isActiveWindow(self) -> bool: ...  # noqa: N802 - API de Qt


def route_key_de(module_cls: type) -> str:
    """Clave estable de un modulo: el nombre de su clase, no su posicion en
    MODULES (si cambia el orden del launcher, un layout guardado tiene que
    seguir abriendo lo mismo)."""
    return f"module-{module_cls.__name__}"


class WindowManager:
    def __init__(self, principal: VentanaConPestanas) -> None:
        self._principal = principal
        self._ventanas: list[VentanaConPestanas] = [principal]
        self._numerador = 1  # la principal es la 1
        # True mientras se cierra la principal: las secundarias que se cierran
        # en ese momento NO devuelven sus pestañas (la sesion se va entera).
        self.cerrando = False

    def proximo_numero(self) -> int:
        self._numerador += 1
        return self._numerador

    @property
    def principal(self) -> VentanaConPestanas:
        return self._principal

    def registrar(self, ventana: VentanaConPestanas) -> None:
        if ventana not in self._ventanas:
            self._ventanas.append(ventana)

    def quitar(self, ventana: VentanaConPestanas) -> None:
        if ventana is not self._principal and ventana in self._ventanas:
            self._ventanas.remove(ventana)

    def ventanas(self) -> list[VentanaConPestanas]:
        return list(self._ventanas)

    def secundarias(self) -> list[VentanaConPestanas]:
        return [v for v in self._ventanas if v is not self._principal]

    def modulos(self) -> Iterator[tuple[VentanaConPestanas, QWidget]]:
        """Cada pagina de pestaña de cada ventana, con su ventana."""
        for ventana in self.ventanas():
            tabs = ventana.tabs
            for i in range(tabs.count()):
                yield ventana, tabs.widget(i)

    def buscar(self, route_key: str) -> tuple[VentanaConPestanas, QWidget] | None:
        for ventana, widget in self.modulos():
            if widget.property("routeKey") == route_key:
                return ventana, widget
        return None

    def buscar_modulo(self, module_cls: type) -> tuple[VentanaConPestanas, QWidget] | None:
        """Una instancia abierta del modulo, en cualquier ventana. Vista en
        Vivo e Inteligente pueden tener varias (`module-<Clase>-<n>`): da la
        primera que encuentra."""
        base = route_key_de(module_cls)
        for ventana, widget in self.modulos():
            clave = widget.property("routeKey") or ""
            if clave == base or clave.startswith(base + "-"):
                return ventana, widget
        return None

    def claves_abiertas(self) -> set[str]:
        return {widget.property("routeKey") or "" for _v, widget in self.modulos()}

    def ventana_de(self, widget: QWidget) -> VentanaConPestanas | None:
        for ventana, pagina in self.modulos():
            if pagina is widget:
                return ventana
        return None

    def enfocar(self, widget: QWidget) -> None:
        """Muestra la pestaña de `widget` y trae su ventana al frente."""
        ventana = self.ventana_de(widget)
        if ventana is None:
            return
        ventana.tabs.setCurrentWidget(widget)
        if ventana is not self._principal or not ventana.isActiveWindow():
            ventana.raise_()
            ventana.activateWindow()

    def ventana_activa(self) -> VentanaConPestanas:
        """La ventana con el foco; la principal si ninguna lo tiene (la app
        esta en segundo plano)."""
        for ventana in self._ventanas:
            if ventana.isActiveWindow():
                return ventana
        return self._principal
