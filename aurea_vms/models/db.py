from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from aurea_vms.config.settings import settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


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
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

    # Importar los modelos para que queden registrados en Base.metadata
    from aurea_vms.models import alarm_event, alarm_rule, analytics_config, device, site, user, zone  # noqa: F401

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
