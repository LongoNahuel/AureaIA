"""Chequeos de existencia para escribir revisiones defensivas.

Hacen falta por como se adoptan las bases anteriores a Alembic: la adopcion
crea las tablas que faltan con el metadata VIVO de los modelos, no con el de
la baseline. O sea que una tabla creada durante la adopcion nace ya con la
forma de la ultima revision -- y despues las revisiones intermedias intentan
aplicarle cambios que esa tabla ya tiene.

El caso concreto que lo destapo: una base vieja sin `alarm_events`. La
adopcion la crea con el modelo de hoy, que ya no lleva `index=True` en
`device_id`; despues 0004 intentaba borrar `ix_alarm_events_device_id` y
moria con "no such index".

Regla, entonces: **toda revision que borre o cree un indice chequea primero**.
Alterar columnas o agregar constraints no necesita esto (la adopcion no
altera nada existente, solo agrega columnas sueltas).
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
