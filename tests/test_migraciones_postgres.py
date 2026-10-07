"""Las migraciones y el repositorio contra un PostgreSQL real.

Es lo que respalda decir "portable a PostgreSQL". Corre con
`pytest -m integration` cuando hay un servidor:

    AUREA_TEST_PG_URL=postgresql+psycopg:///postgres pytest -m integration

La URL es la de una base donde el usuario puede hacer CREATE DATABASE: cada
test crea una base propia con nombre al azar y la tira al terminar. Sin la
variable o sin el driver (extra [postgres]), se saltean.
"""

from __future__ import annotations

import dataclasses
import os
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from test_db_migrations import _cabeza

from aurea_vms.core import credential_store
from aurea_vms.models import db as db_module
from aurea_vms.models import repository
from aurea_vms.models.db import NAMING_CONVENTION, Base, _config_de_alembic
from aurea_vms.models.errors import DuplicateError

pytestmark = pytest.mark.integration

URL_ADMIN = os.environ.get("AUREA_TEST_PG_URL")
if not URL_ADMIN:
    pytest.skip("AUREA_TEST_PG_URL no está definida", allow_module_level=True)
pytest.importorskip("psycopg", reason="falta el driver: pip install -e '.[postgres]'")


@pytest.fixture()
def url_pg():
    """Una base vacia y descartable en el servidor de AUREA_TEST_PG_URL."""
    nombre = f"aurea_test_{uuid.uuid4().hex[:12]}"
    admin = sa.create_engine(URL_ADMIN, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{nombre}"')
    url = sa.make_url(URL_ADMIN).set(database=nombre)
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        if db_module._engine is not None:
            db_module._engine.dispose()
        db_module._engine = None
        db_module._SessionLocal = None
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture()
def app_en_pg(url_pg, tmp_path, monkeypatch):
    """La app apuntando a la base de url_pg, como con AUREA_DB_URL."""
    settings = dataclasses.replace(
        db_module.settings,
        data_dir=tmp_path,
        media_dir=tmp_path / "media",
        db_url=url_pg,
    )
    monkeypatch.setattr(db_module, "settings", settings)
    monkeypatch.setattr(credential_store, "settings", settings)
    credential_store.reset_cache()
    db_module.init_db(force=True)
    yield url_pg
    credential_store.reset_cache()


def _deriva(url: str) -> list:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            contexto = MigrationContext.configure(
                conn, opts={"compare_type": True, "naming_convention": NAMING_CONVENTION}
            )
            return compare_metadata(contexto, Base.metadata)
    finally:
        engine.dispose()


class TestMigraciones:
    def test_una_base_vacia_llega_a_la_cabeza(self, app_en_pg):
        assert db_module.revision_actual(db_module._engine) == _cabeza()

    def test_el_esquema_migrado_es_el_de_los_modelos(self, app_en_pg):
        assert _deriva(app_en_pg) == []

    def test_ida_y_vuelta_hasta_la_ultima_revision_reversible(self, app_en_pg):
        # 0002 consume datos legados y no tiene vuelta: el piso es ella.
        db_module._engine.dispose()
        config = _config_de_alembic(app_en_pg)
        command.downgrade(config, "0002_datos_legados")
        command.upgrade(config, "head")
        assert _deriva(app_en_pg) == []

    def test_la_cabeza_no_hace_backup_ni_vuelve_a_migrar(self, app_en_pg, tmp_path):
        db_module.init_db(force=True)
        assert db_module.revision_actual(db_module._engine) == _cabeza()
        assert not (tmp_path / "backups").exists()


class TestRepositorio:
    def _camara(self, nombre: str = "Ruleta 1"):
        sitio = repository.add_site(name="Casino")
        zona = repository.add_zone(site_id=sitio.id, name="Sala", critical=True)
        camara = repository.add_device(
            name=nombre,
            ip="10.0.0.7",
            rtsp_main_url="rtsp://10.0.0.7/main",
            password="secreta",
            zone_id=zona.id,
        )
        return sitio, zona, camara

    def test_la_jerarquia_y_la_credencial_cifrada(self, app_en_pg):
        sitio, zona, camara = self._camara()
        assert [z.id for z in repository.list_zones(site_id=sitio.id)] == [zona.id]
        assert [d.id for d in repository.list_devices(site_id=sitio.id)] == [camara.id]
        assert repository.get_zone(zona.id).critical is True
        assert repository.get_device(camara.id).password == "secreta"
        with db_module._engine.connect() as conn:
            guardada = conn.execute(
                sa.text("SELECT password FROM devices WHERE id = :id"), {"id": camara.id}
            ).scalar_one()
        assert guardada.startswith(credential_store.PREFIJO)

    def test_config_json_y_booleanos(self, app_en_pg):
        _, _, camara = self._camara()
        config = repository.upsert_analytics_config(
            camara.id, "monitor_tamper", enabled=True, params={"zones": [[1, 2, 3, 4]]}
        )
        releida = repository.get_analytics_config(config.id)
        assert releida.enabled is True
        assert releida.params == {"zones": [[1, 2, 3, 4]]}

    def test_borrar_la_zona_deja_la_camara_sin_zona(self, app_en_pg):
        _, zona, camara = self._camara()
        repository.delete_zone(zona.id)
        assert repository.get_device(camara.id).zone_id is None

    def test_un_duplicado_es_error_de_dominio(self, app_en_pg):
        repository.add_site(name="Casino")
        with pytest.raises(DuplicateError):
            repository.add_site(name="Casino")

    def test_el_contador_de_intentos_usa_returning(self, app_en_pg):
        usuario = repository.add_user(username="operador", password_hash="x", role="operador")
        assert repository.sumar_intento_fallido(usuario.id) == 1
        assert repository.sumar_intento_fallido(usuario.id) == 2

    def test_alarmas_por_camara(self, app_en_pg):
        _, _, camara = self._camara()
        regla = repository.add_alarm_rule(device_id=camara.id, analyzer_name="monitor_tamper")
        repository.add_alarm_event(
            rule_id=regla.id,
            device_id=camara.id,
            timestamp=1.0,
            object_class="fichas_tras_no_va_mas",
            confidence=0.9,
        )
        assert repository.count_alarm_events_by_device() == {camara.id: 1}
        assert repository.list_alarm_event_classes() == ["fichas_tras_no_va_mas"]
