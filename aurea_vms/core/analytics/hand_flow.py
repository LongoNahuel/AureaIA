"""Manos cuadro a cuadro entre detecciones completas.

La deteccion completa de las manos (YOLOX + pose, table_hands) cuesta
100-250 ms con 3-4 personas en la mesa: no llega a 60 fps en CPU. Entre una
deteccion y la siguiente, cada punto de cada mano (la palma y los 21 de
RTMW) se sigue con flujo optico (Lucas-Kanade piramidal) sobre el cuadro a
FLOW_SCALE: las marcas acompañan al video a la velocidad del stream y cada
deteccion completa las vuelve a anclar.

- Cuando llega una deteccion, fue calculada sobre un cuadro de hace
  100-250 ms: sus puntos se llevan de ese cuadro al actual en un salto (la
  piramide de LK_LEVELS niveles alcanza para lo que se mueve una mano en ese
  tiempo).
- Un punto vale si el flujo de ida y vuelta lo devuelve a menos de
  FB_MAX_ERROR px de donde estaba (Kalal, "Forward-Backward Error").
- La mano se mueve con la MEDIANA de sus puntos buenos (si ninguno sirve,
  con la de la persona), y cada punto puede apartarse de esa mediana hasta
  `max_deviation` px (los dedos se abren y se cierran). Medido sobre la RA-04
  (50,4 s): con el flujo de cada punto por separado, los dedos apoyados en la
  rueda se pegaban a la textura del plato y se iban girando con el; la ida y
  vuelta no lo ve, porque la rueda se sigue bien en los dos sentidos.
"""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from aurea_vms.core.analytics.table_hands import Hand, Person

FLOW_SCALE = 0.5
LK_WINDOW = (21, 21)
LK_LEVELS = 3
LK_CRITERIA = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03)
FB_MAX_ERROR = 1.5  # px a FLOW_SCALE
# Cuanto puede apartarse un punto de la mediana de su mano entre dos cuadros
# seguidos (px del cuadro). Al traer una deteccion vieja al cuadro actual la
# mano se mueve rigida (0).
STEP_DEVIATION = 6.0


def flow_gray(frame: np.ndarray) -> np.ndarray:
    """El cuadro en gris a FLOW_SCALE, el que usa el seguimiento."""
    small = cv2.resize(frame, None, fx=FLOW_SCALE, fy=FLOW_SCALE, interpolation=cv2.INTER_LINEAR)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def _hand_points(hand: Hand) -> list[tuple[float, float]]:
    return [(hand.x, hand.y)] + [p for p in hand.points if p is not None]


def _median_shift(shifts: np.ndarray, good: np.ndarray) -> np.ndarray | None:
    return np.median(shifts[good], axis=0) if good.any() else None


def track_people(
    previous: np.ndarray,
    current: np.ndarray,
    people: list[Person],
    max_deviation: float = STEP_DEVIATION,
) -> list[Person]:
    """Las personas con sus manos llevadas del cuadro `previous` al `current`
    (grises de flow_gray). No toca las originales: devuelve copias."""
    points = [point for person in people for hand in person.hands for point in _hand_points(hand)]
    if not points or previous is None or previous.shape != current.shape:
        return people
    start = np.float32(points).reshape(-1, 1, 2) * FLOW_SCALE
    params = {"winSize": LK_WINDOW, "maxLevel": LK_LEVELS, "criteria": LK_CRITERIA}
    moved, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, start, None, **params)
    back, status_back, _ = cv2.calcOpticalFlowPyrLK(current, previous, moved, None, **params)
    error = np.linalg.norm((back - start).reshape(-1, 2), axis=1)
    good = (status.ravel() == 1) & (status_back.ravel() == 1) & (error < FB_MAX_ERROR)
    shifts = (moved - start).reshape(-1, 2) / FLOW_SCALE

    tracked: list[Person] = []
    index = 0
    for person in people:
        counts = [len(_hand_points(hand)) for hand in person.hands]
        span = slice(index, index + sum(counts))
        person_shift = _median_shift(shifts[span], good[span])
        hands = []
        for hand, count in zip(person.hands, counts, strict=True):
            own = slice(index, index + count)
            index += count
            shift = _median_shift(shifts[own], good[own])
            if shift is None:
                shift = person_shift
            if shift is None:
                hands.append(hand)  # nada de la persona se pudo seguir: queda donde estaba
                continue
            hands.append(_moved_hand(hand, shifts[own], good[own], shift, max_deviation))
        box = person.box
        if person_shift is not None:
            dx, dy = person_shift
            box = (int(box[0] + dx), int(box[1] + dy), box[2], box[3])
        tracked.append(replace(person, box=box, hands=hands))
    return tracked


def _moved_hand(
    hand: Hand, shifts: np.ndarray, good: np.ndarray, median: np.ndarray, max_deviation: float
) -> Hand:
    """Cada punto con la mediana de su mano, mas su propio desvio (si es un
    punto bueno) acotado a `max_deviation`."""

    def shift(k: int) -> np.ndarray:
        if not good[k] or max_deviation <= 0:
            return median
        deviation = shifts[k] - median
        size = float(np.hypot(*deviation))
        if size > max_deviation:
            deviation = deviation * (max_deviation / size)
        return median + deviation

    palm = shift(0)
    moved_points = []
    k = 1
    for point in hand.points:
        if point is None:
            moved_points.append(None)
            continue
        dx, dy = shift(k)
        moved_points.append((point[0] + float(dx), point[1] + float(dy)))
        k += 1
    return Hand(
        hand.x + float(palm[0]),
        hand.y + float(palm[1]),
        hand.score,
        hand.side,
        tuple(moved_points),
    )
