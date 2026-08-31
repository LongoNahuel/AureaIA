from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from aurea_vms.config.settings import settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:
    """SQLite ignora las FKs (y por lo tanto los ondelete=CASCADE/SET NULL
    declarados en los modelos) salvo que cada conexion active el pragma.
    Es un listener especifico del dialecto sqlite: si el dia de mañana la
    DB cambia a otro motor, este hook simplemente no se registra y el
    esquema declarativo sigue valiendo igual."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db(db_path: Path | None = None, *, force: bool = False) -> None:
    """Crea el engine (si no existe, o si force=True) y todas las tablas.

    db_path permite apuntar a una base distinta a la de settings (usado en
    tests para aislar cada corrida en un sqlite temporal).
    """
    global _engine, _SessionLocal

    if _engine is not None and not force:
        return

    settings.ensure_dirs()
    path = db_path or settings.db_path

    _engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    if _engine.dialect.name == "sqlite":
        event.listen(_engine, "connect", _enable_sqlite_foreign_keys)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

    # Importar los modelos para que queden registrados en Base.metadata
    from aurea_vms.models import (  # noqa: F401
        alarm_event,
        alarm_rule,
        analytics_config,
        device,
        media_asset,
        site,
        user,
        zone,
    )

    Base.metadata.create_all(_engine)
    _apply_adhoc_migrations()


# Columnas nuevas por tabla, agregadas a los modelos despues de que bases
# de desarrollo pre-existentes ya tenian la tabla creada (create_all crea
# tablas nuevas pero no altera las existentes). Cada entrada es
# (columna, DDL sin "ADD COLUMN"); se aplican en orden y solo si faltan.
# Cualquier cambio mas profundo (renombrar, borrar) se resuelve recreando
# la DB. Alembic reemplaza esto cuando el esquema se estabilice, antes de
# la primera instalacion en campo.
_ADHOC_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "devices": [
        # El ON DELETE SET NULL replica el ondelete del modelo (Device.zone_id):
        # sin él, con PRAGMA foreign_keys=ON, borrar una zona con cámaras
        # asignadas falla con IntegrityError en las DBs migradas.
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


def _apply_adhoc_migrations() -> None:
    """Aplica las columnas de _ADHOC_COLUMNS que falten en cada tabla ya
    existente (una tabla nueva, creada recien por create_all, ya sale con
    el esquema completo y no necesita nada de esto)."""
    assert _engine is not None
    if _engine.dialect.name != "sqlite":
        return
    with _engine.connect() as conn:
        for table, columns in _ADHOC_COLUMNS.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if not existing:
                continue
            for name, ddl in columns:
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        _backfill_zones_from_legacy_site_id(conn)
        conn.commit()


def _backfill_zones_from_legacy_site_id(conn) -> None:
    """Las DBs anteriores a la jerarquia Sitio->Zona asignaban camaras
    directo al sitio (devices.site_id, columna que el modelo actual ya no
    declara pero que esas DBs conservan). Sin backfill, toda camara
    asignada aparecia "Sin zona" en silencio tras migrar. Se crea (o reusa)
    una zona "General" por sitio, se copia la asignacion y se consume el
    site_id legado -- consumirlo hace el paso de una sola vez: desasignar
    una camara despues no la re-asigna en el proximo arranque."""
    device_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(devices)")}
    if "site_id" not in device_columns or "zone_id" not in device_columns:
        return
    legacy_site_ids = conn.exec_driver_sql(
        "SELECT DISTINCT site_id FROM devices WHERE site_id IS NOT NULL AND zone_id IS NULL"
    ).fetchall()
    for (site_id,) in legacy_site_ids:
        # El site_id legado no tenia FK confiable: si el sitio ya no existe,
        # la camara queda "Sin zona" (no hay donde colgarla).
        if conn.exec_driver_sql("SELECT 1 FROM sites WHERE id = ?", (site_id,)).fetchone() is None:
            continue
        zone_row = conn.exec_driver_sql(
            "SELECT id FROM zones WHERE site_id = ? AND name = 'General'", (site_id,)
        ).fetchone()
        if zone_row is not None:
            zone_id = zone_row[0]
        else:
            zone_id = conn.exec_driver_sql(
                "INSERT INTO zones (site_id, name, critical) VALUES (?, 'General', 0)", (site_id,)
            ).lastrowid
        conn.exec_driver_sql(
            "UPDATE devices SET zone_id = ? WHERE site_id = ? AND zone_id IS NULL",
            (zone_id, site_id),
        )
    conn.exec_driver_sql("UPDATE devices SET site_id = NULL WHERE site_id IS NOT NULL")


@contextmanager
def get_session() -> Iterator[Session]:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
