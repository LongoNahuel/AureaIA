"""Helpers de severidad del tema (tokens NOVA): los consumidores (chips de
Alarmas, popups) confian en estas invariantes."""

from __future__ import annotations

import pytest

from aurea_vms.ui.theme import (
    SEVERITY_COLORS,
    severity_qcolor,
    severity_soft_qcolor,
    severity_text_qcolor,
)


def test_severidad_desconocida_cae_a_info():
    assert severity_qcolor("inventada").name() == SEVERITY_COLORS["info"]


def test_soft_es_el_mismo_tono_al_12_por_ciento():
    solido = severity_qcolor("critico")
    soft = severity_soft_qcolor("critico")
    assert (soft.red(), soft.green(), soft.blue()) == (solido.red(), solido.green(), solido.blue())
    # QColor cuantiza el alpha a 8 bits: 0.12 se guarda como 31/255.
    assert soft.alphaF() == pytest.approx(0.12, abs=0.01)


def test_texto_en_tema_claro_se_oscurece():
    """El medio #ffd166 (amarillo) es ilegible sobre blanco: en tema claro
    el texto del chip debe salir mas oscuro que el token pleno."""
    oscuro = severity_text_qcolor("medio", dark=True)
    claro = severity_text_qcolor("medio", dark=False)
    assert oscuro.name() == SEVERITY_COLORS["medio"]
    assert claro.lightness() < oscuro.lightness()
