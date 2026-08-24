from __future__ import annotations

from PySide6.QtWidgets import QFormLayout
from qfluentwidgets import BodyLabel, CaptionLabel, SpinBox

from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase


class PeopleCountingConfigDialog(AnalyticsConfigDialogBase):
    analyzer_name = "people_counting"
    display_name = "Conteo de Personas"
    roi_mode = "rect"

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        params = (existing.params if existing else {}) or {}

        form.addRow(
            BodyLabel(
                "Dibujá la zona (ROI) a monitorear. Sin selección = frame completo.\n"
                "El dashboard muestra la ocupación actual (personas presentes ahora)."
            )
        )

        self.confirmation_spin = SpinBox()
        self.confirmation_spin.setRange(1, 10)
        self.confirmation_spin.setValue(params.get("confirmation_frames", 2))
        form.addRow("Confirmación (muestras):", self.confirmation_spin)
        form.addRow(
            CaptionLabel(
                "Una persona se suma a la ocupación recién después de esta cantidad de muestras "
                "seguidas detectada (1 = cuenta al instante). Más alto = menos falsos positivos, "
                "pero tarda más en reflejar gente que entra/sale rápido."
            )
        )

    def object_classes(self) -> list[str]:
        return ["person"]

    def build_params(self) -> dict:
        return {"confirmation_frames": self.confirmation_spin.value()}
