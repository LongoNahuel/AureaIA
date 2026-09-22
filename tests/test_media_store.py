from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from aurea_vms.core import media_store
from aurea_vms.models import repository


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


class TestRegister:
    """B4: media_assets es el unico indice de evidencia. Una fila que apunta
    a un archivo inexistente o vacio es peor que no tener la fila, porque el
    operador la ve listada como evidencia valida del incidente."""

    def test_archivo_inexistente_no_se_indexa(self, temp_db):
        with pytest.raises(media_store.MediaWriteError):
            media_store.register("clip", 1, "clip/2026/09/22/1/no-existe.mp4", timestamp=1.0)
        assert repository.list_media(kind="clip") == []

    def test_archivo_vacio_no_se_indexa(self, temp_db):
        """El modo de falla real de un VideoWriter que no abrio: el archivo
        existe, pesa 0 bytes y no hay nada que reproducir."""
        rel_path = "clip/2026/09/22/1/vacio.mp4"
        media_store.prepare_path(rel_path).touch()

        with pytest.raises(media_store.MediaWriteError):
            media_store.register("clip", 1, rel_path, timestamp=1.0)
        assert repository.list_media(kind="clip") == []

    def test_archivo_con_contenido_se_indexa_con_su_tamano(self, temp_db):
        device = repository.add_device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
        rel_path = f"clip/2026/09/22/{device.id}/ok.mp4"
        media_store.prepare_path(rel_path).write_bytes(b"x" * 321)

        asset = media_store.register("clip", device.id, rel_path, timestamp=1.0)

        assert asset.size_bytes == 321
        assert asset.rel_path == rel_path
