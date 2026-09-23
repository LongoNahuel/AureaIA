from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout
from qfluentwidgets import BodyLabel, CaptionLabel, DoubleSpinBox, Slider, SpinBox

from aurea_vms.config.settings import settings
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase


class DoorStateConfigDialog(AnalyticsConfigDialogBase):
    analyzer_name = "door_state"
    display_name = "Puerta Abierta/Cerrada"
    roi_mode = "rects"
    max_rects = 4
    show_confidence = False

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        params = (existing.params if existing else {}) or {}
        self.sensitivity_slider = Slider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(1, 100)
        initial_threshold = float(params.get("change_threshold", 0.10))
        initial_sensitivity = round(max(1.0, min(100.0, (0.8 - initial_threshold) / 0.79 * 99 + 1)))
        self.sensitivity_slider.setValue(initial_sensitivity)
        self.sensitivity_value_label = BodyLabel(f"{initial_sensitivity}%")
        self.sensitivity_value_label.setFixedWidth(42)
        self.sensitivity_slider.valueChanged.connect(
            lambda value: self.sensitivity_value_label.setText(f"{value}%")
        )
        sensitivity_row = QHBoxLayout()
        sensitivity_row.addWidget(self.sensitivity_slider, stretch=1)
        sensitivity_row.addWidget(self.sensitivity_value_label)
        form.addRow("Sensibilidad:", sensitivity_row)
        self.open_percent_spin = DoubleSpinBox()
        self.open_percent_spin.setRange(1.0, 100.0)
        self.open_percent_spin.setSuffix(" %")
        self.open_percent_spin.setValue(params.get("opening_percent", 10.0))
        self.open_percent_spin.setMaximumWidth(130)
        form.addRow("Porcentaje de apertura:", self.open_percent_spin)
        hint = CaptionLabel(
            "Dibujá el ROI sobre la puerta. El valor indica qué porcentaje del ROI debe "
            "cambiar para considerar que la puerta está abierta."
        )
        hint.setWordWrap(True)
        form.addRow(hint)

        self.confirmation_spin = SpinBox()
        self.confirmation_spin.setRange(1, 15)
        self.confirmation_spin.setValue(params.get("confirmation_frames", 3))
        self.confirmation_spin.setMaximumWidth(130)
        form.addRow("Confirmación mínima (frames):", self.confirmation_spin)

        self.time_threshold_spin = DoubleSpinBox()
        self.time_threshold_spin.setRange(0.1, 30.0)
        self.time_threshold_spin.setSingleStep(0.1)
        self.time_threshold_spin.setSuffix(" s")
        self.time_threshold_spin.setValue(params.get("threshold_seconds", 0.5))
        self.time_threshold_spin.setMaximumWidth(130)
        form.addRow("Umbral de tiempo:", self.time_threshold_spin)

        self.fps_spin = SpinBox()
        self.fps_spin.setRange(1, 15)
        self.fps_spin.setValue(params.get("fps", min(10, int(settings.analytics_fps))))
        self.fps_spin.setMaximumWidth(130)
        form.addRow("FPS de análisis:", self.fps_spin)

    def validate(self) -> str | None:
        if not self.selector_widget.get_rects():
            return "Dibujá al menos una zona sobre la puerta para calibrar la analítica."
        return None

    def build_params(self) -> dict:
        return {
            "change_threshold": max(
                0.01, 0.8 - (self.sensitivity_slider.value() - 1) / 99 * 0.79
            ),
            "opening_percent": self.open_percent_spin.value(),
            "confirmation_frames": self.confirmation_spin.value(),
            "threshold_seconds": self.time_threshold_spin.value(),
            "fps": self.fps_spin.value(),
        }
