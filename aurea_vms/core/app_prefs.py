"""Preferencias de la app que sobreviven entre arranques (tema visual,
retencion de media) -- un JSON chico en data/, no amerita una tabla en la
DB. A diferencia de Settings (frozen, constantes de build), esto es
editable en runtime desde la UI."""

from __future__ import annotations

import json

from aurea_vms.config.settings import settings

_PREFS_PATH = settings.data_dir / "preferences.json"
_DEFAULTS = {"theme": "dark", "retention_days": 7, "retention_max_gb": 5.0}


def _read() -> dict:
    if not _PREFS_PATH.exists():
        return dict(_DEFAULTS)
    try:
        with open(_PREFS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return {**_DEFAULTS, **data}
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULTS)


def _write(data: dict) -> None:
    settings.ensure_dirs()
    with open(_PREFS_PATH, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def get_theme() -> str:
    """ "dark" | "light"."""
    return _read().get("theme", "dark")


def set_theme(theme: str) -> None:
    data = _read()
    data["theme"] = theme
    _write(data)


def get_retention_days() -> int:
    return int(_read().get("retention_days", 7))


def set_retention_days(days: int) -> None:
    data = _read()
    data["retention_days"] = int(days)
    _write(data)


def get_retention_max_gb() -> float:
    return float(_read().get("retention_max_gb", 5.0))


def set_retention_max_gb(max_gb: float) -> None:
    data = _read()
    data["retention_max_gb"] = float(max_gb)
    _write(data)
