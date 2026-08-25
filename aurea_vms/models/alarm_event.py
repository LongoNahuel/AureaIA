from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base

STATUS_NEW = "nueva"
STATUS_ACKNOWLEDGED = "reconocida"
STATUS_INVESTIGATING = "en_investigacion"
STATUS_RESOLVED = "resuelta"
STATUSES = (STATUS_NEW, STATUS_ACKNOWLEDGED, STATUS_INVESTIGATING, STATUS_RESOLVED)


class AlarmEvent(Base):
    __tablename__ = "alarm_events"

    # El feed de alarmas y las busquedas forenses filtran por camara +
    # rango temporal: el indice compuesto evita el scan completo.
    __table_args__ = (Index("ix_alarm_events_device_ts", "device_id", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # SET NULL y no CASCADE: borrar/editar una regla no debe borrar el
    # historial de incidentes (la severidad ya viene copiada al evento).
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("alarm_rules.id", ondelete="SET NULL"), nullable=True
    )
    # Borrar la camara si borra sus eventos (sin camara no hay contexto
    # ni media asociada que mostrar).
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[float] = mapped_column(Float, index=True)
    object_class: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[float] = mapped_column(Float)
    # Copiada de la regla al momento del disparo -- si la regla cambia de
    # severidad despues, los incidentes ya generados no se alteran.
    severity: Mapped[str] = mapped_column(String(20), default="medio")
    # La media del evento (captura, clip) vive en media_assets, vinculada
    # por alarm_event_id -- este modelo ya no guarda rutas de archivos.
    status: Mapped[str] = mapped_column(String(20), default=STATUS_NEW)
    notes: Mapped[str] = mapped_column(String(4000), default="")
