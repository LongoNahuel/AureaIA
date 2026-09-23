from __future__ import annotations

from aurea_vms.models import repository


def test_add_and_list_device(temp_db):
    device = repository.add_device(
        name="Camara Entrada",
        ip="192.168.1.50",
        port=554,
        username="admin",
        password="admin123",
        rtsp_main_url="rtsp://192.168.1.50:554/Streaming/Channels/101",
    )

    assert device.id is not None
    assert device.status == "unknown"

    devices = repository.list_devices()
    assert len(devices) == 1
    assert devices[0].name == "Camara Entrada"


def test_get_and_update_status(temp_db):
    device = repository.add_device(
        name="Camara Patio",
        ip="192.168.1.51",
        rtsp_main_url="rtsp://192.168.1.51:554/live",
    )

    repository.update_device_status(device.id, "online")

    fetched = repository.get_device(device.id)
    assert fetched is not None
    assert fetched.status == "online"


def test_delete_device(temp_db):
    device = repository.add_device(
        name="Camara Temporal",
        ip="192.168.1.52",
        rtsp_main_url="rtsp://192.168.1.52:554/live",
    )

    repository.delete_device(device.id)

    assert repository.get_device(device.id) is None
    assert repository.list_devices() == []


def test_delete_parent_device_deletes_channels_and_dependents(temp_db):
    parent = repository.add_device(
        name="NVR",
        ip="192.168.1.53",
        rtsp_main_url="rtsp://192.168.1.53/main",
        device_type="nvr",
        channel=0,
    )
    child = repository.add_device(
        name="NVR · Canal 1",
        ip="192.168.1.53",
        rtsp_main_url="rtsp://192.168.1.53/c1",
        device_type="nvr",
        channel=1,
        parent_device_id=parent.id,
    )

    repository.delete_device(parent.id)

    assert repository.get_device(child.id) is None
    assert repository.list_devices() == []
