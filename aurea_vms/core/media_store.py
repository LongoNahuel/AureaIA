"""Storage fisico de media (clips/capturas) + registro en media_assets.

Layout en disco: media/<tipo>/<AAAA>/<MM>/<DD>/<camara>/<HHMMSS>_<evento>.<ext>
ordenado por fecha -> camara para poder navegarlo a mano. El codigo NUNCA
escanea estas carpetas: la tabla media_assets es el unico indice de
busqueda (ver models/media_asset.py) y aca solo se construyen/resuelven
rutas y se registran los archivos recien escritos.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path, PurePosixPath

from aurea_vms.config.settings import settings
from aurea_vms.models import repository
from aurea_vms.models.media_asset import MediaAsset

logger = logging.getLogger(__name__)


class MediaWriteError(RuntimeError):
    """El archivo que se iba a indexar no existe o quedo vacio.

    media_assets es el UNICO indice de busqueda de evidencia: nada escanea
    las carpetas. Una fila que apunta a un archivo inexistente o de 0 bytes
    es peor que no tener la fila, porque el operador la ve listada como
    evidencia valida de un incidente y se entera recien al abrirla, cuando
    ya no hay nada que recuperar. Por eso register() valida en vez de
    confiar en el caller: cv2 no avisa por excepcion cuando un imwrite
    falla o un VideoWriter no abre.
    """


def build_rel_path(kind: str, device_id: int, event_id: int | None, when: float, ext: str) -> str:
    """Ruta relativa a settings.media_dir, siempre con "/" (portable)."""
    stamp = dt.datetime.fromtimestamp(when)
    suffix = f"_{event_id}" if event_id is not None else ""
    return f"{kind}/{stamp:%Y/%m/%d}/{device_id}/{stamp:%H%M%S}{suffix}{ext}"


def absolute_path(rel_path: str) -> Path:
    return settings.media_dir / PurePosixPath(rel_path)


def prepare_path(rel_path: str) -> Path:
    """Ruta absoluta con el directorio padre ya creado, lista para escribir."""
    path = absolute_path(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def register(
    kind: str,
    device_id: int,
    rel_path: str,
    *,
    timestamp: float,
    alarm_event_id: int | None = None,
    created_by: int | None = None,
    duration_s: float | None = None,
    width: int | None = None,
    height: int | None = None,
) -> MediaAsset:
    """Da de alta el archivo (ya escrito en disco) en el indice.

    Levanta MediaWriteError si el archivo no llego a escribirse. Antes el
    .stat() explotaba con un FileNotFoundError crudo si faltaba, y si el
    archivo existia pero estaba vacio la fila se insertaba igual.
    """
    path = absolute_path(rel_path)
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        logger.error("No se indexa %s: no se pudo leer el archivo (%s)", rel_path, exc)
        raise MediaWriteError(f"El archivo de media no existe: {path}") from exc
    if size_bytes == 0:
        logger.error("No se indexa %s: el archivo quedó vacío (0 bytes)", rel_path)
        raise MediaWriteError(f"El archivo de media quedó vacío: {path}")
    return repository.add_media_asset(
        kind=kind,
        device_id=device_id,
        alarm_event_id=alarm_event_id,
        created_by=created_by,
        timestamp=timestamp,
        rel_path=rel_path,
        size_bytes=size_bytes,
        duration_s=duration_s,
        width=width,
        height=height,
    )
