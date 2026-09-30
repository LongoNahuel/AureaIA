"""Tests de 0009: una sola analitica visible (Incidentes en casinos).

Conteo de Personas, Cruce de Linea y Deteccion Facial se ocultan en la
interfaz; sus configuraciones habilitadas se apagan con una marca para que
no sigan corriendo sin que nadie las vea, y el downgrade prende solo esas.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from aurea_vms.core.analytics.registry import AVAILABLE_ANALYZERS, VISIBLE_ANALYZERS
from aurea_vms.models import db as db_module

REVISION_ANTERIOR = "0008_normaliza_adoptadas"
REVISION = "0009_una_sola_analitica"
MARCA = "oculta_0009"


@pytest.fixture(autouse=True)
def _engine_limpio():
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None


def _config(ruta: Path):
    return db_module._config_de_alembic(ruta)


def _sql(ruta: Path, sentencia: str, parametros: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(ruta)
    try:
        filas = con.execute(sentencia, parametros).fetchall()
        con.commit()
        return filas
    finally:
        con.close()


def _insertar(ruta: Path, analizador: str, params: dict, enabled: int = 1) -> None:
    _sql(
        ruta,
        "INSERT INTO analytics_configs (device_id, analyzer_name, enabled, confidence_threshold,"
        " roi_x, roi_y, roi_w, roi_h, object_classes, params)"
        " VALUES (1, ?, ?, 0.5, NULL, NULL, NULL, NULL, '[]', ?)",
        (analizador, enabled, json.dumps(params)),
    )


def _configs(ruta: Path) -> dict[str, tuple[bool, dict]]:
    return {
        nombre: (bool(enabled), json.loads(params))
        for nombre, enabled, params in _sql(
            ruta, "SELECT analyzer_name, enabled, params FROM analytics_configs ORDER BY id"
        )
    }


@pytest.fixture()
def base_en_0008(tmp_path) -> Path:
    ruta = tmp_path / "base.sqlite3"
    command.upgrade(_config(ruta), REVISION_ANTERIOR)
    _sql(
        ruta,
        "INSERT INTO devices (name, device_type, channel, ip, port, username, password,"
        " rtsp_main_url, has_ptz, status) VALUES"
        " ('SIM', 'ipc', 1, '127.0.0.3', 8554, '', '', 'rtsp://127.0.0.3/cam', 0, 'unknown')",
    )
    return ruta


def test_la_interfaz_ofrece_solo_incidentes_y_las_demas_siguen_existiendo():
    assert VISIBLE_ANALYZERS == ["monitor_tamper"]
    assert {"people_counting", "line_crossing", "face_detection"} <= set(AVAILABLE_ANALYZERS)


def test_apaga_las_ocultas_habilitadas_con_la_marca(base_en_0008):
    _insertar(base_en_0008, "people_counting", {"fps": 15})
    _insertar(base_en_0008, "face_detection", {"fps": 30})

    command.upgrade(_config(base_en_0008), REVISION)

    configs = _configs(base_en_0008)
    assert configs["people_counting"] == (False, {"fps": 15, MARCA: True})
    assert configs["face_detection"] == (False, {"fps": 30, MARCA: True})


def test_incidentes_sigue_habilitada(base_en_0008):
    params = {"modo": "ruleta", "zones": [[1, 2, 3, 4]]}
    _insertar(base_en_0008, "monitor_tamper", params)

    command.upgrade(_config(base_en_0008), REVISION)

    assert _configs(base_en_0008)["monitor_tamper"] == (True, params)


def test_una_ya_apagada_no_se_marca(base_en_0008):
    _insertar(base_en_0008, "line_crossing", {"line": [[0, 0], [9, 9]]}, enabled=0)

    command.upgrade(_config(base_en_0008), REVISION)

    assert _configs(base_en_0008)["line_crossing"] == (False, {"line": [[0, 0], [9, 9]]})


def test_el_downgrade_prende_solo_las_marcadas(base_en_0008):
    _insertar(base_en_0008, "people_counting", {"fps": 15})
    _insertar(base_en_0008, "line_crossing", {"fps": 8}, enabled=0)
    command.upgrade(_config(base_en_0008), REVISION)

    command.downgrade(_config(base_en_0008), REVISION_ANTERIOR)

    configs = _configs(base_en_0008)
    assert configs["people_counting"] == (True, {"fps": 15})
    assert configs["line_crossing"] == (False, {"fps": 8})
