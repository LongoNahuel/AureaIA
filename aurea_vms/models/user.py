from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base

ROLE_ADMIN = "admin"
ROLE_SUPERVISOR = "supervisor"
ROLE_OPERATOR = "operador"
ROLE_AUDITOR = "auditor"
ROLES = (ROLE_ADMIN, ROLE_SUPERVISOR, ROLE_OPERATOR, ROLE_AUDITOR)

# Unica fuente de verdad de las etiquetas visibles (antes vivia duplicada
# en user_management_module).
ROLE_LABELS = {
    ROLE_ADMIN: "Administrador",
    ROLE_SUPERVISOR: "Supervisor",
    ROLE_OPERATOR: "Operador",
    ROLE_AUDITOR: "Auditor",
}


class User(Base):
    """Usuario local de la app. El primer usuario (alta desde el wizard de
    primer arranque) siempre es "admin"; los siguientes se crean desde
    Gestion de Usuarios con cualquiera de los roles de ROLES. Que puede
    hacer cada rol lo define core/permissions.py."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    salt: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_ADMIN)
