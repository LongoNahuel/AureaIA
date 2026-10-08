"""Traduccion de valores internos a texto en español para mostrar en la
UI. Los valores internos (nombres de clase de YOLO, estados de
dispositivo) se guardan/comparan en su forma original en ingles -- solo
se traducen al momento de mostrarlos."""

from __future__ import annotations

from aurea_vms.models.alarm_event import (
    STATUS_ACKNOWLEDGED,
    STATUS_INVESTIGATING,
    STATUS_NEW,
    STATUS_RESOLVED,
)

# Estado de un incidente. "Sin reconocer" es STATUS_NEW en todas las
# pantallas (antes Inicio decia "sin reconocer", Vista Inteligente "activas"
# y el Dashboard "Pendiente" para todo lo no resuelto).
ALARM_STATUS_LABELS: dict[str, str] = {
    STATUS_NEW: "Sin reconocer",
    STATUS_ACKNOWLEDGED: "Reconocida",
    STATUS_INVESTIGATING: "En investigación",
    STATUS_RESOLVED: "Resuelta",
}
SEVERITY_LABELS: dict[str, str] = {
    "critico": "Crítico",
    "alto": "Alto",
    "medio": "Medio",
    "info": "Info",
}

CLASS_LABELS_ES: dict[str, str] = {
    "person": "Persona",
    "car": "Auto",
    "motorcycle": "Moto",
    "bicycle": "Bicicleta",
    "bus": "Colectivo",
    "truck": "Camión",
    "patada_monitor": "Patada al monitor",
    "golpe_monitor": "Golpe al monitor",
    "consumo_sustancias": "Consumo de sustancias",
    "preparacion_consumo": "Preparación de consumo (alerta previa)",
    "fichas_movidas": "Fichas movidas",
    "fichas_tras_no_va_mas": "Fichas tras el no va más",
    "cara": "Cara",
}

# Cambio del plato de la ruleta que abrio una jugada nueva ("la rueda ...").
ROTOR_EVENT_ES: dict[str, str] = {
    "sentido": "cambió de sentido",
    "impulso": "se re-impulsó",
    "frenado": "frenó",
}

DEVICE_STATUS_ES: dict[str, str] = {
    "online": "En línea",
    "offline": "Desconectado",
    "unknown": "Desconocido",
}


def display_class(label: str) -> str:
    return CLASS_LABELS_ES.get(label, label)


def display_rotor_event(kind: str | None) -> str:
    return ROTOR_EVENT_ES.get(kind or "", "se movió")


def display_status(status: str) -> str:
    return DEVICE_STATUS_ES.get(status, status)
