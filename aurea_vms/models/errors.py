"""Excepciones de dominio de la capa de persistencia.

La regla de capas del proyecto dice que `core` y `models` no conocen
widgets; la contracara es que la UI no tiene por que conocer el ORM. Hasta
la Fase 7 eso se rompia en un solo lugar: ui/dialogs/device_dialog.py
importaba `sqlalchemy.exc.IntegrityError` para poder traducir un "UNIQUE
constraint failed" a "Ya existe un sitio llamado X". Era el unico import de
SQLAlchemy en toda la capa de widgets.

Con estas excepciones, `repository` traduce en su frontera y la UI habla
solo el idioma del dominio. Ademas deja de atar el mensaje al motor: el
proyecto se disenio para poder cambiar de DB (ver docs/ARQUITECTURA.md), y
un dialogo que captura `sqlalchemy.exc` estaria atado a SQLAlchemy para
siempre.
"""

from __future__ import annotations


class RepositoryError(RuntimeError):
    """Falló una escritura en la capa de persistencia."""


class DuplicateError(RepositoryError):
    """La fila choca contra una restricción de unicidad: un sitio con un
    nombre que ya existe, un usuario repetido, una ruta de media ya
    indexada."""
