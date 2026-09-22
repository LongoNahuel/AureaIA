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
"""

from __future__ import annotations


def indice_existe(conn, nombre: str) -> bool:
    return (
        conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (nombre,)
        ).fetchone()
        is not None
    )


def columna_existe(conn, tabla: str, columna: str) -> bool:
    return columna in {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({tabla})")}
