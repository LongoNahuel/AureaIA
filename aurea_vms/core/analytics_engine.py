"""Orquesta los analizadores activos: por cada AnalyticsConfig habilitado
corre un thread propio que toma frames throttleados del StreamWorker de su
camara (via stream_manager, con su mismo ref-counting), corre el
analizador y publica el resultado en el EventBus."""

from __future__ import annotations

import logging
import threading
import time

from aurea_vms.config.settings import settings
from aurea_vms.core.analytics.registry import ANALYZER_DISPLAY_NAMES, create_analyzer
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.device import Device

logger = logging.getLogger(__name__)


class AnalyticsWorker(threading.Thread):
    def __init__(self, config: AnalyticsConfig, device: Device) -> None:
        super().__init__(daemon=True, name=f"AnalyticsWorker-{config.id}")
        self.config_id = config.id
        self._device = device
        self._analyzer_name = config.analyzer_name
        self._analyzer = create_analyzer(config)
        self._interval_s = 1.0 / settings.analytics_fps
        self._stop_event = threading.Event()

    def run(self) -> None:
        stream_manager.acquire(self._device)
        try:
            while not self._stop_event.is_set():
                start = time.monotonic()

                worker = stream_manager.get_worker(self._device.id)
                frame = worker.get_latest_frame() if worker else None
                if frame is not None:
                    result = self._analyzer.process_frame(frame, time.time())
                    event_bus.detection.emit(
                        DetectionEvent(
                            device_id=self._device.id,
                            analyzer_name=self._analyzer_name,
                            timestamp=time.time(),
                            detections=result.detections,
                            metrics=result.metrics,
                        )
                    )

                elapsed = time.monotonic() - start
                self._stop_event.wait(max(0.0, self._interval_s - elapsed))
        finally:
            stream_manager.release(self._device.id)

    def stop(self) -> None:
        self._stop_event.set()


class AnalyticsEngine:
    def __init__(self) -> None:
        self._workers: dict[int, AnalyticsWorker] = {}

    def start(self, config: AnalyticsConfig, device: Device) -> None:
        self.stop(config.id)
        display_name = ANALYZER_DISPLAY_NAMES.get(config.analyzer_name, config.analyzer_name)
        logger.info("Cámara %s: iniciando analizador '%s'", device.id, display_name)
        worker = AnalyticsWorker(config, device)
        self._workers[config.id] = worker
        worker.start()

    def stop(self, config_id: int) -> None:
        worker = self._workers.pop(config_id, None)
        if worker:
            logger.info("Deteniendo analizador (config %s)", config_id)
            worker.stop()

    def is_running(self, config_id: int) -> bool:
        return config_id in self._workers

    def running_count(self) -> int:
        return len(self._workers)

    def stop_all(self) -> None:
        for config_id in list(self._workers):
            self.stop(config_id)


analytics_engine = AnalyticsEngine()
