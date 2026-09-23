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
import random
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
RECONNECT_MAX_DELAY_S = 30.0
RECONNECT_JITTER = 0.25
HISTORY_FPS = 5.0
JPEG_QUALITY = 80
FPS_WINDOW_SIZE = 60
FRAME_SIGNATURE_STEP = 32

# Sin estos timeouts, un cap.read() contra una camara que se cae "sucio"
# (sin cerrar el TCP: cable cortado, switch reiniciado) puede bloquear el
# hilo indefinidamente. Con read-timeout, read() devuelve False y el loop
# de reconexion existente actua de watchdog.
OPEN_TIMEOUT_MS = 3_000
READ_TIMEOUT_MS = 3_000
STALE_FRAME_S = 15.0
CAPTURE_BUFFER_SIZE = 1

# Cuanto tiempo seguido tiene que repetirse EXACTAMENTE el mismo frame para
# dar el stream por congelado. Mismo valor que STALE_FRAME_S pero otra cosa:
# aquel mide "la imagen que tiene la UI esta vieja", este mide "el decoder
# nos devuelve siempre la misma foto".
FROZEN_STREAM_S = STALE_FRAME_S


def _reconnect_delay(attempt: int, *, jitter: bool = True) -> float:
    """Espera antes del intento numero `attempt` (1 = el primero tras caerse).

    Backoff exponencial desde RECONNECT_DELAY_S hasta RECONNECT_MAX_DELAY_S.
    Antes era un 3.0 fijo para siempre: contra una camara apagada de verdad,
    y sabiendo que cv2.VideoCapture bloquea hasta OPEN_TIMEOUT_MS, eso son
    ~275 aperturas de socket por hora y por camara, todas condenadas a
    fallar, hasta que alguien la vuelva a enchufar.

    El jitter (hasta +RECONNECT_JITTER) evita que N camaras que se caen
    juntas -- un switch que se reinicia es el caso tipico -- se queden
    reintentando en fase para siempre.
    """
    base = min(RECONNECT_DELAY_S * 2 ** max(0, attempt - 1), RECONNECT_MAX_DELAY_S)
    if not jitter:
        return base
    return base * (1.0 + random.random() * RECONNECT_JITTER)  # noqa: S311 - no es cripto


def _frame_signature(frame: np.ndarray) -> bytes:
    """Firma barata para detectar el frame repetido EXACTO.

    Submuestrea 1 de cada FRAME_SIGNATURE_STEP pixeles por eje: ~200 valores
    en 1080p, microsegundos por frame. No mide "parecido" ni pretende
    hacerlo: lo unico que busca es la foto identica que devuelve un decoder
    colgado.
    """
    return frame[::FRAME_SIGNATURE_STEP, ::FRAME_SIGNATURE_STEP].tobytes()


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
        attempt = 0
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
            # Algunos backends y dobles de prueba no exponen set(); la
            # captura sigue funcionando sin este ajuste opcional.
            if hasattr(cap, "set"):
                cap.set(cv2.CAP_PROP_BUFFERSIZE, CAPTURE_BUFFER_SIZE)
            if not cap.isOpened():
                cap.release()
                attempt += 1
                if self._wait_before_retry(attempt, "No se pudo abrir el stream"):
                    break
                continue

            logger.info(
                "Camara %s (%s): socket RTSP conectado, esperando primer frame",
                self.device_id,
                self.kind,
            )
            delivered_frames, frozen = self._capture_loop(cap)
            cap.release()

            if self._stop_event.is_set():
                break

            # El backoff se resetea solo si la conexion ENTREGO frames. Con
            # resetear ante isOpened() alcanzaria para el caso feliz, pero
            # una camara que abre el socket y se muere al instante (firmware
            # colgado, NVR saturado) nunca saldria del delay minimo y
            # seguiriamos con los 3s fijos de antes.
            attempt = 1 if delivered_frames else attempt + 1
            reason = "El stream quedó congelado" if frozen else "Se perdió la conexión"
            if self._wait_before_retry(attempt, reason):
                break

    def _wait_before_retry(self, attempt: int, reason: str) -> bool:
        """Reporta el estado y espera el backoff. True si hay que cortar el
        hilo (alguien llamo a stop() mientras esperaba)."""
        delay = _reconnect_delay(attempt)
        logger.warning(
            "Camara %s (%s): %s (intento %d, reintenta en %.0fs)",
            self.device_id,
            self.kind,
            reason,
            attempt,
            delay,
        )
        self._report_status(False, f"{reason}, reintentando en {delay:.0f}s")
        return self._stop_event.wait(delay)

    def _capture_loop(self, cap) -> tuple[bool, bool]:
        """Lee frames hasta que el stream se corta, se congela o paran el
        worker. Devuelve (entregó frames, quedó congelado)."""
        delivered_frames = False
        last_signature: bytes | None = None
        identical_since = 0.0

        while not self._stop_event.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                return delivered_frames, False

            now = time.time()
            captured_at = time.monotonic()
            if not delivered_frames:
                logger.info("Camara %s (%s): conectada", self.device_id, self.kind)
                self._report_status(True, "Conectado")

            # Watchdog de congelado. Un decoder colgado sigue devolviendo
            # ok=True con el MISMO frame ya decodificado, asi que el corte
            # por read() fallido no llega nunca y la camara se queda
            # mostrando una foto vieja que parece en vivo -- justo lo que
            # is_stale() detectaba para la UI sin que nadie reconectara.
            # Se corta solo si la firma es identica por mas de
            # FROZEN_STREAM_S SEGUIDOS: con ruido de sensor real eso no pasa
            # nunca, y el doble requisito evita cortar un mp4 en loop con un
            # tramo estatico (el rig de demo de tools/demo).
            signature = _frame_signature(frame)
            if signature != last_signature:
                last_signature = signature
                identical_since = captured_at
            elif captured_at - identical_since > FROZEN_STREAM_S:
                logger.warning(
                    "Camara %s (%s): el mismo frame hace %.0fs, se da por congelado",
                    self.device_id,
                    self.kind,
                    captured_at - identical_since,
                )
                return True, True

            with self._lock:
                self._latest_frame = frame
                self._latest_frame_ts = captured_at
                self._frame_times.append(now)
                if now - self._last_history_ts >= self._history_interval:
                    self._last_history_ts = now
                    self._append_history(frame, now)
            delivered_frames = True

        return delivered_frames, False

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
            # El capturador nunca vuelve a modificar el array despues de
            # publicarlo: reemplaza la referencia por el siguiente frame.
            # Compartirlo evita copiar varios megabytes por cada tile,
            # analitica y preview de configuracion.
            return self._latest_frame

    def get_latest_frame_with_timestamp(self) -> tuple[np.ndarray | None, float]:
        """Devuelve el último frame y su timestamp de captura.

        Las analíticas usan esta variante para no inferir varias veces sobre
        la misma imagen cuando el CPU tarda más que el FPS solicitado.
        """
        with self._lock:
            return self._latest_frame, self._latest_frame_ts

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
        worker_to_stop = None
        with self._lock:
            key = self._resolve_key(device_id, kind)
            if key is None:
                return
            self._refcounts[key] -= 1
            if self._refcounts[key] <= 0:
                worker_to_stop = self._workers.pop(key)
                worker_to_stop.stop()
                self._refcounts.pop(key, None)
        if worker_to_stop is not None:
            worker_to_stop.join(timeout=1.0)

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
