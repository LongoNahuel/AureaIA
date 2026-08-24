from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base

SEVERITY_CRITICAL = "critico"
SEVERITY_HIGH = "alto"
SEVERITY_MEDIUM = "medio"
SEVERITY_INFO = "info"
SEVERITIES = (SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_INFO)


class AlarmRule(Base):
    __tablename__ = "alarm_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # None = aplica a todos los dispositivos
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    analyzer_name: Mapped[str] = mapped_column(String(60))
    object_classes: Mapped[list[str]] = mapped_column(JSON, default=list)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.5)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=30)
    severity: Mapped[str] = mapped_column(String(20), default=SEVERITY_MEDIUM)

    # Horario en el que la regla esta activa. Dias: lista de 0(lunes)-6(domingo),
    # vacia = todos los dias. start/end: "HH:MM", vacio = sin restriccion horaria.
    schedule_days: Mapped[list[int]] = mapped_column(JSON, default=list)
    schedule_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    schedule_end: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # ej. {"notify_ui": true, "play_sound": true, "save_clip": true, "notify_desktop": true}
    actions: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
