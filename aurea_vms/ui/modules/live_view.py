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
completa de la grilla y pasa de sub-flujo a flujo principal (RTSP main,
mayor resolucion); doble click de nuevo lo colapsa y vuelve a sub-flujo.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    SegmentedWidget,
    TogglePushButton,
    TransparentToolButton,
)

from aurea_vms.core import app_state
from aurea_vms.core.event_bus import event_bus
from aurea_vms.models import repository
from aurea_vms.ui import icons
from aurea_vms.ui.widgets.device_tree import DeviceTreeWidget
from aurea_vms.ui.widgets.face_gallery import FaceGallery
from aurea_vms.ui.widgets.line_crossing_panel import LineCrossingPanel
from aurea_vms.ui.widgets.motion_panel import MotionPanel
from aurea_vms.ui.widgets.people_count_panel import PeopleCountPanel
from aurea_vms.ui.widgets.video_tile import VideoTile

GRID_LAYOUTS = [(1, 1), (2, 2), (3, 3), (4, 4)]
MAX_TILES = GRID_LAYOUTS[-1][0] * GRID_LAYOUTS[-1][1]
DEFAULT_LAYOUT_INDEX = 1  # 2x2

MODE_NORMAL = 0
MODE_SMART = 1


class LiveViewModule(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = MODE_NORMAL
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

        self.grid_container = QWidget(self)
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(3)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)

        self.motion_panel = MotionPanel(self)
        self.people_count_panel = PeopleCountPanel(self)
        self.line_crossing_panel = LineCrossingPanel(self)
        self.face_gallery = FaceGallery(self)
        self.side_panel = self._build_side_panel()

        mode_row = self._build_mode_toggle()
        toolbar = self._build_toolbar()

        content_row = QHBoxLayout()
        content_row.setSpacing(10)
        content_row.addWidget(self.grid_container, stretch=1)
        content_row.addWidget(self.side_panel)

        right_side = QWidget(self)
        right_layout = QVBoxLayout(right_side)
        right_layout.setContentsMargins(0, 0, 0, 0)
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

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._exit_fullscreen)

        self._apply_grid(*GRID_LAYOUTS[DEFAULT_LAYOUT_INDEX])
        self.layout_buttons.buttons()[DEFAULT_LAYOUT_INDEX].setChecked(True)
        self.side_panel.setVisible(False)
        self._refresh_side_panel()

    def _build_side_panel(self) -> QWidget:
        panel_style = "HeaderCardWidget { background-color: rgba(16, 21, 30, 210); }"
        # Orden fijo de submenu: mismo orden en el que aparecen las pestañas del
        # pivot sea cual sea el orden en que la DB devuelva las configs.
        self._analyzer_panels: dict[str, tuple[str, QWidget]] = {
            "motion_detection": ("Movimiento", self.motion_panel),
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
        row = QHBoxLayout()

        self.mode_buttons = QButtonGroup(self)
        self.mode_buttons.setExclusive(True)

        normal_button = TogglePushButton("Vista en Vivo", self)
        normal_button.setChecked(True)
        normal_button.clicked.connect(lambda: self._set_mode(MODE_NORMAL))

        smart_button = TogglePushButton("Vista Inteligente", self)
        smart_button.clicked.connect(lambda: self._set_mode(MODE_SMART))

        self.mode_buttons.addButton(normal_button, MODE_NORMAL)
        self.mode_buttons.addButton(smart_button, MODE_SMART)

        row.addWidget(normal_button)
        row.addWidget(smart_button)
        row.addStretch(1)
        return row

    def _set_mode(self, mode: int) -> None:
        self._mode = mode
        self.side_panel.setVisible(mode == MODE_SMART)

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

    def focus_camera(self, device_id: int) -> None:
        """API publica para otros modulos (ej. boton "Vista rapida" de
        Dispositivos): cambia a Vista Inteligente, selecciona el primer
        tile de la grilla y le asigna esta camara."""
        self.mode_buttons.button(MODE_SMART).setChecked(True)
        self._set_mode(MODE_SMART)
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

        if self._expanded_tile is not None:
            self._expanded_tile.set_stream_kind("sub")

        self._expanded_tile = tile
        tile.set_stream_kind("main")
        self._show_only(tile)
        self._set_layout_buttons_enabled(False)

    def _collapse_tile(self) -> None:
        if self._expanded_tile is None:
            return
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
