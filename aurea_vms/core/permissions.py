"""Sistema de permisos por rol.

Cuatro roles fijos con una matriz de permisos predefinida (decision de
producto para esta fase: "multiusuario configurable" = elegir el rol del
usuario; la matriz editable por usuario -- una columna JSON que
overridee el rol -- queda como evolucion, ver ROADMAP).

Los 8 permisos estan calcados de la matriz del prototipo NOVA. El
chequeo se hace en la UI (visibilidad de modulos y botones): para un
cliente de escritorio monolitico alcanza; si algun dia hay una capa
cliente/servidor, el enforcement debe repetirse en el servidor.

Este modulo importa auth (para el usuario en sesion); auth NO importa
permissions -- sin ciclos.
"""

from __future__ import annotations

from enum import StrEnum

from aurea_vms.core import auth
from aurea_vms.models.user import (
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_OPERATOR,
    ROLE_SUPERVISOR,
    User,
)


class Perm(StrEnum):
    LIVE_VIEW = "live_view"  # ver video en vivo
    RECORDINGS = "recordings"  # ver el feed de alarmas/grabaciones/evidencia
    ALARM_MANAGE = "alarm_manage"  # reconocer/investigar/resolver alertas
    EVIDENCE_EXPORT = "evidence_export"  # exportar evidencia
    ANALYTICS_CONFIG = "analytics_config"  # configurar analiticas y reglas de alerta
    DEVICE_ADMIN = "device_admin"  # administrar camaras/dispositivos
    USER_ADMIN = "user_admin"  # administrar usuarios
    GLOBAL_CONFIG = "global_config"  # configuracion global (modulo Sistema)


ROLE_PERMISSIONS: dict[str, frozenset[Perm]] = {
    ROLE_ADMIN: frozenset(Perm),
    ROLE_SUPERVISOR: frozenset(
        {
            Perm.LIVE_VIEW,
            Perm.RECORDINGS,
            Perm.ALARM_MANAGE,
            Perm.EVIDENCE_EXPORT,
            Perm.ANALYTICS_CONFIG,
        }
    ),
    ROLE_OPERATOR: frozenset({Perm.LIVE_VIEW, Perm.RECORDINGS, Perm.ALARM_MANAGE}),
    # Auditor: solo-lectura + exportacion. Ve el historial y arma legajos,
    # pero no opera (no reconoce alertas) ni configura nada.
    ROLE_AUDITOR: frozenset({Perm.RECORDINGS, Perm.EVIDENCE_EXPORT}),
}


def can(perm: Perm, user: User | None = None) -> bool:
    """True si el usuario (o el logueado en sesion) tiene el permiso. Un
    rol desconocido no tiene ninguno: fallar cerrado."""
    target = user if user is not None else auth.current_user
    if target is None:
        return False
    return perm in ROLE_PERMISSIONS.get(target.role, frozenset())
