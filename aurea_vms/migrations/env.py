"""Entorno de Alembic.

Se usa de dos formas: desde la app, que llama a `command.upgrade` con la URL
ya resuelta (ver models/db.py), y desde la linea de comandos con el
alembic.ini de la raiz, para generar revisiones nuevas en desarrollo.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

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
    with engine.connect() as connection:
        context.configure(connection=connection, **_opciones_comunes())
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
