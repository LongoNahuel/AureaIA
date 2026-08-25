from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import Qt

import aurea_vms.core.analytics_engine as ae_module
from aurea_vms.core.analytics.base import AnalysisResult
from aurea_vms.core.analytics_engine import AnalyticsWorker
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import Detection
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.device import Device


class FakeAnalyzer:
    name = "fake"

    def __init__(self) -> None:
        self.frames = 0
        self.closed = False

    def process_frame(self, frame, timestamp) -> AnalysisResult:
        self.frames += 1
        return AnalysisResult(
            detections=(Detection(label="cara", confidence=0.9, bbox=(0, 0, 5, 5)),),
            metrics={"caras": 1},
        )

    def close(self) -> None:
        self.closed = True


def test_worker_publica_detecciones_y_libera_todo(monkeypatch):
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    released: list[int] = []
    fake_stream = SimpleNamespace(
        acquire=lambda device, kind="main": None,
        get_worker=lambda _id: SimpleNamespace(get_latest_frame=lambda: frame),
        release=lambda device_id: released.append(device_id),
    )
    monkeypatch.setattr(ae_module, "stream_manager", fake_stream)

    analyzer = FakeAnalyzer()
    monkeypatch.setattr(ae_module, "create_analyzer", lambda _config: analyzer)

    config = AnalyticsConfig(device_id=7, analyzer_name="face_detection")
    config.id = 1
    device = Device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
    device.id = 7

    received: list = []
    # DirectConnection explicita: el slot corre en el thread del worker y
    # el test no necesita un event loop de Qt.
    event_bus.detection.connect(received.append, Qt.ConnectionType.DirectConnection)
    try:
        worker = AnalyticsWorker(config, device)
        worker.start()
        deadline = time.monotonic() + 3.0
        while not received and time.monotonic() < deadline:
            time.sleep(0.02)
        worker.stop()
        worker.join(timeout=3.0)
    finally:
        event_bus.detection.disconnect(received.append)

    assert not worker.is_alive()
    assert analyzer.frames >= 1
    assert analyzer.closed  # close() del analizador al terminar
    assert released == [7]  # release del stream

    event = received[0]
    assert event.device_id == 7
    assert event.analyzer_name == "face_detection"
    assert event.metrics == {"caras": 1}
    assert event.detections[0].label == "cara"
