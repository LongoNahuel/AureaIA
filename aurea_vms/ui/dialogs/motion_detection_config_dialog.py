from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout
from qfluentwidgets import BodyLabel, CaptionLabel, DoubleSpinBox, Slider

from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase


class MotionDetectionConfigDialog(AnalyticsConfigDialogBase):
    analyzer_name = "motion_detection"
    display_name = "Detección de Movimiento"
    roi_mode = "rect"
    show_confidence = False

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        params = (existing.params if existing else {}) or {}

        self.sensitivity_slider = Slider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(1, 100)
        self.sensitivity_slider.setValue(params.get("sensitivity", 50))
        self.sensitivity_value_label = BodyLabel(str(self.sensitivity_slider.value()))
        self.sensitivity_value_label.setFixedWidth(28)
        self.sensitivity_slider.valueChanged.connect(
            lambda v: self.sensitivity_value_label.setText(str(v))
        )

        sensitivity_row = QHBoxLayout()
        sensitivity_row.addWidget(self.sensitivity_slider, stretch=1)
        sensitivity_row.addWidget(self.sensitivity_value_label)
        form.addRow("Sensibilidad:", sensitivity_row)
        form.addRow(CaptionLabel("Más alta = detecta cambios más sutiles (también más ruido)."))

        # % del cuadro (o del ROI) en vez de px^2: el mismo numero tiene
        # sentido sin importar la resolucion de la camara.
        self.min_size_spin = DoubleSpinBox()
        self.min_size_spin.setRange(0.05, 20.0)
        self.min_size_spin.setSingleStep(0.05)
        self.min_size_spin.setDecimals(2)
        self.min_size_spin.setSuffix(" %")
        self.min_size_spin.setValue(params.get("min_area_percent", 0.5))
        form.addRow("Tamaño mínimo del objeto:", self.min_size_spin)
        form.addRow(
            CaptionLabel(
                "Ignora regiones más chicas que este % del cuadro (o del ROI) — "
                "subilo si aparecen falsas detecciones muy pequeñas."
            )
        )
        form.addRow(BodyLabel("ROI opcional: dibujá un rectángulo para limitar la zona vigilada."))

    def build_params(self) -> dict:
        return {
            "sensitivity": self.sensitivity_slider.value(),
            "min_area_percent": self.min_size_spin.value(),
        }
