from __future__ import annotations

import pytest

from aurea_vms.models import repository
from aurea_vms.models.media_asset import KIND_CLIP, KIND_SNAPSHOT


@pytest.fixture()
def dataset(temp_db):
    """Dos camaras con media intercalada en el tiempo."""
    cam1 = repository.add_device(name="C1", ip="10.0.0.1", rtsp_main_url="rtsp://c1/x")
    cam2 = repository.add_device(name="C2", ip="10.0.0.2", rtsp_main_url="rtsp://c2/x")
    user = repository.add_user(username="op", password_hash="h", salt="s", role="operador")

    def _asset(device_id: int, ts: float, kind: str = KIND_CLIP, created_by: int | None = None):
        return repository.add_media_asset(
            kind=kind,
            device_id=device_id,
            timestamp=ts,
            rel_path=f"{kind}/x/{device_id}/{ts}.bin",
            size_bytes=1000,
            created_by=created_by,
        )

    assets = [
        _asset(cam1.id, 100.0),
        _asset(cam1.id, 200.0, kind=KIND_SNAPSHOT),
        _asset(cam2.id, 300.0),
        _asset(cam2.id, 400.0, created_by=user.id),
    ]
    return cam1, cam2, user, assets


class TestListMedia:
    def test_orden_mas_nuevo_primero(self, dataset):
        *_, assets = dataset
        listado = repository.list_media()
        assert [a.timestamp for a in listado] == [400.0, 300.0, 200.0, 100.0]

    def test_filtro_por_kind(self, dataset):
        listado = repository.list_media(kind=KIND_SNAPSHOT)
        assert len(listado) == 1
        assert listado[0].timestamp == 200.0

    def test_filtro_por_camara(self, dataset):
        cam1, *_ = dataset
        assert len(repository.list_media(device_id=cam1.id)) == 2

    def test_filtro_por_usuario(self, dataset):
        _cam1, _cam2, user, _assets = dataset
        listado = repository.list_media(created_by=user.id)
        assert len(listado) == 1
        assert listado[0].timestamp == 400.0

    def test_rango_temporal_y_paginado(self, dataset):
        listado = repository.list_media(since=150.0, until=400.0)
        assert [a.timestamp for a in listado] == [300.0, 200.0]

        pagina = repository.list_media(limit=2, offset=2)
        assert [a.timestamp for a in pagina] == [200.0, 100.0]


class TestRetencionQueries:
    def test_oldest_first_con_corte(self, dataset):
        viejos = repository.list_media_oldest_first(older_than=350.0)
        assert [a.timestamp for a in viejos] == [100.0, 200.0, 300.0]

    def test_total_size(self, dataset):
        assert repository.total_media_size_bytes() == 4000

    def test_total_size_sin_filas(self, temp_db):
        assert repository.total_media_size_bytes() == 0

    def test_delete(self, dataset):
        *_, assets = dataset
        repository.delete_media_asset(assets[0].id)
        assert repository.get_media_asset(assets[0].id) is None
        assert repository.total_media_size_bytes() == 3000


class TestCascadasDeMedia:
    def test_borrar_camara_borra_su_media(self, dataset):
        cam1, cam2, *_ = dataset
        repository.delete_device(cam1.id)

        assert repository.list_media(device_id=cam1.id) == []
        assert len(repository.list_media(device_id=cam2.id)) == 2

    def test_borrar_usuario_no_borra_media(self, dataset):
        _cam1, _cam2, user, _assets = dataset
        repository.delete_user(user.id)

        huerfano = repository.list_media()[0]
        assert huerfano.created_by is None
