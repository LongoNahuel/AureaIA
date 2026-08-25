from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    # Sitio/sede al que pertenece la camara. SET NULL: borrar un sitio no
    # borra sus camaras, quedan "Sin sitio" hasta reasignarse.
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_type: Mapped[str] = mapped_column(String(10), default="ipc")  # ipc | nvr | xvr
    channel: Mapped[int] = mapped_column(Integer, default=1)  # numero de canal (NVR/XVR)
    ip: Mapped[str] = mapped_column(String(64))
    port: Mapped[int] = mapped_column(Integer, default=554)
    username: Mapped[str] = mapped_column(String(120), default="")
    password: Mapped[str] = mapped_column(String(120), default="")
    rtsp_main_url: Mapped[str] = mapped_column(String(500))
    rtsp_sub_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    onvif_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_ptz: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="unknown")

    # Metadata de hardware (se completa sola al agregar por ONVIF; vacia si es manual)
    manufacturer: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
