"""Ventana principal: una sola ventana con pestañas (al estilo Genetec
Security Center) -- una pestaña "Inicio" fija con el launcher de tarjetas
por categoria, y una pestaña por cada modulo que se va abriendo desde ahi
(o se re-activa, si ya estaba abierta)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMainWindow, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon,
    PushButton,
    TabCloseButtonDisplayMode,
    TabWidget,
)

from aurea_vms.core import app_state, auth
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import AlarmEvent
from aurea_vms.core.permissions import Perm, can
from aurea_vms.models import repository
from aurea_vms.models.user import ROLE_LABELS
from aurea_vms.ui import icons, sound
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
from aurea_vms.ui.modules.live_view import LiveViewModule
from aurea_vms.ui.modules.sites_zones_module import SitesZonesModule
from aurea_vms.ui.modules.system_module import SystemModule
from aurea_vms.ui.modules.user_management_module import UserManagementModule
from aurea_vms.ui.notify import confirm, warn
from aurea_vms.ui.widgets.global_alert_popup import GlobalAlertPopupLayer

WINDOW_SIZE = (1320, 840)

# (etiqueta visible, fabrica de icono Lucide -- misma imagen en la tarjeta
# del launcher y en la pestaña del modulo, para que se reconozcan como la
# misma cosa -- clase del modulo)
MODULES = [
    ("Vista en Vivo", icons.icon_live_view, LiveViewModule),
    ("Dispositivos", icons.icon_devices, DeviceManagementModule),
    ("Analizadores", icons.icon_analyzers, AnalyticsConfigModule),
    ("Alarmas", icons.icon_alarms, AlarmModule),
    ("Alertas", icons.icon_alerts, AlertConfigModule),
    ("Sistema", icons.icon_system, SystemModule),
    ("Usuarios", icons.icon_users, UserManagementModule),
    ("Sitios y Zonas", icons.icon_sites, SitesZonesModule),
]

CATEGORIES = {
    "Operación": ["Vista en Vivo", "Alarmas"],
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
    "Alarmas": Perm.RECORDINGS,
    "Dispositivos": Perm.DEVICE_ADMIN,
    "Analizadores": Perm.ANALYTICS_CONFIG,
    "Alertas": Perm.ANALYTICS_CONFIG,
    "Sistema": Perm.GLOBAL_CONFIG,
    "Usuarios": Perm.USER_ADMIN,
    "Sitios y Zonas": Perm.DEVICE_ADMIN,
}

HOME_INDEX = 0


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

        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addLayout(self._build_header())

        self.tabs = TabWidget(self)
        self.tabs.setMovable(False)
        self.tabs.setTabShadowEnabled(True)
        self.tabs.setCloseButtonDisplayMode(TabCloseButtonDisplayMode.ON_HOVER)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        central_layout.addWidget(self.tabs, stretch=1)

        self.setCentralWidget(central)

        self.launcher = LauncherPage(
            MODULES, self._visible_categories(), auth.is_admin(), self.tabs
        )
        self.launcher.module_requested.connect(self.open_module_by_index)
        self.launcher.shortcut_requested.connect(self._on_home_shortcut)
        self.tabs.addTab(self.launcher, "Inicio", FluentIcon.HOME, routeKey="home")
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

        QShortcut(QKeySequence("Ctrl+K"), self, self._open_command_palette)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(14, 8, 14, 8)

        row.addStretch(1)

        user = auth.current_user
        role_label = ROLE_LABELS.get(user.role, user.role) if user is not None else "?"
        name = user.username if user is not None else "?"
        row.addWidget(BodyLabel(f"{name} · {role_label}"))

        # Selector global de sitio: filtra Vista en Vivo, Dispositivos y
        # Alarmas en toda la app (via app_state + site_filter_changed).
        row.addWidget(BodyLabel("Sitio:"))
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
        pestañas ya abiertas (las que se abran despues se inicializan con
        el filtro vigente en open_module_by_index)."""
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
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

    def resizeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if hasattr(self, "alert_layer"):
            self.alert_layer.reposition()
        super().resizeEvent(event)

    def _on_global_alarm(self, event: AlarmEvent) -> None:
        device = repository.get_device(event.device_id)
        device_name = device.name if device is not None else f"Cámara {event.device_id}"
        self.alert_layer.show_alarm(event, device_name)
        if event.play_sound:
            sound.play_alarm()

    def _open_command_palette(self) -> None:
        dialog = CommandPaletteDialog(MODULES, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.action is None:
            return
        action, value = dialog.action
        if action == ACTION_OPEN_MODULE:
            self.open_module_by_index(value)
        elif action == ACTION_QUICK_VIEW:
            event_bus.open_live_view_requested.emit(value)

    def _on_open_live_view_requested(self, device_id: int) -> None:
        """ "Vista rapida" desde Dispositivos: abre/enfoca Vista en Vivo y
        asigna esta camara en Vista Inteligente."""
        self.open_module_by_index(0)
        live_view = self.tabs.currentWidget()
        focus_camera = getattr(live_view, "focus_camera", None)
        if callable(focus_camera):
            focus_camera(device_id)

    def _on_open_analytics_config_requested(self, device_id: int) -> None:
        """ "Ajustes avanzados" desde Dispositivos: abre/enfoca Analizadores
        con esta camara seleccionada."""
        self.open_module_by_index(2)
        analytics_module = self.tabs.currentWidget()
        focus_device = getattr(analytics_module, "focus_device", None)
        if callable(focus_device):
            focus_device(device_id)

    def _on_home_shortcut(self, module_index: int, group: str, leaf: str) -> None:
        """Panel "Base" de Inicio: abre el modulo y, si el atajo apunta a
        una seccion especifica (ej. Sistema > Registro), la enfoca."""
        self.open_module_by_index(module_index)
        if not group or not leaf:
            return
        widget = self.tabs.currentWidget()
        focus_section = getattr(widget, "focus_section", None)
        if callable(focus_section):
            focus_section(group, leaf)

    def open_module_by_index(self, index: int) -> None:
        label, icon_factory, module_cls = MODULES[index]
        if not can(MODULE_PERMISSIONS[label]):
            warn(
                self,
                "Acceso restringido",
                "Tu rol no tiene permisos para abrir esta sección.",
            )
            return

        route_key = f"module-{index}"
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).property("routeKey") == route_key:
                self.tabs.setCurrentIndex(i)
                return

        content = module_cls()
        device_tree = getattr(content, "device_tree", None)
        if device_tree is not None and hasattr(device_tree, "set_site_filter"):
            device_tree.set_site_filter(app_state.current_site_id)
        self.tabs.addTab(content, label, icon_factory(), routeKey=route_key)
        self.tabs.setCurrentWidget(content)

    def _on_tab_close_requested(self, index: int) -> None:
        if index == HOME_INDEX:
            return  # "Inicio" no se cierra

        widget = self.tabs.widget(index)
        on_close = getattr(widget, "on_window_closed", None)
        if callable(on_close):
            on_close()
        self.tabs.removeTab(index)
        widget.deleteLater()
