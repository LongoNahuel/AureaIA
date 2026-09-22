"""Catalogo de rostros de la Vista Inteligente: quien es quien, cuantos van,
y que captura se guarda de cada identidad.

Esto vivia adentro de ui/widgets/face_gallery.py, donde el QListWidget ERA
el modelo de datos: cada candidato se guardaba en el Qt.ItemDataRole.UserRole
de su item y el algoritmo leia y escribia la lista de la UI. Consecuencias
concretas de aquello: el algoritmo de identidad -- el corazon del panel -- no
tenia un solo test, quedaba fuera del scope de cobertura por estar en ui/, y
el reinicio diario usaba dt.datetime.now() directo, o sea era intesteable.

Aca no hay nada de Qt. El catalogo es duenio de la lista de capturas y
devuelve, por cada rostro observado, que le cambio; el widget se limita a
reflejar eso en su QListWidget. Los indices de las dos listas coinciden:
ambas insertan la captura nueva en la posicion 0 (mas reciente primero).

Un ID catalogado por rostro distinto: cada deteccion se compara contra las
ya capturadas con dos firmas combinadas -- una chica en escala de grises
(apariencia) y otra geometrica, a partir de las distancias entre los 5
puntos de referencia que da el detector (ojos/nariz/comisuras de boca),
normalizadas por la distancia entre ojos para que no dependa de que tan
cerca este la cara. Ninguna de las dos es reconocimiento real (no hay un
embedding aprendido), pero combinar forma + apariencia es bastante mas
robusto a cambios de luz o gesto que comparar pixeles solos.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache

import cv2
import numpy as np

MAX_ITEMS = 24
SIGNATURE_SIZE = 24
DEFAULT_DIFF_THRESHOLD = 0.35
DEFAULT_MAX_CAPTURES_PER_FACE = 1
DEFAULT_COUNTING_ENABLED = True
DEFAULT_COUNTING_RESET_TIME = "00:00"


def face_signature(crop_bgr: np.ndarray) -> np.ndarray:
    """Firma chica y liviana de un recorte de cara: escala de grises,
    24x24, ecualizada (para amortiguar diferencias de iluminación). No es
    un embedding de reconocimiento facial, solo alcanza para comparar
    "se parece a una captura ya guardada" contra las pocas que hay en la
    galería."""
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (SIGNATURE_SIZE, SIGNATURE_SIZE))
    equalized = cv2.equalizeHist(resized)
    return equalized.astype(np.float32) / 255.0


@lru_cache(maxsize=8)
def _indices_de_pares(cantidad: int) -> tuple[np.ndarray, np.ndarray]:
    """Los C(n,2) pares (i<j), cacheados: los puntos siempre son los mismos
    y armar la lista en cada frame era parte del costo."""
    return np.triu_indices(cantidad, k=1)


def geometry_signature(keypoints: tuple[tuple[float, float], ...] | None) -> np.ndarray | None:
    """Distancias entre cada par de los 5 puntos de referencia (ojo der,
    ojo izq, nariz, comisura de boca der, comisura de boca izq),
    normalizadas por la distancia entre ojos -- da una firma de "forma"
    de la cara que no depende de que tan cerca/lejos este de la camara.

    Vectorizado: la version con un np.linalg.norm por par costaba 85us por
    cara, mas que face_signature entera (54us), casi todo overhead de
    numpy por llamada. Corre por cada cara y cada frame.
    """
    if not keypoints or len(keypoints) < 5:
        return None
    points = np.asarray(keypoints, dtype=np.float32)
    eye_distance = float(np.hypot(*(points[0] - points[1])))
    if eye_distance < 1e-3:
        return None
    filas, columnas = _indices_de_pares(len(points))
    deltas = points[filas] - points[columnas]
    return (np.hypot(deltas[:, 0], deltas[:, 1]) / eye_distance).astype(np.float32)


def clamp_bbox(
    bbox: tuple[int, int, int, int], width: int, height: int
) -> tuple[int, int, int, int] | None:
    """Recorta una caja a los limites del frame. None si no queda nada --
    puede pasar con una deteccion sobre el borde y un ROI desplazado."""
    x, y, w, h = bbox
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(x + w, width), min(y + h, height)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _difference(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


@dataclass(frozen=True)
class FaceCapture:
    """Una miniatura catalogada. Sin pixmap: la imagen la maneja el widget."""

    track_id: int
    signature: np.ndarray
    geometry: np.ndarray | None
    area: int
    confidence: float


def combined_difference(a: FaceCapture, b: FaceCapture) -> float:
    """Combina apariencia (firma de pixeles) y forma (firma geometrica,
    si ambas capturas la tienen) tomando el MAXIMO de las dos, no un
    promedio: si cualquiera de las dos señales ya muestra una diferencia
    clara, tiene que pesar como tal -- promediar dejaba que una firma
    parecida "diluyera" a la otra aunque fuera claramente distinta (caso
    real: dos personas con recortes de piel/fondo similares por
    casualidad, pero geometria facial bien distinta, terminaban
    matcheando como el mismo ID)."""
    pixel_diff = _difference(a.signature, b.signature)
    if a.geometry is not None and b.geometry is not None:
        return max(pixel_diff, _difference(a.geometry, b.geometry))
    return pixel_diff


@dataclass(frozen=True)
class FaceCatalogSettings:
    """Lo que la camara tiene configurado en Analizadores > Detección Facial."""

    diff_threshold: float = DEFAULT_DIFF_THRESHOLD
    max_captures_per_face: int = DEFAULT_MAX_CAPTURES_PER_FACE
    counting_enabled: bool = DEFAULT_COUNTING_ENABLED
    reset_hour: int = 0
    reset_minute: int = 0

    @classmethod
    def from_params(cls, params: dict | None) -> FaceCatalogSettings:
        params = params or {}
        hour, minute = cls._parse_reset_time(
            params.get("counting_reset_time", DEFAULT_COUNTING_RESET_TIME)
        )
        return cls(
            diff_threshold=params.get("capture_diff_threshold", DEFAULT_DIFF_THRESHOLD),
            max_captures_per_face=params.get(
                "max_captures_per_face", DEFAULT_MAX_CAPTURES_PER_FACE
            ),
            counting_enabled=params.get("counting_enabled", DEFAULT_COUNTING_ENABLED),
            reset_hour=hour,
            reset_minute=minute,
        )

    @staticmethod
    def _parse_reset_time(text: object) -> tuple[int, int]:
        """Medianoche ante cualquier cosa rara: el horario lo escribe un
        humano en un campo de texto y un typo no puede romper el panel."""
        try:
            hour, minute = (int(part) for part in str(text).split(":")[:2])
        except (ValueError, AttributeError, TypeError):
            return 0, 0
        return hour, minute


@dataclass(frozen=True)
class CatalogUpdate:
    """Que le paso a la galeria al observar un rostro.

    `capture=None` significa que la deteccion se descarto (ya habia tomas
    iguales o mejores de esa identidad). `removed_index` se aplica ANTES de
    insertar la captura nueva en la posicion 0, que es el orden en que el
    catalogo modifico su propia lista.
    """

    capture: FaceCapture | None = None
    removed_index: int | None = None
    is_new_identity: bool = False


class FaceCatalog:
    """Identidades vistas por UNA camara durante la sesion. Efimero: no se
    persiste (para eso estan los snapshots de alarma)."""

    def __init__(
        self,
        *,
        max_items: int = MAX_ITEMS,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        # `now` inyectable para poder testear el reinicio diario sin viajar
        # en el tiempo -- era lo que hacia intesteable a _apply_daily_reset.
        self._now = now or dt.datetime.now
        self._max_items = max_items
        self._captures: list[FaceCapture] = []
        self._next_track_id = 1
        self._total_count = 0
        self._last_reset_date: dt.date | None = None

    @property
    def captures(self) -> tuple[FaceCapture, ...]:
        """Mas reciente primero, igual que las filas de la galeria."""
        return tuple(self._captures)

    @property
    def total_count(self) -> int:
        return self._total_count

    def reset(self) -> None:
        """Cambio de camara: se descarta todo, incluidos los IDs."""
        self._captures.clear()
        self._next_track_id = 1
        self._total_count = 0
        self._last_reset_date = None

    def clear_counter(self) -> None:
        """Pone el contador en cero a mano, sin tocar las miniaturas. Marca
        el dia como ya reiniciado para que el reinicio programado de hoy no
        lo vuelva a pisar."""
        self._total_count = 0
        self._last_reset_date = self._now().date()

    def apply_daily_reset(self, settings: FaceCatalogSettings) -> bool:
        """True si el contador se reinicio en esta llamada."""
        if not settings.counting_enabled:
            return False
        now = self._now()
        reset_moment = now.replace(
            hour=settings.reset_hour, minute=settings.reset_minute, second=0, microsecond=0
        )
        if now >= reset_moment and self._last_reset_date != now.date():
            self._total_count = 0
            self._last_reset_date = now.date()
            return True
        return False

    def observe(
        self,
        *,
        signature: np.ndarray,
        geometry: np.ndarray | None,
        area: int,
        confidence: float,
        settings: FaceCatalogSettings,
    ) -> CatalogUpdate:
        """Decide si este rostro es una identidad nueva, una toma mejor de
        una ya catalogada, o nada que valga la pena guardar."""
        candidate = FaceCapture(
            track_id=0, signature=signature, geometry=geometry, area=area, confidence=confidence
        )
        matches = [
            (index, capture)
            for index, capture in enumerate(self._captures)
            if combined_difference(candidate, capture) < settings.diff_threshold
        ]

        removed_index: int | None = None
        if matches:
            track_id = matches[0][1].track_id
            is_new_identity = False
            if len(matches) >= settings.max_captures_per_face:
                # Llegamos al tope de tomas de esta identidad: la mas chica
                # se cambia por la entrante SOLO si la entrante es mas
                # grande. Asi con tope 1 la galeria termina mostrando la
                # mejor toma de cada persona, no la primera (tipicamente
                # chica y lejana).
                smallest_index, smallest = min(matches, key=lambda pair: pair[1].area)
                if area <= smallest.area:
                    return CatalogUpdate()
                removed_index = smallest_index
                del self._captures[smallest_index]
        else:
            track_id = self._next_track_id
            self._next_track_id += 1
            is_new_identity = True
            if settings.counting_enabled:
                self._total_count += 1

        capture = replace(candidate, track_id=track_id)
        self._captures.insert(0, capture)
        return CatalogUpdate(
            capture=capture, removed_index=removed_index, is_new_identity=is_new_identity
        )

    def prune(self) -> int:
        """Recorta la galeria al tope y devuelve cuantas se sacaron del
        final (las mas viejas)."""
        excess = max(0, len(self._captures) - self._max_items)
        if excess:
            del self._captures[len(self._captures) - excess :]
        return excess
