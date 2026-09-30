"""Fichas tras el no va mas (2026-09-30): con el paño armado, mover fichas es
una alerta que dispara la alarma, salvo que sea el crupier. Con la bola
girando cuenta todo lo que no sea solo del crupier; con la bola ya caida hace
falta ver la mano de un jugador. La marca del crupier desarma el paño."""

from __future__ import annotations

import cv2
import numpy as np

from aurea_vms.core.analytics.pose_backend import LEFT_WRIST, Pose
from aurea_vms.core.analytics.roulette_analyzer import (
    LABEL_PAST_POST,
    RouletteAnalyzer,
    WheelEllipse,
)
from aurea_vms.core.analytics.roulette_round import (
    ROUND_BETS,
    ROUND_NO_MORE_BETS,
    ROUND_RESULT,
    RouletteRound,
)
from aurea_vms.core.events import Detection

SIZE = (480, 640)
FPS = 4.0  # un cuadro cada 0,25 s: las manos se muestrean en todos
ZONE = (100, 100, 300, 200)
OTHER_ZONE = (420, 300, 180, 150)
WHEEL = WheelEllipse(100.0, 400.0, 190.0, 190.0, 0.0)  # ficha de 18 px: ~9,5% del plato
CROUPIER = (0, 150, 90, 250)  # a la izquierda, junto a la rueda
PLAYER = (500, 0, 130, 150)  # arriba a la derecha
REST = {CROUPIER: (40.0, 300.0), PLAYER: (560.0, 120.0)}  # manos fuera de las zonas


def _center(box):
    return (box[0] + box[2] / 2, box[1] + box[3] / 2)


class _Detector:
    def detect(self, _frame, _classes, _min_confidence):
        return [Detection("person", 0.9, CROUPIER), Detection("person", 0.8, PLAYER)]

    def close(self):
        pass


class _Estimator:
    """La muñeca de cada persona donde diga `wrists` (por su caja)."""

    def __init__(self):
        self.wrists = dict(REST)

    def estimate(self, _frame, box):
        x, y, w, h = box
        owner = min(
            self.wrists, key=lambda b: np.hypot(*np.subtract(_center(b), (x + w / 2, y + h / 2)))
        )
        points = np.zeros((17, 2), np.float32)
        scores = np.zeros(17, np.float32)
        points[LEFT_WRIST], scores[LEFT_WRIST] = self.wrists[owner], 0.9
        return Pose(points=points, scores=scores)

    def close(self):
        pass


class _Ronda(RouletteRound):
    """Ronda fija: el paño armado desde el principio."""

    def __init__(self, live: bool, state: str = ROUND_NO_MORE_BETS) -> None:
        super().__init__()
        self.state, self.window_since, self._live = state, 0.0, live

    def update(self, ball, timestamp, rotor=None):
        return None

    def ball_live(self, ball, timestamp):
        return self._live


def _table(chips, hand=None) -> np.ndarray:
    frame = np.full((*SIZE, 3), (200, 215, 225), np.uint8)  # paño crema
    for x, y in chips:
        cv2.circle(frame, (x, y), 9, (40, 40, 180), -1)
    if hand is not None:
        cv2.ellipse(frame, (int(hand[0]), int(hand[1])), (40, 22), 0, 0, 360, (90, 130, 190), -1)
    return frame


class _Mesa:
    def __init__(self, ronda: RouletteRound | None = None, zones=(ZONE,)) -> None:
        self.estimator = _Estimator()
        self.analyzer = RouletteAnalyzer(
            zones=list(zones), wheel=WHEEL, detector=_Detector(), estimator=self.estimator
        )
        self.t = 0.0
        self.results = []
        # El crupier toca la rueda: queda identificado.
        self.estimator.wrists[CROUPIER] = (WHEEL.cx, WHEEL.cy)
        self.frame(_table([(150, 150)]))
        self.estimator.wrists[CROUPIER] = REST[CROUPIER]
        if ronda is not None:
            self.analyzer._round = ronda

    def frame(self, image):
        result = self.analyzer.process_frame(image, self.t)
        self.results.append(result)
        self.t += 1 / FPS
        return result

    def touch(self, who, chips_after, chips_before=((150, 150),), path=((250, 200), (300, 220))):
        """`who` mete la mano en la zona, deja `chips_after` y se va."""
        for _ in range(4):
            self.frame(_table(chips_before))
        for x, y in path * 2:
            if who is not None:
                self.estimator.wrists[who] = (float(x), float(y))
            self.frame(_table(chips_before, hand=(x, y)))
        if who is not None:
            self.estimator.wrists[who] = REST[who]
        for _ in range(8):
            self.frame(_table(chips_after))

    def labels(self):
        return [d.label for r in self.results for d in r.detections]

    def triggers(self):
        return [d.label for r in self.results for d in r.triggers]


NEW_CHIP = [(150, 150), (300, 220)]


class TestPaStPost:
    def test_un_jugador_que_agrega_fichas_con_la_bola_girando_dispara_la_alarma(self):
        mesa = _Mesa(_Ronda(live=True))

        mesa.touch(PLAYER, NEW_CHIP)

        assert mesa.triggers() == [LABEL_PAST_POST]
        metrics = mesa.results[-1].metrics
        assert metrics["estado"] == "alerta" and metrics["incidentes"] == 1
        assert metrics["zonas"][0]["motivo"] == "no_va_mas"
        assert metrics["ultimo_incidente"]["manos"] == ["jugador"]
        # Donde aparecio la ficha: la mancha del cambio la contiene.
        x, y, w, h = metrics["zonas"][0]["posiciones"][0]
        assert x <= 300 <= x + w and y <= 220 <= y + h

    def test_un_brazo_que_queda_apoyado_en_el_borde_no_es_una_ficha(self):
        mesa = _Mesa(_Ronda(live=True))

        mesa.touch(None, [(150, 150)])
        arm = _table([(150, 150)])
        cv2.rectangle(arm, (110, 270), (390, 298), (90, 130, 190), -1)
        for _ in range(8):
            mesa.frame(arm)

        assert mesa.triggers() == []

    def test_el_crupier_moviendo_fichas_no_es_alerta(self):
        mesa = _Mesa(_Ronda(live=True))

        mesa.touch(CROUPIER, NEW_CHIP)

        assert mesa.triggers() == []
        assert "fichas_movidas" in mesa.labels()

    def test_con_la_bola_girando_un_cambio_sin_manos_vistas_es_alerta(self):
        mesa = _Mesa(_Ronda(live=True))

        mesa.touch(None, NEW_CHIP)

        assert mesa.triggers() == [LABEL_PAST_POST]

    def test_con_la_bola_caida_sin_un_jugador_a_la_vista_no_se_alerta(self):
        # El crupier empieza a cobrar antes de que se de el resultado.
        mesa = _Mesa(_Ronda(live=False))

        mesa.touch(None, NEW_CHIP)

        assert mesa.triggers() == []

    def test_con_la_bola_caida_un_jugador_que_agrega_fichas_es_alerta(self):
        mesa = _Mesa(_Ronda(live=False, state=ROUND_RESULT))

        mesa.touch(PLAYER, NEW_CHIP)

        assert mesa.triggers() == [LABEL_PAST_POST]

    def test_con_el_paño_libre_mover_fichas_es_normal(self):
        mesa = _Mesa()  # ronda real: sin bola, apuestas abiertas

        mesa.touch(PLAYER, NEW_CHIP)

        assert mesa.analyzer.round.state == ROUND_BETS
        assert mesa.triggers() == [] and "fichas_movidas" in mesa.labels()


class TestMarca:
    def test_la_marca_desarma_el_paño_y_el_toque_del_crupier(self):
        mesa = _Mesa(_Ronda(live=False, state=ROUND_RESULT))

        mesa.touch(CROUPIER, NEW_CHIP)  # marca el numero y deja la marca

        assert not mesa.analyzer.round.window_open
        assert mesa.triggers() == []

    def test_el_jugador_que_agrego_fichas_antes_de_la_marca_igual_cuenta(self):
        mesa = _Mesa(_Ronda(live=False, state=ROUND_RESULT), zones=(ZONE, OTHER_ZONE))
        for _ in range(4):
            mesa.frame(_table([(150, 150)]))
        # El jugador agrega una ficha en la zona 1 y, mientras la zona todavia
        # no quedo quieta, el crupier marca en la zona 2.
        for x, y in ((250, 200), (300, 220)):
            mesa.estimator.wrists[PLAYER] = (float(x), float(y))
            mesa.frame(_table([(150, 150)], hand=(x, y)))
        mesa.estimator.wrists[PLAYER] = REST[PLAYER]
        mesa.estimator.wrists[CROUPIER] = (500.0, 370.0)
        mesa.frame(_table(NEW_CHIP, hand=(500, 370)))
        mesa.estimator.wrists[CROUPIER] = REST[CROUPIER]
        for _ in range(8):
            mesa.frame(_table(NEW_CHIP))

        assert not mesa.analyzer.round.window_open
        assert mesa.triggers() == [LABEL_PAST_POST]

    def test_lo_que_sigue_al_toque_con_el_que_marco_el_crupier_ya_no_cuenta(self):
        # El crupier apoya la mano con la bola todavia girando, la bola cae y
        # con esa misma mano marca; despues un jugador toca antes de que la
        # zona quede quieta. La ventana ya se cerro: no es un past-post.
        ronda = _Ronda(live=True)
        mesa = _Mesa(ronda)
        for _ in range(4):
            mesa.frame(_table([(150, 150)]))
        mesa.estimator.wrists[CROUPIER] = (250.0, 200.0)
        mesa.frame(_table([(150, 150)], hand=(250, 200)))
        ronda._live = False  # la bola cayo: la mano del crupier en el paño es la marca
        mesa.frame(_table([(150, 150)], hand=(260, 205)))
        mesa.estimator.wrists[CROUPIER] = REST[CROUPIER]
        for x, y in ((300, 220), (320, 230)):
            mesa.estimator.wrists[PLAYER] = (float(x), float(y))
            mesa.frame(_table([(150, 150)], hand=(x, y)))
        mesa.estimator.wrists[PLAYER] = REST[PLAYER]
        for _ in range(8):
            mesa.frame(_table(NEW_CHIP))

        assert not ronda.window_open
        assert mesa.triggers() == []


class TestJugadaNuevaPorLaRueda:
    def test_lo_que_se_estaba_tocando_cuando_la_rueda_abre_la_jugada_es_una_apuesta(self):
        # El paño quedo armado (no se vio al crupier marcar); un jugador esta
        # apostando cuando el crupier re-impulsa el plato: jugada nueva. Corre
        # el _update_scene de verdad; solo el cambio del plato es simulado.
        ronda = _Ronda(live=False, state=ROUND_RESULT)
        mesa = _Mesa(ronda)
        events = iter([None] * 6 + ["sentido"] + [None] * 100)
        mesa.analyzer._rotor.update = lambda speed, t: next(events)
        for _ in range(4):
            mesa.frame(_table([(150, 150)]))
        for x, y in ((250, 200), (300, 220)):
            mesa.estimator.wrists[PLAYER] = (float(x), float(y))
            mesa.frame(_table([(150, 150)], hand=(x, y)))
        mesa.estimator.wrists[PLAYER] = REST[PLAYER]
        for _ in range(8):
            mesa.frame(_table(NEW_CHIP))

        assert ronda.games == 1 and not ronda.window_open
        assert mesa.triggers() == []
