"""Incidentes en casinos, modo ruleta: la bola y la ronda (lanzamiento, no va
mas y resultado).

**Por que la bola y no el plato.** El plato casi no frena en una tirada:
medido (2026-09-30) sobre la RA-04 entera, baja ~0,4 grados/s por segundo
(182 -> 142 en 160 s) y solo se desploma cuando el crupier lo frena con la
mano. La bola si: sale a ~500 grados/s por la pista y frena hasta caer al
plato a ~120-145. Por eso el "no va mas" automatico mira la bola.

**La bola** corre por la pista del tazon, que esta QUIETA (el plato gira por
dentro). Sobre el disco normalizado del plato (radio RADIUS), la pista es el
anillo TRACK_BAND, y la bola es lo que se aclaro ahi respecto del cuadro
anterior: una mancha de BALL_AREA pixeles. Medido en los dos clips de la demo:

- RA-04 (una tirada, 53-72 s): 536 -> 118 grados/s, a 1,28-1,33 radios del
  plato, visible en el 52% de los cuadros (el resto de la vuelta la tapan
  reflejos y el vidrio); manchas de 200-600 px a 25 fps.
- AR 4171 (una tirada, 2-17 s): 502 -> 142 grados/s, a 1,33-1,34 radios,
  visible en el 36%; manchas de 120-190 px.

La velocidad sale de pares de detecciones a menos de PAIR_MAX_DT (a 500
grados/s y 10 fps la bola avanza 50 grados por muestra: sin ambigüedad) y se
promedia con la mediana de SPEED_WINDOW_S.

**La ronda**:
- "apuestas": no hay bola en la pista.
- "bola": la bola gira por encima del umbral; se puede apostar. Se confirma
  tras MIN_FLIGHT_S (una mano que roza la pista no es una tirada).
- "no_va_mas": la bola bajo del umbral (por defecto 200 grados/s: 7 s antes
  de caer en la AR 4171 y 11 s en la RA-04). Arma el paño.
- "resultado": la bola dejo la pista (BALL_GONE_S sin verla: a 120
  grados/s una vuelta tarda 3 s y la bola se ve menos de la mitad). El paño
  sigue armado hasta que el crupier marca el numero (toca el paño).

**La rueda marca la jugada** (decision de Nahuel). El crupier frena el plato
y lo vuelve a impulsar para cada jugada: en la RA-04, a los 50,6-52,4 s pasa
de -159 grados/s (antihorario) a +187,5 (horario) y a los 53,9 s lanza la
bola. Un cambio asi (RotorWatcher) abre una jugada nueva: se reabren las
apuestas y se desarma el paño si quedo armado (por ejemplo, si no se vio al
crupier marcar). Medido a 25 muestras por segundo, la velocidad del plato es
limpia (+-1-3 grados/s en la RA-04; escalones de hasta 15% en la AR 4171) y
el rozamiento la baja despacio (182 -> 142 en 160 s): el evento es un cambio
de sentido, o un cambio de mas de ROTOR_CHANGE sostenido ROTOR_CONFIRM_S con
el nivel nuevo estable. Ademas, la bola gira CONTRA el plato (en los dos
clips): una "bola" que va en el mismo sentido que el plato no cuenta.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

# Disco normalizado del plato (lo arma roulette_analyzer.rectify_matrix).
RADIUS = 200
PAD = 100  # margen para la pista, que va por fuera del plato
TRACK_BAND = (1.22, 1.42)  # radios del plato
BALL_BRIGHTEN = 45  # niveles de gris que se aclara la bola al llegar
BALL_AREA = (40, 1200)  # pixeles del disco normalizado
PAIR_MAX_DT = 0.25
SPEED_RANGE = (40.0, 1500.0)  # grados/s plausibles para una bola en la pista
SPEED_WINDOW_S = 1.5
MIN_SPEEDS = 3
SAME_SIGN = 0.75  # fraccion de pares con el mismo sentido
BALL_GONE_S = 3.0
MIN_FLIGHT_S = 3.0
DEFAULT_NO_MORE_BETS_DEG_S = 200.0
# Una mano del crupier en el paño cuenta como la marca del resultado aunque
# la ronda siga en "no va mas", si la bola ya no se ve hace este tiempo: el
# crupier puede marcar antes de que venza BALL_GONE_S.
MARK_BALL_UNSEEN_S = 1.0

# El plato (ver RotorWatcher).
ROTOR_MIN_SPIN = 30.0  # grados/s: por debajo el plato esta quieto o frenado a mano
ROTOR_CHANGE = 0.25  # cambio relativo contra el nivel de los ultimos ROTOR_LEVEL_S
ROTOR_LEVEL_S = 2.0
ROTOR_CONFIRM_S = 1.0  # cuanto tiene que sostenerse el cambio
ROTOR_STABLE = 0.15  # el nivel nuevo: todas las muestras a +-15% de su mediana
ROTOR_REVERSED = "sentido"
ROTOR_FASTER = "impulso"
ROTOR_SLOWER = "frenado"

ROUND_BETS = "apuestas"
ROUND_BALL = "bola"
ROUND_NO_MORE_BETS = "no_va_mas"
ROUND_RESULT = "resultado"


@dataclass(frozen=True)
class BallReading:
    position: tuple[float, float] | None  # pixeles del cuadro, si se vio ahora
    speed: float | None  # grados/s con signo (positivo = horario), mediana
    last_seen: float | None


class BallTracker:
    """La bola en la pista del tazon, cuadro a cuadro."""

    def __init__(self, matrix: np.ndarray) -> None:
        """`matrix`: la afin de roulette_analyzer.rectify_matrix (plato ->
        circulo de radio RADIUS centrado en (RADIUS, RADIUS))."""
        self._matrix = matrix.copy()
        self._matrix[:, 2] += PAD
        self._inverse = cv2.invertAffineTransform(self._matrix)
        size = 2 * (RADIUS + PAD)
        self._size = size
        center = RADIUS + PAD
        yy, xx = np.mgrid[:size, :size]
        radius = np.hypot(xx - center, yy - center) / RADIUS
        self._track = (radius >= TRACK_BAND[0]) & (radius < TRACK_BAND[1])
        self._center = center
        self._previous: np.ndarray | None = None
        self._last: tuple[float, float] | None = None  # (t, angulo)
        self._speeds: deque[tuple[float, float]] = deque()
        self._last_seen: float | None = None

    def update(self, frame: np.ndarray, timestamp: float) -> BallReading:
        disk = cv2.warpAffine(frame, self._matrix, (self._size, self._size))
        gray = cv2.cvtColor(disk, cv2.COLOR_BGR2GRAY).astype(np.int16)
        previous, self._previous = self._previous, gray
        position = None
        if previous is not None:
            angle = self._find(gray - previous)
            if angle is not None:
                position = self._to_frame(angle)
                self._add(timestamp, angle[0])
        while self._speeds and timestamp - self._speeds[0][0] > SPEED_WINDOW_S:
            self._speeds.popleft()
        return BallReading(position, self.speed(), self._last_seen)

    def _find(self, brightened: np.ndarray) -> tuple[float, float] | None:
        """(angulo en grados, radio en el disco) de la mancha de la bola."""
        mask = ((brightened > BALL_BRIGHTEN) & self._track).astype(np.uint8)
        count, _, stats, centers = cv2.connectedComponentsWithStats(mask)
        best = None
        for index in range(1, count):
            area = stats[index, cv2.CC_STAT_AREA]
            if BALL_AREA[0] <= area <= BALL_AREA[1] and (
                best is None or area > stats[best, cv2.CC_STAT_AREA]
            ):
                best = index
        if best is None:
            return None
        x, y = centers[best] - self._center
        return (float(np.degrees(np.arctan2(y, x)) % 360.0), float(np.hypot(x, y)))

    def _to_frame(self, angle: tuple[float, float]) -> tuple[float, float]:
        theta = np.radians(angle[0])
        point = np.array(
            [self._center + angle[1] * np.cos(theta), self._center + angle[1] * np.sin(theta), 1.0]
        )
        x, y = self._inverse @ point
        return (float(x), float(y))

    def _add(self, timestamp: float, angle: float) -> None:
        last, self._last = self._last, (timestamp, angle)
        self._last_seen = timestamp
        if last is None or not 0 < timestamp - last[0] <= PAIR_MAX_DT:
            return
        speed = ((angle - last[1] + 180.0) % 360.0 - 180.0) / (timestamp - last[0])
        if SPEED_RANGE[0] <= abs(speed) <= SPEED_RANGE[1]:
            self._speeds.append((timestamp, speed))

    def speed(self) -> float | None:
        if len(self._speeds) < MIN_SPEEDS:
            return None
        values = np.array([speed for _, speed in self._speeds])
        sign = np.sign(np.median(values))
        if (np.sign(values) == sign).mean() < SAME_SIGN:
            return None
        return float(np.median(values[np.sign(values) == sign]))


def against_rotor(ball_speed: float | None, rotor: float | None) -> bool:
    """La bola gira contra el plato (si el plato gira y se lo midio)."""
    if ball_speed is None or rotor is None or abs(rotor) < ROTOR_MIN_SPIN:
        return True
    return bool(np.sign(ball_speed) != np.sign(rotor))


class RotorWatcher:
    """Cambios del plato que abren una jugada: cambio de sentido, impulso o
    frenado, sostenidos y con el nivel nuevo estable. La deriva lenta del
    rozamiento no cuenta: el nivel la sigue."""

    def __init__(self) -> None:
        self.level: float | None = None
        self._recent: deque[tuple[float, float]] = deque()  # (t, velocidad)
        self._changed: deque[tuple[float, float]] = deque()  # las que se apartan del nivel

    def update(self, speed: float | None, timestamp: float) -> str | None:
        """Devuelve el tipo de cambio cuando se confirma, si no None."""
        if speed is None:
            return None
        self._recent.append((timestamp, speed))
        while timestamp - self._recent[0][0] > ROTOR_LEVEL_S:
            self._recent.popleft()
        if self.level is None:
            if timestamp - self._recent[0][0] >= ROTOR_CONFIRM_S:
                self.level = float(np.median([v for _, v in self._recent]))
            return None
        if not self._apart(speed):
            self._changed.clear()
            self.level = float(np.median([v for _, v in self._recent]))  # sigue la deriva
            return None
        self._changed.append((timestamp, speed))
        while timestamp - self._changed[0][0] > ROTOR_CONFIRM_S:
            self._changed.popleft()
        span = timestamp - self._changed[0][0]
        values = np.array([v for _, v in self._changed])
        new_level = float(np.median(values))
        steady = np.all(np.abs(values - new_level) <= ROTOR_STABLE * max(abs(new_level), 1.0))
        if span < ROTOR_CONFIRM_S * 0.9 or not steady:
            return None
        old, self.level = self.level, new_level
        self._changed.clear()
        self._recent = deque([(timestamp, new_level)])
        if np.sign(new_level) != np.sign(old) and abs(new_level) >= ROTOR_MIN_SPIN:
            return ROTOR_REVERSED
        return ROTOR_FASTER if abs(new_level) > abs(old) else ROTOR_SLOWER

    def _apart(self, speed: float) -> bool:
        if np.sign(speed) != np.sign(self.level) and abs(speed) >= ROTOR_MIN_SPIN:
            return True
        return abs(speed - self.level) > ROTOR_CHANGE * max(abs(self.level), ROTOR_MIN_SPIN)


class RouletteRound:
    """Estado de la ronda a partir de la bola. `window_open`: el paño esta
    armado (desde el no va mas hasta que el crupier marca el resultado)."""

    def __init__(self, no_more_bets_deg_s: float = DEFAULT_NO_MORE_BETS_DEG_S) -> None:
        self.threshold = float(no_more_bets_deg_s)
        self.state = ROUND_BETS
        self.since: float | None = None
        self.window_since: float | None = None
        self.ball_speed: float | None = None
        self.rounds = 0
        self.games = 0  # jugadas abiertas por la rueda
        self.last_game: tuple[float, str] | None = None  # (t, tipo de cambio del plato)
        self._flight_since: float | None = None

    @property
    def window_open(self) -> bool:
        return self.window_since is not None

    def _enter(self, state: str, timestamp: float) -> None:
        self.state, self.since = state, timestamp

    def update(self, ball: BallReading, timestamp: float, rotor: float | None = None) -> str | None:
        """Avanza la ronda; devuelve el estado nuevo si cambio. `rotor` es la
        velocidad del plato con signo: la bola tiene que ir en contra."""
        before = self.state
        gone = ball.last_seen is None or timestamp - ball.last_seen > BALL_GONE_S
        speed = abs(ball.speed) if ball.speed is not None and not gone else None
        if speed is not None and not against_rotor(ball.speed, rotor):
            speed = None  # va con el plato: no es la bola (un reflejo, una mano)
        self.ball_speed = speed
        if self.state in (ROUND_BETS, ROUND_RESULT) and speed is not None:
            if speed >= self.threshold:  # tirada nueva: la marca anterior ya paso
                self.window_since = None
                self._flight_since = timestamp
                self._enter(ROUND_BALL, timestamp)
        elif self.state == ROUND_BALL:
            # Lo que cuenta es cuanto se VIO girar la bola: medido hasta
            # "ahora", una mano que roza la pista 1,5 s quedaba confirmada al
            # vencer BALL_GONE_S y armaba el paño.
            seen = ball.last_seen if ball.last_seen is not None else self._flight_since
            confirmed = seen - self._flight_since >= MIN_FLIGHT_S
            if gone:
                if confirmed:  # cayo sin verla lenta: el paño se arma igual
                    self._open_window(timestamp)
                    self._enter(ROUND_RESULT, timestamp)
                else:  # una mano rozando la pista, no una tirada
                    self._enter(ROUND_BETS, timestamp)
            elif confirmed and speed is not None and speed < self.threshold:
                self._open_window(timestamp)
                self._enter(ROUND_NO_MORE_BETS, timestamp)
        elif self.state == ROUND_NO_MORE_BETS and gone:
            self._enter(ROUND_RESULT, timestamp)
        return self.state if self.state != before else None

    def _open_window(self, timestamp: float) -> None:
        self.window_since = timestamp
        self.rounds += 1

    def new_game(self, kind: str, ball: BallReading, timestamp: float) -> bool:
        """El plato cambio (ver RotorWatcher): jugada nueva, se reabren las
        apuestas y se desarma el paño. Con la bola girando no cuenta (el
        crupier no toca la rueda en plena tirada: seria una mala lectura).
        Devuelve True si se tomo."""
        if self.state == ROUND_BALL or self.ball_live(ball, timestamp):
            return False
        self.games += 1
        self.last_game = (timestamp, kind)
        self.window_since = None
        if self.state != ROUND_BETS:
            self._enter(ROUND_BETS, timestamp)
        return True

    def ball_live(self, ball: BallReading, timestamp: float) -> bool:
        """La bola sigue girando: en no va mas y vista hace menos de
        MARK_BALL_UNSEEN_S."""
        return (
            self.state == ROUND_NO_MORE_BETS
            and ball.last_seen is not None
            and timestamp - ball.last_seen < MARK_BALL_UNSEEN_S
        )

    def croupier_marked(self, ball: BallReading, timestamp: float) -> bool:
        """El crupier toco el paño: si la bola ya cayo, marco el resultado y
        se desarma el paño. Devuelve True si cerro la ventana."""
        if not self.window_open:
            return False
        live = self.ball_live(ball, timestamp)
        if self.state == ROUND_RESULT or (self.state == ROUND_NO_MORE_BETS and not live):
            self.window_since = None
            self._enter(ROUND_BETS, timestamp)
            return True
        return False

    def close_window(self, timestamp: float) -> None:
        """Sin crupier identificado no hay quien marque: el paño se desarma
        con el resultado."""
        self.window_since = None
        self._enter(ROUND_BETS, timestamp)
