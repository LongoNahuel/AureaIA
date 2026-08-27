from __future__ import annotations

from aurea_vms.models import repository
from aurea_vms.ui.dialogs.device_dialog import DeviceDialog


def _dialog(qtbot, initial: dict | None = None) -> DeviceDialog:
    dialog = DeviceDialog(initial=initial)
    qtbot.addWidget(dialog)
    return dialog


class TestSitioYZonaEnElDialogo:
    def test_combo_de_sitio_lista_sin_asignar_mas_los_sitios(self, qtbot, temp_db):
        repository.add_site(name="Sala Principal")
        dialog = _dialog(qtbot)

        textos = [dialog.site_combo.itemText(i) for i in range(dialog.site_combo.count())]
        assert textos == ["(sin asignar)", "Sala Principal"]
        assert dialog.site_combo.currentData() is None

    def test_combo_de_zona_se_arma_al_elegir_sitio(self, qtbot, temp_db):
        sala = repository.add_site(name="Sala Principal")
        repository.add_zone(name="Bóveda", site_id=sala.id)
        dialog = _dialog(qtbot)

        dialog.site_combo.setCurrentIndex(dialog.site_combo.findData(sala.id))

        textos = [dialog.zone_combo.itemText(i) for i in range(dialog.zone_combo.count())]
        assert textos == ["(sin asignar)", "Bóveda"]

    def test_values_incluye_zone_id(self, qtbot, temp_db):
        sala = repository.add_site(name="Sala Principal")
        zona = repository.add_zone(name="Bóveda", site_id=sala.id)
        dialog = _dialog(qtbot)
        dialog.name_edit.setText("Cam 1")
        dialog.ip_edit.setText("10.0.0.5")
        dialog.site_combo.setCurrentIndex(dialog.site_combo.findData(sala.id))
        dialog.zone_combo.setCurrentIndex(dialog.zone_combo.findData(zona.id))

        assert dialog.values()["zone_id"] == zona.id

    def test_load_preselecciona_sitio_y_zona(self, qtbot, temp_db):
        sala = repository.add_site(name="Sala Principal")
        zona = repository.add_zone(name="Bóveda", site_id=sala.id)
        dialog = _dialog(
            qtbot,
            initial={"name": "Cam", "ip": "10.0.0.5", "zone_id": zona.id},
        )
        assert dialog.site_combo.currentData() == sala.id
        assert dialog.zone_combo.currentData() == zona.id

    def test_load_sin_zona_cae_a_sin_asignar(self, qtbot, temp_db):
        repository.add_site(name="Sala Principal")
        dialog = _dialog(qtbot, initial={"name": "Cam", "ip": "10.0.0.5", "zone_id": None})
        assert dialog.zone_combo.currentData() is None
