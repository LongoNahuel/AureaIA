"""Disposicion de ventanas por usuario (pestañas en varios monitores).

Tabla nueva `ui_layouts`: una fila por usuario con el JSON de sus ventanas,
pestañas y camaras por recuadro (ver models/ui_layout.py y
ui/layout_store.py). Borrar el usuario borra su fila (FK con CASCADE).

Defensiva, como pide migrations/ayudas.py: una base vieja que se adopta ya
recibe esta tabla del metadata vivo de los modelos, y crearla de nuevo
moriria con "table already exists". Sin PRAGMA ni nada propio de SQLite:
corre igual en PostgreSQL.

El downgrade la borra: se pierden las disposiciones guardadas, que la app
vuelve a armar en el proximo cierre.

Revision ID: 0009_ui_layouts
Revises: 0008_normaliza_adoptadas
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_ui_layouts"
down_revision: str | None = "0008_normaliza_adoptadas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLA = "ui_layouts"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLA):
        return
    op.create_table(
        TABLA,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_ui_layouts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ui_layouts")),
        sa.UniqueConstraint("user_id", name=op.f("uq_ui_layouts_user_id")),
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLA):
        op.drop_table(TABLA)
