from __future__ import annotations

import pytest

from aurea_vms.core import app_prefs


@pytest.fixture(autouse=True)
def _prefs_en_tmp(tmp_path, monkeypatch):
    """Redirige el JSON de preferencias a un tmp para no pisar data/ real."""
    monkeypatch.setattr(app_prefs, "_PREFS_PATH", tmp_path / "preferences.json")


def test_default_sin_archivo():
    assert app_prefs.get_theme() == "dark"


def test_set_y_get_roundtrip():
    app_prefs.set_theme("light")
    assert app_prefs.get_theme() == "light"


def test_archivo_corrupto_cae_a_defaults(tmp_path):
    app_prefs._PREFS_PATH.write_text("{esto no es json", encoding="utf-8")
    assert app_prefs.get_theme() == "dark"


def test_claves_desconocidas_se_preservan():
    app_prefs._write({"theme": "light", "otra": 1})
    app_prefs.set_theme("dark")
    assert app_prefs._read()["otra"] == 1
