"""Atajos entre modulos (2026-10-07): "Ajustes avanzados" de Dispositivos
abria el modulo 2, que desde que se agrego Vista Inteligente es Dispositivos,
no Analizadores."""

from __future__ import annotations

from types import SimpleNamespace

from aurea_vms.ui.main_window import ALARMS_INDEX, ANALYZERS_INDEX, MODULES, MainWindow


def test_los_indices_salen_del_nombre_del_modulo():
    assert MODULES[ANALYZERS_INDEX][0] == "Analizadores"
    assert MODULES[ALARMS_INDEX][0] == "Alarmas"


def test_ajustes_avanzados_lleva_a_analizadores_con_la_camara():
    opened: list[int] = []
    focused: list[int] = []
    module = SimpleNamespace(focus_device=focused.append)

    def abrir(index, destino=None):
        opened.append(index)
        return module

    window = SimpleNamespace(open_module_by_index=abrir)

    MainWindow._on_open_analytics_config_requested(window, 8)

    assert opened == [ANALYZERS_INDEX] and focused == [8]
