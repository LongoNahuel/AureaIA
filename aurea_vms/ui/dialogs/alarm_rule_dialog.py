"""Formulario de alta/edicion de una regla de alarma. Las clases
disponibles para marcar cambian segun el analizador elegido, porque cada
uno produce etiquetas distintas (persona, cara, movimiento, vehiculos)."""

from __future__ import annotations

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QTimeEdit, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, CheckBox, ComboBox, DoubleSpinBox, FluentIcon, PrimaryPushButton, PushButton, SpinBox

from aurea_vms.core.analytics.registry import ANALYZER_DISPLAY_NAMES, AVAILABLE_ANALYZERS
from aurea_vms.models import repository
from aurea_vms.models.alarm_rule import SEVERITIES, SEVERITY_MEDIUM, AlarmRule
from aurea_vms.ui.labels import display_class
from aurea_vms.ui.notify import warn

ANALYZER_CLASSES: dict[str, list[str]] = {
    "motion_detection": ["movimiento"],
    "people_counting": ["person"],
    "line_crossing": ["person", "car", "motorcycle", "bicycle", "bus", "truck"],
    "face_detection": ["cara"],
}

ALL_DEVICES_LABEL = "Todas las cámaras"

SEVERITY_LABELS = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "info": "Info"}

DAY_LABELS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


class AlarmRuleDialog(QDialog):
    def __init__(self, rule: AlarmRule | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Regla de alarma")
        self.resize(480, 0)

        self.device_selector = ComboBox()
        self.device_selector.addItem(ALL_DEVICES_LABEL, userData=None)
        for device in repository.list_devices():
            self.device_selector.addItem(device.name, userData=device.id)

        self.analyzer_selector = ComboBox()
        for name in AVAILABLE_ANALYZERS:
            self.analyzer_selector.addItem(ANALYZER_DISPLAY_NAMES[name], userData=name)
        self.analyzer_selector.currentIndexChanged.connect(self._rebuild_class_checks)

        self.class_checks_container = QWidget()
        self.class_checks_layout = QHBoxLayout(self.class_checks_container)
        self.class_checks_layout.setContentsMargins(0, 0, 0, 0)
        self.class_checks: dict[str, CheckBox] = {}

        self.severity_selector = ComboBox()
        for value in SEVERITIES:
            self.severity_selector.addItem(SEVERITY_LABELS[value], userData=value)

        self.confidence_spin = DoubleSpinBox()
        self.confidence_spin.setRange(0.05, 0.95)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setValue(0.5)

        self.cooldown_spin = SpinBox()
        self.cooldown_spin.setRange(1, 3600)
        self.cooldown_spin.setValue(30)

        self.save_clip_check = CheckBox("Guardar clip de evento al disparar")
        self.save_clip_check.setChecked(True)

        self.notify_desktop_check = CheckBox("Notificación de escritorio al disparar")
        self.notify_desktop_check.setChecked(False)

        self.enabled_check = CheckBox("Regla habilitada")
        self.enabled_check.setChecked(True)

        # --- horario -------------------------------------------------
        self.day_checks: dict[int, CheckBox] = {}
        days_row = QHBoxLayout()
        for i, label in enumerate(DAY_LABELS):
            check = CheckBox(label)
            self.day_checks[i] = check
            days_row.addWidget(check)
        days_widget = QWidget()
        days_widget.setLayout(days_row)

        self.start_time_edit = QTimeEdit()
        self.start_time_edit.setDisplayFormat("HH:mm")
        self.end_time_edit = QTimeEdit()
        self.end_time_edit.setDisplayFormat("HH:mm")
        self.schedule_enabled_check = CheckBox("Restringir a un horario")
        self.schedule_enabled_check.toggled.connect(self._on_schedule_toggled)

        time_row = QHBoxLayout()
        time_row.addWidget(CaptionLabel("Desde:"))
        time_row.addWidget(self.start_time_edit)
        time_row.addWidget(CaptionLabel("Hasta:"))
        time_row.addWidget(self.end_time_edit)
        time_row.addStretch(1)
        time_widget = QWidget()
        time_widget.setLayout(time_row)

        form = QFormLayout()
        form.addRow("Cámara:", self.device_selector)
        form.addRow("Analizador:", self.analyzer_selector)
        form.addRow("Clases que disparan alarma:", self.class_checks_container)
        form.addRow("Severidad:", self.severity_selector)
        form.addRow("Confianza mínima:", self.confidence_spin)
        form.addRow("Cooldown (segundos):", self.cooldown_spin)
        form.addRow(self.save_clip_check)
        form.addRow(self.notify_desktop_check)
        form.addRow(self.enabled_check)
        form.addRow(CaptionLabel("Días de la semana (sin marcar = todos):"))
        form.addRow(days_widget)
        form.addRow(self.schedule_enabled_check)
        form.addRow(time_widget)

        cancel_button = PushButton("Cancelar")
        save_button = PrimaryPushButton(FluentIcon.SAVE, "Guardar")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self.accept)
        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(cancel_button)
        buttons_row.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons_row)

        self._rebuild_class_checks()
        self._on_schedule_toggled(False)
        if rule is not None:
            self._load(rule)

    def _on_schedule_toggled(self, checked: bool) -> None:
        self.start_time_edit.setEnabled(checked)
        self.end_time_edit.setEnabled(checked)

    def _rebuild_class_checks(self) -> None:
        while self.class_checks_layout.count():
            item = self.class_checks_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.class_checks.clear()

        analyzer_name = self.analyzer_selector.currentData()
        for cls in ANALYZER_CLASSES.get(analyzer_name, []):
            check = CheckBox(display_class(cls))
            check.setChecked(True)
            self.class_checks[cls] = check
            self.class_checks_layout.addWidget(check)

    def _load(self, rule: AlarmRule) -> None:
        index = self.device_selector.findData(rule.device_id)
        if index >= 0:
            self.device_selector.setCurrentIndex(index)

        index = self.analyzer_selector.findData(rule.analyzer_name)
        if index >= 0:
            self.analyzer_selector.setCurrentIndex(index)

        selected = set(rule.object_classes or [])
        for cls, check in self.class_checks.items():
            check.setChecked(cls in selected)

        index = self.severity_selector.findData(rule.severity or SEVERITY_MEDIUM)
        if index >= 0:
            self.severity_selector.setCurrentIndex(index)

        self.confidence_spin.setValue(rule.min_confidence)
        self.cooldown_spin.setValue(rule.cooldown_seconds)
        self.save_clip_check.setChecked(bool((rule.actions or {}).get("save_clip", True)))
        self.notify_desktop_check.setChecked(bool((rule.actions or {}).get("notify_desktop", False)))
        self.enabled_check.setChecked(rule.enabled)

        for day, check in self.day_checks.items():
            check.setChecked(day in (rule.schedule_days or []))

        has_schedule = bool(rule.schedule_start and rule.schedule_end)
        self.schedule_enabled_check.setChecked(has_schedule)
        if has_schedule:
            sh, sm = (int(part) for part in rule.schedule_start.split(":")[:2])
            eh, em = (int(part) for part in rule.schedule_end.split(":")[:2])
            self.start_time_edit.setTime(QTime(sh, sm))
            self.end_time_edit.setTime(QTime(eh, em))

    def accept(self) -> None:
        if not any(check.isChecked() for check in self.class_checks.values()):
            warn(self, "Datos incompletos", "Seleccioná al menos una clase.")
            return
        super().accept()

    def values(self) -> dict:
        schedule_active = self.schedule_enabled_check.isChecked()
        return {
            "device_id": self.device_selector.currentData(),
            "analyzer_name": self.analyzer_selector.currentData(),
            "object_classes": [cls for cls, check in self.class_checks.items() if check.isChecked()],
            "severity": self.severity_selector.currentData(),
            "min_confidence": self.confidence_spin.value(),
            "cooldown_seconds": self.cooldown_spin.value(),
            "actions": {
                "notify_ui": True,
                "save_clip": self.save_clip_check.isChecked(),
                "notify_desktop": self.notify_desktop_check.isChecked(),
            },
            "enabled": self.enabled_check.isChecked(),
            "schedule_days": [day for day, check in self.day_checks.items() if check.isChecked()],
            "schedule_start": self.start_time_edit.time().toString("HH:mm") if schedule_active else None,
            "schedule_end": self.end_time_edit.time().toString("HH:mm") if schedule_active else None,
        }
