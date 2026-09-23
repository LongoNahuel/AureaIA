"""Vista en Vivo: arbol de camaras (izquierda) + una unica grilla de video
(layout seleccionable 1x1..4x4, expandir/colapsar con doble click) que se
comparte entre dos modos:

- Vista en Vivo (pura): solo la grilla, sin panel de analitica -- para
  simplemente mirar camaras.
- Vista Inteligente: la MISMA grilla + un panel lateral con un submenu
  moderno tipo pivot (uno por cada analizador HABILITADO en la camara
  seleccionada: Movimiento, Conteo de Personas, Cruce de Linea,
  Detecciones Faciales) que alterna cual panel se muestra -- si no hay
  ninguno habilitado, muestra un aviso invitando a configurarlos en el
  modulo Analizadores. Cambiar de modo no reordena ni reconstruye la
  grilla: solo muestra u oculta el panel lateral.

Asignar una camara a un tile se hace arrastrandola desde el arbol, o con
doble click en el arbol estando el tile seleccionado (click simple sobre
el tile lo selecciona, y ese es el tile que alimenta el panel lateral en
Vista Inteligente) -- funciona igual en ambos modos.

Doble click SOBRE un tile con camara asignada lo expande a pantalla
completa de la grilla. Todas las vistas consumen el flujo principal.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    SegmentedWidget,
    TransparentToolButton,
)

from aurea_vms.core import app_state
from aurea_vms.core.analytics_engine import analytics_engine
from aurea_vms.core.event_bus import event_bus
from aurea_vms.models import repository
from aurea_vms.ui import icons
from aurea_vms.ui.widgets.branded_background import BrandedBackground
from aurea_vms.ui.widgets.device_tree import DeviceTreeWidget
from aurea_vms.ui.widgets.door_state_panel import DoorStatePanel
from aurea_vms.ui.widgets.face_gallery import FaceGallery
from aurea_vms.ui.widgets.line_crossing_panel import LineCrossingPanel
from aurea_vms.ui.widgets.people_count_panel import PeopleCountPanel
from aurea_vms.ui.widgets.video_tile import VideoTile

GRID_LAYOUTS = [(1, 1), (2, 2), (3, 3), (4, 4)]
MAX_TILES = GRID_LAYOUTS[-1][0] * GRID_LAYOUTS[-1][1]
DEFAULT_LAYOUT_INDEX = 1  # 2x2

MODE_NORMAL = 0
MODE_SMART = 1


class _InsightCard(QFrame):
    def __init__(self, title: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame { background: rgba(8, 20, 38, 205); border: 1px solid rgba(86, 171, 224, 70);"
            " border-radius: 10px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(1)
        self.value = BodyLabel("—", self)
        self.value.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {accent}; background: transparent;"
        )
        caption = CaptionLabel(title, self)
        caption.setStyleSheet("color: #a9c4d8; background: transparent;")
        layout.addWidget(self.value)
        layout.addWidget(caption)

    def set_value(self, value: int | str) -> None:
        self.value.setText(str(value))


class LiveViewModule(QWidget):
    def __init__(self, parent: QWidget | None = None, *, smart_only: bool = False) -> None:
        super().__init__(parent)
        self._smart_only = smart_only
        self._mode = MODE_SMART if smart_only else MODE_NORMAL
        self._selected_tile: VideoTile | None = None
        self._expanded_tile: VideoTile | None = None
        self._fullscreen = False

        self.tiles: list[VideoTile] = [VideoTile(i, self) for i in range(MAX_TILES)]
        for tile in self.tiles:
            tile.clicked.connect(self._on_tile_clicked)
            tile.doubleClicked.connect(self._on_tile_double_clicked)
            tile.device_assigned.connect(lambda _device_id, t=tile: self._on_tile_device_changed(t))

        self.device_tree = DeviceTreeWidget(self)
        self.device_tree.device_double_clicked.connect(self._assign_to_selected)
        self.device_tree.setMinimumWidth(220)
        self.device_tree.setMaximumWidth(320)
        # Metodo bound (no lambda): Qt corta la conexion al destruirse el
        # modulo y el bus no queda apuntando a un widget muerto.
        event_bus.site_filter_changed.connect(self._on_site_filter_changed)
        event_bus.analytics_config_changed.connect(
            self._on_analytics_config_changed, Qt.ConnectionType.QueuedConnection
        )

        self.grid_container = QWidget(self)
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(3)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)

        self.door_state_panel = DoorStatePanel(self)
        self.people_count_panel = PeopleCountPanel(self)
        self.line_crossing_panel = LineCrossingPanel(self)
        self.face_gallery = FaceGallery(self)
        self.side_panel = self._build_side_panel()

        mode_row = self._build_mode_toggle()
        toolbar = self._build_toolbar()

        content_row = QHBoxLayout()
        content_row.setSpacing(8)
        content_row.addWidget(self.grid_container, stretch=1)
        content_row.addWidget(self.side_panel)

        right_side = QWidget(self)
        right_side.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        right_layout = QVBoxLayout(right_side)
        right_layout.setContentsMargins(14, 8, 14, 0)
        if smart_only:
            title_row = QHBoxLayout()
            title_row.addStretch(1)
            title_icon = TransparentToolButton(icons.icon_ai_brain("#62d8ff", 26), right_side)
            title_icon.setEnabled(False)
            title_row.addWidget(title_icon)
            title = BodyLabel("Vista Inteligente", right_side)
            title.setStyleSheet(
                "font-size: 22px; font-weight: 700; color: #e8f7ff; "
                "background: transparent;"
            )
            title_row.addWidget(title)
            title_row.addStretch(1)
            right_layout.addLayout(title_row)
            self.insight_cards = self._build_insight_cards(right_side)
            right_layout.addLayout(self.insight_cards)
        right_layout.addLayout(mode_row)
        right_layout.addLayout(content_row, stretch=1)
        right_layout.addLayout(toolbar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.device_tree)
        self.splitter.addWidget(right_side)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([240, 1000])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.splitter)

        self._background: BrandedBackground | None = None
        if smart_only:
            from PySide6.QtGui import QColor

            self._background = BrandedBackground(
                icons.algorithm_background_pixmap(),
                overlay=QColor(5, 12, 24, 125),
                parent=self,
            )
            self._background.setGeometry(self.rect())
            self._background.lower()
            self.splitter.setStyleSheet("QSplitter { background: transparent; }")

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._exit_fullscreen)

        self._apply_grid(*GRID_LAYOUTS[DEFAULT_LAYOUT_INDEX])
        self.layout_buttons.buttons()[DEFAULT_LAYOUT_INDEX].setChecked(True)
        self.side_panel.setVisible(smart_only)
        self._refresh_side_panel()
        if smart_only:
            self._refresh_insight_cards()
            self._insight_timer = QTimer(self)
            self._insight_timer.setInterval(5000)
            self._insight_timer.timeout.connect(self._refresh_insight_cards)
            self._insight_timer.start()

    def _build_insight_cards(self, parent: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 4)
        row.setSpacing(8)
        self._insight_devices = _InsightCard("Cámaras", "#72c7ff", parent)
        self._insight_online = _InsightCard("En línea", "#4ade80", parent)
        self._insight_alerts = _InsightCard("Alertas activas", "#fbbf24", parent)
        self._insight_analytics = _InsightCard("Analíticas", "#c4b5fd", parent)
        for card in (
            self._insight_devices,
            self._insight_online,
            self._insight_alerts,
            self._insight_analytics,
        ):
            row.addWidget(card, stretch=1)
        return row

    def _refresh_insight_cards(self) -> None:
        devices = repository.list_devices(site_id=app_state.current_site_id)
        self._insight_devices.set_value(len(devices))
        self._insight_online.set_value(sum(device.status == "online" for device in devices))
        self._insight_alerts.set_value(repository.count_pending_alarm_events())
        self._insight_analytics.set_value(analytics_engine.running_count())

    def _build_side_panel(self) -> QWidget:
        panel_style = "HeaderCardWidget { background-color: rgba(16, 21, 30, 210); }"
        # Orden fijo de submenu: mismo orden en el que aparecen las pestañas del
        # pivot sea cual sea el orden en que la DB devuelva las configs.
        self._analyzer_panels: dict[str, tuple[str, QWidget]] = {
            "door_state": ("Puerta", self.door_state_panel),
            "people_counting": ("Conteo de Personas", self.people_count_panel),
            "line_crossing": ("Cruce de Línea", self.line_crossing_panel),
            "face_detection": ("Detección Facial", self.face_gallery),
        }
        for _, panel in self._analyzer_panels.values():
            panel.setStyleSheet(panel_style)

        side_panel = QWidget(self)
        side_panel.setMaximumWidth(300)
        side_panel.setMinimumWidth(280)

        self.no_analyzers_label = CaptionLabel(
            "Seleccioná una cámara con analizadores habilitados.\n"
            "Configuralos en el módulo Analizadores.",
            side_panel,
        )
        self.no_analyzers_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_analyzers_label.setWordWrap(True)

        # Submenu moderno tipo pivot: una pestaña por analizador habilitado en
        # la camara seleccionada, alternando cual panel se ve en vez de
        # apilarlos todos juntos.
        self.analyzer_pivot = SegmentedWidget(side_panel)
        self.analyzer_stack = QStackedWidget(side_panel)

        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(8)
        side_layout.addWidget(self.no_analyzers_label)
        side_layout.addWidget(self.analyzer_pivot)
        side_layout.addWidget(self.analyzer_stack, stretch=1)

        return side_panel

    def _rebuild_analyzer_pivot(self, enabled_names: list[str]) -> None:
        self.analyzer_pivot.clear()
        while self.analyzer_stack.count():
            self.analyzer_stack.removeWidget(self.analyzer_stack.widget(0))

        for name in enabled_names:
            label, panel = self._analyzer_panels[name]
            self.analyzer_stack.addWidget(panel)
            self.analyzer_pivot.addItem(
                routeKey=name,
                text=label,
                onClick=lambda _checked=False, w=panel: self.analyzer_stack.setCurrentWidget(w),
            )

        has_any = bool(enabled_names)
        self.no_analyzers_label.setVisible(not has_any)
        self.analyzer_pivot.setVisible(has_any)
        self.analyzer_stack.setVisible(has_any)
        if has_any:
            self.analyzer_pivot.setCurrentItem(enabled_names[0])
            self.analyzer_stack.setCurrentWidget(self._analyzer_panels[enabled_names[0]][1])

    def _build_mode_toggle(self) -> QHBoxLayout:
        return QHBoxLayout()

    def _set_mode(self, mode: int) -> None:
        if self._smart_only:
            mode = MODE_SMART
        self._mode = mode
        self.side_panel.setVisible(mode == MODE_SMART)
        for tile in self.tiles:
            tile.set_intelligent_mode(mode == MODE_SMART)
            if mode == MODE_NORMAL and tile is not self._expanded_tile:
                tile.set_stream_kind("sub")

    def resizeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if self._background is not None:
            self._background.setGeometry(self.rect())
        super().resizeEvent(event)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()

        self.layout_buttons = QButtonGroup(self)
        self.layout_buttons.setExclusive(True)
        for rows, cols in GRID_LAYOUTS:
            button = TransparentToolButton(icons.icon_grid(rows, cols), self)
            button.setCheckable(True)
            button.setToolTip(f"Grilla {rows}x{cols}")
            button.clicked.connect(lambda _checked=False, r=rows, c=cols: self._apply_grid(r, c))
            self.layout_buttons.addButton(button)
            toolbar.addWidget(button)

        toolbar.addStretch(1)

        self.fullscreen_button = TransparentToolButton(FluentIcon.FULL_SCREEN, self)
        self.fullscreen_button.setToolTip("Pantalla completa")
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
        toolbar.addWidget(self.fullscreen_button)

        return toolbar

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self.device_tree.set_site_filter(app_state.current_site_id)
        super().showEvent(event)

    def _on_site_filter_changed(self, site_id: object) -> None:
        self.device_tree.set_site_filter(site_id)

    def _on_analytics_config_changed(self, device_id: int) -> None:
        for tile in self.tiles:
            if tile.device_id == device_id:
                tile.refresh_analytics_configs()

    def focus_camera(self, device_id: int) -> None:
        """API publica para otros modulos: selecciona el primer tile de la
        grilla de Vista en Vivo y le asigna esta camara."""
        self._on_tile_clicked(self.tiles[0])
        self.tiles[0].assign_device(device_id)

    def on_window_closed(self) -> None:
        """Llamado por MainWindow al cerrar la pestaña: libera las camaras
        activas (si no se hace, sus StreamWorker quedarian corriendo
        indefinidamente, sin nadie que los libere)."""
        for tile in self.tiles:
            tile.release()

    # --- seleccion / asignacion -------------------------------------------------

    def _on_tile_clicked(self, tile: VideoTile) -> None:
        if self._selected_tile is not None:
            self._selected_tile.set_selected(False)
        self._selected_tile = tile
        tile.set_selected(True)
        self._refresh_side_panel()

    def _assign_to_selected(self, device_id: int) -> None:
        if self._selected_tile is None:
            self._on_tile_clicked(self.tiles[0])
        self._selected_tile.assign_device(device_id)

    def _on_tile_device_changed(self, tile: VideoTile) -> None:
        if tile is self._selected_tile:
            self._refresh_side_panel()

    def _refresh_side_panel(self) -> None:
        device_id = self._selected_tile.device_id if self._selected_tile is not None else None
        for _, panel in self._analyzer_panels.values():
            panel.set_device(device_id)

        enabled: set[str] = set()
        if device_id is not None:
            enabled = {
                config.analyzer_name
                for config in repository.list_analytics_configs(device_id)
                if config.enabled
            }
        # Orden fijo del pivot (definido en self._analyzer_panels), no el de la DB.
        enabled_names = [name for name in self._analyzer_panels if name in enabled]
        self._rebuild_analyzer_pivot(enabled_names)

    # --- expandir/colapsar (doble click) -------------------------------------------------

    def _on_tile_double_clicked(self, tile: VideoTile) -> None:
        if self._expanded_tile is tile:
            self._collapse_tile()
            return
        if not tile.has_device():
            return

        if self._expanded_tile is not None and self._mode == MODE_NORMAL:
            self._expanded_tile.set_stream_kind("sub")

        self._expanded_tile = tile
        tile.set_stream_kind("main")
        self._show_only(tile)
        self._set_layout_buttons_enabled(False)

    def _collapse_tile(self) -> None:
        if self._expanded_tile is None:
            return
        if self._mode == MODE_NORMAL:
            self._expanded_tile.set_stream_kind("sub")
        self._expanded_tile = None
        self._set_layout_buttons_enabled(True)

        checked = self.layout_buttons.checkedButton()
        buttons = self.layout_buttons.buttons()
        index = buttons.index(checked) if checked in buttons else DEFAULT_LAYOUT_INDEX
        self._apply_grid(*GRID_LAYOUTS[index])

    def _show_only(self, tile: VideoTile) -> None:
        while self.grid_layout.count():
            self.grid_layout.takeAt(0)
        for other in self.tiles:
            other.setVisible(other is tile)
        self.grid_layout.addWidget(tile, 0, 0)

    def _set_layout_buttons_enabled(self, enabled: bool) -> None:
        for button in self.layout_buttons.buttons():
            button.setEnabled(enabled)

    # --- layout de la grilla -------------------------------------------------

    def _apply_grid(self, rows: int, cols: int) -> None:
        while self.grid_layout.count():
            self.grid_layout.takeAt(0)

        needed = rows * cols
        for i, tile in enumerate(self.tiles):
            if i < needed:
                r, c = divmod(i, cols)
                self.grid_layout.addWidget(tile, r, c)
                tile.setVisible(True)
            else:
                tile.setVisible(False)

    # --- pantalla completa -------------------------------------------------

    def _toggle_fullscreen(self) -> None:
        if self._fullscreen:
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self) -> None:
        if self._fullscreen:
            return
        self._fullscreen = True
        self.device_tree.setVisible(False)
        window = self.window()
        window.showFullScreen()

    def _exit_fullscreen(self) -> None:
        if not self._fullscreen:
            return
        self._fullscreen = False
        self.device_tree.setVisible(True)
        window = self.window()
        window.showNormal()
