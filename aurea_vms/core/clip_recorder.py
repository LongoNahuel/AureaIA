"""Snapshot inmediato + armado de clip mp4 (pre-buffer del StreamWorker +
post-captura) cuando una regla de alarma con accion "save_clip" se dispara.
"""

from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

from aurea_vms.config.settings import settings
from aurea_vms.core import media_store
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import ClipReadyEvent
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models.media_asset import KIND_CLIP, KIND_SNAPSHOT, MediaAsset

logger = logging.getLogger(__name__)

CLIP_FPS = 5.0

# Threads de escritura de clip en curso. Un daemon thread cortado a mitad de
# cv2.VideoWriter deja el .mp4 corrupto (falta el atomo "moov" que se escribe
# recien en writer.release()) -- wait_for_pending() se llama al cerrar la app
# para darles la chance de terminar antes de que el proceso muera.
_active_threads: list[threading.Thread] = []
_threads_lock = threading.Lock()


def save_snapshot(
    device_id: int, alarm_event_id: int, frame: np.ndarray, created_by: int | None = None
) -> MediaAsset:
    """Escribe la captura en el storage por fecha/camara y la registra en
    media_assets. created_by=None significa "la genero el sistema" (alarma
    automatica); las capturas manuales pasan el id del usuario logueado."""
    now = time.time()
    rel_path = media_store.build_rel_path(KIND_SNAPSHOT, device_id, alarm_event_id, now, ".jpg")
    cv2.imwrite(str(media_store.prepare_path(rel_path)), frame)
    return media_store.register(
        KIND_SNAPSHOT,
        device_id,
        rel_path,
        timestamp=now,
        alarm_event_id=alarm_event_id,
        created_by=created_by,
        height=frame.shape[0],
        width=frame.shape[1],
    )


def record_clip_async(device_id: int, alarm_event_id: int) -> None:
    thread = threading.Thread(
        target=_record_clip,
        args=(device_id, alarm_event_id),
        daemon=True,
        name=f"ClipWriter-{alarm_event_id}",
    )
    with _threads_lock:
        _active_threads.append(thread)
    thread.start()


def wait_for_pending(timeout: float = 15.0) -> None:
    """Espera (con timeout total) a que terminen los clips en curso. Debe
    llamarse ANTES de detener stream_manager, porque el post-buffer necesita
    el stream todavia activo."""
    with _threads_lock:
        threads = list(_active_threads)
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))


def _record_clip(device_id: int, alarm_event_id: int) -> None:
    try:
        worker = stream_manager.get_worker(device_id)
        pre_frames = worker.get_recent_history() if worker else []

        post_frames: list[tuple[float, bytes]] = []
        interval = 1.0 / CLIP_FPS
        deadline = time.monotonic() + settings.clip_post_seconds
        while time.monotonic() < deadline:
            start = time.monotonic()
            worker = stream_manager.get_worker(device_id)
            frame = worker.get_latest_frame() if worker else None
            if frame is not None:
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    post_frames.append((time.time(), buf.tobytes()))
            elapsed = time.monotonic() - start
            time.sleep(max(0.0, interval - elapsed))

        all_frames = pre_frames + post_frames
        if not all_frames:
            return

        asset = _write_mp4(device_id, alarm_event_id, all_frames)
        clip_path = str(media_store.absolute_path(asset.rel_path))
        logger.info(
            "Clip de evento #%s guardado en %s (%d frames)",
            alarm_event_id,
            clip_path,
            len(all_frames),
        )
        event_bus.clip_ready.emit(
            ClipReadyEvent(alarm_event_id=alarm_event_id, clip_path=clip_path)
        )
    except Exception:
        # El thread es daemon: sin este log una falla de escritura (disco
        # lleno, codec ausente, frame corrupto) moria en silencio y el
        # evento quedaba para siempre "sin clip" sin ninguna pista.
        logger.exception("Falló la grabación del clip del evento #%s", alarm_event_id)
    finally:
        with _threads_lock:
            current = threading.current_thread()
            if current in _active_threads:
                _active_threads.remove(current)


def _write_mp4(
    device_id: int, alarm_event_id: int, frames: list[tuple[float, bytes]]
) -> MediaAsset:
    now = time.time()
    rel_path = media_store.build_rel_path(KIND_CLIP, device_id, alarm_event_id, now, ".mp4")
    path = media_store.prepare_path(rel_path)

    first_frame = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
    height, width = first_frame.shape[:2]

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), CLIP_FPS, (width, height))
    try:
        for _, jpeg_bytes in frames:
            frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                writer.write(frame)
    finally:
        writer.release()

    return media_store.register(
        KIND_CLIP,
        device_id,
        rel_path,
        timestamp=now,
        alarm_event_id=alarm_event_id,
        duration_s=len(frames) / CLIP_FPS,
        width=width,
        height=height,
    )
