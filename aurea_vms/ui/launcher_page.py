"""Pantalla de inicio (pestaña "Inicio"): panel de accesos rapidos a la
izquierda (estilo "Base" de EZStation) + tarjetas grandes agrupadas por
categoria a la derecha, al estilo del launcher de tareas de Genetec
Security Center / EZStation. Las tarjetas abren (o activan, si ya esta
abierta) una pestaña de modulo; los accesos rapidos son atajos a una
seccion especifica DENTRO de un modulo (ej. Registro de operaciones
dentro de Sistema), no modulos nuevos."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FlowLayout,
    ScrollArea,
    StrongBodyLabel,
)

from aurea_vms.ui import icons
from aurea_vms.ui.widgets.dashboard_panel import DashboardPanel
from aurea_vms.ui.widgets.video_background import VideoBackground

MODULE_COLORS = {
    "Vista en Vivo": "#3b82f6",
    "Alarmas": "#ef4444",
    "Dispositivos": "#14b8a6",
    "Analizadores": "#8b5cf6",
    "Alertas": "#f59e0b",
    "Sistema": "#64748b",
    "Usuarios": "#3b82f6",
}

MODULE_DESCRIPTIONS = {
    "Vista en Vivo": "Video en vivo, grilla y Vista Inteligente",
    "Alarmas": "Feed de alarmas, capturas y clips de evento",
    "Dispositivos": "Alta, edición y descubrimiento ONVIF",
    "Analizadores": "Movimiento, personas, cruce de línea, rostros",
    "Alertas": "Reglas que disparan las alarmas",
    "Sistema": "Configuración, recursos y logs",
    "Usuarios": "Cuentas locales y roles (Administrador/Operador)",
}

BADGE_SIZE = 60
CARD_SIZE = (200, 168)

# (etiqueta, fabrica de icono, color, indice de modulo en MODULES,
#  grupo/hoja del arbol de navegacion de ese modulo si aplica -- "" si el
#  atajo solo tiene que abrir el modulo, sin enfocar una seccion interna --
#  solo admin)
HOME_SHORTCUTS = [
    ("Ver", "icon_live_view", "#3b82f6", 0, "", "", False),
    ("Configuración de la Alarma", "icon_alerts", "#ef4444", 4, "", "", True),
    ("Configuración del sistema", "icon_system", "#64748b", 5, "", "", True),
    ("Recurso de secuencia", "icon_devices", "#3b82f6", 5, "Sistema", "Inicio", True),
    ("Gestión de usuarios", "icon_users", "#3b82f6", 6, "", "", True),
    ("Horario de grabación", "icon_play", "#f59e0b", 5, "Audio y Video", "Grabando", True),
    ("Registro de operaciones", "icon_history", "#22c55e", 5, "Sistema", "Registro", True),
]


def _badge_pixmap(icon_factory, icon_size: int, dpr: float) -> QPixmap:
    """Pixmap del glifo al devicePixelRatio real. Pedirlo explicito importa:
    los QIcon de qfluentwidgets ya devuelven pixmaps DPR-aware y los de
    icons.load_icon tambien -- pero mezclar ambos con .pixmap(w, h) pelado
    daba tamaños fisicos distintos segun el origen del icono."""
    return icon_factory(color="white", size=icon_size).pixmap(QSize(icon_size, icon_size), dpr)


def _draw_centered_pixmap(painter: QPainter, widget: QWidget, pixmap: QPixmap) -> None:
    """Centra usando el tamaño LOGICO del pixmap: width() es fisico, y a
    escala 2x centrar con el fisico corria el glifo fuera del badge (el bug
    de "Gestion de usuarios" con el icono colgando de la esquina)."""
    dpr = pixmap.devicePixelRatio()
    x = (widget.width() - pixmap.width() / dpr) / 2
    y = (widget.height() - pixmap.height() / dpr) / 2
    painter.drawPixmap(QPointF(x, y), pixmap)


class _IconBadge(QWidget):
    """Icono Lucide (blanco) centrado sobre una placa circular de color."""

    def __init__(self, icon_factory, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        icon_size = BADGE_SIZE // 2
        self._pixmap = _badge_pixmap(icon_factory, icon_size, self.devicePixelRatioF())
        self.setFixedSize(BADGE_SIZE, BADGE_SIZE)

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self.rect())
        _draw_centered_pixmap(painter, self, self._pixmap)


class _SquareIconBadge(QWidget):
    """Version chica y con esquinas redondeadas de _IconBadge, para las
    filas del panel de accesos rapidos (mismo estilo que EZStation)."""

    SIZE = 34

    def __init__(self, icon_factory, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        icon_size = self.SIZE - 14
        self._pixmap = _badge_pixmap(icon_factory, icon_size, self.devicePixelRatioF())
        self.setFixedSize(self.SIZE, self.SIZE)

    def paintEvent(self, event) -> None:  # noqa: N802 - override de Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 8, 8)
        _draw_centered_pixmap(painter, self, self._pixmap)


class ModuleCard(CardWidget):
    def __init__(
        self,
        index: int,
        label: str,
        icon_factory,
        color: str,
        description: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.index = index
        self.setFixedSize(*CARD_SIZE)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(16, 20, 16, 18)
        layout.setSpacing(10)

        badge = _IconBadge(icon_factory, color, self)
        layout.addWidget(badge, alignment=Qt.AlignmentFlag.AlignHCenter)

        text_label = StrongBodyLabel(label, self)
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text_label)

        if description:
            desc_label = CaptionLabel(description, self)
            desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)


class _ShortcutRow(CardWidget):
    def __init__(self, label: str, icon_factory, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(46)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(10)

        badge = _SquareIconBadge(icon_factory, color, self)
        layout.addWidget(badge)

        text = BodyLabel(label, self)
        text.setWordWrap(True)
        layout.addWidget(text, stretch=1)


class HomeConfigPanel(QWidget):
    """Panel "Base" de accesos rapidos a la izquierda de Inicio -- atajos
    a secciones especificas de otros modulos, no modulos en si mismos."""

    shortcut_clicked = Signal(int, str, str)  # indice de modulo, grupo, hoja

    def __init__(self, is_admin: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(260)
        self.setStyleSheet("background-color: rgba(13, 18, 26, 225);")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 14)
        layout.setSpacing(3)

        header = StrongBodyLabel("Base", self)
        header.setStyleSheet("padding: 4px 16px 10px 16px; background: transparent;")
        layout.addWidget(header)

        for label, icon_name, color, module_index, group, leaf, admin_only in HOME_SHORTCUTS:
            if admin_only and not is_admin:
                continue
            icon_factory = getattr(icons, icon_name)
            row = _ShortcutRow(label, icon_factory, color, self)
            row.clicked.connect(
                lambda _checked=False, mi=module_index, g=group, lf=leaf: (
                    self.shortcut_clicked.emit(mi, g, lf)
                )
            )
            layout.addWidget(row)

        layout.addStretch(1)


class LauncherPage(QWidget):
    module_requested = Signal(int)
    shortcut_requested = Signal(int, str, str)

    def __init__(
        self,
        modules: list[tuple[str, object, type]],
        categories: dict[str, list[str]],
        is_admin: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.sidebar = HomeConfigPanel(is_admin, self)
        self.sidebar.shortcut_clicked.connect(self.shortcut_requested)

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background: transparent; border: none;}")

        overlay = QColor(8, 11, 18, 175)
        container = VideoBackground(icons.launcher_background_video_path(), overlay, scroll)
        scroll.setWidget(container)

        outer = QVBoxLayout(container)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(22)

        outer.addWidget(DashboardPanel(container))

        label_to_index = {label: i for i, (label, _icon, _cls) in enumerate(modules)}

        for category_name, labels in categories.items():
            title = StrongBodyLabel(category_name, container)
            title.setStyleSheet("font-size: 16px; background: transparent;")
            outer.addWidget(title)

            flow_container = QWidget(container)
            flow_container.setStyleSheet("background: transparent;")
            flow = FlowLayout(flow_container, needAni=True)
            flow.setContentsMargins(0, 0, 0, 0)
            flow.setHorizontalSpacing(16)
            flow.setVerticalSpacing(16)

            for label in labels:
                index = label_to_index[label]
                _, icon_factory, _cls = modules[index]
                color = MODULE_COLORS.get(label, "#3b82f6")
                description = MODULE_DESCRIPTIONS.get(label, "")
                card = ModuleCard(index, label, icon_factory, color, description, flow_container)
                card.clicked.connect(lambda _checked=False, i=index: self.module_requested.emit(i))
                flow.addWidget(card)

            outer.addWidget(flow_container)

        outer.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(scroll, stretch=1)
