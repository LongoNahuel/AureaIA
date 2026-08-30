from __future__ import annotations

from PySide6.QtWidgets import QFormLayout
from qfluentwidgets import BodyLabel, CaptionLabel, DoubleSpinBox, SpinBox

from aurea_vms.config.settings import settings
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase

FPS_RANGE = (1, 30)
FIELD_WIDTH = 130


class PeopleCountingConfigDialog(AnalyticsConfigDialogBase):
    analyzer_name = "people_counting"
    display_name = "Conteo de Personas"
    roi_mode = "rect"

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        params = (existing.params if existing else {}) or {}

        intro = BodyLabel(
            "Dibujá la zona (ROI) a monitorear. Sin selección = frame completo.\n"
            "El dashboard muestra la ocupación actual (personas presentes ahora)."
        )
        intro.setWordWrap(True)
        form.addRow(intro)

        self.confirmation_spin = SpinBox()
        self.confirmation_spin.setRange(1, 10)
        self.confirmation_spin.setMaximumWidth(FIELD_WIDTH)
        self.confirmation_spin.setValue(params.get("confirmation_frames", 2))
        self.confirmation_spin.setToolTip(
            "Una persona se suma a la ocupación recién tras esta cantidad de muestras seguidas "
            "(1 = al instante)."
        )
        form.addRow("Confirmación (muestras):", self.confirmation_spin)
        caption = CaptionLabel(
            "Más alto = menos falsos positivos, pero tarda más en reflejar cambios."
        )
        caption.setWordWrap(True)
        form.addRow(caption)

        self.min_size_spin = DoubleSpinBox()
        self.min_size_spin.setRange(0.0, 20.0)
        self.min_size_spin.setSingleStep(0.05)
        self.min_size_spin.setDecimals(2)
        self.min_size_spin.setSuffix(" %")
        self.min_size_spin.setMaximumWidth(FIELD_WIDTH)
        self.min_size_spin.setValue(params.get("min_area_percent", 0.15))
        form.addRow("Tamaño mínimo:", self.min_size_spin)
        min_size_caption = CaptionLabel(
            "Ignora detecciones más chicas que este % del cuadro — 0 desactiva el filtro."
        )
        min_size_caption.setWordWrap(True)
        form.addRow(min_size_caption)

        self.fps_spin = SpinBox()
        self.fps_spin.setRange(*FPS_RANGE)
        self.fps_spin.setMaximumWidth(FIELD_WIDTH)
        self.fps_spin.setValue(params.get("fps", settings.analytics_fps))
        self.fps_spin.setToolTip(
            "Cuadros por segundo para esta cámara, independiente del FPS global del resto de los "
            "analizadores."
        )
        form.addRow("FPS de análisis:", self.fps_spin)

        self.occlusion_spin = DoubleSpinBox()
        self.occlusion_spin.setRange(0.5, 10.0)
        self.occlusion_spin.setSingleStep(0.5)
        self.occlusion_spin.setDecimals(1)
        self.occlusion_spin.setSuffix(" s")
        self.occlusion_spin.setMaximumWidth(FIELD_WIDTH)
        self.occlusion_spin.setValue(params.get("track_max_age_s", 1.5))
        self.occlusion_spin.setToolTip(
            "Cuánto tiempo sigue contando a alguien tras perderlo (oclusión momentánea detrás de "
            "otra persona u objeto) antes de sacarlo de la ocupación."
        )
        form.addRow("Tolerancia a oclusión:", self.occlusion_spin)
        form.addRow(
            CaptionLabel(
                "Más alta = menos parpadeo del número, pero tarda más en reflejar que alguien "
                "realmente se fue."
            )
        )

    def object_classes(self) -> list[str]:
        return ["person"]

    def build_params(self) -> dict:
        return {
            "confirmation_frames": self.confirmation_spin.value(),
            "min_area_percent": self.min_size_spin.value(),
            "fps": self.fps_spin.value(),
            "track_max_age_s": self.occlusion_spin.value(),
        }
