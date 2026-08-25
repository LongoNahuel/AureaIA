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


class CentroidTracker:
    def __init__(
        self, max_distance: float = 80.0, max_age_s: float = 2.0, min_hits: int = 1
    ) -> None:
        self.max_distance = max_distance
        self.max_age_s = max_age_s
        self.min_hits = max(1, min_hits)
        self._next_id = 1
        self.tracks: dict[int, TrackedObject] = {}

    def update(self, detections: list[Detection], timestamp: float) -> dict[int, TrackedObject]:
        expired = [
            tid
            for tid, track in self.tracks.items()
            if timestamp - track.last_seen > self.max_age_s
        ]
        for tid in expired:
            del self.tracks[tid]

        unmatched_ids = set(self.tracks.keys())
        assigned: dict[int, TrackedObject] = {}

        for det in detections:
            x, y, w, h = det.bbox
            centroid = (x + w / 2, y + h / 2)

            best_id = None
            best_dist = self.max_distance
            for tid in unmatched_ids:
                track = self.tracks[tid]
                if track.label != det.label:
                    continue
                dist = math.dist(track.centroid, centroid)
                if dist < best_dist:
                    best_dist = dist
                    best_id = tid

            if best_id is not None:
                track = self.tracks[best_id]
                track.centroid = centroid
                track.bbox = det.bbox
                track.confidence = det.confidence
                track.last_seen = timestamp
                track.hits += 1
                unmatched_ids.discard(best_id)
                assigned[best_id] = track
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
                )
                self.tracks[tid] = track
                assigned[tid] = track

        return assigned

    def confirmed_tracks(self) -> list[TrackedObject]:
        return [track for track in self.tracks.values() if track.hits >= self.min_hits]
