"""La bola y la ronda de la ruleta (2026-09-30): la velocidad de la bola, el
no va mas automatico cuando frena, el resultado cuando deja la pista y la
marca del crupier que desarma el paño."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from aurea_vms.core.analytics.roulette_analyzer import WheelEllipse, rectify_matrix
from aurea_vms.core.analytics.roulette_round import (
    RADIUS,
    ROTOR_FASTER,
    ROTOR_REVERSED,
    ROTOR_SLOWER,
    ROUND_BALL,
    ROUND_BETS,
    ROUND_NO_MORE_BETS,
    ROUND_RESULT,
    BallReading,
    BallTracker,
    RotorWatcher,
    RouletteRound,
    against_rotor,
)

SIZE = (600, 800)  # alto, ancho
CENTER = (400, 300)
PLATE = 150  # radio del plato
FPS = 10.0


def _frame(ball_angle: float | None) -> np.ndarray:
    """Tazon quieto con la pista oscura y, si hay, la bola blanca a 1,32
    radios del plato. La bola solo se ve en media vuelta (0-180 grados), como
    en las mesas de la demo, donde reflejos y vidrio tapan el resto."""
    frame = np.full((*SIZE, 3), 70, np.uint8)
    cv2.circle(frame, CENTER, int(PLATE * 1.5), (35, 45, 60), -1)  # tazon y pista
    cv2.circle(frame, CENTER, PLATE, (20, 20, 110), -1)  # plato
    if ball_angle is not None and (ball_angle % 360.0) < 180.0:
        theta = np.radians(ball_angle)
        x = int(CENTER[0] + 1.32 * PLATE * np.cos(theta))
        y = int(CENTER[1] + 1.32 * PLATE * np.sin(theta))
        cv2.circle(frame, (x, y), 6, (245, 245, 245), -1)
    return frame


def _tracker() -> BallTracker:
    wheel = WheelEllipse(CENTER[0], CENTER[1], 2 * PLATE, 2 * PLATE, 0.0)
    return BallTracker(rectify_matrix(wheel, RADIUS))


def _spin(start_speed: float, decel: float, seconds: float, sign: float = 1.0):
    """Angulo de la bola cuadro a cuadro, frenando `decel` grados/s por segundo."""
    angle, speed = 10.0, start_speed
    for i in range(int(seconds * FPS)):
        yield i / FPS, angle, speed
        angle += sign * speed / FPS
        speed = max(0.0, speed - decel / FPS)


class TestBola:
    @pytest.mark.parametrize("sign", [1.0, -1.0])
    def test_mide_la_velocidad_y_el_sentido(self, sign):
        tracker = _tracker()
        reading = None
        for t, angle, _ in _spin(400.0, 0.0, 3.0, sign):
            reading = tracker.update(_frame(angle), t)

        assert reading.speed == pytest.approx(sign * 400.0, rel=0.08)

    def test_la_bola_se_marca_donde_esta(self):
        tracker = _tracker()
        tracker.update(_frame(None), 0.0)

        reading = tracker.update(_frame(90.0), 0.1)

        assert reading.position == pytest.approx((CENTER[0], CENTER[1] + 1.32 * PLATE), abs=4)

    def test_sin_bola_no_hay_velocidad(self):
        tracker = _tracker()
        for i in range(20):
            reading = tracker.update(_frame(None), i / FPS)

        assert reading.speed is None and reading.last_seen is None


class TestRonda:
    def _play(self, frames, threshold=200.0):
        tracker, ronda = _tracker(), RouletteRound(threshold)
        changes = []
        for t, angle in frames:
            ball = tracker.update(_frame(angle), t)
            change = ronda.update(ball, t)
            if change:
                changes.append((change, round(t, 1), ronda.window_open))
        return ronda, changes

    def test_tirada_completa(self):
        # Sale a 500 grados/s, frena 20 por segundo, cae a los 18 s (~140).
        spin = [(t, angle) for t, angle, _ in _spin(500.0, 20.0, 18.0)]
        after = [(18.0 + i / FPS, None) for i in range(50)]

        ronda, changes = self._play(spin + after)

        states = [change[0] for change in changes]
        assert states == [ROUND_BALL, ROUND_NO_MORE_BETS, ROUND_RESULT]
        no_more_bets_at = changes[1][1]
        assert no_more_bets_at == pytest.approx(15.0, abs=1.0)  # 500 - 20 t = 200
        assert changes[1][2] is True  # el paño se arma con el no va mas
        assert changes[2][1] == pytest.approx(18.0 + 3.0, abs=0.5)
        assert ronda.window_open and ronda.rounds == 1

    def test_una_mano_que_roza_la_pista_no_es_una_tirada(self):
        brush = [(t, angle) for t, angle, _ in _spin(300.0, 0.0, 1.5)]
        after = [(1.5 + i / FPS, None) for i in range(50)]

        ronda, changes = self._play(brush + after)

        assert [change[0] for change in changes] == [ROUND_BALL, ROUND_BETS]
        assert not ronda.window_open and ronda.rounds == 0

    def test_el_umbral_se_configura(self):
        spin = [(t, angle) for t, angle, _ in _spin(500.0, 20.0, 18.0)]

        _, changes = self._play(spin, threshold=300.0)

        no_more_bets = [c for c in changes if c[0] == ROUND_NO_MORE_BETS]
        # 500 - 20 t = 300 a los 10 s; la mediana de 1,5 s y la media vuelta
        # sin ver la bola lo atrasan hasta ~1 s.
        assert 10.0 <= no_more_bets[0][1] <= 11.5


class TestMarca:
    def _armed(self, state: str) -> RouletteRound:
        ronda = RouletteRound()
        ronda.state, ronda.window_since = state, 10.0
        return ronda

    def test_la_marca_del_crupier_desarma_el_paño_despues_del_resultado(self):
        ronda = self._armed(ROUND_RESULT)

        assert ronda.croupier_marked(BallReading(None, None, 20.0), 25.0) is True
        assert not ronda.window_open and ronda.state == ROUND_BETS

    def test_con_la_bola_girando_el_crupier_no_marca(self):
        ronda = self._armed(ROUND_NO_MORE_BETS)

        assert ronda.croupier_marked(BallReading((1, 1), 150.0, 14.8), 15.0) is False
        assert ronda.window_open

    def test_si_la_bola_ya_no_se_ve_el_crupier_puede_marcar_antes_del_resultado(self):
        ronda = self._armed(ROUND_NO_MORE_BETS)

        assert ronda.croupier_marked(BallReading(None, None, 13.0), 15.0) is True

    def test_la_bola_sigue_viva_mientras_se_la_ve(self):
        ronda = self._armed(ROUND_NO_MORE_BETS)

        assert ronda.ball_live(BallReading(None, 150.0, 14.5), 15.0) is True
        assert ronda.ball_live(BallReading(None, None, 13.0), 15.0) is False


def _series(*pieces, rate: float = 25.0, seed: int = 3):
    """Velocidad del plato por tramos (duracion, desde, hasta, ruido relativo)."""
    rng = np.random.default_rng(seed)
    t, out = 0.0, []
    for duration, start, end, noise in pieces:
        n = int(duration * rate)
        for k in range(n):
            value = start + (end - start) * k / max(1, n - 1)
            out.append((t, value * (1 + noise * rng.uniform(-1, 1))))
            t += 1 / rate
    return out


def _events(series):
    watcher = RotorWatcher()
    return [(round(t, 1), kind) for t, v in series if (kind := watcher.update(v, t))]


class TestPlato:
    def test_un_plato_que_gira_parejo_no_abre_jugadas(self):
        # Como la AR 4171: escalones de hasta 15%.
        assert _events(_series((40, -130, -120, 0.15))) == []

    def test_el_rozamiento_no_abre_jugadas(self):
        assert _events(_series((160, 182, 142, 0.02))) == []

    def test_el_re_impulso_en_contra_abre_una_sola_jugada(self):
        # RA-04: -159 -> frena -> +210 -> se asienta en +187.
        events = _events(
            _series(
                (5, -160, -158, 0.01),
                (1.2, -158, 0, 0.05),
                (0.8, 0, 210, 0.05),
                (0.6, 210, 188, 0.03),
                (5, 188, 187, 0.01),
            )
        )

        assert [kind for _, kind in events] == [ROTOR_REVERSED]
        assert 7.5 <= events[0][0] <= 9.0  # cuando el nivel nuevo ya es estable

    def test_un_impulso_en_el_mismo_sentido(self):
        events = _events(_series((5, 150, 150, 0.01), (0.5, 150, 200, 0.02), (4, 200, 199, 0.01)))

        assert [kind for _, kind in events] == [ROTOR_FASTER]

    def test_frenar_el_plato_a_mano(self):
        events = _events(_series((5, 150, 150, 0.01), (0.5, 150, 5, 0.02), (4, 5, 5, 0.0)))

        assert [kind for _, kind in events] == [ROTOR_SLOWER]

    def test_un_toque_de_un_instante_no_es_una_jugada(self):
        events = _events(_series((5, 170, 170, 0.01), (0.4, 170, 60, 0.0), (5, 170, 169, 0.01)))

        assert events == []


class TestBolaContraElPlato:
    def test_la_bola_gira_contra_el_plato(self):
        assert against_rotor(-521.0, 187.0) and against_rotor(502.0, -130.0)
        assert not against_rotor(400.0, 187.0)
        assert against_rotor(400.0, None) and against_rotor(400.0, 10.0)  # plato quieto

    def test_una_bola_que_va_con_el_plato_no_abre_tirada(self):
        tracker, ronda = _tracker(), RouletteRound()
        for t, angle, _ in _spin(500.0, 0.0, 5.0):  # horario, igual que el plato
            ronda.update(tracker.update(_frame(angle), t), t, rotor=187.0)

        assert ronda.state == ROUND_BETS

        tracker, ronda = _tracker(), RouletteRound()
        for t, angle, _ in _spin(500.0, 0.0, 5.0):
            ronda.update(tracker.update(_frame(angle), t), t, rotor=-187.0)

        assert ronda.state == ROUND_BALL


class TestJugadaNueva:
    def test_desarma_el_paño_que_quedo_armado(self):
        ronda = RouletteRound()
        ronda.state, ronda.window_since = ROUND_RESULT, 10.0

        assert ronda.new_game(ROTOR_REVERSED, BallReading(None, None, 20.0), 40.0) is True
        assert ronda.state == ROUND_BETS and not ronda.window_open
        assert ronda.games == 1 and ronda.last_game == (40.0, ROTOR_REVERSED)

    def test_con_la_bola_girando_no_cuenta(self):
        ronda = RouletteRound()
        ronda.state, ronda.window_since = ROUND_NO_MORE_BETS, 10.0

        assert ronda.new_game(ROTOR_FASTER, BallReading((1, 1), 150.0, 14.9), 15.0) is False
        assert ronda.window_open and ronda.games == 0

        ronda.state = ROUND_BALL
        assert ronda.new_game(ROTOR_FASTER, BallReading((1, 1), 400.0, 14.9), 15.0) is False
