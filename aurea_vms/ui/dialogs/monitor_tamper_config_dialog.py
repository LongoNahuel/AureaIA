"""Configuracion de Detección de incidentes: una zona por pantalla y la sensibilidad de
las dos reglas (patada y golpe con la mano). Ver
core/analytics/monitor_tamper_analyzer.py."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QHBoxLayout
from qfluentwidgets import BodyLabel, CaptionLabel, CheckBox, DoubleSpinBox, Slider, SpinBox

from aurea_vms.config.settings import settings
from aurea_vms.core.analytics.monitor_tamper_analyzer import (
    DEFAULT_ALERT_HOLD_S,
    DEFAULT_HAND_STRIKE_SPEED,
    DEFAULT_KEYPOINT_MIN_SCORE,
)
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase

FIELD_WIDTH = 130
# Sensibilidad 1-100 <-> confianza minima de cada articulacion (0,50-0,20).
SCORE_RANGE = (0.20, 0.50)


def sensitivity_to_score(sensitivity: int) -> float:
    low, high = SCORE_RANGE
    return round(high - (max(1, min(100, sensitivity)) - 1) / 99 * (high - low), 3)


def score_to_sensitivity(score: float) -> int:
    low, high = SCORE_RANGE
    score = max(low, min(high, score))
    return round((high - score) / (high - low) * 99 + 1)


def _caption(text: str) -> CaptionLabel:
    label = CaptionLabel(text)
    label.setWordWrap(True)
    return label


class MonitorTamperConfigDialog(AnalyticsConfigDialogBase):
    analyzer_name = "monitor_tamper"
    display_name = "Detección de incidentes"
    roi_mode = "rects"
    max_rects = 6
    show_confidence = False

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        params = (existing.params if existing else {}) or {}

        intro = BodyLabel(
            "Dibujá un rectángulo sobre cada PANTALLA a vigilar, lo más ajustado posible a la "
            "pantalla (sin mesa ni botonera). Se alarma cuando un pie entra a la pantalla "
            "(patada) o cuando una mano llega a ella a gran velocidad (golpe). Los toques "
            "normales de una pantalla táctil no alarman."
        )
        intro.setWordWrap(True)
        form.addRow(intro)

        self.sensitivity_slider = Slider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(1, 100)
        self.sensitivity_slider.setValue(
            score_to_sensitivity(params.get("keypoint_min_score", DEFAULT_KEYPOINT_MIN_SCORE))
        )
        self.sensitivity_value_label = BodyLabel(f"{self.sensitivity_slider.value()}%")
        self.sensitivity_value_label.setFixedWidth(42)
        self.sensitivity_slider.valueChanged.connect(
            lambda value: self.sensitivity_value_label.setText(f"{value}%")
        )
        sensitivity_row = QHBoxLayout()
        sensitivity_row.addWidget(self.sensitivity_slider, stretch=1)
        sensitivity_row.addWidget(self.sensitivity_value_label)
        form.addRow("Sensibilidad:", sensitivity_row)
        form.addRow(
            _caption(
                "Más alta = detecta con partes del cuerpo menos visibles, a costa de más "
                "falsas alarmas. El valor por defecto está calibrado sobre video real."
            )
        )

        self.hand_check = CheckBox("Detectar golpes con la mano")
        self.hand_check.setChecked(bool(params.get("hand_strikes_enabled", True)))
        form.addRow(self.hand_check)

        self.hand_speed_spin = DoubleSpinBox()
        self.hand_speed_spin.setRange(0.5, 10.0)
        self.hand_speed_spin.setSingleStep(0.1)
        self.hand_speed_spin.setDecimals(1)
        self.hand_speed_spin.setMaximumWidth(FIELD_WIDTH)
        self.hand_speed_spin.setValue(params.get("hand_strike_speed", DEFAULT_HAND_STRIKE_SPEED))
        self.hand_speed_spin.setToolTip(
            "Velocidad de la mano al llegar a la pantalla, en pantallas por segundo. Un toque "
            "normal va a 0,1 y el más rápido medido a 1,1."
        )
        self.hand_speed_spin.setEnabled(self.hand_check.isChecked())
        self.hand_check.stateChanged.connect(
            lambda _state: self.hand_speed_spin.setEnabled(self.hand_check.isChecked())
        )
        form.addRow("Velocidad de golpe:", self.hand_speed_spin)

        self.hold_spin = DoubleSpinBox()
        self.hold_spin.setRange(1.0, 60.0)
        self.hold_spin.setSingleStep(1.0)
        self.hold_spin.setDecimals(0)
        self.hold_spin.setSuffix(" s")
        self.hold_spin.setMaximumWidth(FIELD_WIDTH)
        self.hold_spin.setValue(params.get("alert_hold_s", DEFAULT_ALERT_HOLD_S))
        form.addRow("Alerta visible durante:", self.hold_spin)

        self.fps_spin = SpinBox()
        self.fps_spin.setRange(3, 15)
        self.fps_spin.setMaximumWidth(FIELD_WIDTH)
        self.fps_spin.setValue(int(params.get("fps", min(8, int(settings.analytics_fps)))))
        self.fps_spin.setToolTip(
            "Un golpe dura décimas de segundo: con menos de 5 fps puede pasar entre dos "
            "muestras. Cada pantalla cuesta ~20 ms de CPU por muestra."
        )
        form.addRow("FPS de análisis:", self.fps_spin)

    def validate(self) -> str | None:
        if not self.selector_widget.get_rects():
            return "Dibujá al menos un rectángulo sobre una pantalla."
        return None

    def build_params(self) -> dict:
        return {
            "keypoint_min_score": sensitivity_to_score(self.sensitivity_slider.value()),
            "hand_strikes_enabled": self.hand_check.isChecked(),
            "hand_strike_speed": self.hand_speed_spin.value(),
            "alert_hold_s": self.hold_spin.value(),
            "fps": self.fps_spin.value(),
        }
