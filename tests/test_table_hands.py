"""Manos en la mesa de ruleta (2026-09-30): quien es el crupier, que no se
repitan las manos de una caja que engloba a dos personas, y lo que el modo
ruleta publica de cada mano para dibujarla en el video."""

from __future__ import annotations

import numpy as np
import pytest

from aurea_vms.core.analytics.pose_backend import (
    LEFT_HAND,
    LEFT_WRIST,
    RIGHT_HAND,
    RIGHT_WRIST,
    Pose,
)
from aurea_vms.core.analytics.roulette_analyzer import RouletteAnalyzer, WheelEllipse
from aurea_vms.core.analytics.table_hands import (
    DETAIL_MAX,
    ROLE_CROUPIER,
    ROLE_PLAYER,
    ROLE_UNKNOWN,
    Hand,
    TableHands,
    hands_from_pose,
)
from aurea_vms.core.events import Detection

FRAME = np.zeros((960, 1280, 3), np.uint8)
WHEEL = WheelEllipse(320.0, 240.0, 200.0, 200.0, 0.0)  # plato de radio 100


class _Detector:
    """Devuelve siempre las mismas cajas de personas: (caja, confianza)."""

    def __init__(self, people):
        self.people = people

    def detect(self, _frame, _classes, _min_confidence):
        return [Detection("person", confidence, box) for box, confidence in self.people]

    def close(self):
        pass


class _Estimator:
    """Muñecas fijas por persona, elegida por el centro de la caja (la caja
    llega agrandada por _pad, el centro no cambia)."""

    def __init__(self, wrists):
        self.wrists = wrists  # {centro de la caja: [(x, y), ...]}

    def estimate(self, _frame, box):
        x, y, w, h = box
        center = min(self.wrists, key=lambda c: np.hypot(c[0] - x - w / 2, c[1] - y - h / 2))
        points = np.zeros((17, 2), np.float32)
        scores = np.zeros(17, np.float32)
        for joint, point in zip((LEFT_WRIST, RIGHT_WRIST), self.wrists[center], strict=False):
            points[joint] = point
            scores[joint] = 0.9
        return Pose(points=points, scores=scores)

    def close(self):
        pass


def _center(box):
    return (box[0] + box[2] / 2, box[1] + box[3] / 2)


def _hands(people, touches=lambda x, y: False, on_table=None):
    detector = _Detector([(box, confidence) for box, confidence, _ in people])
    estimator = _Estimator({_center(box): wrists for box, _, wrists in people})
    return TableHands(touches, detector=detector, estimator=estimator, on_table=on_table)


CROUPIER = (100, 150, 120, 200)
PLAYER = (500, 20, 100, 150)


class TestManos:
    def test_la_caja_que_engloba_a_dos_no_repite_sus_manos(self):
        # La caja chica (mas confianza) y una grande floja con la misma mano.
        big = (40, 100, 320, 330)
        table = _hands(
            [
                (CROUPIER, 0.8, [(170, 250), (210, 330)]),
                (big, 0.3, [(172, 252), (330, 400)]),
            ]
        )

        people = table.people(FRAME)

        hands = [(round(h.x), round(h.y)) for person in people for h in person.hands]
        assert len(hands) == 3
        assert people[0].box == CROUPIER and len(people[0].hands) == 2

    def test_una_persona_sin_manos_no_cuenta(self):
        table = _hands([(CROUPIER, 0.8, [(170, 250)]), (PLAYER, 0.2, [])])

        assert [person.box for person in table.people(FRAME)] == [CROUPIER]

    def test_el_crupier_es_quien_toca_la_referencia_y_se_lo_sigue(self):
        wheel_hand = (230.0, 240.0)
        people = [(CROUPIER, 0.8, [wheel_hand]), (PLAYER, 0.8, [(520, 100)])]
        table = _hands(people, touches=lambda x, y: (x, y) == wheel_hand)

        roles = {person.box: person.role for person in table.people(FRAME)}
        assert roles == {CROUPIER: ROLE_CROUPIER, PLAYER: ROLE_PLAYER}

        # Deja de tocar la rueda: sigue siendo el crupier por su puesto.
        table._estimator.wrists[_center(CROUPIER)] = [(170.0, 250.0)]
        roles = {person.box: person.role for person in table.people(FRAME)}
        assert roles == {CROUPIER: ROLE_CROUPIER, PLAYER: ROLE_PLAYER}

    def test_sin_nadie_que_toque_la_rueda_no_hay_roles(self):
        table = _hands([(CROUPIER, 0.8, [(170, 250)]), (PLAYER, 0.8, [(520, 100)])])

        assert {person.role for person in table.people(FRAME)} == {ROLE_UNKNOWN}


class TestSeguimiento:
    def test_una_caja_gigante_que_engloba_a_varios_se_descarta(self):
        giant = (0, 0, 1000, 900)  # 73% del cuadro
        table = _hands([(giant, 0.9, [(170, 250)]), (CROUPIER, 0.5, [(172, 252)])])

        assert [person.box for person in table.people(FRAME)] == [CROUPIER]

    def test_el_crupier_sigue_siendo_crupier_aunque_deje_la_rueda_y_se_mueva(self):
        wheel_hand = (230.0, 240.0)
        people = [(CROUPIER, 0.8, [wheel_hand]), (PLAYER, 0.8, [(520, 100)])]
        table = _hands(people, touches=lambda x, y: (x, y) == wheel_hand)
        table.people(FRAME)
        croupier_id = next(p.track for p in table.people(FRAME) if p.role == ROLE_CROUPIER)

        # Se corre 40 px y deja de tocar la rueda: YOLOX da otra caja.
        moved = (CROUPIER[0] + 40, CROUPIER[1] + 10, CROUPIER[2], CROUPIER[3])
        table._detector.people = [(moved, 0.8), (PLAYER, 0.8)]
        table._estimator.wrists = {_center(moved): [(210.0, 330.0)], _center(PLAYER): [(520, 100)]}
        for _ in range(4):
            people_now = table.people(FRAME)

        croupier = [p for p in people_now if p.role == ROLE_CROUPIER]
        assert [p.track for p in croupier] == [croupier_id] and croupier[0].box == moved

    def test_el_jugador_de_enfrente_no_le_roba_la_pista_al_crupier(self):
        # Vista desde arriba la caja del crupier es enorme: medida con su
        # diagonal, la caja de un jugador a 500 px "era" el crupier.
        big_croupier = (0, 200, 500, 700)  # diagonal ~860
        player = (560, 0, 150, 200)  # a ~500 px de su centro
        wheel_hand = (230.0, 800.0)
        people = [(big_croupier, 0.5, [wheel_hand]), (player, 0.9, [(600, 150)])]
        table = _hands(people, touches=lambda x, y: (x, y) == wheel_hand)
        table.people(FRAME)
        table.people(FRAME)
        table._estimator.wrists[_center(big_croupier)] = [(300.0, 500.0)]
        for _ in range(6):
            roles = {p.box: p.role for p in table.people(FRAME)}

        assert roles == {big_croupier: ROLE_CROUPIER, player: ROLE_PLAYER}

    def test_si_una_muestra_no_le_ve_las_manos_al_crupier_nadie_le_roba_el_rol(self):
        wheel_hand = (230.0, 240.0)
        neighbor = (110, 160, 100, 200)  # parado en el puesto del crupier (otro empleado)
        people = [
            (CROUPIER, 0.8, [wheel_hand]),
            (neighbor, 0.8, [(120, 340)]),
        ]
        table = _hands(people, touches=lambda x, y: (x, y) == wheel_hand)
        table.people(FRAME)
        table._estimator.wrists[_center(CROUPIER)] = []  # dos muestras sin manos
        table.people(FRAME)
        roles = {p.box: p.role for p in table.people(FRAME)}
        assert roles.get(neighbor) != ROLE_CROUPIER

        table._estimator.wrists[_center(CROUPIER)] = [(170.0, 250.0)]
        roles = {p.box: p.role for p in table.people(FRAME)}
        assert roles[CROUPIER] == ROLE_CROUPIER and roles[neighbor] != ROLE_CROUPIER

    def test_si_yolox_lo_pierde_un_momento_se_sostiene_su_caja(self):
        table = _hands([(CROUPIER, 0.8, [(170, 250)])])
        table.people(FRAME)
        table.people(FRAME)
        table._detector.people = []  # dos rondas sin verlo

        assert [p.box for p in table.people(FRAME)] == [CROUPIER]

    def test_quien_nunca_toco_la_mesa_no_es_jugador(self):
        wheel_hand = (230.0, 240.0)
        spectator = (900, 700, 120, 200)
        people = [
            (CROUPIER, 0.8, [wheel_hand]),
            (PLAYER, 0.8, [(520, 100)]),
            (spectator, 0.8, [(950, 800)]),
        ]
        table = _hands(
            people, touches=lambda x, y: (x, y) == wheel_hand, on_table=lambda x, y: y < 600
        )

        roles = {person.box: person.role for person in table.people(FRAME)}
        assert roles[PLAYER] == ROLE_PLAYER and roles[spectator] == ROLE_UNKNOWN


def _wholebody(hands: dict[str, list[tuple[float, float]]], wrists=()) -> Pose:
    """Pose de cuerpo entero (133 puntos) con las manos dadas (21 puntos por
    lado, en orden) y, si hay, las muñecas del cuerpo."""
    points = np.zeros((133, 2), np.float32)
    scores = np.zeros(133, np.float32)
    for side, ids in (("izq", LEFT_HAND), ("der", RIGHT_HAND)):
        for index, point in zip(ids, hands.get(side, ()), strict=False):
            points[index], scores[index] = point, 0.8
    for joint, point in zip((LEFT_WRIST, RIGHT_WRIST), wrists, strict=False):
        points[joint], scores[joint] = point, 0.9
    return Pose(points=points, scores=scores)


def _open_hand(x: float, y: float) -> list[tuple[float, float]]:
    """21 puntos de una mano abierta hacia arriba con la muñeca en (x, y)."""
    points = [(x, y)]
    for finger in range(5):
        for joint in range(1, 5):
            points.append((x - 10 + 5 * finger, y - 6 * joint))
    return points


class TestManoDe21Puntos:
    def test_la_mano_es_la_palma_con_sus_21_puntos(self):
        pose = _wholebody({"izq": _open_hand(100, 200)}, wrists=[(100, 200)])

        hands = hands_from_pose(pose.points, pose.scores, diagonal=800)

        assert len(hands) == 1
        hand = hands[0]
        assert hand.side == "izq" and len(hand.points) == 21
        # palma: la muñeca y los nudillos del indice al meñique (5, 9, 13, 17)
        assert (hand.x, hand.y) == pytest.approx((102.0, 195.2), abs=0.5)

    def test_con_pocos_puntos_vuelve_a_la_muñeca(self):
        pose = _wholebody({"izq": _open_hand(100, 200)[:3]}, wrists=[(100, 200)])

        hands = hands_from_pose(pose.points, pose.scores, diagonal=800)

        assert hands[0].points == () and (hands[0].x, hands[0].y) == (100.0, 200.0)

    def test_una_mano_desparramada_es_una_lectura_rota(self):
        spread = [(100 + 40 * i, 200) for i in range(21)]  # 800 px de punta a punta
        pose = _wholebody({"izq": spread}, wrists=[(100, 200)])

        hands = hands_from_pose(pose.points, pose.scores, diagonal=800)

        assert hands[0].points == ()

    def test_toca_con_la_punta_de_un_dedo(self):
        pose = _wholebody({"der": _open_hand(100, 200)})
        hand = hands_from_pose(pose.points, pose.scores, diagonal=800)[0]

        # Solo las puntas de los dedos (y 176) cruzan la linea y < 180.
        assert hand.touches(lambda x, y: y < 180)
        assert not Hand(hand.x, hand.y, hand.score).touches(lambda x, y: y < 180)


class _Detail(_Estimator):
    """Pose de cuerpo entero: una mano abierta en la muñeca de cada persona."""

    calls = 0

    def estimate(self, frame, box):
        _Detail.calls += 1
        body = super().estimate(frame, box)
        wrist = body.points[LEFT_WRIST]
        return _wholebody({"izq": _open_hand(*wrist)}, wrists=[tuple(wrist)])


class TestDetalle:
    def _table(self, interest):
        wheel_hand = (230.0, 240.0)
        people = [(CROUPIER, 0.8, [wheel_hand]), (PLAYER, 0.8, [(520, 100)])]
        detector = _Detector([(box, c) for box, c, _ in people])
        wrists = {_center(box): w for box, _, w in people}
        return TableHands(
            lambda x, y: (x, y) == wheel_hand,
            detector=detector,
            estimator=_Estimator(dict(wrists)),
            detail_estimator=_Detail(dict(wrists)),
            interest=interest,
        )

    def test_el_crupier_y_quien_juega_en_el_paño_van_con_21_puntos(self):
        table = self._table(interest=lambda x, y: x > 480)  # el paño a la derecha
        table.people(FRAME)  # primera muestra: todavia no se sabe quien es quien
        people = {p.box: p for p in table.people(FRAME)}

        assert all(len(h.points) == 21 for h in people[CROUPIER].hands)
        assert all(len(h.points) == 21 for h in people[PLAYER].hands)

    def test_fuera_del_paño_alcanza_con_un_punto(self):
        table = self._table(interest=lambda x, y: False)
        table.people(FRAME)
        people = {p.box: p for p in table.people(FRAME)}

        assert all(h.points == () for h in people[PLAYER].hands)
        assert all(len(h.points) == 21 for h in people[CROUPIER].hands)

    def test_a_lo_sumo_detail_max_personas_por_muestra(self):
        many = [((100 + 150 * i, 600, 100, 200), 0.8, [(150 + 150 * i, 650)]) for i in range(6)]
        detector = _Detector([(box, c) for box, c, _ in many])
        wrists = {_center(box): w for box, _, w in many}
        detail = _Detail(dict(wrists))
        table = TableHands(
            lambda x, y: False,
            detector=detector,
            estimator=_Estimator(dict(wrists)),
            detail_estimator=detail,
            interest=lambda x, y: True,
        )
        table.people(FRAME)
        _Detail.calls = 0
        table.people(FRAME)

        assert _Detail.calls == DETAIL_MAX

    def test_una_mano_que_la_pose_pierde_una_muestra_se_sostiene(self):
        table = _hands([(CROUPIER, 0.8, [(170, 250)])])
        table.people(FRAME)
        table._estimator.wrists[_center(CROUPIER)] = []

        assert [(h.x, h.y) for p in table.people(FRAME) for h in p.hands] == [(170.0, 250.0)]
        assert table.people(FRAME) == []


class TestModoRuleta:
    def _analyzer(self, croupier_hand):
        zone = (480, 60, 140, 100)
        detector = _Detector([(CROUPIER, 0.8), (PLAYER, 0.8)])
        estimator = _Estimator(
            {_center(CROUPIER): [croupier_hand], _center(PLAYER): [(520.0, 100.0)]}
        )
        return RouletteAnalyzer(zones=[zone], wheel=WHEEL, detector=detector, estimator=estimator)

    def test_el_borde_del_tazon_ya_es_tocar_la_rueda(self):
        # El crupier impulsa la rueda desde la pista de la bola, por fuera del
        # plato: en la RA-04, a 1,18-1,29 radios del plato.
        metrics = self._analyzer((320.0 - 125.0, 240.0)).process_frame(FRAME, 0.0).metrics

        croupier = [hand for hand in metrics["manos"] if hand["rol"] == ROLE_CROUPIER]
        assert len(croupier) == 1 and croupier[0]["en_rueda"] is True
        assert metrics["crupier_ubicado"] is True

    def test_la_mano_apoyada_lejos_de_la_rueda_no_la_toca(self):
        metrics = self._analyzer((320.0 - 150.0, 240.0)).process_frame(FRAME, 0.0).metrics

        assert not any(hand["en_rueda"] for hand in metrics["manos"])
        assert metrics["crupier_ubicado"] is False

    def test_cada_mano_sale_con_su_persona_y_su_zona(self):
        metrics = self._analyzer((320.0 - 125.0, 240.0)).process_frame(FRAME, 0.0).metrics

        by_role = {hand["rol"]: hand for hand in metrics["manos"]}
        player = by_role[ROLE_PLAYER]
        assert metrics["personas"][player["persona"]]["caja"] == list(PLAYER)
        assert player["zona"] == 0 and player["en_rueda"] is False
        assert by_role[ROLE_CROUPIER]["zona"] is None
        assert metrics["zonas"][0]["manos"] == [ROLE_PLAYER]
