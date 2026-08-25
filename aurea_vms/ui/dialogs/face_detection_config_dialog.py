from __future__ import annotations

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QTimeEdit
from qfluentwidgets import BodyLabel, CaptionLabel, CheckBox, DoubleSpinBox, Slider, SpinBox

from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase

MAX_CAPTURES_RANGE = (1, 5)


def sensitivity_to_confidence(sensitivity: int) -> float:
    """1-100 -> umbral de confianza 0.05-0.95 (mas sensible = umbral mas
    bajo = detecta caras mas dudosas)."""
    return max(0.05, min(0.95, 1.0 - sensitivity / 100.0))


def confidence_to_sensitivity(confidence: float) -> int:
    return round(max(0.05, min(0.95, confidence)) * -100 + 100)


class FaceDetectionConfigDialog(AnalyticsConfigDialogBase):
    analyzer_name = "face_detection"
    display_name = "Detección Facial"
    roi_mode = "rect"
    show_confidence = False

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        params = (existing.params if existing else {}) or {}
        confidence = existing.confidence_threshold if existing else 0.5

        form.addRow(
            BodyLabel(
                "Solo detección (sin reconocimiento). Área de captura: dibujá un rectángulo "
                "para restringirla, o dejá sin selección para pantalla completa."
            )
        )

        # --- sensibilidad -------------------------------------------------
        self.sensitivity_slider = Slider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(1, 100)
        self.sensitivity_slider.setValue(confidence_to_sensitivity(confidence))
        self.sensitivity_value_label = BodyLabel(str(self.sensitivity_slider.value()))
        self.sensitivity_value_label.setFixedWidth(28)
        self.sensitivity_slider.valueChanged.connect(
            lambda v: self.sensitivity_value_label.setText(str(v))
        )
        sensitivity_row = QHBoxLayout()
        sensitivity_row.addWidget(self.sensitivity_slider, stretch=1)
        sensitivity_row.addWidget(self.sensitivity_value_label)
        form.addRow("Sensibilidad:", sensitivity_row)

        # --- distancia pupilar minima -------------------------------------------------
        self.min_pupillary_spin = SpinBox()
        self.min_pupillary_spin.setRange(0, 300)
        self.min_pupillary_spin.setSingleStep(5)
        self.min_pupillary_spin.setSuffix(" px")
        self.min_pupillary_spin.setValue(params.get("min_pupillary_distance_px", 40))
        form.addRow("Distancia pupilar mínima:", self.min_pupillary_spin)
        form.addRow(
            CaptionLabel(
                "Descarta caras muy chicas/lejanas: 0 px = sin mínimo (acepta cualquier tamaño)."
            )
        )

        # --- filtro por angulo -------------------------------------------------
        self.filter_angle_check = CheckBox("Filtrar por ángulo (descartar perfiles marcados)")
        self.filter_angle_check.setChecked(bool(params.get("filter_by_angle", False)))
        form.addRow(self.filter_angle_check)

        # --- conteo -------------------------------------------------
        self.counting_check = CheckBox("Contador de capturas habilitado")
        self.counting_check.setChecked(bool(params.get("counting_enabled", True)))
        form.addRow(self.counting_check)

        self.reset_time_edit = QTimeEdit()
        self.reset_time_edit.setDisplayFormat("HH:mm")
        reset_time = params.get("counting_reset_time", "00:00")
        hh, mm = (reset_time.split(":") + ["0", "0"])[:2]
        self.reset_time_edit.setTime(QTime(int(hh), int(mm)))
        form.addRow("Reiniciar contador a las:", self.reset_time_edit)

        # --- capturas por rostro -------------------------------------------------
        self.capture_threshold_spin = DoubleSpinBox()
        self.capture_threshold_spin.setRange(5.0, 90.0)
        self.capture_threshold_spin.setSingleStep(5.0)
        self.capture_threshold_spin.setSuffix(" %")
        self.capture_threshold_spin.setValue(params.get("capture_diff_threshold", 0.35) * 100)
        form.addRow("Umbral de nueva captura:", self.capture_threshold_spin)

        self.max_captures_spin = SpinBox()
        self.max_captures_spin.setRange(*MAX_CAPTURES_RANGE)
        self.max_captures_spin.setValue(params.get("max_captures_per_face", 1))
        form.addRow("Capturas por rostro:", self.max_captures_spin)
        form.addRow(
            CaptionLabel(
                'La galería guarda como mucho "Capturas por rostro" fotos del mismo rostro '
                '(1 = una sola vez). "Umbral de nueva captura" define qué tan distinto tiene '
                "que verse para no contarlo como el mismo: más alto = más estricto (menos "
                "capturas), más bajo = más sensible a cambios sutiles."
            )
        )

    def confidence_threshold_value(self) -> float:
        return sensitivity_to_confidence(self.sensitivity_slider.value())

    def build_params(self) -> dict:
        return {
            "min_pupillary_distance_px": self.min_pupillary_spin.value(),
            "filter_by_angle": self.filter_angle_check.isChecked(),
            "counting_enabled": self.counting_check.isChecked(),
            "counting_reset_time": self.reset_time_edit.time().toString("HH:mm"),
            "max_captures_per_face": self.max_captures_spin.value(),
            "capture_diff_threshold": self.capture_threshold_spin.value() / 100.0,
        }
