from __future__ import annotations

import pytest
from qfluentwidgets import PushButton

from aurea_vms.core import auth
from aurea_vms.models.user import (
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_OPERATOR,
    ROLE_SUPERVISOR,
    User,
)
from aurea_vms.ui.main_window import MODULE_PERMISSIONS, MODULES, compute_visible_categories


def _login_como(role: str) -> None:
    auth.current_user = User(username=f"u-{role}", password_hash="h", salt="s", role=role)


class TestCategoriasVisibles:
    def test_todos_los_modulos_tienen_permiso_mapeado(self):
        assert {label for label, *_ in MODULES} == set(MODULE_PERMISSIONS)

    def test_admin_ve_todo(self):
        _login_como(ROLE_ADMIN)
        visible = compute_visible_categories()
        assert visible["Operación"] == ["Vista en Vivo", "Alarmas"]
        assert set(visible["Configuración"]) == {
            "Dispositivos",
            "Analizadores",
            "Alertas",
            "Sistema",
            "Usuarios",
            "Sitios y Zonas",
        }

    def test_supervisor_configura_analiticas_pero_no_administra(self):
        _login_como(ROLE_SUPERVISOR)
        visible = compute_visible_categories()
        assert visible["Operación"] == ["Vista en Vivo", "Alarmas"]
        assert set(visible["Configuración"]) == {"Analizadores", "Alertas"}

    def test_operador_solo_operacion(self):
        _login_como(ROLE_OPERATOR)
        assert compute_visible_categories() == {"Operación": ["Vista en Vivo", "Alarmas"]}

    def test_auditor_solo_alarmas(self):
        _login_como(ROLE_AUDITOR)
        assert compute_visible_categories() == {"Operación": ["Alarmas"]}

    def test_sin_sesion_nada(self):
        auth.current_user = None
        assert compute_visible_categories() == {}


@pytest.fixture()
def alarm_module(qtbot, temp_db):
    from aurea_vms.ui.modules.alarm_module import AlarmModule

    def _crear() -> dict[str, PushButton]:
        module = AlarmModule()
        qtbot.addWidget(module)
        return {button.text(): button for button in module.findChildren(PushButton)}

    return _crear


class TestGatesEnModuloAlarmas:
    def test_auditor_exporta_pero_no_gestiona(self, alarm_module):
        _login_como(ROLE_AUDITOR)
        buttons = alarm_module()

        assert buttons["Exportar evidencia"].isEnabled()
        assert not buttons["Reconocer"].isEnabled()
        assert not buttons["En investigación"].isEnabled()
        assert not buttons["Resolver"].isEnabled()

    def test_operador_gestiona_pero_no_exporta(self, alarm_module):
        _login_como(ROLE_OPERATOR)
        buttons = alarm_module()

        assert buttons["Reconocer"].isEnabled()
        assert not buttons["Exportar evidencia"].isEnabled()

    def test_admin_todo_habilitado(self, alarm_module):
        _login_como(ROLE_ADMIN)
        buttons = alarm_module()

        assert buttons["Reconocer"].isEnabled()
        assert buttons["Exportar evidencia"].isEnabled()
