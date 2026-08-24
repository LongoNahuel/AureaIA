from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base


class AnalyticsConfig(Base):
    __tablename__ = "analytics_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    analyzer_name: Mapped[str] = mapped_column(String(60))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.5)

    # ROI rectangular opcional, en pixeles del frame original. None = frame completo.
    roi_x: Mapped[int | None] = mapped_column(Integer, nullable=True)
    roi_y: Mapped[int | None] = mapped_column(Integer, nullable=True)
    roi_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    roi_h: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Lista de clases de objeto a detectar, ej. ["person", "car"]
    object_classes: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Parametros especificos del tipo de analizador, ej.:
    #   motion_detection: {"sensitivity": 50, "min_area": 500}
    #   line_crossing: {"line": [[x1,y1],[x2,y2]], "label_in": "Entrada", "label_out": "Salida"}
    params: Mapped[dict] = mapped_column(JSON, default=dict)
