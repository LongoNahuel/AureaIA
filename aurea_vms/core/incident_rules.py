"""Regla de alarma automatica de "Incidentes en casinos".

Un incidente solo queda en el historico (modulo Alarmas, con captura y clip)
si hay una regla de alarma para su camara. Hasta el 2026-10-02 habia una
sola, la de la #5 (Monitor roto), y las "fichas tras el no va mas" de la
ruleta no se guardaban en ningun lado. Decision de Nahuel: al habilitar
"Incidentes en casinos" en una camara, si no tiene ninguna regla de esa
analitica (propia o de "todas las camaras"), se crea sola una con todas las
clases de incidente y "guardar clip". Despues se edita en Alertas como
cualquier otra.
"""

from __future__ import annotations

import copy
import logging

from aurea_vms.core.analytics.consumption_analyzer import LABEL_CONSUMPTION, LABEL_PREPARATION
from aurea_vms.core.analytics.monitor_tamper_analyzer import LABEL_HAND, LABEL_KICK
from aurea_vms.core.analytics.roulette_analyzer import LABEL_PAST_POST
from aurea_vms.models import repository
from aurea_vms.models.alarm_rule import SEVERITY_CRITICAL

logger = logging.getLogger(__name__)

ANALYZER = "monitor_tamper"
# Todo lo que puede disparar "Incidentes en casinos", en sus cuatro modos.
INCIDENT_CLASSES = [
    LABEL_KICK,
    LABEL_HAND,
    LABEL_CONSUMPTION,
    LABEL_PREPARATION,
    LABEL_PAST_POST,
]
DEFAULT_RULE = {
    "object_classes": INCIDENT_CLASSES,
    # Los golpes salen con la confianza del keypoint, desde 0,30; el
    # past-post con 1,0.
    "min_confidence": 0.3,
    # Dos past-posts seguidos en una misma tirada son dos incidentes.
    "cooldown_seconds": 5,
    "severity": SEVERITY_CRITICAL,
    "schedule_days": [],  # sin restriccion: siempre
    "actions": {
        "notify_ui": True,
        "save_clip": True,
        "notify_desktop": True,
        "play_sound": False,
    },
    "enabled": True,
}


def has_incident_rule(device_id: int) -> bool:
    """Hay alguna regla (habilitada o no) de la camara o de todas las camaras."""
    return any(
        rule.analyzer_name == ANALYZER and rule.device_id in (device_id, None)
        for rule in repository.list_alarm_rules()
    )


def ensure_incident_rule(config) -> bool:
    """Si `config` es "Incidentes en casinos" y su camara no tiene regla,
    crea la de DEFAULT_RULE. Devuelve True si la creo. Nunca levanta: no
    poder crear la regla no puede impedir que arranque la analitica."""
    if config.analyzer_name != ANALYZER:
        return False
    try:
        if has_incident_rule(config.device_id):
            return False
        repository.add_alarm_rule(
            device_id=config.device_id, analyzer_name=ANALYZER, **copy.deepcopy(DEFAULT_RULE)
        )
    except Exception:
        logger.exception(
            "No se pudo crear la regla de incidentes de la camara %s", config.device_id
        )
        return False
    logger.info("Camara %s: regla de alarma de incidentes creada", config.device_id)
    return True
