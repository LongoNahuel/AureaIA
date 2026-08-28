from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout
from qfluentwidgets import BodyLabel, CaptionLabel, DoubleSpinBox, Slider, SpinBox

from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase

FPS_RANGE = (1, 30)
DEFAULT_FPS = 15
CONFIRMATION_RANGE = (1, 10)
DEFAULT_CONFIRMATION_FRAMES = 2
FIELD_WIDTH = 130


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
        sensitivity_caption = CaptionLabel(
            "Más alta = detecta cambios más sutiles (también más ruido)."
        )
        sensitivity_caption.setWordWrap(True)
        form.addRow(sensitivity_caption)

        # % del cuadro (o del ROI) en vez de px^2: el mismo numero tiene
        # sentido sin importar la resolucion de la camara.
        self.min_size_spin = DoubleSpinBox()
        self.min_size_spin.setRange(0.05, 20.0)
        self.min_size_spin.setSingleStep(0.05)
        self.min_size_spin.setDecimals(2)
        self.min_size_spin.setSuffix(" %")
        self.min_size_spin.setMaximumWidth(130)
        self.min_size_spin.setValue(params.get("min_area_percent", 0.5))
        form.addRow("Tamaño mínimo del objeto:", self.min_size_spin)
        min_size_caption = CaptionLabel(
            "Ignora regiones más chicas que este % del cuadro (o del ROI) — "
            "subilo si aparecen falsas detecciones muy pequeñas."
        )
        min_size_caption.setWordWrap(True)
        form.addRow(min_size_caption)

        self.confirmation_spin = SpinBox()
        self.confirmation_spin.setRange(*CONFIRMATION_RANGE)
        self.confirmation_spin.setMaximumWidth(FIELD_WIDTH)
        self.confirmation_spin.setValue(
            params.get("confirmation_frames", DEFAULT_CONFIRMATION_FRAMES)
        )
        self.confirmation_spin.setToolTip(
            "Una región solo se reporta tras esta cantidad de cuadros seguidos (1 = al instante). "
            "Filtra ruido de un solo frame (parpadeo de IR, compresión) sin agregar demora "
            "perceptible."
        )
        form.addRow("Confirmación (frames):", self.confirmation_spin)
        form.addRow(
            CaptionLabel("Más alto = menos falsos positivos, pero tarda un poco más en marcar.")
        )

        self.fps_spin = SpinBox()
        self.fps_spin.setRange(*FPS_RANGE)
        self.fps_spin.setMaximumWidth(FIELD_WIDTH)
        self.fps_spin.setValue(params.get("fps", DEFAULT_FPS))
        self.fps_spin.setToolTip(
            "Cuadros por segundo para esta cámara, independiente del FPS global del resto de los "
            "analizadores. MOG2 es liviano: se puede pedir más FPS que en los analizadores con IA."
        )
        form.addRow("FPS de análisis:", self.fps_spin)

        roi_label = BodyLabel("ROI opcional: dibujá un rectángulo para limitar la zona vigilada.")
        roi_label.setWordWrap(True)
        form.addRow(roi_label)

    def build_params(self) -> dict:
        return {
            "sensitivity": self.sensitivity_slider.value(),
            "min_area_percent": self.min_size_spin.value(),
            "confirmation_frames": self.confirmation_spin.value(),
            "fps": self.fps_spin.value(),
        }
