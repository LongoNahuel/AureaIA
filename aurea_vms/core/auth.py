"""Autenticacion local: multi-usuario con roles simples (admin/operador).
Contraseña con PBKDF2-HMAC-SHA256 + salt aleatorio por usuario; se usa la
stdlib para no sumar una dependencia nueva solo para esto.

El "usuario actual" es un estado de sesion en memoria (a nivel de modulo,
no persistido) -- esta app es de un solo proceso con un solo usuario
logueado a la vez, asi que alcanza con una variable global en vez de
pasar el usuario logueado por todos lados."""

from __future__ import annotations

import hashlib
import os

from aurea_vms.models import repository
from aurea_vms.models.user import ROLE_ADMIN, ROLES, User

_ITERATIONS = 260_000
PASSWORD_MIN_LENGTH = 9

__all__ = ["ROLES"]  # re-export por compatibilidad

current_user: User | None = None


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS).hex()


def has_admin_user() -> bool:
    return repository.count_users() > 0


def create_admin_user(username: str, password: str) -> User:
    """Alta del Super Administrador en el primer arranque (unico caso en
    que no hace falta estar logueado como admin para crear un usuario)."""
    salt = os.urandom(16)
    password_hash = _hash_password(password, salt)
    return repository.add_user(
        username=username, password_hash=password_hash, salt=salt.hex(), role=ROLE_ADMIN
    )


def create_user(username: str, password: str, role: str) -> User:
    salt = os.urandom(16)
    password_hash = _hash_password(password, salt)
    return repository.add_user(
        username=username, password_hash=password_hash, salt=salt.hex(), role=role
    )


def authenticate(username: str, password: str) -> User | None:
    user = repository.get_user_by_username(username)
    if user is None:
        return None
    salt = bytes.fromhex(user.salt)
    if _hash_password(password, salt) != user.password_hash:
        return None
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
    user = repository.get_user_by_username(username)
    if user is None or authenticate(username, current_password) is None:
        return "La contraseña actual no es correcta."
    error = validate_password(new_password)
    if error:
        return error
    salt = os.urandom(16)
    repository.update_user(
        user.id, password_hash=_hash_password(new_password, salt), salt=salt.hex()
    )
    return None


def admin_reset_password(user_id: int, new_password: str) -> str | None:
    """Un admin resetea la contraseña de otro usuario sin necesitar la
    actual (para cuando alguien se olvida la suya)."""
    error = validate_password(new_password)
    if error:
        return error
    salt = os.urandom(16)
    repository.update_user(
        user_id, password_hash=_hash_password(new_password, salt), salt=salt.hex()
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
