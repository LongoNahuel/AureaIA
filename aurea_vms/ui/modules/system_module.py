"""Configuración del sistema: menú lateral agrupado (Audio y Video /
Sistema / Operación), al estilo EZStation. Lo real -- monitor de
recursos, log, apariencia, seguridad (cambio de contraseña), PTZ,
capturas/clips de evento -- convive con secciones que todavía no tienen
funcionalidad propia (marcadas "Próximamente"), sin inventar nada que la
app no haga de verdad."""

from __future__ import annotations

import os

import psutil
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QPlainTextEdit,
    QStackedWidget,
    QTableWidgetItem,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon,
    HeaderCardWidget,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SwitchButton,
    TableWidget,
    TreeWidget,
)

from aurea_vms.config.settings import settings
from aurea_vms.core import app_prefs, auth
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository
from aurea_vms.ui.notify import notify, warn
from aurea_vms.ui.theme import apply_theme
from aurea_vms.ui.widgets.ptz_control_panel import PtzControlPanel

LOG_TAIL_LINES = 300
RESOURCE_REFRESH_MS = 2000

PLACEHOLDER_NOTES = {
    "Video": "Próximamente: configuración de codec, resolución y calidad por cámara (vía ONVIF).",
    "Alarma": "La configuración de reglas de alarma vive en los módulos Alarmas y Alertas.",
    "Servicio": "Próximamente: administración de servicios del sistema.",
    "Visualización de atributos": "Próximamente: superposición de atributos detectados sobre el video en vivo.",
}


def _card(title: str) -> tuple[HeaderCardWidget, QWidget]:
    card = HeaderCardWidget()
    card.setTitle(title)
    content = QWidget()
    card.viewLayout.addWidget(content)
    return card, content


def _page(*cards: QWidget) -> QWidget:
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    for card in cards:
        layout.addWidget(card)
    layout.addStretch(1)
    return wrapper


class SystemModule(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._process = psutil.Process(os.getpid())
        self._process.cpu_percent(None)  # primer llamado solo "arma" la medicion
        self._cpu_count = psutil.cpu_count() or 1

        self.pages = QStackedWidget(self)
        self.nav_tree = TreeWidget(self)
        self.nav_tree.setHeaderHidden(True)
        self.nav_tree.setFixedWidth(220)
        self.nav_tree.itemClicked.connect(self._on_nav_clicked)

        self._add_page("Audio y Video", "Video", self._build_placeholder_page("Video"))
        self._add_page("Audio y Video", "Instantánea", self._build_snapshot_page())
        self._add_page("Audio y Video", "Grabando", self._build_recording_page())
        self._add_page("Sistema", "Inicio", self._build_home_page())
        self._add_page("Sistema", "Sistema", self._build_appearance_page())
        self._add_page("Sistema", "Registro", self._build_logs_page())
        self._add_page("Sistema", "Seguridad", self._build_security_page())
        self._add_page("Operación", "Alarma", self._build_placeholder_page("Alarma"))
        self._add_page("Operación", "Servicio", self._build_placeholder_page("Servicio"))
        self._add_page(
            "Operación",
            "Visualización de atributos",
            self._build_placeholder_page("Visualización de atributos"),
        )
        self._add_page("Operación", "PTZ", self._build_ptz_page())

        for i in range(self.nav_tree.topLevelItemCount()):
            self.nav_tree.topLevelItem(i).setExpanded(True)
        first_leaf = self.nav_tree.topLevelItem(0).child(0)
        self.nav_tree.setCurrentItem(first_leaf)
        self.pages.setCurrentIndex(0)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.nav_tree)
        layout.addWidget(self.pages, stretch=1)

        self._timer = QTimer(self)
        self._timer.setInterval(RESOURCE_REFRESH_MS)
        self._timer.timeout.connect(self._refresh_resources)
        self._timer.start()

        self._refresh_resources()
        self._refresh_logs()

    def showEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._refresh_resources()
        self._refresh_logs()
        self.ptz_panel.reload_devices()
        super().showEvent(event)

    # --- navegacion -------------------------------------------------

    def _add_page(self, group_label: str, leaf_label: str, page: QWidget) -> None:
        group_item = self._group_item(group_label)
        leaf_item = QTreeWidgetItem([leaf_label])
        index = self.pages.addWidget(page)
        leaf_item.setData(0, Qt.ItemDataRole.UserRole, index)
        group_item.addChild(leaf_item)

    def _group_item(self, label: str) -> QTreeWidgetItem:
        for i in range(self.nav_tree.topLevelItemCount()):
            item = self.nav_tree.topLevelItem(i)
            if item.text(0) == label:
                return item
        item = QTreeWidgetItem([label])
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.nav_tree.addTopLevelItem(item)
        return item

    def _on_nav_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if index is None:
            item.setExpanded(not item.isExpanded())
            return
        self.pages.setCurrentIndex(index)

    def focus_section(self, group_label: str, leaf_label: str) -> None:
        """API publica para otros modulos (ej. panel "Base" de Inicio):
        enfoca una seccion especifica del arbol de navegacion."""
        for i in range(self.nav_tree.topLevelItemCount()):
            group_item = self.nav_tree.topLevelItem(i)
            if group_item.text(0) != group_label:
                continue
            group_item.setExpanded(True)
            for j in range(group_item.childCount()):
                leaf_item = group_item.child(j)
                if leaf_item.text(0) != leaf_label:
                    continue
                self.nav_tree.setCurrentItem(leaf_item)
                index = leaf_item.data(0, Qt.ItemDataRole.UserRole)
                if index is not None:
                    self.pages.setCurrentIndex(index)
                return

    # --- Audio y Video > Video (placeholder) / Alarma, Servicio, Atributos -----------

    def _build_placeholder_page(self, key: str) -> QWidget:
        card, content = _card(key)
        layout = QVBoxLayout(content)
        label = BodyLabel(PLACEHOLDER_NOTES[key])
        label.setWordWrap(True)
        layout.addWidget(label)
        return _page(card)

    # --- Audio y Video > Instantánea -------------------------------------------------

    def _build_snapshot_page(self) -> QWidget:
        card, content = _card("Instantánea")
        form = QFormLayout(content)
        form.addRow("Carpeta de capturas:", BodyLabel(str(settings.media_dir / "snapshot")))
        note = CaptionLabel(
            "La captura se guarda automáticamente apenas se dispara una alarma "
            "(no hay captura manual todavía)."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return _page(card)

    # --- Audio y Video > Grabando -------------------------------------------------

    def _build_recording_page(self) -> QWidget:
        card, content = _card("Grabando")
        form = QFormLayout(content)
        form.addRow("Carpeta de clips:", BodyLabel(str(settings.media_dir / "clip")))
        form.addRow("Pre-buffer:", BodyLabel(f"{settings.clip_pre_seconds} s"))
        form.addRow("Post-captura:", BodyLabel(f"{settings.clip_post_seconds} s"))
        note = CaptionLabel(
            "Esta fase no graba en continuo: solo se guarda un clip corto "
            "alrededor de cada evento de alarma."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return _page(card)

    # --- Sistema > Inicio -------------------------------------------------

    def _build_home_page(self) -> QWidget:
        info_card, info_content = _card("Inicio")
        form = QFormLayout(info_content)
        form.addRow("Directorio de datos:", BodyLabel(str(settings.data_dir)))
        form.addRow("Base de datos:", BodyLabel(str(settings.db_path)))
        form.addRow("Log:", BodyLabel(str(settings.log_path)))
        form.addRow("FPS Vista en Vivo:", BodyLabel(str(settings.display_fps)))
        form.addRow("FPS Analizadores:", BodyLabel(str(settings.analytics_fps)))

        resources_card, resources_content = _card("Recursos")
        layout = QVBoxLayout(resources_content)

        stats_row = QHBoxLayout()
        self.cpu_label = BodyLabel("CPU (proceso): —")
        self.mem_label = BodyLabel("Memoria (proceso): —")
        stats_row.addWidget(self.cpu_label)
        stats_row.addWidget(self.mem_label)
        stats_row.addStretch(1)
        layout.addLayout(stats_row)

        self.cameras_table = TableWidget(resources_content)
        self.cameras_table.setColumnCount(3)
        self.cameras_table.setHorizontalHeaderLabels(["Cámara", "Estado", "FPS medido"])
        self.cameras_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.cameras_table.verticalHeader().setVisible(False)
        self.cameras_table.setBorderVisible(True)
        self.cameras_table.setBorderRadius(6)
        self.cameras_table.setMinimumHeight(180)
        layout.addWidget(self.cameras_table)

        return _page(info_card, resources_card)

    # --- Sistema > Sistema (Apariencia) -------------------------------------------------

    def _build_appearance_page(self) -> QWidget:
        card, content = _card("Sistema")
        layout = QVBoxLayout(content)

        row = QHBoxLayout()
        row.addWidget(StrongBodyLabel("Modo oscuro"))
        self.dark_mode_switch = SwitchButton(content)
        self.dark_mode_switch.setChecked(app_prefs.get_theme() != "light")
        self.dark_mode_switch.checkedChanged.connect(self._on_theme_toggled)
        row.addWidget(self.dark_mode_switch)
        row.addStretch(1)
        layout.addLayout(row)

        hint = CaptionLabel("Se aplica al instante y se recuerda la próxima vez que abras la app.")
        layout.addWidget(hint)

        return _page(card)

    def _on_theme_toggled(self, checked: bool) -> None:
        apply_theme(checked)
        app_prefs.set_theme("dark" if checked else "light")

    # --- Sistema > Registro -------------------------------------------------

    def _build_logs_page(self) -> QWidget:
        card, content = _card("Registro")
        layout = QVBoxLayout(content)

        refresh_button = PushButton(FluentIcon.SYNC, "Actualizar registro")
        refresh_button.clicked.connect(self._refresh_logs)
        layout.addWidget(refresh_button)

        self.log_view = QPlainTextEdit(content)
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_TAIL_LINES)
        self.log_view.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        self.log_view.setMinimumHeight(320)
        layout.addWidget(self.log_view)

        return _page(card)

    # --- Sistema > Seguridad -------------------------------------------------

    def _build_security_page(self) -> QWidget:
        card, content = _card("Seguridad")
        form = QFormLayout(content)

        self.current_password_edit = PasswordLineEdit()
        self.new_password_edit = PasswordLineEdit()
        self.confirm_password_edit = PasswordLineEdit()
        form.addRow("Contraseña actual:", self.current_password_edit)
        form.addRow("Contraseña nueva:", self.new_password_edit)
        form.addRow("Confirmar contraseña nueva:", self.confirm_password_edit)

        hint = CaptionLabel("Al menos 9 caracteres, combinando letras, números y símbolos.")
        hint.setWordWrap(True)
        form.addRow(hint)

        change_button = PrimaryPushButton(FluentIcon.SAVE, "Cambiar contraseña")
        change_button.clicked.connect(self._on_change_password)
        form.addRow(change_button)

        return _page(card)

    def _on_change_password(self) -> None:
        user = auth.current_user
        if user is None:
            warn(self, "Seguridad", "No hay una sesión iniciada.")
            return
        if self.new_password_edit.text() != self.confirm_password_edit.text():
            warn(self, "Seguridad", "Las contraseñas nuevas no coinciden.")
            return

        error = auth.change_password(
            user.username, self.current_password_edit.text(), self.new_password_edit.text()
        )
        if error:
            warn(self, "Seguridad", error)
            return

        self.current_password_edit.clear()
        self.new_password_edit.clear()
        self.confirm_password_edit.clear()
        notify(self, "Seguridad", "Contraseña actualizada correctamente.")

    # --- Operación > PTZ -------------------------------------------------

    def _build_ptz_page(self) -> QWidget:
        self.ptz_panel = PtzControlPanel()
        return _page(self.ptz_panel)

    # --- refresco periodico -------------------------------------------------

    def _refresh_resources(self) -> None:
        cpu = self._process.cpu_percent(None) / self._cpu_count
        mem_mb = self._process.memory_info().rss / (1024 * 1024)
        self.cpu_label.setText(f"CPU (proceso): {cpu:.1f}%")
        self.mem_label.setText(f"Memoria (proceso): {mem_mb:.0f} MB")

        devices = repository.list_devices()
        self.cameras_table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            worker = stream_manager.get_worker(device.id)
            status = "Activa" if worker is not None else "Inactiva"
            fps_text = f"{worker.get_fps():.1f}" if worker is not None else "—"
            self.cameras_table.setItem(row, 0, QTableWidgetItem(device.name))
            self.cameras_table.setItem(row, 1, QTableWidgetItem(status))
            self.cameras_table.setItem(row, 2, QTableWidgetItem(fps_text))

    def _refresh_logs(self) -> None:
        log_path = settings.log_path
        if not log_path.exists():
            self.log_view.setPlainText("(sin logs todavía)")
            return
        try:
            with open(log_path, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()[-LOG_TAIL_LINES:]
        except OSError as exc:
            self.log_view.setPlainText(f"No se pudo leer el log: {exc}")
            return

        self.log_view.setPlainText("".join(lines))
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
