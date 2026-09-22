"""Adopcion de bases anteriores a Alembic.

Puente de una sola vez, por base. Hasta el 2026-09-22 el esquema se mantenia
con `create_all` (que crea tablas nuevas pero NO altera las existentes) mas
este dict de columnas, aplicado con ALTER TABLE ADD COLUMN en CADA arranque.
Una base de campo podia estar en cualquier punto de esa historia y no habia
forma de saber en cual salvo inspeccionando columnas.

`init_db` usa esto para llevar una base sin versionar a la forma de
`0001_baseline` y recien ahi marcarla en esa revision. Despues, Alembic.

NO AGREGAR NADA ACA. Toda columna nueva va en una revision. Este archivo es
historia congelada: describe como llegar al 22/09/2026 desde cualquier base
anterior, y cuando ya no queden bases de esa epoca se borra entero.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# (columna, DDL sin "ADD COLUMN") por tabla, en orden, solo si falta.
COLUMNAS_ADHOC: dict[str, list[tuple[str, str]]] = {
    "devices": [
        # El ON DELETE SET NULL replica el ondelete del modelo (Device.zone_id):
        # sin el, con PRAGMA foreign_keys=ON, borrar una zona con camaras
        # asignadas falla con IntegrityError en las bases migradas.
        ("zone_id", "INTEGER REFERENCES zones(id) ON DELETE SET NULL"),
        ("device_type", "VARCHAR(10) NOT NULL DEFAULT 'ipc'"),
        ("channel", "INTEGER NOT NULL DEFAULT 1"),
        ("manufacturer", "VARCHAR(80)"),
        ("model", "VARCHAR(120)"),
        ("firmware_version", "VARCHAR(120)"),
        ("serial_number", "VARCHAR(120)"),
    ],
    "sites": [
        ("description", "VARCHAR(300) NOT NULL DEFAULT ''"),
    ],
    "alarm_events": [
        ("severity", "VARCHAR(20) NOT NULL DEFAULT 'medio'"),
        ("status", "VARCHAR(20) NOT NULL DEFAULT 'nueva'"),
        ("notes", "VARCHAR(4000) NOT NULL DEFAULT ''"),
    ],
    "alarm_rules": [
        ("severity", "VARCHAR(20) NOT NULL DEFAULT 'medio'"),
        ("schedule_days", "JSON NOT NULL DEFAULT '[]'"),
        ("schedule_start", "VARCHAR(5)"),
        ("schedule_end", "VARCHAR(5)"),
    ],
}


def adoptar(engine, metadata) -> list[str]:
    """Lleva una base sin versionar a la forma de 0001_baseline.

    Crea las tablas que falten (una base pre-zonas no tiene `zones`), agrega
    las columnas ad-hoc ausentes y crea los indices que falten. Devuelve lo
    que toco, para el log.
    """
    tocado: list[str] = []
    metadata.create_all(engine)  # tablas que falten; no altera las que ya estan

    with engine.connect() as conn:
        for tabla, columnas in COLUMNAS_ADHOC.items():
            existentes = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({tabla})")}
            if not existentes:
                continue  # la tabla la acaba de crear create_all, ya viene completa
            for nombre, ddl in columnas:
                if nombre not in existentes:
                    conn.exec_driver_sql(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {ddl}")
                    tocado.append(f"{tabla}.{nombre}")
        conn.commit()

    # Los indices que el sistema ad-hoc nunca creo. ALTER TABLE ADD COLUMN
    # agrega la columna y nada mas: la base de desarrollo venia corriendo
    # sin ix_devices_zone_id desde que existen las zonas, o sea que filtrar
    # camaras por zona hacia scan completo.
    with engine.begin() as conn:
        existentes = {
            row[0]
            for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index'")
        }
        for tabla in metadata.tables.values():
            for indice in tabla.indexes:
                if indice.name not in existentes:
                    indice.create(bind=conn)
                    tocado.append(f"índice {indice.name}")

    if tocado:
        logger.info("Adopción de la base existente: %s", ", ".join(tocado))
    return tocado
