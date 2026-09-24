"""Migraciones que no pueden dejar la base inservible (Fase 2, 2026-09-24).

- Atomicas: una revision que muere a mitad deja la base como estaba.
- `init_db` publica el engine solo si la migracion termino bien.
- Backup previo (base + camera_key), rotado a los ultimos 3.
- Lock entre procesos: dos instancias migrando a la vez no se pisan.
- Los `_alembic_tmp_*` de una corrida vieja se limpian.
- 0004 no activa reglas que nadie termino de configurar.
"""

from __future__ import annotations

import multiprocessing
import os
import shutil
import sqlite3
import stat
import threading
from pathlib import Path

import pytest
from alembic import command
from test_db_migrations import ESQUEMA_VIEJO

from aurea_vms.core.credential_store import KEY_FILENAME
from aurea_vms.migrations import MIGRATIONS_DIR, resguardo
from aurea_vms.models import db as db_module

CABEZA_REAL = "0008_normaliza_adoptadas"

REVISION_QUE_FALLA = '''
"""Revision de prueba: toca esquema y datos y despues, si se lo piden, muere.

Revision ID: 0099_falla
Revises: {anterior}
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0099_falla"
down_revision = "{anterior}"
branch_labels = None
depends_on = None


def upgrade():
    # Un batch (recrea la tabla), un indice y un UPDATE: las tres cosas que
    # quedaban a medias sin DDL transaccional.
    with op.batch_alter_table("sites") as batch_op:
        batch_op.add_column(sa.Column("prueba", sa.Integer(), nullable=True))
    op.create_index("ix_devices_prueba", "devices", ["name"])
    op.execute("UPDATE sites SET description = 'tocada'")
    if os.environ.get("AUREA_TEST_FALLA_0099"):
        raise RuntimeError("falla inyectada a mitad de la revision")


def downgrade():
    op.drop_index("ix_devices_prueba", table_name="devices")
    with op.batch_alter_table("sites") as batch_op:
        batch_op.drop_column("prueba")
'''


@pytest.fixture(autouse=True)
def _engine_limpio():
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None


def _consulta(ruta: Path, sql: str, parametros: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(ruta)
    try:
        return con.execute(sql, parametros).fetchall()
    finally:
        con.close()


def _revision(ruta: Path) -> str:
    return _consulta(ruta, "SELECT version_num FROM alembic_version")[0][0]


def _base_legada(directorio: Path) -> Path:
    ruta = directorio / "vieja.sqlite3"
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA_VIEJO)
    con.commit()
    con.close()
    return ruta


def _base_en_cabeza_con_datos(tmp_path: Path) -> Path:
    ruta = tmp_path / "base.sqlite3"
    # La cabeza REAL y no "head": con `migraciones_con_falla` puesta, "head"
    # ya es la 0099 y la base naceria migrada.
    command.upgrade(db_module._config_de_alembic(ruta), CABEZA_REAL)
    con = sqlite3.connect(ruta)
    con.execute("INSERT INTO sites (name, description) VALUES ('Sala', 'original')")
    con.commit()
    con.close()
    return ruta


@pytest.fixture()
def migraciones_con_falla(tmp_path, monkeypatch) -> Path:
    """Copia de las revisiones reales + 0099_falla encima de la cabeza."""
    copia = tmp_path / "migraciones"
    shutil.copytree(MIGRATIONS_DIR, copia, ignore=shutil.ignore_patterns("__pycache__"))
    (copia / "versions" / "0099_falla.py").write_text(
        REVISION_QUE_FALLA.format(anterior=CABEZA_REAL), encoding="utf-8"
    )
    monkeypatch.setattr(db_module, "MIGRATIONS_DIR", copia)
    return copia


def _backups(ruta: Path) -> list[Path]:
    return sorted((ruta.parent / resguardo.BACKUPS_DIRNAME).glob("*.sqlite3"))


class TestMigracionAtomica:
    def test_una_revision_que_falla_a_mitad_deja_la_base_como_estaba(
        self, tmp_path, migraciones_con_falla, monkeypatch
    ):
        ruta = _base_en_cabeza_con_datos(tmp_path)
        esquema = _consulta(ruta, "SELECT name, sql FROM sqlite_master ORDER BY name")
        monkeypatch.setenv("AUREA_TEST_FALLA_0099", "1")

        with pytest.raises(RuntimeError, match="falla inyectada"):
            db_module.init_db(ruta, force=True)

        assert _revision(ruta) == CABEZA_REAL
        assert _consulta(ruta, "SELECT name, sql FROM sqlite_master ORDER BY name") == esquema
        assert _consulta(ruta, "SELECT description FROM sites") == [("original",)]

    def test_el_siguiente_arranque_migra_bien(self, tmp_path, migraciones_con_falla, monkeypatch):
        ruta = _base_en_cabeza_con_datos(tmp_path)
        monkeypatch.setenv("AUREA_TEST_FALLA_0099", "1")
        with pytest.raises(RuntimeError):
            db_module.init_db(ruta, force=True)

        monkeypatch.delenv("AUREA_TEST_FALLA_0099")
        db_module.init_db(ruta, force=True)

        assert _revision(ruta) == "0099_falla"
        assert "prueba" in {fila[1] for fila in _consulta(ruta, "PRAGMA table_info(sites)")}

    def test_varias_revisiones_son_una_sola_transaccion(
        self, tmp_path, migraciones_con_falla, monkeypatch
    ):
        """Una base en 0005 que muere en la 0099 no queda en 0008: o llega a
        la cabeza o vuelve a donde estaba (el backup previo es de ahi)."""
        ruta = tmp_path / "base.sqlite3"
        command.upgrade(db_module._config_de_alembic(ruta), "0005_seguridad")
        monkeypatch.setenv("AUREA_TEST_FALLA_0099", "1")

        with pytest.raises(RuntimeError):
            db_module.init_db(ruta, force=True)

        assert _revision(ruta) == "0005_seguridad"


class TestGlobalesDeInitDb:
    def test_un_init_db_que_falla_no_deja_el_engine_publicado(
        self, tmp_path, migraciones_con_falla, monkeypatch
    ):
        ruta = _base_en_cabeza_con_datos(tmp_path)
        monkeypatch.setenv("AUREA_TEST_FALLA_0099", "1")

        with pytest.raises(RuntimeError):
            db_module.init_db(ruta, force=True)

        assert db_module._engine is None
        assert db_module._SessionLocal is None

    def test_si_falla_un_reinicio_queda_el_engine_anterior(
        self, tmp_path, migraciones_con_falla, monkeypatch
    ):
        buena = tmp_path / "buena.sqlite3"
        db_module.init_db(buena, force=True)
        anterior = db_module._engine
        ruta = tmp_path / "otra" / "base.sqlite3"
        ruta.parent.mkdir()
        ruta = _base_en_cabeza_con_datos(ruta.parent)
        monkeypatch.setenv("AUREA_TEST_FALLA_0099", "1")

        with pytest.raises(RuntimeError):
            db_module.init_db(ruta, force=True)

        assert db_module._engine is anterior


class TestBackup:
    def test_migrar_una_base_con_datos_deja_un_backup_con_esos_datos(self, tmp_path):
        ruta = _base_legada(tmp_path)

        db_module.init_db(ruta, force=True)

        (backup,) = _backups(ruta)
        assert "sin_versionar" in backup.name
        assert _consulta(backup, "SELECT name FROM devices") == [("Cam vieja",)]
        # Es la base de ANTES: sin alembic_version, con la columna legada.
        assert "site_id" in {fila[1] for fila in _consulta(backup, "PRAGMA table_info(devices)")}

    def test_el_backup_lleva_la_revision_de_la_que_salio(self, tmp_path):
        ruta = tmp_path / "base.sqlite3"
        command.upgrade(db_module._config_de_alembic(ruta), "0005_seguridad")

        db_module.init_db(ruta, force=True)

        (backup,) = _backups(ruta)
        assert "-0005_seguridad-" in backup.name

    def test_se_copia_la_clave_con_permisos_restringidos(self, tmp_path):
        """Sin la clave, el backup no sirve: las credenciales van cifradas."""
        ruta = _base_legada(tmp_path)
        (tmp_path / KEY_FILENAME).write_bytes(b"clave-de-prueba")

        db_module.init_db(ruta, force=True)

        (backup,) = _backups(ruta)
        copia = backup.with_suffix(f".{KEY_FILENAME}")
        assert copia.read_bytes() == b"clave-de-prueba"
        if os.name == "posix":
            assert stat.S_IMODE(copia.stat().st_mode) == 0o600
            assert stat.S_IMODE(backup.stat().st_mode) == 0o600

    def test_una_base_nueva_no_genera_backup(self, tmp_path):
        ruta = tmp_path / "nueva.sqlite3"

        db_module.init_db(ruta, force=True)

        assert _backups(ruta) == []

    def test_una_base_en_la_cabeza_no_genera_backup(self, tmp_path):
        ruta = _base_en_cabeza_con_datos(tmp_path)

        db_module.init_db(ruta, force=True)

        assert _backups(ruta) == []

    def test_se_conservan_los_ultimos_tres_con_su_clave(self, tmp_path):
        ruta = _base_en_cabeza_con_datos(tmp_path)
        (tmp_path / KEY_FILENAME).write_bytes(b"k")

        hechos = [resguardo.respaldar(ruta, f"r{i}") for i in range(5)]

        assert _backups(ruta) == hechos[-3:]
        claves = sorted((tmp_path / resguardo.BACKUPS_DIRNAME).glob(f"*.{KEY_FILENAME}"))
        assert claves == [h.with_suffix(f".{KEY_FILENAME}") for h in hechos[-3:]]


class TestTablasTemporales:
    def test_un_alembic_tmp_de_una_corrida_vieja_se_limpia(self, tmp_path):
        ruta = tmp_path / "base.sqlite3"
        command.upgrade(db_module._config_de_alembic(ruta), "0005_seguridad")
        con = sqlite3.connect(ruta)
        con.execute("CREATE TABLE _alembic_tmp_devices (id INTEGER)")
        con.commit()
        con.close()

        db_module.init_db(ruta, force=True)

        assert (
            _consulta(ruta, "SELECT name FROM sqlite_master WHERE name LIKE '%alembic_tmp%'") == []
        )
        assert _revision(ruta) == CABEZA_REAL

    def test_no_borra_una_tabla_que_solo_se_parece(self, tmp_path):
        """El `_` es comodin en LIKE: sin escaparlo, `xalembicxtmpx` caeria."""
        ruta = _base_en_cabeza_con_datos(tmp_path)
        con = sqlite3.connect(ruta)
        con.execute("CREATE TABLE xalembicxtmpxdevices (id INTEGER)")
        con.commit()
        con.close()
        from sqlalchemy import create_engine

        engine = create_engine(f"sqlite:///{ruta}")
        try:
            assert resguardo.limpiar_tablas_temporales(engine) == []
        finally:
            engine.dispose()


class TestLock:
    def test_un_segundo_lock_espera_y_se_rinde_con_un_error_claro(self, tmp_path):
        tomado = threading.Event()
        soltar = threading.Event()

        def duenio():
            with resguardo.lock_de_migracion(tmp_path):
                tomado.set()
                soltar.wait(5)

        hilo = threading.Thread(target=duenio)
        hilo.start()
        try:
            assert tomado.wait(5)
            with (
                pytest.raises(resguardo.LockDeMigracionOcupado, match="Otra instancia"),
                resguardo.lock_de_migracion(tmp_path, timeout_s=0.3),
            ):
                pass
        finally:
            soltar.set()
            hilo.join()

        with resguardo.lock_de_migracion(tmp_path, timeout_s=0.3):
            pass  # suelto: se vuelve a tomar

    def test_hasta_la_primera_lectura_espera_el_lock(self, tmp_path, monkeypatch):
        """Determinista, a diferencia del de dos procesos: la primera conexion
        pasa la base a WAL, y eso pide un lock exclusivo que SQLite niega sin
        esperar si otro esta migrando (~1 de cada 20 corridas con la lectura
        fuera del lock). Asi que ni el camino rapido lee sin el lock."""
        ruta = _base_en_cabeza_con_datos(tmp_path)
        monkeypatch.setattr(
            db_module,
            "lock_de_migracion",
            lambda directorio: resguardo.lock_de_migracion(directorio, timeout_s=0.3),
        )
        tomado = threading.Event()
        soltar = threading.Event()

        def duenio():
            with resguardo.lock_de_migracion(tmp_path):
                tomado.set()
                soltar.wait(5)

        hilo = threading.Thread(target=duenio)
        hilo.start()
        try:
            assert tomado.wait(5)
            with pytest.raises(resguardo.LockDeMigracionOcupado):
                db_module.init_db(ruta, force=True)
        finally:
            soltar.set()
            hilo.join()

    def test_dos_procesos_migran_la_misma_base_a_la_vez(self, tmp_path):
        """De punta a punta con procesos de verdad. Probabilistico: la carrera
        que lo hacia fallar la fija, determinista, el test anterior."""
        ruta = _base_legada(tmp_path)
        contexto = multiprocessing.get_context("spawn")
        barrera = contexto.Barrier(2)
        procesos = [
            contexto.Process(
                target=_migrar_en_otro_proceso, args=(str(ruta), str(tmp_path), barrera)
            )
            for _ in range(2)
        ]
        for proceso in procesos:
            proceso.start()
        for proceso in procesos:
            proceso.join(60)

        assert [p.exitcode for p in procesos] == [0, 0]
        assert _revision(ruta) == CABEZA_REAL
        # Uno migro (y respaldo); el otro encontro la base ya en la cabeza.
        assert len(_backups(ruta)) == 1
        assert _consulta(ruta, "SELECT name FROM devices") == [("Cam vieja",)]


def _migrar_en_otro_proceso(ruta: str, data_dir: str, barrera) -> None:
    os.environ["AUREA_DATA_DIR"] = data_dir
    from aurea_vms.models import db

    barrera.wait(30)
    db.init_db(Path(ruta), force=True)
    db._engine.dispose()


class TestReglasSaneadasEn0004:
    def _base_en_0003_con_reglas(self, tmp_path) -> Path:
        ruta = tmp_path / "base.sqlite3"
        command.upgrade(db_module._config_de_alembic(ruta), "0003_drop_site_id")
        con = sqlite3.connect(ruta)
        con.execute(
            "INSERT INTO devices (name, device_type, channel, ip, port, username, password,"
            " rtsp_main_url, has_ptz, status) VALUES ('C', 'ipc', 1, '1.1.1.1', 554, '', '',"
            " 'rtsp://x', 0, 'unknown')"
        )
        for analizador, enabled in (
            (None, 1),  # sin analizador: nadie la termino de configurar
            ("people_counting", None),  # enabled en NULL
            ("people_counting", 1),  # sana
        ):
            con.execute(
                "INSERT INTO alarm_rules (device_id, analyzer_name, enabled) VALUES (1, ?, ?)",
                (analizador, enabled),
            )
        con.commit()
        con.close()
        return ruta

    def test_las_reglas_incompletas_quedan_apagadas_y_la_sana_no_cambia(self, tmp_path):
        ruta = self._base_en_0003_con_reglas(tmp_path)

        command.upgrade(db_module._config_de_alembic(ruta), "0004_constraints")

        assert _consulta(ruta, "SELECT enabled FROM alarm_rules ORDER BY id") == [(0,), (0,), (1,)]
