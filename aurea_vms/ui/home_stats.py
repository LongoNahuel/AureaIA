"""Los numeros de los graficos de Inicio (Fase 2 de la interfaz, 2026-10-09):
incidentes en el tiempo por tipo, por tipo, por estado y por camara, en el
periodo elegido (24 h, 7 dias o 30 dias) y el sitio de arriba.

Solo calcula: los graficos estan en widgets/home_charts.py.

Colores (guia de visualizacion, validados contra el fondo de las tarjetas
#1a2029 con validate_palette: banda, croma, daltonismo y contraste pasan):
- Cada tipo de incidente tiene SU color, siempre el mismo, en la linea y en
  la torta: el color sigue al tipo, no a su puesto en el ranking.
- Mas de MAX_TYPES tipos se pliegan en "Otros" (gris), nunca un color nuevo.
- Los estados usan los colores de estado de la app (rojo, ambar, gris,
  verde), siempre con su rotulo: verde y ambar no se distinguen con
  protanopia, asi que no van pegados y nunca llevan el significado solos.
"""

from __future__ import annotations

import datetime as dt
import time
from collections import Counter
from dataclasses import dataclass

from aurea_vms.models import repository
from aurea_vms.models.alarm_event import (
    STATUS_ACKNOWLEDGED,
    STATUS_INVESTIGATING,
    STATUS_NEW,
    STATUS_RESOLVED,
)
from aurea_vms.ui.labels import ALARM_STATUS_LABELS, display_class
from aurea_vms.ui.theme import TONES

# (cantidad de puntos, rotulo, unidad de cada punto)
RANGES = {
    "24h": (24, "24 h", "hour"),
    "7d": (7, "7 días", "day"),
    "30d": (30, "30 días", "day"),
}
DEFAULT_RANGE = "7d"

# Orden fijo de colores por tipo (paleta categorica oscura de la guia).
TYPE_COLORS = {
    "fichas_tras_no_va_mas": "#3987e5",
    "fichas_movidas": "#d95926",
    "consumo_sustancias": "#199e70",
    "preparacion_consumo": "#c98500",
    "patada_monitor": "#d55181",
    "golpe_monitor": "#9085e9",
}
OTHER_COLOR = "#6b7280"
OTHER_LABEL = "Otros"
# Lineas en el grafico de tiempo: las tres que mas pesan + "Otros".
MAX_LINES = 3
# Segmentos de la torta por tipo (la guia: parte del todo, hasta 6).
MAX_TYPES = 5
MAX_CAMERAS = 5
MAX_EVENTS = 20000

STATUS_ORDER = [STATUS_NEW, STATUS_INVESTIGATING, STATUS_ACKNOWLEDGED, STATUS_RESOLVED]
STATUS_COLORS = {
    STATUS_NEW: TONES["alert"],
    STATUS_INVESTIGATING: TONES["warn"],
    STATUS_ACKNOWLEDGED: OTHER_COLOR,
    STATUS_RESOLVED: TONES["ok"],
}

Segment = tuple[str, int, str]  # (rotulo, valor, color)
Series = tuple[str, list[int], str]  # (rotulo, valores por punto, color)


@dataclass(frozen=True)
class HomeStats:
    range_label: str
    unit: str  # "hora" | "día"
    labels: list[str]  # rotulos del eje X
    series: list[Series]
    by_type: list[Segment]
    by_status: list[Segment]
    by_camera: list[tuple[str, int]]
    total: int
    open_total: int  # sin reconocer + en investigacion


def _bucket_starts(points: int, unit: str, now: float) -> list[dt.datetime]:
    current = dt.datetime.fromtimestamp(now)
    if unit == "hour":
        last = current.replace(minute=0, second=0, microsecond=0)
        return [last - dt.timedelta(hours=points - 1 - i) for i in range(points)]
    last = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return [last - dt.timedelta(days=points - 1 - i) for i in range(points)]


def _bucket_index(timestamp: float, first: dt.datetime, unit: str) -> int:
    moment = dt.datetime.fromtimestamp(timestamp)
    if unit == "hour":
        return int(
            (moment.replace(minute=0, second=0, microsecond=0) - first).total_seconds() // 3600
        )
    return (moment.date() - first.date()).days


def _type_color(object_class: str) -> str:
    return TYPE_COLORS.get(object_class, OTHER_COLOR)


def compute(
    site_id: int | None, range_key: str = DEFAULT_RANGE, now: float | None = None
) -> HomeStats:
    points, range_label, unit = RANGES[range_key]
    now = now if now is not None else time.time()
    starts = _bucket_starts(points, unit, now)
    since = starts[0].timestamp()
    events = [
        event
        for event in repository.list_alarm_events(limit=MAX_EVENTS, site_id=site_id)
        if since <= event.timestamp <= now
    ]

    by_class = Counter(event.object_class for event in events)
    ranked = [cls for cls, _count in by_class.most_common()]

    # Linea: las MAX_LINES clases que mas pesan, el resto en "Otros". El
    # orden de dibujo sigue el orden fijo de colores, no el ranking.
    lines = sorted(
        ranked[:MAX_LINES],
        key=lambda c: list(TYPE_COLORS).index(c) if c in TYPE_COLORS else len(TYPE_COLORS),
    )
    values = {cls: [0] * points for cls in lines}
    other = [0] * points
    for event in events:
        index = _bucket_index(event.timestamp, starts[0], unit)
        if not 0 <= index < points:
            continue
        if event.object_class in values:
            values[event.object_class][index] += 1
        else:
            other[index] += 1
    series: list[Series] = [(display_class(cls), values[cls], _type_color(cls)) for cls in lines]
    if any(other):
        series.append((OTHER_LABEL, other, OTHER_COLOR))

    # Torta por tipo: hasta MAX_TYPES, el resto plegado.
    by_type: list[Segment] = [
        (display_class(cls), by_class[cls], _type_color(cls)) for cls in ranked[:MAX_TYPES]
    ]
    rest = sum(by_class[cls] for cls in ranked[MAX_TYPES:])
    if rest:
        by_type.append((OTHER_LABEL, rest, OTHER_COLOR))

    by_status_count = Counter(event.status for event in events)
    by_status: list[Segment] = [
        (ALARM_STATUS_LABELS[status], by_status_count[status], STATUS_COLORS[status])
        for status in STATUS_ORDER
        if by_status_count[status]
    ]

    names = {device.id: device.name for device in repository.list_devices()}
    by_camera = [
        (names.get(device_id, f"Cámara #{device_id}"), count)
        for device_id, count in Counter(e.device_id for e in events).most_common(MAX_CAMERAS)
    ]

    fmt = "%H:%M" if unit == "hour" else "%d/%m"
    return HomeStats(
        range_label=range_label,
        unit="hora" if unit == "hour" else "día",
        labels=[start.strftime(fmt) for start in starts],
        series=series,
        by_type=by_type,
        by_status=by_status,
        by_camera=by_camera,
        total=len(events),
        open_total=by_status_count[STATUS_NEW] + by_status_count[STATUS_INVESTIGATING],
    )
