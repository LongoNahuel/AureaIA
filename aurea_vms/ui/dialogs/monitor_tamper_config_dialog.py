"""Configuracion de Incidentes en casinos, en sus cuatro modos:

- Ruleta: la rueda (primer rectangulo) y las zonas de fichas del paño. El
  boton "Detectar ruleta y paño" las ubica solas sobre ~3 s de video. Ver
  roulette_analyzer.py.
- BlackJack: pendiente; se calibra sobre video de una mesa real.
- Monitor roto: una zona por pantalla y la sensibilidad de las dos reglas
  (patada y golpe con la mano). Ver monitor_tamper_analyzer.py.
- Consumo de sustancias: una zona por puesto (el jugador sentado), con
  alerta previa por preparacion. Ver consumption_analyzer.py."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFormLayout, QHBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    FluentIcon,
    PushButton,
    Slider,
    SpinBox,
)

from aurea_vms.config.settings import settings
from aurea_vms.core.analytics.consumption_analyzer import (
    DEFAULT_ALERT_HOLD_S as CONSUMPTION_ALERT_HOLD_S,
)
from aurea_vms.core.analytics.consumption_analyzer import DEFAULT_PREPARATION_MIN_S
from aurea_vms.core.analytics.monitor_tamper_analyzer import (
    DEFAULT_ALERT_HOLD_S,
    DEFAULT_HAND_STRIKE_SPEED,
    DEFAULT_KEYPOINT_MIN_SCORE,
)
from aurea_vms.core.analytics.registry import (
    BLACKJACK_MODE,
    CONSUMPTION_MODE,
    ROULETTE_MODE,
    STRIKES_MODE,
)
from aurea_vms.core.analytics.roulette_analyzer import (
    DEFAULT_HANDS_FPS,
    WHEEL_MIN_FRAMES,
    WheelEllipse,
    detect_layout_grid,
    detect_wheel,
)
from aurea_vms.core.analytics.roulette_round import DEFAULT_NO_MORE_BETS_DEG_S
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.device import Device
from aurea_vms.ui.dialogs.analytics_config_dialog_base import AnalyticsConfigDialogBase

FIELD_WIDTH = 130
# Sensibilidad 1-100 <-> confianza minima de cada articulacion (0,50-0,20).
SCORE_RANGE = (0.20, 0.50)
MODES = (
    (ROULETTE_MODE, "Ruleta"),
    (BLACKJACK_MODE, "BlackJack (pendiente)"),
    (STRIKES_MODE, "Monitor roto (golpes a pantallas)"),
    (CONSUMPTION_MODE, "Consumo de sustancias"),
)
DEFAULT_FPS = {ROULETTE_MODE: 25, BLACKJACK_MODE: 5, STRIKES_MODE: 8, CONSUMPTION_MODE: 5}
# En ruleta los FPS de analisis son a los que se siguen las manos cuadro a
# cuadro (flujo optico, barato): hasta el stream, 60 como maximo. En los otros
# modos la pose corre en cada muestra y mas de 15 no los da el CPU.
MAX_FPS = {ROULETTE_MODE: 60, BLACKJACK_MODE: 15, STRIKES_MODE: 15, CONSUMPTION_MODE: 15}
DEFAULT_HOLD = {
    ROULETTE_MODE: DEFAULT_ALERT_HOLD_S,
    BLACKJACK_MODE: DEFAULT_ALERT_HOLD_S,
    STRIKES_MODE: DEFAULT_ALERT_HOLD_S,
    CONSUMPTION_MODE: CONSUMPTION_ALERT_HOLD_S,
}
INTRO = {
    ROULETTE_MODE: (
        "Tocá «Detectar ruleta y paño»: sobre unos segundos de video se ubican la rueda (por "
        "su giro) y la grilla de apuestas. El PRIMER rectángulo es la RUEDA; los siguientes son "
        "las ZONAS DE FICHAS. Podés redibujarlos. Se sigue la bola: cuando frena, se canta el "
        "no va más solo y se arma el paño; hasta que el crupier marca el número, mover fichas "
        "es una alerta."
    ),
    BLACKJACK_MODE: (
        "BlackJack todavía no está disponible: las reglas (fichas movidas después de repartir "
        "y manos sobre las cartas) se calibran sobre video de una mesa real."
    ),
    STRIKES_MODE: (
        "Dibujá un rectángulo sobre cada PANTALLA a vigilar, lo más ajustado posible a la "
        "pantalla (sin mesa ni botonera). Se alarma cuando un pie entra a la pantalla "
        "(patada) o cuando una mano llega a ella a gran velocidad (golpe). Los toques "
        "normales de una pantalla táctil no alarman."
    ),
    CONSUMPTION_MODE: (
        "Dibujá un rectángulo sobre cada PUESTO a vigilar, que tome al jugador sentado de la "
        "cintura para arriba (cara y manos). Hay alerta previa cuando arma algo con las dos "
        "manos cerca de la cara, e incidente cuando se lleva la mano a la nariz o a la boca. "
        "Se detecta el gesto, no la sustancia: el operador confirma mirando el video. Hace "
        "falta ver la cara; con jugadores de espaldas no sirve."
    ),
}
VALIDATION = {
    ROULETTE_MODE: "Marcá la rueda (primer rectángulo) o usá «Detectar ruleta y paño».",
    STRIKES_MODE: "Dibujá al menos un rectángulo sobre una pantalla.",
    CONSUMPTION_MODE: "Dibujá al menos un rectángulo sobre un puesto.",
}
# Muestras para detectar la rueda: una cada DETECTION_INTERVAL_MS.
DETECTION_INTERVAL_MS = 200


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
    display_name = "Incidentes en casinos"
    roi_mode = "rects"
    max_rects = 6
    show_confidence = False

    def __init__(self, device: Device, parent=None) -> None:
        super().__init__(device, parent)
        # La base carga params["zones"] en el selector; en ruleta el primer
        # rectangulo es la rueda y se guarda aparte ("rueda_zona").
        region = self._params.get("rueda_zona")
        if self._params.get("modo") == ROULETTE_MODE and region and len(region) == 4:
            zones = [tuple(zone) for zone in self._params.get("zones", []) if len(zone) == 4]
            self.selector_widget.set_initial_rects([tuple(region), *zones])

    def build_extra_fields(self, form: QFormLayout, existing: AnalyticsConfig | None) -> None:
        params = (existing.params if existing else {}) or {}
        self._params = params
        self._form = form
        mode = params.get("modo", STRIKES_MODE)
        if mode not in dict(MODES):
            mode = STRIKES_MODE
        self._wheel = WheelEllipse.from_list(params.get("rueda")) if params.get("rueda") else None
        self._wheel_region = tuple(params["rueda_zona"]) if params.get("rueda_zona") else None
        self._detection_frames: list = []
        self._detection_timer = QTimer(self)
        self._detection_timer.setInterval(DETECTION_INTERVAL_MS)
        self._detection_timer.timeout.connect(self._collect_detection_frame)

        self.mode_combo = ComboBox()
        for key, text in MODES:
            self.mode_combo.addItem(text, userData=key)
        self.mode_combo.setCurrentIndex([key for key, _ in MODES].index(mode))
        form.addRow("Qué vigilar:", self.mode_combo)

        self.intro_label = BodyLabel(INTRO[mode])
        self.intro_label.setWordWrap(True)
        form.addRow(self.intro_label)

        # --- ruleta -----------------------------------------------------------
        self.detect_button = PushButton(FluentIcon.SEARCH, "Detectar ruleta y paño")
        self.detect_button.clicked.connect(self._start_detection)
        self.detection_label = _caption(self._wheel_text())
        self._detect_row = QHBoxLayout()
        self._detect_row.addWidget(self.detect_button)
        self._detect_row.addWidget(self.detection_label, stretch=1)
        form.addRow(self._detect_row)

        self.table_hands_check = CheckBox("Marcar las manos del crupier y de los jugadores")
        self.table_hands_check.setChecked(bool(params.get("manos", True)))
        form.addRow(self.table_hands_check)
        self._table_hands_caption = _caption(
            "El crupier es quien toca la rueda. Las manos en el paño y en la rueda se marcan "
            "con los 21 puntos de la mano (muñeca y dedos)."
        )
        form.addRow(self._table_hands_caption)

        self.hands_fps_spin = SpinBox()
        self.hands_fps_spin.setRange(1, 15)
        self.hands_fps_spin.setSuffix(" /s")
        self.hands_fps_spin.setMaximumWidth(FIELD_WIDTH)
        self.hands_fps_spin.setValue(int(params.get("manos_fps", DEFAULT_HANDS_FPS)))
        self.hands_fps_spin.setToolTip(
            "Cada detección completa (personas y los 21 puntos de cada mano) cuesta 100-250 ms "
            "con 3-4 personas en la mesa. Si el CPU no llega, corre a lo que dé."
        )
        form.addRow("Detección completa de manos:", self.hands_fps_spin)
        self._hands_fps_caption = _caption(
            "Entre una detección completa y la siguiente, cada punto de cada dedo se sigue "
            "cuadro a cuadro a los FPS de análisis (hasta 60): las marcas acompañan al video."
        )
        form.addRow(self._hands_fps_caption)

        self.no_more_bets_spin = SpinBox()
        self.no_more_bets_spin.setRange(80, 600)
        self.no_more_bets_spin.setSingleStep(10)
        self.no_more_bets_spin.setSuffix(" °/s")
        self.no_more_bets_spin.setMaximumWidth(FIELD_WIDTH)
        self.no_more_bets_spin.setValue(
            int(params.get("no_va_mas_deg_s", DEFAULT_NO_MORE_BETS_DEG_S))
        )
        self.no_more_bets_spin.setToolTip(
            "La bola sale a ~500 °/s y cae al plato a ~120-145 °/s. Con 200 °/s el no va más "
            "llega 7 a 11 s antes de que caiga (medido en dos mesas)."
        )
        form.addRow("No va más con la bola bajo:", self.no_more_bets_spin)
        self._no_more_bets_caption = _caption(
            "Desde el no va más hasta que el crupier marca el número, mover fichas en el paño "
            "es una alerta (salvo que lo haga el crupier)."
        )
        form.addRow(self._no_more_bets_caption)

        # --- golpes a pantallas ---------------------------------------------
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
        self._sensitivity_row = QHBoxLayout()
        self._sensitivity_row.addWidget(self.sensitivity_slider, stretch=1)
        self._sensitivity_row.addWidget(self.sensitivity_value_label)
        form.addRow("Sensibilidad:", self._sensitivity_row)
        self._sensitivity_caption = _caption(
            "Más alta = detecta con partes del cuerpo menos visibles, a costa de más "
            "falsas alarmas. El valor por defecto está calibrado sobre video real."
        )
        form.addRow(self._sensitivity_caption)

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

        # --- consumo de sustancias ------------------------------------------
        self.require_preparation_check = CheckBox(
            "El incidente exige una preparación previa (menos falsas alarmas)"
        )
        self.require_preparation_check.setChecked(bool(params.get("require_preparation", True)))
        self.require_preparation_check.setToolTip(
            "Sin preparación previa, cualquier mano a la nariz con la cara visible (rascarse, "
            "sonarse) es un incidente."
        )
        form.addRow(self.require_preparation_check)

        self.preparation_spin = DoubleSpinBox()
        self.preparation_spin.setRange(2.0, 30.0)
        self.preparation_spin.setSingleStep(1.0)
        self.preparation_spin.setDecimals(0)
        self.preparation_spin.setSuffix(" s")
        self.preparation_spin.setMaximumWidth(FIELD_WIDTH)
        self.preparation_spin.setValue(params.get("preparation_min_s", DEFAULT_PREPARATION_MIN_S))
        self.preparation_spin.setToolTip(
            "Cuánto tiene que durar el armado (las dos manos juntas cerca de la cara) para "
            "dar la alerta previa."
        )
        form.addRow("Alerta previa tras:", self.preparation_spin)
        self._consumption_caption = _caption(
            "Cada puesto cuesta ~70-150 ms de CPU por muestra (pose de cuerpo, cara y "
            "manos): conviene vigilar 1 o 2 puestos por cámara, a 5 fps."
        )
        form.addRow(self._consumption_caption)

        # --- comunes ----------------------------------------------------------
        self.hold_spin = DoubleSpinBox()
        self.hold_spin.setRange(1.0, 60.0)
        self.hold_spin.setSingleStep(1.0)
        self.hold_spin.setDecimals(0)
        self.hold_spin.setSuffix(" s")
        self.hold_spin.setMaximumWidth(FIELD_WIDTH)
        self.hold_spin.setValue(params.get("alert_hold_s", DEFAULT_HOLD[mode]))
        form.addRow("Alerta visible durante:", self.hold_spin)

        self.fps_spin = SpinBox()
        self.fps_spin.setRange(3, MAX_FPS[mode])
        self.fps_spin.setMaximumWidth(FIELD_WIDTH)
        default_fps = DEFAULT_FPS[mode]
        if mode != ROULETTE_MODE:
            default_fps = min(default_fps, int(settings.analytics_fps))
        self.fps_spin.setValue(int(params.get("fps", default_fps)))
        self.fps_spin.setToolTip(
            "Un golpe dura décimas de segundo: con menos de 5 fps puede pasar entre dos "
            "muestras. El gesto de consumo dura ~0,5 s. En ruleta son los cuadros por segundo "
            "a los que se siguen las manos y la bola: hasta los del stream (máximo 60)."
        )
        form.addRow("FPS de análisis:", self.fps_spin)

        self._mode = mode
        self._apply_mode(mode)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

    def mode(self) -> str:
        return self.mode_combo.currentData() or STRIKES_MODE

    def _on_mode_changed(self, _index: int) -> None:
        mode = self.mode()
        # Los valores que siguen en el default del otro modo pasan al de este.
        if self.hold_spin.value() == DEFAULT_HOLD[self._mode]:
            self.hold_spin.setValue(DEFAULT_HOLD[mode])
        if self.fps_spin.value() == DEFAULT_FPS[self._mode]:
            self.fps_spin.setValue(DEFAULT_FPS[mode])
        self._mode = mode
        self._apply_mode(mode)

    def _apply_mode(self, mode: str) -> None:
        self.intro_label.setText(INTRO[mode])
        self.fps_spin.setMaximum(MAX_FPS[mode])
        rows = {
            ROULETTE_MODE: (
                self._detect_row,
                self.table_hands_check,
                self._table_hands_caption,
                self.hands_fps_spin,
                self._hands_fps_caption,
                self.no_more_bets_spin,
                self._no_more_bets_caption,
            ),
            STRIKES_MODE: (
                self._sensitivity_row,
                self._sensitivity_caption,
                self.hand_check,
                self.hand_speed_spin,
            ),
            CONSUMPTION_MODE: (
                self.require_preparation_check,
                self.preparation_spin,
                self._consumption_caption,
            ),
        }
        for owner, owned in rows.items():
            for row in owned:
                self._form.setRowVisible(row, owner == mode)

    # --- deteccion de la ruleta ----------------------------------------------

    def _wheel_text(self) -> str:
        if self._wheel is None:
            return "Rueda sin ubicar: se busca sola al arrancar la analítica."
        return (
            f"Rueda ubicada: centro ({self._wheel.cx:.0f}, {self._wheel.cy:.0f}), "
            f"{self._wheel.width:.0f}×{self._wheel.height:.0f} px."
        )

    def _start_detection(self) -> None:
        self._detection_frames = []
        self.detect_button.setEnabled(False)
        self.detection_label.setText("Mirando la mesa unos segundos…")
        self._detection_timer.start()

    def _collect_detection_frame(self) -> None:
        frame, _ = self._preview_worker.get_latest_frame_with_timestamp()
        if frame is not None:
            self._detection_frames.append(frame)
        if len(self._detection_frames) < WHEEL_MIN_FRAMES:
            return
        self._detection_timer.stop()
        self._finish_detection(self._detection_frames)

    def _finish_detection(self, frames: list) -> None:
        self.detect_button.setEnabled(True)
        wheel = detect_wheel(frames)
        grid = detect_layout_grid(frames[-1])
        if wheel is None:
            self.detection_label.setText(
                "No se encontró la rueda: tiene que estar girando y a la vista. "
                "Podés marcarla a mano (primer rectángulo)."
            )
            return
        self._wheel = wheel
        self._wheel_region = wheel.bounding_rect()
        rects = [self._wheel_region] + ([grid] if grid else [])
        self.selector_widget.set_initial_rects(rects)
        grid_text = "y la grilla del paño como zona 1" if grid else "(la grilla no se encontró)"
        self.detection_label.setText(f"{self._wheel_text()} Se propone {grid_text}.")

    # --- guardado ---------------------------------------------------------------

    def validate(self) -> str | None:
        mode = self.mode()
        if mode == BLACKJACK_MODE:
            return INTRO[BLACKJACK_MODE]
        if not self.selector_widget.get_rects():
            return VALIDATION[mode]
        return None

    def zone_params(self) -> dict:
        rects = [list(rect) for rect in self.selector_widget.get_rects()]
        if self.mode() != ROULETTE_MODE:
            return {"zones": rects}
        params = {"zones": rects[1:], "rueda_zona": rects[0] if rects else None}
        # La elipse detectada vale mientras el rectangulo de la rueda sea el
        # mismo; si se redibujo, el analizador la vuelve a buscar ahi adentro.
        if self._wheel is not None and rects and tuple(rects[0]) == tuple(self._wheel_region or ()):
            params["rueda"] = self._wheel.as_list()
        return params

    def build_params(self) -> dict:
        mode = self.mode()
        params = {
            "modo": mode,
            "alert_hold_s": self.hold_spin.value(),
            "fps": self.fps_spin.value(),
        }
        if mode == ROULETTE_MODE:
            params["manos"] = self.table_hands_check.isChecked()
            params["no_va_mas_deg_s"] = float(self.no_more_bets_spin.value())
            params["manos_fps"] = float(self.hands_fps_spin.value())
        elif mode == CONSUMPTION_MODE:
            params["require_preparation"] = self.require_preparation_check.isChecked()
            params["preparation_min_s"] = self.preparation_spin.value()
        elif mode == STRIKES_MODE:
            params["keypoint_min_score"] = sensitivity_to_score(self.sensitivity_slider.value())
            params["hand_strikes_enabled"] = self.hand_check.isChecked()
            params["hand_strike_speed"] = self.hand_speed_spin.value()
        return params

    def done(self, result: int) -> None:
        self._detection_timer.stop()
        super().done(result)
