from __future__ import annotations

import datetime as real_dt
from types import SimpleNamespace

import pytest

import aurea_vms.core.alarm_engine as alarm_engine_mod
from aurea_vms.core.alarm_engine import AlarmEngine
from aurea_vms.core.events import Detection, DetectionEvent
from aurea_vms.models.alarm_rule import AlarmRule


def _freeze_now(monkeypatch, when: real_dt.datetime) -> None:
    """Congela `dt.datetime.now()` SOLO dentro del modulo alarm_engine."""
    monkeypatch.setattr(
        alarm_engine_mod, "dt", SimpleNamespace(datetime=SimpleNamespace(now=lambda: when))
    )


def _rule(**fields) -> AlarmRule:
    defaults = {
        "analyzer_name": "face_detection",
        "object_classes": [],
        "min_confidence": 0.5,
        "cooldown_seconds": 30,
        "schedule_days": [],
        "schedule_start": None,
        "schedule_end": None,
        "actions": {},
        "enabled": True,
    }
    defaults.update(fields)
    return AlarmRule(**defaults)


def _detection(label: str = "cara", confidence: float = 0.9) -> Detection:
    return Detection(label=label, confidence=confidence, bbox=(0, 0, 10, 10))


class TestWithinSchedule:
    LUNES_MEDIODIA = real_dt.datetime(2026, 8, 24, 12, 0)  # lunes

    def test_sin_restricciones_siempre_activa(self, monkeypatch):
        _freeze_now(monkeypatch, self.LUNES_MEDIODIA)
        assert AlarmEngine._within_schedule(_rule()) is True

    def test_dia_permitido(self, monkeypatch):
        _freeze_now(monkeypatch, self.LUNES_MEDIODIA)
        assert AlarmEngine._within_schedule(_rule(schedule_days=[0])) is True

    def test_dia_no_permitido(self, monkeypatch):
        _freeze_now(monkeypatch, self.LUNES_MEDIODIA)
        assert AlarmEngine._within_schedule(_rule(schedule_days=[5, 6])) is False

    def test_rango_normal_dentro(self, monkeypatch):
        _freeze_now(monkeypatch, self.LUNES_MEDIODIA)
        rule = _rule(schedule_start="08:00", schedule_end="18:00")
        assert AlarmEngine._within_schedule(rule) is True

    def test_rango_normal_fuera(self, monkeypatch):
        _freeze_now(monkeypatch, real_dt.datetime(2026, 8, 24, 20, 30))
        rule = _rule(schedule_start="08:00", schedule_end="18:00")
        assert AlarmEngine._within_schedule(rule) is False

    @pytest.mark.parametrize(
        ("hora", "esperado"),
        [
            (real_dt.datetime(2026, 8, 24, 23, 0), True),  # noche, despues del inicio
            (real_dt.datetime(2026, 8, 24, 3, 0), True),  # madrugada, antes del fin
            (real_dt.datetime(2026, 8, 24, 12, 0), False),  # mediodia, fuera del rango
            (real_dt.datetime(2026, 8, 24, 22, 0), True),  # borde inicial exacto
            (real_dt.datetime(2026, 8, 24, 6, 0), True),  # borde final exacto
        ],
    )
    def test_rango_que_cruza_medianoche(self, monkeypatch, hora, esperado):
        _freeze_now(monkeypatch, hora)
        rule = _rule(schedule_start="22:00", schedule_end="06:00")
        assert AlarmEngine._within_schedule(rule) is esperado

    def test_solo_start_sin_end_no_restringe(self, monkeypatch):
        _freeze_now(monkeypatch, self.LUNES_MEDIODIA)
        rule = _rule(schedule_start="22:00", schedule_end=None)
        assert AlarmEngine._within_schedule(rule) is True


class TestBestMatch:
    def test_filtra_por_confianza_y_clase_y_elige_la_mayor(self):
        rule = _rule(min_confidence=0.5, object_classes=["person"])
        detections = (
            _detection("person", 0.4),  # descartada: confianza baja
            _detection("person", 0.7),
            _detection("car", 0.95),  # descartada: clase no permitida
            _detection("person", 0.6),
        )
        match = AlarmEngine._best_match(rule, detections)
        assert match is not None
        assert match.confidence == 0.7

    def test_clases_vacias_permite_cualquiera(self):
        rule = _rule(min_confidence=0.5, object_classes=[])
        match = AlarmEngine._best_match(rule, (_detection("car", 0.8),))
        assert match is not None
        assert match.label == "car"

    def test_sin_candidatas_devuelve_none(self):
        rule = _rule(min_confidence=0.9)
        assert AlarmEngine._best_match(rule, (_detection("cara", 0.5),)) is None


class TestCooldown:
    def _event(self, ts: float) -> DetectionEvent:
        return DetectionEvent(
            device_id=1,
            analyzer_name="face_detection",
            timestamp=ts,
            detections=(_detection(),),
        )

    def test_no_redispara_dentro_del_cooldown(self, monkeypatch):
        engine = AlarmEngine()
        rule = _rule(cooldown_seconds=30)
        rule.id = 99

        monkeypatch.setattr(alarm_engine_mod.repository, "list_alarm_rules_for", lambda *_: [rule])
        triggered: list[tuple] = []
        monkeypatch.setattr(engine, "_trigger", lambda *args: triggered.append(args))

        engine._on_detection(self._event(1000.0))
        engine._on_detection(self._event(1001.0))
        assert len(triggered) == 1

    def test_redispara_pasado_el_cooldown(self, monkeypatch):
        engine = AlarmEngine()
        rule = _rule(cooldown_seconds=30)
        rule.id = 99

        monkeypatch.setattr(alarm_engine_mod.repository, "list_alarm_rules_for", lambda *_: [rule])
        triggered: list[tuple] = []
        monkeypatch.setattr(engine, "_trigger", lambda *args: triggered.append(args))

        fake_clock = iter([1000.0, 1040.0])
        monkeypatch.setattr(alarm_engine_mod.time, "time", lambda: next(fake_clock))

        engine._on_detection(self._event(1000.0))
        engine._on_detection(self._event(1040.0))
        assert len(triggered) == 2

    def test_evento_sin_detecciones_no_hace_nada(self, monkeypatch):
        engine = AlarmEngine()
        called = []
        monkeypatch.setattr(
            alarm_engine_mod.repository,
            "list_alarm_rules_for",
            lambda *_: called.append(True) or [],
        )
        engine._on_detection(
            DetectionEvent(device_id=1, analyzer_name="face_detection", timestamp=0.0)
        )
        assert called == []
