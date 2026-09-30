"""Una sola analitica visible: Incidentes en casinos.

Solo datos, sin cambios de esquema. Desde el 30/09 la interfaz ofrece una
sola analitica (Incidentes en casinos: ruleta, BlackJack, monitor roto y
consumo). Conteo de Personas, Cruce de Linea y Deteccion Facial siguen
implementadas, pero no se muestran: si sus configuraciones quedaran
habilitadas, main._start_enabled_analytics las arrancaria igual y
consumirian CPU sin que nadie las vea ni las pueda apagar.

Aca se deshabilitan con la marca `oculta_0009` en los params. Las que ya
estaban apagadas no se marcan: si se marcaran, el downgrade las prenderia.
El downgrade rehabilita solo las marcadas y saca la marca.

Revision ID: 0009_una_sola_analitica
Revises: 0008_normaliza_adoptadas
Create Date: 2026-09-30
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_una_sola_analitica"
down_revision: str | None = "0008_normaliza_adoptadas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.0009_una_sola_analitica")

MARCA = "oculta_0009"
# Copia congelada: una revision no depende del codigo vivo (registry).
OCULTAS = ("people_counting", "line_crossing", "face_detection")

_ACTUALIZAR_CONFIG = sa.text(
    "UPDATE analytics_configs SET enabled = :enabled, params = :params WHERE id = :id"
).bindparams(sa.bindparam("params", type_=sa.JSON))


def _cargar(crudo) -> dict:
    if isinstance(crudo, dict):
        return dict(crudo)
    if not crudo:
        return {}
    try:
        valor = json.loads(crudo)
    except (TypeError, ValueError):
        return {}
    return valor if isinstance(valor, dict) else {}


def _filas(conn, solo_habilitadas: bool):
    condicion = " AND enabled = 1" if solo_habilitadas else ""
    return conn.execute(
        sa.text(
            "SELECT id, params FROM analytics_configs "
            f"WHERE analyzer_name IN ({', '.join(repr(n) for n in OCULTAS)}){condicion}"
        )
    ).fetchall()


def upgrade() -> None:
    conn = op.get_bind()
    apagadas = 0
    for fila_id, crudo in _filas(conn, solo_habilitadas=True):
        params = _cargar(crudo)
        params[MARCA] = True
        conn.execute(_ACTUALIZAR_CONFIG, {"enabled": False, "params": params, "id": fila_id})
        apagadas += 1
    if apagadas:
        logger.warning("Analíticas ocultas: %d configuración(es) deshabilitada(s)", apagadas)


def downgrade() -> None:
    conn = op.get_bind()
    for fila_id, crudo in _filas(conn, solo_habilitadas=False):
        params = _cargar(crudo)
        if not params.pop(MARCA, False):
            continue
        conn.execute(_ACTUALIZAR_CONFIG, {"enabled": True, "params": params, "id": fila_id})
