from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base

STATUS_NEW = "nueva"
STATUS_ACKNOWLEDGED = "reconocida"
STATUS_INVESTIGATING = "en_investigacion"
STATUS_RESOLVED = "resuelta"
STATUSES = (STATUS_NEW, STATUS_ACKNOWLEDGED, STATUS_INVESTIGATING, STATUS_RESOLVED)


class AlarmEvent(Base):
    __tablename__ = "alarm_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alarm_rules.id"))
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    timestamp: Mapped[float] = mapped_column(Float)
    object_class: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[float] = mapped_column(Float)
    # Copiada de la regla al momento del disparo -- si la regla cambia de
    # severidad despues, los incidentes ya generados no se alteran.
    severity: Mapped[str] = mapped_column(String(20), default="medio")
    snapshot_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    clip_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_NEW)
    notes: Mapped[str] = mapped_column(String(4000), default="")
