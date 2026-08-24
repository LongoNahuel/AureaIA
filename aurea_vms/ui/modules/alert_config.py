from __future__ import annotations

from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, PrimaryPushButton, PushButton, TableWidget

from aurea_vms.core.analytics.registry import ANALYZER_DISPLAY_NAMES
from aurea_vms.models import repository
from aurea_vms.models.alarm_rule import AlarmRule
from aurea_vms.ui.dialogs.alarm_rule_dialog import ALL_DEVICES_LABEL, AlarmRuleDialog
from aurea_vms.ui.labels import display_class
from aurea_vms.ui.notify import confirm, warn

COLUMNS = ["Cámara", "Analizador", "Clases", "Severidad", "Estado"]
SEVERITY_LABELS = {"critico": "Crítico", "alto": "Alto", "medio": "Medio", "info": "Info"}


class AlertConfigModule(QWidget):
    """CRUD de reglas de alarma (dispositivo, analizador, clases, umbral, cooldown)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rules: list[AlarmRule] = []

        self.table = TableWidget(self)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(6)

        add_button = PrimaryPushButton(FluentIcon.ADD, "Agregar regla")
        edit_button = PushButton(FluentIcon.EDIT, "Editar")
        toggle_button = PushButton(FluentIcon.SETTING, "Habilitar/Deshabilitar")
        delete_button = PushButton(FluentIcon.DELETE, "Eliminar")

        add_button.clicked.connect(self._on_add)
        edit_button.clicked.connect(self._on_edit)
        toggle_button.clicked.connect(self._on_toggle)
        delete_button.clicked.connect(self._on_delete)

        toolbar = QHBoxLayout()
        toolbar.addWidget(add_button)
        toolbar.addWidget(edit_button)
        toolbar.addWidget(toggle_button)
        toolbar.addWidget(delete_button)
        toolbar.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self.table)

        self._reload()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._reload()
        super().showEvent(event)

    def _reload(self) -> None:
        self._rules = repository.list_alarm_rules()
        self.table.setRowCount(len(self._rules))
        for row, rule in enumerate(self._rules):
            device = repository.get_device(rule.device_id) if rule.device_id else None
            device_name = device.name if device else ALL_DEVICES_LABEL
            self.table.setItem(row, 0, QTableWidgetItem(device_name))
            self.table.setItem(
                row, 1, QTableWidgetItem(ANALYZER_DISPLAY_NAMES.get(rule.analyzer_name, rule.analyzer_name))
            )
            classes_text = ", ".join(display_class(c) for c in (rule.object_classes or []))
            self.table.setItem(row, 2, QTableWidgetItem(classes_text))
            self.table.setItem(row, 3, QTableWidgetItem(SEVERITY_LABELS.get(rule.severity, rule.severity)))
            self.table.setItem(row, 4, QTableWidgetItem("Habilitada" if rule.enabled else "Deshabilitada"))

    def _selected_rule(self) -> AlarmRule | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._rules[rows[0].row()]

    def _on_add(self) -> None:
        dialog = AlarmRuleDialog(parent=self)
        if dialog.exec():
            repository.add_alarm_rule(**dialog.values())
            self._reload()

    def _on_edit(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            warn(self, "Editar regla", "Seleccioná una regla primero.")
            return
        dialog = AlarmRuleDialog(rule=rule, parent=self)
        if dialog.exec():
            repository.update_alarm_rule(rule.id, **dialog.values())
            self._reload()

    def _on_toggle(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        repository.set_alarm_rule_enabled(rule.id, not rule.enabled)
        self._reload()

    def _on_delete(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        if confirm(self, "Eliminar regla", "¿Eliminar la regla seleccionada?"):
            repository.delete_alarm_rule(rule.id)
            self._reload()
