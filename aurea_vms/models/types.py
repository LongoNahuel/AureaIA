"""Tipos de columna propios.

`EncryptedString` mantiene el cifrado donde corresponde: en la frontera con
la base. En Python el valor sigue siendo texto plano, asi que los doce
lugares que leen `device.password` -- stream_manager para armar la URL RTSP,
ptz_control, el dialogo de dispositivo, el descubrimiento ONVIF -- no saben
que existe y no cambiaron ni una linea.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from aurea_vms.core import credential_store


class EncryptedString(TypeDecorator):
    """Cifrado al escribir, descifrado al leer. Ver core/credential_store.py
    para el alcance real de esa proteccion."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return credential_store.cifrar(value) if value else value

    def process_result_value(self, value, dialect):
        return credential_store.descifrar(value) if value else value
