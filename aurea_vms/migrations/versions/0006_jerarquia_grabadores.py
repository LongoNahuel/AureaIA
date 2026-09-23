"""Grabadores con canales y asignacion directa de camara a sitio.

Dos columnas nuevas en `devices`, con su indice y su FK:

1. `parent_device_id` (FK a devices, CASCADE): un NVR/XVR es una fila padre
   y cada canal cuelga de ella. Borrar el grabador se lleva sus canales.
2. `assigned_site_id` (FK a sites, SET NULL): el sitio de una camara que
   todavia no tiene zona. En Python es `Device.site_id`; la columna lleva
   otro nombre porque `devices.site_id` fue la columna legada que se llevo
   0003, y reusar el nombre haria ambiguo cualquier backup anterior.

Y un paso de datos, de una sola vez: agrupar bajo un padre los canales
NVR/XVR que versiones anteriores guardaban como filas sueltas (misma
conexion = mismo grabador; el de menor id queda de padre).

Estas columnas entraron primero (commits 44ae65c/7487ef9) por el sistema
ad-hoc de ALTER TABLE en cada arranque, que habia reemplazado Alembic el
22/09. Esta revision las pasa a Alembic. Las bases que ya corrieron aquel
codigo tienen las columnas pero no los indices: por eso todo se chequea
antes (ver migrations/ayudas.py).

Revision ID: 0006_jerarquia_grabadores
Revises: 0005_seguridad
Create Date: 2026-09-23
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from aurea_vms.migrations.ayudas import columna_existe, indice_existe

revision: str = "0006_jerarquia_grabadores"
down_revision: str | None = "0005_seguridad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.0006_jerarquia_grabadores")

SEPARADOR_CANAL = " · Canal"


def _agrupar_canales_sueltos(conn) -> None:
    grupos = conn.execute(
        sa.text(
            "SELECT ip, port, username, onvif_port, device_type FROM devices "
            "WHERE parent_device_id IS NULL AND device_type IN ('nvr', 'xvr') "
            "GROUP BY ip, port, username, onvif_port, device_type "
            "HAVING COUNT(*) > 1"
        )
    ).fetchall()

    agrupados = 0
    for ip, port, username, onvif_port, device_type in grupos:
        filas = conn.execute(
            sa.text(
                "SELECT id, name FROM devices "
                "WHERE parent_device_id IS NULL AND ip = :ip AND port = :port "
                "AND username = :username AND device_type = :device_type "
                "AND (onvif_port = :onvif_port OR (onvif_port IS NULL AND :onvif_port IS NULL)) "
                "ORDER BY id"
            ),
            {
                "ip": ip,
                "port": port,
                "username": username,
                "device_type": device_type,
                "onvif_port": onvif_port,
            },
        ).fetchall()
        padre_id, nombre_padre = filas[0]
        conn.execute(
            sa.text("UPDATE devices SET name = :name, channel = 0 WHERE id = :id"),
            {"name": nombre_padre.split(SEPARADOR_CANAL, 1)[0].strip(), "id": padre_id},
        )
        for hijo_id, nombre_hijo in filas[1:]:
            conn.execute(
                sa.text(
                    "UPDATE devices SET parent_device_id = :padre, "
                    "name = CASE WHEN :ya_tiene_canal THEN name "
                    "ELSE name || :separador || ' ' || channel END "
                    "WHERE id = :id"
                ),
                {
                    "padre": padre_id,
                    "ya_tiene_canal": SEPARADOR_CANAL in nombre_hijo,
                    "separador": SEPARADOR_CANAL,
                    "id": hijo_id,
                },
            )
            agrupados += 1

    if agrupados:
        logger.info("Canales de grabador agrupados bajo su padre: %d", agrupados)


def upgrade() -> None:
    conn = op.get_bind()

    # Batch porque SQLite no sabe agregar una FK a una tabla existente: la
    # recrea. Las columnas se chequean: una base que corrio el codigo ad-hoc
    # ya las tiene (con una FK anonima de ALTER TABLE, que se deja como esta).
    with op.batch_alter_table("devices", schema=None) as batch_op:
        if not columna_existe(conn, "devices", "assigned_site_id"):
            batch_op.add_column(sa.Column("assigned_site_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_devices_assigned_site_id_sites",
                "sites",
                ["assigned_site_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if not columna_existe(conn, "devices", "parent_device_id"):
            batch_op.add_column(sa.Column("parent_device_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_devices_parent_device_id_devices",
                "devices",
                ["parent_device_id"],
                ["id"],
                ondelete="CASCADE",
            )

    for columna in ("assigned_site_id", "parent_device_id"):
        nombre = f"ix_devices_{columna}"
        if not indice_existe(conn, nombre):
            op.create_index(nombre, "devices", [columna])

    _agrupar_canales_sueltos(conn)


def downgrade() -> None:
    """Los canales vuelven a ser filas sueltas: se pierde a que grabador
    pertenecia cada uno, pero ninguna camara."""
    conn = op.get_bind()
    for columna in ("assigned_site_id", "parent_device_id"):
        nombre = f"ix_devices_{columna}"
        if indice_existe(conn, nombre):
            op.drop_index(nombre, table_name="devices")
    with op.batch_alter_table("devices", schema=None) as batch_op:
        if columna_existe(conn, "devices", "parent_device_id"):
            batch_op.drop_column("parent_device_id")
        if columna_existe(conn, "devices", "assigned_site_id"):
            batch_op.drop_column("assigned_site_id")
