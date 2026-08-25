from __future__ import annotations

import pytest

import aurea_vms.core.analytics_engine as ae_module
from aurea_vms.core.analytics_engine import AnalyticsEngine
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.device import Device


class FakeAnalyticsWorker:
    def __init__(self, config: AnalyticsConfig, device: Device) -> None:
        self.config_id = config.id
        self.stopped = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture()
def engine(monkeypatch) -> AnalyticsEngine:
    monkeypatch.setattr(ae_module, "AnalyticsWorker", FakeAnalyticsWorker)
    return AnalyticsEngine()


def _config(config_id: int) -> AnalyticsConfig:
    config = AnalyticsConfig(device_id=1, analyzer_name="motion_detection")
    config.id = config_id
    return config


def _device() -> Device:
    device = Device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://x/main")
    device.id = 1
    return device


def test_start_y_stop(engine):
    engine.start(_config(5), _device())
    assert engine.is_running(5)
    assert engine.running_count() == 1

    engine.stop(5)
    assert not engine.is_running(5)


def test_restart_reemplaza_el_worker_anterior(engine):
    engine.start(_config(5), _device())
    primero = engine._workers[5]

    engine.start(_config(5), _device())
    assert primero.stopped
    assert engine.running_count() == 1


def test_stop_de_config_inexistente_es_noop(engine):
    engine.stop(99)


def test_stop_all(engine):
    engine.start(_config(1), _device())
    engine.start(_config(2), _device())

    engine.stop_all()
    assert engine.running_count() == 0
