"""Lo que rodea a una migracion para que no pueda dejar la base inservible.

`models/db.py::migrar` lo usa en este orden, y solo cuando hay algo que
aplicar (el arranque normal, con la base ya en la cabeza, no pasa por aca):

1. `lock_de_migracion`: un lock de archivo entre PROCESOS. Dos instancias de
   la app (doble click, o el smoke corriendo con la app abierta) migrando a
   la vez se pisaban: la segunda veia la revision vieja, arrancaba la misma
   revision y moria a mitad. Con el lock tomado, `migrar` vuelve a leer la
   revision: la que espero encuentra la base ya migrada y no hace nada.
2. `respaldar`: copia de la base (y de `camera_key`, sin la cual la copia
   no sirve: las credenciales van cifradas con ella) ANTES de tocar nada.
   Se usa la API de backup de sqlite3, que es segura con WAL (copiar el
   archivo a mano no lo es: faltarian las paginas que viven en el -wal).
   Se conservan los ultimos `BACKUPS_A_CONSERVAR`.
3. `limpiar_tablas_temporales`: los `_alembic_tmp_*` que haya dejado una
   corrida vieja cortada a mitad de un batch. Con las migraciones atomicas
   (ver env.py) ya no deberian aparecer, pero una base que viene de antes
   puede tenerlos, y un batch sobre esa tabla muere con "table already
   exists".

Sin dependencias nuevas: fcntl en POSIX, msvcrt en Windows.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from aurea_vms.core.credential_store import KEY_FILENAME

logger = logging.getLogger(__name__)

BACKUPS_DIRNAME = "backups"
BACKUPS_A_CONSERVAR = 3
LOCK_FILENAME = "migracion.lock"
# Lo que espera una instancia a que la otra termine de migrar. Una migracion
# de las nuestras tarda menos de un segundo; esto cubre una base grande en
# un disco lento sin colgar la app para siempre si el lock quedo tomado por
# algo raro.
LOCK_TIMEOUT_S = 120.0
_LOCK_REINTENTO_S = 0.1


class LockDeMigracionOcupado(RuntimeError):
    """Otra instancia sigue migrando despues de LOCK_TIMEOUT_S."""


def _tomar(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _soltar(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def lock_de_migracion(directorio: Path, timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Lock exclusivo entre procesos sobre `<directorio>/migracion.lock`.

    El archivo no se borra al soltar: borrarlo abre una carrera (un tercero
    abre el archivo viejo, otro crea uno nuevo, y los dos "tienen" el lock).
    Lo que se bloquea es el archivo abierto, no su existencia; si el proceso
    muere, el sistema operativo suelta el lock solo.
    """
    directorio.mkdir(parents=True, exist_ok=True)
    fd = os.open(directorio / LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        limite = time.monotonic() + timeout_s
        while not _tomar(fd):
            if time.monotonic() >= limite:
                raise LockDeMigracionOcupado(
                    f"Otra instancia de la app está migrando la base desde hace más de "
                    f"{timeout_s:.0f} s ({directorio / LOCK_FILENAME})"
                )
            time.sleep(_LOCK_REINTENTO_S)
        try:
            yield
        finally:
            _soltar(fd)
    finally:
        os.close(fd)


def _backups(destino: Path, base: str) -> list[Path]:
    return sorted(destino.glob(f"{base}-*.sqlite3"), key=lambda p: p.name)


def respaldar(db_path: Path, revision: str | None) -> Path:
    """Copia la base (y su `camera_key`, si hay) a `<dir de la base>/backups/`
    y deja solo los ultimos BACKUPS_A_CONSERVAR. Devuelve la ruta del backup.

    Nombre: `<base>-<revision>-<AAAAmmdd-HHMMSS-ffffff>.sqlite3`; con el
    timestamp de ancho fijo, el orden alfabetico es el cronologico.
    """
    destino = db_path.parent / BACKUPS_DIRNAME
    destino.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    nombre = f"{db_path.stem}-{revision or 'sin_versionar'}-{sello}"
    copia = destino / f"{nombre}.sqlite3"

    # El archivo se crea 0600 ANTES de que sqlite3 lo abra (sqlite respeta
    # los permisos de un archivo existente): la copia lleva los hashes de
    # usuario y las credenciales cifradas, igual que la base.
    os.close(os.open(copia, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    origen = sqlite3.connect(db_path)
    try:
        salida = sqlite3.connect(copia)
        try:
            origen.backup(salida)
        finally:
            salida.close()
    finally:
        origen.close()

    clave = db_path.parent / KEY_FILENAME
    if clave.exists():
        copia_clave = destino / f"{nombre}.{KEY_FILENAME}"
        # Mismos permisos que la original: es la clave de las credenciales.
        fd = os.open(copia_clave, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as archivo:
            archivo.write(clave.read_bytes())

    viejos = _backups(destino, db_path.stem)[:-BACKUPS_A_CONSERVAR]
    for viejo in viejos:
        viejo.unlink(missing_ok=True)
        viejo.with_suffix(f".{KEY_FILENAME}").unlink(missing_ok=True)

    logger.info("Backup previo a migrar: %s", copia)
    return copia


def limpiar_tablas_temporales(engine) -> list[str]:
    """Borra los `_alembic_tmp_*` huerfanos. Devuelve los que borro."""
    if engine.dialect.name != "sqlite":
        return []
    with engine.begin() as connection:
        nombres = [
            fila[0]
            for fila in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name LIKE '\\_alembic\\_tmp\\_%' ESCAPE '\\'"
            )
        ]
        for nombre in nombres:
            connection.execute(text(f'DROP TABLE "{nombre}"'))
    if nombres:
        logger.warning("Tablas temporales de una migración cortada, borradas: %s", nombres)
    return nombres
