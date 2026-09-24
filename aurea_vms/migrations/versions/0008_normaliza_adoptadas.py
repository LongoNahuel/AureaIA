"""Normaliza las bases adoptadas y apaga los monitores que no pueden arrancar.

Dos arreglos, uno de datos y uno de esquema:

1. **Monitor roto sin zonas.** 0007 paso `door_state` -> `monitor_tamper`
   conservando zonas y fps. Pero la analitica de puerta aceptaba el cuadro
   completo (sin zonas ni ROI), y `MonitorTamperAnalyzer` no: exige al
   menos una pantalla y levanta ValueError. Esas configs quedaban
   habilitadas, fallaban en cada arranque (main._start_enabled_analytics
   solo lo loguea) y la UI las mostraba activas. La base de desarrollo de
   Daniel tenia una. Aca se deshabilitan con la marca `deshabilitada_0008`
   en los params, y el downgrade rehabilita solo las marcadas. Criterio,
   el mismo que `registry.create_analyzer`: sin ninguna zona de 4 valores y
   sin ROI completo.

2. **Deriva de las bases adoptadas.** Una base anterior a Alembic trae
   constraints SIN nombre (las creaba `create_all` sin naming convention):
   `devices.zone_id` sin ON DELETE SET NULL, y los UNIQUE de `sites.name`,
   `users.username` y `media_assets.rel_path` anonimos. Las mas viejas
   (anteriores a la baseline) ni siquiera tienen la FK
   `analytics_configs.device_id`. La unicidad ya se cumplia; lo que no se
   cumplia es que la base fuera la de los modelos (`compare_metadata` la
   marcaba). Las FKs si cambian comportamiento: borrar una zona con camaras
   fallaba en vez de dejarlas "Sin zona", y borrar una camara dejaba sus
   configs huerfanas. Las filas que ya estaban huerfanas se resuelven como
   lo habria hecho la FK (NULL o borradas), y se loguea cuantas. Todo es
   condicional: en una base nueva esta parte no hace nada.

   Batch con `naming_convention`: asi el batch le pone nombre a las
   constraints anonimas que refleja y se las puede reemplazar. Requiere
   `PRAGMA foreign_keys=OFF`: con FKs activas, el DROP de la tabla vieja que
   hace el batch borra en cascada los hijos. Hoy el engine de migracion de
   env.py no aplica el listener de pragmas (queda OFF, el default de
   SQLite), y esta revision lo verifica antes de tocar nada.

   El downgrade de esquema no vuelve a la deriva: no aporta nada y no hay
   datos que recuperar.

Revision ID: 0008_normaliza_adoptadas
Revises: 0007_monitor_roto
Create Date: 2026-09-24
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_normaliza_adoptadas"
down_revision: str | None = "0007_monitor_roto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.0008_normaliza_adoptadas")

MARCA = "deshabilitada_0008"
# Copia congelada de models/db.py::NAMING_CONVENTION: una revision no
# depende del codigo vivo.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
# (tabla, columna, tabla referida, ON DELETE) tal como los declaran los modelos.
FKS = (
    ("devices", "zone_id", "zones", "SET NULL"),
    ("analytics_configs", "device_id", "devices", "CASCADE"),
)
UNICOS = (("sites", "name"), ("users", "username"), ("media_assets", "rel_path"))

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


def _tiene_pantalla(params: dict, roi: tuple) -> bool:
    zonas = params.get("zones") or []
    if isinstance(zonas, list) and any(
        isinstance(zona, (list, tuple)) and len(zona) == 4 for zona in zonas
    ):
        return True
    return None not in roi


def _apagar_monitores_sin_pantalla(conn) -> None:
    filas = conn.execute(
        sa.text(
            "SELECT id, params, roi_x, roi_y, roi_w, roi_h FROM analytics_configs "
            "WHERE analyzer_name = 'monitor_tamper' AND enabled = 1"
        )
    ).fetchall()
    apagadas = 0
    for fila_id, crudo, *roi in filas:
        params = _cargar(crudo)
        if _tiene_pantalla(params, tuple(roi)):
            continue
        params[MARCA] = True
        conn.execute(_ACTUALIZAR_CONFIG, {"enabled": False, "params": params, "id": fila_id})
        apagadas += 1
    if apagadas:
        logger.warning(
            "Detección de incidentes sin zonas: %d configuración(es) deshabilitada(s)", apagadas
        )


def _prender_monitores_marcados(conn) -> None:
    filas = conn.execute(
        sa.text("SELECT id, params FROM analytics_configs WHERE analyzer_name = 'monitor_tamper'")
    ).fetchall()
    for fila_id, crudo in filas:
        params = _cargar(crudo)
        if not params.pop(MARCA, False):
            continue
        conn.execute(_ACTUALIZAR_CONFIG, {"enabled": True, "params": params, "id": fila_id})


def _exigir_fks_apagadas(conn) -> None:
    if conn.dialect.name != "sqlite":
        return
    if conn.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError(
            "0008 recrea tablas con batch y necesita PRAGMA foreign_keys=OFF: "
            "con FKs activas, el DROP de la tabla vieja borra en cascada los hijos"
        )


def _normalizar_fk(conn, tabla: str, columna: str, referida: str, ondelete: str) -> None:
    nombre = f"fk_{tabla}_{columna}_{referida}"
    fks = [
        fk
        for fk in sa.inspect(conn).get_foreign_keys(tabla)
        if fk["constrained_columns"] == [columna]
    ]
    if any(
        fk["name"] == nombre and (fk["options"].get("ondelete") or "").upper() == ondelete
        for fk in fks
    ):
        return
    # Huerfanas: con FKs apagadas nadie las impidio. Se resuelven con la
    # semantica de la FK que se crea: lo que ella habria hecho al borrar el
    # padre. Sin esto, la FK nace violada y `foreign_key_check` la marca.
    huerfanas = f"{columna} IS NOT NULL AND {columna} NOT IN (SELECT id FROM {referida})"
    if ondelete == "CASCADE":
        resultado = conn.execute(sa.text(f"DELETE FROM {tabla} WHERE {huerfanas}"))
    else:
        resultado = conn.execute(sa.text(f"UPDATE {tabla} SET {columna} = NULL WHERE {huerfanas}"))
    if resultado.rowcount:
        logger.warning(
            "%s.%s: %d fila(s) apuntaban a %s inexistentes (%s)",
            tabla,
            columna,
            resultado.rowcount,
            referida,
            "borradas" if ondelete == "CASCADE" else "puestas en NULL",
        )
    with op.batch_alter_table(tabla, naming_convention=NAMING_CONVENTION) as batch_op:
        # Reflejada con la convencion, una FK anonima ya se llama `nombre`.
        for fk in fks:
            batch_op.drop_constraint(fk["name"] or nombre, type_="foreignkey")
        batch_op.create_foreign_key(nombre, referida, [columna], ["id"], ondelete=ondelete)
    logger.info("%s.%s: FK normalizada con ON DELETE %s", tabla, columna, ondelete)


def _normalizar_unico(conn, tabla: str, columna: str) -> None:
    nombre = f"uq_{tabla}_{columna}"
    unicos = [
        uq
        for uq in sa.inspect(conn).get_unique_constraints(tabla)
        if uq["column_names"] == [columna]
    ]
    if any(uq["name"] == nombre for uq in unicos):
        return
    if unicos:
        # Anonimo: recrear la tabla con la convencion le pone el nombre.
        with op.batch_alter_table(tabla, naming_convention=NAMING_CONVENTION, recreate="always"):
            pass
    else:
        repetidos = conn.execute(
            sa.text(f"SELECT {columna} FROM {tabla} GROUP BY {columna} HAVING COUNT(*) > 1 LIMIT 5")
        ).fetchall()
        if repetidos:
            raise RuntimeError(
                f"0008 no puede crear {nombre}: hay valores repetidos en {tabla}.{columna} "
                f"({', '.join(repr(r[0]) for r in repetidos)}). Hay que resolverlos a mano."
            )
        with op.batch_alter_table(tabla, naming_convention=NAMING_CONVENTION) as batch_op:
            batch_op.create_unique_constraint(nombre, [columna])
    logger.info("%s.%s: UNIQUE normalizado como %s", tabla, columna, nombre)


def upgrade() -> None:
    conn = op.get_bind()
    _exigir_fks_apagadas(conn)
    _apagar_monitores_sin_pantalla(conn)
    for tabla, columna, referida, ondelete in FKS:
        _normalizar_fk(conn, tabla, columna, referida, ondelete)
    for tabla, columna in UNICOS:
        _normalizar_unico(conn, tabla, columna)


def downgrade() -> None:
    """Rehabilita los monitores que apago el upgrade. El esquema queda
    normalizado (ver el docstring del modulo)."""
    _prender_monitores_marcados(op.get_bind())
