"""Zoom digital de una analitica: correrla sobre un recorte del cuadro.

Una camara que toma la sala entera ve la mesa chica. Sobre el cuadro
completo, el detector de personas lo achica a MAX_ANALYSIS_DIMENSION (ver
base.py) y la mesa queda con pocos pixeles; con el zoom digital la analitica
recibe solo el recorte, en la resolucion nativa del stream, y ademas procesa
menos pixeles por cuadro.

Todo se configura y se guarda en pixeles del cuadro completo (las zonas, la
rueda y el propio zoom): ZoomedAnalyzer lleva la configuracion al recorte al
crear la analitica y trae de vuelta los resultados (detecciones y metricas).
El tile, las alarmas y las capturas no se enteran del recorte.

En AnalyticsConfig.params:

- "zoom_digital": [x, y, w, h] del recorte;
- "analizar_zoom": True para analizar el recorte; False (o sin zoom), el
  cuadro completo. El zoom queda guardado aunque se analice el completo.

Una zona que queda fuera del zoom no se analiza: en metrics["zonas"] queda
en su lugar con estado OUTSIDE_ZOOM, para que los indices de las zonas
sigan siendo los de la configuracion.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from aurea_vms.core.analytics.base import AnalysisResult, Analyzer
from aurea_vms.core.events import Detection

Rect = tuple[int, int, int, int]

ZOOM_PARAM = "zoom_digital"
ANALYZE_ZOOM_PARAM = "analizar_zoom"
# Las analiticas cuyas metricas con coordenadas sabe trasladar shift_metrics.
ZOOM_ANALYZERS = frozenset({"monitor_tamper"})
# Lado minimo del recorte, en pixeles.
MIN_ZOOM_SIDE = 32
# Estado de una zona que quedo fuera del zoom (metrics["zonas"]).
OUTSIDE_ZOOM = "fuera_del_zoom"
# Claves de metrics que guardan el indice de una zona.
ZONE_INDEX_KEYS = ("ultimo_incidente", "ultimo_golpe", "ultima_previa", "ultimo_movimiento")


def zoom_region(params: dict | None) -> Rect | None:
    """El zoom guardado, si es un rectangulo valido."""
    region = (params or {}).get(ZOOM_PARAM)
    try:
        x, y, w, h = (round(float(value)) for value in region)
    except (TypeError, ValueError):
        return None
    if x < 0 or y < 0 or w < MIN_ZOOM_SIDE or h < MIN_ZOOM_SIDE:
        return None
    return (x, y, w, h)


def active_zoom(analyzer_name: str, params: dict | None) -> Rect | None:
    """El recorte sobre el que corre la analitica; None = el cuadro completo."""
    if analyzer_name not in ZOOM_ANALYZERS or not (params or {}).get(ANALYZE_ZOOM_PARAM):
        return None
    return zoom_region(params)


def clip_rect(rect, region: Rect) -> Rect | None:
    """La parte de `rect` dentro de `region`; None si no se tocan."""
    x, y, w, h = (round(float(value)) for value in rect)
    rx, ry, rw, rh = region
    left, top = max(x, rx), max(y, ry)
    right, bottom = min(x + w, rx + rw), min(y + h, ry + rh)
    if right <= left or bottom <= top:
        return None
    return (left, top, right - left, bottom - top)


def config_zones(params: dict, roi: Rect | None) -> list[Rect]:
    """Las zonas como las arma registry.create_analyzer (y las dibuja el
    tile): las de params["zones"]; sin zonas, el ROI simple."""
    zones = [
        tuple(zone)
        for zone in params.get("zones", [])
        if isinstance(zone, (list, tuple)) and len(zone) == 4
    ]
    if not zones and roi is not None:
        zones = [roi]
    return zones


def zones_outside(params: dict, region: Rect, roi: Rect | None = None) -> list[int]:
    """Los indices de las zonas que quedan fuera de `region`."""
    return [
        index
        for index, zone in enumerate(config_zones(params, roi))
        if clip_rect(zone, region) is None
    ]


def wheel_inside(params: dict, region: Rect) -> bool:
    """El centro de la rueda (o de su rectangulo) dentro de `region`. Sin
    rueda marcada, True: la analitica la busca sola en el recorte."""
    x, y, w, h = region
    wheel = params.get("rueda")
    if wheel and len(wheel) == 5:
        cx, cy = float(wheel[0]), float(wheel[1])
    elif params.get("rueda_zona") and len(params["rueda_zona"]) == 4:
        zx, zy, zw, zh = (float(value) for value in params["rueda_zona"])
        cx, cy = zx + zw / 2, zy + zh / 2
    else:
        return True
    return x <= cx < x + w and y <= cy < y + h


def zoomed_config(config, region: Rect) -> tuple[SimpleNamespace, list[int], int]:
    """La configuracion con las coordenadas llevadas al recorte, lista para
    registry.create_analyzer. Devuelve tambien que zonas quedaron (sus
    indices en la configuracion) y cuantas habia."""
    params = dict(config.params or {})
    rx, ry = region[:2]
    roi = None
    if None not in (config.roi_x, config.roi_y, config.roi_w, config.roi_h):
        roi = (config.roi_x, config.roi_y, config.roi_w, config.roi_h)
    zones = config_zones(params, roi)
    kept, inside = [], []
    for index, zone in enumerate(zones):
        clipped = clip_rect(zone, region)
        if clipped is not None:
            kept.append(index)
            inside.append([clipped[0] - rx, clipped[1] - ry, clipped[2], clipped[3]])
    params["zones"] = inside
    wheel_zone = params.get("rueda_zona")
    clipped = clip_rect(wheel_zone, region) if wheel_zone and len(wheel_zone) == 4 else None
    params["rueda_zona"] = (
        [clipped[0] - rx, clipped[1] - ry, clipped[2], clipped[3]] if clipped else None
    )
    wheel = params.get("rueda")
    if wheel and len(wheel) == 5 and wheel_inside({"rueda": wheel}, region):
        params["rueda"] = [float(wheel[0]) - rx, float(wheel[1]) - ry, *wheel[2:]]
    else:
        # Fuera del zoom: la analitica la busca sola adentro del recorte.
        params.pop("rueda", None)
    zoomed = SimpleNamespace(
        analyzer_name=config.analyzer_name,
        params=params,
        roi_x=None,
        roi_y=None,
        roi_w=None,
        roi_h=None,
        confidence_threshold=config.confidence_threshold,
        object_classes=config.object_classes,
    )
    return zoomed, kept, len(zones)


def _shift_rect(rect, dx: float, dy: float) -> list:
    x, y, w, h = rect
    return [x + dx, y + dy, w, h]


def shift_detection(detection: Detection, dx: int, dy: int) -> Detection:
    x, y, w, h = detection.bbox
    polygon = detection.polygon
    keypoints = detection.keypoints
    return Detection(
        label=detection.label,
        confidence=detection.confidence,
        bbox=(x + dx, y + dy, w, h),
        polygon=tuple((px + dx, py + dy) for px, py in polygon) if polygon else polygon,
        keypoints=tuple((px + dx, py + dy) for px, py in keypoints) if keypoints else keypoints,
    )


def shift_metrics(metrics: dict, dx: float, dy: float) -> dict:
    """Las metricas con coordenadas (las de la ruleta: bola, rueda, personas,
    manos y donde cambiaron las fichas) llevadas del recorte al cuadro
    completo. Las demas pasan igual."""
    out = dict(metrics)
    if isinstance(out.get("bola"), (list, tuple)):
        x, y = out["bola"]
        out["bola"] = [x + dx, y + dy]
    if isinstance(out.get("rueda"), (list, tuple)):
        cx, cy, *rest = out["rueda"]
        out["rueda"] = [cx + dx, cy + dy, *rest]
    if isinstance(out.get("personas"), list):
        out["personas"] = [
            {**person, "caja": _shift_rect(person["caja"], dx, dy)}
            if person.get("caja")
            else person
            for person in out["personas"]
        ]
    if isinstance(out.get("manos"), list):
        hands = []
        for hand in out["manos"]:
            hand = {**hand, "x": hand["x"] + dx, "y": hand["y"] + dy}
            if hand.get("caja"):
                hand["caja"] = _shift_rect(hand["caja"], dx, dy)
            hands.append(hand)
        out["manos"] = hands
    if isinstance(out.get("zonas"), list):
        out["zonas"] = [
            {**zone, "posiciones": [_shift_rect(spot, dx, dy) for spot in zone["posiciones"]]}
            if zone.get("posiciones")
            else zone
            for zone in out["zonas"]
        ]
    return out


def remap_zones(metrics: dict, kept: list[int], count: int) -> dict:
    """Los indices de zona del analizador (solo las zonas dentro del zoom)
    llevados a los de la configuracion; las de afuera quedan en su lugar con
    estado OUTSIDE_ZOOM."""
    if kept == list(range(count)):
        return metrics
    out = dict(metrics)

    def original(index):
        return kept[index] if isinstance(index, int) and 0 <= index < len(kept) else index

    if isinstance(out.get("zonas"), list):
        zones: list[dict] = [{"estado": OUTSIDE_ZOOM, "motivo": None} for _ in range(count)]
        for index, zone in enumerate(out["zonas"][: len(kept)]):
            zones[kept[index]] = {**zone, "indice": kept[index]} if "indice" in zone else zone
        out["zonas"] = zones
    for key in ZONE_INDEX_KEYS:
        value = out.get(key)
        if isinstance(value, dict) and "zona" in value:
            out[key] = {**value, "zona": original(value["zona"])}
    if isinstance(out.get("manos"), list):
        out["manos"] = [
            {**hand, "zona": original(hand["zona"])} if hand.get("zona") is not None else hand
            for hand in out["manos"]
        ]
    return out


class ZoomedAnalyzer(Analyzer):
    """Corre `inner` sobre el recorte `region` de cada cuadro y devuelve sus
    resultados en pixeles del cuadro completo. `inner` se creo con
    zoomed_config: `kept` son los indices (en la configuracion) de sus zonas
    y `zone_count` cuantas zonas tiene la configuracion."""

    def __init__(self, inner: Analyzer, region: Rect, kept: list[int], zone_count: int) -> None:
        self.name = inner.name
        self.inner = inner
        self.region = tuple(region)
        self._kept = list(kept)
        self._zone_count = zone_count

    def process_frame(self, frame: np.ndarray, timestamp: float) -> AnalysisResult:
        x, y, w, h = self.region
        height, width = frame.shape[:2]
        if x + MIN_ZOOM_SIDE > width or y + MIN_ZOOM_SIDE > height:
            raise ValueError(
                f"El zoom digital {list(self.region)} queda fuera del cuadro ({width}×{height})"
            )
        crop = np.ascontiguousarray(frame[y : y + h, x : x + w])
        self.inner.source_size = (width, height)
        result = self.inner.process_frame(crop, timestamp)
        # El mismo objeto en detecciones y triggers sigue siendo el mismo.
        shifted: dict[int, Detection] = {}

        def shift(detection: Detection) -> Detection:
            key = id(detection)
            if key not in shifted:
                shifted[key] = shift_detection(detection, x, y)
            return shifted[key]

        metrics = remap_zones(shift_metrics(result.metrics, x, y), self._kept, self._zone_count)
        metrics[ZOOM_PARAM] = list(self.region)
        return AnalysisResult(
            detections=tuple(shift(d) for d in result.detections),
            metrics=metrics,
            triggers=None if result.triggers is None else tuple(shift(d) for d in result.triggers),
        )

    def reset_counters(self) -> None:
        self.inner.reset_counters()

    def close(self) -> None:
        self.inner.close()
