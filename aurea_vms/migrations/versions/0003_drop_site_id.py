"""Saca devices.site_id, la columna legada que el modelo ya no declara.

La jerarquia real es Sitio -> Zona -> Camara desde el commit 3977978: una
camara cuelga de una zona y la zona de un sitio. `devices.site_id` sobrevivia
en las bases viejas sin que ningun modelo la declarara, o sea invisible para
el ORM y para el autogenerate -- que en cada corrida futura querria borrarla.

Va DESPUES de 0002 a proposito: el backfill de zonas la lee para saber a que
sitio pertenecia cada camara.

Revision ID: 0003_drop_site_id
Revises: 0002_datos_legados
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_drop_site_id"
down_revision: str | None = "0002_datos_legados"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    columnas = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(devices)")}
    if "site_id" not in columnas:
        return  # base creada por 0001: nunca tuvo la columna

    # Las bases viejas traen ix_devices_site_id. batch_alter_table refleja la
    # tabla entera y recrea sus indices sobre la copia nueva, asi que un
    # indice sobre la columna que estamos sacando la hace fallar con
    # "no such column: site_id". Se van primero.
    for fila in conn.exec_driver_sql("PRAGMA index_list(devices)").fetchall():
        nombre = fila[1]
        if nombre.startswith("sqlite_autoindex"):
            continue
        columnas_del_indice = {r[2] for r in conn.exec_driver_sql(f"PRAGMA index_info({nombre})")}
        if "site_id" in columnas_del_indice:
            op.drop_index(nombre, table_name="devices")

    # SQLite no sabe DROP COLUMN con constraints: batch_alter_table recrea
    # la tabla entera, y por eso hizo falta la naming_convention de
    # models/db.py (sin nombres, las constraints anonimas no se pueden
    # referenciar al recrear).
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_column("site_id")


def downgrade() -> None:
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.add_column(sa.Column("site_id", sa.Integer(), nullable=True))
