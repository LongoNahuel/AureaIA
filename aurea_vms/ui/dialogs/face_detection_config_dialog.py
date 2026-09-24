from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    Slider,
    SpinBox,
    StrongBodyLabel,
)

from aurea_vms.config.settings import settings
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase

FPS_RANGE = (1, 30)
CONFIRMATION_RANGE = (1, 10)
DEFAULT_CONFIRMATION_FRAMES = 2
FIELD_WIDTH = 130


def sensitivity_to_confidence(sensitivity: int) -> float:
    """1-100 -> umbral de confianza 0.05-0.95 (mas sensible = umbral mas
    bajo = detecta caras mas dudosas)."""
    return max(0.05, min(0.95, 1.0 - sensitivity / 100.0))


def confidence_to_sensitivity(confidence: float) -> int:
    return round(max(0.05, min(0.95, confidence)) * -100 + 100)


def _section_header(form: QFormLayout, text: str) -> None:
    label = StrongBodyLabel(text)
    label.setContentsMargins(0, 8, 0, 0)
    form.addRow(label)


def _caption(text: str) -> CaptionLabel:
    label = CaptionLabel(text)
    label.setWordWrap(True)
    return label


class FaceDetectionConfigDialog(AnalyticsConfigDialogBase):
    analyzer_name = "face_detection"
    display_name = "Detección Facial"
    roi_mode = "rect"
    show_confidence = False

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        params = (existing.params if existing else {}) or {}
        confidence = existing.confidence_threshold if existing else 0.5

        intro = BodyLabel(
            "Solo detección (sin reconocimiento). Dibujá un rectángulo para restringir el área "
            "de captura, o dejá sin selección para pantalla completa."
        )
        intro.setWordWrap(True)
        form.addRow(intro)

        # =========================== Detección ===========================
        _section_header(form, "Detección")

        self.sensitivity_slider = Slider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(1, 100)
        self.sensitivity_slider.setValue(confidence_to_sensitivity(confidence))
        self.sensitivity_slider.setToolTip("Más alto = detecta caras más dudosas (más sensible).")
        self.sensitivity_value_label = BodyLabel(f"{self.sensitivity_slider.value()}%")
        self.sensitivity_value_label.setFixedWidth(28)
        self.sensitivity_slider.valueChanged.connect(
            lambda v: self.sensitivity_value_label.setText(f"{v}%")
        )
        sensitivity_row = QHBoxLayout()
        sensitivity_row.addWidget(self.sensitivity_slider, stretch=1)
        sensitivity_row.addWidget(self.sensitivity_value_label)
        form.addRow("Sensibilidad:", sensitivity_row)

        self.min_pupillary_spin = SpinBox()
        self.min_pupillary_spin.setRange(0, 300)
        self.min_pupillary_spin.setSingleStep(5)
        self.min_pupillary_spin.setSuffix(" px")
        self.min_pupillary_spin.setMaximumWidth(FIELD_WIDTH)
        self.min_pupillary_spin.setValue(params.get("min_pupillary_distance_px", 40))
        self.min_pupillary_spin.setToolTip("Descarta caras muy chicas/lejanas. 0 px = sin mínimo.")
        form.addRow("Distancia pupilar mínima:", self.min_pupillary_spin)

        self.tilt_filter_check = CheckBox("Filtrar rostros inclinados")
        self.tilt_filter_check.setChecked(bool(params.get("tilted_faces_filter", True)))
        form.addRow(self.tilt_filter_check)

        self.confirmation_spin = SpinBox()
        self.confirmation_spin.setRange(*CONFIRMATION_RANGE)
        self.confirmation_spin.setMaximumWidth(FIELD_WIDTH)
        self.confirmation_spin.setValue(
            params.get("confirmation_frames", DEFAULT_CONFIRMATION_FRAMES)
        )
        self.confirmation_spin.setToolTip(
            "Cuadros seguidos que una cara debe sostenerse para contar como detección real (1 = al "
            "instante). Se suma a la validación geométrica de puntos de referencia, siempre activa."
        )
        form.addRow("Confirmación (frames):", self.confirmation_spin)
        form.addRow(_caption("Más alto = menos falsos disparos, pero tarda un poco más en marcar."))

        # ============================ Captura =============================
        _section_header(form, "Captura")
        form.addRow(
            _caption(
                "Se guarda una sola captura por cada paso de una cara por la cámara: la mejor "
                "toma, y solo si el rostro se ve bien (de frente, nítido, con al menos "
                "18 px entre ojos y detección segura). La sensibilidad de arriba decide qué se "
                "marca en el video; esta regla, qué se guarda."
            )
        )

        self.attribute_checks = {
            "gafas": CheckBox("Gafas"),
            "barbijo": CheckBox("Barbijo"),
            "sombrero": CheckBox("Sombrero"),
        }
        selected_attributes = set(params.get("face_attributes", []))
        for name, check in self.attribute_checks.items():
            check.setChecked(name in selected_attributes)
            form.addRow(check)

        self.fps_spin = SpinBox()
        self.fps_spin.setRange(*FPS_RANGE)
        self.fps_spin.setMaximumWidth(FIELD_WIDTH)
        self.fps_spin.setValue(params.get("fps", settings.analytics_fps))
        self.fps_spin.setToolTip(
            "Cuadros por segundo para esta cámara, independiente del FPS global del resto de los "
            "analizadores. Más alto = más fluido y más CPU."
        )
        form.addRow("FPS de análisis:", self.fps_spin)

    def confidence_threshold_value(self) -> float:
        return sensitivity_to_confidence(self.sensitivity_slider.value())

    def build_params(self) -> dict:
        return {
            "min_pupillary_distance_px": self.min_pupillary_spin.value(),
            "confirmation_frames": self.confirmation_spin.value(),
            "tilted_faces_filter": self.tilt_filter_check.isChecked(),
            "face_attributes": [
                name for name, check in self.attribute_checks.items() if check.isChecked()
            ],
            "fps": self.fps_spin.value(),
        }
