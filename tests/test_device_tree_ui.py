from __future__ import annotations

import pytest

from aurea_vms.models import repository
from aurea_vms.ui.widgets.device_tree import DeviceTreeWidget


def _cam(name: str, site_id: int | None = None):
    return repository.add_device(
        name=name, ip="10.0.0.1", rtsp_main_url="rtsp://c/x", site_id=site_id
    )


@pytest.fixture()
def arbol(qtbot, temp_db):
    def _crear() -> DeviceTreeWidget:
        widget = DeviceTreeWidget()
        qtbot.addWidget(widget)
        return widget

    return _crear


def _grupos(widget: DeviceTreeWidget) -> dict[str, int]:
    """{label del grupo: cantidad de hijos}."""
    tree = widget.tree
    return {
        tree.topLevelItem(i).text(0): tree.topLevelItem(i).childCount()
        for i in range(tree.topLevelItemCount())
    }


class TestAgrupacionPorSitio:
    def test_sin_sitios_mantiene_arbol_plano(self, arbol):
        _cam("C1")
        _cam("C2")
        assert _grupos(arbol()) == {"Cámaras (2)": 2}

    def test_agrupa_por_sitio_y_junta_sin_sitio(self, arbol):
        sala = repository.add_site(name="Sala Principal")
        anexo = repository.add_site(name="Anexo VIP")
        _cam("C1", sala.id)
        _cam("C2", sala.id)
        _cam("C3", anexo.id)
        _cam("C4")

        assert _grupos(arbol()) == {
            "Anexo VIP (1)": 1,
            "Sala Principal (2)": 2,
            "Sin sitio (1)": 1,
        }

    def test_sitio_vacio_aparece_con_cero(self, arbol):
        repository.add_site(name="Sala Principal")
        assert _grupos(arbol()) == {"Sala Principal (0)": 0}

    def test_reload_filtrado_por_sitio(self, arbol):
        sala = repository.add_site(name="Sala Principal")
        repository.add_site(name="Anexo VIP")
        _cam("C1", sala.id)
        _cam("C2")

        widget = arbol()
        widget.reload(site_id=sala.id)
        assert _grupos(widget) == {"Sala Principal (1)": 1}


class TestBuscador:
    def test_filtra_y_oculta_grupos_sin_coincidencias(self, arbol):
        sala = repository.add_site(name="Sala Principal")
        anexo = repository.add_site(name="Anexo VIP")
        _cam("Entrada Norte", sala.id)
        _cam("Caja 3", anexo.id)

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
