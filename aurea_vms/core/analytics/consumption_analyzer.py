"""Detección de incidentes, modo consumo: consumo de sustancias en un puesto.

Cada zona dibujada es un PUESTO (el jugador sentado frente a su maquina).
Sobre el puesto se estima la pose whole-body (RTMW, 133 puntos: cara y 21
por mano, ver pose_backend.WHOLEBODY_MODEL) y se sigue una secuencia:

1. **Preparacion -> alerta previa.** Las dos manos juntas, levantadas cerca
   del menton (no sobre la botonera) y con la cara visible, sostenido
   `preparation_min_s`. Es el armado: abrir un envoltorio, preparar la
   dosis, armar un cigarrillo.
2. **Consumo -> incidente.** Una mano (cualquier punto de los dedos) sobre
   la nariz o la boca en `gesture_samples` muestras seguidas, con la cara
   visible y, por defecto, despues de una preparacion reciente.

No se detecta la sustancia: se detecta el GESTO. Es una alerta para que el
operador mire el video, no una prueba.

Calibrado (2026-09-23/24) sobre dos clips cenitales 1080p, a 5 fps:
- "CONSUME SUSTANCIAS 01": preparacion a los 8-30 s y 40-52 s, gesto nasal
  a los 30,8-31,4 s, cigarrillo desde los 56 s. Distancia mano-nariz, en
  anchos de hombro: 0,43-0,86 durante la preparacion; 0,26 -> 0,10 -> 0,01
  -> 0,17 en el gesto nasal; 0,01-0,25 fumando. El modelo de cuerpo (17
  puntos) no veia el gesto nasal: con la mano tapando la cara ponia la
  muñeca en el pecho.
- Sala ESP SUB 08-5, dos jugadores normales: 0 tramos de preparacion. Uno,
  de espaldas y con la mano en la botonera, quedaba a 0,01-0,23 de la
  "nariz" por perspectiva. Lo separa la cara: la confianza media de sus
  68 puntos es 0,23-0,37 de espaldas contra 0,51-0,72 en los gestos reales
  (umbral 0,45).

Resultado con estos umbrales, a 5 fps:
- "CONSUME SUSTANCIAS 01": alerta previa a los 17,0 s y 42,0 s; incidente a
  los 31,2 s (gesto nasal) y 56,8 s (cigarrillo).
- 9 jugadores normales en 5 clips de la demo (~5,5 min): 0 alertas previas
  y 1 gesto crudo, una jugadora que se toca el menton. Con la preparacion
  previa exigida (el default) son 0 falsas alarmas.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer
from aurea_vms.core.analytics.monitor_tamper_analyzer import Rect, expand_rect
from aurea_vms.core.analytics.pose_backend import (
    FACE_POINTS,
    LEFT_HAND,
    LEFT_SHOULDER,
    NOSE,
    RIGHT_HAND,
    RIGHT_SHOULDER,
    WHOLEBODY_MODEL,
    PoseEstimator,
)
from aurea_vms.core.events import Detection

LABEL_CONSUMPTION = "consumo_sustancias"
LABEL_PREPARATION = "preparacion_consumo"
MOTIVE_CONSUMPTION = "consumo"
MOTIVE_PREPARATION = "preparacion"

DEFAULT_CROP_EXPANSION = 1.15  # el puesto ya se dibuja alrededor del jugador
DEFAULT_KEYPOINT_MIN_SCORE = 0.30
DEFAULT_FACE_MIN_SCORE = 0.45  # confianza media de los 68 puntos de la cara
DEFAULT_GESTURE_MAX = 0.20  # mano-nariz, en anchos de hombro
DEFAULT_GESTURE_SAMPLES = 2
DEFAULT_HANDS_GAP_MAX = 0.50  # entre las dos manos, en anchos de hombro
DEFAULT_HANDS_FACE_MAX = 0.80  # manos cerca del menton, no sobre la botonera
DEFAULT_PREPARATION_MIN_S = 5.0
DEFAULT_PREPARATION_MEMORY_S = 120.0
DEFAULT_ALERT_HOLD_S = 6.0
DEFAULT_RELEASE_S = 10.0

# La preparacion se decide sobre una ventana corta: la pose parpadea y una
# muestra suelta no debe cortar un armado de 20 segundos.
PREPARATION_WINDOW_S = 3.0
PREPARATION_WINDOW_RATIO = 0.6
PREPARATION_WINDOW_MIN_SAMPLES = 3
MIN_HAND_POINTS = 6


@dataclass(frozen=True)
class PoseReading:
    """Lo que las reglas necesitan de una pose, en anchos de hombro."""

    face_visible: bool
    hand_to_face: float | None  # punto de mano mas cercano a la nariz
    hands_gap: float | None  # entre los centros de las dos manos
    closest_hand_point: tuple[float, float] | None


def read_pose(
    points: np.ndarray,
    scores: np.ndarray,
    min_score: float = DEFAULT_KEYPOINT_MIN_SCORE,
    face_min_score: float = DEFAULT_FACE_MIN_SCORE,
) -> PoseReading | None:
    """None si no hay hombros para medir la escala de la persona."""
    if scores[LEFT_SHOULDER] < min_score or scores[RIGHT_SHOULDER] < min_score:
        return None
    shoulders = float(np.linalg.norm(points[LEFT_SHOULDER] - points[RIGHT_SHOULDER]))
    if shoulders < 1.0:
        return None
    face_visible = bool(
        scores[NOSE] >= min_score and float(np.mean(scores[list(FACE_POINTS)])) >= face_min_score
    )

    hand = [k for k in LEFT_HAND + RIGHT_HAND if scores[k] >= min_score]
    hand_to_face = closest = None
    if hand and scores[NOSE] >= min_score:
        distances = np.linalg.norm(points[hand] - points[NOSE], axis=1)
        nearest = int(np.argmin(distances))
        hand_to_face = float(distances[nearest]) / shoulders
        closest = (float(points[hand[nearest]][0]), float(points[hand[nearest]][1]))

    left = [k for k in LEFT_HAND if scores[k] >= min_score]
    right = [k for k in RIGHT_HAND if scores[k] >= min_score]
    hands_gap = None
    if len(left) >= MIN_HAND_POINTS and len(right) >= MIN_HAND_POINTS:
        gap = np.linalg.norm(points[left].mean(axis=0) - points[right].mean(axis=0))
        hands_gap = float(gap) / shoulders
    return PoseReading(face_visible, hand_to_face, hands_gap, closest)


def is_preparing(
    reading: PoseReading,
    hands_gap_max: float = DEFAULT_HANDS_GAP_MAX,
    hands_face_max: float = DEFAULT_HANDS_FACE_MAX,
) -> bool:
    return (
        reading.face_visible
        and reading.hands_gap is not None
        and reading.hand_to_face is not None
        and reading.hands_gap < hands_gap_max
        and reading.hand_to_face < hands_face_max
    )


def is_consumption_gesture(reading: PoseReading, gesture_max: float = DEFAULT_GESTURE_MAX) -> bool:
    return (
        reading.face_visible
        and reading.hand_to_face is not None
        and reading.hand_to_face < gesture_max
    )


@dataclass
class _SeatState:
    samples: deque = field(default_factory=deque)  # (t, preparando)
    preparing_since: float | None = None
    preparation_confirmed_at: float | None = None  # ultima vez con armado sostenido
    in_preparation: bool = False
    pre_alerts: int = 0
    pre_alert_started_at: float | None = None
    last_pre_alert_at: float | None = None
    streak: int = 0
    incidents: int = 0
    in_incident: bool = False
    incident_started_at: float | None = None
    last_gesture_at: float | None = None


class ConsumptionAnalyzer(Analyzer):
    name = "monitor_tamper"

    def __init__(
        self,
        zones: list[Rect],
        crop_expansion: float = DEFAULT_CROP_EXPANSION,
        keypoint_min_score: float = DEFAULT_KEYPOINT_MIN_SCORE,
        face_min_score: float = DEFAULT_FACE_MIN_SCORE,
        gesture_max: float = DEFAULT_GESTURE_MAX,
        gesture_samples: int = DEFAULT_GESTURE_SAMPLES,
        require_preparation: bool = True,
        preparation_min_s: float = DEFAULT_PREPARATION_MIN_S,
        preparation_memory_s: float = DEFAULT_PREPARATION_MEMORY_S,
        alert_hold_s: float = DEFAULT_ALERT_HOLD_S,
        release_s: float = DEFAULT_RELEASE_S,
        estimator: PoseEstimator | None = None,
    ) -> None:
        self._zones = [tuple(int(v) for v in zone) for zone in zones if len(zone) == 4]
        if not self._zones:
            raise ValueError("Detección de consumo necesita al menos un puesto")
        self._expansion = max(1.0, float(crop_expansion))
        self._min_score = float(keypoint_min_score)
        self._face_min = float(face_min_score)
        self._gesture_max = float(gesture_max)
        self._gesture_samples = max(1, int(gesture_samples))
        self._require_preparation = bool(require_preparation)
        self._preparation_min_s = max(0.0, float(preparation_min_s))
        self._preparation_memory_s = max(0.0, float(preparation_memory_s))
        self._hold_s = max(0.0, float(alert_hold_s))
        self._release_s = max(0.0, float(release_s))
        self._estimator = estimator or PoseEstimator(WHOLEBODY_MODEL)
        self._states = [_SeatState() for _ in self._zones]

    # --- secuencia por puesto ------------------------------------------------

    def _update_preparation(self, state: _SeatState, preparing: bool, now: float) -> bool:
        """Registra la muestra y devuelve True al EMPEZAR una alerta previa."""
        state.samples.append((now, preparing))
        while state.samples and now - state.samples[0][0] > PREPARATION_WINDOW_S:
            state.samples.popleft()
        ratio = sum(flag for _, flag in state.samples) / len(state.samples)
        sustained = (
            len(state.samples) >= PREPARATION_WINDOW_MIN_SAMPLES
            and ratio >= PREPARATION_WINDOW_RATIO
        )
        if not sustained:
            state.preparing_since = None
            state.in_preparation = False
            return False
        if state.preparing_since is None:
            state.preparing_since = now
        if now - state.preparing_since < self._preparation_min_s:
            return False
        state.preparation_confirmed_at = now
        state.last_pre_alert_at = now
        if state.in_preparation:
            return False
        state.in_preparation = True
        state.pre_alerts += 1
        state.pre_alert_started_at = state.preparing_since
        return True

    def _preparation_recent(self, state: _SeatState, now: float) -> bool:
        if not self._require_preparation:
            return True
        return (
            state.preparation_confirmed_at is not None
            and now - state.preparation_confirmed_at <= self._preparation_memory_s
        )

    def _update_gesture(self, state: _SeatState, gesture: bool, now: float) -> bool:
        """Registra la muestra y devuelve True al EMPEZAR un incidente."""
        if not gesture:
            state.streak = 0
            if (
                state.in_incident
                and state.last_gesture_at is not None
                and now - state.last_gesture_at > self._release_s
            ):
                state.in_incident = False
            return False
        state.streak += 1
        if state.streak < self._gesture_samples or not self._preparation_recent(state, now):
            return False
        state.last_gesture_at = now
        if state.in_incident:
            return False
        state.in_incident = True
        state.incidents += 1
        state.incident_started_at = now
        return True

    # --- analisis -----------------------------------------------------------

    def reset_counters(self) -> None:
        self._states = [_SeatState() for _ in self._states]

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        height, width = frame.shape[:2]
        detections: list[Detection] = []
        triggers: list[Detection] = []
        seats = []

        for zone, state in zip(self._zones, self._states, strict=True):
            pose = self._estimator.estimate(
                frame, expand_rect(zone, self._expansion, width, height)
            )
            reading = read_pose(pose.points, pose.scores, self._min_score, self._face_min)
            preparing = reading is not None and is_preparing(reading)
            gesture = reading is not None and is_consumption_gesture(reading, self._gesture_max)

            if self._update_preparation(state, preparing, timestamp):
                pre_alert = Detection(
                    label=LABEL_PREPARATION, confidence=1.0, bbox=zone, keypoints=()
                )
                detections.append(pre_alert)
                triggers.append(pre_alert)
            if self._update_gesture(state, gesture, timestamp):
                incident = Detection(
                    label=LABEL_CONSUMPTION,
                    confidence=1.0,
                    bbox=zone,
                    keypoints=(reading.closest_hand_point,) if reading.closest_hand_point else (),
                )
                detections.append(incident)
                triggers.append(incident)

            alert = (
                state.last_gesture_at is not None
                and timestamp - state.last_gesture_at <= self._hold_s
            )
            pre_alert_on = (
                state.last_pre_alert_at is not None
                and timestamp - state.last_pre_alert_at <= self._hold_s
            )
            if alert:
                status, motive, since = "alerta", MOTIVE_CONSUMPTION, state.incident_started_at
            elif pre_alert_on:
                status, motive, since = "previa", MOTIVE_PREPARATION, state.pre_alert_started_at
            else:
                status, motive, since = "normal", None, None
            seats.append(
                {
                    "estado": status,
                    "motivo": motive,
                    "desde": since,
                    "incidentes": state.incidents,
                    "previas": state.pre_alerts,
                }
            )

        states = {seat["estado"] for seat in seats}
        overall = "alerta" if "alerta" in states else "previa" if "previa" in states else "normal"
        last_incident = max(
            (
                (state.last_gesture_at, i)
                for i, state in enumerate(self._states)
                if state.last_gesture_at is not None
            ),
            default=None,
        )
        last_pre_alert = max(
            (
                (state.last_pre_alert_at, i)
                for i, state in enumerate(self._states)
                if state.last_pre_alert_at is not None
            ),
            default=None,
        )
        metrics = {
            "modo": "consumo",
            "zona_tipo": "puesto",
            "estado": overall,
            "zonas": seats,
            "incidentes": sum(state.incidents for state in self._states),
            "previas": sum(state.pre_alerts for state in self._states),
            "ultimo_incidente": (
                {"t": last_incident[0], "zona": last_incident[1], "motivo": MOTIVE_CONSUMPTION}
                if last_incident
                else None
            ),
            "ultima_previa": (
                {"t": last_pre_alert[0], "zona": last_pre_alert[1]} if last_pre_alert else None
            ),
            "contacto": False,
        }
        return AnalysisResult(
            detections=tuple(detections), metrics=metrics, triggers=tuple(triggers)
        )

    def close(self) -> None:
        self._estimator.close()
