"""Detección de incidentes -> AlarmEngine, de punta a punta.

El analizador emite `triggers` UNA sola vez por incidente, en la primera
muestra, con la confianza del keypoint que golpeo (una patada cuenta desde
0.30). Las reglas filtran por `min_confidence` (0.5 por defecto). Si el
primer golpe sale con 0.30-0.49, la regla lo descarta y los golpes
siguientes del mismo incidente ya no generan triggers: el incidente no
alarma nunca.

Encontrado en la revision del 24/09 (sesiones/2026-09-24.md). La semantica
de confianza es de la analitica (Nahuel): el test queda xfail(strict) para
que el CI avise cuando se arregle.
"""

from __future__ import annotations

import pytest
from test_monitor_tamper import FRAME, _analyzer, _patada

import aurea_vms.core.alarm_engine as alarm_engine_mod
from aurea_vms.core.alarm_engine import AlarmEngine
from aurea_vms.core.events import DetectionEvent
from aurea_vms.models.alarm_rule import AlarmRule


def _regla(min_confidence: float = 0.5) -> AlarmRule:
    regla = AlarmRule(
        analyzer_name="monitor_tamper",
        object_classes=[],
        min_confidence=min_confidence,
        cooldown_seconds=30,
        schedule_days=[],
        schedule_start=None,
        schedule_end=None,
        actions={},
        enabled=True,
    )
    regla.id = 1
    return regla


def _alarmas(monkeypatch, scores: list[float]) -> int:
    """Corre un incidente (un golpe por cuadro, sin pausa) por el analizador
    y pasa cada resultado por el motor de alarmas. Devuelve cuantas
    alarmas disparo."""
    regla = _regla()
    monkeypatch.setattr(alarm_engine_mod.repository, "list_alarm_rules_for", lambda *_: [regla])
    disparos: list[tuple] = []
    motor = AlarmEngine()
    monkeypatch.setattr(motor, "_trigger", lambda *args: disparos.append(args))

    analyzer, _ = _analyzer([_patada(score) for score in scores])
    for i, _score in enumerate(scores):
        t = i * 0.2
        result = analyzer.process_frame(FRAME, t)
        motor._on_detection(
            DetectionEvent(
                device_id=1,
                analyzer_name="monitor_tamper",
                timestamp=t,
                detections=result.detections,
                metrics=result.metrics,
                triggers=result.triggers,
            )
        )
    return len(disparos)


def test_un_incidente_con_golpes_firmes_alarma_una_vez(monkeypatch):
    """Control: el circuito anda cuando el primer golpe supera la regla."""
    assert _alarmas(monkeypatch, [0.9, 0.9, 0.9]) == 1


@pytest.mark.xfail(
    strict=True,
    reason="Detección de incidentes: el trigger sale solo en la primera muestra, y si su "
    "confianza queda bajo min_confidence de la regla el incidente no alarma (para Nahuel)",
)
def test_un_primer_golpe_debil_no_puede_silenciar_el_incidente(monkeypatch):
    assert _alarmas(monkeypatch, [0.40, 0.9, 0.9]) == 1
