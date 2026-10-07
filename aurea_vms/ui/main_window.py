"""Ventana principal: una sola ventana con pestañas (al estilo Genetec
Security Center) -- una pestaña "Inicio" fija con el launcher de tarjetas
por categoria, y una pestaña por cada modulo que se va abriendo desde ahi
(o se re-activa, si ya estaba abierta)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMainWindow, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, FluentIcon, PushButton

from aurea_vms.core import app_state, auth, desktop_notify
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import AlarmEvent
from aurea_vms.core.permissions import Perm, can
from aurea_vms.models import repository
from aurea_vms.models.user import ROLE_LABELS
from aurea_vms.ui import icons, pestanas, sound
from aurea_vms.ui.dialogs.command_palette_dialog import (
    ACTION_OPEN_MODULE,
    ACTION_QUICK_VIEW,
    CommandPaletteDialog,
)
from aurea_vms.ui.launcher_page import LauncherPage
from aurea_vms.ui.modules.alarm_module import AlarmModule
from aurea_vms.ui.modules.alert_config import AlertConfigModule
from aurea_vms.ui.modules.analytics_config import AnalyticsConfigModule
from aurea_vms.ui.modules.device_management import DeviceManagementModule
from aurea_vms.ui.modules.event_dashboard import EventDashboardModule
from aurea_vms.ui.modules.intelligent_view import IntelligentViewModule
from aurea_vms.ui.modules.live_view import LiveViewModule
from aurea_vms.ui.modules.sites_zones_module import SitesZonesModule
from aurea_vms.ui.modules.system_module import SystemModule
from aurea_vms.ui.modules.user_management_module import UserManagementModule
from aurea_vms.ui.notify import confirm, warn
from aurea_vms.ui.pestanas import HOME_ROUTE_KEY, PestanasMovibles
from aurea_vms.ui.ventana_secundaria import VentanaSecundaria
from aurea_vms.ui.widgets.global_alert_popup import GlobalAlertPopupLayer
from aurea_vms.ui.window_manager import WindowManager, route_key_de

WINDOW_SIZE = (1320, 840)

# (etiqueta visible, fabrica de icono Lucide -- misma imagen en la tarjeta
# del launcher y en la pestaña del modulo, para que se reconozcan como la
# misma cosa -- clase del modulo)
MODULES = [
    ("Vista en Vivo", icons.icon_live_view, LiveViewModule),
    ("Vista Inteligente", icons.icon_ai_brain, IntelligentViewModule),
    ("Dispositivos", icons.icon_devices, DeviceManagementModule),
    ("Analizadores", icons.icon_analyzers, AnalyticsConfigModule),
    ("Alarmas", icons.icon_alarms, AlarmModule),
    ("Alertas", icons.icon_alerts, AlertConfigModule),
    ("Sistema", icons.icon_system, SystemModule),
    ("Usuarios", icons.icon_users, UserManagementModule),
    ("Sitios y Zonas", icons.icon_sites, SitesZonesModule),
    ("Dashboard de Eventos", icons.icon_alarms, EventDashboardModule),
]

CATEGORIES = {
    "Operación": ["Vista en Vivo", "Vista Inteligente", "Alarmas", "Dashboard de Eventos"],
    "Configuración": [
        "Dispositivos",
        "Analizadores",
        "Alertas",
        "Sistema",
        "Usuarios",
        "Sitios y Zonas",
    ],
}

# Permiso requerido para ver/abrir cada modulo. Chequeado tanto al armar
# el launcher como en open_module_by_index (defensa en profundidad: los
# accesos "rapidos" via event_bus, ej. Vista rapida desde Dispositivos,
# tambien pasan por ahi).
MODULE_PERMISSIONS = {
    "Vista en Vivo": Perm.LIVE_VIEW,
    "Vista Inteligente": Perm.LIVE_VIEW,
    "Alarmas": Perm.RECORDINGS,
    "Dispositivos": Perm.DEVICE_ADMIN,
    "Analizadores": Perm.ANALYTICS_CONFIG,
    "Alertas": Perm.ANALYTICS_CONFIG,
    "Sistema": Perm.GLOBAL_CONFIG,
    "Usuarios": Perm.USER_ADMIN,
    "Sitios y Zonas": Perm.DEVICE_ADMIN,
    "Dashboard de Eventos": Perm.RECORDINGS,
}

# Modulos que admiten varias instancias abiertas a la vez (una grilla por
# monitor). El resto, una sola: dos pantallas de configuracion editando lo
# mismo no tienen sentido.
MULTI_INSTANCIA = (LiveViewModule, IntelligentViewModule)


def module_index(module_cls: type) -> int:
    """Posicion de un modulo en MODULES por su clase. Los atajos la usan en
    vez de un numero fijo: "Ajustes avanzados" abria el 2 (Dispositivos)
    creyendo que era Analizadores, y `focus_device` no hacia nada."""
    for index, (_label, _icon, cls) in enumerate(MODULES):
        if cls is module_cls:
            return index
    raise ValueError(f"{module_cls.__name__} no esta en MODULES")


def compute_visible_categories() -> dict:
    """Categorias del launcher visibles para el usuario en sesion, segun
    su matriz de permisos (funcion libre para poder testearla sin
    construir la ventana)."""
    filtered = {}
    for name, labels in CATEGORIES.items():
        visible = [label for label in labels if can(MODULE_PERMISSIONS[label])]
        if visible:
            filtered[name] = visible
    return filtered


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AureaIA VMS")
        self.setWindowIcon(icons.icon_live_view())
        self.resize(*WINDOW_SIZE)
        # True si se llego a close() via "Cerrar sesión" -- main.py lo usa
        # para decidir si vuelve a mostrar el login o corta la app del todo.
        self.logout_requested = False
        # Ventanas de la sesion y sus modulos (Fase V2): la principal y,
        # desde V3, las secundarias con pestañas arrancadas de esta.
        self.ventanas = WindowManager(self)
        # Cerrar la ventana la BORRA. Sin esto sobrevivia a cada logout:
        # la lambda del combo de sitio (y otras de los modulos) arma un ciclo
        # por C++ que el GC de Python no ve, y la ventana vieja seguia con
        # sus timers consultando la base y conectada al event_bus (medido
        # 2026-10-07: viva despues de close + del + gc.collect()).
        # `logout_requested` es atributo de Python: se lee igual despues.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addLayout(self._build_header())

        self.nombre = "principal"
        self.tabs = PestanasMovibles(self)
        self.tabs.tabCloseRequested.connect(lambda i: self.cerrar_pestana(self, i))
        self.tabs.menu_pedido.connect(lambda w, pos: self.menu_de_pestana(self, w, pos))
        # El "+" abre una Vista en Vivo nueva (antes no hacia nada).
        self.tabs.tabAddRequested.connect(lambda: self.nueva_vista_en_vivo(self))
        self.tabs.tabBar.setAddButtonVisible(self.puede_ver_en_vivo())
        central_layout.addWidget(self.tabs, stretch=1)

        self.setCentralWidget(central)

        self.launcher = LauncherPage(
            MODULES, self._visible_categories(), auth.is_admin(), self.tabs
        )
        self.launcher.module_requested.connect(self.open_module_by_index)
        self.launcher.shortcut_requested.connect(self._on_home_shortcut)
        self.tabs.addTab(self.launcher, "Inicio", FluentIcon.HOME, routeKey=HOME_ROUTE_KEY)
        self.tabs.setCurrentIndex(0)

        event_bus.open_live_view_requested.connect(
            self._on_open_live_view_requested, Qt.ConnectionType.QueuedConnection
        )
        event_bus.open_analytics_config_requested.connect(
            self._on_open_analytics_config_requested, Qt.ConnectionType.QueuedConnection
        )
        event_bus.alarm.connect(self._on_global_alarm, Qt.ConnectionType.QueuedConnection)
        event_bus.site_filter_changed.connect(self._on_site_filter_changed)

        # Capa de popups de alarma, visible sobre cualquier pestaña. Se
        # autoajusta a su contenido (ver GlobalAlertPopupLayer) y queda
        # oculta cuando no hay tarjetas -- no cubre toda la ventana.
        self.alert_layer = GlobalAlertPopupLayer(central)

        QShortcut(QKeySequence("Ctrl+K"), self, lambda: self.abrir_paleta(self))

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(14, 8, 14, 8)

        row.addStretch(1)

        user = auth.current_user
        role_label = ROLE_LABELS.get(user.role, user.role) if user is not None else "?"
        name = user.username if user is not None else "?"
        row.addWidget(BodyLabel(f"{name} · {role_label}"))
        row.addSpacing(18)

        # Selector global de sitio: filtra Vista en Vivo, Dispositivos y
        # Alarmas en toda la app (via app_state + site_filter_changed).
        row.addWidget(BodyLabel("Sitio:"))
        row.addSpacing(6)
        self.site_combo = ComboBox()
        self.site_combo.addItem("Todos los sitios", userData=None)
        for site in repository.list_sites():
            self.site_combo.addItem(site.name, userData=site.id)
        index = self.site_combo.findData(app_state.current_site_id)
        self.site_combo.setCurrentIndex(index if index >= 0 else 0)
        self.site_combo.currentIndexChanged.connect(
            lambda _i: app_state.set_site_filter(self.site_combo.currentData())
        )
        row.addWidget(self.site_combo)
        row.addSpacing(10)

        logout_button = PushButton(FluentIcon.RETURN, "Cerrar sesión")
        logout_button.clicked.connect(self._on_logout)
        row.addWidget(logout_button)
        return row

    def _on_site_filter_changed(self, site_id: object) -> None:
        """Propaga el filtro global de sitio a los DeviceTreeWidget de las
        pestañas ya abiertas, en todas las ventanas (las que se abran despues
        se inicializan con el filtro vigente en open_module_by_index)."""
        for _ventana, widget in self.ventanas.modulos():
            device_tree = getattr(widget, "device_tree", None)
            if device_tree is not None and hasattr(device_tree, "set_site_filter"):
                device_tree.set_site_filter(site_id)

    def _visible_categories(self) -> dict:
        return compute_visible_categories()

    def _on_logout(self) -> None:
        if not confirm(self, "Cerrar sesión", "¿Cerrar la sesión actual?"):
            return
        self.logout_requested = True
        auth.logout()
        app_state.reset()  # el filtro de sitio no debe sobrevivir a la sesion
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        """Cerrar la principal (X o "Cerrar sesión") cierra la sesion entera:
        las ventanas secundarias se van con ella y cada modulo suelta lo suyo
        (streams de los recuadros). Sin esto, con una secundaria abierta,
        app.exec() no volvia y los motores no se apagaban."""
        self.ventanas.cerrando = True  # las secundarias no devuelven pestañas
        for _ventana, widget in self.ventanas.modulos():
            on_close = getattr(widget, "on_window_closed", None)
            if callable(on_close):
                on_close()
        for ventana in self.ventanas.secundarias():
            ventana.close()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if hasattr(self, "alert_layer"):
            self.alert_layer.reposition()
        super().resizeEvent(event)

    def _on_global_alarm(self, event: AlarmEvent) -> None:
        """Slot con QueuedConnection: corre en el hilo principal aunque el
        AlarmEvent lo emita un AnalyticsWorker. Es el unico lugar donde se
        ejecutan las acciones de escritorio de una regla -- los engines solo
        marcan la intencion en el DTO."""
        device = repository.get_device(event.device_id)
        device_name = device.name if device is not None else f"Cámara {event.device_id}"
        # El popup sale en la ventana que el operador esta mirando (la que
        # tiene el foco; la principal si ninguna). Sonido y aviso de
        # escritorio, una sola vez: este slot existe solo en la principal.
        self.ventanas.ventana_activa().alert_layer.show_alarm(event, device_name)
        if event.play_sound:
            sound.play_alarm()
        if event.notify_desktop:
            desktop_notify.notify(
                f"Alarma ({event.severity}) — {device_name}",
                f"{event.object_class} detectado con {event.confidence:.0%} de confianza.",
            )

    def abrir_paleta(self, ventana: QWidget) -> None:
        """Ctrl+K desde cualquier ventana: lo que se abra nuevo va a ESA
        ventana (lo ya abierto se enfoca donde este)."""
        dialog = CommandPaletteDialog(MODULES, ventana)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.action is None:
            return
        action, value = dialog.action
        if action == ACTION_OPEN_MODULE:
            self.open_module_by_index(value, ventana)
        elif action == ACTION_QUICK_VIEW:
            event_bus.open_live_view_requested.emit(value)

    def _on_open_live_view_requested(self, device_id: int) -> None:
        """ "Vista rapida" desde Dispositivos: abre/enfoca Vista en Vivo y
        asigna esta camara en Vista Inteligente."""
        live_view = self.open_module_by_index(module_index(LiveViewModule))
        focus_camera = getattr(live_view, "focus_camera", None)
        if callable(focus_camera):
            focus_camera(device_id)

    def _on_open_analytics_config_requested(self, device_id: int) -> None:
        """ "Ajustes avanzados" desde Dispositivos: abre/enfoca Analizadores
        con esta camara seleccionada."""
        analytics_module = self.open_module_by_index(module_index(AnalyticsConfigModule))
        focus_device = getattr(analytics_module, "focus_device", None)
        if callable(focus_device):
            focus_device(device_id)

    def _on_home_shortcut(self, module_index: int, group: str, leaf: str) -> None:
        """Panel "Base" de Inicio: abre el modulo y, si el atajo apunta a
        una seccion especifica (ej. Sistema > Registro), la enfoca."""
        widget = self.open_module_by_index(module_index)
        if not group or not leaf:
            return
        focus_section = getattr(widget, "focus_section", None)
        if callable(focus_section):
            focus_section(group, leaf)

    def open_module_by_index(self, index: int, destino: QWidget | None = None) -> QWidget | None:
        """Abre el modulo, o enfoca el que ya esta abierto en CUALQUIER
        ventana, y lo devuelve (None si el rol no tiene permiso). Los atajos
        usan el widget devuelto: `self.tabs.currentWidget()` no sirve si el
        modulo vive en otra ventana. Uno nuevo va a `destino` (la principal
        por defecto)."""
        if not self._permitido(index):
            return None
        existente = self.ventanas.buscar_modulo(MODULES[index][2])
        if existente is not None:
            _ventana, widget = existente
            self.ventanas.enfocar(widget)
            return widget
        return self._crear_modulo(index, destino or self)

    def nueva_instancia(self, index: int, destino: QWidget) -> QWidget | None:
        """Siempre una instancia nueva (Vista en Vivo o Inteligente; para
        el resto es lo mismo que open_module_by_index)."""
        if MODULES[index][2] not in MULTI_INSTANCIA:
            return self.open_module_by_index(index, destino)
        if not self._permitido(index):
            return None
        return self._crear_modulo(index, destino)

    def nueva_vista_en_vivo(self, destino: QWidget) -> QWidget | None:
        return self.nueva_instancia(module_index(LiveViewModule), destino)

    def puede_ver_en_vivo(self) -> bool:
        return can(MODULE_PERMISSIONS[MODULES[module_index(LiveViewModule)][0]])

    def _permitido(self, index: int) -> bool:
        if can(MODULE_PERMISSIONS[MODULES[index][0]]):
            return True
        warn(self, "Acceso restringido", "Tu rol no tiene permisos para abrir esta sección.")
        return False

    def _crear_modulo(self, index: int, destino: QWidget) -> QWidget:
        label, icon_factory, module_cls = MODULES[index]
        route_key, label = self._clave_y_titulo(module_cls, label)
        content = module_cls()
        device_tree = getattr(content, "device_tree", None)
        if device_tree is not None and hasattr(device_tree, "set_site_filter"):
            device_tree.set_site_filter(app_state.current_site_id)
        destino.tabs.addTab(content, label, icon_factory(), routeKey=route_key)
        destino.tabs.setCurrentWidget(content)
        if destino is not self:
            destino.raise_()
            destino.activateWindow()
        return content

    def _clave_y_titulo(self, module_cls: type, label: str) -> tuple[str, str]:
        """La primera instancia es `module-<Clase>` y lleva el titulo del
        modulo; las siguientes, `module-<Clase>-<n>` y "<titulo> <n>"."""
        base = route_key_de(module_cls)
        abiertas = self.ventanas.claves_abiertas()
        if base not in abiertas:
            return base, label
        n = 2
        while f"{base}-{n}" in abiertas:
            n += 1
        return f"{base}-{n}", f"{label} {n}"

    def nueva_ventana(self) -> VentanaSecundaria:
        ventana = VentanaSecundaria(self, self.ventanas.proximo_numero())
        self.ventanas.registrar(ventana)
        return ventana

    def menu_de_pestana(self, ventana: QWidget, widget: QWidget, pos) -> None:
        menu = pestanas.armar_menu(self.ventanas, ventana, widget, self.nueva_ventana, ventana)
        if menu is not None:
            menu.exec(pos)

    def cerrar_pestana(self, ventana: QWidget, index: int) -> None:
        widget = ventana.tabs.widget(index)
        if widget is None or widget.property("routeKey") == HOME_ROUTE_KEY:
            return  # "Inicio" no se cierra
        on_close = getattr(widget, "on_window_closed", None)
        if callable(on_close):
            on_close()
        ventana.tabs.removeTab(index)
        widget.deleteLater()
        if hasattr(ventana, "pestana_salio"):
            ventana.pestana_salio()
