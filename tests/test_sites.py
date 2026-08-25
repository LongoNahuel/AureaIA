from __future__ import annotations

import pytest
import sqlalchemy.exc

from aurea_vms.models import repository


def _cam(name: str, site_id: int | None = None):
    return repository.add_device(
        name=name, ip="10.0.0.1", rtsp_main_url="rtsp://c/x", site_id=site_id
    )


class TestSitesCrud:
    def test_ciclo_completo(self, temp_db):
        site = repository.add_site(name="Sala Principal", description="Planta baja")
        assert repository.get_site(site.id).name == "Sala Principal"

        repository.update_site(site.id, description="Planta baja y subsuelo")
        assert repository.get_site(site.id).description == "Planta baja y subsuelo"

        repository.delete_site(site.id)
        assert repository.get_site(site.id) is None

    def test_nombre_unico(self, temp_db):
        repository.add_site(name="Sala Principal")
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            repository.add_site(name="Sala Principal")

    def test_orden_alfabetico(self, temp_db):
        repository.add_site(name="Zona Sur")
        repository.add_site(name="Anexo VIP")
        assert [s.name for s in repository.list_sites()] == ["Anexo VIP", "Zona Sur"]


class TestDevicesPorSitio:
    def test_filtro_por_sitio(self, temp_db):
        sala = repository.add_site(name="Sala Principal")
        anexo = repository.add_site(name="Anexo VIP")
        _cam("C1", sala.id)
        _cam("C2", sala.id)
        _cam("C3", anexo.id)
        _cam("C4")  # sin sitio

        assert len(repository.list_devices()) == 4
        assert len(repository.list_devices(site_id=sala.id)) == 2
        assert len(repository.list_devices(site_id=anexo.id)) == 1

    def test_borrar_sitio_deja_camaras_sin_sitio(self, temp_db):
        sala = repository.add_site(name="Sala Principal")
        cam = _cam("C1", sala.id)

        repository.delete_site(sala.id)

        actualizado = repository.get_device(cam.id)
        assert actualizado is not None
        assert actualizado.site_id is None

    def test_reasignar_camara_de_sitio(self, temp_db):
        sala = repository.add_site(name="Sala Principal")
        anexo = repository.add_site(name="Anexo VIP")
        cam = _cam("C1", sala.id)

        repository.update_device(cam.id, site_id=anexo.id)
        assert repository.get_device(cam.id).site_id == anexo.id
