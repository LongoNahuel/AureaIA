"""Tira de "Rostros recientes" del dashboard de la Vista Inteligente.

A diferencia de la galeria del panel lateral (FaceGallery), que muestra una
sola camara y solo despues de seleccionar su recuadro y abrir la pestaña de
rostros, esta tira esta siempre visible y junta las capturas de TODAS las
camaras con Deteccion Facial: el operador ve quien paso sin tener que
buscarlo.

Una miniatura por captura (la mejor toma de cada paso de una cara, del
registro compartido ui/face_registry.py), la mas reciente a la izquierda. Clic en una captura emite `face_selected(device_id)` para llevar
a esa camara; doble clic abre el visor forense.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
)
from qfluentwidgets import CaptionLabel, FluentIcon, TransparentToolButton

from aurea_vms.core.events import DetectionEvent
from aurea_vms.ui.face_registry import face_registry
from aurea_vms.ui.widgets.analytics_visuals import TEXT_MUTED, TEXT_PRIMARY, TEXT_SECONDARY
from aurea_vms.ui.widgets.face_forensics import open_forensics
from aurea_vms.ui.widgets.face_visuals import quality_label, record_pixmap

STRIP_THUMB = QSize(72, 72)
MAX_STRIP_ITEMS = 40


class FaceStrip(QFrame):
    face_selected = Signal(int)  # device_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("faceStrip")
        self.setStyleSheet(
            "#faceStrip { background: rgba(8, 20, 38, 205);"
            " border: 1px solid rgba(86, 171, 224, 70); border-radius: 10px; }"
        )
        face_registry.connect_bus()

        header = QHBoxLayout()
        title = QLabel("Rostros recientes", self)
        title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-weight: 600; font-size: 13px; background: transparent;"
        )
        header.addWidget(title)
        header.addStretch(1)
        self.count_label = CaptionLabel("", self)
        self.count_label.setStyleSheet(f"color: {TEXT_SECONDARY}; background: transparent;")
        header.addWidget(self.count_label)
        self.clear_button = TransparentToolButton(FluentIcon.BROOM, self)
        self.clear_button.setToolTip("Limpiar los últimos rostros y volver a mostrar los nuevos")
        self.clear_button.clicked.connect(face_registry.clear)
        header.addWidget(self.clear_button)

        self.empty_label = CaptionLabel(
            "Sin rostros todavía — aparecen acá apenas una cámara con Detección Facial "
            "capta una cara nítida.",
            self,
        )
        self.empty_label.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent;")

        self.list_widget = QListWidget(self)
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setFlow(QListWidget.Flow.LeftToRight)
        self.list_widget.setWrapping(False)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setIconSize(STRIP_THUMB)
        self.list_widget.setGridSize(QSize(STRIP_THUMB.width() + 22, STRIP_THUMB.height() + 36))
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setFixedHeight(STRIP_THUMB.height() + 48)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; color: {TEXT_SECONDARY};"
            " font-size: 11px; outline: none; }"
            "QListWidget::item { border-radius: 10px; padding: 2px; }"
            "QListWidget::item:hover { background: rgba(255,255,255,0.06); }"
            "QScrollBar:horizontal { height: 5px; background: transparent; }"
            "QScrollBar::handle:horizontal { background: rgba(255,255,255,0.16);"
            " border-radius: 2px; min-width: 24px; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal"
            " { background: transparent; }"
        )
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)

        # Alto fijo: la tira nunca le quita espacio a la grilla de video.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 6)
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.list_widget)
        face_registry.captures_changed.connect(self._rebuild)
        self._rebuild()

    # --- datos ------------------------------------------------------------------

    def _on_detection(self, event: DetectionEvent) -> None:
        """Entrada directa (tests): procesa en el registro compartido. En la
        app el registro escucha el bus y esta vista solo se redibuja."""
        face_registry.process(event)

    def _rebuild(self, _device_id: int | None = None) -> None:
        """Las capturas de todas las camaras, la mas reciente a la izquierda."""
        ordered = sorted(
            (
                record
                for device_id in face_registry.all_devices()
                for record in face_registry.records(device_id)
            ),
            key=lambda record: record.timestamp,
            reverse=True,
        )
        self.list_widget.clear()
        for record in ordered[:MAX_STRIP_ITEMS]:
            camera = face_registry.camera_name(record.device_id)
            item = QListWidgetItem(
                record_pixmap(record, STRIP_THUMB, 10), f"{record.when[:5]} · {camera[:10]}"
            )
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setToolTip(
                f"{camera} · {record.when}\n{quality_label(record.quality)}\n"
                "Clic: ir a la cámara · Doble clic: análisis forense"
            )
            self.list_widget.addItem(item)
        self._refresh_state()

    def _refresh_state(self, _device_id: int | None = None) -> None:
        count = self.list_widget.count()
        self.list_widget.setVisible(count > 0)
        self.empty_label.setVisible(count == 0)
        total = face_registry.capture_count()
        self.count_label.setText(
            f"{total} {'captura' if total == 1 else 'capturas'}" if total else ""
        )

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        record = item.data(Qt.ItemDataRole.UserRole)
        if record is not None:
            self.face_selected.emit(int(record.device_id))

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        record = item.data(Qt.ItemDataRole.UserRole)
        if record is not None:
            open_forensics(record, self)
