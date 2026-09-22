from __future__ import annotations

import datetime as real_dt
import logging
from types import SimpleNamespace

import pytest  # type: ignore[import-not-found]
from sqlalchemy.exc import OperationalError

import aurea_vms.core.alarm_engine as alarm_engine_mod
from aurea_vms.core.alarm_engine import AlarmEngine
from aurea_vms.core.events import Detection, DetectionEvent
from aurea_vms.models.alarm_rule import AlarmRule


def _freeze_now(monkeypatch, when: real_dt.datetime) -> None:
    """Congela `dt.datetime.now()` SOLO dentro del modulo alarm_engine."""
    monkeypatch.setattr(
        alarm_engine_mod, "dt", SimpleNamespace(datetime=SimpleNamespace(now=lambda: when))
    )


def _fake_clock(monkeypatch, *valores: float) -> None:
    """Congela `time.time()` SOLO dentro del modulo alarm_engine.

    Importa que sea el modulo y no el atributo: `logging` tambien llama a
    `time.time()` al armar cada LogRecord, asi que parchear el atributo del
    modulo `time` compartido hace que un simple `logger.exception` consuma
    valores del reloj falso.
    """
    reloj = iter(valores)
    monkeypatch.setattr(alarm_engine_mod, "time", SimpleNamespace(time=lambda: next(reloj)))


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

        _fake_clock(monkeypatch, 1000.0, 1040.0)

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

    def test_puerta_no_repite_alerta_sin_transicion(self, monkeypatch):
        engine = AlarmEngine()
        rule = _rule(analyzer_name="door_state", object_classes=["puerta_abierta"])
        rule.id = 100
        monkeypatch.setattr(alarm_engine_mod.repository, "list_alarm_rules_for", lambda *_: [rule])
        triggered: list[tuple] = []
        monkeypatch.setattr(engine, "_trigger", lambda *args: triggered.append(args))
        detection = _detection("puerta_abierta", 0.95)

        engine._on_detection(
            DetectionEvent(
                device_id=1,
                analyzer_name="door_state",
                timestamp=1000.0,
                detections=(detection,),
                metrics={"estado": "abierta", "transicion": None},
            )
        )
        engine._on_detection(
            DetectionEvent(
                device_id=1,
                analyzer_name="door_state",
                timestamp=1001.0,
                detections=(detection,),
                metrics={"estado": "abierta", "transicion": "cerrada_a_abierta"},
            )
        )
        engine._on_detection(
            DetectionEvent(
                device_id=1,
                analyzer_name="door_state",
                timestamp=1002.0,
                detections=(detection,),
                metrics={"estado": "abierta", "transicion": None},
            )
        )

        assert len(triggered) == 1


class TestResilienciaDeHilo:
    """`_on_detection` es un slot conectado a una signal que emiten los
    AnalyticsWorker, y AlarmEngine no es un QObject: no hay marshaleo, corre
    en el hilo de la analitica. Una excepcion que se escape de ahi sube cruda
    por el slot de Qt y se lleva puesto ese hilo -- la camara deja de analizar
    hasta que alguien reinicie la app. Estos tests fijan que ninguna falla de
    DB (el caso realista: un lock transitorio de SQLite) llegue tan lejos.
    """

    def _event(self, ts: float = 1000.0) -> DetectionEvent:
        return DetectionEvent(
            device_id=1,
            analyzer_name="face_detection",
            timestamp=ts,
            detections=(_detection(),),
        )

    def test_leer_las_reglas_puede_fallar_sin_matar_el_hilo(self, monkeypatch, caplog):
        engine = AlarmEngine()

        def explota(*_):
            raise OperationalError("SELECT", {}, Exception("database is locked"))

        monkeypatch.setattr(alarm_engine_mod.repository, "list_alarm_rules_for", explota)

        with caplog.at_level(logging.ERROR, logger=alarm_engine_mod.__name__):
            engine._on_detection(self._event())

        assert "reglas de alarma" in caplog.text
        assert "database is locked" in caplog.text

    def test_una_regla_que_falla_no_frena_a_las_demas(self, monkeypatch, caplog):
        """Las reglas de una camara son independientes: que el insert de la
        primera choque contra un lock no puede dejar sin evaluar a la
        segunda, que quiza es la critica."""
        engine = AlarmEngine()
        rota, sana = _rule(cooldown_seconds=0), _rule(cooldown_seconds=0)
        rota.id, sana.id = 1, 2

        monkeypatch.setattr(
            alarm_engine_mod.repository, "list_alarm_rules_for", lambda *_: [rota, sana]
        )
        disparadas: list[int] = []

        def trigger(rule, *_):
            if rule.id == rota.id:
                raise OperationalError("INSERT", {}, Exception("database is locked"))
            disparadas.append(rule.id)

        monkeypatch.setattr(engine, "_trigger", trigger)

        with caplog.at_level(logging.ERROR, logger=alarm_engine_mod.__name__):
            engine._on_detection(self._event())

        assert disparadas == [sana.id]
        assert "regla 1" in caplog.text

    def test_un_disparo_fallido_no_consume_el_cooldown(self, monkeypatch):
        """El punto fino del fix: si el cooldown se marcara antes de disparar,
        un lock transitorio dejaria la regla MUDA hasta que venza (30s por
        defecto). Marcandolo despues, el proximo frame reintenta."""
        engine = AlarmEngine()
        rule = _rule(cooldown_seconds=30)
        rule.id = 7

        monkeypatch.setattr(alarm_engine_mod.repository, "list_alarm_rules_for", lambda *_: [rule])
        intentos: list[float] = []

        def trigger(_rule, event, _detection):
            intentos.append(event.timestamp)
            if len(intentos) == 1:
                raise OperationalError("INSERT", {}, Exception("database is locked"))

        monkeypatch.setattr(engine, "_trigger", trigger)

        # Dos frames consecutivos, MUY dentro del cooldown de 30s.
        _fake_clock(monkeypatch, 1000.0, 1000.2)

        engine._on_detection(self._event(1000.0))
        engine._on_detection(self._event(1000.2))

        assert intentos == [1000.0, 1000.2], "el fallo consumio el cooldown y silencio la regla"
        assert engine._last_triggered[rule.id] == 1000.2

    def test_un_disparo_exitoso_si_consume_el_cooldown(self, monkeypatch):
        """La contracara del test anterior: el reintento no puede volverse
        spam una vez que la alarma entro bien."""
        engine = AlarmEngine()
        rule = _rule(cooldown_seconds=30)
        rule.id = 7

        monkeypatch.setattr(alarm_engine_mod.repository, "list_alarm_rules_for", lambda *_: [rule])
        intentos: list[float] = []
        monkeypatch.setattr(engine, "_trigger", lambda _r, e, _d: intentos.append(e.timestamp))

        _fake_clock(monkeypatch, 1000.0, 1000.2)

        engine._on_detection(self._event(1000.0))
        engine._on_detection(self._event(1000.2))

        assert intentos == [1000.0]
