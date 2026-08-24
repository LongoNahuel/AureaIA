from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base


ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operador"


class User(Base):
    """Usuario local de la app. El primer usuario (alta desde el wizard de
    primer arranque) siempre es "admin"; los que se crean despues desde
    Gestion de Usuarios pueden ser "admin" u "operador"."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    salt: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_ADMIN)
