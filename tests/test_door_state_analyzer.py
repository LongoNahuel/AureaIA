from __future__ import annotations

import numpy as np

from aurea_vms.core.analytics.door_state_analyzer import DoorStateAnalyzer


def _closed_frame() -> np.ndarray:
    return np.full((240, 320, 3), 80, dtype=np.uint8)


def _open_frame() -> np.ndarray:
    frame = _closed_frame()
    frame[50:200, 90:230] = 220
    return frame


def test_door_starts_closed_and_reports_metrics() -> None:
    analyzer = DoorStateAnalyzer(
        change_threshold=0.1, confirmation_frames=2, roi=(60, 30, 220, 190)
    )

    first = analyzer.process_frame(_closed_frame(), 0.0)

    assert first.metrics["estado"] == "cerrada"
    assert first.metrics["transicion"] is None
    assert first.detections == ()


def test_door_requires_confirmation_to_open() -> None:
    analyzer = DoorStateAnalyzer(change_threshold=0.1, confirmation_frames=2)
    analyzer.process_frame(_closed_frame(), 0.0)

    first_open = analyzer.process_frame(_open_frame(), 1.0)
    second_open = analyzer.process_frame(_open_frame(), 2.0)

    assert first_open.metrics["estado"] == "cerrada"
    assert first_open.detections == ()
    assert second_open.metrics["estado"] == "abierta"
    assert second_open.metrics["transicion"] == "cerrada_a_abierta"
    assert second_open.detections[0].label == "puerta_abierta"


def test_door_returns_closed_after_stable_reference() -> None:
    analyzer = DoorStateAnalyzer(change_threshold=0.1, confirmation_frames=2)
    analyzer.process_frame(_closed_frame(), 0.0)
    analyzer.process_frame(_open_frame(), 1.0)
    analyzer.process_frame(_open_frame(), 2.0)

    analyzer.process_frame(_closed_frame(), 3.0)
    result = analyzer.process_frame(_closed_frame(), 4.0)

    assert result.metrics["estado"] == "cerrada"
    assert result.metrics["transicion"] == "abierta_a_cerrada"
    assert result.detections[0].label == "puerta_cerrada"


def test_door_detects_partial_change_with_previous_default_threshold() -> None:
    analyzer = DoorStateAnalyzer(change_threshold=0.10, confirmation_frames=2)
    analyzer.process_frame(_closed_frame(), 0.0)

    analyzer.process_frame(_open_frame(), 1.0)
    result = analyzer.process_frame(_open_frame(), 2.0)

    assert result.metrics["estado"] == "abierta"
