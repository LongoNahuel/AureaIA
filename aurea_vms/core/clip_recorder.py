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

# Margen sobre clip_post_seconds al esperar los clips en curso durante el
# apagado: el post-buffer todavia tiene que cerrarse y escribir el mp4.
CLIP_SHUTDOWN_MARGIN_S = 5.0

# Threads de escritura de clip en curso. Un daemon thread cortado a mitad de
# cv2.VideoWriter deja el .mp4 corrupto (falta el atomo "moov" que se escribe
# recien en writer.release()) -- wait_for_pending() se llama al cerrar la app
# para darles la chance de terminar antes de que el proceso muera.
_active_threads: list[threading.Thread] = []
_threads_lock = threading.Lock()


def _decode(jpeg_bytes: bytes) -> np.ndarray | None:
    """None si el JPEG esta vacio o corrupto (cv2.imdecode no lanza)."""
    if not jpeg_bytes:
        return None
    return cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)


def _discard(rel_path: str, alarm_event_id: int, reason: str) -> None:
    """Descarta un archivo que no se llego a escribir: lo loguea y lo borra.

    Borrarlo importa: media_assets es el unico indice, asi que un archivo
    que no se indexa queda invisible para el RetentionWorker y no lo limpia
    nunca nadie. Se cambia una fila mentirosa por cero bytes de basura.
    """
    logger.error("Evento #%s: %s. No se indexa y se descarta %s", alarm_event_id, reason, rel_path)
    try:
        media_store.absolute_path(rel_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("No se pudo borrar el archivo descartado %s", rel_path)


def save_snapshot(
    device_id: int, alarm_event_id: int, frame: np.ndarray, created_by: int | None = None
) -> MediaAsset | None:
    """Escribe la captura en el storage por fecha/camara y la registra en
    media_assets. created_by=None significa "la genero el sistema" (alarma
    automatica); las capturas manuales pasan el id del usuario logueado.

    Devuelve None si no se pudo escribir o indexar, siempre con el motivo en
    el log. No levanta: el caller es AlarmEngine._trigger, y perder la
    captura no puede costar tambien la alarma -- el incidente es el dato
    importante, el JPEG es el adorno.
    """
    now = time.time()
    rel_path = media_store.build_rel_path(KIND_SNAPSHOT, device_id, alarm_event_id, now, ".jpg")
    # cv2.imwrite no lanza ante un disco lleno o un formato que este build
    # de OpenCV no sabe codificar: devuelve False. Mirar ese retorno es lo
    # unico que separa "captura guardada" de "fila que apunta a la nada".
    if not cv2.imwrite(str(media_store.prepare_path(rel_path)), frame):
        _discard(rel_path, alarm_event_id, "cv2.imwrite no pudo escribir la captura")
        return None
    try:
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
    except Exception:  # noqa: BLE001 - la alarma vale mas que su captura
        logger.exception("Evento #%s: la captura no se pudo indexar", alarm_event_id)
        _discard(rel_path, alarm_event_id, "el indice rechazó la captura")
        return None


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


def wait_for_pending(timeout: float | None = None) -> None:
    """Espera (con timeout total) a que terminen los clips en curso. Debe
    llamarse ANTES de detener stream_manager, porque el post-buffer necesita
    el stream todavia activo.

    timeout=None deriva el tope de settings.clip_post_seconds. Antes el
    caller pasaba 15.0 fijo: subir clip_post_seconds por encima de eso
    truncaba clips en cada apagado, y el .mp4 quedaba sin el atomo "moov"
    (o sea irreproducible) sin que nadie se enterara.
    """
    if timeout is None:
        timeout = settings.clip_post_seconds + CLIP_SHUTDOWN_MARGIN_S
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
        if asset is None:
            # _write_mp4 ya logueo el motivo y descarto el archivo. No se
            # emite clip_ready: la UI no puede ofrecer "ver el clip" de algo
            # que no existe.
            return
        clip_path = str(media_store.absolute_path(asset.rel_path))
        logger.info(
            "Clip de evento #%s guardado en %s (%d frames)",
            alarm_event_id,
            clip_path,
            asset.duration_s * CLIP_FPS,
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
) -> MediaAsset | None:
    """Arma el mp4 y lo indexa. Devuelve None (con el motivo en el log, y
    sin dejar el archivo a medias en disco) si el clip no se pudo escribir."""
    now = time.time()
    rel_path = media_store.build_rel_path(KIND_CLIP, device_id, alarm_event_id, now, ".mp4")
    path = media_store.prepare_path(rel_path)

    # Las dimensiones del clip salen del primer frame: si ese no decodifica,
    # no hay con que abrir el writer. Antes esto reventaba con un
    # AttributeError sobre None.
    first_frame = _decode(frames[0][1])
    if first_frame is None:
        _discard(rel_path, alarm_event_id, "el primer frame del clip no se pudo decodificar")
        return None
    height, width = first_frame.shape[:2]

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), CLIP_FPS, (width, height))
    # cv2.VideoWriter tampoco lanza ante un codec ausente o un disco lleno:
    # devuelve un writer CERRADO, los write() son no-op y el archivo queda
    # vacio. Sin este chequeo el incidente terminaba con un .mp4
    # irreproducible indexado como evidencia y ni una linea de log.
    if not writer.isOpened():
        writer.release()
        _discard(
            rel_path,
            alarm_event_id,
            f"cv2.VideoWriter no pudo abrir el archivo (mp4v, {width}x{height})",
        )
        return None

    written = 0
    try:
        for _, jpeg_bytes in frames:
            frame = _decode(jpeg_bytes)
            if frame is not None:
                writer.write(frame)
                written += 1
    finally:
        writer.release()

    if written == 0:
        _discard(rel_path, alarm_event_id, "ningún frame del clip se pudo decodificar")
        return None
    if written < len(frames):
        logger.warning(
            "Clip del evento #%s: %d de %d frames no se pudieron decodificar",
            alarm_event_id,
            len(frames) - written,
            len(frames),
        )

    try:
        return media_store.register(
            KIND_CLIP,
            device_id,
            rel_path,
            timestamp=now,
            alarm_event_id=alarm_event_id,
            # Con los frames EFECTIVAMENTE escritos: antes se calculaba con
            # los intentados, asi que un clip con frames corruptos declaraba
            # mas duracion de la que tiene.
            duration_s=written / CLIP_FPS,
            width=width,
            height=height,
        )
    except media_store.MediaWriteError:
        _discard(rel_path, alarm_event_id, "el mp4 quedó vacío después de escribirlo")
        return None
