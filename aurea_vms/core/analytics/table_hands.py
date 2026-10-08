"""Manos en una mesa de juego: del crupier y de los jugadores.

Personas con YOLOX-Tiny (el mismo detector y la misma sesion que ya usa el
proyecto) y pose con RTMPose-s sobre cada una (pose_backend). La mano no es
la muñeca: esta un poco mas alla, en la direccion del antebrazo
(codo -> muñeca), asi que se extrapola HAND_REACH del antebrazo.

**Quien es el crupier.** Lo que solo hace el crupier: tocar la rueda
(impulsarla, lanzar y retirar la bola). La primera persona que mete una mano
en la zona de referencia (en ruleta, la rueda) queda marcada como crupier y
se la sigue por su posicion: la persona mas cercana a donde estaba, si queda
cerca y sin otra a la misma distancia. Mientras nadie toca la rueda, las
manos se muestran como "persona".

Medido (2026-09-30) sobre la RA-04 (2048x1536): el crupier sale en los 5
cuadros de prueba con las dos muñecas sobre las manos (a los 50 s, una
sobre la rueda: el impulso); los jugadores de la derecha, con las manos en el
borde del paño; se escapa algun cliente cortado por el borde del cuadro.
YOLOX tarda ~40 ms por cuadro sin carga y RTMPose ~9 ms por persona.

En corrido, de 44 a 75 s a 5 fps: el crupier queda identificado en el
re-impulso (50,6 s) y se lo sigue hasta el final salvo 1 s en que se inclina
sobre la mesa (53,6-54,4 s). El modo ruleta completo cuesta 91 ms por
muestra en promedio (312 ms el peor).

**Seguimiento** (2026-09-30, segunda vuelta). Cada persona es una pista que
se sigue entre muestras por el solape de su caja: el rol queda pegado a la
pista (el crupier sigue siendo crupier aunque en un cuadro no toque la
rueda), las manos se suavizan, y si YOLOX la pierde se sostiene su ultima
caja TRACK_HOLD rondas (la pose confirma si sigue ahi). Jugador es quien
alguna vez puso una mano sobre la mesa: el espectador de detras de la soga
de la RA-04 salia como jugador y ahora queda como "persona".

**Manos de 21 puntos** (2026-09-30, tercera vuelta). La muñeca extrapolada
cae atras de la mano. Donde importa se usa RTMW (el modelo de cuerpo entero
que ya usa el consumo de sustancias, pose_backend.WHOLEBODY_MODEL): da la
muñeca y las cuatro articulaciones de cada dedo, 21 puntos por mano, y la
mano es la palma (muñeca y nudillos). Medido sobre la RA-04 en 8 cuadros:
los puntos caen sobre los dedos, y 46 de 74 manos salen con confianza
mediana >= 0,3. Cuesta 2,6 veces RTMPose-s por persona (67 contra 26 ms con
la maquina cargada), asi que corre solo para DETAIL_MAX personas por
muestra: el crupier y quien tenga las manos en la zona de interes (el paño
y la rueda); el resto sigue con RTMPose-s y la mano de un punto. Cada
persona corre UN modelo: RTMW trae tambien los 17 puntos del cuerpo. Una
mano toca algo si lo toca cualquiera de sus puntos (la punta de un dedo en
el borde de la rueda identifica al crupier).

La mirada (orientacion de la cabeza) se probo y se saco: desde arriba la
pose ve nariz y orejas en el 38% de las muestras, y Nahuel prefirio el
costo en las manos.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from aurea_vms.core.analytics.object_detector_backend import YoloxDetector
from aurea_vms.core.analytics.pose_backend import (
    LEFT_HAND,
    LEFT_WRIST,
    RIGHT_HAND,
    RIGHT_WRIST,
    WHOLEBODY_MODEL,
    PoseEstimator,
)

LEFT_ELBOW, RIGHT_ELBOW = 7, 8
SIDE_LEFT, SIDE_RIGHT = "izq", "der"
ARMS = (
    (SIDE_LEFT, LEFT_WRIST, LEFT_ELBOW, LEFT_HAND),
    (SIDE_RIGHT, RIGHT_WRIST, RIGHT_ELBOW, RIGHT_HAND),
)
# Con la camara cenital YOLOX da poca confianza a la gente: en la RA-04 el
# jugador de la derecha sale con 0,12-0,5 y con 0,3 se perdia la mitad de
# las muestras. Las cajas flojas que no son una persona casi nunca dan una
# muñeca con HAND_MIN_SCORE, asi que la pose hace de filtro.
PERSON_MIN_CONFIDENCE = 0.12
HAND_MIN_SCORE = 0.3
# Mano de 21 puntos (RTMW): cada punto vale con esta confianza, y la mano con
# al menos HAND_MIN_POINTS; si no, se usa la muñeca extrapolada. Los puntos no
# pueden abarcar mas de HAND_MAX_EXTENT diagonales de la persona (una mano
# desparramada es una lectura rota).
HAND_POINT_MIN_SCORE = 0.3
HAND_MIN_POINTS = 5
HAND_MAX_EXTENT = 0.3
PALM = (0, 5, 9, 13, 17)  # muñeca y nudillos, dentro de los 21 de la mano
# Personas por muestra con el modelo de cuerpo entero (el resto, RTMPose-s).
DETAIL_MAX = 3
# Una caja grande que engloba a dos personas repite sus manos. Dos manos de
# personas distintas a menos de esta fraccion de la diagonal de la persona
# mas chica son la misma: queda la de la caja con mas confianza.
HAND_DUPLICATE = 0.08
# Una caja de mas de esta fraccion del cuadro engloba a varias personas (en la
# RA-04 aparecen de 1210x1257 sobre 2048x1536; el crupier ocupa ~12%): se
# descarta, porque con mas confianza que la del crupier se quedaba con sus
# manos y las mostraba como de una "persona".
PERSON_MAX_AREA = 0.3
HAND_REACH = 0.35  # fraccion del antebrazo mas alla de la muñeca
BOX_PADDING = 0.08  # la caja de YOLOX a veces corta la mano estirada
# El crupier se sigue si la persona mas cercana esta a menos de esta fraccion
# de la diagonal de su caja, y la segunda queda al menos CROUPIER_AMBIGUITY
# veces mas lejos. Con 0,6 se perdia en la RA-04 al inclinarse sobre la mesa
# (54-58 s): la caja cambia mucho de forma.
CROUPIER_MATCH = 1.0
CROUPIER_AMBIGUITY = 1.5
CROUPIER_SMOOTHING = 0.3
# Alguien es jugador solo si no esta en el puesto del crupier y alguna vez
# apoyo una mano sobre la mesa; si no, queda como "persona". El puesto es
# por donde anduvo el crupier (los centros de su caja mientras su identidad
# esta confirmada: la misma pista desde que toco la rueda), con un radio de
# STATION_RADIUS diagonales de su caja. Antes era 1,2 diagonales de la caja
# de cada uno (el jugador de la derecha de la RA-04, a ~1000 px, quedaba
# "cerca" en el 57% de sus manos) y despues una sola caja, la del toque a la
# rueda: cuando el crupier se iba a la bandeja de fichas, una caja suelta con
# su mano quedaba afuera y salia como jugador (RA-04, 58 s).
STATION_RADIUS = 0.25
STATION_MEMORY = 300  # muestras (~1 minuto a 5 por segundo)
# YOLOX corre un cuadro de cada PEOPLE_EVERY; en el intermedio se reusan las
# cajas (la gente se mueve poco en 0,2 s) y la pose, que sigue las manos,
# corre siempre.
PEOPLE_EVERY = 2
# Seguimiento: una caja nueva es la misma persona si se solapa al menos
# TRACK_IOU o si su centro quedo a menos de TRACK_DISTANCE diagonales. Una
# pista que YOLOX no ve se sostiene TRACK_HOLD rondas de deteccion.
TRACK_IOU = 0.25
TRACK_DISTANCE = 0.5
TRACK_HOLD = 2
# Suavizado de manos entre muestras (peso de la lectura nueva), por lado. Una
# mano que salto mas de HAND_JUMP diagonales no se promedia: es otra lectura.
# Una mano que la pose pierde una muestra se sostiene HAND_HOLD muestras.
HAND_SMOOTHING = 0.6
HAND_JUMP = 0.12
HAND_HOLD = 1

ROLE_CROUPIER = "crupier"
ROLE_PLAYER = "jugador"
ROLE_UNKNOWN = "persona"

Box = tuple[int, int, int, int]


Point = tuple[float, float]


@dataclass(frozen=True)
class Hand:
    """Una mano: (x, y) es la palma (o la muñeca extrapolada, si no hay
    puntos) y `points` los 21 de RTMW (None los que no se ven)."""

    x: float
    y: float
    score: float
    side: str = ""
    points: tuple[Point | None, ...] = ()

    def touches(self, inside: Callable[[float, float], bool]) -> bool:
        """La palma o cualquier punto de la mano cae adentro."""
        return inside(self.x, self.y) or any(p is not None and inside(*p) for p in self.points)

    def moved(self, dx: float, dy: float) -> Hand:
        points = tuple(None if p is None else (p[0] + dx, p[1] + dy) for p in self.points)
        return Hand(self.x + dx, self.y + dy, self.score, self.side, points)


@dataclass
class Person:
    box: Box
    hands: list[Hand]
    role: str = ROLE_UNKNOWN
    track: int = -1

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.box
        return (x + w / 2, y + h / 2)

    @property
    def diagonal(self) -> float:
        return float(np.hypot(self.box[2], self.box[3]))

    def as_metrics(self) -> dict:
        return {
            "caja": list(self.box),
            "rol": self.role,
            "id": self.track,
        }


@dataclass
class _Track:
    id: int
    box: Box
    missed: int = 0
    on_table: bool = False
    hands: list[Hand] = field(default_factory=list)
    missing: dict[str, int] = field(default_factory=dict)  # muestras sin ver cada lado


def hands_from_pose(points: np.ndarray, scores: np.ndarray, diagonal: float = 0.0) -> list[Hand]:
    """Las manos de una pose: de 21 puntos si la pose es de cuerpo entero y
    la mano se ve, si no la muñeca extrapolada HAND_REACH del antebrazo."""
    hands = []
    for side, wrist, elbow, finger_ids in ARMS:
        detailed = _detailed_hand(points, scores, side, finger_ids, diagonal)
        if detailed is not None:
            hands.append(detailed)
            continue
        if scores[wrist] < HAND_MIN_SCORE:
            continue
        position = points[wrist].astype(np.float64)
        if scores[elbow] >= HAND_MIN_SCORE:
            position = position + HAND_REACH * (position - points[elbow])
        hands.append(Hand(float(position[0]), float(position[1]), float(scores[wrist]), side))
    return hands


def _detailed_hand(
    points: np.ndarray, scores: np.ndarray, side: str, finger_ids, diagonal: float
) -> Hand | None:
    if len(scores) <= max(finger_ids):
        return None  # pose de cuerpo solo (RTMPose-s)
    seen = [i for i in finger_ids if scores[i] >= HAND_POINT_MIN_SCORE]
    if len(seen) < HAND_MIN_POINTS:
        return None
    hand = np.array([points[i] for i in seen], dtype=np.float64)
    extent = float(np.hypot(*(hand.max(axis=0) - hand.min(axis=0))))
    if diagonal > 0 and extent > HAND_MAX_EXTENT * diagonal:
        return None
    all_points = tuple(
        (float(points[i][0]), float(points[i][1])) if scores[i] >= HAND_POINT_MIN_SCORE else None
        for i in finger_ids
    )
    palm = [all_points[k] for k in PALM if all_points[k] is not None]
    x, y = np.mean(palm if len(palm) >= 2 else hand, axis=0)
    return Hand(float(x), float(y), float(np.mean(scores[seen])), side, all_points)


def _pad(box, width: int, height: int) -> tuple[float, float, float, float]:
    x, y, w, h = box
    dx, dy = w * BOX_PADDING, h * BOX_PADDING
    x0, y0 = max(0.0, x - dx), max(0.0, y - dy)
    x1, y1 = min(float(width), x + w + dx), min(float(height), y + h + dy)
    return (x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))


def _iou(a: Box, b: Box) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / float(aw * ah + bw * bh - inter)


def _same_person(track: Box, box: Box) -> float:
    """Que tan probable es que `box` sea la persona de `track` (0 = no)."""
    overlap = _iou(track, box)
    if overlap >= TRACK_IOU:
        return 1.0 + overlap
    tx, ty, tw, th = track
    bx, by, bw, bh = box
    distance = np.hypot(tx + tw / 2 - bx - bw / 2, ty + th / 2 - by - bh / 2)
    # Con la caja mas chica: la del crupier, vista desde arriba, mide ~1200 px
    # de diagonal y con la suya el jugador que apostaba enfrente le robaba la
    # pista (RA-04, 58 s).
    reach = TRACK_DISTANCE * min(np.hypot(tw, th), np.hypot(bw, bh))
    return float(1.0 - distance / reach) if distance < reach else 0.0


def _seen(hand: Hand, owner: Person, people: list[Person]) -> bool:
    """La mano ya la tiene otra persona (de una caja con mas confianza)."""
    for other in people:
        limit = HAND_DUPLICATE * min(owner.diagonal, other.diagonal)
        if any(np.hypot(hand.x - seen.x, hand.y - seen.y) < limit for seen in other.hands):
            return True
    return False


def _smooth_hands(track: _Track, current: list[Hand], diagonal: float) -> list[Hand]:
    """Promedia cada mano con la del mismo lado de la muestra anterior (se
    corre toda la mano, con sus dedos), y sostiene HAND_HOLD muestras la que
    la pose dejo de ver."""
    previous = {hand.side: hand for hand in track.hands}
    smoothed = []
    for hand in current:
        track.missing[hand.side] = 0
        near = previous.get(hand.side)
        if near is None or np.hypot(near.x - hand.x, near.y - hand.y) > HAND_JUMP * diagonal:
            smoothed.append(hand)
            continue
        k = HAND_SMOOTHING
        target = (near.x + k * (hand.x - near.x), near.y + k * (hand.y - near.y))
        smoothed.append(hand.moved(target[0] - hand.x, target[1] - hand.y))
    sides = {hand.side for hand in current}
    for side, hand in previous.items():
        if side in sides:
            continue
        misses = track.missing.get(side, 0) + 1
        track.missing[side] = misses
        if misses <= HAND_HOLD:
            smoothed.append(hand)
    return smoothed


class TableHands:
    """Personas con alguna mano a la vista, seguidas entre muestras, con el
    crupier identificado. `interest` es donde vale la mano de 21 puntos (en
    ruleta, el paño y la rueda); `detail_estimator` es la pose de cuerpo
    entero (None con `detailed=False`: solo RTMPose-s)."""

    def __init__(
        self,
        touches_reference: Callable[[float, float], bool],
        detector: YoloxDetector | None = None,
        estimator: PoseEstimator | None = None,
        on_table: Callable[[float, float], bool] | None = None,
        interest: Callable[[float, float], bool] | None = None,
        detail_estimator: PoseEstimator | None = None,
        detailed: bool | None = None,
    ) -> None:
        self._touches_reference = touches_reference
        self._on_table = on_table or (lambda _x, _y: True)
        self._interest = interest or (lambda _x, _y: False)
        self._detector = detector or YoloxDetector()
        self._estimator = estimator or PoseEstimator()
        self._detail = detail_estimator
        # Sin decir nada, con la pose de cuerpo inyectada (tests) no se carga
        # la de cuerpo entero.
        if detailed is None:
            detailed = estimator is None
        if self._detail is None and detailed:
            self._detail = PoseEstimator(WHOLEBODY_MODEL)
        self._croupier_at: tuple[float, float] | None = None
        self._croupier_track: int | None = None
        self._confirmed_track: int | None = None  # la pista que toco la rueda
        self._station: deque[tuple[float, float, float]] = deque(maxlen=STATION_MEMORY)
        self._tracks: list[_Track] = []
        self._next_id = 0
        self._frames = 0
        # El cuadro completo cuando se analiza un recorte (zoom digital):
        # PERSON_MAX_AREA es una fraccion de el, no del recorte.
        self.reference_size: tuple[int, int] | None = None

    @property
    def croupier_located(self) -> bool:
        return self._croupier_at is not None

    def people(self, frame: np.ndarray) -> list[Person]:
        height, width = frame.shape[:2]
        if self._frames % PEOPLE_EVERY == 0:
            reference_w, reference_h = self.reference_size or (width, height)
            limit = PERSON_MAX_AREA * reference_w * reference_h
            detections = [
                d
                for d in self._detector.detect(frame, ["person"], PERSON_MIN_CONFIDENCE)
                if d.bbox[2] * d.bbox[3] <= limit
            ]
            self._update_tracks(detections)
        self._frames += 1
        people: list[Person] = []
        # El crupier primero: si otra caja lo solapa, las manos repetidas
        # quedan con el.
        tracks = sorted(self._tracks, key=lambda t: t.id != self._croupier_track)
        detailed = self._detailed_tracks()
        for track in tracks:
            estimator = self._detail if track.id in detailed else self._estimator
            pose = estimator.estimate(frame, _pad(track.box, width, height))
            person = Person(track.box, [], track=track.id)
            fresh = [
                hand
                for hand in hands_from_pose(pose.points, pose.scores, person.diagonal)
                if not _seen(hand, person, people)
            ]
            track.hands = _smooth_hands(track, fresh, person.diagonal)
            if any(hand.touches(self._on_table) for hand in track.hands):
                track.on_table = True
            person.hands = list(track.hands)
            # Sin ninguna mano a la vista no aporta, y una caja floja cerca
            # del puesto confundiria el seguimiento del crupier. La pista
            # sigue: si vuelve a mostrar las manos, vuelve con su rol.
            if person.hands:
                people.append(person)
        self._assign_roles(people)
        return people

    def _detailed_tracks(self) -> set[int]:
        """Quienes van con la mano de 21 puntos: el crupier y quien tenia una
        mano en la zona de interes en la muestra anterior, hasta DETAIL_MAX."""
        if self._detail is None:
            return set()
        chosen = [self._croupier_track] if self._croupier_track is not None else []
        chosen += [
            track.id
            for track in self._tracks
            if track.id != self._croupier_track
            and any(hand.touches(self._interest) for hand in track.hands)
        ]
        return set(chosen[:DETAIL_MAX])

    def _update_tracks(self, detections) -> None:
        """Empareja las cajas nuevas con las pistas: primero los pares mas
        parecidos (no la caja de mas confianza con la pista que mas le
        convenga, que dejaba al crupier sin la suya)."""
        ordered = sorted(detections, key=lambda d: -d.confidence)
        boxes = [tuple(int(v) for v in detection.bbox) for detection in ordered]
        pairs = sorted(
            (
                (score, track_index, box_index)
                for track_index, track in enumerate(self._tracks)
                for box_index, box in enumerate(boxes)
                if (score := _same_person(track.box, box)) > 0
            ),
            reverse=True,
        )
        owner: dict[int, _Track] = {}
        taken: set[int] = set()
        for _, track_index, box_index in pairs:
            if track_index in taken or box_index in owner:
                continue
            taken.add(track_index)
            owner[box_index] = self._tracks[track_index]
        matched: list[_Track] = []
        for box_index, box in enumerate(boxes):
            track = owner.get(box_index)
            if track is None:
                track = _Track(self._next_id, box)
                self._next_id += 1
            track.box, track.missed = box, 0
            matched.append(track)
        free = [track for index, track in enumerate(self._tracks) if index not in taken]
        held = []
        for track in free:
            track.missed += 1
            if track.missed <= TRACK_HOLD:
                held.append(track)
            elif track.id == self._croupier_track:
                self._croupier_track = None
        # Primero las cajas de esta ronda (por confianza), despues las sostenidas.
        self._tracks = matched + held

    def _track(self, person: Person) -> _Track | None:
        return next((t for t in self._tracks if t.id == person.track), None)

    def _assign_roles(self, people: list[Person]) -> None:
        touching = [
            person
            for person in people
            if any(hand.touches(self._touches_reference) for hand in person.hands)
        ]
        croupier: Person | None = None
        confirmed = len(touching) == 1
        if confirmed:
            croupier = touching[0]
        elif self._croupier_track is not None:
            croupier = next((p for p in people if p.track == self._croupier_track), None)
        # Solo si su pista ya no existe se busca al crupier en su puesto. Si
        # la pista sigue pero esta muestra no le vio las manos, no hay
        # crupier por ahora: antes se le daba el rol al mas cercano y quedaba
        # pegado (RA-04, 57,8 s: el jugador de verde salia como crupier).
        lost = self._croupier_track is None or all(
            track.id != self._croupier_track for track in self._tracks
        )
        if croupier is None and lost and self._croupier_at is not None and people:
            croupier = self._nearest_to_station(people)
        if croupier is not None:
            self._croupier_track = croupier.track
            if confirmed:
                self._confirmed_track = croupier.track
                self._follow(croupier)
            if croupier.track == self._confirmed_track:
                self._station.append((*croupier.center, croupier.diagonal))
        for person in people:
            track = self._track(person)
            if person is croupier:
                person.role = ROLE_CROUPIER
            elif (
                self._croupier_at is not None
                and track is not None
                and track.on_table
                and self._far_from_station(person)
            ):
                person.role = ROLE_PLAYER

    def _far_from_station(self, person: Person) -> bool:
        if not self._station:
            return True
        station = np.array(self._station)
        distances = np.hypot(station[:, 0] - person.center[0], station[:, 1] - person.center[1])
        return bool(np.all(distances > STATION_RADIUS * station[:, 2]))

    def _nearest_to_station(self, people: list[Person]) -> Person | None:
        """Quien ocupa el puesto del crupier: el mas cercano a donde toco la
        rueda, sin nadie a una distancia parecida, y dentro del area por donde
        anduvo el crupier."""
        sx, sy = self._croupier_at
        people = [p for p in people if not self._station or not self._far_from_station(p)]
        if not people:
            return None
        ranked = sorted(people, key=lambda p: np.hypot(p.center[0] - sx, p.center[1] - sy))
        best = ranked[0]
        distance = np.hypot(best.center[0] - sx, best.center[1] - sy)
        if distance > CROUPIER_MATCH * best.diagonal:
            return None
        if len(ranked) > 1:
            second = np.hypot(ranked[1].center[0] - sx, ranked[1].center[1] - sy)
            if second < CROUPIER_AMBIGUITY * distance:
                return None
        return best

    def _follow(self, croupier: Person) -> None:
        """Donde se recupera al crupier si se pierde su pista: donde estaba
        cuando toco la rueda. Solo se mueve con otro toque a la rueda; antes
        seguia a la pista del crupier en cada muestra y, cuando esa pista se
        equivocaba de persona, se iba con ella (RA-04, 89-100 s: el crupier
        quedo como jugador)."""
        cx, cy = croupier.center
        if self._croupier_at is None:
            self._croupier_at = (cx, cy)
            return
        sx, sy = self._croupier_at
        k = CROUPIER_SMOOTHING
        self._croupier_at = (sx + k * (cx - sx), sy + k * (cy - sy))

    def close(self) -> None:
        self._detector.close()
        self._estimator.close()
        if self._detail is not None:
            self._detail.close()
