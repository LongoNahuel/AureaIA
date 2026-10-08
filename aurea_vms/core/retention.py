"""Retencion de media: purga por edad y por tope de tamaño total.

Corre como daemon thread (mismo patron Event.wait que StreamWorker /
AnalyticsWorker) porque debe trabajar aunque la UI este en el login, y
porque borrar cientos de archivos toma segundos que no son del hilo de UI.

Todas las decisiones se toman contra la DB (media_assets, ordenada por el
indice de timestamp) -- nunca se escanea el filesystem. El disco solo se
toca para hacer unlink de archivos ya elegidos.

Tres reglas que no borran evidencia sin que alguien lo decida (2026-10-07,
despues de que la base de desarrollo perdiera 190 archivos en un arranque):
- **Sin retencion configurada no se poda** (app_prefs.RetencionSinConfigurar).
- **La evidencia de un incidente en investigacion nunca es candidata**
  (repository.list_media_oldest_first). Solo "en investigacion": nadie
  resuelve las alarmas, y proteger todo lo no resuelto llenaba el disco.
- **Tope por pasada**: el 10 % de la media, entre TOPE_MINIMO y TOPE_ARCHIVOS
  archivos (ver tope_de_la_pasada). Una PC apagada semanas ya no borra todo lo vencido en el
  primer minuto: borra hasta el tope, lo loguea en ERROR y sigue de a poco
  en las pasadas siguientes, con tiempo para frenarlo.

Los avisos de "sin configurar" y de "evidencia protegida" salen una vez
cuando el estado aparece, no en cada pasada (2026-10-08): repetidos cada 30
minutos tapaban el resto del log. Si el estado se va y vuelve, se avisa de
nuevo.
"""

from __future__ import annotations

import logging
import threading
import time

from aurea_vms.core import app_prefs, media_store
from aurea_vms.models import repository
from aurea_vms.models.media_asset import MediaAsset

logger = logging.getLogger(__name__)

INTERVAL_S = 1800.0  # una pasada cada 30 minutos
FIRST_PASS_DELAY_S = 60.0  # dejar arrancar la app antes de la primera
# Nunca tocar media recien creada: el clip de una alarma en curso se
# registra al terminar de escribirse, pero el margen evita ademas borrar
# la evidencia de un evento que el operador esta mirando ahora mismo.
MIN_AGE_S = 120.0
_BATCH = 500
TOPE_FRACCION = 0.10
TOPE_MINIMO = 50
TOPE_ARCHIVOS = 500


def tope_de_la_pasada(total_archivos: int) -> int:
    """Cuantos archivos puede borrar una pasada: el 10 %, entre 50 y 500. El
    piso es para que una instalacion chica pode normal (el 10 % de 30
    archivos es 3); el techo, para que una grande no pierda miles de golpe."""
    return min(TOPE_ARCHIVOS, max(TOPE_MINIMO, int(total_archivos * TOPE_FRACCION)))


Stats = dict[str, int | bool]


def prune(*, max_age_days: float, max_total_gb: float, now: float | None = None) -> Stats:
    """Una pasada completa de retencion. Devuelve estadisticas.

    1) Borra todo lo mas viejo que max_age_days.
    2) Si el total sigue sobre max_total_gb, borra lo mas viejo hasta
       volver bajo el tope (respetando MIN_AGE_S).

    `al_tope`: la pasada corto en el tope y queda algo por borrar (si lo
    borrado justo agota lo vencido, no). `protegida`: se paso del tope de GB
    y lo que queda no se puede borrar (evidencia en investigacion o media
    recien creada).
    """
    now = time.time() if now is None else now
    stats: Stats = {"deleted": 0, "freed_bytes": 0, "al_tope": False, "protegida": False}
    tope = tope_de_la_pasada(repository.count_media())

    age_cutoff = min(now - max_age_days * 86400.0, now - MIN_AGE_S)
    while True:
        restante = tope - stats["deleted"]
        if restante <= 0:
            # Llegar al tope no es "queda mas": solo si hay otro vencido.
            stats["al_tope"] = bool(
                repository.list_media_oldest_first(older_than=age_cutoff, limit=1)
            )
            break
        batch = repository.list_media_oldest_first(
            older_than=age_cutoff, limit=min(_BATCH, restante)
        )
        if not batch:
            break
        deleted = sum(_delete_asset(asset, stats) for asset in batch)
        if deleted == 0:
            break  # nada avanzo (p.ej. unlink fallando): reintentar recien en la proxima pasada

    max_bytes = int(max_total_gb * 1024**3)
    total = repository.total_media_size_bytes()
    while total > max_bytes and not stats["al_tope"]:
        batch = repository.list_media_oldest_first(older_than=now - MIN_AGE_S, limit=_BATCH)
        if not batch:
            # Lo que queda es evidencia protegida (o recien creada): no se
            # toca aunque pase el tope. El worker lo avisa.
            stats["protegida"] = True
            break
        progressed = False
        for asset in batch:
            if total <= max_bytes:
                break
            if stats["deleted"] >= tope:
                stats["al_tope"] = True
                break
            if _delete_asset(asset, stats):
                total -= asset.size_bytes
                progressed = True
        if not progressed:
            break

    return stats


def _delete_asset(asset: MediaAsset, stats: Stats) -> bool:
    """Borra archivo + fila. Si el unlink falla, conserva la fila para
    reintentar en la proxima pasada (el indice nunca miente sobre lo que
    hay en disco)."""
    path = media_store.absolute_path(asset.rel_path)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Retención: no se pudo borrar %s (se reintenta luego)", path)
        return False

    repository.delete_media_asset(asset.id)
    stats["deleted"] += 1
    stats["freed_bytes"] += asset.size_bytes
    _cleanup_empty_dirs(path)
    return True


def _cleanup_empty_dirs(path) -> None:
    """Sube desde el archivo borrado removiendo directorios vacios, sin
    pasar de la raiz de media."""
    media_root = media_store.settings.media_dir.resolve()
    current = path.parent
    while current.resolve() != media_root:
        try:
            current.rmdir()  # falla (y cortamos) si no esta vacio
        except OSError:
            break
        current = current.parent


class RetentionWorker(threading.Thread):
    def __init__(
        self, interval_s: float = INTERVAL_S, first_delay_s: float = FIRST_PASS_DELAY_S
    ) -> None:
        super().__init__(daemon=True, name="RetentionWorker")
        self._interval_s = interval_s
        self._first_delay_s = first_delay_s
        self._stop_event = threading.Event()
        # Estados ya avisados (ver el docstring del modulo).
        self._avisados: set[str] = set()

    def run(self) -> None:
        if self._stop_event.wait(self._first_delay_s):
            return
        while not self._stop_event.is_set():
            self.pasada()
            if self._stop_event.wait(self._interval_s):
                break

    def pasada(self) -> None:
        """Una vuelta del worker: lee la configuracion, poda y avisa."""
        try:
            dias, gb = app_prefs.leer_retencion()
        except app_prefs.RetencionSinConfigurar:
            self._avisar_una_vez(
                "sin_configurar",
                "Retención sin configurar: no se borra nada. Configurala en %s "
                "(días y tamaño máximo) para que la media vieja se pode.",
                app_prefs.DONDE_SE_CONFIGURA,
            )
            return
        except app_prefs.PrefsIlegibles as exc:
            # Podar con los defaults puede borrar evidencia que el
            # operador configuro conservar (ver core/app_prefs.py). Se
            # saltea la pasada y se reintenta en la proxima.
            logger.error("Retención suspendida: no se pudieron leer las preferencias (%s)", exc)
            return
        self._avisados.discard("sin_configurar")
        try:
            stats = prune(max_age_days=dias, max_total_gb=gb)
        except Exception:
            logger.exception("Falló la pasada de retención")
            return
        if stats.get("protegida"):
            self._avisar_una_vez(
                "protegida",
                "Retención: se pasó el tope de %.1f GB y lo que queda es evidencia de "
                "incidentes en investigación o media recién creada: no se borra.",
                gb,
            )
        else:
            self._avisados.discard("protegida")
        if stats.get("al_tope"):
            logger.error(
                "Retención: la pasada llegó al tope (%d archivos, %.1f MB) y queda más "
                "por borrar; sigue en la próxima pasada. Si no es lo esperado, revisá "
                "la retención en %s.",
                stats["deleted"],
                stats["freed_bytes"] / 1024**2,
                app_prefs.DONDE_SE_CONFIGURA,
            )
        elif stats["deleted"]:
            logger.info(
                "Retención: %d archivo(s) borrados, %.1f MB liberados",
                stats["deleted"],
                stats["freed_bytes"] / 1024**2,
            )

    def _avisar_una_vez(self, estado: str, mensaje: str, *args: object) -> None:
        if estado not in self._avisados:
            self._avisados.add(estado)
            logger.warning(mensaje, *args)

    def stop(self) -> None:
        self._stop_event.set()
