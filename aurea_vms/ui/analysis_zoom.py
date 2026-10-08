"""Zoom digital de la analitica desde la vista en vivo: "Analizar este zoom" y
"Analizar el flujo completo" del menu del tile. Guarda la eleccion en la
configuracion de la analitica (params, ver core/analytics/digital_zoom.py) y
la reinicia para que la tome. Si no arranca, la deja como estaba."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QWidget

from aurea_vms.core import incident_rules
from aurea_vms.core.analytics.digital_zoom import (
    ANALYZE_ZOOM_PARAM,
    ZOOM_PARAM,
    config_zones,
    wheel_inside,
    zones_outside,
)
from aurea_vms.core.analytics.registry import ANALYZER_DISPLAY_NAMES, ROULETTE_MODE
from aurea_vms.core.analytics_engine import analytics_engine
from aurea_vms.core.event_bus import event_bus
from aurea_vms.models import repository
from aurea_vms.ui.notify import confirm, notify, warn

logger = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]

TITLE_ZOOM = "Analizar este zoom"
TITLE_FULL = "Analizar el flujo completo"


def _roi(config) -> Rect | None:
    values = (config.roi_x, config.roi_y, config.roi_w, config.roi_h)
    return None if None in values else values


def zoom_check(params: dict, region: Rect, roi: Rect | None = None) -> tuple[str | None, list[str]]:
    """Que pasa con las zonas y la rueda si la analitica corre sobre
    `region`: (motivo por el que no se puede, avisos)."""
    zones = config_zones(params, roi)
    outside = zones_outside(params, region, roi)
    roulette = params.get("modo") == ROULETTE_MODE
    if zones and len(outside) == len(zones) and not roulette:
        return "Ninguna zona queda dentro del zoom: alejá el zoom o mové las zonas.", []
    notes = []
    if len(outside) == 1:
        notes.append(f"La zona {outside[0] + 1} queda fuera del zoom y no se analiza.")
    elif outside:
        names = ", ".join(str(index + 1) for index in outside[:-1])
        notes.append(f"Las zonas {names} y {outside[-1] + 1} quedan fuera del zoom.")
    if roulette and not wheel_inside(params, region):
        notes.append(
            "La rueda marcada queda fuera del zoom: se la busca adentro, y sin rueda no hay "
            "no va más."
        )
    return None, notes


def set_analysis_zoom(parent: QWidget, config, region: Rect | None) -> bool:
    """Corre la analitica de `config` sobre `region` (pixeles del cuadro
    completo) o, con None, sobre el flujo completo. El zoom queda guardado
    aunque se vuelva al flujo completo. Pide confirmacion: la analitica se
    reinicia (en ruleta, la ronda en curso se pierde)."""
    params = dict(config.params or {})
    name = ANALYZER_DISPLAY_NAMES.get(config.analyzer_name, config.analyzer_name)
    if region is not None:
        title = TITLE_ZOOM
        error, notes = zoom_check(params, region, _roi(config))
        if error:
            warn(parent, title, error)
            return False
        text = " ".join(
            [f"{name} se reinicia y analiza solo este zoom ({region[2]}×{region[3]} px).", *notes]
        )
        params.update({ZOOM_PARAM: list(region), ANALYZE_ZOOM_PARAM: True})
    else:
        title = TITLE_FULL
        text = f"{name} se reinicia y vuelve a analizar el cuadro completo."
        params[ANALYZE_ZOOM_PARAM] = False
    if not confirm(parent, title, text):
        return False
    if not _save_and_restart(parent, config, params, title):
        return False
    done = "Analizando el zoom digital." if region is not None else "Analizando el cuadro completo."
    notify(parent, title, done)
    return True


def _save_and_restart(parent: QWidget, config, params: dict, title: str) -> bool:
    previous = dict(config.params or {})
    device = repository.get_device(config.device_id)
    updated = repository.upsert_analytics_config(
        config.device_id, config.analyzer_name, params=params
    )
    ok = True
    if updated.enabled and device is not None:
        try:
            incident_rules.ensure_incident_rule(updated)
            analytics_engine.start(updated, device)
        except Exception as exc:  # noqa: BLE001 - config invalida o modelo que no carga
            logger.exception("Zoom digital: la analítica %s no arrancó", config.id)
            restored = repository.upsert_analytics_config(
                config.device_id, config.analyzer_name, params=previous
            )
            try:
                analytics_engine.start(restored, device)
            except Exception:  # noqa: BLE001 - ya se avisa abajo
                logger.exception("Zoom digital: no se pudo volver a la configuración anterior")
            warn(parent, title, f"No se pudo reiniciar la analítica ({exc}). Quedó como estaba.")
            ok = False
    event_bus.analytics_config_changed.emit(config.device_id)
    return ok
