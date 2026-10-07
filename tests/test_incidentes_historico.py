"""Limpiar incidentes y el historico de Alarmas (2026-10-02).

- "Limpiar incidentes" pone en cero los contadores y alertas en vivo de una
  camara (el analizador lo hace en su propio hilo) y reconoce sus alarmas
  pendientes; el historico no se toca.
- Al habilitar "Incidentes en casinos" se crea sola la regla de alarma, para
  que cada incidente quede en el historico con captura y clip.
- Alarmas: filtros por canal / incidente / estado, historico paginado y el
  detalle de un incidente puntual.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest

import aurea_vms.core.analytics_engine as ae_module
from aurea_vms.core import auth, incident_rules
from aurea_vms.core.analytics.base import AnalysisResult
from aurea_vms.core.analytics.monitor_tamper_analyzer import MonitorTamperAnalyzer
from aurea_vms.core.analytics.roulette_analyzer import RouletteAnalyzer
from aurea_vms.core.analytics_engine import AnalyticsEngine, AnalyticsWorker
from aurea_vms.models import repository
from aurea_vms.models.alarm_event import STATUS_ACKNOWLEDGED, STATUS_NEW, STATUS_RESOLVED
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.device import Device
from aurea_vms.models.user import ROLE_ADMIN, ROLE_AUDITOR, User


def _login(role: str) -> None:
    auth.current_user = User(username=f"u-{role}", password_hash="h", salt="s", role=role)


def _device(name: str, ip: str):
    return repository.add_device(name=name, ip=ip, rtsp_main_url=f"rtsp://{ip}/x")


def _alarm(device_id: int, label: str = "fichas_tras_no_va_mas", status: str = STATUS_NEW):
    return repository.add_alarm_event(
        device_id=device_id,
        timestamp=time.time(),
        object_class=label,
        confidence=1.0,
        severity="critico",
        status=status,
    )


class TestReglaAutomatica:
    def test_al_habilitar_incidentes_se_crea_la_regla_con_clip(self, temp_db):
        device = _device("Ruleta", "10.0.0.7")
        config = SimpleNamespace(analyzer_name="monitor_tamper", device_id=device.id)

        assert incident_rules.ensure_incident_rule(config) is True
        assert incident_rules.ensure_incident_rule(config) is False  # una sola

        (rule,) = repository.list_alarm_rules(device.id)
        assert "fichas_tras_no_va_mas" in rule.object_classes
        assert "patada_monitor" in rule.object_classes
        assert rule.actions["save_clip"] is True and rule.enabled
        assert rule.schedule_days == []

    def test_si_ya_hay_una_regla_para_todas_las_camaras_no_se_crea_otra(self, temp_db):
        device = _device("Ruleta", "10.0.0.7")
        repository.add_alarm_rule(device_id=None, analyzer_name="monitor_tamper")

        config = SimpleNamespace(analyzer_name="monitor_tamper", device_id=device.id)
        assert incident_rules.ensure_incident_rule(config) is False
        assert repository.list_alarm_rules(device.id) == []

    def test_otras_analiticas_no_crean_reglas(self, temp_db):
        device = _device("Hall", "10.0.0.8")
        config = SimpleNamespace(analyzer_name="people_counting", device_id=device.id)

        assert incident_rules.ensure_incident_rule(config) is False


class TestRepositorio:
    def test_reconocer_las_pendientes_de_una_camara(self, temp_db):
        ruleta, monitor = _device("Ruleta", "10.0.0.7"), _device("Monitor", "10.0.0.3")
        nuevas = [_alarm(ruleta.id), _alarm(ruleta.id)]
        resuelta = _alarm(ruleta.id, status=STATUS_RESOLVED)
        ajena = _alarm(monitor.id)

        assert repository.acknowledge_pending_alarm_events(ruleta.id) == 2

        assert {repository.get_alarm_event(a.id).status for a in nuevas} == {STATUS_ACKNOWLEDGED}
        assert repository.get_alarm_event(resuelta.id).status == STATUS_RESOLVED
        assert repository.get_alarm_event(ajena.id).status == STATUS_NEW

    def test_historico_filtrado_y_paginado(self, temp_db):
        ruleta, monitor = _device("Ruleta", "10.0.0.7"), _device("Monitor", "10.0.0.3")
        ids = [_alarm(ruleta.id).id for _ in range(5)]
        _alarm(monitor.id, label="patada_monitor")

        first = repository.list_alarm_events(limit=2, device_id=ruleta.id)
        second = repository.list_alarm_events(limit=2, offset=2, device_id=ruleta.id)
        assert [a.id for a in first + second] == sorted(ids, reverse=True)[:4]
        assert len(repository.list_alarm_events(object_class="patada_monitor")) == 1
        assert repository.count_alarm_events_by_device() == {ruleta.id: 5, monitor.id: 1}
        assert repository.list_alarm_event_classes() == [
            "fichas_tras_no_va_mas",
            "patada_monitor",
        ]
        assert repository.count_alarm_events(object_class="patada_monitor") == 1


class _CountingAnalyzer:
    name = "monitor_tamper"

    def __init__(self) -> None:
        self.resets = 0
        self.frames = 0
        self.calls: list[str] = []

    def reset_counters(self) -> None:
        self.resets += 1
        self.calls.append("reset")

    def process_frame(self, frame, timestamp) -> AnalysisResult:
        self.frames += 1
        self.calls.append("frame")
        return AnalysisResult(detections=(), metrics={})

    def close(self) -> None:
        pass


class TestLimpiarEnElMotor:
    def test_el_worker_limpia_en_su_hilo_antes_del_proximo_cuadro(self, monkeypatch):
        analyzer = _CountingAnalyzer()
        monkeypatch.setattr(ae_module, "create_analyzer", lambda _config: analyzer)
        config = AnalyticsConfig(device_id=7, analyzer_name="monitor_tamper")
        config.id = 1
        device = Device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
        device.id = 7
        worker = AnalyticsWorker(config, device)
        frame = np.zeros((10, 10, 3), np.uint8)

        worker.request_reset()
        worker._analyze(frame)
        worker._analyze(frame)

        assert analyzer.calls == ["reset", "frame", "frame"]  # una vez, antes del cuadro

    def test_sin_worker_corriendo_no_hay_nada_que_limpiar(self):
        assert AnalyticsEngine().reset_counters(99) is False

    def test_la_ruleta_pone_en_cero_contadores_pero_sigue_la_ronda(self):
        analyzer = RouletteAnalyzer(zones=[(0, 0, 50, 50)], hands_enabled=False)
        analyzer._incidents, analyzer._last_incident = 3, {"t": 1.0}
        analyzer._alerts = {0: time.time() + 5}
        analyzer._zones[0].changes, analyzer._zones[0].last_change_at = 4, 1.0
        analyzer._round.window_since, analyzer._round.games = 10.0, 2

        analyzer.reset_counters()

        assert analyzer._incidents == 0 and analyzer._last_incident is None
        assert analyzer._alerts == {} and analyzer._zones[0].changes == 0
        assert analyzer._round.games == 0
        assert analyzer._round.window_open  # el paño armado sigue armado

    def test_golpes_al_monitor_vuelve_a_cero(self):
        analyzer = MonitorTamperAnalyzer(zones=[(0, 0, 50, 50)], estimator=object())
        analyzer._states[0].incidents, analyzer._states[0].in_incident = 2, True

        analyzer.reset_counters()

        assert analyzer._states[0].incidents == 0 and not analyzer._states[0].in_incident


class TestBotonLimpiar:
    @pytest.fixture()
    def panel(self, qtbot, temp_db, monkeypatch):
        import aurea_vms.ui.widgets.monitor_tamper_panel as panel_module
        from aurea_vms.ui.widgets.monitor_tamper_panel import MonitorTamperPanel

        device = _device("Ruleta", "10.0.0.7")
        config = repository.upsert_analytics_config(device.id, "monitor_tamper", params={})
        resets: list[int] = []
        monkeypatch.setattr(panel_module, "confirm", lambda *_a: True)
        monkeypatch.setattr(panel_module, "notify", lambda *_a: None)
        monkeypatch.setattr(
            panel_module.analytics_engine, "reset_counters", lambda cid: resets.append(cid)
        )
        widget = MonitorTamperPanel()
        qtbot.addWidget(widget)
        widget.set_device(device.id)
        return widget, device, config, resets

    def test_limpia_la_vista_en_vivo_y_reconoce_las_pendientes(self, panel):
        _login(ROLE_ADMIN)
        widget, device, config, resets = panel
        alarms = [_alarm(device.id), _alarm(device.id)]

        widget.clear_button.click()

        assert resets == [config.id]
        assert {repository.get_alarm_event(a.id).status for a in alarms} == {STATUS_ACKNOWLEDGED}
        assert repository.count_alarm_events(device_id=device.id) == 2  # el historico sigue

    def test_sin_permiso_de_gestion_solo_limpia_la_vista(self, panel):
        _login(ROLE_AUDITOR)
        widget, device, config, resets = panel
        alarm = _alarm(device.id)

        widget.clear_button.click()

        assert resets == [config.id]
        assert repository.get_alarm_event(alarm.id).status == STATUS_NEW


class TestModuloAlarmas:
    @pytest.fixture()
    def modulo(self, qtbot, temp_db):
        from aurea_vms.ui.modules.alarm_module import AlarmModule

        _login(ROLE_ADMIN)
        ruleta, monitor = _device("Ruleta", "10.0.0.7"), _device("Monitor", "10.0.0.3")
        for _ in range(3):
            _alarm(ruleta.id)
        _alarm(monitor.id, label="patada_monitor")

        def crear():
            module = AlarmModule()
            qtbot.addWidget(module)
            return module

        return crear, ruleta, monitor

    def test_cada_canal_con_su_cantidad_y_el_filtro(self, modulo):
        crear, ruleta, _monitor = modulo
        module = crear()
        textos = [module.channel_combo.itemText(i) for i in range(module.channel_combo.count())]
        assert textos[:3] == ["Todos los canales (4)", "Ruleta (3)", "Monitor (1)"]
        assert module.table.rowCount() == 4

        module.channel_combo.setCurrentIndex(module.channel_combo.findData(ruleta.id))

        assert module.table.rowCount() == 3

    def test_el_historico_se_carga_de_a_paginas(self, modulo, monkeypatch):
        import aurea_vms.ui.modules.alarm_module as alarm_module

        monkeypatch.setattr(alarm_module, "PAGE_SIZE", 2)
        crear, _ruleta, _monitor = modulo
        module = crear()
        assert module.table.rowCount() == 2 and module.more_button.isEnabled()
        assert module.count_label.text() == "2 de 4 incidentes"

        module.more_button.click()

        assert module.table.rowCount() == 4 and not module.more_button.isEnabled()

    def test_el_detalle_muestra_un_incidente_puntual(self, modulo):
        crear, _ruleta, monitor = modulo
        module = crear()
        module.class_combo.setCurrentIndex(module.class_combo.findData("patada_monitor"))
        module.table.selectRow(0)

        assert module.detail_title.text().endswith("· Monitor")
        assert "Patada al monitor" in module.detail_info.text()
        assert not module.play_button.isEnabled()  # sin clip todavia
        assert "todavía no está" in module.clip_status.text()
