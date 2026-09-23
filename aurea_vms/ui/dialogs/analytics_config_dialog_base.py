"""Base compartida por los dialogos de configuracion de cada tipo de
analitica: carga un snapshot de la camara, deja dibujar ROI/linea sobre
el, y persiste un AnalyticsConfig via upsert_analytics_config.

Cada subclase define: analyzer_name, display_name, roi_mode ('rect',
'line' o None) y build_extra_fields()/build_params() para sus campos
propios (sensibilidad, clases a contar, etiquetas, etc.).

El selector puede expandirse a pantalla completa para dibujar reglas sobre
el flujo principal con mayor comodidad; la selección permanece en el mismo
widget y se conserva al volver al formulario.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    FluentIcon,
    PrimaryPushButton,
    PushButton,
    Slider,
)

from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.device import Device
from aurea_vms.ui.notify import warn
from aurea_vms.ui.widgets.frame_selector import FrameSelectorWidget


class AnalyticsConfigDialogBase(QDialog):
    analyzer_name: str = ""
    display_name: str = ""
    roi_mode: str | None = "rect"
    max_rects: int = 1
    show_confidence: bool = True

    def __init__(self, device: Device, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.device = device
        self.setWindowTitle(f"{self.display_name} — {device.name}")
        self.resize(680, 600)
        self._video_expanded = False
        # Techo duro de ancho: un CaptionLabel/BodyLabel de descripcion sin
        # wordWrap fuerza un minimumSizeHint enorme en la fila del
        # QFormLayout (todo el texto en una sola linea), lo que termina
        # estirando el dialogo entero mucho mas alla de los 680px pedidos
        # arriba. Ademas de que cada subclase envuelva sus textos largos,
        # este limite evita que el dialogo vuelva a "explotar" en ancho si
        # alguna se olvida.
        self.setMaximumWidth(820)

        existing = repository.get_analytics_config_for(device.id, self.analyzer_name)
        self.enabled_check = CheckBox("Analizador habilitado")
        self.enabled_check.setChecked(existing.enabled if existing else False)

        top_form = QFormLayout()
        top_form.addRow(self.enabled_check)

        self.confidence_slider: Slider | None = None
        self.confidence_value_label: BodyLabel | None = None
        if self.show_confidence:
            self.confidence_slider = Slider()
            self.confidence_slider.setOrientation(Qt.Orientation.Horizontal)
            self.confidence_slider.setRange(1, 100)
            initial = round((existing.confidence_threshold if existing else 0.5) * 100)
            self.confidence_slider.setValue(initial)
            self.confidence_value_label = BodyLabel(f"{initial}%")
            self.confidence_value_label.setFixedWidth(42)
            self.confidence_slider.valueChanged.connect(
                lambda value: self.confidence_value_label.setText(f"{value}%")
            )
            confidence_row = QHBoxLayout()
            confidence_row.addWidget(self.confidence_slider, stretch=1)
            confidence_row.addWidget(self.confidence_value_label)
            top_form.addRow("Confianza mínima:", confidence_row)

        self.extra_form = QFormLayout()
        self.build_extra_fields(self.extra_form, existing)

        self.selector_widget = FrameSelectorWidget(
            self.roi_mode or "rect", self, max_rects=self.max_rects
        )
        if existing is not None and self.roi_mode == "rect":
            roi = None
            if None not in (existing.roi_x, existing.roi_y, existing.roi_w, existing.roi_h):
                roi = (existing.roi_x, existing.roi_y, existing.roi_w, existing.roi_h)
            self.selector_widget.set_initial_rect(roi)
        elif existing is not None and self.roi_mode == "rects":
            zones = (existing.params or {}).get("zones", [])
            self.selector_widget.set_initial_rects(
                [tuple(zone) for zone in zones if len(zone) == 4]
            )
        elif existing is not None and self.roi_mode == "line":
            line = (existing.params or {}).get("line")
            if line:
                self.selector_widget.set_initial_line((tuple(line[0]), tuple(line[1])))

        self.snapshot_status = BodyLabel("")
        self.snapshot_status.setWordWrap(True)
        refresh_button = PushButton(FluentIcon.SYNC, "Actualizar captura")
        refresh_button.clicked.connect(self._refresh_live_preview)
        self.expand_video_button = PushButton(FluentIcon.FULL_SCREEN, "Expandir video")
        self.expand_video_button.setToolTip(
            "Maximiza el flujo para dibujar las reglas con mayor precisión."
        )
        self.expand_video_button.clicked.connect(self._toggle_video_expanded)
        clear_button = PushButton(FluentIcon.BROOM, "Limpiar selección")
        clear_button.clicked.connect(self.selector_widget.clear_selection)

        snapshot_row = QHBoxLayout()
        snapshot_row.addWidget(refresh_button)
        snapshot_row.addWidget(self.expand_video_button)
        snapshot_row.addWidget(clear_button)
        snapshot_row.addStretch(1)
        snapshot_row.addWidget(self.snapshot_status)

        cancel_button = PushButton("Cancelar")
        save_button = PrimaryPushButton(FluentIcon.SAVE, "Guardar")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self.accept)
        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)
        buttons_row.addWidget(cancel_button)
        buttons_row.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top_form)
        layout.addLayout(self.extra_form)
        layout.addLayout(snapshot_row)
        layout.addWidget(self.selector_widget, stretch=1)
        layout.addLayout(buttons_row)

        self._preview_worker = stream_manager.acquire(self.device, "main")
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._refresh_live_preview)
        self._preview_timer.start()
        self.snapshot_status.setText("Conectando al flujo en vivo...")

    # --- hooks para las subclases -------------------------------------------------

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        return None

    def build_params(self) -> dict:
        return {}

    def object_classes(self) -> list[str] | None:
        return None

    def validate(self) -> str | None:
        return None

    def confidence_threshold_value(self) -> float:
        """Hook para subclases que reemplazan el spinner generico de
        confianza por su propio control (ej. un slider de "Sensibilidad"
        0-100 que internamente se mapea a un umbral 0-1)."""
        return self.confidence_slider.value() / 100 if self.confidence_slider else 0.5

    # --- comportamiento comun -------------------------------------------------

    def accept(self) -> None:
        error = self.validate()
        if error:
            warn(self, "Datos incompletos", error)
            return
        super().accept()

    def roi_fields(self) -> dict:
        if self.roi_mode == "rect":
            rect = self.selector_widget.get_rect()
            if rect is None:
                return {"roi_x": None, "roi_y": None, "roi_w": None, "roi_h": None}
            x, y, w, h = rect
            return {"roi_x": x, "roi_y": y, "roi_w": w, "roi_h": h}
        return {"roi_x": None, "roi_y": None, "roi_w": None, "roi_h": None}

    def zone_params(self) -> dict:
        if self.roi_mode != "rects":
            return {}
        return {"zones": [list(rect) for rect in self.selector_widget.get_rects()]}

    def save(self) -> AnalyticsConfig:
        fields: dict = {
            "enabled": self.enabled_check.isChecked(),
            "confidence_threshold": self.confidence_threshold_value(),
            "params": {**self.build_params(), **self.zone_params()},
            **self.roi_fields(),
        }
        object_classes = self.object_classes()
        if object_classes is not None:
            fields["object_classes"] = object_classes
        return repository.upsert_analytics_config(self.device.id, self.analyzer_name, **fields)

    def _refresh_live_preview(self) -> None:
        frame, _timestamp = self._preview_worker.get_latest_frame_with_timestamp()
        if frame is not None:
            self.snapshot_status.setText("Flujo en vivo")
            self.selector_widget.set_frame(frame)

    def _toggle_video_expanded(self) -> None:
        if self._video_expanded:
            self._video_expanded = False
            self.expand_video_button.setText("Expandir video")
            self.expand_video_button.setIcon(FluentIcon.FULL_SCREEN)
            self.selector_widget.setMinimumSize(320, 180)
            self.setMaximumWidth(820)
            self.showNormal()
            self.resize(680, 600)
            return

        self._video_expanded = True
        self.expand_video_button.setText("Restaurar formulario")
        self.expand_video_button.setIcon(FluentIcon.MINIMIZE)
        self.selector_widget.setMinimumSize(640, 420)
        self.setMaximumWidth(16777215)
        self.showMaximized()
        self.selector_widget.setFocus()

    def closeEvent(self, event) -> None:  # noqa: N802 - override de Qt
        self._preview_timer.stop()
        stream_manager.release(self.device.id, "main")
        super().closeEvent(event)
