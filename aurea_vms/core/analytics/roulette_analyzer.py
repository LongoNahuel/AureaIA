"""Incidentes en casinos, modo ruleta: la rueda, su velocidad y las zonas de
fichas del paño.

- **Rueda**: se ubica sola. El plato gira siempre y el tazón queda quieto,
  asi que la diferencia entre cuadros dibuja el plato; se toma el percentil
  bajo del movimiento (una persona se mueve de a ratos, el plato en todos
  los pares de cuadros) y se ajusta una elipse (`detect_wheel`).
- **Velocidad** (grados por segundo, rpm y sentido): la elipse se lleva a un
  circulo y el anillo de numeros se "desenrolla" en una tira angulo x radio.
  El cero verde da el giro grueso sin ambigüedad, y la correlacion de la
  tira entre muestras lo afina. La correlacion sola no alcanza: los
  bolsillos se repiten cada 9,73 grados (360/37) y a 5 fps el plato gira
  ~26 grados entre muestras.
- **Paño**: la grilla de numeros se ubica por densidad de bordes dentro de
  la superficie clara de la mesa (`detect_layout_grid`).
- **Zonas de fichas**: por zona, si hay manos moviendose (interaccion) y, cuando
  la zona vuelve a quedar quieta, si cambiaron las fichas respecto de la
  ultima vista quieta (diferencia de color en Lab: las fichas claras casi no
  contrastan en gris con el paño crema).

Calibrado (2026-09-30) sobre el clip de la demo "Ruleta AR 4171" (2048x1536,
25 fps, 42 s):
- rueda: centro (935, 1007) y ejes 433x516 px, estable a +-5 px en cuatro
  ventanas del clip;
- velocidad: 135 -> 120 grados/s (~22 rpm, antihorario) desacelerando por
  rozamiento; ruido de +-12 grados/s por muestra a 5 fps y +-5 en el promedio
  de 1 s. El cero no se ve en el 15% de las muestras: ahi se predice con la
  ultima velocidad y se afina por correlacion;
- fichas: sin cambios reales la zona cambia 0,15-0,26%; la limpieza del paño
  3,1% y las apuestas nuevas 1,1% (umbral 0,5%).

**Resultado (bola en el bolsillo): todavia no.** En el clip la bola cae hacia
los 21-22 s y se ve, en el marco del plato, como una anomalia fija en la
banda de bolsillos; pero con una sola tirada no se pudo calibrar una regla
en vivo sin falsos. Sin resultado no hay incidente de "fichas tras el
resultado": este modo mide y muestra.
"""

from __future__ import annotations

import logging
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import cv2
import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer
from aurea_vms.core.analytics.hand_flow import flow_gray, track_people
from aurea_vms.core.analytics.roulette_round import (
    DEFAULT_NO_MORE_BETS_DEG_S,
    ROUND_RESULT,
    BallReading,
    BallTracker,
    RotorWatcher,
    RouletteRound,
)
from aurea_vms.core.analytics.roulette_round import RADIUS as BALL_RADIUS
from aurea_vms.core.analytics.table_hands import ROLE_CROUPIER, ROLE_PLAYER, TableHands
from aurea_vms.core.events import Detection

logger = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]

POCKETS = 37
POCKET_DEG = 360.0 / POCKETS
# Tira del anillo: circulo normalizado de radio RING_RADIUS y ANGLE_BINS
# columnas (medio grado cada una).
RING_RADIUS = 200
ANGLE_BINS = 720
NUMBERS_BAND = (0.72, 0.97)  # fraccion del radio del plato
# El cero: verde saturado (HSV de OpenCV).
ZERO_HUE = (45, 95)
ZERO_MIN_SAT = 60
ZERO_MIN_VALUE = 60
ZERO_MIN_PIXELS = 3
# La correlacion solo busca cerca del giro grueso: los picos vecinos estan a
# un bolsillo (9,73 grados).
REFINE_WINDOW_DEG = 5.0
SPEED_WINDOW_S = 1.0
# Deteccion de la rueda por movimiento.
WHEEL_SCALE = 0.5
WHEEL_MOTION_PERCENTILE = 10
WHEEL_MOTION_THRESHOLD = 5.0
WHEEL_MIN_FRAMES = 15  # ~3 s a 5 fps
WHEEL_MIN_RATIO = 0.6  # eje menor / mayor: la camara no mira de canto
# Un candidato es la rueda solo si su anillo muestra el cero verde en al
# menos esta fraccion de los cuadros (una persona que se mueve no lo tiene).
WHEEL_ZERO_FRACTION = 0.4
# Zonas de fichas.
ZONE_SCALE = 0.5
ZONE_MOTION_LEVEL = 18  # nivel de gris entre muestras
ZONE_MOTION_FRACTION = 0.01
ZONE_QUIET_S = 1.0
ZONE_CHANGE_DELTA_E = 12.0
ZONE_CHANGE_FRACTION = 0.005
# Donde cambiaron las fichas: manchas del cambio de al menos esta area (en
# pixeles de la zona a ZONE_SCALE), las mas grandes primero.
CHIP_SPOT_MIN_AREA = 40
CHIP_SPOTS_MAX = 8
# Una ficha suelta. Crema sobre el paño crema cambia solo el 0,15% de la zona
# en color (umbral 0,5%): se mira tambien el borde (gradiente), y se buscan
# manchas del tamaño y la forma de una ficha. Medido (2026-09-30) sobre la
# RA-04 pegando una ficha roja, una crema y una verde: manchas compactas de
# 340-444 px (a ZONE_SCALE); sin fichas nuevas, lo mas parecido a una ficha
# fue una mancha de 201 px y alargada (2,0), y los brazos del borde de la
# zona dan manchas de mas de 1000 px muy alargadas (8-9).
CHIP_DIAMETER_WHEEL = 0.095  # diametro de una ficha / ancho del plato (en las dos mesas)
CHIP_DEFAULT_DIAMETER = 44  # px del cuadro, sin rueda ubicada
CHIP_MIN_FILL = 0.8  # area minima: esta fraccion del disco de una ficha
CHIP_MAX_CHIPS = 8  # area maxima: tantas fichas juntas
CHIP_MAX_ASPECT = 2.2
CHIP_MIN_SOLIDITY = 0.45
ZONE_CHANGE_GRADIENT = 40.0
# La deteccion completa de las manos (YOLOX + pose: 100-250 ms con 3-4
# personas) corre en su propio hilo hasta DEFAULT_HANDS_FPS veces por segundo
# (configurable: "manos_fps"); en cada cuadro, al ritmo del stream, las manos
# se siguen con flujo optico (hand_flow). La bola, la rueda y las zonas corren
# en cada cuadro.
DEFAULT_HANDS_FPS = 5.0
HANDS_RATE_SMOOTHING = 0.2
# La rueda, la bola y las zonas no ganan nada por encima de 25 muestras por
# segundo (la bola se midio igual a 5, 10 y 15 fps); las manos si, que se ven
# moverse. Con un stream de 60 fps, la escena corre a 25 y las manos a 60.
SCENE_MAX_FPS = 25.0
# El recuadro de cada mano (lo que dibuja el video): los 21 puntos con un
# margen de HAND_BOX_PAD de su tamaño, o, con la mano de un punto, un cuadrado
# de HAND_BOX_SINGLE diagonales de la persona; nunca menos de HAND_BOX_MIN. El
# centro sigue a la mano casi sin demora (el flujo ya es por cuadro) y el
# tamaño se suaviza, para que el recuadro no "respire" con cada deteccion
# completa. Un salto de mas de un recuadro es otra mano: arranca de cero.
HAND_BOX_PAD = 0.2
HAND_BOX_SINGLE = 0.1
HAND_BOX_MIN = 0.05
HAND_BOX_CENTER_SMOOTHING = 0.75
HAND_BOX_SIZE_SMOOTHING = 0.3
HAND_BOX_FORGET_S = 1.0
# La mesa (para saber quien es jugador): el paño a 1/4, agrandado un 5% del
# ancho del cuadro, y se vuelve a mirar cada TABLE_REFRESH_S (la gente tapa
# partes: se suma lo nuevo).
TABLE_SCALE = 0.25
TABLE_MARGIN = 0.05
TABLE_REFRESH_S = 10.0
DETAIL_ZONE_MARGIN = 0.15
DEFAULT_ALERT_HOLD_S = 5.0
LABEL_PAST_POST = "fichas_tras_no_va_mas"
MOTIVE_PAST_POST = "no_va_mas"
# Una mano "toca la rueda" dentro de la elipse del plato agrandada: al
# impulsarla o lanzar la bola se toca el borde del tazon y la pista de la
# bola, por fuera del plato que gira. Medido en la RA-04 (2026-09-30), en
# radios del plato: el crupier impulsando, 1,18-1,29; el crupier sin tocarla,
# 1,40 o mas; los jugadores, 2,4 o mas. Con 1,1 no se identificaba nunca.
WHEEL_TOUCH_SCALE = 1.35


@dataclass(frozen=True)
class WheelEllipse:
    """La elipse del plato en pixeles del cuadro (convencion de cv2.fitEllipse)."""

    cx: float
    cy: float
    width: float
    height: float
    angle: float

    def as_list(self) -> list[float]:
        return [round(v, 1) for v in (self.cx, self.cy, self.width, self.height, self.angle)]

    @classmethod
    def from_list(cls, values) -> WheelEllipse | None:
        try:
            cx, cy, width, height, angle = (float(v) for v in values)
        except (TypeError, ValueError):
            return None
        return cls(cx, cy, width, height, angle) if width > 0 and height > 0 else None

    def bounding_rect(self) -> Rect:
        box = cv2.boxPoints(((self.cx, self.cy), (self.width, self.height), self.angle))
        x, y, w, h = cv2.boundingRect(box.astype(np.float32))
        return (int(x), int(y), int(w), int(h))


# --- geometria -------------------------------------------------------------


def detect_wheel(frames: list[np.ndarray], region: Rect | None = None) -> WheelEllipse | None:
    """El plato de la ruleta a partir de ~1 s de cuadros seguidos (lo que gira
    en todos los pares de cuadros). `region` acota la busqueda."""
    if len(frames) < WHEEL_MIN_FRAMES:
        return None
    x0 = y0 = 0
    originals = frames
    if region is not None:
        x0, y0, w, h = region
        frames = [frame[y0 : y0 + h, x0 : x0 + w] for frame in frames]
    gray = [
        cv2.cvtColor(
            cv2.resize(frame, None, fx=WHEEL_SCALE, fy=WHEEL_SCALE), cv2.COLOR_BGR2GRAY
        ).astype(np.float32)
        for frame in frames
    ]
    diffs = np.stack([np.abs(gray[i + 1] - gray[i]) for i in range(len(gray) - 1)])
    motion = np.percentile(diffs, WHEEL_MOTION_PERCENTILE, axis=0)
    mask = (motion > WHEEL_MOTION_THRESHOLD).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    candidates: list[tuple[float, WheelEllipse]] = []
    for contour in contours:
        if len(contour) < 50:
            continue
        (cx, cy), (w, h), angle = cv2.fitEllipse(contour)
        area = np.pi * w * h / 4
        if area < 2000 or min(w, h) / max(w, h) < WHEEL_MIN_RATIO:
            continue
        fill = cv2.contourArea(cv2.convexHull(contour)) / area
        score = area * max(0.0, 1.0 - abs(1.0 - fill) * 2.0)
        ellipse = WheelEllipse(
            cx / WHEEL_SCALE + x0, cy / WHEEL_SCALE + y0, w / WHEEL_SCALE, h / WHEEL_SCALE, angle
        )
        candidates.append((score, ellipse))
    full = originals if region is not None else frames
    for _, ellipse in sorted(candidates, key=lambda c: -c[0]):
        meter = WheelSpeedMeter(ellipse)
        seen = sum(zero_angle(meter.ring(frame)) is not None for frame in full)
        if seen >= WHEEL_ZERO_FRACTION * len(full):
            return ellipse
    return None


def _felt(small: np.ndarray) -> np.ndarray | None:
    """La superficie clara y poco saturada mas grande: el paño de la mesa."""
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    felt = ((hsv[..., 2] > 130) & (hsv[..., 1] < 60)).astype(np.uint8) * 255
    felt = cv2.morphologyEx(felt, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(felt)
    if count < 2:
        return None
    return labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))


def detect_table(frame: np.ndarray) -> np.ndarray | None:
    """La mesa a TABLE_SCALE: el paño agrandado TABLE_MARGIN (la baranda, donde
    los jugadores apoyan las manos). Un espectador no la toca."""
    felt = _felt(cv2.resize(frame, None, fx=TABLE_SCALE, fy=TABLE_SCALE))
    if felt is None:
        return None
    margin = max(1, int(TABLE_MARGIN * frame.shape[1] * TABLE_SCALE))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1))
    return cv2.dilate(felt.astype(np.uint8), kernel) > 0


def detect_layout_grid(frame: np.ndarray) -> Rect | None:
    """La grilla de numeros del paño: la zona de bordes densos dentro de la
    superficie clara y poco saturada de la mesa."""
    scale = 0.25
    small = cv2.resize(frame, None, fx=scale, fy=scale)
    felt = _felt(small)
    if felt is None:
        return None
    edges = cv2.Canny(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 60, 160)
    edges[~felt] = 0
    density = cv2.blur((edges > 0).astype(np.float32), (15, 15))
    grid = cv2.morphologyEx(
        (density > 0.18).astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8)
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(grid)
    if count < 2:
        return None
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(v / scale) for v in stats[best, :4])
    return (x, y, w, h) if w * h > 0 else None


def rectify_matrix(wheel: WheelEllipse, radius: int = RING_RADIUS) -> np.ndarray:
    """Afin que lleva la elipse del plato a un circulo de radio `radius`
    centrado en (radius, radius), sin girar la imagen."""
    angle = np.deg2rad(wheel.angle)
    rot = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
    scale = np.diag([radius / (wheel.width / 2), radius / (wheel.height / 2)])
    matrix = rot.T @ scale @ rot
    offset = np.array([radius, radius]) - matrix @ np.array([wheel.cx, wheel.cy])
    return np.hstack([matrix, offset[:, None]])


def zero_angle(strip: np.ndarray) -> float | None:
    """Angulo (grados) del bolsillo verde en la tira, o None si no se ve."""
    hsv = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
    green = (
        (hsv[..., 0] > ZERO_HUE[0])
        & (hsv[..., 0] < ZERO_HUE[1])
        & (hsv[..., 1] > ZERO_MIN_SAT)
        & (hsv[..., 2] > ZERO_MIN_VALUE)
    ).sum(axis=1)
    if green.max() < ZERO_MIN_PIXELS:
        return None
    smoothed = np.convolve(np.r_[green, green[:20]], np.ones(21), "valid")[:ANGLE_BINS]
    return ((int(np.argmax(smoothed)) + 10) % ANGLE_BINS) * 360.0 / ANGLE_BINS


def ring_profile(strip: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY).astype(np.float32).mean(axis=1)
    return (gray - gray.mean()) / (gray.std() + 1e-6)


def refine_shift(previous: np.ndarray, current: np.ndarray, coarse_deg: float) -> float:
    """Pico de correlacion circular mas alto a +-REFINE_WINDOW_DEG del giro grueso."""
    corr = np.fft.ifft(np.fft.fft(current) * np.conj(np.fft.fft(previous))).real
    lags = (np.arange(ANGLE_BINS) + ANGLE_BINS // 2) % ANGLE_BINS - ANGLE_BINS // 2
    degrees = lags * 360.0 / ANGLE_BINS
    near = np.abs((degrees - coarse_deg + 180.0) % 360.0 - 180.0) <= REFINE_WINDOW_DEG
    return float(degrees[int(np.argmax(np.where(near, corr, -np.inf)))])


def wrap_deg(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


class WheelSpeedMeter:
    """Velocidad del plato (grados por segundo, con signo) promediada en
    SPEED_WINDOW_S. Positiva = sentido de las agujas del reloj en la imagen."""

    def __init__(self, wheel: WheelEllipse) -> None:
        self._matrix = rectify_matrix(wheel)
        self._previous: tuple[float | None, np.ndarray, float] | None = None
        self._samples: deque[tuple[float, float, float]] = deque()  # (t, giro, dt)
        self._speed: float | None = None

    def ring(self, frame: np.ndarray, band: tuple[float, float] = NUMBERS_BAND) -> np.ndarray:
        size = 2 * RING_RADIUS
        disk = cv2.warpAffine(frame, self._matrix, (size, size), flags=cv2.INTER_LINEAR)
        polar = cv2.warpPolar(
            disk,
            (RING_RADIUS, ANGLE_BINS),
            (RING_RADIUS, RING_RADIUS),
            RING_RADIUS,
            cv2.WARP_POLAR_LINEAR,
        )
        return polar[:, int(band[0] * RING_RADIUS) : int(band[1] * RING_RADIUS)]

    def update(self, frame: np.ndarray, timestamp: float) -> float | None:
        strip = self.ring(frame)
        zero, profile = zero_angle(strip), ring_profile(strip)
        previous, self._previous = self._previous, (zero, profile, timestamp)
        if previous is None:
            return self._speed
        last_zero, last_profile, last_t = previous
        dt = timestamp - last_t
        if dt <= 0:
            return self._speed
        if zero is not None and last_zero is not None:
            coarse = wrap_deg(zero - last_zero)
        elif self._speed is not None:
            coarse = self._speed * dt  # sin cero: se predice con la ultima velocidad
        else:
            return self._speed
        turn = refine_shift(last_profile, profile, coarse)
        self._samples.append((timestamp, turn, dt))
        while self._samples and timestamp - self._samples[0][0] > SPEED_WINDOW_S:
            self._samples.popleft()
        total_turn = sum(sample[1] for sample in self._samples)
        total_time = sum(sample[2] for sample in self._samples)
        self._speed = total_turn / total_time if total_time > 0 else None
        return self._speed


class ChipZone:
    """Una zona del paño: manos moviendose y fichas que cambiaron.

    Un "toque" va desde que la zona empieza a moverse hasta que vuelve a
    quedar quieta ZONE_QUIET_S; `touch_started_at`, `touch_armed` y
    `touch_roles` (los carga el analizador) dicen cuando empezo, si el paño
    estaba armado en ese momento y los roles de las manos que se vieron
    adentro. Si las fichas cambiaron, `last_spots` son los lugares
    exactos (en pixeles del cuadro) donde aparecio, se fue o se corrio una
    ficha: la posicion de las fichas movidas."""

    def __init__(self, rect: Rect) -> None:
        self.rect = rect
        self._previous_gray: np.ndarray | None = None
        self._reference: np.ndarray | None = None
        self._quiet_since: float | None = None
        self._touched = False
        self.active = False
        self.changes = 0
        self.last_change_at: float | None = None
        self.last_change_fraction = 0.0
        self.last_spots: list[Rect] = []
        self.touch_started_at: float | None = None
        self.touch_roles: set[str] = set()
        self.chip_diameter = float(CHIP_DEFAULT_DIAMETER)
        self.last_chips = 0  # manchas con forma de ficha en el ultimo cambio
        self._reference_gradient: np.ndarray | None = None
        self.touch_armed = False  # el toque empezo con el paño armado
        self.touch_ball_live = False  # ... y con la bola todavia girando

    @property
    def touching(self) -> bool:
        return self._touched

    def _crop(self, frame: np.ndarray) -> np.ndarray:
        x, y, w, h = self.rect
        crop = cv2.resize(frame[y : y + h, x : x + w], None, fx=ZONE_SCALE, fy=ZONE_SCALE)
        return cv2.GaussianBlur(crop, (5, 5), 0)

    @staticmethod
    def _lab(crop: np.ndarray) -> np.ndarray:
        # Solo al comparar (no en cada cuadro): a 60 fps cada milisegundo cuenta.
        return cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)

    @staticmethod
    def _gradient(lab: np.ndarray) -> np.ndarray:
        light = lab[..., 0]
        return np.hypot(
            cv2.Sobel(light, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(light, cv2.CV_32F, 0, 1, ksize=3)
        )

    def update(self, frame: np.ndarray, timestamp: float) -> float | None:
        """Fraccion de la zona que cambio si las fichas se movieron, si no None."""
        crop = self._crop(frame)
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.int16)
        if self._previous_gray is None or self._previous_gray.shape != gray.shape:
            self._previous_gray, self._quiet_since = gray, timestamp
            self._reference = self._lab(crop)
            self._reference_gradient = self._gradient(self._reference)
            return None
        moving = float((np.abs(gray - self._previous_gray) > ZONE_MOTION_LEVEL).mean())
        self._previous_gray = gray
        self.active = moving > ZONE_MOTION_FRACTION
        if self.active:
            if not self._touched:
                self.touch_started_at, self.touch_roles = timestamp, set()
            self._quiet_since, self._touched = None, True
            return None
        if self._quiet_since is None:
            self._quiet_since = timestamp
        if not self._touched or timestamp - self._quiet_since < ZONE_QUIET_S:
            return None
        # Volvio a quedar quieta despues de que la tocaran: ¿cambiaron las fichas?
        lab = self._lab(crop)
        color = np.linalg.norm(lab - self._reference, axis=2) > ZONE_CHANGE_DELTA_E
        changed = cv2.morphologyEx(
            color.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
        )
        fraction = float(changed.mean())
        gradient = self._gradient(lab)
        chips = self._chip_spots(
            color | (np.abs(gradient - self._reference_gradient) > ZONE_CHANGE_GRADIENT)
        )
        self._reference, self._reference_gradient, self._touched = lab, gradient, False
        if fraction <= ZONE_CHANGE_FRACTION and not chips:
            return None
        self.changes += 1
        self.last_change_at = timestamp
        self.last_change_fraction = fraction
        self.last_chips = len(chips)
        self.last_spots = (chips + [s for s in self._spots(changed) if s not in chips])[
            :CHIP_SPOTS_MAX
        ]
        return fraction

    def _chip_spots(self, changed: np.ndarray) -> list[Rect]:
        """Manchas del cambio con el tamaño y la forma de una o pocas fichas."""
        mask = cv2.morphologyEx(
            changed.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        chip_area = np.pi * (self.chip_diameter * ZONE_SCALE / 2) ** 2
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        x0, y0 = self.rect[:2]
        spots = []
        for i in range(1, count):
            x, y, w, h, area = (int(v) for v in stats[i])
            if not CHIP_MIN_FILL * chip_area <= area <= CHIP_MAX_CHIPS * chip_area:
                continue
            if (
                max(w, h) / max(1, min(w, h)) > CHIP_MAX_ASPECT
                or area / (w * h) < CHIP_MIN_SOLIDITY
            ):
                continue
            spots.append(
                (
                    int(x0 + x / ZONE_SCALE),
                    int(y0 + y / ZONE_SCALE),
                    int(w / ZONE_SCALE),
                    int(h / ZONE_SCALE),
                )
            )
        return spots

    def _spots(self, changed: np.ndarray) -> list[Rect]:
        """Manchas del cambio, de mayor a menor, en pixeles del cuadro."""
        count, _, stats, _ = cv2.connectedComponentsWithStats(changed)
        x0, y0 = self.rect[:2]
        spots = [
            (
                int(x0 + stats[i, 0] / ZONE_SCALE),
                int(y0 + stats[i, 1] / ZONE_SCALE),
                int(stats[i, 2] / ZONE_SCALE),
                int(stats[i, 3] / ZONE_SCALE),
                int(stats[i, cv2.CC_STAT_AREA]),
            )
            for i in range(1, count)
            if stats[i, cv2.CC_STAT_AREA] >= CHIP_SPOT_MIN_AREA
        ]
        return [spot[:4] for spot in sorted(spots, key=lambda s: -s[4])[:CHIP_SPOTS_MAX]]


class RouletteAnalyzer(Analyzer):
    name = "monitor_tamper"

    def __init__(
        self,
        wheel_region: Rect | None = None,
        zones: list[Rect] | None = None,
        wheel: WheelEllipse | None = None,
        hands_enabled: bool = True,
        no_more_bets_deg_s: float = DEFAULT_NO_MORE_BETS_DEG_S,
        alert_hold_s: float = DEFAULT_ALERT_HOLD_S,
        hands_fps: float = DEFAULT_HANDS_FPS,
        detector=None,
        estimator=None,
        detail_estimator=None,
        background_hands: bool | None = None,
    ) -> None:
        self._wheel_region = tuple(int(v) for v in wheel_region) if wheel_region else None
        self._zones = [ChipZone(tuple(int(v) for v in zone)) for zone in zones or ()]
        self._wheel: WheelEllipse | None = None
        self._meter: WheelSpeedMeter | None = None
        self._ball_tracker: BallTracker | None = None
        self._rotor = RotorWatcher()
        if wheel is not None:
            self._set_wheel(wheel)
        self._warmup: list[np.ndarray] = []
        self._round = RouletteRound(no_more_bets_deg_s)
        self._alert_hold_s = float(alert_hold_s)
        # Manos del crupier y de los jugadores: el crupier es quien toca la rueda.
        self._hands = (
            TableHands(
                self._touches_wheel,
                detector=detector,
                estimator=estimator,
                on_table=self._on_table,
                interest=self._in_play,
                detail_estimator=detail_estimator,
                # Con una pose inyectada (tests) no se carga el modelo de
                # cuerpo entero salvo que tambien se inyecte.
                detailed=None if estimator is not None else True,
            )
            if hands_enabled
            else None
        )
        self._table: np.ndarray | None = None
        self._table_at: float | None = None
        # Deteccion completa: en su hilo en produccion; en linea con una pose
        # inyectada (tests: deterministas).
        if background_hands is None:
            background_hands = estimator is None
        self._executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="manos-ruleta")
            if self._hands is not None and background_hands
            else None
        )
        self._hands_interval = 1.0 / max(0.5, float(hands_fps))
        self._pending: Future | None = None
        self._pending_gray: np.ndarray | None = None
        self._submitted_at: float | None = None
        self._detected_at: float | None = None
        self._detections_per_s: float | None = None
        self._hand_errors = 0
        self._people: list = []
        self._gray: np.ndarray | None = None
        self._boxes: dict[tuple[int, str], tuple[float, float, float, float, float]] = {}
        self._scene_due: float | None = None
        self._speed: float | None = None
        self._ball = BallReading(None, None, None)
        self._alerts: dict[int, float] = {}  # zona -> hasta cuando se muestra la alerta
        self._incidents = 0
        self._last_incident: dict | None = None

    def _set_wheel(self, wheel: WheelEllipse) -> None:
        self._wheel = wheel
        self._meter = WheelSpeedMeter(wheel)
        self._ball_tracker = BallTracker(rectify_matrix(wheel, BALL_RADIUS))
        self._rotor = RotorWatcher()
        for zone in self._zones:
            zone.chip_diameter = CHIP_DIAMETER_WHEEL * min(wheel.width, wheel.height)

    def _touches_wheel(self, x: float, y: float) -> bool:
        """Punto dentro de la elipse del plato agrandada WHEEL_TOUCH_SCALE."""
        if self._wheel is None:
            return False
        wheel = self._wheel
        angle = np.deg2rad(wheel.angle)
        dx, dy = x - wheel.cx, y - wheel.cy
        u = dx * np.cos(angle) + dy * np.sin(angle)
        v = -dx * np.sin(angle) + dy * np.cos(angle)
        a = wheel.width / 2 * WHEEL_TOUCH_SCALE
        b = wheel.height / 2 * WHEEL_TOUCH_SCALE
        return bool((u / a) ** 2 + (v / b) ** 2 <= 1.0)

    def _on_table(self, x: float, y: float) -> bool:
        if self._table is None:
            return True  # sin mesa ubicada no se descarta a nadie
        row, col = int(y * TABLE_SCALE), int(x * TABLE_SCALE)
        height, width = self._table.shape
        return 0 <= row < height and 0 <= col < width and bool(self._table[row, col])

    def _look_at_table(self, frame: np.ndarray, timestamp: float) -> None:
        if self._table_at is not None and timestamp - self._table_at < TABLE_REFRESH_S:
            return
        self._table_at = timestamp
        table = detect_table(frame)
        if table is not None:
            self._table = table if self._table is None else (self._table | table)

    @staticmethod
    def _in_zone(zone: ChipZone, hand) -> bool:
        """La palma o cualquier dedo de la mano adentro de la zona."""
        x, y, w, h = zone.rect
        return hand.touches(lambda px, py: x <= px <= x + w and y <= py <= y + h)

    def _in_play(self, px: float, py: float) -> bool:
        """Donde vale la mano de 21 puntos: las zonas de fichas (agrandadas
        DETAIL_ZONE_MARGIN, para tener la mano detallada al entrar) y la rueda."""
        for zone in self._zones:
            x, y, w, h = zone.rect
            mx, my = w * DETAIL_ZONE_MARGIN, h * DETAIL_ZONE_MARGIN
            if x - mx <= px <= x + w + mx and y - my <= py <= y + h + my:
                return True
        return self._touches_wheel(px, py)

    @property
    def wheel(self) -> WheelEllipse | None:
        return self._wheel

    @property
    def round(self) -> RouletteRound:
        return self._round

    def _locate_wheel(self, frame: np.ndarray) -> None:
        """Junta ~1 s de cuadros y ubica la rueda (solo si no vino configurada)."""
        self._warmup.append(frame)
        if len(self._warmup) < WHEEL_MIN_FRAMES:
            return
        wheel = detect_wheel(self._warmup, self._wheel_region)
        self._warmup = []
        if wheel is not None:
            self._set_wheel(wheel)

    def _update_scene(self, frame: np.ndarray, timestamp: float) -> None:
        """La rueda, la bola y la ronda (hasta SCENE_MAX_FPS por segundo)."""
        self._ball = BallReading(None, None, self._ball.last_seen)
        if self._meter is None:
            self._locate_wheel(frame)
        else:
            self._speed = self._meter.update(frame, timestamp)
            self._ball = self._ball_tracker.update(frame, timestamp)
        round_changed = self._round.update(self._ball, timestamp, rotor=self._speed)
        kind = self._rotor.update(self._speed, timestamp) if self._meter is not None else None
        if kind is not None and self._round.new_game(kind, self._ball, timestamp):
            # Jugada nueva: lo que se este tocando en el paño son apuestas.
            for zone in self._zones:
                zone.touch_armed = zone.touch_ball_live = False
        croupier_known = self._hands is not None and self._hands.croupier_located
        if round_changed == ROUND_RESULT and not croupier_known:
            # Sin crupier identificado no se puede separar su pago de un
            # jugador que agrega fichas: el paño se desarma con el resultado.
            self._round.close_window(timestamp)

    def _hand_box(self, key: tuple[int, str], hand, diagonal: float, timestamp: float) -> list[int]:
        """El recuadro de la mano, suavizado entre cuadros (ver HAND_BOX_*)."""
        points = [(hand.x, hand.y)] + [p for p in hand.points if p is not None]
        minimum = HAND_BOX_MIN * diagonal
        if len(points) > 1:
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            w = max(minimum, (max(xs) - min(xs)) * (1 + 2 * HAND_BOX_PAD))
            h = max(minimum, (max(ys) - min(ys)) * (1 + 2 * HAND_BOX_PAD))
            cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        else:
            w = h = max(minimum, HAND_BOX_SINGLE * diagonal)
            cx, cy = hand.x, hand.y
        previous = self._boxes.get(key)
        if previous is not None and np.hypot(cx - previous[0], cy - previous[1]) <= max(w, h):
            k, q = HAND_BOX_CENTER_SMOOTHING, HAND_BOX_SIZE_SMOOTHING
            cx = previous[0] + k * (cx - previous[0])
            cy = previous[1] + k * (cy - previous[1])
            w = previous[2] + q * (w - previous[2])
            h = previous[3] + q * (h - previous[3])
        self._boxes[key] = (cx, cy, w, h, timestamp)
        return [round(cx - w / 2), round(cy - h / 2), round(w), round(h)]

    def _forget_boxes(self, timestamp: float) -> None:
        stale = [k for k, box in self._boxes.items() if timestamp - box[4] > HAND_BOX_FORGET_S]
        for key in stale:
            del self._boxes[key]

    def _update_hands(self, frame: np.ndarray, timestamp: float) -> list:
        """Las manos en ESTE cuadro: la ultima deteccion completa llevada
        hasta aca con flujo optico. La deteccion completa se larga cada
        `hands_fps` (si la anterior ya termino) y, cuando llega, se lleva del
        cuadro en que se calculo al actual."""
        gray = flow_gray(frame)
        due = self._submitted_at is None or timestamp - self._submitted_at >= self._hands_interval
        fresh, base = None, None
        if self._executor is not None:
            if self._pending is not None and self._pending.done():
                fresh, base = self._collect(), self._pending_gray
                self._pending = None
            if self._pending is None and due:
                self._submitted_at, self._pending_gray = timestamp, gray
                self._pending = self._executor.submit(self._hands.people, frame)
        elif due:
            self._submitted_at = timestamp
            fresh, base = self._hands.people(frame), gray
        if fresh is not None:
            self._count_detection(timestamp)
            people = fresh if base is gray else track_people(base, gray, fresh, max_deviation=0)
        else:
            people = track_people(self._gray, gray, self._people)
        self._people, self._gray = people, gray
        return people

    def _collect(self) -> list | None:
        try:
            return self._pending.result()
        except Exception:
            self._hand_errors += 1
            if self._hand_errors == 1 or self._hand_errors % 100 == 0:
                logger.exception("Deteccion de manos: fallo (%d veces)", self._hand_errors)
            return None

    def _count_detection(self, timestamp: float) -> None:
        if self._detected_at is not None and timestamp > self._detected_at:
            rate = 1.0 / (timestamp - self._detected_at)
            if self._detections_per_s is None:
                self._detections_per_s = rate
            else:
                self._detections_per_s += HANDS_RATE_SMOOTHING * (rate - self._detections_per_s)
        self._detected_at = timestamp

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        # Agenda que acumula el periodo: "paso 1/25 s desde la ultima" a 60 fps
        # corria cada 3 cuadros (20 por segundo, no 25).
        period = 1.0 / SCENE_MAX_FPS
        scene = self._scene_due is None or timestamp >= self._scene_due - 1e-3
        if scene:
            due = (self._scene_due or timestamp) + period
            self._scene_due = due if due > timestamp else timestamp + period
            self._update_scene(frame, timestamp)
        speed, ball = self._speed, self._ball

        people = []
        if self._hands is not None:
            self._look_at_table(frame, timestamp)
            people = self._update_hands(frame, timestamp)
        hands = [
            (hand, person.role, owner)
            for owner, person in enumerate(people)
            for hand in person.hands
        ]
        if self._round.window_open:
            croupier_on_felt = any(
                role == ROLE_CROUPIER and self._in_zone(zone, hand)
                for hand, role, _ in hands
                for zone in self._zones
            )
            if croupier_on_felt and self._round.croupier_marked(ball, timestamp):
                # El toque con el que el crupier marca es suyo: lo que cambie
                # (la marca, las fichas que cobra) no es un past-post. Si un
                # jugador ya estaba tocando esa zona, el toque sigue armado.
                for zone in self._zones:
                    if zone.touching and zone.touch_roles <= {ROLE_CROUPIER}:
                        zone.touch_armed = False

        detections: list[Detection] = []
        triggers: list[Detection] = []
        zones = []
        for index, zone in enumerate(self._zones):
            change = zone.update(frame, timestamp) if scene else None
            if zone.touching and zone.touch_started_at == timestamp:
                # Vale el estado del paño cuando EMPEZO el toque: si el
                # crupier marca mientras la zona espera quedar quieta, el
                # jugador que agrego fichas despues del resultado igual cuenta.
                zone.touch_armed = self._round.window_open
                zone.touch_ball_live = zone.touch_armed and self._round.ball_live(ball, timestamp)
            roles = sorted({role for hand, role, _ in hands if self._in_zone(zone, hand)})
            if zone.touching:
                zone.touch_roles.update(roles)
            if change is not None:
                spot = zone.last_spots[0] if zone.last_spots else zone.rect
                if self._is_past_post(zone):
                    incident = Detection(label=LABEL_PAST_POST, confidence=1.0, bbox=spot)
                    detections.append(incident)
                    triggers.append(incident)
                    self._alerts[index] = timestamp + self._alert_hold_s
                    self._incidents += 1
                    self._last_incident = {
                        "t": timestamp,
                        "zona": index,
                        "motivo": MOTIVE_PAST_POST,
                        "manos": sorted(zone.touch_roles),
                    }
                else:
                    detections.append(Detection(label="fichas_movidas", confidence=1.0, bbox=spot))
            alerting = self._alerts.get(index, 0.0) > timestamp
            if alerting:
                state, motive = "alerta", MOTIVE_PAST_POST
            elif zone.active:
                state, motive = "actividad", "fichas"
            else:
                state, motive = "normal", None
            zones.append(
                {
                    "estado": state,
                    "motivo": motive,
                    "cambios": zone.changes,
                    "ultimo_cambio": zone.last_change_at,
                    "posiciones": [list(spot) for spot in zone.last_spots],
                    "indice": index,
                    "manos": roles,
                }
            )
        last = max(
            (
                (zone.last_change_at, index)
                for index, zone in enumerate(self._zones)
                if zone.last_change_at is not None
            ),
            default=None,
        )
        metrics = {
            "modo": "ruleta",
            "zona_tipo": "zona",
            "estado": "alerta" if any(z["estado"] == "alerta" for z in zones) else "normal",
            "zonas": zones,
            "incidentes": self._incidents,
            "ultimo_incidente": self._last_incident,
            "ronda": {
                "estado": self._round.state,
                "desde": self._round.since,
                "bola_deg_s": (
                    round(self._round.ball_speed, 1) if self._round.ball_speed else None
                ),
                "umbral_deg_s": self._round.threshold,
                "pano_armado": self._round.window_open,
                "armado_desde": self._round.window_since,
                "tiradas": self._round.rounds,
                # Jugadas que abrio la rueda (el crupier freno o re-impulso el plato).
                "jugadas": self._round.games,
                "ultima_jugada": (
                    {"t": self._round.last_game[0], "tipo": self._round.last_game[1]}
                    if self._round.last_game
                    else None
                ),
            },
            "bola": [round(v, 1) for v in ball.position] if ball.position else None,
            "rueda": self._wheel.as_list() if self._wheel else None,
            "velocidad_deg_s": round(abs(speed), 1) if speed is not None else None,
            "rpm": round(abs(speed) / 6.0, 1) if speed is not None else None,
            "sentido": ("horario" if speed > 0 else "antihorario") if speed else None,
            "fichas_movidas": sum(zone.changes for zone in self._zones),
            "personas": [person.as_metrics() for person in people],
            # Cada mano con su persona (indice en "personas"), para que el
            # video agrupe las dos manos de alguien, y con lo que esta tocando.
            "manos": [
                {
                    "x": round(hand.x, 1),
                    "y": round(hand.y, 1),
                    "rol": role,
                    "persona": owner,
                    "confianza": round(hand.score, 2),
                    "lado": hand.side,
                    # El recuadro que dibuja el video (suavizado entre cuadros)
                    # y cuantos de los 21 puntos de la mano se vieron (0: mano
                    # de un punto, la muñeca extrapolada).
                    "caja": self._hand_box(
                        (people[owner].track, hand.side), hand, people[owner].diagonal, timestamp
                    ),
                    "puntos_vistos": sum(point is not None for point in hand.points),
                    "en_rueda": hand.touches(self._touches_wheel),
                    "zona": next(
                        (i for i, zone in enumerate(self._zones) if self._in_zone(zone, hand)),
                        None,
                    ),
                }
                for hand, role, owner in hands
            ],
            "crupier_ubicado": self._hands is not None and self._hands.croupier_located,
            # Detecciones completas de manos por segundo (entre medio, flujo optico).
            "manos_detecciones_s": (
                round(self._detections_per_s, 1) if self._detections_per_s else None
            ),
            "ultimo_movimiento": {"t": last[0], "zona": last[1]} if last else None,
            "contacto": any(zone.active for zone in self._zones),
        }
        self._forget_boxes(timestamp)
        return AnalysisResult(
            detections=tuple(detections), metrics=metrics, triggers=tuple(triggers)
        )

    @staticmethod
    def _is_past_post(zone: ChipZone) -> bool:
        """Fichas que cambiaron en un toque que empezo con el paño armado (una
        apuesta que se estaba dejando cuando se canto el no va mas no cuenta):

        - con la bola todavia girando, nadie tiene por que tocar el paño:
          cuenta todo lo que no sea solo del crupier, aunque no se hayan visto
          manos;
        - con la bola ya caida, el crupier marca y cobra: hace falta ver la
          mano de un jugador. En la AR 4171 el crupier empezo a cobrar 2,8 s
          despues de la ultima vez que se vio la bola, antes de que se diera
          el resultado por BALL_GONE_S, sin manos a la vista, y salia como
          past-post."""
        if not zone.touch_armed or zone.last_chips == 0:
            # Sin una mancha con forma de ficha, lo que cambio es un brazo
            # apoyado en el borde de la zona o el paño barrido: no una ficha.
            return False
        if zone.touch_ball_live:
            return zone.touch_roles != {ROLE_CROUPIER}
        return ROLE_PLAYER in zone.touch_roles

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
        if self._hands is not None:
            self._hands.close()
