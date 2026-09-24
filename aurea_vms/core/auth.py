"""Autenticacion local: multi-usuario con roles simples (admin/operador).

Contraseña con PBKDF2-HMAC-SHA256 + salt aleatorio por usuario; se usa la
stdlib para no sumar una dependencia nueva solo para esto.

El hash se guarda en formato **auto-descriptivo**,
`pbkdf2_sha256$<iteraciones>$<salt>$<hash>` -- el mismo que usan Django y
passlib. Que el coste viaje adentro del hash es lo que permite subirlo sin
resetearle la contraseña a nadie: `authenticate` valida contra los
parametros con los que se calculo ESE hash y, si quedaron viejos, lo
re-calcula al vuelo con los actuales. Antes las iteraciones eran una
constante del modulo, asi que subirlas invalidaba todas las contraseñas
existentes -- que es la razon por la que nadie las subia.

El "usuario actual" es un estado de sesion en memoria (a nivel de modulo,
no persistido) -- esta app es de un solo proceso con un solo usuario
logueado a la vez, asi que alcanza con una variable global en vez de
pasar el usuario logueado por todos lados."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time

from aurea_vms.models import repository
from aurea_vms.models.user import ROLE_ADMIN, ROLES, User

logger = logging.getLogger(__name__)

ALGORITMO = "pbkdf2_sha256"
# Minimo OWASP vigente para PBKDF2-HMAC-SHA256. Venia de 260.000, menos de
# la mitad. Subir este numero ya NO invalida nada: los hashes viejos se
# validan con su propio coste y se re-calculan al siguiente login.
ITERACIONES = 600_000
# Con cuantas iteraciones se calcularon los hashes del formato viejo. Es un
# dato historico fijo: el formato legado no las guardaba en ningun lado.
ITERACIONES_LEGADAS = 260_000
PASSWORD_MIN_LENGTH = 9

# Lockout: N intentos fallidos bloquean la cuenta M segundos. Por ventana de
# tiempo y nunca permanente -- ver el comentario de User.locked_until.
MAX_INTENTOS = 5
BLOQUEO_SEGUNDOS = 15 * 60
# Un hash que dice llevar mas iteraciones que esto es un hash corrupto o
# plantado: verificarlo congelaria el login (1e12 iteraciones son horas).
ITERACIONES_MAXIMAS = 10 * ITERACIONES
# Salt fijo para el PBKDF2 "de relleno" de un usuario que no existe: lo que
# importa es gastar el mismo tiempo, no el resultado.
_SALT_DE_RELLENO = b"\x00" * 16

__all__ = ["ROLES"]  # re-export por compatibilidad

current_user: User | None = None


class CuentaBloqueada(Exception):
    """Demasiados intentos fallidos. Lleva los segundos que faltan para que
    el bloqueo venza, para que la UI pueda decir algo util."""

    def __init__(self, segundos_restantes: float) -> None:
        super().__init__(f"Cuenta bloqueada por {segundos_restantes:.0f} segundos")
        self.segundos_restantes = segundos_restantes


def _hash_password(password: str, salt: bytes, iteraciones: int | None = None) -> str:
    # El default se lee ACA y no en la firma: `iteraciones=ITERACIONES` ata
    # el valor al definirse la funcion, asi que subir la constante despues
    # (o bajarla en los tests) no tendria ningun efecto -- y el sintoma es
    # un hash calculado con un coste distinto al que dice llevar adentro.
    if iteraciones is None:
        iteraciones = ITERACIONES
    derivada = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iteraciones)
    return f"{ALGORITMO}${iteraciones}${salt.hex()}${derivada.hex()}"


def _nuevo_hash(password: str) -> tuple[str, str]:
    """(hash, salt) para guardar. El salt va dentro del hash; la columna
    `salt` queda vacia y existe solo por las filas legadas."""
    return _hash_password(password, os.urandom(16)), ""


def _verificar(password: str, user: User) -> tuple[bool, bool]:
    """(la contraseña es correcta, hay que re-calcular el hash).

    Soporta los dos formatos: el actual y el legado (hex pelado de 260.000
    iteraciones con el salt en su propia columna), para no obligar a nadie a
    resetear su contraseña.
    """
    guardado = user.password_hash or ""

    if guardado.count("$") == 3:
        algoritmo, iteraciones_txt, salt_hex, _esperado = guardado.split("$")
        if algoritmo != ALGORITMO:
            logger.error("Usuario %s: algoritmo de hash desconocido %r", user.username, algoritmo)
            return False, False
        # Un hash corrupto es "contraseña incorrecta", no un ValueError que
        # tumba el dialogo de login.
        try:
            iteraciones = int(iteraciones_txt)
            salt = bytes.fromhex(salt_hex)
        except ValueError:
            logger.error("Usuario %s: hash de contraseña ilegible", user.username)
            return False, False
        if not 0 < iteraciones <= ITERACIONES_MAXIMAS:
            logger.error(
                "Usuario %s: el hash dice llevar %d iteraciones, fuera de rango",
                user.username,
                iteraciones,
            )
            return False, False
        calculado = _hash_password(password, salt, iteraciones)
        # compare_digest y no !=: la comparacion de strings corta en el
        # primer byte distinto y filtra informacion por tiempo.
        correcta = hmac.compare_digest(calculado, guardado)
        return correcta, correcta and iteraciones < ITERACIONES

    # Formato legado: hex pelado + columna salt, siempre 260.000 iteraciones.
    if not user.salt:
        return False, False
    try:
        salt_legado = bytes.fromhex(user.salt)
    except ValueError:
        logger.error("Usuario %s: salt legado ilegible", user.username)
        return False, False
    legado = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_legado, ITERACIONES_LEGADAS
    ).hex()
    correcta = hmac.compare_digest(legado, guardado)
    return correcta, correcta


def _segundos_de_bloqueo(user: User) -> float:
    """Con tope en BLOQUEO_SEGUNDOS: si el reloj del equipo retrocede (un
    NTP que corrige, una pila de BIOS agotada que vuelve a 2001), un
    `locked_until` del "futuro" dejaba la cuenta bloqueada dias o años. El
    peor caso ahora es el bloqueo normal."""
    if not user.locked_until:
        return 0.0
    return min(max(0.0, user.locked_until - time.time()), float(BLOQUEO_SEGUNDOS))


def _gastar_como_si_verificara(password: str) -> None:
    """Un PBKDF2 con el coste vigente cuyo resultado se descarta. Sin esto,
    "el usuario no existe" respondia en microsegundos y "contraseña
    incorrecta" en ~150ms: cronometrar el login decia que usuarios existen."""
    _hash_password(password, _SALT_DE_RELLENO)


def _registrar_fallo(user: User) -> None:
    if user.locked_until and _segundos_de_bloqueo(user) == 0:
        # El bloqueo anterior ya vencio: se arranca de cero. Antes el
        # contador seguia en MAX_INTENTOS y el primer error despues de
        # esperar los 15 minutos volvia a bloquear la cuenta.
        repository.update_user(user.id, failed_attempts=0, locked_until=None)
    intentos = repository.sumar_intento_fallido(user.id)
    if intentos >= MAX_INTENTOS:
        repository.update_user(user.id, locked_until=time.time() + BLOQUEO_SEGUNDOS)
        logger.warning(
            "Usuario %s bloqueado %d minutos tras %d intentos fallidos",
            user.username,
            BLOQUEO_SEGUNDOS // 60,
            intentos,
        )


def _limpiar_fallos(user: User) -> None:
    if user.failed_attempts or user.locked_until:
        repository.update_user(user.id, failed_attempts=0, locked_until=None)


def has_admin_user() -> bool:
    return repository.count_users() > 0


def create_admin_user(username: str, password: str) -> User:
    """Alta del Super Administrador en el primer arranque (unico caso en
    que no hace falta estar logueado como admin para crear un usuario)."""
    password_hash, salt = _nuevo_hash(password)
    return repository.add_user(
        username=username, password_hash=password_hash, salt=salt, role=ROLE_ADMIN
    )


def create_user(username: str, password: str, role: str) -> User:
    """Levanta ValueError si la contraseña no cumple la politica: antes era
    el unico camino de alta que NO la validaba, a diferencia de
    change_password y admin_reset_password."""
    error = validate_password(password)
    if error:
        raise ValueError(error)
    password_hash, salt = _nuevo_hash(password)
    return repository.add_user(username=username, password_hash=password_hash, salt=salt, role=role)


def authenticate(username: str, password: str) -> User | None:
    """None si el usuario no existe o la contraseña es incorrecta.

    Levanta CuentaBloqueada si se agotaron los intentos: la UI necesita
    poder distinguir "te equivocaste" de "esperá 15 minutos".
    """
    user = repository.get_user_by_username(username)
    if user is None:
        _gastar_como_si_verificara(password)
        return None

    restantes = _segundos_de_bloqueo(user)
    if restantes > 0:
        raise CuentaBloqueada(restantes)

    correcta, hay_que_rehashear = _verificar(password, user)
    if not correcta:
        _registrar_fallo(user)
        return None

    _limpiar_fallos(user)
    if hay_que_rehashear:
        # Se aprovecha que acá tenemos la contraseña en claro, que es el
        # unico momento en que se puede re-calcular el hash.
        nuevo, salt = _nuevo_hash(password)
        repository.update_user(user.id, password_hash=nuevo, salt=salt)
        logger.info("Usuario %s: hash actualizado al coste vigente", user.username)
        user.password_hash, user.salt = nuevo, salt
    return user


def login(username: str, password: str) -> User | None:
    """Autentica y, si es correcto, marca ese usuario como el logueado en
    esta sesion. Devuelve el User o None si las credenciales fallan."""
    global current_user
    user = authenticate(username, password)
    if user is not None:
        current_user = user
    return user


def logout() -> None:
    global current_user
    current_user = None


def is_admin(user: User | None = None) -> bool:
    target = user if user is not None else current_user
    return target is not None and target.role == ROLE_ADMIN


def change_password(username: str, current_password: str, new_password: str) -> str | None:
    """Cambia la contraseña del usuario si la actual es correcta. Devuelve
    un mensaje de error, o None si el cambio se aplico."""
    # authenticate primero, tambien para un usuario que no existe: gasta el
    # PBKDF2 de relleno, asi que responde en el mismo tiempo que uno real.
    try:
        user = authenticate(username, current_password)
        if user is None:
            return "La contraseña actual no es correcta."
    except CuentaBloqueada as bloqueo:
        return f"Cuenta bloqueada. Volvé a intentar en {bloqueo.segundos_restantes / 60:.0f} min."
    error = validate_password(new_password)
    if error:
        return error
    password_hash, salt = _nuevo_hash(new_password)
    repository.update_user(user.id, password_hash=password_hash, salt=salt)
    return None


def admin_reset_password(user_id: int, new_password: str) -> str | None:
    """Un admin resetea la contraseña de otro usuario sin necesitar la
    actual (para cuando alguien se olvida la suya)."""
    error = validate_password(new_password)
    if error:
        return error
    password_hash, salt = _nuevo_hash(new_password)
    # Un reset de admin tambien levanta el bloqueo: si a alguien lo
    # bloquearon por olvidarse la contraseña, resetearsela y dejarlo
    # esperando 15 minutos igual no tiene ningun sentido.
    repository.update_user(
        user_id,
        password_hash=password_hash,
        salt=salt,
        failed_attempts=0,
        locked_until=None,
    )
    return None


def validate_password(password: str) -> str | None:
    """Devuelve un mensaje de error si la contraseña no cumple el minimo
    (misma regla que EZStation en su alta de Super Administrador: al menos
    9 caracteres combinando letras, numeros y simbolos), o None si es valida."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Debe tener al menos {PASSWORD_MIN_LENGTH} caracteres."
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_symbol = any(not c.isalnum() for c in password)
    if not (has_letter and has_digit and has_symbol):
        return "Debe combinar letras, números y símbolos."
    return None


def password_strength(password: str) -> str:
    """ "Débil" | "Media" | "Fuerte", para el indicador visual mientras se
    escribe (no bloquea nada por si sola, solo orienta)."""
    if not password:
        return ""
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_symbol = any(not c.isalnum() for c in password)
    criteria_met = sum([has_letter, has_digit, has_symbol])

    if len(password) >= PASSWORD_MIN_LENGTH and criteria_met == 3:
        return "Fuerte"
    if len(password) >= 6 and criteria_met >= 2:
        return "Media"
    return "Débil"
