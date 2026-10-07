"""Base por URL (AUREA_DB_URL): el camino de un nodo central en un servidor.

Se prueba con una URL `sqlite:///`, que recorre la misma rama de `migrar`
que una de Postgres (sin db_path: sin backup y sin adopcion) sin necesitar
un servidor. La corrida contra un Postgres real esta en
test_migraciones_postgres.py.
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3

import pytest
from alembic import command
from test_db_migrations import ESQUEMA_VIEJO, _cabeza

from aurea_vms.models import db as db_module
from aurea_vms.models.db import _config_de_alembic


@pytest.fixture(autouse=True)
def _engine_limpio():
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None


@pytest.fixture()
def por_url(tmp_path, monkeypatch):
    """Settings con AUREA_DB_URL apuntando a un SQLite del tmp_path."""
    datos = tmp_path / "datos"
    base = tmp_path / "servidor.sqlite3"
    monkeypatch.setattr(
        db_module,
        "settings",
        dataclasses.replace(
            db_module.settings,
            data_dir=datos,
            media_dir=datos / "media",
            db_url=f"sqlite:///{base}",
        ),
    )
    return base, datos


def _revision(ruta) -> str | None:
    con = sqlite3.connect(ruta)
    try:
        filas = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchall()
        if not filas:
            return None
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()


class TestConfigDeAlembic:
    def test_una_ruta_se_vuelve_url_sqlite(self, tmp_path):
        config = _config_de_alembic(tmp_path / "x.sqlite3")
        assert config.get_main_option("sqlalchemy.url") == f"sqlite:///{tmp_path / 'x.sqlite3'}"

    def test_una_url_pasa_tal_cual(self):
        url = "postgresql+psycopg://aurea:clave@nodo/aurea"
        assert _config_de_alembic(url).get_main_option("sqlalchemy.url") == url

    def test_un_porcentaje_en_la_clave_no_rompe_la_interpolacion(self):
        url = "postgresql+psycopg://aurea:50%25off@nodo/aurea"
        assert _config_de_alembic(url).get_main_option("sqlalchemy.url") == url


class TestBasePorUrl:
    def test_una_base_nueva_llega_a_la_cabeza(self, por_url):
        base, _ = por_url
        db_module.init_db(force=True)
        assert _revision(base) == _cabeza()

    def test_un_db_path_explicito_gana_sobre_la_url(self, por_url, tmp_path):
        base, _ = por_url
        local = tmp_path / "local.sqlite3"
        db_module.init_db(local, force=True)
        assert _revision(local) == _cabeza()
        assert not base.exists()

    def test_sube_una_base_versionada_sin_backup_y_avisa(self, por_url, caplog):
        base, datos = por_url
        command.upgrade(_config_de_alembic(base), "0007_monitor_roto")
        with caplog.at_level(logging.WARNING, logger="aurea_vms.models.db"):
            db_module.init_db(force=True)
        assert _revision(base) == _cabeza()
        assert not (datos / "backups").exists()
        assert "sin backup automático" in caplog.text

    def test_no_adopta_una_base_sin_versionar(self, por_url):
        base, _ = por_url
        con = sqlite3.connect(base)
        con.executescript(ESQUEMA_VIEJO)
        con.commit()
        con.close()
        with pytest.raises(RuntimeError, match="no está versionada"):
            db_module.init_db(force=True)
        assert _revision(base) is None
        assert db_module._engine is None
