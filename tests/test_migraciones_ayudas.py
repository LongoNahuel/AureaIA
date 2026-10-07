"""Chequeos de existencia de `migrations/ayudas.py`.

Desde el 07/10 usan el inspector de SQLAlchemy en vez de `PRAGMA` /
`sqlite_master`, para que las revisiones corran en PostgreSQL. Estos tests
fijan el contrato que tenian con la version SQLite-only: buscar un indice
por nombre en toda la base, y una tabla inexistente da False en vez de
levantar.
"""

from __future__ import annotations

import sqlalchemy as sa

from aurea_vms.migrations.ayudas import columna_existe, indice_existe


def _engine():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE camaras (id INTEGER PRIMARY KEY, zona INTEGER)")
        conn.exec_driver_sql("CREATE TABLE zonas (id INTEGER PRIMARY KEY, nombre VARCHAR(20))")
        conn.exec_driver_sql("CREATE INDEX ix_zonas_nombre ON zonas (nombre)")
    return engine


class TestIndiceExiste:
    def test_lo_encuentra_por_nombre_en_cualquier_tabla(self):
        with _engine().connect() as conn:
            assert indice_existe(conn, "ix_zonas_nombre")

    def test_un_nombre_que_no_existe_da_false(self):
        with _engine().connect() as conn:
            assert not indice_existe(conn, "ix_camaras_zona")

    def test_ve_un_indice_creado_en_la_misma_conexion(self):
        # Una revision crea y despues pregunta: el chequeo no puede usar un
        # inspector cacheado de antes del DDL.
        with _engine().connect() as conn:
            assert not indice_existe(conn, "ix_camaras_zona")
            conn.exec_driver_sql("CREATE INDEX ix_camaras_zona ON camaras (zona)")
            assert indice_existe(conn, "ix_camaras_zona")


class TestColumnaExiste:
    def test_columna_presente(self):
        with _engine().connect() as conn:
            assert columna_existe(conn, "camaras", "zona")

    def test_columna_ausente(self):
        with _engine().connect() as conn:
            assert not columna_existe(conn, "camaras", "site_id")

    def test_tabla_inexistente_da_false_en_vez_de_levantar(self):
        with _engine().connect() as conn:
            assert not columna_existe(conn, "users", "failed_attempts")

    def test_ve_una_columna_agregada_en_la_misma_conexion(self):
        with _engine().connect() as conn:
            assert not columna_existe(conn, "camaras", "canal")
            conn.exec_driver_sql("ALTER TABLE camaras ADD COLUMN canal INTEGER")
            assert columna_existe(conn, "camaras", "canal")
