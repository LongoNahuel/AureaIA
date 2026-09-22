from __future__ import annotations

from PySide6.QtWidgets import QFormLayout
from qfluentwidgets import CaptionLabel, DoubleSpinBox, SpinBox

from aurea_vms.config.settings import settings
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase


class DoorStateConfigDialog(AnalyticsConfigDialogBase):
    analyzer_name = "door_state"
    display_name = "Puerta Abierta/Cerrada"
    roi_mode = "rect"
    show_confidence = False

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        params = (existing.params if existing else {}) or {}
        self.threshold_spin = DoubleSpinBox()
        self.threshold_spin.setRange(0.01, 0.8)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setValue(params.get("change_threshold", 0.10))
        self.threshold_spin.setMaximumWidth(130)
        form.addRow("Área mínima de cambio:", self.threshold_spin)
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
        form.addRow("Confirmación (frames):", self.confirmation_spin)

        self.fps_spin = SpinBox()
        self.fps_spin.setRange(1, 15)
        self.fps_spin.setValue(params.get("fps", min(10, int(settings.analytics_fps))))
        self.fps_spin.setMaximumWidth(130)
        form.addRow("FPS de análisis:", self.fps_spin)

    def validate(self) -> str | None:
        if self.selector_widget.get_rect() is None:
            return "Dibujá un rectángulo que cubra la puerta para calibrar la analítica."
        return None

    def build_params(self) -> dict:
        return {
            "change_threshold": self.threshold_spin.value(),
            "confirmation_frames": self.confirmation_spin.value(),
            "fps": self.fps_spin.value(),
        }
