from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from aurea_vms.core import clip_recorder, media_store
from aurea_vms.models import repository
from aurea_vms.models.media_asset import KIND_CLIP, KIND_SNAPSHOT


@pytest.fixture(autouse=True)
def _media_en_tmp(tmp_path, monkeypatch):
    """settings es un dataclass frozen: se reemplaza el nombre importado en
    media_store por un doble que apunta a tmp."""
    fake_settings = SimpleNamespace(media_dir=tmp_path / "media")
    monkeypatch.setattr(media_store, "settings", fake_settings)
    return fake_settings


@pytest.fixture()
def device_y_evento(temp_db):
    device = repository.add_device(
        name="Cam", ip="192.168.1.80", rtsp_main_url="rtsp://192.168.1.80/live"
    )
    rule = repository.add_alarm_rule(analyzer_name="motion_detection")
    event = repository.add_alarm_event(
        rule_id=rule.id,
        device_id=device.id,
        timestamp=100.0,
        object_class="movimiento",
        confidence=1.0,
    )
    return device, event


def _jpeg_frame(color: int) -> bytes:
    frame = np.full((120, 160, 3), color, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", frame)
    assert ok
    return buf.tobytes()


class TestSaveSnapshot:
    def test_escribe_jpg_en_layout_por_fecha_y_registra_asset(self, device_y_evento):
        device, event = device_y_evento
        frame = np.zeros((120, 160, 3), dtype=np.uint8)

        asset = clip_recorder.save_snapshot(device.id, event.id, frame)

        # Layout: snapshot/AAAA/MM/DD/<camara>/HHMMSS_<evento>.jpg
        hoy = dt.datetime.now()
        assert asset.rel_path.startswith(f"snapshot/{hoy:%Y/%m/%d}/{device.id}/")
        assert asset.rel_path.endswith(f"_{event.id}.jpg")

        path = media_store.absolute_path(asset.rel_path)
        assert cv2.imread(str(path)) is not None

        # Registrado en el indice, vinculado al evento, generado por sistema.
        assert asset.kind == KIND_SNAPSHOT
        assert asset.alarm_event_id == event.id
        assert asset.created_by is None
        assert asset.size_bytes > 0
        assert (asset.width, asset.height) == (160, 120)

    def test_created_by_para_capturas_manuales(self, device_y_evento):
        device, event = device_y_evento
        user = repository.add_user(username="op", password_hash="h", salt="s", role="operador")

        asset = clip_recorder.save_snapshot(
            device.id, event.id, np.zeros((10, 10, 3), dtype=np.uint8), created_by=user.id
        )
        assert asset.created_by == user.id


class TestWriteMp4:
    def test_genera_video_reproducible_y_registra_asset(self, device_y_evento):
        device, event = device_y_evento
        frames = [(float(i), _jpeg_frame(i * 40)) for i in range(5)]

        asset = clip_recorder._write_mp4(device.id, event.id, frames)

        assert asset.kind == KIND_CLIP
        assert asset.duration_s == pytest.approx(5 / clip_recorder.CLIP_FPS)
        assert asset.size_bytes > 0

        cap = cv2.VideoCapture(str(media_store.absolute_path(asset.rel_path)))
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

    def test_la_media_del_evento_se_encuentra_por_indice(self, device_y_evento):
        device, event = device_y_evento
        clip_recorder.save_snapshot(device.id, event.id, np.zeros((10, 10, 3), dtype=np.uint8))
        clip_recorder._write_mp4(device.id, event.id, [(0.0, _jpeg_frame(10))])

        por_evento = repository.list_media_for_events([event.id])
        kinds = {asset.kind for asset in por_evento[event.id]}
        assert kinds == {KIND_SNAPSHOT, KIND_CLIP}
