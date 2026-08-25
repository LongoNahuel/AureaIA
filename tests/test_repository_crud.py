from __future__ import annotations

from aurea_vms.models import repository


def _device(**overrides) -> int:
    fields = {
        "name": "Cam",
        "ip": "192.168.1.60",
        "rtsp_main_url": "rtsp://192.168.1.60:554/live",
    }
    fields.update(overrides)
    return repository.add_device(**fields).id


class TestAnalyticsConfigs:
    def test_upsert_crea_y_actualiza(self, temp_db):
        device_id = _device()

        config = repository.upsert_analytics_config(
            device_id, "motion_detection", enabled=True, params={"sensitivity": 30}
        )
        assert config.id is not None

        actualizado = repository.upsert_analytics_config(
            device_id, "motion_detection", params={"sensitivity": 70}
        )
        assert actualizado.id == config.id
        assert actualizado.params == {"sensitivity": 70}
        assert len(repository.list_analytics_configs()) == 1

    def test_get_for_y_filtro_por_device(self, temp_db):
        d1, d2 = _device(), _device(ip="192.168.1.61")
        repository.upsert_analytics_config(d1, "motion_detection")
        repository.upsert_analytics_config(d2, "face_detection")

        assert repository.get_analytics_config_for(d1, "motion_detection") is not None
        assert repository.get_analytics_config_for(d1, "face_detection") is None
        assert len(repository.list_analytics_configs(device_id=d2)) == 1

    def test_set_enabled(self, temp_db):
        device_id = _device()
        config = repository.upsert_analytics_config(device_id, "motion_detection", enabled=True)

        repository.set_analytics_config_enabled(config.id, False)
        assert repository.get_analytics_config(config.id).enabled is False


class TestAlarmRules:
    def test_add_y_get(self, temp_db):
        rule = repository.add_alarm_rule(analyzer_name="face_detection", severity="alto")
        fetched = repository.get_alarm_rule(rule.id)
        assert fetched is not None
        assert fetched.severity == "alto"

    def test_list_alarm_rules_for_incluye_globales_y_excluye_deshabilitadas(self, temp_db):
        device_id = _device()
        otro = _device(ip="192.168.1.62")

        repository.add_alarm_rule(device_id=device_id, analyzer_name="face_detection")
        repository.add_alarm_rule(device_id=None, analyzer_name="face_detection")  # global
        repository.add_alarm_rule(device_id=otro, analyzer_name="face_detection")  # otra cam
        repository.add_alarm_rule(
            device_id=device_id, analyzer_name="face_detection", enabled=False
        )
        repository.add_alarm_rule(device_id=device_id, analyzer_name="motion_detection")

        rules = repository.list_alarm_rules_for(device_id, "face_detection")
        assert len(rules) == 2

    def test_update_y_delete(self, temp_db):
        rule = repository.add_alarm_rule(analyzer_name="face_detection")

        repository.update_alarm_rule(rule.id, severity="critico")
        assert repository.get_alarm_rule(rule.id).severity == "critico"

        repository.set_alarm_rule_enabled(rule.id, False)
        assert repository.get_alarm_rule(rule.id).enabled is False

        repository.delete_alarm_rule(rule.id)
        assert repository.get_alarm_rule(rule.id) is None


class TestAlarmEvents:
    def _event(self, device_id: int, rule_id: int, ts: float):
        return repository.add_alarm_event(
            rule_id=rule_id,
            device_id=device_id,
            timestamp=ts,
            object_class="cara",
            confidence=0.8,
        )

    def test_list_devuelve_los_mas_nuevos_primero(self, temp_db):
        device_id = _device()
        rule = repository.add_alarm_rule(analyzer_name="face_detection")

        primero = self._event(device_id, rule.id, 100.0)
        segundo = self._event(device_id, rule.id, 200.0)

        events = repository.list_alarm_events()
        assert [e.id for e in events] == [segundo.id, primero.id]

    def test_limit(self, temp_db):
        device_id = _device()
        rule = repository.add_alarm_rule(analyzer_name="face_detection")
        for i in range(5):
            self._event(device_id, rule.id, float(i))

        assert len(repository.list_alarm_events(limit=3)) == 3

    def test_update_y_estado(self, temp_db):
        device_id = _device()
        rule = repository.add_alarm_rule(analyzer_name="face_detection")
        event = self._event(device_id, rule.id, 100.0)

        repository.update_alarm_event(event.id, notes="revisado por guardia")
        repository.set_alarm_event_status(event.id, "resuelta")

        fetched = repository.get_alarm_event(event.id)
        assert fetched.notes == "revisado por guardia"
        assert fetched.status == "resuelta"


class TestUsers:
    def test_ciclo_completo(self, temp_db):
        assert repository.count_users() == 0

        user = repository.add_user(username="ana", password_hash="h", salt="s", role="admin")
        assert repository.count_users() == 1
        assert repository.get_user_by_username("ana").id == user.id
        assert repository.get_user_by_username("nadie") is None

        repository.update_user(user.id, role="operador")
        assert repository.list_users()[0].role == "operador"

        repository.delete_user(user.id)
        assert repository.count_users() == 0
