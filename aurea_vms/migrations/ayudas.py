"""Chequeos de existencia para escribir revisiones defensivas.

Hacen falta por como se adoptan las bases anteriores a Alembic: la adopcion
crea las tablas que faltan con el metadata VIVO de los modelos, no con el de
la baseline. O sea que una tabla creada durante la adopcion nace ya con la
forma de la ultima revision -- y despues las revisiones intermedias intentan
aplicarle cambios que esa tabla ya tiene.

Dos casos concretos, uno por revision:

- 0004: una base vieja sin `alarm_events`. La adopcion la crea con el modelo
  de hoy, que ya no lleva `index=True` en `device_id`; despues la revision
  intentaba borrar `ix_alarm_events_device_id` y moria con "no such index".
- 0005: una base vieja sin `users`. La adopcion la crea con las columnas de
  lockout ya puestas, y la revision moria con "duplicate column name:
  failed_attempts".

Regla, entonces: **toda revision que cree o borre un indice, o agregue una
columna, chequea primero**. Alterar una columna existente o agregar una
constraint no lo necesita: son idempotentes o fallan ruidosamente, no en
silencio.

Los dos chequeos usan el inspector de SQLAlchemy y no `PRAGMA` ni
`sqlite_master`, asi que corren igual en SQLite y en PostgreSQL. Cada
llamada arma un inspector nuevo: el inspector cachea lo que refleja, y una
revision consulta despues de haber cambiado el esquema.
"""

from __future__ import annotations

import sqlalchemy as sa


def indice_existe(conn, nombre: str) -> bool:
    """Busca por nombre en todas las tablas, como el `sqlite_master` de antes:
    los nombres de indice son unicos en la base (y en el schema, en Postgres)."""
    inspector = sa.inspect(conn)
    return any(
        indice["name"] == nombre
        for tabla in inspector.get_table_names()
        for indice in inspector.get_indexes(tabla)
    )


def columna_existe(conn, tabla: str, columna: str) -> bool:
    """False si la tabla no existe, igual que el `PRAGMA table_info` vacio de antes."""
    inspector = sa.inspect(conn)
    if not inspector.has_table(tabla):
        return False
    return columna in {c["name"] for c in inspector.get_columns(tabla)}
