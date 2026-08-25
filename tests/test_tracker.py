from __future__ import annotations

from aurea_vms.core.analytics.tracker import CentroidTracker
from aurea_vms.core.events import Detection


def _det(x: int, y: int, label: str = "person", confidence: float = 0.8) -> Detection:
    return Detection(label=label, confidence=confidence, bbox=(x, y, 20, 40))


class TestAsociacion:
    def test_deteccion_cercana_actualiza_el_mismo_track(self):
        tracker = CentroidTracker(max_distance=80.0)
        tracker.update([_det(100, 100)], timestamp=0.0)
        assigned = tracker.update([_det(110, 105)], timestamp=0.2)

        assert len(tracker.tracks) == 1
        track = next(iter(assigned.values()))
        assert track.hits == 2

    def test_deteccion_lejana_crea_track_nuevo(self):
        tracker = CentroidTracker(max_distance=80.0)
        tracker.update([_det(0, 0)], timestamp=0.0)
        tracker.update([_det(500, 500)], timestamp=0.2)

        assert len(tracker.tracks) == 2

    def test_no_asocia_entre_clases_distintas(self):
        tracker = CentroidTracker(max_distance=80.0)
        tracker.update([_det(100, 100, label="person")], timestamp=0.0)
        tracker.update([_det(100, 100, label="car")], timestamp=0.2)

        assert len(tracker.tracks) == 2

    def test_ids_incrementales_y_estables(self):
        tracker = CentroidTracker(max_distance=80.0)
        first = tracker.update([_det(100, 100)], timestamp=0.0)
        second = tracker.update([_det(102, 101)], timestamp=0.2)

        assert list(first.keys()) == list(second.keys())


class TestHisteresis:
    def test_track_nuevo_no_confirmado_hasta_min_hits(self):
        tracker = CentroidTracker(min_hits=3)

        tracker.update([_det(100, 100)], timestamp=0.0)
        assert tracker.confirmed_tracks() == []

        tracker.update([_det(101, 100)], timestamp=0.2)
        assert tracker.confirmed_tracks() == []

        tracker.update([_det(102, 100)], timestamp=0.4)
        assert len(tracker.confirmed_tracks()) == 1

    def test_min_hits_uno_confirma_de_entrada(self):
        tracker = CentroidTracker(min_hits=1)
        tracker.update([_det(100, 100)], timestamp=0.0)
        assert len(tracker.confirmed_tracks()) == 1


class TestExpiracion:
    def test_track_viejo_expira(self):
        tracker = CentroidTracker(max_age_s=2.0)
        tracker.update([_det(100, 100)], timestamp=0.0)

        tracker.update([], timestamp=5.0)
        assert tracker.tracks == {}

    def test_oclusion_corta_no_expira(self):
        tracker = CentroidTracker(max_age_s=2.0)
        tracker.update([_det(100, 100)], timestamp=0.0)

        tracker.update([], timestamp=1.0)  # un frame sin deteccion
        assigned = tracker.update([_det(105, 100)], timestamp=1.5)

        assert len(tracker.tracks) == 1
        assert next(iter(assigned.values())).hits == 2
