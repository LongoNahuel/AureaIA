"""Tests del seed de demo (tools/demo/seed_demo.py).

Hasta hoy `tools/` no tenía un solo test, y el seed estaba roto en
cualquier máquina limpia desde la migración a zonas (3977978): pasaba
`site_id=` a `repository.add_device`, columna que Device ya no declara, así
que el constructor de SQLAlchemy levantaba

    TypeError: 'site_id' is an invalid keyword argument for Device

Solo explotaba al crear una cámara NUEVA, así que en una DB ya sembrada el
script pasaba de largo por ese branch y el bug quedó escondido.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from aurea_vms.models import repository

_SEED_PATH = Path(__file__).resolve().parents[1] / "tools" / "demo" / "seed_demo.py"


def _load_seed_module():
    """El seed es un script suelto (tools/ no es un paquete), así que se
    carga por ruta."""
    spec = importlib.util.spec_from_file_location("seed_demo", _SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def seed_demo():
    return _load_seed_module()


class TestSeed:
    def test_siembra_las_camaras_con_zona(self, temp_db, seed_demo):
        """La regresión: con el site_id legado esto ni siquiera llegaba a
        correr, levantaba TypeError en la primera cámara."""
        seed_demo.seed("127.0.0.1:8554")

        devices = repository.list_devices()
        assert len(devices) == len(seed_demo.CAMERAS)
        assert all(device.zone_id is not None for device in devices)

    def test_las_camaras_son_visibles_por_el_filtro_global_de_sitio(self, temp_db, seed_demo):
        """El síntoma que produce el bug: cámaras sin zona quedan fuera del
        selector de sitio de la topbar y la demo arranca con la grilla vacía."""
        seed_demo.seed("127.0.0.1:8554")

        por_sitio = {
            site.name: repository.list_devices(site_id=site.id) for site in repository.list_sites()
        }
        esperado: dict[str, int] = {}
        for _name, site_name, *_resto in seed_demo.CAMERAS:
            esperado[site_name] = esperado.get(site_name, 0) + 1

        assert {name: len(devices) for name, devices in por_sitio.items()} == esperado

    def test_una_zona_por_sitio(self, temp_db, seed_demo):
        seed_demo.seed("127.0.0.1:8554")

        sites = repository.list_sites()
        assert len(sites) == len(seed_demo.SITES)
        for site in sites:
            zones = repository.list_zones(site_id=site.id)
            assert [zone.name for zone in zones] == [seed_demo.DEFAULT_ZONE_NAME]

    def test_es_idempotente(self, temp_db, seed_demo):
        """El docstring del script promete que se puede correr todas las
        veces que haga falta sin duplicar nada."""
        seed_demo.seed("127.0.0.1:8554")
        seed_demo.seed("127.0.0.1:8554")

        assert len(repository.list_sites()) == len(seed_demo.SITES)
        assert len(repository.list_devices()) == len(seed_demo.CAMERAS)
        assert len(repository.list_zones()) == len(seed_demo.SITES)
        assert len(repository.list_users()) == len(seed_demo.USERS) + 1  # +admin

    def test_una_camara_sin_zona_se_reasigna(self, temp_db, seed_demo):
        """Caso de una DB sembrada por una versión anterior, o migrada a
        medias: la cámara existe pero quedó sin zona."""
        site = repository.add_site(name="Sala Principal", description="")
        huerfana = repository.add_device(
            name="Mesas 01", ip="127.0.0.1", rtsp_main_url="rtsp://viejo/cam1"
        )
        assert huerfana.zone_id is None

        seed_demo.seed("127.0.0.1:8554")

        reasignada = repository.get_device(huerfana.id)
        assert reasignada.zone_id is not None
        assert repository.get_zone(reasignada.zone_id).site_id == site.id

    def test_no_pisa_una_zona_asignada_a_mano(self, temp_db, seed_demo):
        seed_demo.seed("127.0.0.1:8554")
        device = next(d for d in repository.list_devices() if d.name == "Mesas 01")
        site = next(s for s in repository.list_sites() if s.name == "Anexo VIP")
        propia = repository.add_zone(site_id=site.id, name="Mesas VIP")
        repository.update_device(device.id, zone_id=propia.id)

        seed_demo.seed("127.0.0.1:8554")

        assert repository.get_device(device.id).zone_id == propia.id
