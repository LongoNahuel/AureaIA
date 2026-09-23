"""Tests de 0006: grabadores con canales y asignacion directa a sitio.

Las dos columnas entraron primero por fuera de Alembic, con el sistema
ad-hoc de ALTER TABLE en cada arranque. Por eso hay tres bases de partida
y no dos: nueva, ya versionada en 0005, y versionada en 0005 pero con las
columnas agregadas a mano por aquel codigo (la base de Nahuel).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from aurea_vms.models import db as db_module

REVISION_ANTERIOR = "0005_seguridad"
REVISION = "0006_jerarquia_grabadores"


@pytest.fixture(autouse=True)
def _engine_limpio():
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None


def _base_en(ruta: Path, revision: str) -> Path:
    command.upgrade(db_module._config_de_alembic(ruta), revision)
    return ruta


def _sql(ruta: Path, *sentencias: str) -> None:
    con = sqlite3.connect(ruta)
    try:
        for sentencia in sentencias:
            con.execute(sentencia)
        con.commit()
    finally:
        con.close()


def _consulta(ruta: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(ruta)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _indices_de_devices(ruta: Path) -> set[str]:
    return {r[1] for r in _consulta(ruta, "PRAGMA index_list(devices)")}


def _fks_de_devices(ruta: Path) -> dict[str, tuple[str, str]]:
    """columna -> (tabla referida, on_delete)."""
    return {r[3]: (r[2], r[6]) for r in _consulta(ruta, "PRAGMA foreign_key_list(devices)")}


def _revision(ruta: Path) -> str:
    return _consulta(ruta, "SELECT version_num FROM alembic_version")[0][0]


def _insertar_camara(ruta: Path, nombre: str, **campos) -> None:
    valores = {
        "ip": "10.0.0.50",
        "port": 554,
        "username": "admin",
        "password": "",
        "rtsp_main_url": "rtsp://10.0.0.50/main",
        "has_ptz": 0,
        "status": "unknown",
        "device_type": "ipc",
        "channel": 1,
        **campos,
    }
    columnas = ", ".join(["name", *valores])
    marcas = ", ".join(["?"] * (len(valores) + 1))
    con = sqlite3.connect(ruta)
    try:
        con.execute(
            f"INSERT INTO devices ({columnas}) VALUES ({marcas})", [nombre, *valores.values()]
        )
        con.commit()
    finally:
        con.close()


class TestBaseNueva:
    def test_trae_las_columnas_con_indice_y_fk(self, tmp_path):
        ruta = tmp_path / "nueva.sqlite3"
        db_module.init_db(ruta, force=True)

        assert {"ix_devices_assigned_site_id", "ix_devices_parent_device_id"} <= (
            _indices_de_devices(ruta)
        )
        fks = _fks_de_devices(ruta)
        assert fks["assigned_site_id"] == ("sites", "SET NULL")
        assert fks["parent_device_id"] == ("devices", "CASCADE")


class TestBaseDelSistemaAdhoc:
    """Una base en 0005 a la que el codigo ad-hoc ya le agrego las columnas
    con ALTER TABLE, sin indices. La revision no puede morir con
    "duplicate column name" y tiene que crear los indices que faltan."""

    def _base_adhoc(self, tmp_path) -> Path:
        ruta = _base_en(tmp_path / "adhoc.sqlite3", REVISION_ANTERIOR)
        _sql(
            ruta,
            "ALTER TABLE devices ADD COLUMN assigned_site_id "
            "INTEGER REFERENCES sites(id) ON DELETE SET NULL",
            "ALTER TABLE devices ADD COLUMN parent_device_id "
            "INTEGER REFERENCES devices(id) ON DELETE CASCADE",
        )
        return ruta

    def test_migra_sin_error_y_crea_los_indices(self, tmp_path):
        ruta = self._base_adhoc(tmp_path)

        db_module.init_db(ruta, force=True)

        assert _revision(ruta) == REVISION
        assert {"ix_devices_assigned_site_id", "ix_devices_parent_device_id"} <= (
            _indices_de_devices(ruta)
        )

    def test_conserva_las_asignaciones_que_ya_tenia(self, tmp_path):
        ruta = self._base_adhoc(tmp_path)
        _sql(ruta, "INSERT INTO sites (name, description) VALUES ('Sala', '')")
        _insertar_camara(ruta, "Cam", assigned_site_id=1)

        db_module.init_db(ruta, force=True)

        assert _consulta(ruta, "SELECT assigned_site_id FROM devices") == [(1,)]


class TestCanalesSueltos:
    """Versiones anteriores guardaban cada canal de un NVR como una fila
    independiente. La revision los agrupa bajo el de menor id, una vez."""

    def _base_con_canales(self, tmp_path) -> Path:
        ruta = _base_en(tmp_path / "canales.sqlite3", REVISION_ANTERIOR)
        for canal in (1, 2, 3):
            _insertar_camara(
                ruta, f"NVR Sala · Canal {canal}", device_type="nvr", channel=canal, ip="10.0.0.9"
            )
        _insertar_camara(ruta, "Cam suelta")
        return ruta

    def test_el_de_menor_id_queda_de_padre_con_el_nombre_limpio(self, tmp_path):
        ruta = self._base_con_canales(tmp_path)

        db_module.init_db(ruta, force=True)

        padre = _consulta(ruta, "SELECT name, channel, parent_device_id FROM devices WHERE id = 1")
        assert padre == [("NVR Sala", 0, None)]

    def test_los_demas_cuelgan_del_padre(self, tmp_path):
        ruta = self._base_con_canales(tmp_path)

        db_module.init_db(ruta, force=True)

        hijos = _consulta(ruta, "SELECT id, parent_device_id FROM devices WHERE id IN (2, 3)")
        assert hijos == [(2, 1), (3, 1)]

    def test_una_camara_ip_no_se_toca(self, tmp_path):
        ruta = self._base_con_canales(tmp_path)

        db_module.init_db(ruta, force=True)

        assert _consulta(ruta, "SELECT name, parent_device_id FROM devices WHERE id = 4") == [
            ("Cam suelta", None)
        ]

    def test_un_canal_sin_sufijo_lo_recibe(self, tmp_path):
        ruta = _base_en(tmp_path / "sufijo.sqlite3", REVISION_ANTERIOR)
        _insertar_camara(ruta, "XVR", device_type="xvr", channel=1)
        _insertar_camara(ruta, "XVR", device_type="xvr", channel=2)

        db_module.init_db(ruta, force=True)

        assert _consulta(ruta, "SELECT name FROM devices WHERE id = 2") == [("XVR · Canal 2",)]

    def test_onvif_port_nulo_agrupa_igual(self, tmp_path):
        """El SQL original usaba `onvif_port IS ?`, que es de SQLite; el
        portable compara NULL explicito. Los canales sin puerto ONVIF (el
        caso de un alta manual) tienen que agruparse igual."""
        ruta = _base_en(tmp_path / "sin_onvif.sqlite3", REVISION_ANTERIOR)
        _insertar_camara(ruta, "NVR", device_type="nvr", channel=1, onvif_port=None)
        _insertar_camara(ruta, "NVR", device_type="nvr", channel=2, onvif_port=None)

        db_module.init_db(ruta, force=True)

        assert _consulta(ruta, "SELECT parent_device_id FROM devices WHERE id = 2") == [(1,)]


class TestDowngrade:
    def test_vuelve_a_0005_sin_perder_camaras(self, tmp_path):
        ruta = tmp_path / "baja.sqlite3"
        db_module.init_db(ruta, force=True)
        db_module._engine.dispose()
        _insertar_camara(ruta, "Cam")

        command.downgrade(db_module._config_de_alembic(ruta), REVISION_ANTERIOR)

        columnas = {r[1] for r in _consulta(ruta, "PRAGMA table_info(devices)")}
        assert "parent_device_id" not in columnas
        assert "assigned_site_id" not in columnas
        assert _consulta(ruta, "SELECT name FROM devices") == [("Cam",)]


class TestNoVuelveElSistemaAdhoc:
    """El 23/09 el esquema volvio a crecer con ALTER TABLE en cada arranque,
    por fuera de Alembic, y la base migrada dejo de coincidir con los
    modelos. Toda columna nueva va en una revision (migrations/versions/)."""

    def test_db_py_no_altera_el_esquema_por_su_cuenta(self):
        fuente = Path(db_module.__file__).read_text(encoding="utf-8")
        assert "ALTER TABLE" not in fuente
        assert "_ADHOC" not in fuente
