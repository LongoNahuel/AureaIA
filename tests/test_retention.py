from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from aurea_vms.core import media_store, retention
from aurea_vms.models import repository

AHORA = 1_000_000_000.0
DIA = 86400.0


@pytest.fixture(autouse=True)
def _media_en_tmp(tmp_path, monkeypatch):
    fake_settings = SimpleNamespace(media_dir=tmp_path / "media")
    monkeypatch.setattr(media_store, "settings", fake_settings)
    return fake_settings


@pytest.fixture()
def camara(temp_db):
    return repository.add_device(name="Cam", ip="10.0.0.9", rtsp_main_url="rtsp://c/x")


def _asset_con_archivo(device_id: int, ts: float, size: int = 1000, kind: str = "clip"):
    """Crea el archivo fisico + su fila en el indice, como hace media_store."""
    rel = media_store.build_rel_path(kind, device_id, int(ts), ts, ".bin")
    path = media_store.prepare_path(rel)
    path.write_bytes(b"x" * size)
    return repository.add_media_asset(
        kind=kind, device_id=device_id, timestamp=ts, rel_path=rel, size_bytes=size
    )


class TestPrunePorEdad:
    def test_borra_viejos_y_conserva_nuevos(self, camara):
        viejo = _asset_con_archivo(camara.id, AHORA - 10 * DIA)
        nuevo = _asset_con_archivo(camara.id, AHORA - 1 * DIA)

        stats = retention.prune(max_age_days=7, max_total_gb=100.0, now=AHORA)

        assert stats["deleted"] == 1
        assert stats["freed_bytes"] == 1000
        assert repository.get_media_asset(viejo.id) is None
        assert repository.get_media_asset(nuevo.id) is not None
        assert not media_store.absolute_path(viejo.rel_path).exists()
        assert media_store.absolute_path(nuevo.rel_path).exists()

    def test_limpia_directorios_vacios(self, camara):
        viejo = _asset_con_archivo(camara.id, AHORA - 10 * DIA)
        dir_del_dia = media_store.absolute_path(viejo.rel_path).parent

        retention.prune(max_age_days=7, max_total_gb=100.0, now=AHORA)

        assert not dir_del_dia.exists()
        assert media_store.settings.media_dir.exists()  # la raiz nunca se toca

    def test_archivo_ya_ausente_igual_limpia_la_fila(self, camara):
        fantasma = _asset_con_archivo(camara.id, AHORA - 10 * DIA)
        media_store.absolute_path(fantasma.rel_path).unlink()

        stats = retention.prune(max_age_days=7, max_total_gb=100.0, now=AHORA)

        assert stats["deleted"] == 1
        assert repository.get_media_asset(fantasma.id) is None


class TestPrunePorTamano:
    def test_borra_los_mas_viejos_hasta_bajar_del_tope(self, camara):
        assets = [
            _asset_con_archivo(camara.id, AHORA - (5 - i) * DIA, size=1024**2)  # 1 MB c/u
            for i in range(4)
        ]

        # Tope de 2.5 MB con 4 MB usados: deben caer los 2 mas viejos.
        tope_gb = 2.5 / 1024
        stats = retention.prune(max_age_days=365, max_total_gb=tope_gb, now=AHORA)

        assert stats["deleted"] == 2
        assert repository.get_media_asset(assets[0].id) is None
        assert repository.get_media_asset(assets[1].id) is None
        assert repository.get_media_asset(assets[2].id) is not None
        assert repository.total_media_size_bytes() == 2 * 1024**2

    def test_media_recien_creada_no_se_toca_ni_con_tope_excedido(self, camara):
        reciente = _asset_con_archivo(camara.id, AHORA - 10.0, size=1024**2)

        stats = retention.prune(max_age_days=365, max_total_gb=0.0000001, now=AHORA)

        assert stats["deleted"] == 0
        assert repository.get_media_asset(reciente.id) is not None


class TestPruneSinNada:
    def test_sin_media_no_falla(self, temp_db):
        stats = retention.prune(max_age_days=7, max_total_gb=5.0, now=AHORA)
        assert stats == {"deleted": 0, "freed_bytes": 0}


class TestWorker:
    def test_corre_pasadas_periodicas_y_se_detiene(self, monkeypatch, temp_db):
        llamadas: list[float] = []

        def fake_prune(**_kwargs):
            llamadas.append(time.monotonic())
            return {"deleted": 0, "freed_bytes": 0}

        monkeypatch.setattr(retention, "prune", fake_prune)

        worker = retention.RetentionWorker(interval_s=0.03, first_delay_s=0.01)
        worker.start()
        time.sleep(0.15)
        worker.stop()
        worker.join(timeout=1.0)

        assert not worker.is_alive()
        assert len(llamadas) >= 2
