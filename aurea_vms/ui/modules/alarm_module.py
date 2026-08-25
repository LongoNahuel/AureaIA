"""Feed de alarmas -> incidentes: cada alarma tiene un estado (nueva,
reconocida, en investigación, resuelta), notas de investigación libres, y
se puede exportar como evidencia (snapshot + clip + resumen en texto)."""

from __future__ import annotations

import datetime as dt
import os
import shutil

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CaptionLabel, FluentIcon, PrimaryPushButton, PushButton, TableWidget

from aurea_vms.core import media_store
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import AlarmEvent as AlarmEventDTO
from aurea_vms.core.events import ClipReadyEvent
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

COLUMNS = ["Hora", "Cámara", "Clase", "Severidad", "Confianza", "Captura", "Estado"]
THUMBNAIL_SIZE = QSize(72, 40)

STATUS_LABELS = {
    STATUS_NEW: "Nueva",
    STATUS_ACKNOWLEDGED: "Reconocida",
    STATUS_INVESTIGATING: "En investigación",
    STATUS_RESOLVED: "Resuelta",
}
SEVERITY_LABELS = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "info": "Info"}
SEVERITY_COLORS = {"critico": "#e5534b", "alto": "#f0a020", "medio": "#3b82f6", "info": "#6e7681"}


class AlarmModule(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._events: list[AlarmEventRow] = []

        self.table = TableWidget(self)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(6)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        refresh_button = PushButton(FluentIcon.SYNC, "Actualizar")
        refresh_button.clicked.connect(self._reload)
        ack_button = PushButton(FluentIcon.ACCEPT, "Reconocer")
        ack_button.clicked.connect(lambda: self._set_status(STATUS_ACKNOWLEDGED))
        investigate_button = PushButton(FluentIcon.SEARCH, "En investigación")
        investigate_button.clicked.connect(lambda: self._set_status(STATUS_INVESTIGATING))
        resolve_button = PushButton(FluentIcon.COMPLETED, "Resolver")
        resolve_button.clicked.connect(lambda: self._set_status(STATUS_RESOLVED))
        play_button = PrimaryPushButton(FluentIcon.PLAY, "Reproducir clip")
        play_button.clicked.connect(self._on_play_clip)
        export_button = PushButton(FluentIcon.SHARE, "Exportar evidencia")
        export_button.clicked.connect(self._on_export)

        toolbar = QHBoxLayout()
        toolbar.addWidget(refresh_button)
        toolbar.addWidget(ack_button)
        toolbar.addWidget(investigate_button)
        toolbar.addWidget(resolve_button)
        toolbar.addWidget(play_button)
        toolbar.addWidget(export_button)
        toolbar.addStretch(1)

        notes_row = QVBoxLayout()
        notes_row.addWidget(CaptionLabel("Notas de investigación (del incidente seleccionado):"))
        self.notes_edit = QPlainTextEdit(self)
        self.notes_edit.setMaximumHeight(70)
        self.notes_edit.setEnabled(False)
        notes_row.addWidget(self.notes_edit)
        notes_buttons = QHBoxLayout()
        notes_buttons.addStretch(1)
        self.save_notes_button = PushButton(FluentIcon.SAVE, "Guardar notas")
        self.save_notes_button.setEnabled(False)
        self.save_notes_button.clicked.connect(self._on_save_notes)
        notes_buttons.addWidget(self.save_notes_button)
        notes_row.addLayout(notes_buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(notes_row)

        event_bus.alarm.connect(self._on_alarm, Qt.ConnectionType.QueuedConnection)
        event_bus.clip_ready.connect(self._on_clip_ready, Qt.ConnectionType.QueuedConnection)

        self._reload()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._reload()
        super().showEvent(event)

    def _reload(self) -> None:
        self._events = repository.list_alarm_events(limit=200)
        # La media de TODOS los eventos listados sale en una sola consulta
        # indexada (media_assets.alarm_event_id) -- nada de una query por
        # fila ni de tocar el filesystem para saber si hay clip/captura.
        self._media_by_event = repository.list_media_for_events([e.id for e in self._events])
        self.table.setRowCount(len(self._events))
        for row, alarm_event in enumerate(self._events):
            self._set_row(row, alarm_event)
        self._on_selection_changed()

    def _media_path(self, alarm_event_id: int, kind: str) -> str | None:
        for asset in self._media_by_event.get(alarm_event_id, []):
            if asset.kind == kind:
                return str(media_store.absolute_path(asset.rel_path))
        return None

    def _set_row(self, row: int, alarm_event: AlarmEventRow) -> None:
        device = repository.get_device(alarm_event.device_id)
        device_name = device.name if device else f"#{alarm_event.device_id}"
        when = dt.datetime.fromtimestamp(alarm_event.timestamp).strftime("%H:%M:%S")

        self.table.setItem(row, 0, QTableWidgetItem(when))
        self.table.setItem(row, 1, QTableWidgetItem(device_name))
        self.table.setItem(row, 2, QTableWidgetItem(display_class(alarm_event.object_class)))

        severity_item = QTableWidgetItem(
            SEVERITY_LABELS.get(alarm_event.severity, alarm_event.severity)
        )
        color = SEVERITY_COLORS.get(alarm_event.severity, "#e5e7eb")
        severity_item.setForeground(Qt.GlobalColor.white)
        severity_item.setBackground(QColor(color))
        self.table.setItem(row, 3, severity_item)

        self.table.setItem(row, 4, QTableWidgetItem(f"{alarm_event.confidence:.0%}"))

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

        self.table.setItem(
            row, 6, QTableWidgetItem(STATUS_LABELS.get(alarm_event.status, alarm_event.status))
        )

    def _selected_event(self) -> AlarmEventRow | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._events[rows[0].row()]

    def _on_selection_changed(self) -> None:
        alarm_event = self._selected_event()
        has_selection = alarm_event is not None
        self.notes_edit.setEnabled(has_selection)
        self.save_notes_button.setEnabled(has_selection)
        self.notes_edit.setPlainText(alarm_event.notes if alarm_event else "")

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
        # Abre con el reproductor por defecto del sistema operativo --
        # QDesktopServices es portable (os.startfile era solo-Windows y
        # impedia probar la app en Linux durante el desarrollo).
        QDesktopServices.openUrl(QUrl.fromLocalFile(clip_path))

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

    def _on_alarm(self, _event: AlarmEventDTO) -> None:
        self._reload()

    def _on_clip_ready(self, _event: ClipReadyEvent) -> None:
        self._reload()
