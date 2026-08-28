"""Tests de _apply_adhoc_migrations contra una DB con esquema viejo real.

El esquema "viejo" reproduce una base de desarrollo anterior a la jerarquia
Sitio/Zona: devices sin zone_id ni metadata de hardware, sites sin
description, y sin tabla zones. init_db debe dejarla usable sin perder las
filas existentes.
"""

from __future__ import annotations

import sqlite3

import pytest

from aurea_vms.models import db as db_module
from aurea_vms.models import repository

OLD_SCHEMA = """
CREATE TABLE sites (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(120) NOT NULL UNIQUE
);
CREATE TABLE devices (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
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
INSERT INTO sites (name) VALUES ('Sala Principal');
INSERT INTO devices (name, ip, port, username, password, rtsp_main_url, has_ptz, status)
VALUES ('Cam vieja', '10.0.0.7', 554, '', '', 'rtsp://10.0.0.7/main', 0, 'unknown');
"""


@pytest.fixture()
def old_db(tmp_path):
    """DB con esquema viejo, migrada por init_db. Devuelve el path."""
    db_path = tmp_path / "vieja.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(OLD_SCHEMA)
    conn.close()
    db_module.init_db(db_path, force=True)
    yield db_path
    db_module._engine = None
    db_module._SessionLocal = None


def _columns(db_path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_agrega_las_columnas_faltantes_sin_perder_datos(old_db):
    assert {"zone_id", "device_type", "channel", "manufacturer"} <= _columns(old_db, "devices")
    assert "description" in _columns(old_db, "sites")
    assert "zones" in {
        row[0]
        for row in sqlite3.connect(old_db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    devices = repository.list_devices()
    assert [d.name for d in devices] == ["Cam vieja"]
    assert devices[0].device_type == "ipc"
    assert devices[0].zone_id is None
    assert repository.list_sites()[0].description == ""


def test_la_migracion_es_idempotente(old_db):
    db_module.init_db(old_db, force=True)  # segunda pasada sobre la misma DB
    device_columns = sqlite3.connect(old_db).execute("PRAGMA table_info(devices)").fetchall()
    assert len({row[1] for row in device_columns}) == len(device_columns)


def test_borrar_zona_con_camaras_asignadas_las_deja_sin_zona(old_db):
    """El caso que rompia en DBs migradas: el ALTER TABLE original creaba el
    FK de zone_id sin ON DELETE SET NULL y, con PRAGMA foreign_keys=ON,
    borrar la zona lanzaba IntegrityError."""
    site = repository.list_sites()[0]
    zone = repository.add_zone(site_id=site.id, name="Acceso")
    device = repository.list_devices()[0]
    repository.update_device(device.id, zone_id=zone.id)

    repository.delete_zone(zone.id)

    assert repository.get_device(device.id).zone_id is None
    assert repository.get_zone(zone.id) is None


def test_borrar_zona_en_db_migrada_con_el_fk_sin_set_null(tmp_path):
    """Una DB que ya migro con la version anterior del DDL quedo con
    `zone_id INTEGER REFERENCES zones(id)` (accion de borrado NO ACTION).
    Ahi el arreglo del DDL no llega: el borrado depende de que delete_zone
    y delete_site nulifiquen en Python."""
    db_path = tmp_path / "migrada-fk-malo.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(OLD_SCHEMA)
    conn.executescript(
        """
        CREATE TABLE zones (
            id INTEGER NOT NULL PRIMARY KEY,
            site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            name VARCHAR(120) NOT NULL,
            critical BOOLEAN NOT NULL DEFAULT 0
        );
        ALTER TABLE devices ADD COLUMN zone_id INTEGER REFERENCES zones(id);
        """
    )
    conn.close()
    db_module.init_db(db_path, force=True)
    try:
        site = repository.list_sites()[0]
        zone = repository.add_zone(site_id=site.id, name="Acceso")
        device = repository.list_devices()[0]
        repository.update_device(device.id, zone_id=zone.id)

        repository.delete_zone(zone.id)
        assert repository.get_device(device.id).zone_id is None

        zone2 = repository.add_zone(site_id=site.id, name="Bóveda")
        repository.update_device(device.id, zone_id=zone2.id)
        repository.delete_site(site.id)
        assert repository.get_device(device.id).zone_id is None
    finally:
        db_module._engine = None
        db_module._SessionLocal = None


def test_borrar_sitio_con_zonas_y_camaras_no_borra_las_camaras(old_db):
    site = repository.list_sites()[0]
    zone = repository.add_zone(site_id=site.id, name="Acceso")
    device = repository.list_devices()[0]
    repository.update_device(device.id, zone_id=zone.id)

    repository.delete_site(site.id)

    assert repository.get_device(device.id).zone_id is None
    assert repository.list_zones() == []
    assert repository.list_sites() == []
