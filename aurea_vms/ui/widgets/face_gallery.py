"""Panel de la Vista Inteligente: rostros detectados en la camara enfocada.

Diseño: dos cifras (capturas y caras en cuadro), la captura seleccionada en
grande con su ficha (hora, calidad) y una grilla compacta del resto. Sin
sellos quemados sobre la foto: la metadata va en la ficha y en el tooltip,
la imagen queda limpia.

Cada captura es la mejor toma de un paso de una cara por la camara, solo
si el rostro se ve bien (ver core/analytics/face_quality.py). No hay
identidad ni conteo de personas unicas. Es efimera (solo en memoria durante
la sesion): para evidencia persistida estan los snapshots de alarma.

Este widget es un espejo de ui/face_registry.py, compartido con la tira del
dashboard y el visor forense.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import FluentIcon, TransparentToolButton

from aurea_vms.core.events import DetectionEvent
from aurea_vms.ui.face_registry import FaceRecord, face_registry
from aurea_vms.ui.widgets.analytics_panel_base import AnalyticsPanelBase, big_number, caption
from aurea_vms.ui.widgets.analytics_visuals import (
    GRID_LINE,
    SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    format_elapsed,
)
from aurea_vms.ui.widgets.face_forensics import open_forensics
from aurea_vms.ui.widgets.face_visuals import (  # noqa: F401 - reexportados
    _bgr_to_pixmap,
    _cover_scaled,
    _rounded,
    quality_label,
    record_pixmap,
)

THUMB_SIZE = QSize(66, 66)
HERO_SIZE = QSize(104, 104)
THUMB_RADIUS = 10
# Datos de cada miniatura guardados en el item de la lista.
CAPTURE_ROLE = Qt.ItemDataRole.UserRole + 1


class FaceGallery(AnalyticsPanelBase):
    analyzer_name = "face_detection"
    panel_title = "Rostros"
    fills_height = True

    def build(self, content: QWidget) -> None:
        face_registry.connect_bus()
        self._pinned_id: int | None = None

        # --- cifras -------------------------------------------------------
        stats = QHBoxLayout()
        stats.setSpacing(18)
        unique_column = QVBoxLayout()
        unique_column.setSpacing(0)
        self.counter_label = big_number(content, 30)
        self.counter_label.setText("0")
        unique_column.addWidget(self.counter_label)
        unique_column.addWidget(caption("capturas", content))
        stats.addLayout(unique_column)

        in_frame_column = QVBoxLayout()
        in_frame_column.setSpacing(0)
        self.in_frame_label = big_number(content, 30)
        in_frame_column.addWidget(self.in_frame_label)
        in_frame_column.addWidget(caption("en cuadro", content))
        stats.addLayout(in_frame_column)
        stats.addStretch(1)

        clear_button = TransparentToolButton(FluentIcon.BROOM, content)
        clear_button.setToolTip("Vaciar las capturas de esta cámara")
        clear_button.clicked.connect(self._clear_captures)
        stats.addWidget(clear_button, alignment=Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(stats)

        # --- ficha de la captura destacada --------------------------------
        self.hero = QWidget(content)
        self.hero.setObjectName("faceHero")
        self.hero.setStyleSheet(
            f"#faceHero {{ background: {SURFACE}; border: 1px solid {GRID_LINE};"
            " border-radius: 12px; }"
        )
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(8, 8, 10, 8)
        hero_layout.setSpacing(12)
        self.hero.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hero.mouseReleaseEvent = lambda _event: self._open_hero_forensics()
        self.hero_image = QLabel(self.hero)
        self.hero_image.setFixedSize(HERO_SIZE)
        self.hero_image.setStyleSheet("background: transparent;")
        hero_layout.addWidget(self.hero_image)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.hero_title = QLabel("", self.hero)
        self.hero_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 17px; font-weight: 700; background: transparent;"
        )
        self.hero_time = caption("", self.hero)
        self.hero_quality = caption("", self.hero)
        self.hero_seen = caption("Clic para ampliar", self.hero, TEXT_MUTED)
        for label in (self.hero_title, self.hero_time, self.hero_quality, self.hero_seen):
            info.addWidget(label)
        info.addStretch(1)
        hero_layout.addLayout(info, stretch=1)
        self.body.addWidget(self.hero)

        self.empty_label = caption(
            "Todavía no hay capturas.\nSe guarda la mejor toma de cada rostro que se vea bien.",
            content,
            TEXT_MUTED,
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(self.empty_label)

        # --- grilla ---------------------------------------------------------
        # QListWidget liso, NO el ListWidget de qfluentwidgets: ese trae una
        # hoja de estilo propia que fija "height: 35px" en cada item (pensada
        # para filas de menu compactas, no para una grilla de fotos), y
        # pisaba el iconSize sin importar que tan grande se pidiera.
        self.list_widget = QListWidget(content)
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setIconSize(THUMB_SIZE)
        self.list_widget.setGridSize(QSize(THUMB_SIZE.width() + 8, THUMB_SIZE.height() + 24))
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background: transparent; border: none; color: {TEXT_SECONDARY};"
            " font-size: 11px; outline: none; }"
            "QListWidget::item { border: none; border-radius: 10px; padding: 2px; }"
            "QListWidget::item:hover { background: rgba(255,255,255,0.05); }"
            "QListWidget::item:selected { background: rgba(255,255,255,0.10);"
            f" color: {TEXT_PRIMARY}; }}"
            "QScrollBar:vertical { width: 6px; background: transparent; margin: 0; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,0.16);"
            " border-radius: 3px; min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background: transparent; }"
        )
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.body.addWidget(self.list_widget, stretch=1)

        face_registry.captures_changed.connect(self._on_captures_changed)

    # --- ciclo de vida -------------------------------------------------------

    def reset(self) -> None:
        self._pinned_id = None
        self.in_frame_label.setText("—")
        self._rebuild()

    def _clear_captures(self) -> None:
        if self._device_id is not None:
            face_registry.clear(self._device_id)

    def _refresh_counter(self) -> None:
        count = face_registry.capture_count(self._device_id) if self._device_id is not None else 0
        self.counter_label.setText(str(count))

    def _on_captures_changed(self, device_id: int) -> None:
        if device_id == self._device_id:
            self._rebuild()

    # --- eventos ---------------------------------------------------------------

    def _on_detection(self, event: DetectionEvent) -> None:
        """Entrada directa (tests, integraciones): procesa el evento en el
        registro y actualiza el panel. En la app el registro ya escucha el
        bus por su cuenta."""
        face_registry.process(event)
        self._on_detection_event(event)

    def on_event(self, event: DetectionEvent) -> None:
        self.in_frame_label.setText(str(event.metrics.get("caras", len(event.detections))))

    # --- grilla --------------------------------------------------------------------

    def _rebuild(self) -> None:
        """Espejo de las capturas del registro para esta camara (la mas
        reciente primero)."""
        self.list_widget.clear()
        for record in face_registry.records(self._device_id):
            self.list_widget.addItem(self._make_item(record))
        self._refresh_counter()
        self._refresh_hero()

    @staticmethod
    def _make_item(record: FaceRecord) -> QListWidgetItem:
        item = QListWidgetItem(QIcon(record_pixmap(record, THUMB_SIZE, THUMB_RADIUS)), "")
        item.setText(record.when)
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
        item.setData(CAPTURE_ROLE, record)
        item.setToolTip(
            f"{record.when} · {quality_label(record.quality)}"
            f" · detección {record.confidence:.0%}\nDoble clic: análisis forense"
        )
        return item

    # --- ficha destacada ---------------------------------------------------------

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        record = item.data(CAPTURE_ROLE)
        capture_id = record.capture_id if record is not None else None
        # Segundo clic sobre la misma: vuelve a seguir la mas reciente.
        self._pinned_id = None if capture_id == self._pinned_id else capture_id
        if self._pinned_id is None:
            self.list_widget.clearSelection()
        self._refresh_hero()

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        record = item.data(CAPTURE_ROLE)
        if record is not None:
            open_forensics(record, self)

    def _open_hero_forensics(self) -> None:
        record = self._hero_record()
        if record is not None:
            open_forensics(record, self)

    def _hero_record(self) -> FaceRecord | None:
        records = face_registry.records(self._device_id)
        for record in records:
            if self._pinned_id is None or record.capture_id == self._pinned_id:
                return record
        self._pinned_id = None
        return records[0] if records else None

    def _refresh_hero(self) -> None:
        record = self._hero_record()
        has_any = record is not None
        self.hero.setVisible(has_any)
        self.empty_label.setVisible(not has_any and self._device_id is not None)
        if not has_any:
            return
        self.hero_image.setPixmap(record_pixmap(record, HERO_SIZE, THUMB_RADIUS + 2))
        self.hero_title.setText("Fijada" if self._pinned_id is not None else "Última captura")
        self._refresh_hero_time(record, time.time())
        self.hero_quality.setText(quality_label(record.quality))

    def _refresh_hero_time(self, record: FaceRecord, now: float) -> None:
        self.hero_time.setText(f"{record.when} · hace {format_elapsed(now - record.timestamp)}")

    def on_tick(self, now: float) -> None:
        if self.hero.isVisible():
            record = self._hero_record()
            if record is not None:
                self._refresh_hero_time(record, now)
