"""Captura RTSP continua por camara.

Cada StreamWorker mantiene solo el ultimo frame leido, protegido por un
lock (no encola frames viejos: prioriza baja latencia). La UI (via QTimer)
y cada Analyzer activo leen ese mismo slot a su propio ritmo con
get_latest_frame() -- no hace falta un "tee" tipo GStreamer.

Ademas mantiene un buffer historico corto (frames JPEG, acotado por tiempo
a settings.clip_pre_seconds) para poder armar el "pre-buffer" de un clip de
evento sin tener que arrancar a grabar recien cuando salta la alarma.

StreamManager lleva referencia por (dispositivo, calidad): "main" es el
flujo RTSP principal (rtsp_main_url, mayor resolucion) y "sub" el
sub-flujo (rtsp_sub_url, mas liviano). Si dos consumidores piden la misma
camara+calidad, comparten un unico worker/conexion RTSP. Si se pide "sub"
pero el dispositivo no tiene sub-flujo configurado, se cae a "main" sin
abrir una segunda conexion redundante.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

import cv2
import numpy as np

from aurea_vms.config.settings import settings
from aurea_vms.core.device_manager import build_authenticated_url
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import DeviceStatusEvent
from aurea_vms.models import repository
from aurea_vms.models.device import Device

logger = logging.getLogger(__name__)

RECONNECT_DELAY_S = 3.0
HISTORY_FPS = 5.0
JPEG_QUALITY = 80
FPS_WINDOW_SIZE = 60

# Sin estos timeouts, un cap.read() contra una camara que se cae "sucio"
# (sin cerrar el TCP: cable cortado, switch reiniciado) puede bloquear el
# hilo indefinidamente. Con read-timeout, read() devuelve False y el loop
# de reconexion existente actua de watchdog.
OPEN_TIMEOUT_MS = 10_000
READ_TIMEOUT_MS = 10_000
STALE_FRAME_S = 15.0


class StreamWorker(threading.Thread):
    def __init__(self, device: Device, kind: str = "main") -> None:
        super().__init__(daemon=True, name=f"StreamWorker-{device.id}-{kind}")
        self.device_id = device.id
        self.kind = kind
        raw_url = (
            device.rtsp_sub_url if kind == "sub" and device.rtsp_sub_url else device.rtsp_main_url
        )
        self._url = build_authenticated_url(raw_url, device.username, device.password)
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._latest_frame_ts: float = 0.0
        self._stop_event = threading.Event()
        # Ultimo estado persistido en DB por ESTE worker: sin esto, el loop
        # de reconexion escribiria "offline" cada ~13s por camara caida.
        self._last_persisted_online: bool | None = None

        self._history: deque[tuple[float, bytes]] = deque()
        self._history_interval = 1.0 / HISTORY_FPS
        self._last_history_ts = 0.0
        self._frame_times: deque[float] = deque(maxlen=FPS_WINDOW_SIZE)

    def run(self) -> None:
        while not self._stop_event.is_set():
            cap = cv2.VideoCapture(
                self._url,
                cv2.CAP_FFMPEG,
                [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                    OPEN_TIMEOUT_MS,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                    READ_TIMEOUT_MS,
                ],
            )
            if not cap.isOpened():
                cap.release()
                logger.warning(
                    "Camara %s (%s): no se pudo abrir el stream", self.device_id, self.kind
                )
                self._report_status(False, "No se pudo abrir el stream")
                if self._stop_event.wait(RECONNECT_DELAY_S):
                    break
                continue

            logger.info("Camara %s (%s): conectada", self.device_id, self.kind)
            self._report_status(True, "Conectado")
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                now = time.time()
                with self._lock:
                    self._latest_frame = frame
                    self._latest_frame_ts = time.monotonic()
                    self._frame_times.append(now)
                    if now - self._last_history_ts >= self._history_interval:
                        self._last_history_ts = now
                        self._append_history(frame, now)
            cap.release()

            if self._stop_event.is_set():
                break
            logger.warning("Camara %s: se perdió la conexión, reintentando...", self.device_id)
            self._report_status(False, "Se perdió la conexión, reintentando...")
            self._stop_event.wait(RECONNECT_DELAY_S)

    def _append_history(self, frame: np.ndarray, now: float) -> None:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if ok:
            self._history.append((now, buf.tobytes()))
        cutoff = now - settings.clip_pre_seconds
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def _report_status(self, online: bool, detail: str) -> None:
        event_bus.device_status.emit(
            DeviceStatusEvent(device_id=self.device_id, online=online, detail=detail)
        )
        # Persistir el estado real del stream: antes solo lo escribia el
        # boton "probar conexion", asi que el dashboard mostraba camaras
        # transmitiendo como "Sin probar". Solo en transiciones (dedup por
        # worker) y sin dejar que un error de DB mate el hilo de captura.
        if online == self._last_persisted_online:
            return
        try:
            repository.update_device_status(self.device_id, "online" if online else "offline")
            self._last_persisted_online = online
        except Exception:  # noqa: BLE001 - la captura sigue aunque la DB falle
            logger.exception("Cámara %s: no se pudo persistir el estado", self.device_id)

    def get_latest_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    def get_recent_history(self) -> list[tuple[float, bytes]]:
        """Frames JPEG de los ultimos settings.clip_pre_seconds, mas viejo primero."""
        with self._lock:
            return list(self._history)

    def get_fps(self) -> float:
        """FPS medido en base a los ultimos frames recibidos (no el nominal de la camara)."""
        with self._lock:
            times = list(self._frame_times)
        if len(times) < 2:
            return 0.0
        span = times[-1] - times[0]
        return (len(times) - 1) / span if span > 0 else 0.0

    def is_stale(self, max_age_s: float = STALE_FRAME_S) -> bool:
        """True si el ultimo frame es demasiado viejo (stream congelado o en
        reconexion) -- la UI puede mostrar "reconectando" en vez de una
        imagen vieja que parece en vivo."""
        with self._lock:
            ts = self._latest_frame_ts
        return ts == 0.0 or (time.monotonic() - ts) > max_age_s

    def stop(self) -> None:
        self._stop_event.set()


WorkerKey = tuple[int, str]


class StreamManager:
    """acquire()/release() se llaman tanto desde el hilo de UI (VideoTile)
    como desde los threads de analitica (AnalyticsWorker.run) -- todos los
    accesos a _workers/_refcounts van bajo _lock, si no dos acquire()
    concurrentes pueden crear dos workers para la misma camara y romper el
    ref-counting. RLock porque stop_device() itera y detiene bajo el mismo
    lock."""

    def __init__(self) -> None:
        self._workers: dict[WorkerKey, StreamWorker] = {}
        self._refcounts: dict[WorkerKey, int] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _effective_kind(device: Device, kind: str) -> str:
        # Sin sub-flujo configurado, "sub" reusa la conexion "main" en vez
        # de abrir una segunda conexion redundante a la misma camara.
        return kind if kind == "main" or device.rtsp_sub_url else "main"

    def acquire(self, device: Device, kind: str = "main") -> StreamWorker:
        key = (device.id, self._effective_kind(device, kind))
        with self._lock:
            worker = self._workers.get(key)
            if worker is None:
                logger.info("Cámara %s: arrancando StreamWorker (%s)", key[0], key[1])
                worker = StreamWorker(device, key[1])
                self._workers[key] = worker
                self._refcounts[key] = 0
                worker.start()
            self._refcounts[key] += 1
            return worker

    def _resolve_key(self, device_id: int, kind: str) -> WorkerKey | None:
        """acquire() puede haber redirigido "sub" -> "main" (sin sub-flujo
        configurado); release()/get_worker() necesitan encontrar esa misma
        entrada aunque no conozcan el Device para recalcular el fallback."""
        key = (device_id, kind)
        if key in self._workers:
            return key
        fallback = (device_id, "main")
        return fallback if kind != "main" and fallback in self._workers else None

    def release(self, device_id: int, kind: str = "main") -> None:
        with self._lock:
            key = self._resolve_key(device_id, kind)
            if key is None:
                return
            self._refcounts[key] -= 1
            if self._refcounts[key] <= 0:
                self._workers.pop(key).stop()
                self._refcounts.pop(key, None)

    def get_worker(self, device_id: int, kind: str = "main") -> StreamWorker | None:
        with self._lock:
            key = self._resolve_key(device_id, kind)
            return self._workers.get(key) if key else None

    def stop_device(self, device_id: int) -> None:
        """Corta todos los streams de una camara sin esperar releases: al
        borrar el dispositivo no debe quedar ningun worker vivo aunque haya
        tiles o analiticas que todavia lo referencien."""
        with self._lock:
            for key in [k for k in self._workers if k[0] == device_id]:
                self._workers.pop(key).stop()
                self._refcounts.pop(key, None)

    def stop_all(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
            self._refcounts.clear()
        for worker in workers:
            worker.stop()
        # Espera acotada: logout->login re-arranca engines y sin el join se
        # acumulan sockets/threads zombies de la sesion anterior.
        for worker in workers:
            worker.join(timeout=2.0)


stream_manager = StreamManager()
