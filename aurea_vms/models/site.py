from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base


class Site(Base):
    """Sitio/sede fisica (ej. "Sala Principal", "Anexo VIP"): primer nivel
    de la jerarquia Organizacion -> Sitios -> Zonas -> Camaras (la
    organizacion todavia no se modela: una sola instalacion por
    despliegue). Para un despliegue de un solo local, alcanza con crear
    un unico Site y organizar las zonas debajo de el."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(String(300), default="")
