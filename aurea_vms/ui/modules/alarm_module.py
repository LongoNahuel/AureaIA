"""Alarmas: el historico de incidentes, por canal, con el detalle de cada uno.

Cada alarma es un incidente con estado (nueva, reconocida, en investigacion,
resuelta), notas de investigacion, y su evidencia: la captura del momento y
el clip del evento (unos segundos antes y despues), que se reproduce aca
mismo. Se puede exportar (captura + clip + resumen en texto).

- Filtros por canal (con cuantos incidentes tiene cada camara), por tipo de
  incidente y por estado. El historico completo, de a PAGE_SIZE ("Cargar
  mas"); antes se veian solo los ultimos 200.
- El detalle muestra un incidente puntual: para presentar la evidencia sin
  el resto de la tabla alrededor.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TableWidget,
)

from aurea_vms.core import app_prefs, app_state, media_store
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import AlarmEvent as AlarmEventDTO
from aurea_vms.core.events import ClipReadyEvent
from aurea_vms.core.permissions import Perm, can
from aurea_vms.models import repository
from aurea_vms.models.alarm_event import (
    STATUS_ACKNOWLEDGED,
    STATUS_INVESTIGATING,
    STATUS_NEW,
    STATUS_RESOLVED,
)
from aurea_vms.models.alarm_event import AlarmEvent as AlarmEventRow
from aurea_vms.models.media_asset import KIND_CLIP, KIND_SNAPSHOT
from aurea_vms.ui.labels import display_class
from aurea_vms.ui.notify import notify, warn
from aurea_vms.ui.theme import severity_soft_qcolor, severity_text_qcolor

COLUMNS = ["Fecha y hora", "Canal", "Incidente", "Severidad", "Estado", "Captura", "Clip"]
THUMBNAIL_SIZE = QSize(72, 40)
PAGE_SIZE = 100
PREVIEW_SIZE = QSize(640, 400)

STATUS_LABELS = {
    STATUS_NEW: "Nueva",
    STATUS_ACKNOWLEDGED: "Reconocida",
    STATUS_INVESTIGATING: "En investigación",
    STATUS_RESOLVED: "Resuelta",
}
SEVERITY_LABELS = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "info": "Info"}


def _when(timestamp: float, with_year: bool = False) -> str:
    fmt = "%d/%m/%Y %H:%M:%S" if with_year else "%d/%m %H:%M:%S"
    return dt.datetime.fromtimestamp(timestamp).strftime(fmt)


class AlarmModule(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._events: list[AlarmEventRow] = []
        self._media_by_event: dict = {}
        self._device_names: dict[int, str] = {}
        self._has_more = False
        self._player = None  # QMediaPlayer, recien cuando se mira un clip
        self._clip_shown_for: int | None = None

        # --- filtros --------------------------------------------------------
        self.channel_combo = ComboBox(self)
        self.channel_combo.setMinimumWidth(260)
        self.class_combo = ComboBox(self)
        self.class_combo.setMinimumWidth(200)
        self.status_combo = ComboBox(self)
        self.status_combo.addItem("Todos los estados", userData=None)
        for status, label in STATUS_LABELS.items():
            self.status_combo.addItem(label, userData=status)
        refresh_button = PushButton(FluentIcon.SYNC, "Actualizar")
        refresh_button.clicked.connect(self._reload)

        filters = QHBoxLayout()
        filters.addWidget(BodyLabel("Canal:"))
        filters.addWidget(self.channel_combo)
        filters.addSpacing(8)
        filters.addWidget(BodyLabel("Incidente:"))
        filters.addWidget(self.class_combo)
        filters.addSpacing(8)
        filters.addWidget(BodyLabel("Estado:"))
        filters.addWidget(self.status_combo)
        filters.addStretch(1)
        filters.addWidget(refresh_button)

        # --- tabla ------------------------------------------------------------
        self.table = TableWidget(self)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        header = self.table.horizontalHeader()
        # Canal e incidente se llevan el espacio; el resto, lo que ocupa
        # (con todo en Stretch los nombres de canal salian cortados).
        for column in range(len(COLUMNS)):
            stretch = COLUMNS[column] in ("Canal", "Incidente")
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Stretch
                if stretch
                else QHeaderView.ResizeMode.ResizeToContents,
            )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(6)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        self.count_label = CaptionLabel("", self)
        self.more_button = PushButton(FluentIcon.DOWN, "Cargar más")
        self.more_button.clicked.connect(self._load_more)
        table_footer = QHBoxLayout()
        table_footer.addWidget(self.count_label, stretch=1)
        table_footer.addWidget(self.more_button)

        table_side = QWidget(self)
        table_layout = QVBoxLayout(table_side)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.table, stretch=1)
        table_layout.addLayout(table_footer)

        # --- detalle de un incidente -----------------------------------------
        detail = QWidget(self)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(8, 0, 0, 0)
        self.detail_title = StrongBodyLabel("Elegí un incidente", detail)
        self.detail_info = BodyLabel("", detail)
        self.detail_info.setWordWrap(True)
        self.detail_info.setTextFormat(Qt.TextFormat.RichText)

        self.media_stack = QStackedWidget(detail)
        self.media_stack.setMinimumSize(QSize(360, 225))
        self.snapshot_view = QLabel("Sin captura", detail)
        self.snapshot_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.snapshot_view.setStyleSheet("background: #0b1017; color: #8b949e; border-radius: 6px;")
        self.media_stack.addWidget(self.snapshot_view)
        self.video_view = None  # QVideoWidget, recien cuando se mira un clip

        self.snapshot_button = PushButton(FluentIcon.PHOTO, "Ver captura")
        self.snapshot_button.clicked.connect(self._show_snapshot)
        play_button = PrimaryPushButton(FluentIcon.PLAY, "Reproducir clip")
        play_button.clicked.connect(self._on_play_clip)
        self.play_button = play_button
        self.pause_button = PushButton(FluentIcon.PAUSE, "Pausa")
        self.pause_button.clicked.connect(self._toggle_pause)
        self.external_button = PushButton(FluentIcon.LINK, "Abrir afuera")
        self.external_button.setToolTip("Abre el clip con el reproductor del sistema")
        self.external_button.clicked.connect(self._open_external)
        self.position_slider = QSlider(Qt.Orientation.Horizontal, detail)
        self.position_slider.setEnabled(False)
        self.position_slider.sliderMoved.connect(self._seek)
        self.clip_status = CaptionLabel("", detail)

        media_buttons = QHBoxLayout()
        media_buttons.addWidget(self.snapshot_button)
        media_buttons.addWidget(play_button)
        media_buttons.addWidget(self.pause_button)
        media_buttons.addStretch(1)
        media_buttons.addWidget(self.external_button)

        ack_button = PushButton(FluentIcon.ACCEPT, "Reconocer")
        ack_button.clicked.connect(lambda: self._set_status(STATUS_ACKNOWLEDGED))
        investigate_button = PushButton(FluentIcon.SEARCH, "En investigación")
        investigate_button.clicked.connect(lambda: self._set_status(STATUS_INVESTIGATING))
        resolve_button = PushButton(FluentIcon.COMPLETED, "Resolver")
        resolve_button.clicked.connect(lambda: self._set_status(STATUS_RESOLVED))
        export_button = PushButton(FluentIcon.SHARE, "Exportar evidencia")
        export_button.clicked.connect(self._on_export)

        # Gates por permiso: el Auditor entra al modulo (RECORDINGS) y
        # exporta evidencia, pero no gestiona el ciclo de vida de la alerta.
        self._can_manage = can(Perm.ALARM_MANAGE)
        for gestion in (ack_button, investigate_button, resolve_button):
            gestion.setEnabled(self._can_manage)
        export_button.setEnabled(can(Perm.EVIDENCE_EXPORT))

        lifecycle = QHBoxLayout()
        lifecycle.addWidget(ack_button)
        lifecycle.addWidget(investigate_button)
        lifecycle.addWidget(resolve_button)
        lifecycle.addStretch(1)
        lifecycle.addWidget(export_button)

        self.notes_edit = QPlainTextEdit(detail)
        self.notes_edit.setMaximumHeight(80)
        self.notes_edit.setEnabled(False)
        self.save_notes_button = PushButton(FluentIcon.SAVE, "Guardar notas")
        self.save_notes_button.setEnabled(False)
        self.save_notes_button.clicked.connect(self._on_save_notes)
        notes_buttons = QHBoxLayout()
        notes_buttons.addStretch(1)
        notes_buttons.addWidget(self.save_notes_button)

        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.media_stack, stretch=1)
        detail_layout.addLayout(media_buttons)
        detail_layout.addWidget(self.position_slider)
        detail_layout.addWidget(self.clip_status)
        detail_layout.addWidget(self.detail_info)
        detail_layout.addLayout(lifecycle)
        detail_layout.addWidget(CaptionLabel("Notas de investigación:", detail))
        detail_layout.addWidget(self.notes_edit)
        detail_layout.addLayout(notes_buttons)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(table_side)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(splitter, stretch=1)

        event_bus.alarm.connect(self._on_alarm, Qt.ConnectionType.QueuedConnection)
        event_bus.clip_ready.connect(self._on_clip_ready, Qt.ConnectionType.QueuedConnection)
        event_bus.site_filter_changed.connect(self._on_site_filter_changed)

        self._fill_filters()
        self.channel_combo.currentIndexChanged.connect(self._reload)
        self.class_combo.currentIndexChanged.connect(self._reload)
        self.status_combo.currentIndexChanged.connect(self._reload)
        self._reload()

    # --- filtros y carga ------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._fill_filters()
        self._reload()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - override de Qt
        if self._player is not None:
            self._player.pause()
        super().hideEvent(event)

    def _on_site_filter_changed(self, _site_id: object) -> None:
        self._fill_filters()
        self._reload()

    def _fill_filters(self) -> None:
        """Canales con cuantos incidentes tiene cada uno, y los tipos de
        incidente del historico. Conserva lo que estaba elegido."""
        channel = self.channel_combo.currentData() if self.channel_combo.count() else None
        kind = self.class_combo.currentData() if self.class_combo.count() else None
        devices = repository.list_devices(site_id=app_state.current_site_id)
        self._device_names = {d.id: d.name for d in repository.list_devices()}
        counts = repository.count_alarm_events_by_device(site_id=app_state.current_site_id)
        for combo in (self.channel_combo, self.class_combo):
            combo.blockSignals(True)
            combo.clear()
        self.channel_combo.addItem(f"Todos los canales ({sum(counts.values())})", userData=None)
        for device in sorted(devices, key=lambda d: (-counts.get(d.id, 0), d.name)):
            self.channel_combo.addItem(
                f"{device.name} ({counts.get(device.id, 0)})", userData=device.id
            )
        self.class_combo.addItem("Todos los incidentes", userData=None)
        for label in repository.list_alarm_event_classes():
            self.class_combo.addItem(display_class(label), userData=label)
        for combo, value in ((self.channel_combo, channel), (self.class_combo, kind)):
            index = combo.findData(value) if value is not None else 0
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)

    def _filters(self) -> dict:
        return {
            "device_id": self.channel_combo.currentData(),
            "object_class": self.class_combo.currentData(),
            "status": self.status_combo.currentData(),
            "site_id": app_state.current_site_id,
        }

    def _reload(self) -> None:
        selected = self._selected_event()
        self._events = []
        self._media_by_event = {}
        self.table.setRowCount(0)
        self._load_more()
        if selected is not None:
            self.select_event(selected.id)
        self._on_selection_changed()

    def _load_more(self) -> None:
        page = repository.list_alarm_events(
            limit=PAGE_SIZE, offset=len(self._events), **self._filters()
        )
        # La media de la pagina sale en una sola consulta indexada
        # (media_assets.alarm_event_id): nada de una query por fila ni de
        # tocar el filesystem para saber si hay clip/captura.
        self._media_by_event.update(repository.list_media_for_events([e.id for e in page]))
        start = len(self._events)
        self._events.extend(page)
        self.table.setRowCount(len(self._events))
        for row, alarm_event in enumerate(page, start=start):
            self._set_row(row, alarm_event)
        filters = self._filters()
        total = repository.count_alarm_events(
            device_id=filters["device_id"],
            site_id=filters["site_id"],
            status=filters["status"],
            object_class=filters["object_class"],
        )
        # Contra el total: "la pagina vino llena" dejaba "Cargar mas" activo
        # cuando el historico terminaba justo en el borde de una pagina.
        shown = len(self._events)
        self._has_more = shown < total
        self.more_button.setEnabled(self._has_more)
        self.count_label.setText(
            f"{shown} de {total} incidentes" if self._has_more else f"{shown} incidentes"
        )

    def select_event(self, alarm_event_id: int) -> bool:
        """Selecciona un incidente de la tabla (para mostrarlo en el detalle)."""
        for row, alarm_event in enumerate(self._events):
            if alarm_event.id == alarm_event_id:
                self.table.selectRow(row)
                return True
        return False

    def _media_path(self, alarm_event_id: int, kind: str) -> str | None:
        for asset in self._media_by_event.get(alarm_event_id, []):
            if asset.kind == kind:
                return str(media_store.absolute_path(asset.rel_path))
        return None

    def _set_row(self, row: int, alarm_event: AlarmEventRow) -> None:
        device_name = self._device_names.get(alarm_event.device_id, f"#{alarm_event.device_id}")
        self.table.setItem(row, 0, QTableWidgetItem(_when(alarm_event.timestamp)))
        # Con el panel de detalle al lado los nombres largos se cortan: el
        # completo queda en el tooltip.
        for column, text in ((1, device_name), (2, display_class(alarm_event.object_class))):
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            self.table.setItem(row, column, item)

        severity_item = QTableWidgetItem(
            SEVERITY_LABELS.get(alarm_event.severity, alarm_event.severity)
        )
        # Chip discreto (texto en el color de severidad + fondo soft 12%,
        # tokens NOVA) en vez del bloque de color pleno que dominaba la
        # tabla entera cuando habia muchas filas de la misma severidad.
        dark = app_prefs.get_theme() == "dark"
        severity_item.setForeground(severity_text_qcolor(alarm_event.severity, dark))
        severity_item.setBackground(severity_soft_qcolor(alarm_event.severity))
        self.table.setItem(row, 3, severity_item)
        self.table.setItem(
            row, 4, QTableWidgetItem(STATUS_LABELS.get(alarm_event.status, alarm_event.status))
        )

        thumb_label = QLabel()
        snapshot_path = self._media_path(alarm_event.id, KIND_SNAPSHOT)
        if snapshot_path:
            pixmap = QPixmap(snapshot_path)
            if not pixmap.isNull():
                thumb_label.setPixmap(
                    pixmap.scaled(
                        THUMBNAIL_SIZE,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        self.table.setCellWidget(row, 5, thumb_label)
        has_clip = self._media_path(alarm_event.id, KIND_CLIP) is not None
        self.table.setItem(row, 6, QTableWidgetItem("Sí" if has_clip else "—"))

    # --- detalle --------------------------------------------------------------

    def _selected_event(self) -> AlarmEventRow | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._events):
            return None
        return self._events[rows[0].row()]

    def _on_selection_changed(self) -> None:
        alarm_event = self._selected_event()
        has_selection = alarm_event is not None
        self.notes_edit.setEnabled(has_selection and self._can_manage)
        self.save_notes_button.setEnabled(has_selection and self._can_manage)
        self.notes_edit.setPlainText(alarm_event.notes if alarm_event else "")
        self._show_detail(alarm_event)

    def _show_detail(self, alarm_event: AlarmEventRow | None) -> None:
        if self._player is not None:
            self._player.stop()
        self.position_slider.setEnabled(False)
        if alarm_event is None:
            self.detail_title.setText("Elegí un incidente")
            self.detail_info.setText("")
            self.snapshot_view.setPixmap(QPixmap())
            self.snapshot_view.setText("Sin captura")
            self.media_stack.setCurrentWidget(self.snapshot_view)
            self.clip_status.setText("")
            for button in (self.snapshot_button, self.play_button, self.pause_button):
                button.setEnabled(False)
            self.external_button.setEnabled(False)
            return
        device_name = self._device_names.get(alarm_event.device_id, f"#{alarm_event.device_id}")
        self.detail_title.setText(f"Incidente #{alarm_event.id} · {device_name}")
        rows = [
            ("Incidente", display_class(alarm_event.object_class)),
            ("Fecha y hora", _when(alarm_event.timestamp, with_year=True)),
            ("Severidad", SEVERITY_LABELS.get(alarm_event.severity, alarm_event.severity)),
            ("Confianza", f"{alarm_event.confidence:.0%}"),
            ("Estado", STATUS_LABELS.get(alarm_event.status, alarm_event.status)),
        ]
        self.detail_info.setText(
            "<br>".join(f"<span style='color:#8b949e'>{k}:</span> {v}" for k, v in rows)
        )
        snapshot_path = self._media_path(alarm_event.id, KIND_SNAPSHOT)
        pixmap = QPixmap(snapshot_path) if snapshot_path else QPixmap()
        if pixmap.isNull():
            self.snapshot_view.setPixmap(QPixmap())
            self.snapshot_view.setText("Sin captura")
        else:
            self.snapshot_view.setPixmap(
                pixmap.scaled(
                    PREVIEW_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.media_stack.setCurrentWidget(self.snapshot_view)
        has_clip = self._media_path(alarm_event.id, KIND_CLIP) is not None
        self.snapshot_button.setEnabled(not pixmap.isNull())
        self.play_button.setEnabled(has_clip)
        self.pause_button.setEnabled(False)
        self.external_button.setEnabled(has_clip)
        self.clip_status.setText(
            "Clip del evento: unos segundos antes y después del incidente."
            if has_clip
            else "El clip todavía no está (se graba unos segundos después del incidente)."
        )

    def _show_snapshot(self) -> None:
        if self._player is not None:
            self._player.pause()
        self.pause_button.setEnabled(False)
        self.media_stack.setCurrentWidget(self.snapshot_view)

    def _ensure_player(self) -> None:
        if self._player is not None:
            return
        # QtMultimedia recien cuando se mira un clip: arrancar su backend
        # (FFmpeg) al abrir el modulo no hace falta y en CI sin audio avisa.
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self.video_view = QVideoWidget(self)
        self.media_stack.addWidget(self.video_view)
        self._player = QMediaPlayer(self)
        self._player.setVideoOutput(self.video_view)
        self._player.durationChanged.connect(lambda d: self.position_slider.setRange(0, d))
        self._player.positionChanged.connect(self._on_position)

    def _on_position(self, position: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)

    def _seek(self, position: int) -> None:
        if self._player is not None:
            self._player.setPosition(position)

    def _on_play_clip(self) -> None:
        alarm_event = self._selected_event()
        if alarm_event is None:
            warn(self, "Reproducir clip", "Seleccioná una alarma primero.")
            return
        clip_path = self._media_path(alarm_event.id, KIND_CLIP)
        if not clip_path:
            warn(
                self,
                "Reproducir clip",
                "El clip todavía no está listo (o la regla no tiene 'guardar clip' activado).",
            )
            return
        self._ensure_player()
        if self._clip_shown_for != alarm_event.id:
            self._player.setSource(QUrl.fromLocalFile(clip_path))
            self._clip_shown_for = alarm_event.id
        self.media_stack.setCurrentWidget(self.video_view)
        self.position_slider.setEnabled(True)
        self.pause_button.setEnabled(True)
        self._player.play()

    def _toggle_pause(self) -> None:
        if self._player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _open_external(self) -> None:
        alarm_event = self._selected_event()
        clip_path = self._media_path(alarm_event.id, KIND_CLIP) if alarm_event else None
        if clip_path:
            # QDesktopServices es portable (os.startfile era solo-Windows).
            QDesktopServices.openUrl(QUrl.fromLocalFile(clip_path))

    # --- ciclo de vida, notas, exportar ----------------------------------------

    def _set_status(self, status: str) -> None:
        alarm_event = self._selected_event()
        if alarm_event is None:
            warn(self, "Cambiar estado", "Seleccioná un incidente primero.")
            return
        repository.set_alarm_event_status(alarm_event.id, status)
        self._reload()

    def _on_save_notes(self) -> None:
        alarm_event = self._selected_event()
        if alarm_event is None:
            return
        repository.update_alarm_event(alarm_event.id, notes=self.notes_edit.toPlainText())
        notify(self, "Notas", "Notas guardadas.")
        self._reload()

    def _on_export(self) -> None:
        alarm_event = self._selected_event()
        if alarm_event is None:
            warn(self, "Exportar evidencia", "Seleccioná un incidente primero.")
            return

        target_dir = QFileDialog.getExistingDirectory(self, "Elegí dónde exportar la evidencia")
        if not target_dir:
            return

        device = repository.get_device(alarm_event.device_id)
        device_name = device.name if device else f"dispositivo {alarm_event.device_id}"
        when = dt.datetime.fromtimestamp(alarm_event.timestamp)
        folder_name = f"incidente-{alarm_event.id}-{when.strftime('%Y%m%d-%H%M%S')}"
        export_dir = os.path.join(target_dir, folder_name)
        os.makedirs(export_dir, exist_ok=True)

        snapshot_path = self._media_path(alarm_event.id, KIND_SNAPSHOT)
        clip_path = self._media_path(alarm_event.id, KIND_CLIP)
        if snapshot_path and os.path.exists(snapshot_path):
            shutil.copy2(snapshot_path, export_dir)
        if clip_path and os.path.exists(clip_path):
            shutil.copy2(clip_path, export_dir)

        summary_lines = [
            f"Incidente #{alarm_event.id}",
            f"Cámara: {device_name}",
            f"Fecha/hora: {when.strftime('%d/%m/%Y %H:%M:%S')}",
            f"Clase: {display_class(alarm_event.object_class)}",
            f"Confianza: {alarm_event.confidence:.0%}",
            f"Severidad: {SEVERITY_LABELS.get(alarm_event.severity, alarm_event.severity)}",
            f"Estado: {STATUS_LABELS.get(alarm_event.status, alarm_event.status)}",
            "",
            "Notas de investigación:",
            alarm_event.notes or "(sin notas)",
        ]
        with open(os.path.join(export_dir, "resumen.txt"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(summary_lines))

        notify(self, "Exportar evidencia", f"Evidencia exportada a {export_dir}")

    # --- en vivo ----------------------------------------------------------------

    def _on_alarm(self, _event: AlarmEventDTO) -> None:
        self._fill_filters()
        self._reload()

    def _on_clip_ready(self, _event: ClipReadyEvent) -> None:
        self._reload()
