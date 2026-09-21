from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Column, Float, ForeignKey, Integer, String

from aurea_vms.models.db import Base

SEVERITY_CRITICAL = "critico"
SEVERITY_HIGH = "alto"
SEVERITY_MEDIUM = "medio"
SEVERITY_INFO = "info"
SEVERITIES = (SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_INFO)


class AlarmRule(Base):
    __tablename__ = "alarm_rules"

    id: Any = Column(Integer, primary_key=True)

    # None = aplica a todos los dispositivos. Borrar la camara borra solo
    # las reglas especificas de esa camara (las globales no tienen FK).
    device_id: Any = Column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True
    )
    analyzer_name: Any = Column(String(60))
    object_classes: Any = Column(JSON, default=list)
    min_confidence: Any = Column(Float, default=0.5)
    cooldown_seconds: Any = Column(Integer, default=30)
    severity: Any = Column(String(20), default=SEVERITY_MEDIUM)

    # Horario en el que la regla esta activa. Dias: lista de 0(lunes)-6(domingo),
    # vacia = todos los dias. start/end: "HH:MM", vacio = sin restriccion horaria.
    schedule_days: Any = Column(JSON, default=list)
    schedule_start: Any = Column(String(5), nullable=True)
    schedule_end: Any = Column(String(5), nullable=True)

    # ej. {"notify_ui": true, "play_sound": true, "save_clip": true, "notify_desktop": true}
    actions: Any = Column(JSON, default=dict)
    enabled: Any = Column(Boolean, default=True)
