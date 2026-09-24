"""Tests de 0008: monitores sin pantalla y normalizacion de bases adoptadas.

- Datos: una Detección de incidentes sin zonas ni ROI no puede construirse
  (MonitorTamperAnalyzer exige una pantalla). 0007 las dejaba habilitadas; 0008
  las apaga con una marca y el downgrade rehabilita solo esas.
- Esquema: una base adoptada trae constraints anonimas (FK de zona sin SET
  NULL, UNIQUE sin nombre). Despues de 0008 tiene que coincidir con los
  modelos, igual que una base nueva.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine
from test_db_migrations import ESQUEMA_VIEJO

from aurea_vms.migrations import BASELINE_REVISION
from aurea_vms.migrations.adopcion import adoptar
from aurea_vms.models import db as db_module
from aurea_vms.models.db import Base, importar_modelos

REVISION_ANTERIOR = "0007_monitor_roto"
REVISION = "0008_normaliza_adoptadas"
MARCA = "deshabilitada_0008"


@pytest.fixture(autouse=True)
def _engine_limpio():
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None


def _config(ruta: Path):
    return db_module._config_de_alembic(ruta)


def _sql(ruta: Path, sentencia: str, parametros: tuple = ()) -> None:
    con = sqlite3.connect(ruta)
    try:
        con.execute(sentencia, parametros)
        con.commit()
    finally:
        con.close()


def _consulta(ruta: Path, sql: str, parametros: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(ruta)
    try:
        return con.execute(sql, parametros).fetchall()
    finally:
        con.close()


def _insertar_camara(ruta: Path, nombre: str = "SIM", padre: int | None = None) -> int:
    con = sqlite3.connect(ruta)
    try:
        cursor = con.execute(
            "INSERT INTO devices (name, device_type, channel, ip, port, username, password,"
            " rtsp_main_url, has_ptz, status, parent_device_id) VALUES"
            " (?, 'ipc', 1, '127.0.0.3', 8554, '', '', 'rtsp://127.0.0.3/cam', 0, 'unknown', ?)",
            (nombre, padre),
        )
        con.commit()
        return cursor.lastrowid
    finally:
        con.close()


def _insertar_monitor(
    ruta: Path, params: dict, *, roi=(None, None, None, None), enabled=1, camara=1
):
    _sql(
        ruta,
        "INSERT INTO analytics_configs (device_id, analyzer_name, enabled, confidence_threshold,"
        " roi_x, roi_y, roi_w, roi_h, object_classes, params)"
        " VALUES (?, 'monitor_tamper', ?, 0.5, ?, ?, ?, ?, '[]', ?)",
        (camara, enabled, *roi, json.dumps(params)),
    )


def _monitores(ruta: Path) -> list[tuple[int, dict]]:
    return [
        (bool(enabled), json.loads(params))
        for enabled, params in _consulta(
            ruta, "SELECT enabled, params FROM analytics_configs ORDER BY id"
        )
    ]


@pytest.fixture()
def base_en_0007(tmp_path) -> Path:
    ruta = tmp_path / "base.sqlite3"
    command.upgrade(_config(ruta), REVISION_ANTERIOR)
    _insertar_camara(ruta)
    return ruta


@pytest.fixture()
def base_adoptada(tmp_path) -> Path:
    """La base legada de test_db_migrations, adoptada y llevada a 0007: trae
    la FK de zona anonima y el UNIQUE de sites sin nombre, como la base de
    desarrollo de Daniel."""
    ruta = tmp_path / "adoptada.sqlite3"
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA_VIEJO)
    con.commit()
    con.close()
    importar_modelos()
    engine = create_engine(f"sqlite:///{ruta}")
    adoptar(engine, Base.metadata)
    engine.dispose()
    command.stamp(_config(ruta), BASELINE_REVISION)
    command.upgrade(_config(ruta), REVISION_ANTERIOR)
    return ruta


def _deriva(ruta: Path) -> list:
    importar_modelos()
    engine = create_engine(f"sqlite:///{ruta}")
    try:
        with engine.connect() as connection:
            return compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        engine.dispose()


class TestMonitoresSinPantalla:
    def test_sin_zonas_ni_roi_queda_deshabilitado_con_la_marca(self, base_en_0007):
        """La forma real de la base de desarrollo: `door_state` con params {}
        que 0007 paso a monitor_tamper."""
        _insertar_monitor(base_en_0007, {})

        command.upgrade(_config(base_en_0007), REVISION)

        assert _monitores(base_en_0007) == [(False, {MARCA: True})]

    def test_con_zonas_sigue_habilitado(self, base_en_0007):
        params = {"zones": [[10, 10, 50, 50]], "fps": 8}
        _insertar_monitor(base_en_0007, params)

        command.upgrade(_config(base_en_0007), REVISION)

        assert _monitores(base_en_0007) == [(True, params)]

    def test_con_roi_completo_sigue_habilitado(self, base_en_0007):
        """El registry usa el ROI como pantalla cuando no hay zonas."""
        _insertar_monitor(base_en_0007, {}, roi=(0, 0, 100, 100))

        command.upgrade(_config(base_en_0007), REVISION)

        assert _monitores(base_en_0007) == [(True, {})]

    def test_zonas_mal_formadas_no_cuentan_como_pantalla(self, base_en_0007):
        _insertar_monitor(base_en_0007, {"zones": [[1, 2, 3]]})

        command.upgrade(_config(base_en_0007), REVISION)

        assert _monitores(base_en_0007)[0][0] is False

    def test_uno_ya_deshabilitado_no_se_marca(self, base_en_0007):
        """Si se marcara, el downgrade lo prenderia: algo que el usuario
        habia apagado."""
        _insertar_monitor(base_en_0007, {}, enabled=0)

        command.upgrade(_config(base_en_0007), REVISION)

        assert _monitores(base_en_0007) == [(False, {})]

    def test_no_toca_otras_analiticas(self, base_en_0007):
        _sql(
            base_en_0007,
            "INSERT INTO analytics_configs (device_id, analyzer_name, enabled,"
            " confidence_threshold, object_classes, params)"
            " VALUES (1, 'people_counting', 1, 0.5, '[]', '{}')",
        )

        command.upgrade(_config(base_en_0007), REVISION)

        assert _monitores(base_en_0007) == [(True, {})]

    def test_el_downgrade_rehabilita_solo_los_marcados(self, base_en_0007):
        _insertar_monitor(base_en_0007, {})
        _insertar_monitor(base_en_0007, {}, enabled=0, camara=_insertar_camara(base_en_0007, "B"))

        command.upgrade(_config(base_en_0007), REVISION)
        command.downgrade(_config(base_en_0007), REVISION_ANTERIOR)

        assert _monitores(base_en_0007) == [(True, {}), (False, {})]

    def test_ida_y_vuelta_deja_lo_mismo(self, base_en_0007):
        _insertar_monitor(base_en_0007, {})
        command.upgrade(_config(base_en_0007), REVISION)
        antes = _monitores(base_en_0007)

        command.downgrade(_config(base_en_0007), REVISION_ANTERIOR)
        command.upgrade(_config(base_en_0007), REVISION)

        assert _monitores(base_en_0007) == antes


class TestNormalizacionDeLaBaseAdoptada:
    def test_la_base_adoptada_trae_deriva_antes(self, base_adoptada):
        """Sin esto, el test siguiente pasaria con una base que nunca tuvo
        nada que normalizar."""
        assert _deriva(base_adoptada) != []

    def test_despues_de_0008_coincide_con_los_modelos(self, base_adoptada):
        command.upgrade(_config(base_adoptada), REVISION)

        assert _deriva(base_adoptada) == []

    def test_borrar_una_zona_deja_la_camara_sin_zona(self, base_adoptada):
        """El efecto visible de la FK: antes, borrar una zona con camaras
        fallaba por la FK sin ON DELETE."""
        command.upgrade(_config(base_adoptada), REVISION)
        con = sqlite3.connect(base_adoptada)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            zona_id = con.execute("SELECT zone_id FROM devices").fetchone()[0]
            assert zona_id is not None
            con.execute("DELETE FROM zones WHERE id = ?", (zona_id,))
            con.commit()
            assert con.execute("SELECT zone_id FROM devices").fetchall() == [(None,)]
        finally:
            con.close()

    def test_recrear_devices_no_borra_a_los_hijos(self, base_adoptada):
        """El batch hace DROP de la tabla vieja: con FKs activas se llevaria
        en cascada canales, configs, reglas y media."""
        canal = _insertar_camara(base_adoptada, "Canal", padre=1)
        _sql(
            base_adoptada,
            "INSERT INTO alarm_rules (device_id, analyzer_name, object_classes, min_confidence,"
            " cooldown_seconds, severity, schedule_days, actions, enabled)"
            " VALUES (1, 'people_counting', '[]', 0.5, 30, 'alto', '[]', '{}', 1)",
        )
        _sql(
            base_adoptada,
            "INSERT INTO media_assets (kind, device_id, timestamp, rel_path, size_bytes)"
            " VALUES ('snapshot', 1, 0, 'a.jpg', 1)",
        )
        tablas = ("devices", "analytics_configs", "alarm_rules", "media_assets")
        antes = {t: _consulta(base_adoptada, f"SELECT COUNT(*) FROM {t}") for t in tablas}

        command.upgrade(_config(base_adoptada), REVISION)

        assert {t: _consulta(base_adoptada, f"SELECT COUNT(*) FROM {t}") for t in tablas} == antes
        assert _consulta(
            base_adoptada, "SELECT parent_device_id FROM devices WHERE id = ?", (canal,)
        ) == [(1,)]

    def test_la_config_de_una_camara_que_ya_no_existe_se_borra(self, base_adoptada):
        """Sin FK, borrar la camara dejaba la config colgando; la FK nueva es
        CASCADE, asi que se hace lo que ella habria hecho."""
        _sql(
            base_adoptada,
            "INSERT INTO analytics_configs (device_id, analyzer_name, enabled,"
            " confidence_threshold, object_classes, params)"
            " VALUES (99, 'people_counting', 1, 0.5, '[]', '{}')",
        )

        command.upgrade(_config(base_adoptada), REVISION)

        assert _consulta(base_adoptada, "SELECT device_id FROM analytics_configs") == [(1,)]
        assert _consulta(base_adoptada, "PRAGMA foreign_key_check") == []

    def test_una_camara_en_una_zona_que_ya_no_existe_queda_sin_zona(self, base_adoptada):
        _sql(base_adoptada, "UPDATE devices SET zone_id = 99")

        command.upgrade(_config(base_adoptada), REVISION)

        assert _consulta(base_adoptada, "SELECT zone_id FROM devices") == [(None,)]
        assert _consulta(base_adoptada, "PRAGMA foreign_key_check") == []

    def test_una_base_nueva_no_cambia(self, tmp_path):
        ruta = tmp_path / "nueva.sqlite3"
        command.upgrade(_config(ruta), REVISION_ANTERIOR)
        esquema = _consulta(ruta, "SELECT name, sql FROM sqlite_master ORDER BY name")

        command.upgrade(_config(ruta), REVISION)

        assert _consulta(ruta, "SELECT name, sql FROM sqlite_master ORDER BY name") == esquema

    def test_sin_unique_y_con_repetidos_falla_con_un_mensaje_claro(self, base_en_0007):
        """Si el UNIQUE directamente no existe, crearlo sobre datos repetidos
        no puede terminar en un IntegrityError anonimo."""
        con = sqlite3.connect(base_en_0007)
        con.executescript(
            "DROP TABLE sites;"
            "CREATE TABLE sites (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(120) NOT NULL,"
            " description VARCHAR(300) NOT NULL);"
            "INSERT INTO sites (name, description) VALUES ('Sala', ''), ('Sala', '');"
        )
        con.close()

        with pytest.raises(RuntimeError, match="valores repetidos en sites.name"):
            command.upgrade(_config(base_en_0007), REVISION)

    def test_sin_unique_y_sin_repetidos_lo_crea(self, base_en_0007):
        con = sqlite3.connect(base_en_0007)
        con.executescript(
            "DROP TABLE sites;"
            "CREATE TABLE sites (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(120) NOT NULL,"
            " description VARCHAR(300) NOT NULL);"
            "INSERT INTO sites (name, description) VALUES ('Sala', '');"
        )
        con.close()

        command.upgrade(_config(base_en_0007), REVISION)

        assert _deriva(base_en_0007) == []


def test_con_foreign_keys_activas_se_niega_a_migrar(base_en_0007):
    """Precondicion de la revision: con FKs activas el batch borraria hijos."""
    _insertar_monitor(base_en_0007, {})
    engine = create_engine(f"sqlite:///{base_en_0007}")
    config = _config(base_en_0007)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            config.attributes["connection"] = connection
            with pytest.raises(RuntimeError, match="foreign_keys=OFF"):
                command.upgrade(config, REVISION)
    finally:
        engine.dispose()

    assert _consulta(base_en_0007, "SELECT version_num FROM alembic_version") == [
        (REVISION_ANTERIOR,)
    ]
    assert _monitores(base_en_0007) == [(True, {})]
