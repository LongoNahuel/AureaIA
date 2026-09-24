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

**Perder la clave no puede costar las credenciales** (Fase 3, 2026-09-24).
Antes, si `camera_key` faltaba, el primer `cifrar` creaba una nueva en
silencio: las contraseñas guardadas quedaban ilegibles para siempre, cada
lectura devolvia "" (y el stream conectaba con contraseña VACIA, que es como
una camara bloquea la IP), y guardar el dispositivo desde el dialogo pisaba
el token con el vacio. Ahora:

- Si la clave falta y hay credenciales cifradas, o el archivo esta vacio o
  truncado, o existe pero no descifra NINGUNA credencial de la base (es otra
  clave), el estado pasa a CLAVE_PERDIDA. No se crea una clave nueva.
- `descifrar` de algo que no se puede descifrar devuelve **el token
  intacto** (`enc1:...`) y avisa una sola vez. Un valor que sigue cifrado
  despues de descifrar es una credencial ilegible (`es_ilegible`); quien
  conecta a la camara lo mira y no usa una contraseña basura. Y como
  `cifrar` es idempotente, guardar el dispositivo sin tocar la contraseña
  conserva el token: si despues aparece la clave vieja, todo vuelve a andar.
- `cifrar` una contraseña nueva levanta `ClavePerdidaError`: cifrarla con
  una clave nueva mezclaria dos claves en la misma base.
- Salir de ese estado es una decision explicita: restaurar la clave (hay una
  copia junto a cada backup de la base, ver migrations/resguardo.py) o
  `regenerar_clave()`, que confirma el aviso del arranque (main.py).
"""

from __future__ import annotations

import logging
import os
import stat
import threading
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from aurea_vms.config.settings import settings

logger = logging.getLogger(__name__)

# Marca de version del formato. Sirve para dos cosas: distinguir un valor
# cifrado de una fila legada en texto plano (asi la migracion puede ser
# perezosa) y poder cambiar de esquema de cifrado mas adelante sin adivinar.
PREFIJO = "enc1:"

KEY_FILENAME = "camera_key"

ESTADO_OK = "ok"
ESTADO_CLAVE_PERDIDA = "clave_perdida"


class ClavePerdidaError(RuntimeError):
    """No hay una clave valida para cifrar: ver el docstring del modulo."""


# Todo el estado del modulo se escribe bajo este lock: la clave la pueden
# pedir a la vez el hilo de la GUI (guardar un dispositivo) y los workers
# (leer camaras), y crearla dos veces a la vez eran dos claves distintas.
_lock = threading.Lock()
_fernet: Fernet | None = None
_estado = ESTADO_OK
_motivo: str | None = None
_ilegibles = 0
_avisado = False


def _key_path() -> Path:
    return settings.data_dir / KEY_FILENAME


def _marcar_perdida(motivo: str) -> None:
    """Llamar con _lock tomado."""
    global _estado, _motivo, _fernet
    _estado = ESTADO_CLAVE_PERDIDA
    _motivo = motivo
    _fernet = None


def _leer_clave(ruta: Path) -> Fernet | None:
    """La clave del archivo, o None si el archivo no es una clave Fernet
    (vacio, truncado, basura). Nunca levanta ValueError hacia arriba."""
    try:
        return Fernet(ruta.read_bytes().strip())
    except (ValueError, TypeError):
        return None


def _crear_clave(ruta: Path) -> Fernet:
    """Crea la clave de forma atomica y sin pisar una ajena.

    Se escribe a un temporal propio (0600 desde el open: entre un write y un
    chmod hay una ventana en la que la clave es legible por cualquiera), con
    fsync, y se publica con os.link: falla si `ruta` ya existe, asi que si
    otro proceso la creo primero gana la suya y esta se descarta. Nunca queda
    a la vista un archivo a medio escribir.
    """
    settings.ensure_dirs()
    temporal = ruta.with_name(f".{ruta.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    descriptor = os.open(
        temporal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR
    )
    try:
        with os.fdopen(descriptor, "wb") as archivo:
            archivo.write(Fernet.generate_key())
            archivo.flush()
            os.fsync(archivo.fileno())
        try:
            os.link(temporal, ruta)
            logger.info("Clave de credenciales creada en %s", ruta)
        except FileExistsError:
            logger.info("Otra instancia creó la clave de credenciales primero: se usa esa")
    finally:
        temporal.unlink(missing_ok=True)
    fernet = _leer_clave(ruta)
    if fernet is None:
        _marcar_perdida(f"la clave de {ruta} está vacía o dañada")
        raise ClavePerdidaError(_motivo)
    return fernet


def _cifrador(*, crear: bool) -> Fernet:
    """La clave cargada. `crear=False` (descifrar) nunca crea una: sin clave
    no hay nada que descifrar, y crearla ahi era perder las credenciales."""
    global _fernet
    with _lock:
        if _estado == ESTADO_CLAVE_PERDIDA:
            raise ClavePerdidaError(_motivo)
        if _fernet is not None:
            return _fernet
        ruta = _key_path()
        if ruta.exists():
            fernet = _leer_clave(ruta)
            if fernet is None:
                _marcar_perdida(f"la clave de {ruta} está vacía o dañada")
                raise ClavePerdidaError(_motivo)
        elif crear:
            fernet = _crear_clave(ruta)
        else:
            _marcar_perdida(f"no existe la clave {ruta}")
            raise ClavePerdidaError(_motivo)
        _fernet = fernet
        return fernet


def verificar(tokens: list[str]) -> str:
    """Evalua la clave contra las credenciales cifradas de la base. La llama
    `init_db` despues de migrar, con los valores crudos de la columna.

    Devuelve el estado. Sin tokens y sin archivo no pasa nada: la clave se
    crea recien cuando se guarde la primera contraseña.
    """
    global _fernet, _estado, _motivo, _ilegibles, _avisado
    with _lock:
        _fernet, _estado, _motivo, _ilegibles, _avisado = None, ESTADO_OK, None, 0, False
        ruta = _key_path()
        if not ruta.exists():
            if tokens:
                _ilegibles = len(tokens)
                _marcar_perdida(f"no existe la clave {ruta}")
        else:
            fernet = _leer_clave(ruta)
            if fernet is None:
                _ilegibles = len(tokens)
                _marcar_perdida(f"la clave de {ruta} está vacía o dañada")
            else:
                _ilegibles = sum(not _descifra(fernet, token) for token in tokens)
                if tokens and _ilegibles == len(tokens):
                    _marcar_perdida(
                        f"la clave de {ruta} no descifra ninguna credencial de la base "
                        "(es otra clave)"
                    )
                else:
                    _fernet = fernet
                    if _ilegibles:
                        logger.error(
                            "%d credencial(es) de cámara no se pueden descifrar con %s: "
                            "hay que volver a cargarlas",
                            _ilegibles,
                            ruta,
                        )
        if _estado == ESTADO_CLAVE_PERDIDA:
            logger.error(
                "Clave de credenciales perdida: %s. %d cámara(s) quedan sin conectar.",
                _motivo,
                _ilegibles,
            )
        return _estado


def _descifra(fernet: Fernet, token: str) -> bool:
    try:
        fernet.decrypt(token[len(PREFIJO) :].encode("ascii"))
    except (InvalidToken, ValueError):
        return False
    return True


def estado() -> str:
    return _estado


def motivo() -> str | None:
    return _motivo


def credenciales_ilegibles() -> int:
    """Cuantas credenciales de la base no se pudieron descifrar en el ultimo
    `verificar`."""
    return _ilegibles


def regenerar_clave() -> Path | None:
    """Sale de CLAVE_PERDIDA creando una clave nueva. Las credenciales
    guardadas siguen ilegibles (hay que volver a cargarlas); solo vuelve a
    ser posible guardar contraseñas.

    Un archivo de clave existente (dañado, o de otra instalacion) no se
    borra: se renombra a `camera_key.reemplazada-<fecha>` y se devuelve su
    ruta, por si resulta ser la buena.
    """
    global _fernet, _estado, _motivo, _avisado
    with _lock:
        ruta = _key_path()
        apartada = None
        if ruta.exists():
            apartada = ruta.with_name(
                f"{ruta.name}.reemplazada-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            )
            os.replace(ruta, apartada)
        _estado, _motivo, _avisado = ESTADO_OK, None, False
        _fernet = _crear_clave(ruta)
        logger.warning("Clave de credenciales regenerada; la anterior: %s", apartada)
        return apartada


def reset_cache() -> None:
    """Olvida la clave cargada y el estado. Para tests, y para cuando cambia
    el data-dir."""
    global _fernet, _estado, _motivo, _ilegibles, _avisado
    with _lock:
        _fernet, _estado, _motivo, _ilegibles, _avisado = None, ESTADO_OK, None, 0, False


def esta_cifrado(valor: str) -> bool:
    return valor.startswith(PREFIJO)


def es_ilegible(valor: str | None) -> bool:
    """Para un valor YA pasado por `descifrar` (p. ej. `device.password`):
    si sigue cifrado, es que no se pudo descifrar. Quien conecta a una
    camara no tiene que usarlo como contraseña."""
    return bool(valor) and esta_cifrado(valor)


def cifrar(valor: str) -> str:
    """Texto plano -> token. Un valor vacio se deja como esta: cifrar la
    cadena vacia solo gasta bytes y delata que el campo existe. Un token
    se deja como esta (idempotente): asi se conserva una credencial que no
    se pudo descifrar cuando se guarda el dispositivo sin tocarla.

    Levanta ClavePerdidaError si no hay una clave valida."""
    if not valor:
        return valor
    if esta_cifrado(valor):
        return valor
    return PREFIJO + _cifrador(crear=True).encrypt(valor.encode("utf-8")).decode("ascii")


def _avisar_una_vez(detalle: str) -> None:
    global _avisado
    with _lock:
        if _avisado:
            return
        _avisado = True
    logger.error(
        "No se pudo descifrar una credencial de cámara (%s). Se deja cifrada y esa "
        "cámara no conecta hasta restaurar la clave o volver a cargar la contraseña. "
        "No se vuelve a avisar en esta sesión.",
        detalle,
    )


def descifrar(valor: str) -> str:
    """Token -> texto plano. Una fila legada (sin el prefijo) se devuelve tal
    cual: la migracion a cifrado es perezosa, cada fila se convierte cuando
    se la vuelve a guardar.

    Si no se puede descifrar, devuelve el token intacto (ver `es_ilegible`)
    en vez de levantar: una credencial ilegible no puede dejar la lista de
    camaras sin abrir."""
    if not valor or not esta_cifrado(valor):
        return valor
    try:
        fernet = _cifrador(crear=False)
    except ClavePerdidaError:
        _avisar_una_vez(_motivo or "clave perdida")
        return valor
    try:
        return fernet.decrypt(valor[len(PREFIJO) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        _avisar_una_vez(f"la clave de {_key_path()} no corresponde")
        return valor
