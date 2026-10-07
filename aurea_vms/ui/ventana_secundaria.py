"""Ventana secundaria: pestañas arrancadas de la principal para llevarlas a
otro monitor (Fase V3, 2026-10-07).

Tiene su propia barra de pestañas (con el mismo menu para moverlas), su capa
de popups de alarma y Ctrl+K. No tiene header: el selector de sitio y
"Cerrar sesión" viven solo en la principal, y el sitio es global.

Cerrarla con la X devuelve sus pestañas a la principal. Si la que se cierra
es la principal, la sesion entera se va y las pestañas de aca se sueltan
(MainWindow.closeEvent). Una secundaria que se queda sin pestañas se cierra.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from aurea_vms.ui import icons, pestanas
from aurea_vms.ui.pestanas import PestanasMovibles
from aurea_vms.ui.widgets.global_alert_popup import GlobalAlertPopupLayer

if TYPE_CHECKING:
    from aurea_vms.ui.main_window import MainWindow

# Corrimiento de una ventana nueva respecto de la que la abrio, para que no
# quede exactamente encima.
CORRIMIENTO_PX = 48


class VentanaSecundaria(QMainWindow):
    def __init__(self, principal: MainWindow, numero: int) -> None:
        super().__init__()
        self._principal = principal
        self.nombre = f"Ventana {numero}"
        self.setWindowTitle(f"AureaIA VMS · {self.nombre}")
        self.setWindowIcon(icons.icon_live_view())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = PestanasMovibles(self)
        self.tabs.tabCloseRequested.connect(lambda i: principal.cerrar_pestana(self, i))
        self.tabs.menu_pedido.connect(lambda w, pos: principal.menu_de_pestana(self, w, pos))
        self.tabs.tabAddRequested.connect(lambda: principal.nueva_vista_en_vivo(self))
        self.tabs.tabBar.setAddButtonVisible(principal.puede_ver_en_vivo())
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.alert_layer = GlobalAlertPopupLayer(central)
        QShortcut(QKeySequence("Ctrl+K"), self, lambda: principal.abrir_paleta(self))

        geometria = principal.geometry()
        self.resize(geometria.size())
        self.move(geometria.topLeft() + _corrimiento(numero))

    def pestana_salio(self) -> None:
        """La llama pestanas.mover cuando una pagina se va de aca."""
        if self.tabs.count() == 0:
            self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        manager = self._principal.ventanas
        if not manager.cerrando:
            # La X de una secundaria no cierra modulos: vuelven a la principal.
            while self.tabs.count():
                pestanas.mover(self.tabs.widget(0), self, self._principal)
        manager.quitar(self)
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.alert_layer.reposition()
        super().resizeEvent(event)


def _corrimiento(numero: int) -> QPoint:
    paso = CORRIMIENTO_PX * (numero - 1)
    return QPoint(paso, paso)
