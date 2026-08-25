from __future__ import annotations

from types import SimpleNamespace

import numpy as np

import aurea_vms.core.alarm_engine as ae_module
from aurea_vms.core.alarm_engine import AlarmEngine
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import Detection, DetectionEvent
from aurea_vms.models import repository


def _setup(temp_db_unused, monkeypatch, actions: dict):
    device = repository.add_device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
    rule = repository.add_alarm_rule(
        device_id=device.id,
        analyzer_name="face_detection",
        severity="critico",
        actions=actions,
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    monkeypatch.setattr(
        ae_module.stream_manager,
        "get_worker",
        lambda _id: SimpleNamespace(get_latest_frame=lambda: frame),
    )
    snapshots: list[tuple] = []

    def fake_snapshot(device_id, event_id, _frame):
        snapshots.append((device_id, event_id))
        return SimpleNamespace(rel_path=f"snapshot/x/{event_id}.jpg")

    monkeypatch.setattr(ae_module.clip_recorder, "save_snapshot", fake_snapshot)
    clips: list[tuple] = []
    monkeypatch.setattr(
        ae_module.clip_recorder, "record_clip_async", lambda d, e: clips.append((d, e))
    )
    notifications: list[tuple] = []
    monkeypatch.setattr(
        ae_module.desktop_notify, "notify", lambda *args: notifications.append(args)
    )

    event = DetectionEvent(
        device_id=device.id,
        analyzer_name="face_detection",
        timestamp=100.0,
        detections=(Detection(label="cara", confidence=0.8, bbox=(0, 0, 5, 5)),),
    )
    return device, rule, event, snapshots, clips, notifications


class TestTrigger:
    def test_persiste_evento_snapshot_y_emite_dto(self, temp_db, monkeypatch):
        device, rule, event, snapshots, clips, _notif = _setup(
            temp_db, monkeypatch, actions={"notify_ui": True, "play_sound": True}
        )
        emitted: list = []
        event_bus.alarm.connect(emitted.append)
        try:
            AlarmEngine._trigger(rule, event, event.detections[0])
        finally:
            event_bus.alarm.disconnect(emitted.append)

        rows = repository.list_alarm_events()
        assert len(rows) == 1
        assert rows[0].severity == "critico"  # copiada de la regla
        assert snapshots == [(device.id, rows[0].id)]
        assert clips == []  # sin save_clip

        assert len(emitted) == 1
        dto = emitted[0]
        assert dto.alarm_event_id == rows[0].id
        assert dto.play_sound is True
        assert dto.snapshot_path and dto.snapshot_path.endswith(".jpg")

    def test_save_clip_lanza_la_grabacion(self, temp_db, monkeypatch):
        device, rule, event, _snaps, clips, _notif = _setup(
            temp_db, monkeypatch, actions={"save_clip": True}
        )
        AlarmEngine._trigger(rule, event, event.detections[0])

        rows = repository.list_alarm_events()
        assert clips == [(device.id, rows[0].id)]

    def test_notify_desktop(self, temp_db, monkeypatch):
        _device, rule, event, _snaps, _clips, notifications = _setup(
            temp_db, monkeypatch, actions={"notify_desktop": True}
        )
        AlarmEngine._trigger(rule, event, event.detections[0])
        assert len(notifications) == 1

    def test_sin_frame_no_hay_snapshot(self, temp_db, monkeypatch):
        _device, rule, event, snapshots, _clips, _notif = _setup(temp_db, monkeypatch, actions={})
        monkeypatch.setattr(ae_module.stream_manager, "get_worker", lambda _id: None)

        emitted: list = []
        event_bus.alarm.connect(emitted.append)
        try:
            AlarmEngine._trigger(rule, event, event.detections[0])
        finally:
            event_bus.alarm.disconnect(emitted.append)

        assert snapshots == []
        assert emitted[0].snapshot_path is None
        assert emitted[0].play_sound is False
