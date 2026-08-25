"""Storage fisico de media (clips/capturas) + registro en media_assets.

Layout en disco: media/<tipo>/<AAAA>/<MM>/<DD>/<camara>/<HHMMSS>_<evento>.<ext>
ordenado por fecha -> camara para poder navegarlo a mano. El codigo NUNCA
escanea estas carpetas: la tabla media_assets es el unico indice de
busqueda (ver models/media_asset.py) y aca solo se construyen/resuelven
rutas y se registran los archivos recien escritos.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path, PurePosixPath

from aurea_vms.config.settings import settings
from aurea_vms.models import repository
from aurea_vms.models.media_asset import MediaAsset


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
    """Da de alta el archivo (ya escrito en disco) en el indice."""
    size_bytes = absolute_path(rel_path).stat().st_size
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
