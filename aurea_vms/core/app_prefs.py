"""Preferencias de la app que sobreviven entre arranques (tema visual,
retencion de media) -- un JSON chico en data/, no amerita una tabla en la
DB. A diferencia de Settings (frozen, constantes de build), esto es
editable en runtime desde la UI.

**Un archivo ilegible no es un archivo que no existe** (Fase 4,
2026-09-24). Antes los dos devolvian los defaults, y la retencion no los
distinguia: un `preferences.json` truncado por un corte de luz a mitad de
`_write` (que ademas no era atomico) hacia que la siguiente pasada podara
con 7 dias y 5 GB, cuando el operador habia configurado 90 dias y 500 GB --
borrando evidencia que tenia que conservar. Ahora:

- `_write` es atomico: temporal en la misma carpeta + fsync + os.replace.
  Un corte deja el archivo viejo o el nuevo, nunca uno a medias.
- `leer_retencion()` es estricta: con el archivo ilegible, o con valores
  de retencion invalidos, levanta `PrefsIlegibles` y la retencion no poda.
- Los getters de la UI (tema, marca) siguen cayendo a los defaults, para
  que un JSON roto no deje la app sin abrir; lo loguean una vez.
- Guardar una preferencia sobre un archivo ilegible lo aparta primero
  (`preferences.json.ilegible-<fecha>`) en vez de pisarlo.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime

from aurea_vms.config.settings import settings

logger = logging.getLogger(__name__)

_PREFS_PATH = settings.data_dir / "preferences.json"
_DEFAULTS = {
    "theme": "dark",
    "retention_days": 7,
    "retention_max_gb": 5.0,
    "intelligent_branding": True,
    "brand_name": "AureaIA Intelligence",
}
# Los minimos que ofrece la UI (system_module): un valor por debajo no lo
# eligio nadie, y podar con 0 dias es borrar todo.
RETENCION_MIN_DIAS = 1
RETENCION_MIN_GB = 0.5

_lock = threading.Lock()
_avisado = False


class PrefsIlegibles(Exception):
    """preferences.json existe pero no se puede leer o trae valores
    invalidos."""


def _leer_estricto() -> dict:
    if not _PREFS_PATH.exists():
        return dict(_DEFAULTS)
    try:
        with open(_PREFS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrefsIlegibles(f"{_PREFS_PATH}: {exc}") from exc
    if not isinstance(data, dict):
        raise PrefsIlegibles(f"{_PREFS_PATH}: se esperaba un objeto JSON")
    return {**_DEFAULTS, **data}


def _read() -> dict:
    """Para la UI: con el archivo ilegible devuelve los defaults (y lo
    avisa una vez). La retencion NO usa esto: ver leer_retencion()."""
    global _avisado
    try:
        return _leer_estricto()
    except PrefsIlegibles as exc:
        if not _avisado:
            _avisado = True
            logger.error("Preferencias ilegibles, se usan los valores por defecto: %s", exc)
        return dict(_DEFAULTS)


def _write(data: dict) -> None:
    settings.ensure_dirs()
    with _lock:
        if _PREFS_PATH.exists():
            try:
                _leer_estricto()
            except PrefsIlegibles:
                apartado = _PREFS_PATH.with_name(
                    f"{_PREFS_PATH.name}.ilegible-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                )
                os.replace(_PREFS_PATH, apartado)
                logger.warning("Preferencias ilegibles apartadas en %s", apartado)
        temporal = _PREFS_PATH.with_name(f".{_PREFS_PATH.name}.{os.getpid()}.tmp")
        try:
            with open(temporal, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporal, _PREFS_PATH)
        finally:
            temporal.unlink(missing_ok=True)


def leer_retencion() -> tuple[float, float]:
    """(dias, GB) para podar. Estricta: levanta PrefsIlegibles si el archivo
    no se puede leer o los valores no son numeros validos. Quien poda con
    esto no tiene que caer a los defaults: podar de mas borra evidencia."""
    data = _leer_estricto()
    try:
        dias = float(data["retention_days"])
        gb = float(data["retention_max_gb"])
    except (TypeError, ValueError) as exc:
        raise PrefsIlegibles(f"{_PREFS_PATH}: valores de retención inválidos ({exc})") from exc
    if not (dias >= RETENCION_MIN_DIAS and gb >= RETENCION_MIN_GB):
        raise PrefsIlegibles(f"{_PREFS_PATH}: retención fuera de rango ({dias} días, {gb} GB)")
    return dias, gb


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


def intelligent_branding_enabled() -> bool:
    return bool(_read().get("intelligent_branding", True))


def set_intelligent_branding_enabled(enabled: bool) -> None:
    data = _read()
    data["intelligent_branding"] = bool(enabled)
    _write(data)


def get_brand_name() -> str:
    return str(_read().get("brand_name", "AureaIA Intelligence")).strip() or "AureaIA Intelligence"
