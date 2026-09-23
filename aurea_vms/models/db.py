from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from aurea_vms.config.settings import settings
from aurea_vms.migrations import BASELINE_REVISION, MIGRATIONS_DIR
from aurea_vms.migrations.adopcion import adoptar

# Nombres deterministas para indices y constraints. SQLite no sabe alterar
# una tabla: Alembic emula ALTER con batch_alter_table, que copia la tabla
# entera -- y para eso necesita poder referenciar cada constraint POR NOMBRE.
# Sin esta convencion, las constraints que SQLAlchemy genera anonimas (todo
# lo que sale de index=True, unique=True o un ForeignKey) no se pueden tocar
# desde una migracion, que es exactamente lo que necesita la fase de
# constraints e indices.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


logger = logging.getLogger(__name__)

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def importar_modelos() -> None:
    """Registra las 8 tablas en Base.metadata.

    Los modelos no se importan en ningun lado por su valor: se importan para
    que el mapeo declarativo corra. Lo necesitan el autogenerate de Alembic
    (ver migrations/env.py), la adopcion de bases viejas y cualquier cosa que
    lea el metadata.
    """
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


# Milisegundos que una conexion reintenta antes de rendirse con "database
# is locked". Coincide a proposito con el default del driver sqlite3 de
# Python (timeout=5.0): se explicita, no se cambia. Ver _apply_sqlite_pragmas.
SQLITE_BUSY_TIMEOUT_MS = 5000


def _apply_sqlite_pragmas(dbapi_connection, _record) -> None:
    """Pragmas por conexion. Listener especifico del dialecto sqlite: si el
    dia de mañana la DB cambia a otro motor, este hook simplemente no se
    registra y el esquema declarativo sigue valiendo igual.

    - foreign_keys: SQLite ignora las FKs (y por lo tanto los
      ondelete=CASCADE/SET NULL declarados en los modelos) salvo que cada
      conexion active el pragma.

    - journal_mode=WAL: cuatro tipos de hilo escriben esta base a la vez
      (StreamWorker el estado de cada camara, AlarmEngine los eventos desde
      el hilo de analitica, ClipWriter y RetentionWorker la media) mientras
      la UI lee. En el modo por defecto (rollback journal) el que escribe
      bloquea a los que leen y viceversa; con WAL las escrituras van a un
      archivo aparte y dejan de pelearse con las lecturas. Sigue habiendo un
      solo escritor a la vez: eso es de SQLite, no lo cambia el modo.

      OJO: a diferencia del resto, este pragma es PERSISTENTE -- queda
      grabado en el archivo de la base, no se re-aplica por conexion (se
      setea igual en cada una: es idempotente y barato). Deja dos archivos
      hermanos, "<base>-wal" y "<base>-shm", que hay que copiar junto con la
      base si se hace un backup a mano. Y NO funciona sobre unidades de red
      (NFS/SMB): si algun dia el data-dir vive en un disco compartido, esto
      hay que revisarlo.

    - busy_timeout: cuanto espera una conexion que encuentra la base ocupada
      antes de rendirse con "database is locked". MEDIDO: el driver sqlite3
      de Python ya pasa timeout=5.0 a connect(), asi que el default efectivo
      ya era 5000 -- este PRAGMA no cambia el comportamiento, lo vuelve
      explicito y auditable (y deja de depender de un default del driver que
      nadie en el equipo tiene por que conocer). El valor: una escritura de
      las nuestras tarda milisegundos, asi que 5s absorbe un pico, no tapa
      una query lenta.

    - synchronous=NORMAL: la combinacion recomendada junto con WAL. Menos
      fsync por commit; ante un corte de luz se pueden perder las ultimas
      transacciones, pero la base no se corrompe.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def init_db(db_path: Path | None = None, *, force: bool = False) -> None:
    """Crea el engine (si no existe, o si force=True) y deja la base en la
    ultima revision de esquema.

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
        event.listen(_engine, "connect", _apply_sqlite_pragmas)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

    importar_modelos()
    migrar(_engine, path)


def _config_de_alembic(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def migrar(engine, db_path: Path) -> None:
    """Lleva la base a la ultima revision, venga de donde venga.

    Tres casos, y los tres terminan en `upgrade head`:

    - Base nueva: no hay tablas, las crea la revision baseline.
    - Base ya versionada: aplica lo que falte.
    - Base ANTERIOR a Alembic (tiene tablas pero no alembic_version): se
      adopta -- se la lleva a la forma de la baseline con las columnas
      ad-hoc que se venian agregando a mano -- y se la marca en esa
      revision. Pasa una sola vez por base.

    La app migra sola al arrancar porque en una instalacion de cliente no
    hay nadie que corra `alembic upgrade` a mano.
    """
    config = _config_de_alembic(db_path)
    cabeza = ScriptDirectory.from_config(config).get_current_head()

    with engine.connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()
        tablas = set(inspect(connection).get_table_names())

    if revision == cabeza:
        # El caso normal de todos los arranques a partir del segundo. Se
        # corta aca para no cargar env.py ni armar la maquinaria de upgrade
        # solo para descubrir que no hay nada que aplicar (73ms -> <1ms).
        return

    if revision is None and tablas - {"alembic_version"}:
        adoptar(engine, Base.metadata)
        logger.info("Base de datos sin versionar: adoptada en la revisión %s", BASELINE_REVISION)
        command.stamp(config, BASELINE_REVISION)

    command.upgrade(config, "head")


def revision_actual(engine) -> str | None:
    """Revision en la que esta la base, o None si nunca se versiono."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


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
