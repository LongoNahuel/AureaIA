"""Tests de las constraints e índices que el esquema no tenía (D3-D5).

Hasta la Fase 9 el esquema permitía estados que el código daba por
imposibles: una regla de alarma sin analizador ni severidad, dos
configuraciones de analítica para la misma cámara y analizador (que rompían
el arranque con `MultipleResultsFound`), y dos zonas con el mismo nombre en
el mismo sitio.
"""

from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy.exc
from sqlalchemy import inspect as sa_inspect

from aurea_vms.models import db as db_module
from aurea_vms.models import repository
from aurea_vms.models.alarm_rule import AlarmRule
from aurea_vms.models.analytics_config import AnalyticsConfig
from aurea_vms.models.errors import DuplicateError


def _indices(tabla: str) -> set[str]:
    with db_module._engine.connect() as conn:
        return {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (tabla,)
            )
            if row[0] and not row[0].startswith("sqlite_autoindex")
        }


def _camara(nombre="Cam"):
    return repository.add_device(name=nombre, ip="10.0.0.1", rtsp_main_url="rtsp://c/x")


class TestAlarmRuleNoAdmiteNulls:
    """D3: venía de la API legacy `Column`, donde el default es NULLABLE."""

    @pytest.mark.parametrize(
        "columna",
        [
            "analyzer_name",
            "object_classes",
            "min_confidence",
            "cooldown_seconds",
            "severity",
            "schedule_days",
            "actions",
            "enabled",
        ],
    )
    def test_la_columna_es_not_null(self, temp_db, columna):
        columnas = {c["name"]: c for c in sa_inspect(db_module._engine).get_columns("alarm_rules")}

        assert columnas[columna]["nullable"] is False

    def test_el_horario_sigue_siendo_opcional(self, temp_db):
        """schedule_start/end vacíos significan "sin restricción horaria":
        esos SÍ tienen que poder ser NULL."""
        columnas = {c["name"]: c for c in sa_inspect(db_module._engine).get_columns("alarm_rules")}

        assert columnas["schedule_start"]["nullable"] is True
        assert columnas["schedule_end"]["nullable"] is True

    def test_una_regla_global_sigue_sin_camara(self, temp_db):
        """device_id NULL = la regla aplica a todas las cámaras."""
        regla = repository.add_alarm_rule(analyzer_name="face_detection")

        assert regla.device_id is None

    def test_el_modelo_usa_la_api_moderna(self):
        """Guarda de regresión: con `Column(String(60))` sin nullable=False,
        SQLAlchemy vuelve a dejar la columna nullable sin avisar."""
        import inspect as py_inspect

        fuente = py_inspect.getsource(AlarmRule)
        assert "mapped_column" in fuente
        assert ": Any = Column(" not in fuente


class TestUnicidadDeAnalyticsConfig:
    """D4: `upsert_analytics_config` y `get_analytics_config_for` ya asumían
    esta unicidad con `.one_or_none()` sin que el esquema la garantizara."""

    def test_la_base_rechaza_el_duplicado(self, temp_db):
        """Se inserta por debajo del repositorio a propósito: lo que se está
        probando es la constraint del ESQUEMA. `get_session` es la primitiva
        de bajo nivel y no traduce excepciones — eso lo hace `repository` en
        su frontera (ver test_repository_errores.py)."""
        device = _camara()
        repository.upsert_analytics_config(device.id, "face_detection")

        with pytest.raises(sqlalchemy.exc.IntegrityError), db_module.get_session() as session:
            session.add(
                AnalyticsConfig(
                    device_id=device.id,
                    analyzer_name="face_detection",
                    object_classes=[],
                    params={},
                )
            )

    def test_el_upsert_sigue_actualizando_en_vez_de_insertar(self, temp_db):
        device = _camara()
        repository.upsert_analytics_config(device.id, "face_detection", confidence_threshold=0.4)
        repository.upsert_analytics_config(device.id, "face_detection", confidence_threshold=0.8)

        configs = repository.list_analytics_configs(device.id)
        assert len(configs) == 1
        assert configs[0].confidence_threshold == 0.8

    def test_la_misma_analitica_en_otra_camara_si_se_puede(self, temp_db):
        primera, segunda = _camara("Uno"), _camara("Dos")
        repository.upsert_analytics_config(primera.id, "face_detection")
        repository.upsert_analytics_config(segunda.id, "face_detection")

        assert len(repository.list_analytics_configs()) == 2


class TestUnicidadDeZonas:
    def test_no_se_repite_el_nombre_dentro_del_sitio(self, temp_db):
        sitio = repository.add_site(name="Sala")
        repository.add_zone(site_id=sitio.id, name="General")

        with pytest.raises(DuplicateError):
            repository.add_zone(site_id=sitio.id, name="General")

    def test_el_mismo_nombre_en_otro_sitio_si_se_puede(self, temp_db):
        sala = repository.add_site(name="Sala")
        anexo = repository.add_site(name="Anexo")
        repository.add_zone(site_id=sala.id, name="General")
        repository.add_zone(site_id=anexo.id, name="General")

        assert len(repository.list_zones()) == 2

    def test_renombrar_a_uno_que_ya_existe_falla(self, temp_db):
        sitio = repository.add_site(name="Sala")
        repository.add_zone(site_id=sitio.id, name="General")
        otra = repository.add_zone(site_id=sitio.id, name="Mesas")

        with pytest.raises(DuplicateError):
            repository.update_zone(otra.id, name="General")


class TestIndices:
    def test_alarm_events_tiene_indice_por_estado(self, temp_db):
        """Lo filtra el tile de alarmas pendientes cada 5 segundos."""
        assert "ix_alarm_events_status" in _indices("alarm_events")

    def test_no_sobra_el_indice_de_device_id(self, temp_db):
        """Lo cubre el compuesto ix_alarm_events_device_ts, que empieza por
        device_id: uno de más es escritura de más en cada alarma."""
        indices = _indices("alarm_events")

        assert "ix_alarm_events_device_ts" in indices
        assert "ix_alarm_events_device_id" not in indices

    def test_devices_tiene_indice_por_zona(self, temp_db):
        assert "ix_devices_zone_id" in _indices("devices")


class TestCascadas:
    """Repuestos: los tenía el test_db_migrations viejo y la reescritura de
    la Fase 8 se los llevó puestos sin que nadie lo notara, porque la
    cobertura total subió por otras adiciones."""

    def test_borrar_zona_deja_sus_camaras_sin_zona_pero_vivas(self, temp_db):
        sitio = repository.add_site(name="Sala")
        zona = repository.add_zone(site_id=sitio.id, name="General")
        device = repository.add_device(
            name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x", zone_id=zona.id
        )

        repository.delete_zone(zona.id)

        superviviente = repository.get_device(device.id)
        assert superviviente is not None
        assert superviviente.zone_id is None

    def test_borrar_sitio_borra_sus_zonas_pero_no_sus_camaras(self, temp_db):
        sitio = repository.add_site(name="Sala")
        zona = repository.add_zone(site_id=sitio.id, name="General")
        device = repository.add_device(
            name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x", zone_id=zona.id
        )

        repository.delete_site(sitio.id)

        assert repository.list_zones() == []
        superviviente = repository.get_device(device.id)
        assert superviviente is not None
        assert superviviente.zone_id is None

    def test_borrar_zona_funciona_aunque_el_fk_no_tenga_set_null(self, temp_db, tmp_path):
        """Una base migrada puede tener el FK sin ON DELETE SET NULL, porque
        el ALTER TABLE ADD COLUMN del sistema ad-hoc no siempre lo llevaba.
        Por eso `delete_zone` nulifica en Python antes de borrar, en vez de
        confiar en la cascada del esquema."""
        sitio = repository.add_site(name="Sala")
        zona = repository.add_zone(site_id=sitio.id, name="General")
        device = repository.add_device(
            name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x", zone_id=zona.id
        )
        # Se fuerza el escenario: FK sin SET NULL en una copia de la base.
        with db_module._engine.connect() as conn:
            ruta = conn.engine.url.database
        db_module._engine.dispose()
        con = sqlite3.connect(ruta)
        con.execute("PRAGMA foreign_keys=ON")
        con.close()

        repository.delete_zone(zona.id)

        assert repository.get_device(device.id).zone_id is None
