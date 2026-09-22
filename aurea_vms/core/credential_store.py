"""Cifrado en reposo de las credenciales de camara.

**Que protege y que no, sin adornos.** La clave vive en un archivo al lado
de la base, con permisos de solo-el-duenio. Contra alguien que ya entro al
equipo con el usuario de la app, esto NO es secreto: la clave esta ahi al
lado. Lo que si hace, y no es poco:

- Sube la barrera de "abrir el .sqlite3 con cualquier visor y leer las
  contraseñas" a "ademas saber donde esta la clave y como se usa".
- Saca las credenciales de los backups de la base, que es por donde se
  filtran de verdad: una copia del .sqlite3 mandada por mail o subida a un
  drive ya no lleva las contraseñas de las camaras adentro.
- Evita que aparezcan en claro en un volcado, un log de SQL o una
  inspeccion casual durante un soporte remoto.

Secreto de verdad contra acceso fisico al equipo necesitaria que el usuario
tipee una master password al arrancar. En un VMS de sala que tiene que
levantar solo despues de un corte de luz, eso es un problema operativo
serio, asi que queda como decision consciente (ver ROADMAP).

El cifrado se aplica solo en la frontera con la base, via el TypeDecorator
`EncryptedString` de models/types.py: en Python `device.password` sigue
siendo texto plano y ninguno de los doce lugares que lo leen cambio.
"""

from __future__ import annotations

import logging
import os
import stat

from cryptography.fernet import Fernet, InvalidToken

from aurea_vms.config.settings import settings

logger = logging.getLogger(__name__)

# Marca de version del formato. Sirve para dos cosas: distinguir un valor
# cifrado de una fila legada en texto plano (asi la migracion puede ser
# perezosa) y poder cambiar de esquema de cifrado mas adelante sin adivinar.
PREFIJO = "enc1:"

KEY_FILENAME = "camera_key"

_fernet: Fernet | None = None


def _key_path():
    return settings.data_dir / KEY_FILENAME


def _cargar_o_crear_clave() -> bytes:
    ruta = _key_path()
    if ruta.exists():
        return ruta.read_bytes().strip()

    clave = Fernet.generate_key()
    settings.ensure_dirs()
    # Se escribe con los permisos ya puestos, no despues: entre el write y
    # el chmod hay una ventana en la que la clave es legible por cualquiera.
    descriptor = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(descriptor, "wb") as archivo:
        archivo.write(clave)
    logger.info("Clave de credenciales creada en %s", ruta)
    return clave


def _cifrador() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_cargar_o_crear_clave())
    return _fernet


def reset_cache() -> None:
    """Olvida la clave cargada. Para tests, y para cuando cambia el data-dir."""
    global _fernet
    _fernet = None


def esta_cifrado(valor: str) -> bool:
    return valor.startswith(PREFIJO)


def cifrar(valor: str) -> str:
    """Texto plano -> token. Un valor vacio se deja como esta: cifrar la
    cadena vacia solo gasta bytes y delata que el campo existe."""
    if not valor:
        return valor
    if esta_cifrado(valor):
        return valor  # idempotente: no re-cifrar lo ya cifrado
    return PREFIJO + _cifrador().encrypt(valor.encode("utf-8")).decode("ascii")


def descifrar(valor: str) -> str:
    """Token -> texto plano. Una fila legada (sin el prefijo) se devuelve tal
    cual: la migracion a cifrado es perezosa, cada fila se convierte cuando
    se la vuelve a guardar."""
    if not valor or not esta_cifrado(valor):
        return valor
    try:
        return _cifrador().decrypt(valor[len(PREFIJO) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Clave perdida o cambiada. Se devuelve vacio a proposito en vez de
        # levantar: que no se pueda descifrar UNA contraseña no puede dejar
        # la lista de camaras sin abrir. El operador la vuelve a cargar.
        logger.error(
            "No se pudo descifrar una credencial: la clave de %s no corresponde. "
            "Hay que volver a cargar la contraseña de esa cámara.",
            _key_path(),
        )
        return ""
