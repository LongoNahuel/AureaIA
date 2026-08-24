"""Traduccion de valores internos a texto en español para mostrar en la
UI. Los valores internos (nombres de clase de YOLO, estados de
dispositivo) se guardan/comparan en su forma original en ingles -- solo
se traducen al momento de mostrarlos."""

from __future__ import annotations

CLASS_LABELS_ES: dict[str, str] = {
    "person": "Persona",
    "car": "Auto",
    "motorcycle": "Moto",
    "bicycle": "Bicicleta",
    "bus": "Colectivo",
    "truck": "Camión",
    "movimiento": "Movimiento",
    "cara": "Cara",
}

DEVICE_STATUS_ES: dict[str, str] = {
    "online": "En línea",
    "offline": "Desconectado",
    "unknown": "Desconocido",
}


def display_class(label: str) -> str:
    return CLASS_LABELS_ES.get(label, label)


def display_status(status: str) -> str:
    return DEVICE_STATUS_ES.get(status, status)
