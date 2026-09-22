"""Tests de los COUNT agregados y los filtros por sitio del repositorio.

Los dos dashboards traían 200 eventos + todos los dispositivos cada 5 s y
contaban en Python. No era sólo costo: la tarjeta "Eventos registrados"
mostraba `len()` de la página de 200 (o sea 200 para siempre en cuanto
hubiera más), y con el filtro global de sitio activo la tabla mostraba un
subconjunto de los últimos 200 GLOBALES en vez de los últimos 200 de ese
sitio.
"""

from __future__ import annotations

import pytest

from aurea_vms.models import repository
from aurea_vms.models.alarm_event import STATUS_RESOLVED


@pytest.fixture()
def dos_sitios(temp_db):
    """Sala (2 cámaras) y Anexo (1 cámara), más una cámara sin zona."""
    sala = repository.add_site(name="Sala")
    anexo = repository.add_site(name="Anexo")
    zona_sala = repository.add_zone(site_id=sala.id, name="General")
    zona_anexo = repository.add_zone(site_id=anexo.id, name="General")

    def _camara(nombre, zone_id, status):
        device = repository.add_device(
            name=nombre, ip="10.0.0.1", rtsp_main_url="rtsp://c/x", zone_id=zone_id
        )
        repository.update_device_status(device.id, status)
        return device

    return {
        "sala": sala,
        "anexo": anexo,
        "camaras": {
            "sala_1": _camara("Sala 1", zona_sala.id, "online"),
            "sala_2": _camara("Sala 2", zona_sala.id, "offline"),
            "anexo_1": _camara("Anexo 1", zona_anexo.id, "online"),
            "huerfana": _camara("Sin zona", None, "unknown"),
        },
    }


def _evento(device, *, severity="medio", status="nueva", timestamp=100.0):
    row = repository.add_alarm_event(
        device_id=device.id,
        timestamp=timestamp,
        object_class="persona",
        confidence=0.9,
        severity=severity,
    )
    if status != "nueva":
        repository.set_alarm_event_status(row.id, status)
    return row


class TestCountDevicesByStatus:
    def test_agrupa_por_estado(self, dos_sitios):
        assert repository.count_devices_by_status() == {"online": 2, "offline": 1, "unknown": 1}

    def test_filtra_por_sitio(self, dos_sitios):
        sala = repository.count_devices_by_status(site_id=dos_sitios["sala"].id)

        assert sala == {"online": 1, "offline": 1}
        # La cámara sin zona no cuenta para ningún sitio.
        assert "unknown" not in sala

    def test_sin_dispositivos_devuelve_vacio(self, temp_db):
        assert repository.count_devices_by_status() == {}


class TestListDevicesPorSitio:
    def test_el_join_devuelve_solo_las_del_sitio(self, dos_sitios):
        nombres = [d.name for d in repository.list_devices(site_id=dos_sitios["sala"].id)]

        assert nombres == ["Sala 1", "Sala 2"]

    def test_una_camara_sin_zona_no_aparece_en_ningun_sitio(self, dos_sitios):
        for site in repository.list_sites():
            assert "Sin zona" not in [d.name for d in repository.list_devices(site_id=site.id)]


class TestCountAlarmEvents:
    def test_cuenta_toda_la_tabla_y_no_una_pagina(self, dos_sitios):
        """El bug de la tarjeta de totales: se clavaba en el largo de la
        página de 200."""
        for i in range(250):
            _evento(dos_sitios["camaras"]["sala_1"], timestamp=float(i))

        assert repository.count_alarm_events() == 250
        assert len(repository.list_alarm_events(limit=200)) == 200

    def test_filtra_por_sitio(self, dos_sitios):
        _evento(dos_sitios["camaras"]["sala_1"])
        _evento(dos_sitios["camaras"]["sala_2"])
        _evento(dos_sitios["camaras"]["anexo_1"])

        assert repository.count_alarm_events(site_id=dos_sitios["sala"].id) == 2
        assert repository.count_alarm_events(site_id=dos_sitios["anexo"].id) == 1
        assert repository.count_alarm_events() == 3

    def test_filtra_por_camara(self, dos_sitios):
        _evento(dos_sitios["camaras"]["sala_1"])
        _evento(dos_sitios["camaras"]["sala_2"])

        assert repository.count_alarm_events(device_id=dos_sitios["camaras"]["sala_1"].id) == 1

    def test_filtra_por_severidad(self, dos_sitios):
        _evento(dos_sitios["camaras"]["sala_1"], severity="critico")
        _evento(dos_sitios["camaras"]["sala_1"], severity="medio")

        assert repository.count_alarm_events(severity="critico") == 1

    def test_combina_sitio_y_severidad(self, dos_sitios):
        _evento(dos_sitios["camaras"]["sala_1"], severity="critico")
        _evento(dos_sitios["camaras"]["anexo_1"], severity="critico")

        assert repository.count_alarm_events(site_id=dos_sitios["sala"].id, severity="critico") == 1

    def test_pendientes_excluye_las_resueltas(self, dos_sitios):
        _evento(dos_sitios["camaras"]["sala_1"])
        _evento(dos_sitios["camaras"]["sala_1"], status=STATUS_RESOLVED)

        assert repository.count_pending_alarm_events() == 1
        assert repository.count_alarm_events() == 2

    def test_pendientes_por_sitio(self, dos_sitios):
        _evento(dos_sitios["camaras"]["sala_1"])
        _evento(dos_sitios["camaras"]["anexo_1"])

        assert repository.count_pending_alarm_events(site_id=dos_sitios["sala"].id) == 1

    def test_sin_eventos_devuelve_cero_y_no_none(self, temp_db):
        assert repository.count_alarm_events() == 0
        assert repository.count_pending_alarm_events() == 0


class TestListAlarmEventsFiltrado:
    def test_el_limite_se_aplica_DESPUES_del_filtro_de_sitio(self, dos_sitios):
        """El bug: sin filtro en la consulta, se traían los últimos 200
        globales y después se descartaban en Python los de otros sitios, así
        que con un sitio seleccionado la tabla mostraba un puñado arbitrario
        en vez de sus últimos 200."""
        for i in range(250):
            _evento(dos_sitios["camaras"]["anexo_1"], timestamp=float(i))
        for i in range(5):
            _evento(dos_sitios["camaras"]["sala_1"], timestamp=float(1000 + i))
        for i in range(250):
            _evento(dos_sitios["camaras"]["anexo_1"], timestamp=float(2000 + i))

        de_sala = repository.list_alarm_events(limit=200, site_id=dos_sitios["sala"].id)

        # Los 5 de Sala aparecen enteros, aunque estén sepultados entre 500
        # eventos de Anexo.
        assert len(de_sala) == 5

    def test_filtra_por_camara(self, dos_sitios):
        _evento(dos_sitios["camaras"]["sala_1"])
        _evento(dos_sitios["camaras"]["sala_2"])

        eventos = repository.list_alarm_events(device_id=dos_sitios["camaras"]["sala_2"].id)

        assert [e.device_id for e in eventos] == [dos_sitios["camaras"]["sala_2"].id]

    def test_los_mas_recientes_primero(self, dos_sitios):
        primero = _evento(dos_sitios["camaras"]["sala_1"], timestamp=1.0)
        ultimo = _evento(dos_sitios["camaras"]["sala_1"], timestamp=2.0)

        eventos = repository.list_alarm_events()

        assert [e.id for e in eventos] == [ultimo.id, primero.id]
