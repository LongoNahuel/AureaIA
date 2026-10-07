"""Tests de 0009: la tabla `ui_layouts` (disposicion de ventanas por usuario)
y su repositorio."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from aurea_vms.models import db as db_module
from aurea_vms.models import repository
from aurea_vms.models.db import Base, importar_modelos

REVISION_ANTERIOR = "0008_normaliza_adoptadas"
REVISION = "0009_ui_layouts"


@pytest.fixture(autouse=True)
def _engine_limpio():
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None


def _config(ruta: Path):
    return db_module._config_de_alembic(ruta)


def _consulta(ruta: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(ruta)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _tablas(ruta: Path) -> set[str]:
    return {r[0] for r in _consulta(ruta, "SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture()
def base_en_0008(tmp_path) -> Path:
    ruta = tmp_path / "base.sqlite3"
    command.upgrade(_config(ruta), REVISION_ANTERIOR)
    return ruta


class TestMigracion:
    def test_crea_la_tabla_con_su_fk_en_cascada(self, base_en_0008):
        command.upgrade(_config(base_en_0008), REVISION)

        assert "ui_layouts" in _tablas(base_en_0008)
        fks = _consulta(base_en_0008, "PRAGMA foreign_key_list(ui_layouts)")
        assert [(fk[2], fk[3], fk[4], fk[6]) for fk in fks] == [
            ("users", "user_id", "id", "CASCADE")
        ]
        assert _consulta(base_en_0008, "PRAGMA integrity_check") == [("ok",)]
        assert _consulta(base_en_0008, "PRAGMA foreign_key_check") == []

    def test_ida_y_vuelta(self, base_en_0008):
        command.upgrade(_config(base_en_0008), REVISION)
        command.downgrade(_config(base_en_0008), REVISION_ANTERIOR)
        assert "ui_layouts" not in _tablas(base_en_0008)

        command.upgrade(_config(base_en_0008), REVISION)
        assert "ui_layouts" in _tablas(base_en_0008)

    def test_si_la_adopcion_ya_creo_la_tabla_no_falla(self, base_en_0008):
        """Una base vieja adoptada recibe las tablas que faltan del metadata
        vivo, ui_layouts incluida, antes de correr las revisiones."""
        importar_modelos()
        from sqlalchemy import create_engine

        engine = create_engine(f"sqlite:///{base_en_0008}")
        Base.metadata.tables["ui_layouts"].create(engine)
        engine.dispose()

        command.upgrade(_config(base_en_0008), REVISION)  # no "table already exists"

        assert _consulta(base_en_0008, "SELECT version_num FROM alembic_version") == [(REVISION,)]


class TestRepositorio:
    def _usuario(self, nombre: str = "op"):
        return repository.add_user(username=nombre, password_hash="h", salt="", role="operador")

    def test_guarda_y_lee(self, temp_db):
        user = self._usuario()

        repository.save_ui_layout(user.id, 1, {"ventanas": [{"principal": True}]})

        fila = repository.get_ui_layout(user.id)
        assert fila.schema_version == 1
        assert fila.data == {"ventanas": [{"principal": True}]}
        assert fila.updated_at > 0

    def test_guardar_otra_vez_reemplaza_la_misma_fila(self, temp_db):
        user = self._usuario()
        repository.save_ui_layout(user.id, 1, {"a": 1})
        repository.save_ui_layout(user.id, 1, {"b": 2})

        assert repository.get_ui_layout(user.id).data == {"b": 2}
        with db_module.get_session() as session:
            from aurea_vms.models.ui_layout import UiLayout

            assert session.query(UiLayout).count() == 1

    def test_cada_usuario_tiene_la_suya(self, temp_db):
        uno, otro = self._usuario("uno"), self._usuario("otro")
        repository.save_ui_layout(uno.id, 1, {"de": "uno"})

        assert repository.get_ui_layout(otro.id) is None

    def test_borrar_el_usuario_borra_su_disposicion(self, temp_db):
        user = self._usuario()
        repository.save_ui_layout(user.id, 1, {"a": 1})

        repository.delete_user(user.id)

        assert repository.get_ui_layout(user.id) is None
