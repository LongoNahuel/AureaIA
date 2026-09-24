"""Entorno de Alembic.

Se usa de dos formas: desde la app, que llama a `command.upgrade` con la URL
ya resuelta (ver models/db.py), y desde la linea de comandos con el
alembic.ini de la raiz, para generar revisiones nuevas en desarrollo.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, event, pool

from aurea_vms.models.db import NAMING_CONVENTION, Base, importar_modelos

# Importar los modelos registra las 8 tablas en Base.metadata, que es contra
# lo que compara `alembic revision --autogenerate`.
importar_modelos()
target_metadata = Base.metadata

config = context.config


def _opciones_comunes() -> dict:
    return {
        "target_metadata": target_metadata,
        # SQLite no tiene ALTER TABLE de verdad: sin render_as_batch, toda
        # migracion que toque una constraint o un nullable falla.
        "render_as_batch": True,
        "compare_type": True,
        "naming_convention": NAMING_CONVENTION,
    }


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_opciones_comunes(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, **_opciones_comunes())
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    if engine.dialect.name == "sqlite":
        _migracion_atomica_en_sqlite(engine)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            # SQLiteImpl declara transactional_ddl=False y Alembic no abre
            # transaccion alrededor de las revisiones. Con el listener de
            # abajo el DDL de SQLite SI es transaccional: se le avisa, y el
            # upgrade entero corre en UNA transaccion (o llega a la cabeza,
            # o la base queda en la revision de la que salio).
            transactional_ddl=True,
            **_opciones_comunes(),
        )
        with context.begin_transaction():
            context.run_migrations()


def _migracion_atomica_en_sqlite(engine) -> None:
    """Receta oficial de SQLAlchemy para pysqlite ("Serializable isolation /
    Savepoints / Transactional DDL").

    - pysqlite, en su modo por defecto, no abre transaccion antes de un DDL:
      cada CREATE/DROP/ALTER se aplicaba solo, y una revision que moria a
      mitad dejaba la base con media revision puesta (un batch cortado deja
      la tabla original borrada y un `_alembic_tmp_*` en su lugar).
      `isolation_level=None` apaga la gestion propia del driver, y el
      `BEGIN IMMEDIATE` la reemplaza: toma el lock de escritura de entrada,
      en vez de descubrir a mitad de la migracion que otro esta escribiendo.

    - `PRAGMA foreign_keys=OFF` es un INVARIANTE de las migraciones: el
      batch recrea la tabla (CREATE nueva, copia, DROP vieja, RENAME), y con
      FKs activas el DROP borra en cascada a los hijos (verificado). Hoy ya
      quedaba OFF porque este engine no tiene el listener de models/db.py,
      pero eso era un accidente; ahora es explicito. Se setea al conectar,
      antes del BEGIN: dentro de una transaccion SQLite lo ignora.
    """

    @event.listens_for(engine, "connect")
    def _al_conectar(dbapi_connection, _record) -> None:
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("PRAGMA foreign_keys=OFF")

    @event.listens_for(engine, "begin")
    def _al_empezar(connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
