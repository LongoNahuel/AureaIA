from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from aurea_vms.core import clip_recorder


@pytest.fixture(autouse=True)
def _storage_en_tmp(tmp_path, monkeypatch):
    """settings es un dataclass frozen: se reemplaza el nombre importado en
    el modulo por un doble que apunta a tmp."""
    fake_settings = SimpleNamespace(
        clips_dir=tmp_path / "clips",
        snapshots_dir=tmp_path / "snapshots",
        clip_pre_seconds=5,
        clip_post_seconds=1,
    )
    monkeypatch.setattr(clip_recorder, "settings", fake_settings)
    return fake_settings


def _jpeg_frame(color: int) -> bytes:
    frame = np.full((120, 160, 3), color, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return buf.tobytes()


def test_save_snapshot_escribe_jpg(tmp_path):
    frame = np.zeros((120, 160, 3), dtype=np.uint8)

    path = clip_recorder.save_snapshot(device_id=7, alarm_event_id=42, frame=frame)

    assert path.endswith(".jpg")
    leido = cv2.imread(path)
    assert leido is not None
    assert leido.shape == (120, 160, 3)


def test_write_mp4_genera_video_reproducible(tmp_path):
    frames = [(float(i), _jpeg_frame(i * 40)) for i in range(5)]

    path = clip_recorder._write_mp4(device_id=7, alarm_event_id=42, frames=frames)

    cap = cv2.VideoCapture(path)
    assert cap.isOpened()
    count = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        count += 1
        assert frame.shape == (120, 160, 3)
    cap.release()
    assert count == 5
