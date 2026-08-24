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
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import ClipReadyEvent
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository

logger = logging.getLogger(__name__)

CLIP_FPS = 5.0

# Threads de escritura de clip en curso. Un daemon thread cortado a mitad de
# cv2.VideoWriter deja el .mp4 corrupto (falta el atomo "moov" que se escribe
# recien en writer.release()) -- wait_for_pending() se llama al cerrar la app
# para darles la chance de terminar antes de que el proceso muera.
_active_threads: list[threading.Thread] = []
_threads_lock = threading.Lock()


def save_snapshot(device_id: int, alarm_event_id: int, frame: np.ndarray) -> str:
    directory = settings.snapshots_dir / str(device_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{alarm_event_id}.jpg"
    cv2.imwrite(str(path), frame)
    return str(path)


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

        clip_path = _write_mp4(device_id, alarm_event_id, all_frames)
        repository.update_alarm_event(alarm_event_id, clip_path=clip_path)
        logger.info("Clip de evento #%s guardado en %s (%d frames)", alarm_event_id, clip_path, len(all_frames))
        event_bus.clip_ready.emit(ClipReadyEvent(alarm_event_id=alarm_event_id, clip_path=clip_path))
    finally:
        with _threads_lock:
            current = threading.current_thread()
            if current in _active_threads:
                _active_threads.remove(current)


def _write_mp4(device_id: int, alarm_event_id: int, frames: list[tuple[float, bytes]]) -> str:
    directory = settings.clips_dir / str(device_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{alarm_event_id}.mp4"

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

    return str(path)
