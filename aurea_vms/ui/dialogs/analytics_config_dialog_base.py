"""Base compartida por los dialogos de configuracion de cada tipo de
analitica: carga un snapshot de la camara, deja dibujar ROI/linea sobre
el, y persiste un AnalyticsConfig via upsert_analytics_config.

Cada subclase define: analyzer_name, display_name, roi_mode ('rect',
'line' o None) y build_extra_fields()/build_params() para sus campos
propios (sensibilidad, clases a contar, etiquetas, etc.).
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    DoubleSpinBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
)

from aurea_vms.core import device_manager
from aurea_vms.models import repository
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.device import Device
from aurea_vms.ui.notify import warn
from aurea_vms.ui.widgets.frame_selector import FrameSelectorWidget
from aurea_vms.ui.workers import FunctionWorker


class AnalyticsConfigDialogBase(QDialog):
    analyzer_name: str = ""
    display_name: str = ""
    roi_mode: str | None = "rect"
    show_confidence: bool = True

    def __init__(self, device: Device, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.device = device
        self.setWindowTitle(f"{self.display_name} — {device.name}")
        self.resize(680, 600)
        # Techo duro de ancho: un CaptionLabel/BodyLabel de descripcion sin
        # wordWrap fuerza un minimumSizeHint enorme en la fila del
        # QFormLayout (todo el texto en una sola linea), lo que termina
        # estirando el dialogo entero mucho mas alla de los 680px pedidos
        # arriba. Ademas de que cada subclase envuelva sus textos largos,
        # este limite evita que el dialogo vuelva a "explotar" en ancho si
        # alguna se olvida.
        self.setMaximumWidth(820)

        existing = repository.get_analytics_config_for(device.id, self.analyzer_name)
        self._worker: FunctionWorker | None = None

        self.enabled_check = CheckBox("Analizador habilitado")
        self.enabled_check.setChecked(existing.enabled if existing else False)

        top_form = QFormLayout()
        top_form.addRow(self.enabled_check)

        self.confidence_spin: DoubleSpinBox | None = None
        if self.show_confidence:
            self.confidence_spin = DoubleSpinBox()
            self.confidence_spin.setRange(0.05, 0.95)
            self.confidence_spin.setSingleStep(0.05)
            self.confidence_spin.setValue(existing.confidence_threshold if existing else 0.5)
            top_form.addRow("Confianza mínima:", self.confidence_spin)

        self.extra_form = QFormLayout()
        self.build_extra_fields(self.extra_form, existing)

        self.selector_widget = FrameSelectorWidget(self.roi_mode or "rect", self)
        if existing is not None and self.roi_mode == "rect":
            roi = None
            if None not in (existing.roi_x, existing.roi_y, existing.roi_w, existing.roi_h):
                roi = (existing.roi_x, existing.roi_y, existing.roi_w, existing.roi_h)
            self.selector_widget.set_initial_rect(roi)
        elif existing is not None and self.roi_mode == "line":
            line = (existing.params or {}).get("line")
            if line:
                self.selector_widget.set_initial_line((tuple(line[0]), tuple(line[1])))

        self.snapshot_status = BodyLabel("")
        self.snapshot_status.setWordWrap(True)
        refresh_button = PushButton(FluentIcon.SYNC, "Actualizar captura")
        refresh_button.clicked.connect(self._load_snapshot)
        clear_button = PushButton(FluentIcon.BROOM, "Limpiar selección")
        clear_button.clicked.connect(self.selector_widget.clear_selection)

        snapshot_row = QHBoxLayout()
        snapshot_row.addWidget(refresh_button)
        snapshot_row.addWidget(clear_button)
        snapshot_row.addStretch(1)
        snapshot_row.addWidget(self.snapshot_status)

        cancel_button = PushButton("Cancelar")
        save_button = PrimaryPushButton(FluentIcon.SAVE, "Guardar")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self.accept)
        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(cancel_button)
        buttons_row.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top_form)
        layout.addLayout(self.extra_form)
        layout.addLayout(snapshot_row)
        layout.addWidget(self.selector_widget, stretch=1)
        layout.addLayout(buttons_row)

        self._load_snapshot()

    # --- hooks para las subclases -------------------------------------------------

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        return None

    def build_params(self) -> dict:
        return {}

    def object_classes(self) -> list[str] | None:
        return None

    def validate(self) -> str | None:
        return None

    def confidence_threshold_value(self) -> float:
        """Hook para subclases que reemplazan el spinner generico de
        confianza por su propio control (ej. un slider de "Sensibilidad"
        0-100 que internamente se mapea a un umbral 0-1)."""
        return self.confidence_spin.value() if self.confidence_spin else 0.5

    # --- comportamiento comun -------------------------------------------------

    def accept(self) -> None:
        error = self.validate()
        if error:
            warn(self, "Datos incompletos", error)
            return
        super().accept()

    def roi_fields(self) -> dict:
        if self.roi_mode == "rect":
            rect = self.selector_widget.get_rect()
            if rect is None:
                return {"roi_x": None, "roi_y": None, "roi_w": None, "roi_h": None}
            x, y, w, h = rect
            return {"roi_x": x, "roi_y": y, "roi_w": w, "roi_h": h}
        return {"roi_x": None, "roi_y": None, "roi_w": None, "roi_h": None}

    def save(self) -> AnalyticsConfig:
        fields: dict = {
            "enabled": self.enabled_check.isChecked(),
            "confidence_threshold": self.confidence_threshold_value(),
            "params": self.build_params(),
            **self.roi_fields(),
        }
        object_classes = self.object_classes()
        if object_classes is not None:
            fields["object_classes"] = object_classes
        return repository.upsert_analytics_config(self.device.id, self.analyzer_name, **fields)

    def _load_snapshot(self) -> None:
        self.snapshot_status.setText("Cargando captura...")
        self._worker = FunctionWorker(lambda: device_manager.grab_snapshot(self.device), self)
        self._worker.succeeded.connect(self._on_snapshot)
        self._worker.failed.connect(lambda msg: self.snapshot_status.setText(f"Error: {msg}"))
        self._worker.start()

    def _on_snapshot(self, result: tuple) -> None:
        frame, detail = result
        if frame is None:
            self.snapshot_status.setText(f"No se pudo obtener la captura: {detail}")
            return
        self.snapshot_status.setText("Captura obtenida")
        self.selector_widget.set_frame(frame)
