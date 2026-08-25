from __future__ import annotations

from aurea_vms.models import repository
from aurea_vms.ui.dialogs.device_dialog import DeviceDialog


def _dialog(qtbot, initial: dict | None = None) -> DeviceDialog:
    dialog = DeviceDialog(initial=initial)
    qtbot.addWidget(dialog)
    return dialog


class TestSitioEnElDialogo:
    def test_combo_lista_sin_sitio_mas_los_sitios(self, qtbot, temp_db):
        repository.add_site(name="Sala Principal")
        dialog = _dialog(qtbot)

        textos = [dialog.site_combo.itemText(i) for i in range(dialog.site_combo.count())]
        assert textos == ["(Sin sitio)", "Sala Principal"]
        assert dialog.site_combo.currentData() is None

    def test_values_incluye_site_id(self, qtbot, temp_db):
        sala = repository.add_site(name="Sala Principal")
        dialog = _dialog(qtbot)
        dialog.name_edit.setText("Cam 1")
        dialog.ip_edit.setText("10.0.0.5")
        dialog.site_combo.setCurrentIndex(dialog.site_combo.findData(sala.id))

        assert dialog.values()["site_id"] == sala.id

    def test_load_preselecciona_el_sitio(self, qtbot, temp_db):
        sala = repository.add_site(name="Sala Principal")
        dialog = _dialog(
            qtbot,
            initial={"name": "Cam", "ip": "10.0.0.5", "site_id": sala.id},
        )
        assert dialog.site_combo.currentData() == sala.id

    def test_load_sin_sitio_cae_a_sin_sitio(self, qtbot, temp_db):
        repository.add_site(name="Sala Principal")
        dialog = _dialog(qtbot, initial={"name": "Cam", "ip": "10.0.0.5", "site_id": None})
        assert dialog.site_combo.currentData() is None
