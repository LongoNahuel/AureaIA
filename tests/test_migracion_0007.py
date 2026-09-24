"""Tests de 0007: la analitica de puerta pasa a ser Detección de incidentes
(entonces llamada Monitor roto).

Migracion de datos pura: renombra configuraciones y reglas, conserva zonas y
fps, guarda los parametros de puerta en `puerta_legado` para que el
downgrade los restaure, y no toca las demas analiticas.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from aurea_vms.models import db as db_module

REVISION_ANTERIOR = "0006_jerarquia_grabadores"
REVISION = "0007_monitor_roto"

PARAMS_PUERTA = {
    "zones": [[605, 565, 281, 311]],
    "fps": 15,
    "change_threshold": 0.098,
    "opening_percent": 9.0,
    "confirmation_frames": 5,
    "threshold_seconds": 0.5,
}


@pytest.fixture(autouse=True)
def _engine_limpio():
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None


def _config(ruta: Path) -> object:
    return db_module._config_de_alembic(ruta)


def _sql(ruta: Path, sentencia: str, parametros: tuple = ()) -> None:
    con = sqlite3.connect(ruta)
    try:
        con.execute(sentencia, parametros)
        con.commit()
    finally:
        con.close()


def _consulta(ruta: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(ruta)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


@pytest.fixture()
def base_con_puerta(tmp_path) -> Path:
    """Base en 0006 con una camara, su config de puerta, una de conteo y una
    regla de alarma de puerta: como la base de desarrollo de Nahuel."""
    ruta = tmp_path / "base.sqlite3"
    command.upgrade(_config(ruta), REVISION_ANTERIOR)
    _sql(
        ruta,
        "INSERT INTO devices (name, device_type, channel, ip, port, username, password,"
        " rtsp_main_url, has_ptz, status) VALUES ('SIM', 'ipc', 1, '127.0.0.3', 8554, '', '',"
        " 'rtsp://127.0.0.3:8554/cam3', 0, 'unknown')",
    )
    for nombre, params in (("door_state", PARAMS_PUERTA), ("people_counting", {"zones": []})):
        _sql(
            ruta,
            "INSERT INTO analytics_configs (device_id, analyzer_name, enabled,"
            " confidence_threshold, object_classes, params) VALUES (1, ?, 1, 0.5, '[]', ?)",
            (nombre, json.dumps(params)),
        )
    _sql(
        ruta,
        "INSERT INTO alarm_rules (device_id, analyzer_name, object_classes, min_confidence,"
        " cooldown_seconds, severity, schedule_days, actions, enabled)"
        " VALUES (1, 'door_state', '[\"puerta_abierta\"]', 0.5, 30, 'alto', '[]', '{}', 1)",
    )
    return ruta


def _configs(ruta: Path) -> dict[str, dict]:
    return {
        nombre: json.loads(params)
        for nombre, params in _consulta(ruta, "SELECT analyzer_name, params FROM analytics_configs")
    }


class TestUpgrade:
    def test_la_puerta_pasa_a_monitor_con_sus_zonas_y_fps(self, base_con_puerta):
        command.upgrade(_config(base_con_puerta), REVISION)
        configs = _configs(base_con_puerta)

        assert "door_state" not in configs
        monitor = configs["monitor_tamper"]
        assert monitor["zones"] == PARAMS_PUERTA["zones"]
        assert monitor["fps"] == 15

    def test_los_parametros_de_puerta_no_quedan_activos_pero_se_guardan(self, base_con_puerta):
        """confirmation_frames: 5 le exigiria al monitor cinco muestras
        seguidas con el pie sobre la pantalla: no detectaria patadas."""
        command.upgrade(_config(base_con_puerta), REVISION)
        monitor = _configs(base_con_puerta)["monitor_tamper"]

        assert "confirmation_frames" not in monitor
        assert monitor["puerta_legado"]["confirmation_frames"] == 5
        assert monitor["puerta_legado"]["opening_percent"] == 9.0

    def test_la_regla_de_puerta_pasa_a_monitor_con_cualquier_golpe(self, base_con_puerta):
        command.upgrade(_config(base_con_puerta), REVISION)

        assert _consulta(
            base_con_puerta, "SELECT analyzer_name, object_classes FROM alarm_rules"
        ) == [("monitor_tamper", "[]")]

    def test_no_toca_las_demas_analiticas(self, base_con_puerta):
        command.upgrade(_config(base_con_puerta), REVISION)

        assert _configs(base_con_puerta)["people_counting"] == {"zones": []}


class TestDowngrade:
    def test_vuelve_a_puerta_con_los_parametros_originales(self, base_con_puerta):
        command.upgrade(_config(base_con_puerta), REVISION)
        command.downgrade(_config(base_con_puerta), REVISION_ANTERIOR)
        configs = _configs(base_con_puerta)

        assert configs["door_state"] == PARAMS_PUERTA
        assert "monitor_tamper" not in configs
        assert _consulta(base_con_puerta, "SELECT analyzer_name FROM alarm_rules") == [
            ("door_state",)
        ]


def test_una_base_nueva_llega_a_esta_revision(tmp_path):
    """Contra la cabeza real, no contra esta revision fija: la proxima
    revision no tiene que romper este test."""
    ruta = tmp_path / "nueva.sqlite3"
    command.upgrade(_config(ruta), "head")

    script = ScriptDirectory.from_config(_config(ruta))
    cabeza = script.get_current_head()
    assert _consulta(ruta, "SELECT version_num FROM alembic_version") == [(cabeza,)]
    assert REVISION in {r.revision for r in script.iterate_revisions(cabeza, "base")}
