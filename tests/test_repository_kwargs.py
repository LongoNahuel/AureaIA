"""Tests de la whitelist de kwargs de los `update_*` (D6).

`setattr` sobre un objeto de SQLAlchemy con un nombre que no es columna crea
alegremente un atributo Python que **nunca llega al UPDATE**: el clásico
"guardé y no pasó nada", sin error, sin log y sin forma de darse cuenta salvo
mirando la base. Los `add_*` nunca tuvieron el problema — el constructor
declarativo levanta `TypeError` ante un kwarg desconocido — así que esto es
consistencia, no una regla nueva.

El riesgo del cambio son los 10 call-sites que pasan `**dict` dinámico desde
los diálogos: una clave vieja que hoy se pierde en silencio pasaría a romper
el guardado en la cara del usuario. `TestLosDialogosMandanColumnasReales` es
la guarda contra eso.
"""

from __future__ import annotations

import pytest

from aurea_vms.models import repository
from aurea_vms.models.alarm_rule import AlarmRule
from aurea_vms.models.device import Device
from aurea_vms.models.site import Site
from aurea_vms.models.user import User
from aurea_vms.models.zone import Zone


@pytest.fixture()
def datos(temp_db):
    sitio = repository.add_site(name="Sala")
    zona = repository.add_zone(site_id=sitio.id, name="General")
    device = repository.add_device(
        name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x", zone_id=zona.id
    )
    regla = repository.add_alarm_rule(device_id=device.id, analyzer_name="face_detection")
    evento = repository.add_alarm_event(
        device_id=device.id, timestamp=1.0, object_class="cara", confidence=0.9
    )
    usuario = repository.add_user(username="op", password_hash="h", salt="s", role="operador")
    return {
        "sitio": sitio,
        "zona": zona,
        "device": device,
        "regla": regla,
        "evento": evento,
        "usuario": usuario,
    }


class TestUnKwargDesconocidoLevanta:
    def test_update_site(self, datos):
        with pytest.raises(ValueError, match="nombre"):
            repository.update_site(datos["sitio"].id, nombre="Sala 2")  # es `name`

    def test_update_zone(self, datos):
        with pytest.raises(ValueError):
            repository.update_zone(datos["zona"].id, critico=True)  # es `critical`

    def test_update_device(self, datos):
        """El caso exacto que rompió el seed de demo: site_id dejó de ser
        columna de Device cuando entraron las zonas."""
        with pytest.raises(ValueError, match="site_id"):
            repository.update_device(datos["device"].id, site_id=1)

    def test_update_alarm_rule(self, datos):
        with pytest.raises(ValueError):
            repository.update_alarm_rule(datos["regla"].id, severidad="critico")

    def test_update_alarm_event(self, datos):
        with pytest.raises(ValueError):
            repository.update_alarm_event(datos["evento"].id, estado="resuelta")

    def test_update_user(self, datos):
        with pytest.raises(ValueError):
            repository.update_user(datos["usuario"].id, rol="admin")

    def test_upsert_analytics_config(self, datos):
        with pytest.raises(ValueError):
            repository.upsert_analytics_config(datos["device"].id, "face_detection", umbral=0.5)

    def test_el_mensaje_dice_cual_sobra_y_cuales_valen(self, datos):
        with pytest.raises(ValueError) as error:
            repository.update_device(datos["device"].id, site_id=1)

        mensaje = str(error.value)
        assert "site_id" in mensaje
        assert "zone_id" in mensaje  # la que probablemente quería usar

    def test_no_escribe_nada_de_lo_que_venia_en_el_mismo_lote(self, datos):
        """Levanta ANTES de abrir la sesión: un lote con una clave mala no
        guarda a medias."""
        with pytest.raises(ValueError):
            repository.update_device(datos["device"].id, name="Nuevo nombre", site_id=1)

        assert repository.get_device(datos["device"].id).name == "Cam"


class TestLoBuenoSigueAndando:
    def test_un_update_valido_guarda(self, datos):
        repository.update_device(datos["device"].id, name="Renombrada", port=8554)

        device = repository.get_device(datos["device"].id)
        assert (device.name, device.port) == ("Renombrada", 8554)

    def test_un_id_inexistente_sigue_siendo_un_no_op(self, datos):
        repository.update_device(99999, name="Fantasma")  # no debe levantar

    def test_un_update_vacio_no_levanta(self, datos):
        repository.update_device(datos["device"].id)


class TestLosDialogosMandanColumnasReales:
    """Guarda permanente sobre los 10 call-sites que pasan `**dict` dinámico:
    con la whitelist, una clave vieja rompe el guardado en la cara del
    usuario. Acá se caza en el CI, no en producción."""

    @pytest.mark.parametrize(
        ("modelo", "claves"),
        [
            # DeviceDialog.values() -> add_device / update_device
            (
                Device,
                {
                    "name",
                    "zone_id",
                    "device_type",
                    "channel",
                    "ip",
                    "port",
                    "username",
                    "password",
                    "rtsp_main_url",
                    "rtsp_sub_url",
                    "onvif_port",
                    "has_ptz",
                },
            ),
            # device_management._on_add_discovered() suma la metadata ONVIF
            (Device, {"manufacturer", "model", "firmware_version", "serial_number"}),
            # device_management._on_edit() arma el dict inicial del diálogo
            (Device, {"device_type", "name", "zone_id", "channel", "ip", "port"}),
            # _SiteDialog.values()
            (Site, {"name", "description"}),
            # _ZoneDialog.values()
            (Zone, {"site_id", "name", "critical"}),
            # AlarmRuleDialog.values()
            (
                AlarmRule,
                {
                    "device_id",
                    "analyzer_name",
                    "object_classes",
                    "min_confidence",
                    "cooldown_seconds",
                    "severity",
                    "schedule_days",
                    "schedule_start",
                    "schedule_end",
                    "actions",
                    "enabled",
                },
            ),
            # user_management_module
            (User, {"username", "password_hash", "salt", "role"}),
        ],
    )
    def test_las_claves_son_columnas_del_modelo(self, modelo, claves):
        assert claves <= repository.columnas_de(modelo)

    def test_el_dialogo_de_dispositivo_manda_exactamente_lo_que_dice(self, qtbot, temp_db):
        """Se instancia el diálogo de verdad y se leen sus values(): si
        alguien agrega un campo al formulario y se olvida de la columna, esto
        lo caza."""
        from aurea_vms.ui.dialogs.device_dialog import DeviceDialog

        dialog = DeviceDialog()
        qtbot.addWidget(dialog)

        assert set(dialog.values()) <= repository.columnas_de(Device)

    def test_el_dialogo_de_regla_manda_exactamente_lo_que_dice(self, qtbot, temp_db):
        from aurea_vms.ui.dialogs.alarm_rule_dialog import AlarmRuleDialog

        dialog = AlarmRuleDialog()
        qtbot.addWidget(dialog)

        assert set(dialog.values()) <= repository.columnas_de(AlarmRule)
