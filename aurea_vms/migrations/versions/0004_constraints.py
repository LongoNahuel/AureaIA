"""Constraints e indices que el esquema no tenia, con saneo previo de datos.

Cuatro cosas, y las tres primeras necesitan limpiar antes de restringir --
que es exactamente lo que el autogenerate no sabe hacer:

1. alarm_rules deja de tener columnas nullables. Venia de la API legacy
   `Column`, donde el default es NULL: una regla sin analizador o sin
   severidad era valida para la base aunque el codigo la de por sentada.
2. UNIQUE(device_id, analyzer_name) en analytics_configs, que el repositorio
   ya asumia con .one_or_none().
3. UNIQUE(site_id, name) en zones.
4. Indice en alarm_events.status (lo filtra el dashboard cada 5s) y se va el
   de device_id, redundante con el compuesto ix_alarm_events_device_ts.

La base de desarrollo no tiene ni NULLs ni duplicados (verificado el
2026-09-22), pero una de campo puede: si el saneo no corriera, la migracion
fallaria a mitad y dejaria la base en un estado raro.

Corregida en el lugar el 2026-09-24 (no hay bases de cliente migradas): las
reglas saneadas quedan deshabilitadas en vez de activarse.

Revision ID: 0004_constraints
Revises: 0003_drop_site_id
Create Date: 2026-09-22
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from aurea_vms.migrations.ayudas import indice_existe

revision: str = "0004_constraints"
down_revision: str | None = "0003_drop_site_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


logger = logging.getLogger("alembic.0004_constraints")

# Defaults del modelo, para las filas que quedaron con NULL antes de que las
# columnas fueran NOT NULL. Salvo `enabled`: el default del modelo es 1, pero
# una regla que llega aca con el analizador o el propio `enabled` en NULL es
# una regla que nadie termino de configurar -- rellenarla con el default la
# ACTIVABA (con analizador inventado) sin que nadie lo pidiera. Se apaga, y
# queda visible en la UI para que alguien la revise.
DEFAULTS_DE_ALARM_RULES = {
    "analyzer_name": "'door_state'",
    "object_classes": "'[]'",
    "min_confidence": "0.5",
    "cooldown_seconds": "30",
    "severity": "'medio'",
    "schedule_days": "'[]'",
    "actions": "'{}'",
    "enabled": "0",
}


def _rellenar_nulls_de_alarm_rules(conn) -> None:
    # Antes de rellenar analyzer_name: despues ya no se sabe cual venia NULL.
    apagadas = conn.execute(
        sa.text("UPDATE alarm_rules SET enabled = 0 WHERE analyzer_name IS NULL")
    )
    if apagadas.rowcount:
        logger.info(
            "alarm_rules: %d reglas sin analizador quedan deshabilitadas", apagadas.rowcount
        )
    for columna, valor in DEFAULTS_DE_ALARM_RULES.items():
        resultado = conn.execute(
            sa.text(
                f"UPDATE alarm_rules SET {columna} = {valor} "  # noqa: S608 - valores fijos
                f"WHERE {columna} IS NULL"
            )
        )
        if resultado.rowcount:
            logger.info(
                "alarm_rules.%s: %d filas en NULL rellenadas con el default",
                columna,
                resultado.rowcount,
            )


def _deduplicar_analytics_configs(conn) -> None:
    """Se queda con la de mayor id: es la ultima que escribio el usuario, que
    es lo que habria devuelto el upsert."""
    borradas = conn.execute(
        sa.text(
            "DELETE FROM analytics_configs WHERE id NOT IN ("
            "  SELECT MAX(id) FROM analytics_configs GROUP BY device_id, analyzer_name"
            ")"
        )
    )
    if borradas.rowcount:
        logger.warning(
            "analytics_configs: %d configuraciones duplicadas descartadas "
            "(se conservo la mas reciente de cada cámara/analizador)",
            borradas.rowcount,
        )


def _deduplicar_zonas(conn) -> None:
    """Se queda con la de MENOR id -- la original -- y reapunta a ella las
    camaras de las duplicadas ANTES de borrarlas.

    El orden importa: Device.zone_id es ON DELETE SET NULL, asi que borrar
    primero dejaria esas camaras "Sin zona" e invisibles para el filtro
    global de sitio. Perder la zona es aceptable; perder la asignacion de una
    camara, no.
    """
    duplicadas = conn.execute(
        sa.text(
            "SELECT z.id, ("
            "  SELECT MIN(o.id) FROM zones o WHERE o.site_id = z.site_id AND o.name = z.name"
            ") AS conservada FROM zones z"
        )
    ).fetchall()

    for zona_id, conservada in duplicadas:
        if zona_id == conservada:
            continue
        conn.execute(
            sa.text("UPDATE devices SET zone_id = :conservada WHERE zone_id = :zona_id"),
            {"conservada": conservada, "zona_id": zona_id},
        )
        conn.execute(sa.text("DELETE FROM zones WHERE id = :zona_id"), {"zona_id": zona_id})
        logger.warning(
            "zones: zona duplicada %s fusionada con %s (sus cámaras se reapuntaron)",
            zona_id,
            conservada,
        )


def upgrade() -> None:
    conn = op.get_bind()
    # Sanear ANTES de restringir: con una sola fila en NULL o un solo
    # duplicado, los ALTER de abajo fallan y la base queda a medio migrar.
    _rellenar_nulls_de_alarm_rules(conn)
    _deduplicar_analytics_configs(conn)
    _deduplicar_zonas(conn)

    with op.batch_alter_table("alarm_events", schema=None) as batch_op:
        # Chequeado: una base adoptada pudo crear esta tabla con el metadata
        # de hoy, que ya no lleva index=True en device_id (ver
        # migrations/ayudas.py).
        if indice_existe(conn, "ix_alarm_events_device_id"):
            batch_op.drop_index(batch_op.f("ix_alarm_events_device_id"))
        if not indice_existe(conn, "ix_alarm_events_status"):
            batch_op.create_index(batch_op.f("ix_alarm_events_status"), ["status"], unique=False)

    with op.batch_alter_table("alarm_rules", schema=None) as batch_op:
        batch_op.alter_column("analyzer_name", existing_type=sa.VARCHAR(length=60), nullable=False)
        batch_op.alter_column("object_classes", existing_type=sa.JSON(), nullable=False)
        batch_op.alter_column("min_confidence", existing_type=sa.FLOAT(), nullable=False)
        batch_op.alter_column("cooldown_seconds", existing_type=sa.INTEGER(), nullable=False)
        batch_op.alter_column("severity", existing_type=sa.VARCHAR(length=20), nullable=False)
        batch_op.alter_column("schedule_days", existing_type=sa.JSON(), nullable=False)
        batch_op.alter_column("actions", existing_type=sa.JSON(), nullable=False)
        batch_op.alter_column("enabled", existing_type=sa.BOOLEAN(), nullable=False)

    with op.batch_alter_table("analytics_configs", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            batch_op.f("uq_analytics_configs_device_id_analyzer_name"),
            ["device_id", "analyzer_name"],
        )

    with op.batch_alter_table("zones", schema=None) as batch_op:
        batch_op.create_unique_constraint(batch_op.f("uq_zones_site_id_name"), ["site_id", "name"])


def downgrade() -> None:
    with op.batch_alter_table("zones", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_zones_site_id_name"), type_="unique")

    with op.batch_alter_table("analytics_configs", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("uq_analytics_configs_device_id_analyzer_name"), type_="unique"
        )

    with op.batch_alter_table("alarm_rules", schema=None) as batch_op:
        batch_op.alter_column("enabled", existing_type=sa.BOOLEAN(), nullable=True)
        batch_op.alter_column("actions", existing_type=sa.JSON(), nullable=True)
        batch_op.alter_column("schedule_days", existing_type=sa.JSON(), nullable=True)
        batch_op.alter_column("severity", existing_type=sa.VARCHAR(length=20), nullable=True)
        batch_op.alter_column("cooldown_seconds", existing_type=sa.INTEGER(), nullable=True)
        batch_op.alter_column("min_confidence", existing_type=sa.FLOAT(), nullable=True)
        batch_op.alter_column("object_classes", existing_type=sa.JSON(), nullable=True)
        batch_op.alter_column("analyzer_name", existing_type=sa.VARCHAR(length=60), nullable=True)

    conn = op.get_bind()
    with op.batch_alter_table("alarm_events", schema=None) as batch_op:
        if indice_existe(conn, "ix_alarm_events_status"):
            batch_op.drop_index(batch_op.f("ix_alarm_events_status"))
        if not indice_existe(conn, "ix_alarm_events_device_id"):
            batch_op.create_index(
                batch_op.f("ix_alarm_events_device_id"), ["device_id"], unique=False
            )
