from __future__ import annotations

import pytest

from aurea_vms.models import repository
from aurea_vms.ui.widgets.device_tree import DeviceTreeWidget


def _cam(name: str, zone_id: int | None = None):
    return repository.add_device(
        name=name, ip="10.0.0.1", rtsp_main_url="rtsp://c/x", zone_id=zone_id
    )


@pytest.fixture()
def arbol(qtbot, temp_db):
    def _crear() -> DeviceTreeWidget:
        widget = DeviceTreeWidget()
        qtbot.addWidget(widget)
        return widget

    return _crear


def _sitios(widget: DeviceTreeWidget) -> dict[str, int]:
    """{label del sitio (o grupo "Sin asignar"): cantidad de zonas/hijos}."""
    tree = widget.tree
    return {
        tree.topLevelItem(i).text(0): tree.topLevelItem(i).childCount()
        for i in range(tree.topLevelItemCount())
    }


class TestAgrupacionPorSitioYZona:
    def test_sin_sitios_junta_todo_en_sin_asignar(self, arbol):
        _cam("C1")
        _cam("C2")
        assert _sitios(arbol()) == {"Sin asignar (2)": 2}

    def test_agrupa_por_sitio_y_zona_y_junta_sin_asignar(self, arbol):
        sala = repository.add_site(name="Sala Principal")
        anexo = repository.add_site(name="Anexo VIP")
        zona_sala = repository.add_zone(name="Bóveda", site_id=sala.id)
        zona_anexo = repository.add_zone(name="Recepción", site_id=anexo.id)
        _cam("C1", zona_sala.id)
        _cam("C2", zona_sala.id)
        _cam("C3", zona_anexo.id)
        _cam("C4")

        assert _sitios(arbol()) == {
            "Anexo VIP (1)": 1,
            "Sala Principal (2)": 1,
            "Sin asignar (1)": 1,
        }

    def test_sitio_y_zona_vacios_no_aparecen(self, arbol):
        sala = repository.add_site(name="Sala Principal")
        repository.add_zone(name="Bóveda", site_id=sala.id)
        assert _sitios(arbol()) == {}

    def test_set_site_filter_filtra_el_arbol(self, arbol):
        sala = repository.add_site(name="Sala Principal")
        anexo = repository.add_site(name="Anexo VIP")
        zona_sala = repository.add_zone(name="Bóveda", site_id=sala.id)
        zona_anexo = repository.add_zone(name="Recepción", site_id=anexo.id)
        _cam("C1", zona_sala.id)
        _cam("C2", zona_anexo.id)

        widget = arbol()
        widget.set_site_filter(sala.id)
        assert _sitios(widget) == {"Sala Principal (1)": 1}

        widget.set_site_filter(None)
        assert _sitios(widget) == {"Sala Principal (1)": 1, "Anexo VIP (1)": 1}


class TestBuscador:
    def test_filtra_y_oculta_ramas_sin_coincidencias(self, arbol):
        sala = repository.add_site(name="Sala Principal")
        anexo = repository.add_site(name="Anexo VIP")
        zona_sala = repository.add_zone(name="Bóveda", site_id=sala.id)
        zona_anexo = repository.add_zone(name="Recepción", site_id=anexo.id)
        _cam("Entrada Norte", zona_sala.id)
        _cam("Caja 3", zona_anexo.id)

        widget = arbol()
        widget.search_edit.setText("entrada")

        tree = widget.tree
        estados = {
            tree.topLevelItem(i).text(0): tree.topLevelItem(i).isHidden()
            for i in range(tree.topLevelItemCount())
        }
        assert estados["Sala Principal (1)"] is False
        assert estados["Anexo VIP (1)"] is True

        widget.search_edit.setText("")
        assert all(not tree.topLevelItem(i).isHidden() for i in range(tree.topLevelItemCount()))
