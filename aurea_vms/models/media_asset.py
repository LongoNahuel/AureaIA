from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from aurea_vms.models.db import Base

KIND_CLIP = "clip"
KIND_SNAPSHOT = "snapshot"
KIND_RECORDING = "recording"  # reservado para grabacion continua (roadmap)
KINDS = (KIND_CLIP, KIND_SNAPSHOT, KIND_RECORDING)


class MediaAsset(Base):
    """Indice de todo archivo de media que la app escribe a disco (clips,
    capturas y, a futuro, grabacion continua).

    La regla de oro del storage: BUSCAR un archivo nunca recorre el
    filesystem -- toda consulta (por camara, fecha, evento, usuario o tipo)
    se resuelve contra esta tabla por indice, y el disco solo se toca para
    cargar el archivo ya localizado. El layout fisico en carpetas
    (media/<tipo>/<A>/<M>/<D>/<camara>/) existe solo para que un humano
    pueda navegarlo a mano; el codigo no lo escanea jamas.
    """

    __tablename__ = "media_assets"

    __table_args__ = (
        # Las dos consultas calientes: "lo mas nuevo/viejo de un tipo"
        # (feed, retencion) y "lo de una camara en un rango" (forense).
        Index("ix_media_assets_kind_ts", "kind", "timestamp"),
        Index("ix_media_assets_device_ts", "device_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))

    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    # SET NULL: purgar/borrar el evento no debe romper el indice de media
    # (la retencion decide aparte cuando muere el archivo).
    alarm_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("alarm_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Usuario que genero el archivo (captura manual, export). None = lo
    # genero el sistema (clip/captura automatica de una alarma).
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    timestamp: Mapped[float] = mapped_column(Float, index=True)

    # Ruta relativa a settings.media_dir, SIEMPRE con "/" (portable entre
    # SO): el directorio de datos puede moverse sin invalidar la tabla.
    rel_path: Mapped[str] = mapped_column(String(500), unique=True)

    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
