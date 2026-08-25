from __future__ import annotations

from aurea_vms.models import repository


def _setup_completo():
    """Camara con config de analitica, regla propia, regla global y un evento."""
    device = repository.add_device(
        name="Cam", ip="192.168.1.70", rtsp_main_url="rtsp://192.168.1.70/live"
    )
    config = repository.upsert_analytics_config(device.id, "motion_detection")
    rule_propia = repository.add_alarm_rule(device_id=device.id, analyzer_name="motion_detection")
    rule_global = repository.add_alarm_rule(device_id=None, analyzer_name="motion_detection")
    event = repository.add_alarm_event(
        rule_id=rule_propia.id,
        device_id=device.id,
        timestamp=100.0,
        object_class="movimiento",
        confidence=1.0,
    )
    return device, config, rule_propia, rule_global, event


class TestCascadaDeDevice:
    def test_borrar_device_limpia_configs_reglas_y_eventos(self, temp_db):
        device, config, rule_propia, rule_global, event = _setup_completo()

        repository.delete_device(device.id)

        assert repository.get_analytics_config(config.id) is None
        assert repository.get_alarm_rule(rule_propia.id) is None
        assert repository.get_alarm_event(event.id) is None
        # La regla global (device_id NULL) no pertenece a la camara borrada.
        assert repository.get_alarm_rule(rule_global.id) is not None


class TestReglaBorradaPreservaHistorial:
    def test_borrar_regla_deja_el_evento_sin_rule_id(self, temp_db):
        _device, _config, rule_propia, _rule_global, event = _setup_completo()

        repository.delete_alarm_rule(rule_propia.id)

        preservado = repository.get_alarm_event(event.id)
        assert preservado is not None
        assert preservado.rule_id is None
        # La severidad copiada al momento del disparo sigue intacta.
        assert preservado.severity == "medio"
