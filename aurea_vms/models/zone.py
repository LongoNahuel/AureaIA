from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base


class Zone(Base):
    """Zona dentro de un Sitio (ej. "Bóveda", "Sala de Máquinas A").
    `critical` marca zonas sensibles para resaltarlas en el arbol de
    camaras y (a futuro) priorizarlas en reportes."""

    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # CASCADE: borrar un sitio borra sus zonas (a diferencia de las
    # camaras, que quedan "Sin zona" via SET NULL en Device.zone_id).
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
