"""Tests del versionado de esquema con Alembic.

Cubre los tres caminos por los que puede entrar una base a `init_db`:

- **nueva**: no hay tablas, las crea la revisión baseline;
- **legada**: tiene tablas pero no `alembic_version` (venía del sistema
  ad-hoc de `create_all` + ALTER TABLE) — se adopta y se marca;
- **ya versionada**: aplica lo que falte, y si no falta nada corta temprano.

Antes de la Fase 8 este archivo probaba `_apply_adhoc_migrations` con un
esquema viejo escrito a mano. Ese esquema sigue acá, porque es exactamente
la base que hay que poder adoptar.
"""

from __future__ import annotations

import sqlite3

import pytest
from alembic.script import ScriptDirectory

from aurea_vms.migrations import BASELINE_REVISION, MIGRATIONS_DIR
from aurea_vms.models import db as db_module
from aurea_vms.models import repository

# Base de desarrollo anterior a la jerarquía Sitio->Zona: devices sin
# zone_id ni metadata de hardware, sites sin description, sin tabla zones, y
# con el site_id legado colgando de la cámara.
ESQUEMA_VIEJO = """
CREATE TABLE sites (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(120) NOT NULL UNIQUE
);
CREATE TABLE devices (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    site_id INTEGER,
    ip VARCHAR(64) NOT NULL,
    port INTEGER NOT NULL,
    username VARCHAR(120) NOT NULL,
    password VARCHAR(120) NOT NULL,
    rtsp_main_url VARCHAR(500) NOT NULL,
    rtsp_sub_url VARCHAR(500),
    onvif_port INTEGER,
    has_ptz BOOLEAN NOT NULL,
    status VARCHAR(20) NOT NULL
);
CREATE INDEX ix_devices_site_id ON devices (site_id);
CREATE TABLE analytics_configs (
    id INTEGER NOT NULL PRIMARY KEY,
    device_id INTEGER NOT NULL,
    analyzer_name VARCHAR(60) NOT NULL,
    enabled BOOLEAN NOT NULL,
    confidence_threshold FLOAT NOT NULL,
    roi_x INTEGER, roi_y INTEGER, roi_w INTEGER, roi_h INTEGER,
    object_classes JSON NOT NULL,
    params JSON NOT NULL
);
INSERT INTO sites (name) VALUES ('Sala Principal');
INSERT INTO devices (name, site_id, ip, port, username, password, rtsp_main_url, has_ptz, status)
VALUES ('Cam vieja', 1, '10.0.0.7', 554, '', '', 'rtsp://10.0.0.7/main', 0, 'unknown');
INSERT INTO analytics_configs
    (device_id, analyzer_name, enabled, confidence_threshold, object_classes, params)
VALUES (1, 'motion_detection', 1, 0.5, '[]', '{}');
"""


@pytest.fixture(autouse=True)
def _engine_limpio():
    """init_db usa globales de módulo; ningún test puede heredarlas."""
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None


def _base_legada(tmp_path):
    ruta = tmp_path / "vieja.sqlite3"
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA_VIEJO)
    con.commit()
    con.close()
    return ruta


def _columnas(ruta, tabla) -> set[str]:
    con = sqlite3.connect(ruta)
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({tabla})")}
    finally:
        con.close()


def _tablas(ruta) -> set[str]:
    con = sqlite3.connect(ruta)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def _indices(ruta, tabla) -> set[str]:
    con = sqlite3.connect(ruta)
    try:
        return {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (tabla,)
            )
            if row[0] and not row[0].startswith("sqlite_autoindex")
        }
    finally:
        con.close()


def _cabeza() -> str:
    from aurea_vms.models.db import _config_de_alembic

    return ScriptDirectory.from_config(_config_de_alembic("x")).get_current_head()


class TestBaseNueva:
    def test_queda_en_la_ultima_revision(self, tmp_path):
        ruta = tmp_path / "nueva.sqlite3"
        db_module.init_db(ruta, force=True)

        assert db_module.revision_actual(db_module._engine) == _cabeza()

    def test_crea_las_ocho_tablas(self, tmp_path):
        ruta = tmp_path / "nueva.sqlite3"
        db_module.init_db(ruta, force=True)

        con = sqlite3.connect(ruta)
        tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()

        assert {
            "sites",
            "zones",
            "devices",
            "alarm_rules",
            "analytics_configs",
            "alarm_events",
            "media_assets",
            "users",
        } <= tablas

    def test_no_nace_con_la_columna_legada(self, tmp_path):
        """0003 la saca de las bases viejas; una nueva nunca la tuvo."""
        ruta = tmp_path / "nueva.sqlite3"
        db_module.init_db(ruta, force=True)

        assert "site_id" not in _columnas(ruta, "devices")


class TestAdopcionDeUnaBaseLegada:
    def test_se_marca_en_la_baseline_y_sube_hasta_la_cabeza(self, tmp_path):
        ruta = _base_legada(tmp_path)

        db_module.init_db(ruta, force=True)

        assert db_module.revision_actual(db_module._engine) == _cabeza()

    def test_no_pierde_las_filas_que_ya_estaban(self, tmp_path):
        ruta = _base_legada(tmp_path)

        db_module.init_db(ruta, force=True)

        assert [d.name for d in repository.list_devices()] == ["Cam vieja"]
        assert [s.name for s in repository.list_sites()] == ["Sala Principal"]

    def test_agrega_las_columnas_que_faltaban(self, tmp_path):
        ruta = _base_legada(tmp_path)

        db_module.init_db(ruta, force=True)

        assert {"zone_id", "device_type", "channel", "manufacturer"} <= _columnas(ruta, "devices")
        assert "description" in _columnas(ruta, "sites")

    def test_crea_la_tabla_de_zonas_que_no_existia(self, tmp_path):
        """La base legada no tiene `zones` en absoluto: la crea la adopción,
        y el backfill la puebla acto seguido."""
        ruta = _base_legada(tmp_path)
        assert "zones" not in _tablas(ruta)

        db_module.init_db(ruta, force=True)

        assert "zones" in _tablas(ruta)
        assert [z.name for z in repository.list_zones()] == ["General"]

    def test_crea_los_indices_que_el_sistema_adhoc_nunca_creo(self, tmp_path):
        """ALTER TABLE ADD COLUMN agrega la columna y nada más: la base de
        desarrollo venía corriendo sin ix_devices_zone_id desde que existen
        las zonas, o sea filtrando cámaras por zona con scan completo."""
        ruta = _base_legada(tmp_path)

        db_module.init_db(ruta, force=True)

        assert "ix_devices_zone_id" in _indices(ruta, "devices")

    def test_la_camara_asignada_al_sitio_legado_queda_en_una_zona(self, tmp_path):
        """El backfill: sin él, toda cámara asignada aparecía "Sin zona" en
        silencio e invisible para el filtro global de sitio."""
        ruta = _base_legada(tmp_path)

        db_module.init_db(ruta, force=True)

        sitio = repository.list_sites()[0]
        assert [d.name for d in repository.list_devices(site_id=sitio.id)] == ["Cam vieja"]
        assert [z.name for z in repository.list_zones(site_id=sitio.id)] == ["General"]

    def test_se_va_la_columna_legada_y_su_indice(self, tmp_path):
        """batch_alter_table recrea la tabla entera y reconstruye sus
        índices: si no se saca primero el índice sobre site_id, la migración
        muere con "no such column: site_id"."""
        ruta = _base_legada(tmp_path)

        db_module.init_db(ruta, force=True)

        assert "site_id" not in _columnas(ruta, "devices")
        assert "ix_devices_site_id" not in _indices(ruta, "devices")

    def test_renombra_el_analizador_de_movimiento(self, tmp_path):
        ruta = _base_legada(tmp_path)

        db_module.init_db(ruta, force=True)

        configs = repository.list_analytics_configs()
        assert [c.analyzer_name for c in configs] == ["door_state"]


class TestIdempotencia:
    def test_migrar_dos_veces_no_cambia_nada(self, tmp_path):
        ruta = _base_legada(tmp_path)
        db_module.init_db(ruta, force=True)
        antes = (_columnas(ruta, "devices"), _indices(ruta, "devices"))

        db_module.init_db(ruta, force=True)

        assert (_columnas(ruta, "devices"), _indices(ruta, "devices")) == antes
        assert len(repository.list_devices()) == 1

    def test_el_backfill_de_zonas_es_de_una_sola_vez(self, tmp_path):
        """Consume el site_id legado, así que desasignar una cámara después
        no la vuelve a asignar en el próximo arranque."""
        ruta = _base_legada(tmp_path)
        db_module.init_db(ruta, force=True)
        device = repository.list_devices()[0]
        repository.update_device(device.id, zone_id=None)

        db_module.init_db(ruta, force=True)

        assert repository.get_device(device.id).zone_id is None

    def test_una_base_en_la_cabeza_corta_temprano(self, tmp_path, monkeypatch):
        """El caso de todos los arranques a partir del segundo: no se carga
        env.py ni se arma la maquinaria de upgrade para descubrir que no hay
        nada que aplicar."""
        ruta = tmp_path / "nueva.sqlite3"
        db_module.init_db(ruta, force=True)

        from alembic import command

        def no_deberia(*_args, **_kwargs):
            raise AssertionError("se invocó a Alembic estando ya en la cabeza")

        monkeypatch.setattr(command, "upgrade", no_deberia)
        monkeypatch.setattr(command, "stamp", no_deberia)

        db_module.init_db(ruta, force=True)  # no debe levantar


class TestRevisiones:
    def test_la_baseline_declarada_existe(self):
        script = ScriptDirectory(str(MIGRATIONS_DIR))
        assert script.get_revision(BASELINE_REVISION) is not None

    def test_hay_una_sola_cabeza(self):
        """Dos cabezas significan un merge sin resolver: la base se quedaría
        a mitad de camino sin que nadie se entere."""
        script = ScriptDirectory(str(MIGRATIONS_DIR))
        assert len(script.get_heads()) == 1

    def test_el_paquete_lleva_env_y_las_revisiones(self):
        """Si esto falla empaquetado, el .exe no puede migrar la base del
        cliente (ver el spec de PyInstaller)."""
        assert (MIGRATIONS_DIR / "env.py").exists()
        assert list((MIGRATIONS_DIR / "versions").glob("*.py"))


class TestSinDerivaContraLosModelos:
    def test_la_base_migrada_coincide_con_los_modelos(self, tmp_path):
        """Guarda contra el bug que motivó toda la fase: una columna
        agregada a un modelo y olvidada en la migración rompía la base de
        campo en silencio. Acá el autogenerate no debe encontrar nada."""
        from alembic.autogenerate import compare_metadata
        from alembic.runtime.migration import MigrationContext

        from aurea_vms.models.db import Base, importar_modelos

        ruta = tmp_path / "nueva.sqlite3"
        db_module.init_db(ruta, force=True)
        importar_modelos()

        with db_module._engine.connect() as connection:
            contexto = MigrationContext.configure(connection)
            diferencias = compare_metadata(contexto, Base.metadata)

        assert diferencias == []
