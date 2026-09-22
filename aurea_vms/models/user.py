from __future__ import annotations

from sqlalchemy import Float, Integer, String
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
    # Formato auto-descriptivo "pbkdf2_sha256$<iteraciones>$<salt>$<hash>"
    # (el mismo de Django/passlib): lleva adentro con que coste se calculo,
    # que es lo que permite subir las iteraciones sin resetearle la
    # contraseña a nadie. Ver core/auth.py.
    password_hash: Mapped[str] = mapped_column(String(200))
    # Solo para las filas legadas, que guardaban el hash hex pelado y el
    # salt aparte. Se vacia sola al primer login de cada usuario.
    salt: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(20), default=ROLE_ADMIN)

    # Lockout por ventana de tiempo. Persistido y no en memoria a proposito:
    # un dict de modulo se resetea cerrando y abriendo la app, que es
    # justamente lo que puede hacer quien esta sentado frente a la maquina.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Timestamp unix hasta el que la cuenta esta bloqueada. NUNCA permanente:
    # dejar al admin afuera de un VMS en una sala a las 3 AM es peor que el
    # ataque que estamos frenando.
    locked_until: Mapped[float | None] = mapped_column(Float, nullable=True)
