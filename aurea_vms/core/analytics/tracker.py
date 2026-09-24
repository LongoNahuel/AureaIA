"""Tracker de centroides simple: asocia detecciones entre frames por
distancia euclidiana al centroide mas cercano de la misma clase.

Alcanza para conteo de cruce de linea y conteo de personas en un PoC de
pocas camaras. No es un tracker robusto tipo DeepSORT/ByteTrack: no
maneja oclusiones largas ni swaps de identidad en escenas muy
concurridas.

Histeresis: un track nuevo no cuenta como "confirmado" (`is_confirmed`)
hasta acumular `min_hits` actualizaciones seguidas -- eso evita contar una
deteccion espuria de un solo frame (ruido del modelo) como si fuera una
persona/objeto real. `max_age_s` tolera perder la deteccion un par de
frames (oclusion momentanea, frame con poca luz) sin descartar el track
ni resetear el contador de hits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aurea_vms.core.events import Detection


def _bbox_iou(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0, right - left) * max(0, bottom - top)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union else 0.0


@dataclass
class TrackedObject:
    track_id: int
    centroid: tuple[float, float]
    bbox: tuple[int, int, int, int]
    label: str
    confidence: float
    last_seen: float
    hits: int = 1
    side: int | None = None  # lado de la linea de cruce en la ultima actualizacion
    # Puntos de referencia de la ultima deteccion asociada a este track (solo
    # Deteccion Facial los usa) -- se propagan para que un track recien
    # confirmado no pierda la geometria que necesita la galeria de rostros.
    keypoints: tuple[tuple[float, float], ...] | None = None
    # Contorno simplificado de la ultima deteccion asociada (solo Deteccion
    # de Movimiento) -- mismo motivo que keypoints: sin esto, un track
    # recien confirmado dibujaria un rectangulo en vez de la silueta real.
    polygon: tuple[tuple[int, int], ...] | None = None
    # Velocidad estimada del centroide en px/s (ver CentroidTracker.update).
    velocity: tuple[float, float] = (0.0, 0.0)


# Peso de la ultima medicion al actualizar la velocidad (EMA).
VELOCITY_SMOOTHING = 0.5


class CentroidTracker:
    def __init__(
        self,
        max_distance: float = 80.0,
        max_age_s: float = 2.0,
        min_hits: int = 1,
        min_iou: float = 0.0,
    ) -> None:
        self.max_distance = max_distance
        self.max_age_s = max_age_s
        self.min_hits = max(1, min_hits)
        self.min_iou = max(0.0, min(1.0, min_iou))
        self._next_id = 1
        self.tracks: dict[int, TrackedObject] = {}

    def _gate(self, track: TrackedObject) -> float:
        """Distancia maxima de asociacion para ESTE track: el piso fijo
        (`max_distance`) o, si el objeto es grande en cuadro, una vez su
        lado mayor. Con un piso fijo en pixeles, una persona cerca de una
        camara 1080p muestreada a 2-5 fps se desplaza mas que el piso entre
        muestras: el track se perdia, nacia otro sin historial y el cruce
        de linea no se contaba."""
        return max(self.max_distance, float(max(track.bbox[2], track.bbox[3])))

    @staticmethod
    def _predicted_centroid(track: TrackedObject, timestamp: float) -> tuple[float, float]:
        dt = max(0.0, timestamp - track.last_seen)
        return (
            track.centroid[0] + track.velocity[0] * dt,
            track.centroid[1] + track.velocity[1] * dt,
        )

    def update(self, detections: list[Detection], timestamp: float) -> dict[int, TrackedObject]:
        expired = [
            tid
            for tid, track in self.tracks.items()
            if timestamp - track.last_seen > self.max_age_s
        ]
        for tid in expired:
            del self.tracks[tid]

        centroids = [(d.bbox[0] + d.bbox[2] / 2, d.bbox[1] + d.bbox[3] / 2) for d in detections]

        # Asociacion GLOBAL y greedy por costo: se arman todos los pares
        # (track, deteccion) validos y se asignan de mejor a peor. Antes se
        # recorria deteccion por deteccion y la primera de la lista se
        # quedaba con el track mas cercano aunque otra deteccion le quedara
        # mucho mas cerca -- swaps de identidad dependientes del orden en
        # que el detector devolviera las cajas. Los pares con solapamiento
        # (IoU) van primero; despues, por distancia al centroide predicho.
        candidates: list[tuple[tuple[int, float], int, int]] = []
        for tid, track in self.tracks.items():
            predicted = self._predicted_centroid(track, timestamp)
            gate = self._gate(track)
            for index, det in enumerate(detections):
                if track.label != det.label:
                    continue
                iou = _bbox_iou(track.bbox, det.bbox)
                if iou > 0 and iou >= self.min_iou:
                    candidates.append(((0, -iou), tid, index))
                    continue
                dist = math.dist(predicted, centroids[index])
                if dist < gate:
                    candidates.append(((1, dist), tid, index))
        candidates.sort(key=lambda item: item[0])

        matched: dict[int, int] = {}  # indice de deteccion -> track id
        used_tracks: set[int] = set()
        for _, tid, index in candidates:
            if tid in used_tracks or index in matched:
                continue
            matched[index] = tid
            used_tracks.add(tid)

        assigned: dict[int, TrackedObject] = {}
        for index, det in enumerate(detections):
            centroid = centroids[index]
            tid = matched.get(index)
            if tid is not None:
                track = self.tracks[tid]
                dt = timestamp - track.last_seen
                if dt > 0:
                    # Velocidad suavizada (px/s) para predecir la posicion en
                    # la proxima muestra; el suavizado evita que el jitter de
                    # la caja se amplifique en la prediccion.
                    vx = (centroid[0] - track.centroid[0]) / dt
                    vy = (centroid[1] - track.centroid[1]) / dt
                    track.velocity = (
                        VELOCITY_SMOOTHING * vx + (1 - VELOCITY_SMOOTHING) * track.velocity[0],
                        VELOCITY_SMOOTHING * vy + (1 - VELOCITY_SMOOTHING) * track.velocity[1],
                    )
                track.centroid = centroid
                track.bbox = det.bbox
                track.confidence = det.confidence
                track.last_seen = timestamp
                track.hits += 1
                track.keypoints = det.keypoints
                track.polygon = det.polygon
            else:
                tid = self._next_id
                self._next_id += 1
                track = TrackedObject(
                    track_id=tid,
                    centroid=centroid,
                    bbox=det.bbox,
                    label=det.label,
                    confidence=det.confidence,
                    last_seen=timestamp,
                    hits=1,
                    keypoints=det.keypoints,
                    polygon=det.polygon,
                )
                self.tracks[tid] = track
            assigned[tid] = track

        return assigned

    def confirmed_tracks(self) -> list[TrackedObject]:
        return [track for track in self.tracks.values() if track.hits >= self.min_hits]
