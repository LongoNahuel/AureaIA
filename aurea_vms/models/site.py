from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base


class Site(Base):
    """Sitio/sede fisica (ej. "Sala Principal", "Anexo VIP"): agrupa
    camaras para la operacion multisede. La jerarquia del producto es
    Organizacion -> Sitios -> Camaras; la organizacion todavia no se
    modela (una sola instalacion por despliegue)."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(String(300), default="")
