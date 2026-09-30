"""Panel de Incidentes en casinos, modo ruleta (2026-09-30): el estado de la
ronda, la jugada nueva que abre la rueda y la alerta."""

from __future__ import annotations

import time

import pytest

from aurea_vms.core.events import DetectionEvent
from aurea_vms.ui.widgets.monitor_tamper_panel import MonitorTamperPanel


@pytest.fixture()
def panel(qtbot):
    widget = MonitorTamperPanel()
    qtbot.addWidget(widget)
    return widget


def _event(ronda: dict, **extra) -> DetectionEvent:
    metrics = {
        "modo": "ruleta",
        "zona_tipo": "zona",
        "estado": "normal",
        "zonas": [{"estado": "normal", "cambios": 0}],
        "velocidad_deg_s": 187.0,
        "rpm": 31.2,
        "sentido": "horario",
        "incidentes": 0,
        "fichas_movidas": 3,
        "ronda": ronda,
        **extra,
    }
    return DetectionEvent(10, "monitor_tamper", time.time(), metrics=metrics)


def test_la_jugada_nueva_de_la_rueda(panel):
    panel.on_event(
        _event(
            {
                "estado": "apuestas",
                "pano_armado": False,
                "jugadas": 2,
                "ultima_jugada": {"t": time.time() - 1.0, "tipo": "sentido"},
            }
        )
    )

    assert "Nueva jugada · la rueda cambió de sentido" in panel.state_pill.text()
    assert "2 jugadas" in panel.contact_label.text()


def test_despues_de_unos_segundos_vuelve_a_apuestas_abiertas(panel):
    panel.on_event(
        _event(
            {
                "estado": "apuestas",
                "pano_armado": False,
                "jugadas": 1,
                "ultima_jugada": {"t": time.time() - 30.0, "tipo": "sentido"},
            }
        )
    )

    assert "Apuestas abiertas" in panel.state_pill.text()


def test_no_va_mas_con_la_bola_y_la_alerta(panel):
    panel.on_event(_event({"estado": "no_va_mas", "pano_armado": True, "bola_deg_s": 180.0}))
    assert "NO VA MÁS" in panel.state_pill.text() and "bola 180 °/s" in panel.state_pill.text()
    assert "paño armado" in panel.contact_label.text()

    panel.on_event(
        _event(
            {"estado": "resultado", "pano_armado": True},
            estado="alerta",
            incidentes=1,
            ultimo_incidente={"t": time.time(), "zona": 0, "motivo": "no_va_mas"},
        )
    )
    assert "ALERTA · fichas tras el no va más" in panel.state_pill.text()
    assert panel.incidents_label.text() == "1"
