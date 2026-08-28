from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, CheckBox, DoubleSpinBox, LineEdit, SpinBox

from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase
from aurea_vms.ui.labels import display_class

COMMON_CLASSES = ["person", "car", "motorcycle", "bicycle", "bus", "truck"]
FPS_RANGE = (1, 30)
DEFAULT_FPS = 10
FIELD_WIDTH = 130


class LineCrossingConfigDialog(AnalyticsConfigDialogBase):
    analyzer_name = "line_crossing"
    display_name = "Cruce de Línea"
    roi_mode = "line"

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        params = (existing.params if existing else {}) or {}
        selected = set(
            existing.object_classes if existing and existing.object_classes else ["person"]
        )

        self.class_checks: dict[str, CheckBox] = {}
        classes_row = QHBoxLayout()
        for cls in COMMON_CLASSES:
            check = CheckBox(display_class(cls))
            check.setChecked(cls in selected)
            self.class_checks[cls] = check
            classes_row.addWidget(check)
        classes_widget = QWidget()
        classes_widget.setLayout(classes_row)
        form.addRow("Clases a contar:", classes_widget)

        self.label_in_edit = LineEdit()
        self.label_in_edit.setText(params.get("label_in", "Entrada"))
        self.label_out_edit = LineEdit()
        self.label_out_edit.setText(params.get("label_out", "Salida"))
        form.addRow("Etiqueta sentido A→B:", self.label_in_edit)
        form.addRow("Etiqueta sentido B→A:", self.label_out_edit)

        self.confirmation_spin = SpinBox()
        self.confirmation_spin.setRange(1, 10)
        self.confirmation_spin.setMaximumWidth(FIELD_WIDTH)
        self.confirmation_spin.setValue(params.get("confirmation_frames", 2))
        self.confirmation_spin.setToolTip(
            "Un objeto solo dispara un cruce tras esta cantidad de muestras seguidas (1 = sin "
            "confirmación)."
        )
        form.addRow("Confirmación (muestras):", self.confirmation_spin)
        caption = CaptionLabel("Más alto = evita cruces falsos por ruido de un solo frame.")
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
        self.fps_spin.setValue(params.get("fps", DEFAULT_FPS))
        self.fps_spin.setToolTip(
            "Cuadros por segundo para esta cámara, independiente del FPS global del resto de los "
            "analizadores."
        )
        form.addRow("FPS de análisis:", self.fps_spin)

        line_label = BodyLabel("Dibujá la línea de cruce sobre la captura.")
        line_label.setWordWrap(True)
        form.addRow(line_label)

    def build_params(self) -> dict:
        line = self.selector_widget.get_line()
        return {
            "line": [list(line[0]), list(line[1])] if line else None,
            "label_in": self.label_in_edit.text().strip() or "Entrada",
            "label_out": self.label_out_edit.text().strip() or "Salida",
            "confirmation_frames": self.confirmation_spin.value(),
            "min_area_percent": self.min_size_spin.value(),
            "fps": self.fps_spin.value(),
        }

    def object_classes(self) -> list[str]:
        selected = [cls for cls, check in self.class_checks.items() if check.isChecked()]
        return selected or ["person"]

    def validate(self) -> str | None:
        # La línea se exige siempre, no solo con "habilitado" tildado: un
        # config persistido con line=None puede prenderse después desde el
        # switch del módulo Analíticas (o en el arranque), y ahí
        # create_analyzer no tiene línea con la que construir el analizador.
        if self.selector_widget.get_line() is None:
            return "Dibujá una línea de cruce sobre la captura antes de guardar."
        return None
