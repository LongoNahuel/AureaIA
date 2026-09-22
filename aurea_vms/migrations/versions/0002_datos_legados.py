"""Migraciones de DATOS que venian corriendo en cada arranque.

Las tres cosas de aca vivian en `_apply_adhoc_migrations()` y se ejecutaban
en CADA `init_db()`, para siempre: dos UPDATE que renombran el analizador
`motion_detection` a `door_state` y el backfill de zonas para las bases
anteriores a la jerarquia Sitio->Zona->Camara. Son de una sola vez por base,
y esta revision es lo que hace que efectivamente lo sean.

Revision ID: 0002_datos_legados
Revises: 0001_baseline
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_datos_legados"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ZONA_POR_DEFECTO = "General"


def _backfill_de_zonas(conn) -> None:
    """Las bases anteriores a la jerarquia Sitio->Zona->Camara asignaban las
    camaras directo al sitio (devices.site_id, columna que el modelo actual
    ya no declara pero que esas bases conservan). Sin backfill, toda camara
    asignada aparecia "Sin zona" en silencio tras migrar, e invisible para
    el filtro global de sitio.

    Se crea (o reusa) una zona "General" por sitio, se copia la asignacion y
    se CONSUME el site_id legado -- consumirlo hace el paso de una sola vez:
    desasignar una camara despues no la vuelve a asignar.
    """
    columnas = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(devices)")}
    if "site_id" not in columnas:
        return  # base nueva: nunca tuvo la columna legada

    sitios_legados = conn.execute(
        sa.text(
            "SELECT DISTINCT site_id FROM devices WHERE site_id IS NOT NULL AND zone_id IS NULL"
        )
    ).fetchall()

    for (site_id,) in sitios_legados:
        # El site_id legado no tenia FK confiable: si el sitio ya no existe,
        # la camara queda "Sin zona" (no hay donde colgarla).
        existe = conn.execute(
            sa.text("SELECT 1 FROM sites WHERE id = :site_id"), {"site_id": site_id}
        ).fetchone()
        if existe is None:
            continue

        params = {"site_id": site_id, "nombre": ZONA_POR_DEFECTO}
        zona = conn.execute(
            sa.text("SELECT id FROM zones WHERE site_id = :site_id AND name = :nombre"), params
        ).fetchone()
        if zona is None:
            conn.execute(
                sa.text(
                    "INSERT INTO zones (site_id, name, critical) VALUES (:site_id, :nombre, 0)"
                ),
                params,
            )
            zona = conn.execute(
                sa.text("SELECT id FROM zones WHERE site_id = :site_id AND name = :nombre"),
                params,
            ).fetchone()

        conn.execute(
            sa.text(
                "UPDATE devices SET zone_id = :zone_id WHERE site_id = :site_id AND zone_id IS NULL"
            ),
            {"zone_id": zona[0], "site_id": site_id},
        )

    conn.execute(sa.text("UPDATE devices SET site_id = NULL WHERE site_id IS NOT NULL"))


def upgrade() -> None:
    conn = op.get_bind()

    # El analizador de movimiento paso a ser "estado de puerta" (commit
    # 7f6d5ff). El registry todavia construye motion_detection por
    # compatibilidad, pero las configuraciones y reglas guardadas se
    # renombran para que la UI las muestre donde corresponde.
    for tabla in ("analytics_configs", "alarm_rules"):
        conn.execute(
            sa.text(
                f"UPDATE {tabla} SET analyzer_name = 'door_state' "  # noqa: S608 - tabla fija
                "WHERE analyzer_name = 'motion_detection'"
            )
        )

    _backfill_de_zonas(conn)


def downgrade() -> None:
    """Sin vuelta atras: es una migracion de datos que consume el origen.

    Revertirla significaria adivinar que camaras estaban en que sitio antes
    del backfill, y que configuraciones eran de movimiento y cuales de
    puerta. El camino de vuelta es restaurar un backup.
    """
    raise NotImplementedError(
        "0002_datos_legados no se puede revertir: restaurar un backup de la base"
    )
