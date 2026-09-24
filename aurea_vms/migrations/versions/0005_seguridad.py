"""Credenciales de camara cifradas en reposo y lockout de login.

Tres cosas:

1. `devices.password` pasa de VARCHAR(120) a VARCHAR(500). Un token Fernet
   de una contraseña corta ya pasa los 100 caracteres.
2. Las contraseñas que ya estan guardadas se cifran. Es el unico paso que
   necesita el cifrador de la app: una migracion que importa codigo de
   `core` no es lo habitual, pero no hay otra forma de cifrar con LA clave
   de esta instalacion, y el alternativo -- dejarlas en claro hasta que
   alguien las vuelva a guardar -- es justamente lo que se esta arreglando.
3. `users` suma failed_attempts y locked_until para el lockout.

El hash de contraseña NO necesita migracion: el formato nuevo es
auto-descriptivo y `authenticate` valida los dos, re-calculando el viejo al
primer login exitoso de cada usuario (ver core/auth.py).

Revision ID: 0005_seguridad
Revises: 0004_constraints
Create Date: 2026-09-22
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from aurea_vms.migrations.ayudas import columna_existe

revision: str = "0005_seguridad"
down_revision: str | None = "0004_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.0005_seguridad")


def _cifrar_credenciales_existentes(conn) -> None:
    from aurea_vms.core import credential_store

    filas = conn.execute(
        sa.text("SELECT id, password FROM devices WHERE password IS NOT NULL AND password <> ''")
    ).fetchall()

    cifradas = 0
    for device_id, password in filas:
        if credential_store.esta_cifrado(password):
            continue  # ya cifrada: la migracion es reentrante
        conn.execute(
            sa.text("UPDATE devices SET password = :cifrada WHERE id = :device_id"),
            {"cifrada": credential_store.cifrar(password), "device_id": device_id},
        )
        cifradas += 1

    if cifradas:
        logger.info("Credenciales de cámara cifradas en reposo: %d", cifradas)


def upgrade() -> None:
    # El ancho primero: si no, las contraseñas cifradas no entran.
    # sa.String y no el TypeDecorator de la app a proposito: a nivel de base
    # esto es un VARCHAR, y una revision no deberia quedar atada a un tipo
    # de Python que mañana puede cambiar de nombre o desaparecer.
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.alter_column(
            "password",
            existing_type=sa.VARCHAR(length=120),
            type_=sa.String(length=500),
            existing_nullable=False,
        )

    _cifrar_credenciales_existentes(op.get_bind())

    # server_default para poder agregarlas NOT NULL sobre una tabla con
    # usuarios adentro, y se saca despues para que el esquema coincida
    # exactamente con el modelo (que no declara default de servidor).
    # Chequeadas: una base adoptada pudo crear `users` con el metadata de
    # hoy, que ya las declara (ver migrations/ayudas.py).
    conn = op.get_bind()
    with op.batch_alter_table("users", schema=None) as batch_op:
        if not columna_existe(conn, "users", "failed_attempts"):
            batch_op.add_column(
                sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0")
            )
        if not columna_existe(conn, "users", "locked_until"):
            batch_op.add_column(sa.Column("locked_until", sa.Float(), nullable=True))
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("failed_attempts", server_default=None)


def downgrade() -> None:
    """Descifra de vuelta antes de achicar la columna: si no, las
    contraseñas quedan como tokens ilegibles en un VARCHAR(120) que ni
    siquiera los contiene."""
    from aurea_vms.core import credential_store

    conn = op.get_bind()
    for device_id, password in conn.execute(
        sa.text("SELECT id, password FROM devices WHERE password IS NOT NULL AND password <> ''")
    ).fetchall():
        if credential_store.esta_cifrado(password):
            plana = credential_store.descifrar(password)
            if credential_store.es_ilegible(plana):
                # Antes se escribia lo que devolviera descifrar (el vacio):
                # el downgrade borraba la contraseña para siempre. Sin la
                # clave no hay vuelta atras posible; que lo diga, y la
                # transaccion de la migracion deja la base como estaba.
                raise RuntimeError(
                    f"No se puede bajar de 0005: la contraseña de la cámara {device_id} "
                    "no se puede descifrar con la clave actual. Restaurá camera_key antes."
                )
            conn.execute(
                sa.text("UPDATE devices SET password = :plana WHERE id = :device_id"),
                {"plana": plana, "device_id": device_id},
            )

    with op.batch_alter_table("users", schema=None) as batch_op:
        if columna_existe(conn, "users", "locked_until"):
            batch_op.drop_column("locked_until")
        if columna_existe(conn, "users", "failed_attempts"):
            batch_op.drop_column("failed_attempts")

    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.alter_column(
            "password",
            existing_type=sa.String(length=500),
            type_=sa.VARCHAR(length=120),
            existing_nullable=False,
        )
