"""Orquesta los analizadores activos: por cada AnalyticsConfig habilitado
corre un thread propio que toma frames throttleados del StreamWorker de su
camara (via stream_manager, con su mismo ref-counting), corre el
analizador y publica el resultado en el EventBus."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from aurea_vms.config.settings import settings
from aurea_vms.core.analytics.registry import ANALYZER_DISPLAY_NAMES, create_analyzer
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DetectionEvent
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.device import Device

logger = logging.getLogger(__name__)

# Piso de espera cuando la inferencia consume todo el intervalo: sin el,
# wait(0.0) vuelve al instante y el loop clava un core al 100% persiguiendo
# un FPS que el CPU no puede dar.
MIN_YIELD_S = 0.05


def pacing_wait_s(interval_s: float, elapsed_s: float) -> float:
    """Cuanto dormir entre frames: el resto del intervalo si sobra tiempo,
    MIN_YIELD_S si la inferencia se comio el presupuesto (funcion pura
    para poder testearla sin levantar threads)."""
    remaining = interval_s - elapsed_s
    return remaining if remaining > 0 else MIN_YIELD_S


# Con un fallo persistente, loguear la traza en el 1er error y cada N.
ERROR_LOG_EVERY = 100
# Suavizado (EMA) de la latencia y los fps medidos.
STATS_SMOOTHING = 0.2


@dataclass
class WorkerStats:
    """Telemetria de un AnalyticsWorker para la UI: a cuantos fps corre de
    verdad (no los configurados), cuanto tarda cada inferencia y cuantos
    cuadros fallaron. Se escribe desde el hilo del worker y se lee desde
    la UI: son floats/ints sueltos, una lectura apenas desfasada es
    aceptable y no amerita un lock."""

    fps: float = 0.0
    latency_ms: float = 0.0
    frames: int = 0
    errors: int = 0
    last_ok: float = 0.0  # time.monotonic() del ultimo cuadro procesado bien

    def record(self, now: float, latency_ms: float) -> None:
        if self.frames == 0:
            self.latency_ms = latency_ms
        else:
            self.latency_ms += STATS_SMOOTHING * (latency_ms - self.latency_ms)
            interval = now - self.last_ok
            if interval > 0:
                self.fps += STATS_SMOOTHING * (1.0 / interval - self.fps)
        self.frames += 1
        self.last_ok = now


class AnalyticsWorker(threading.Thread):
    def __init__(self, config: AnalyticsConfig, device: Device) -> None:
        super().__init__(daemon=True, name=f"AnalyticsWorker-{config.id}")
        self.config_id = config.id
        self._device = device
        self._analyzer_name = config.analyzer_name
        self._analyzer = create_analyzer(config)
        # Cada analizador puede pedir su propio fps de muestreo (ej. Deteccion
        # Facial en modo forense a 25fps); si no lo configura, usa el
        # default global.
        fps = (config.params or {}).get("fps", settings.analytics_fps)
        self._fps = max(0.1, float(fps))
        self._interval_s = 1.0 / self._fps
        self._stop_event = threading.Event()
        self._overrun_warned = False
        self._last_frame_ts = 0.0
        self.stats = WorkerStats()

    def run(self) -> None:
        # Las coordenadas de ROI/linea se configuran sobre la captura del
        # flujo principal; la analitica nunca debe consumir el sub-stream.
        stream_manager.acquire(self._device, "main")
        try:
            while not self._stop_event.is_set():
                start = time.monotonic()

                worker = stream_manager.get_worker(self._device.id, "main")
                if worker is None:
                    frame, frame_ts = None, 0.0
                elif hasattr(worker, "get_latest_frame_with_timestamp"):
                    frame, frame_ts = worker.get_latest_frame_with_timestamp()
                else:
                    # Compatibilidad con workers mínimos usados por
                    # integraciones/tests antiguos que solo exponen
                    # get_latest_frame().
                    frame, frame_ts = worker.get_latest_frame(), time.monotonic()
                if frame is not None and frame_ts == self._last_frame_ts:
                    self._stop_event.wait(MIN_YIELD_S)
                    continue
                if frame is not None:
                    self._last_frame_ts = frame_ts
                    self._analyze(frame)

                elapsed = time.monotonic() - start
                if not self._overrun_warned and frame is not None and elapsed > self._interval_s:
                    # Feedback unico: antes esto degradaba en silencio (y con
                    # wait(0.0) ademas quemaba el core).
                    self._overrun_warned = True
                    logger.warning(
                        "Analizador %s (cámara %s): la inferencia tarda %.0f ms y no alcanza "
                        "los %.1f fps configurados; corre al ritmo que da el CPU",
                        self._analyzer_name,
                        self._device.id,
                        elapsed * 1000,
                        self._fps,
                    )
                self._stop_event.wait(pacing_wait_s(self._interval_s, elapsed))
        finally:
            stream_manager.release(self._device.id, "main")
            try:
                self._analyzer.close()
            except Exception:  # liberar recursos nunca debe matar el shutdown
                logger.exception("Fallo al cerrar el analizador (config %s)", self.config_id)

    def _analyze(self, frame) -> None:
        inference_start = time.monotonic()
        try:
            result = self._analyzer.process_frame(frame, time.time())
        except Exception:
            # Antes una excepcion aca (frame corrupto a mitad de una
            # reconexion, error puntual de onnxruntime) mataba el hilo en
            # silencio: la analitica dejaba de correr pero la UI seguia
            # mostrandola "corriendo". Se loguea con traza la primera vez y
            # despues cada ERROR_LOG_EVERY, para no inundar el log si el
            # fallo es persistente.
            self.stats.errors += 1
            if self.stats.errors == 1 or self.stats.errors % ERROR_LOG_EVERY == 0:
                logger.exception(
                    "Analizador %s (cámara %s): fallo procesando un cuadro (%d errores)",
                    self._analyzer_name,
                    self._device.id,
                    self.stats.errors,
                )
            return
        now = time.monotonic()
        self.stats.record(now, (now - inference_start) * 1000)
        event_bus.detection.emit(
            DetectionEvent(
                device_id=self._device.id,
                analyzer_name=self._analyzer_name,
                timestamp=time.time(),
                detections=result.detections,
                metrics=result.metrics,
                triggers=result.triggers,
            )
        )

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
            # Join acotado: espera a que termine la inferencia en vuelo y
            # corra el close() del analizador. Sin esto, al cerrar la app
            # una inferencia todavia en curso muere contra el teardown del
            # interprete ("cannot schedule new futures after shutdown"),
            # visto en pruebas E2E.
            worker.join(timeout=2.0)

    def is_running(self, config_id: int) -> bool:
        return config_id in self._workers

    def stats(self, config_id: int) -> WorkerStats | None:
        worker = self._workers.get(config_id)
        return worker.stats if worker is not None else None

    def running_count(self) -> int:
        return len(self._workers)

    def stop_all(self) -> None:
        for config_id in list(self._workers):
            self.stop(config_id)


analytics_engine = AnalyticsEngine()
