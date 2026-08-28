from __future__ import annotations

from types import SimpleNamespace

import pytest

import aurea_vms.ui.modules.analytics_config as ac_module
from aurea_vms.models import repository
from aurea_vms.ui.dialogs.line_crossing_config_dialog import LineCrossingConfigDialog
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

    def test_si_el_engine_no_arranca_el_switch_revierte_y_avisa(
        self, qtbot, temp_db, fake_engine, monkeypatch
    ):
        """Un config prendible pero inutilizable (ej. line_crossing guardado
        con line=None por una version vieja del dialogo) no debe dejar
        enabled=True en la DB: eso rompe el proximo arranque en
        _start_enabled_analytics."""
        warnings: list[tuple[str, str]] = []
        monkeypatch.setattr(
            ac_module, "warn", lambda _parent, title, text: warnings.append((title, text))
        )

        def failing_start(config, device):
            raise ValueError("necesita una línea configurada")

        fake_engine.start = failing_start

        device = _device()
        config = repository.upsert_analytics_config(device.id, "line_crossing", enabled=False)
        module = _module_with_device(qtbot, device)
        row = ac_module.AVAILABLE_ANALYZERS.index("line_crossing")

        module.table.cellWidget(row, 1).setChecked(True)

        assert repository.get_analytics_config(config.id).enabled is False
        assert module.table.cellWidget(row, 1).isChecked() is False
        assert len(warnings) == 1
        assert "necesita una línea configurada" in warnings[0][1]


class TestValidacionDeCruceDeLinea:
    """validate() debe exigir la linea SIEMPRE: guardar sin linea con
    "habilitado" destildado persistia params["line"]=None, un estado que el
    switch del modulo puede prender despues sin pasar por el dialogo."""

    @staticmethod
    def _stub(line):
        return SimpleNamespace(selector_widget=SimpleNamespace(get_line=lambda: line))

    def test_sin_linea_no_se_puede_guardar_ni_deshabilitado(self):
        error = LineCrossingConfigDialog.validate(self._stub(None))
        assert error is not None
        assert "línea" in error

    def test_con_linea_dibujada_valida_ok(self):
        assert LineCrossingConfigDialog.validate(self._stub(((0, 0), (100, 100)))) is None
