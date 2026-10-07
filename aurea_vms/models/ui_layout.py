from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base


class UiLayout(Base):
    """Disposicion de ventanas de un usuario (Fase V4, 2026-10-07): que
    ventanas tenia abiertas, en que monitor y con que pestañas, y que camara
    iba en cada recuadro. Se guarda al cerrar y se restaura despues del login.

    Una fila por usuario, no por usuario y PC: lo decidio Daniel. Si el
    monitor guardado no existe en la maquina donde entra, la ventana cae en
    el principal (ver ui/layout_store.py).

    `data` es un JSON con su propio `schema_version`: el formato lo dueño la
    UI y puede cambiar sin migracion; una version que no se entiende se
    ignora y se loguea, no rompe el login.
    """

    __tablename__ = "ui_layouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # CASCADE: borrar el usuario borra su disposicion, no queda huerfana.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    # Timestamp unix, como el resto de las tablas (alarm_events, users).
    updated_at: Mapped[float] = mapped_column(Float)
