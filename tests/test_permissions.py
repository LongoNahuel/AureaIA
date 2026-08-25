from __future__ import annotations

from aurea_vms.core import auth, permissions
from aurea_vms.core.permissions import Perm, can
from aurea_vms.models.user import (
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_LABELS,
    ROLE_OPERATOR,
    ROLE_SUPERVISOR,
    ROLES,
    User,
)


def _user(role: str) -> User:
    return User(username=f"u-{role}", password_hash="h", salt="s", role=role)


class TestMatriz:
    def test_admin_tiene_todo(self):
        assert all(can(perm, _user(ROLE_ADMIN)) for perm in Perm)

    def test_supervisor_opera_y_configura_analiticas_pero_no_administra(self):
        supervisor = _user(ROLE_SUPERVISOR)
        assert can(Perm.LIVE_VIEW, supervisor)
        assert can(Perm.ALARM_MANAGE, supervisor)
        assert can(Perm.ANALYTICS_CONFIG, supervisor)
        assert can(Perm.EVIDENCE_EXPORT, supervisor)
        assert not can(Perm.DEVICE_ADMIN, supervisor)
        assert not can(Perm.USER_ADMIN, supervisor)
        assert not can(Perm.GLOBAL_CONFIG, supervisor)

    def test_operador_solo_opera(self):
        operador = _user(ROLE_OPERATOR)
        assert can(Perm.LIVE_VIEW, operador)
        assert can(Perm.RECORDINGS, operador)
        assert can(Perm.ALARM_MANAGE, operador)
        assert not can(Perm.EVIDENCE_EXPORT, operador)
        assert not can(Perm.ANALYTICS_CONFIG, operador)

    def test_auditor_ve_y_exporta_pero_no_toca(self):
        auditor = _user(ROLE_AUDITOR)
        assert can(Perm.RECORDINGS, auditor)
        assert can(Perm.EVIDENCE_EXPORT, auditor)
        assert not can(Perm.ALARM_MANAGE, auditor)
        assert not can(Perm.LIVE_VIEW, auditor)
        assert not can(Perm.DEVICE_ADMIN, auditor)

    def test_todos_los_roles_tienen_matriz_y_label(self):
        assert set(ROLES) == set(permissions.ROLE_PERMISSIONS)
        assert set(ROLES) == set(ROLE_LABELS)


class TestSesionYFallos:
    def test_sin_sesion_no_hay_permisos(self):
        auth.current_user = None
        assert not can(Perm.LIVE_VIEW)

    def test_usa_el_usuario_en_sesion(self):
        auth.current_user = _user(ROLE_AUDITOR)
        assert can(Perm.EVIDENCE_EXPORT)
        assert not can(Perm.LIVE_VIEW)

    def test_rol_desconocido_falla_cerrado(self):
        fantasma = _user("superusuario-inventado")
        assert not any(can(perm, fantasma) for perm in Perm)
