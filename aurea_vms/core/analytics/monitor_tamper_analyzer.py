"""Detección de incidentes: golpes a la pantalla de una maquina de casino.

Reemplaza a "Puerta abierta/cerrada". Cada zona dibujada es un MONITOR (la
pantalla de un puesto). Sobre un recorte alrededor de cada monitor -- el
puesto con su jugador -- se estima la pose (core/analytics/pose_backend.py)
y se aplica una regla de intrusion por parte del cuerpo:

- **Patada**: un pie (tobillo) DENTRO de la pantalla. Un pie nunca esta
  ahi jugando, asi que es la señal fuerte. Para no disparar con una sola
  articulacion mal estimada, hacen falta al menos dos articulaciones de la
  pierna adentro, o un tobillo con confianza alta.
- **Golpe con la mano**: una muñeca que entra a la pantalla a gran
  velocidad. Las terminales son TACTILES: los jugadores tocan la pantalla
  todo el tiempo, asi que una mano adentro no alcanza; lo que distingue el
  golpe es la velocidad.

Calibrado sobre el clip de la demo "MONITOR ROTO 01" (cenital, 1080p):
- las patadas (56,4-58,0 s) se detectan; los toques normales de los dos
  jugadores (cientos de muestras) no disparan nada;
- las falsas alarmas que aparecian con reglas mas laxas eran siempre UNA
  articulacion (casi siempre una rodilla) con confianza 0,30-0,34;
- la muñeca en toques normales va a 0,03-0,11 diagonales de pantalla por
  segundo (maximo 1,13); el umbral de golpe queda en 2,0. El clip no trae
  un golpe real con la mano: ese umbral esta calibrado contra el juego
  normal, no contra un golpe verdadero.

Un ataque (varias patadas seguidas) es UN incidente: la alarma sale al
empezar (`triggers`) y el incidente se cierra tras `release_s` sin golpes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer
from aurea_vms.core.analytics.pose_backend import ANKLES, LEG_JOINTS, WRISTS, PoseEstimator
from aurea_vms.core.events import Detection

LABEL_KICK = "patada_monitor"
LABEL_HAND = "golpe_monitor"
MOTIVE_KICK = "patada"
MOTIVE_HAND = "golpe_mano"

DEFAULT_CROP_EXPANSION = 2.2
DEFAULT_KEYPOINT_MIN_SCORE = 0.30
DEFAULT_STRONG_ANKLE_SCORE = 0.45
DEFAULT_MIN_LEG_JOINTS = 2
DEFAULT_HAND_STRIKE_SPEED = 2.0  # diagonales de la pantalla por segundo
DEFAULT_ALERT_HOLD_S = 4.0
DEFAULT_RELEASE_S = 1.0
# Una muñeca vista hace mas que esto no sirve para medir velocidad.
MAX_WRIST_GAP_S = 0.6

Rect = tuple[int, int, int, int]


def expand_rect(zone: Rect, factor: float, width: int, height: int) -> tuple[float, ...]:
    """El recorte del puesto: la pantalla agrandada `factor` veces alrededor
    de su centro (asi entra el jugador), recortado al cuadro."""
    x, y, w, h = zone
    cx, cy = x + w / 2, y + h / 2
    half_w, half_h = w * factor / 2, h * factor / 2
    x0, y0 = max(0.0, cx - half_w), max(0.0, cy - half_h)
    x1, y1 = min(float(width), cx + half_w), min(float(height), cy + half_h)
    return (x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))


def point_in_rect(point, zone: Rect) -> bool:
    x, y, w, h = zone
    return x <= point[0] <= x + w and y <= point[1] <= y + h


@dataclass
class _ZoneState:
    incidents: int = 0
    in_incident: bool = False
    streak: int = 0
    last_hit_at: float | None = None
    incident_started_at: float | None = None
    motive: str | None = None
    prev_wrists: dict[int, tuple[float, float, float]] = field(default_factory=dict)


class MonitorTamperAnalyzer(Analyzer):
    name = "monitor_tamper"

    def __init__(
        self,
        zones: list[Rect],
        crop_expansion: float = DEFAULT_CROP_EXPANSION,
        keypoint_min_score: float = DEFAULT_KEYPOINT_MIN_SCORE,
        strong_ankle_score: float = DEFAULT_STRONG_ANKLE_SCORE,
        min_leg_joints: int = DEFAULT_MIN_LEG_JOINTS,
        hand_strikes_enabled: bool = True,
        hand_strike_speed: float = DEFAULT_HAND_STRIKE_SPEED,
        confirmation_frames: int = 1,
        alert_hold_s: float = DEFAULT_ALERT_HOLD_S,
        release_s: float = DEFAULT_RELEASE_S,
        estimator: PoseEstimator | None = None,
    ) -> None:
        self._zones = [tuple(int(v) for v in zone) for zone in zones if len(zone) == 4]
        if not self._zones:
            raise ValueError(
                "Detección de incidentes necesita al menos una zona sobre una pantalla"
            )
        self._expansion = max(1.0, float(crop_expansion))
        self._min_score = float(keypoint_min_score)
        self._strong_ankle = float(strong_ankle_score)
        self._min_leg_joints = max(1, int(min_leg_joints))
        self._hands = bool(hand_strikes_enabled)
        self._hand_speed = max(0.1, float(hand_strike_speed))
        self._confirmation = max(1, int(confirmation_frames))
        self._hold_s = max(0.0, float(alert_hold_s))
        self._release_s = max(0.0, float(release_s))
        self._estimator = estimator or PoseEstimator()
        self._states = [_ZoneState() for _ in self._zones]

    # --- reglas (puras, testeables sin modelo) ------------------------------

    def _leg_hit(self, zone: Rect, points: np.ndarray, scores: np.ndarray) -> list[int]:
        """Articulaciones de pierna dentro de la pantalla si hay patada, [] si no.

        Hace falta un PIE (tobillo) adentro: las rodillas solas no cuentan.
        Medido en el clip de la demo: una jugadora sentada con las piernas
        cruzadas deja las dos rodillas en el borde inferior de un rectangulo
        que, por estar la pantalla girada, incluye la mesa de abajo; en las
        patadas, en cambio, el tobillo esta siempre dentro de la pantalla."""
        inside = [
            joint
            for joint in LEG_JOINTS
            if scores[joint] >= self._min_score and point_in_rect(points[joint], zone)
        ]
        ankles = [joint for joint in inside if joint in ANKLES]
        if not ankles:
            return []
        strong_ankle = any(scores[joint] >= self._strong_ankle for joint in ankles)
        return inside if len(inside) >= self._min_leg_joints or strong_ankle else []

    def _hand_hit(
        self, zone: Rect, state: _ZoneState, points: np.ndarray, scores: np.ndarray, now: float
    ) -> list[int]:
        """Muñecas que entraron a la pantalla a velocidad de golpe."""
        diagonal = math.hypot(zone[2], zone[3]) or 1.0
        hits = []
        for joint in WRISTS:
            if scores[joint] < self._min_score:
                state.prev_wrists.pop(joint, None)
                continue
            x, y = float(points[joint][0]), float(points[joint][1])
            previous = state.prev_wrists.get(joint)
            state.prev_wrists[joint] = (x, y, now)
            if previous is None or not point_in_rect((x, y), zone):
                continue
            px, py, pt = previous
            elapsed = now - pt
            if elapsed <= 0 or elapsed > MAX_WRIST_GAP_S:
                continue
            speed = math.hypot(x - px, y - py) / diagonal / elapsed
            if speed >= self._hand_speed:
                hits.append(joint)
        return hits

    # --- analisis -----------------------------------------------------------

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        height, width = frame.shape[:2]
        detections: list[Detection] = []
        triggers: list[Detection] = []
        zone_metrics = []
        touching = False

        for zone, state in zip(self._zones, self._states, strict=True):
            pose = self._estimator.estimate(
                frame, expand_rect(zone, self._expansion, width, height)
            )
            points, scores = pose.points, pose.scores
            legs = self._leg_hit(zone, points, scores)
            hands = self._hand_hit(zone, state, points, scores, timestamp) if self._hands else []
            touching = touching or any(
                scores[j] >= self._min_score and point_in_rect(points[j], zone) for j in WRISTS
            )

            hit_joints = legs or hands
            if hit_joints:
                motive = MOTIVE_KICK if legs else MOTIVE_HAND
                label = LABEL_KICK if legs else LABEL_HAND
                state.streak += 1
                confidence = float(max(scores[j] for j in hit_joints))
                detection = Detection(
                    label=label,
                    confidence=min(1.0, confidence),
                    bbox=zone,
                    keypoints=tuple((float(points[j][0]), float(points[j][1])) for j in hit_joints),
                )
                detections.append(detection)
                if state.streak >= self._confirmation:
                    state.last_hit_at = timestamp
                    state.motive = motive
                    if not state.in_incident:
                        # Inicio de incidente: la unica muestra que alarma.
                        state.in_incident = True
                        state.incidents += 1
                        state.incident_started_at = timestamp
                        triggers.append(detection)
            else:
                state.streak = 0
                if (
                    state.in_incident
                    and state.last_hit_at is not None
                    and timestamp - state.last_hit_at > self._release_s
                ):
                    state.in_incident = False

            alert = state.last_hit_at is not None and timestamp - state.last_hit_at <= self._hold_s
            zone_metrics.append(
                {
                    "estado": "alerta" if alert else "normal",
                    "motivo": state.motive if alert else None,
                    "desde": state.incident_started_at if alert else None,
                    "ultimo_golpe": state.last_hit_at,
                    "incidentes": state.incidents,
                }
            )

        in_alert = [i for i, zone in enumerate(zone_metrics) if zone["estado"] == "alerta"]
        last = max(
            (
                (state.last_hit_at, i, state.motive)
                for i, state in enumerate(self._states)
                if state.last_hit_at is not None
            ),
            default=None,
        )
        metrics = {
            "estado": "alerta" if in_alert else "normal",
            "zonas": zone_metrics,
            "incidentes": sum(state.incidents for state in self._states),
            "ultimo_golpe": (
                {"t": last[0], "zona": last[1], "motivo": last[2]} if last is not None else None
            ),
            "contacto": touching,
        }
        return AnalysisResult(
            detections=tuple(detections), metrics=metrics, triggers=tuple(triggers)
        )

    def close(self) -> None:
        self._estimator.close()
