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
        user,
    )

    Base.metadata.create_all(_engine)


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
