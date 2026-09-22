"""Versionado de esquema con Alembic.

Vive DENTRO del paquete y no en la raiz del repo a proposito: el .exe de
PyInstaller tiene que llevarse las revisiones, porque la base del cliente se
migra al arrancar la app -- no hay nadie que corra `alembic upgrade` a mano
en una sala de casino. El spec las empaqueta como datas y el smoke del
ejecutable es la prueba de que llegaron.

Hasta la Fase 8 el esquema se mantenia con `create_all` (que crea tablas
nuevas pero NO altera las existentes) mas un dict `_ADHOC_COLUMNS` con
ALTER TABLE ADD COLUMN. No habia tabla de version: no se podia saber en que
estado estaba una base de campo salvo inspeccionando columnas, y una columna
agregada al modelo pero olvidada en el dict rompia la base vieja en
silencio. Ademas dos UPDATE de migracion de datos corrian en CADA arranque,
para siempre.
"""

from __future__ import annotations

from pathlib import Path

from aurea_vms.config import resources


def _resolver_dir() -> Path:
    """Donde viven env.py y las revisiones.

    En desarrollo, al lado de este archivo. Empaquetado hay que resolverlas
    contra la raiz del bundle, donde el spec las copia como datas: bajo
    PyInstaller este modulo vive DENTRO del archivo PYZ y __file__ no apunta
    a un directorio de verdad. Es la misma trampa que ya se pago con los
    modelos .onnx (ver core/analytics/model_assets.py).
    """
    if resources.is_frozen():
        return resources.bundled_path("aurea_vms/migrations")
    return Path(__file__).resolve().parent


MIGRATIONS_DIR = _resolver_dir()

# La revision que representa "el esquema tal como estaba cuando se adopto
# Alembic". Las bases anteriores se marcan aca (ver models/db.py).
BASELINE_REVISION = "0001_baseline"
