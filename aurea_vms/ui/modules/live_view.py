"""Vista en Vivo: arbol de camaras (izquierda) + area principal con dos
modos:

- Vista Normal: grilla de video con layout seleccionable (1x1..4x4).
- Vista Inteligente: una camara enfocada + un panel por cada analizador
  que este HABILITADO para esa camara (Movimiento, Conteo de Personas,
  Cruce de Linea, Detecciones Faciales) -- si no hay ninguno habilitado,
  muestra un aviso invitando a configurarlos en el modulo Analizadores.

Asignar una camara a un tile se hace arrastrandola desde el arbol, o con
doble click en el arbol estando el tile seleccionado (click simple sobre
el tile lo selecciona) -- funciona igual en ambos modos.

En Vista Normal, doble click SOBRE un tile con camara asignada lo expande
a pantalla completa de la grilla y pasa de sub-flujo a flujo principal
(RTSP main, mayor resolucion); doble click de nuevo lo colapsa y vuelve
a sub-flujo.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CaptionLabel, FluentIcon, TogglePushButton, TransparentToolButton

from aurea_vms.models import repository
from aurea_vms.ui import icons
from aurea_vms.ui.widgets.branded_background import BrandedBackground
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
        self._selected_tile: VideoTile | None = None
        self._expanded_tile: VideoTile | None = None
        self._fullscreen = False

        self.tiles: list[VideoTile] = [VideoTile(i, self) for i in range(MAX_TILES)]
        self.smart_tile = VideoTile(-1, self)
        for tile in (*self.tiles, self.smart_tile):
            tile.clicked.connect(self._on_tile_clicked)
        for tile in self.tiles:
            tile.doubleClicked.connect(self._on_tile_double_clicked)
        self.smart_tile.device_assigned.connect(self._on_smart_device_changed)

        self.device_tree = DeviceTreeWidget(self)
        self.device_tree.device_double_clicked.connect(self._assign_to_selected)
        self.device_tree.setMinimumWidth(220)
        self.device_tree.setMaximumWidth(320)

        self.grid_container = QWidget(self)
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(3)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)

        self.motion_panel = MotionPanel(self)
        self.people_count_panel = PeopleCountPanel(self)
        self.line_crossing_panel = LineCrossingPanel(self)
        self.face_gallery = FaceGallery(self)
        smart_container = self._build_smart_container()

        self.view_stack = QStackedWidget(self)
        self.view_stack.addWidget(self.grid_container)
        self.view_stack.addWidget(smart_container)

        mode_row = self._build_mode_toggle()
        toolbar = self._build_toolbar()

        right_side = QWidget(self)
        right_layout = QVBoxLayout(right_side)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(mode_row)
        right_layout.addWidget(self.view_stack, stretch=1)
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

    def _build_smart_container(self) -> QWidget:
        overlay = QColor(8, 11, 18, 190)
        container = BrandedBackground(icons.algorithm_background_pixmap(), overlay, self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        layout.addWidget(self.smart_tile, stretch=1)

        panel_style = "HeaderCardWidget { background-color: rgba(16, 21, 30, 210); }"
        all_panels = (
            self.motion_panel,
            self.people_count_panel,
            self.line_crossing_panel,
            self.face_gallery,
        )
        for panel in all_panels:
            panel.setStyleSheet(panel_style)

        self.no_analyzers_label = CaptionLabel(
            "Esta cámara no tiene analizadores habilitados.\nConfiguralos en el módulo Analizadores.",
            container,
        )
        self.no_analyzers_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_analyzers_label.setWordWrap(True)

        side_panel = QWidget(container)
        side_panel.setMaximumWidth(260)
        side_panel.setStyleSheet("background: transparent;")
        self.side_layout = QVBoxLayout(side_panel)
        self.side_layout.setContentsMargins(0, 0, 0, 0)
        self.side_layout.addWidget(self.no_analyzers_label)
        for panel in all_panels:
            self.side_layout.addWidget(panel)
            panel.setVisible(False)
        self.side_layout.addStretch(1)

        layout.addWidget(side_panel)
        return container

    def _build_mode_toggle(self) -> QHBoxLayout:
        row = QHBoxLayout()

        self.mode_buttons = QButtonGroup(self)
        self.mode_buttons.setExclusive(True)

        normal_button = TogglePushButton("Vista Normal", self)
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
        if mode != MODE_NORMAL and self._expanded_tile is not None:
            self._collapse_tile()
        self.view_stack.setCurrentIndex(mode)
        for button in self.layout_buttons.buttons():
            button.setVisible(mode == MODE_NORMAL)

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
        self.device_tree.reload()
        super().showEvent(event)

    def focus_camera(self, device_id: int) -> None:
        """API publica para otros modulos (ej. boton "Vista rapida" de
        Dispositivos): cambia a Vista Inteligente y enfoca esta camara."""
        self.mode_buttons.button(MODE_SMART).setChecked(True)
        self._set_mode(MODE_SMART)
        self._on_tile_clicked(self.smart_tile)
        self.smart_tile.assign_device(device_id)

    def on_window_closed(self) -> None:
        """Llamado por MainWindow al cerrar la pestaña: libera las camaras
        activas (si no se hace, sus StreamWorker quedarian corriendo
        indefinidamente, sin nadie que los libere)."""
        for tile in (*self.tiles, self.smart_tile):
            tile.release()

    # --- seleccion / asignacion -------------------------------------------------

    def _on_tile_clicked(self, tile: VideoTile) -> None:
        if self._selected_tile is not None:
            self._selected_tile.set_selected(False)
        self._selected_tile = tile
        tile.set_selected(True)

    def _assign_to_selected(self, device_id: int) -> None:
        if self._selected_tile is None:
            default_tile = (
                self.smart_tile if self.view_stack.currentIndex() == MODE_SMART else self.tiles[0]
            )
            self._on_tile_clicked(default_tile)
        self._selected_tile.assign_device(device_id)

    def _on_smart_device_changed(self, device_id: object) -> None:
        panels = {
            "motion_detection": self.motion_panel,
            "people_counting": self.people_count_panel,
            "line_crossing": self.line_crossing_panel,
            "face_detection": self.face_gallery,
        }
        for panel in panels.values():
            panel.set_device(device_id)

        enabled_names: set[str] = set()
        if device_id is not None:
            enabled_names = {
                config.analyzer_name
                for config in repository.list_analytics_configs(device_id)
                if config.enabled
            }

        any_visible = False
        for analyzer_name, panel in panels.items():
            visible = analyzer_name in enabled_names
            panel.setVisible(visible)
            any_visible = any_visible or visible
        self.no_analyzers_label.setVisible(not any_visible)

    # --- expandir/colapsar (doble click) -------------------------------------------------

    def _on_tile_double_clicked(self, tile: VideoTile) -> None:
        if self.view_stack.currentIndex() != MODE_NORMAL:
            return
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
