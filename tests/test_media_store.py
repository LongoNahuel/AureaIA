from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from aurea_vms.core import media_store


@pytest.fixture(autouse=True)
def _media_en_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(media_store, "settings", SimpleNamespace(media_dir=tmp_path / "media"))
    return tmp_path / "media"


class TestBuildRelPath:
    def test_layout_fecha_camara(self):
        when = dt.datetime(2026, 8, 24, 21, 30, 45).timestamp()
        rel = media_store.build_rel_path("clip", 3, 17, when, ".mp4")
        assert rel == "clip/2026/08/24/3/213045_17.mp4"

    def test_sin_evento(self):
        when = dt.datetime(2026, 8, 24, 21, 30, 45).timestamp()
        rel = media_store.build_rel_path("snapshot", 3, None, when, ".jpg")
        assert rel == "snapshot/2026/08/24/3/213045.jpg"

    def test_siempre_usa_barras_posix(self):
        rel = media_store.build_rel_path("clip", 1, 1, 0.0, ".mp4")
        assert "\\" not in rel


class TestPaths:
    def test_absolute_bajo_media_dir(self, _media_en_tmp):
        path = media_store.absolute_path("clip/2026/08/24/3/x.mp4")
        assert path == _media_en_tmp / "clip" / "2026" / "08" / "24" / "3" / "x.mp4"
        assert isinstance(path, Path)

    def test_prepare_path_crea_el_directorio(self):
        path = media_store.prepare_path("clip/2026/08/24/3/x.mp4")
        assert path.parent.is_dir()
        assert not path.exists()
