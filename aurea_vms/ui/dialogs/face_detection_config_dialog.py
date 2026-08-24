from __future__ import annotations

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QTimeEdit
from qfluentwidgets import BodyLabel, CaptionLabel, CheckBox, DoubleSpinBox, Slider, SpinBox, StrongBodyLabel

from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase

FPS_RANGE = (1, 30)
DEFAULT_FPS = 25
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
        self.sensitivity_value_label = BodyLabel(str(self.sensitivity_slider.value()))
        self.sensitivity_value_label.setFixedWidth(28)
        self.sensitivity_slider.valueChanged.connect(
            lambda v: self.sensitivity_value_label.setText(str(v))
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

        self.filter_angle_check = CheckBox("Filtrar por ángulo (descartar perfiles marcados)")
        self.filter_angle_check.setChecked(bool(params.get("filter_by_angle", False)))
        form.addRow(self.filter_angle_check)

        self.confirmation_spin = SpinBox()
        self.confirmation_spin.setRange(*CONFIRMATION_RANGE)
        self.confirmation_spin.setMaximumWidth(FIELD_WIDTH)
        self.confirmation_spin.setValue(params.get("confirmation_frames", DEFAULT_CONFIRMATION_FRAMES))
        self.confirmation_spin.setToolTip(
            "Cuadros seguidos que una cara debe sostenerse para contar como detección real (1 = al "
            "instante). Se suma a la validación geométrica de puntos de referencia, siempre activa."
        )
        form.addRow("Confirmación (frames):", self.confirmation_spin)
        form.addRow(_caption("Más alto = menos falsos disparos, pero tarda un poco más en marcar."))

        # ======================= Captura y conteo =========================
        _section_header(form, "Captura y conteo")

        self.counting_check = CheckBox("Contador de capturas habilitado")
        self.counting_check.setChecked(bool(params.get("counting_enabled", True)))
        form.addRow(self.counting_check)

        self.reset_time_edit = QTimeEdit()
        self.reset_time_edit.setDisplayFormat("HH:mm")
        self.reset_time_edit.setMaximumWidth(FIELD_WIDTH)
        reset_time = params.get("counting_reset_time", "00:00")
        hh, mm = (reset_time.split(":") + ["0", "0"])[:2]
        self.reset_time_edit.setTime(QTime(int(hh), int(mm)))
        form.addRow("Reiniciar contador a las:", self.reset_time_edit)

        self.capture_threshold_spin = DoubleSpinBox()
        self.capture_threshold_spin.setRange(5.0, 90.0)
        self.capture_threshold_spin.setSingleStep(5.0)
        self.capture_threshold_spin.setSuffix(" %")
        self.capture_threshold_spin.setMaximumWidth(FIELD_WIDTH)
        self.capture_threshold_spin.setValue(params.get("capture_diff_threshold", 0.35) * 100)
        self.capture_threshold_spin.setToolTip(
            "Qué tan distinto debe verse un rostro para catalogarlo como un ID nuevo en vez de "
            "actualizar la captura ya guardada de ese ID."
        )
        form.addRow("Umbral de ID nuevo:", self.capture_threshold_spin)
        form.addRow(_caption("Más alto = más estricto (menos IDs nuevos); más bajo = más sensible."))

        self.fps_spin = SpinBox()
        self.fps_spin.setRange(*FPS_RANGE)
        self.fps_spin.setMaximumWidth(FIELD_WIDTH)
        self.fps_spin.setValue(params.get("fps", DEFAULT_FPS))
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
            "filter_by_angle": self.filter_angle_check.isChecked(),
            "confirmation_frames": self.confirmation_spin.value(),
            "counting_enabled": self.counting_check.isChecked(),
            "counting_reset_time": self.reset_time_edit.time().toString("HH:mm"),
            "capture_diff_threshold": self.capture_threshold_spin.value() / 100.0,
            "fps": self.fps_spin.value(),
        }
