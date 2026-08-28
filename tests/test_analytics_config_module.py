from __future__ import annotations

import pytest

import aurea_vms.ui.modules.analytics_config as ac_module
from aurea_vms.models import repository
from aurea_vms.ui.modules.analytics_config import AnalyticsConfigModule


class FakeAnalyticsEngine:
    def __init__(self) -> None:
        self.started: list[tuple[int, int]] = []
        self.stopped: list[int] = []

    def start(self, config, device) -> None:
        self.started.append((config.id, device.id))

    def stop(self, config_id: int) -> None:
        self.stopped.append(config_id)

    def is_running(self, config_id: int) -> bool:
        return False


@pytest.fixture()
def fake_engine(monkeypatch):
    engine = FakeAnalyticsEngine()
    monkeypatch.setattr(ac_module, "analytics_engine", engine)
    return engine


def _device():
    return repository.add_device(name="Cam 1", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")


def _module_with_device(qtbot, device):
    module = AnalyticsConfigModule()
    qtbot.addWidget(module)
    module.device_selector.setCurrentIndex(module.device_selector.findData(device.id))
    return module


class TestSwitchDeHabilitado:
    def test_switch_deshabilitado_sin_configuracion_previa(self, qtbot, temp_db, fake_engine):
        device = _device()
        module = _module_with_device(qtbot, device)

        switch = module.table.cellWidget(0, 1)  # motion_detection: primer analizador
        assert switch.isEnabled() is False

    def test_switch_refleja_una_analitica_ya_habilitada(self, qtbot, temp_db, fake_engine):
        device = _device()
        repository.upsert_analytics_config(device.id, "motion_detection", enabled=True)
        module = _module_with_device(qtbot, device)

        switch = module.table.cellWidget(0, 1)
        assert switch.isEnabled() is True
        assert switch.isChecked() is True

    def test_apagar_el_switch_deshabilita_sin_abrir_el_dialogo(self, qtbot, temp_db, fake_engine):
        device = _device()
        config = repository.upsert_analytics_config(device.id, "motion_detection", enabled=True)
        module = _module_with_device(qtbot, device)

        module.table.cellWidget(0, 1).setChecked(False)

        assert repository.get_analytics_config(config.id).enabled is False
        assert config.id in fake_engine.stopped

    def test_prender_el_switch_habilita_y_arranca_el_engine(self, qtbot, temp_db, fake_engine):
        device = _device()
        config = repository.upsert_analytics_config(device.id, "motion_detection", enabled=False)
        module = _module_with_device(qtbot, device)

        module.table.cellWidget(0, 1).setChecked(True)

        assert repository.get_analytics_config(config.id).enabled is True
        assert (config.id, device.id) in fake_engine.started
