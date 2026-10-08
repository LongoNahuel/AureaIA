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
- **Sin configurar no es "7 dias"** (2026-10-07). Si el archivo no trae las
  DOS claves de retencion (no existe, o el operador nunca la guardo),
  `leer_retencion()` levanta `RetencionSinConfigurar` y la retencion no
  poda. Antes valian los defaults (7 dias, 5 GB) en silencio: la base de
  desarrollo de Daniel perdio 190 archivos al arrancar despues de 39 dias
  sin correr, y a un casino le habria pasado lo mismo con su evidencia.
  Se piden las dos: con solo los dias guardados, el tope de 5 GB por
  defecto igual borraba por tamaño.
- **Y tiene que estar confirmada** (`retention_confirmada`, la graba
  `confirmar_retencion`, que usa el boton de Sistema). Hasta el 07/10 cada
  setter escribia los defaults mezclados: cambiar el tema grababa 7 dias y
  5 GB. Un archivo con esas claves no se distingue de una eleccion real, asi
  que las instalaciones previas quedan "sin configurar" hasta que un admin
  confirme (decision de Daniel: no borra y avisa).
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
# Marca de que un admin eligio la retencion (ver el docstring del modulo).
RETENCION_CONFIRMADA = "retention_confirmada"

_lock = threading.Lock()
_avisado = False


class PrefsIlegibles(Exception):
    """preferences.json existe pero no se puede leer o trae valores
    invalidos."""


class RetencionSinConfigurar(Exception):
    """Nadie configuro la retencion: no hay dias ni tope que respetar, asi
    que no se borra nada (ver el docstring del modulo)."""


def _leer_estricto() -> dict:
    return {**_DEFAULTS, **_leer_crudo()}


def _leer_crudo() -> dict:
    """Lo que dice el archivo, sin defaults: {} si no existe."""
    if not _PREFS_PATH.exists():
        return {}
    try:
        with open(_PREFS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrefsIlegibles(f"{_PREFS_PATH}: {exc}") from exc
    if not isinstance(data, dict):
        raise PrefsIlegibles(f"{_PREFS_PATH}: se esperaba un objeto JSON")
    return data


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


def _guardar(**cambios: object) -> None:
    """Guarda SOLO lo que cambia, sobre lo que ya dice el archivo. Antes cada
    setter escribia `_read()`, que trae los defaults mezclados: cambiar el
    tema grababa `retention_days: 7` y `retention_max_gb: 5`, y la retencion
    quedaba "configurada" sin que nadie la eligiera. Con el archivo
    ilegible se parte de cero (y _write aparta el ilegible)."""
    try:
        data = _leer_crudo()
    except PrefsIlegibles:
        data = {}
    data.update(cambios)
    _write(data)


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
    no se puede leer o los valores no son numeros validos, y
    RetencionSinConfigurar si falta alguno de los dos. Quien poda con esto
    no tiene que caer a los defaults: podar de mas borra evidencia."""
    data = _leer_crudo()
    if "retention_days" not in data or "retention_max_gb" not in data:
        raise RetencionSinConfigurar(f"{_PREFS_PATH}: sin días ni tope de retención guardados")
    if not data.get(RETENCION_CONFIRMADA):
        raise RetencionSinConfigurar(f"{_PREFS_PATH}: la retención nunca se confirmó en Sistema")
    try:
        dias = float(data["retention_days"])
        gb = float(data["retention_max_gb"])
    except (TypeError, ValueError) as exc:
        raise PrefsIlegibles(f"{_PREFS_PATH}: valores de retención inválidos ({exc})") from exc
    if not (dias >= RETENCION_MIN_DIAS and gb >= RETENCION_MIN_GB):
        raise PrefsIlegibles(f"{_PREFS_PATH}: retención fuera de rango ({dias} días, {gb} GB)")
    return dias, gb


def confirmar_retencion(dias: float, max_gb: float) -> None:
    """Lo que hace el boton "Guardar retención" de Sistema: dias, tope y la
    marca de que alguien los eligio, en una sola escritura atomica. Valida
    contra los minimos de la UI antes de escribir."""
    if not (dias >= RETENCION_MIN_DIAS and max_gb >= RETENCION_MIN_GB):
        raise ValueError(f"retención fuera de rango ({dias} días, {max_gb} GB)")
    _guardar(
        retention_days=int(dias),
        retention_max_gb=float(max_gb),
        **{RETENCION_CONFIRMADA: datetime.now().isoformat(timespec="seconds")},
    )


def retencion_configurada() -> bool:
    """Para la UI: True si la retencion tiene dias y tope guardados y
    validos (o sea, si la proxima pasada va a podar)."""
    try:
        leer_retencion()
    except (PrefsIlegibles, RetencionSinConfigurar):
        return False
    return True


def get_theme() -> str:
    """ "dark" | "light"."""
    return _read().get("theme", "dark")


def set_theme(theme: str) -> None:
    _guardar(theme=theme)


def get_retention_days() -> int:
    return int(_read().get("retention_days", 7))


def set_retention_days(days: int) -> None:
    _guardar(retention_days=int(days))


def get_retention_max_gb() -> float:
    return float(_read().get("retention_max_gb", 5.0))


def set_retention_max_gb(max_gb: float) -> None:
    _guardar(retention_max_gb=float(max_gb))


def intelligent_branding_enabled() -> bool:
    return bool(_read().get("intelligent_branding", True))


def set_intelligent_branding_enabled(enabled: bool) -> None:
    _guardar(intelligent_branding=bool(enabled))


def get_brand_name() -> str:
    return str(_read().get("brand_name", "AureaIA Intelligence")).strip() or "AureaIA Intelligence"
