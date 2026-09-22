"""Pragmas de SQLite y comportamiento bajo acceso concurrente.

Cuatro tipos de hilo escriben esta base a la vez en produccion (StreamWorker,
AlarmEngine desde el hilo de analitica, ClipWriter y RetentionWorker) mientras
la UI lee.

Que escenario prueba QUE, medido corriendo estos mismos tests con
journal_mode=DELETE forzado:

- Escritor contra escritor: NO distingue WAL. El driver sqlite3 de Python ya
  abre con timeout=5.0, asi que dos escritores se turnan sin romperse en los
  dos modos. El test esta igual, pero como red contra un repository que no
  sea thread-safe -- no como prueba de WAL.
- Escritor con la transaccion abierta contra un lector: tampoco distingue,
  ni con una transaccion de 20.000 filas. En rollback-journal el escritor
  toma EXCLUSIVE recien al commitear.
- LECTOR con una transaccion de lectura sostenida contra un escritor: el
  unico que distingue, y por lejos. Sin WAL el escritor agota los 5s de
  busy_timeout y tira "database is locked"; con WAL pasa en 0.00s.

Ese ultimo necesita un BEGIN explicito, porque pysqlite no abre transaccion
para un SELECT suelto -- ver el docstring del test.
"""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import text

from aurea_vms.models import db as db_module
from aurea_vms.models import repository


def _pragma(name: str):
    assert db_module._engine is not None
    with db_module._engine.connect() as conn:
        return conn.exec_driver_sql(f"PRAGMA {name}").scalar()


@pytest.mark.usefixtures("temp_db")
class TestPragmas:
    def test_journal_mode_es_wal(self):
        assert _pragma("journal_mode") == "wal"

    def test_busy_timeout_configurado(self):
        assert _pragma("busy_timeout") == db_module.SQLITE_BUSY_TIMEOUT_MS

    def test_synchronous_normal(self):
        # 1 == NORMAL (0=OFF, 2=FULL, 3=EXTRA).
        assert _pragma("synchronous") == 1

    def test_foreign_keys_sigue_activo(self):
        """El pragma que ya estaba no se perdio al sumar los nuevos: sin el,
        los ondelete=CASCADE/SET NULL de los modelos son decorativos."""
        assert _pragma("foreign_keys") == 1

    def test_wal_quedo_grabado_en_el_archivo(self, tmp_path):
        """journal_mode es persistente: se escribe en el header de la base.
        Una conexion nueva, sin pasar por el listener, lo ve igual."""
        import sqlite3

        db_path = tmp_path / "persistente.sqlite3"
        db_module.init_db(db_path, force=True)
        with db_module.get_session() as session:
            session.execute(text("SELECT 1"))

        raw = sqlite3.connect(db_path)
        try:
            assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            raw.close()


@pytest.mark.usefixtures("temp_db")
def test_una_lectura_sostenida_no_bloquea_al_escritor():
    """La UI leyendo mientras un hilo de fondo escribe. Sin WAL el escritor
    agota el busy_timeout entero y tira OperationalError; con WAL pasa de
    largo. Es el unico escenario que distingue los dos modos: verificado
    forzando journal_mode=DELETE, 5.02s + "database is locked" contra 0.00s.

    El BEGIN explicito no es decorativo y no se puede sacar: pysqlite NO abre
    transaccion para un SELECT suelto (la abre recien ante un INSERT/UPDATE/
    DELETE), asi que sin el, el lector suelta el lock apenas termina la query
    y el test pasa en verde con WAL y sin WAL por igual -- que es exactamente
    como estaba escrito antes. El BEGIN modela una transaccion de lectura que
    dura: hoy no la hay en el codigo, pero aparece sola en cuanto alguien
    lee-modifica-escribe en una misma sesion, y es justo el caso que WAL
    cubre.
    """
    site = repository.add_site(name="Sitio", description="")
    zone = repository.add_zone(site_id=site.id, name="Zona")
    device = repository.add_device(
        name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x", zone_id=zone.id
    )

    resultado: dict[str, object] = {}

    def escribir() -> None:
        inicio = time.monotonic()
        try:
            repository.add_alarm_event(
                rule_id=None,
                device_id=device.id,
                timestamp=1.0,
                object_class="person",
                confidence=0.9,
                severity="alto",
            )
            resultado["error"] = None
        except Exception as exc:  # noqa: BLE001 - el test reporta, no traga
            resultado["error"] = exc
        resultado["segundos"] = time.monotonic() - inicio

    # Lector con la transaccion ABIERTA durante toda la escritura.
    with db_module.get_session() as session:
        session.execute(text("BEGIN"))
        session.execute(text("SELECT count(*) FROM alarm_events")).scalar()

        hilo = threading.Thread(target=escribir)
        hilo.start()
        hilo.join(timeout=30.0)
        assert not hilo.is_alive(), "el escritor quedo colgado contra el lector"

        session.rollback()

    assert resultado["error"] is None, f"el escritor fallo: {resultado['error']}"
    # Sin WAL esto tarda lo que dure el busy_timeout y recien ahi falla.
    assert resultado["segundos"] < 1.0, (
        f"el escritor espero {resultado['segundos']:.2f}s: parece que el lector lo bloqueo"
    )


@pytest.mark.usefixtures("temp_db")
def test_varios_hilos_escribiendo_a_la_vez():
    """Escritor contra escritor: se turnan, nadie se rompe ni se cuelga.

    OJO: esto pasa con WAL y sin WAL -- el busy_timeout del driver ya lo
    resolvia. No lo borramos porque cubre otra cosa que igual queremos fija
    (que `repository` aguante escrituras de varios hilos sobre las dos tablas
    que mas se tocan en vivo), pero no vale como evidencia del fix de B1.
    """
    site = repository.add_site(name="Sitio", description="")
    zone = repository.add_zone(site_id=site.id, name="Zona")
    device = repository.add_device(
        name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x", zone_id=zone.id
    )

    errores: list[Exception] = []
    barrera = threading.Barrier(4)

    def escribir_eventos() -> None:
        barrera.wait()
        try:
            for i in range(25):
                repository.add_alarm_event(
                    rule_id=None,
                    device_id=device.id,
                    timestamp=float(i),
                    object_class="person",
                    confidence=0.9,
                    severity="alto",
                )
        except Exception as exc:  # noqa: BLE001
            errores.append(exc)

    def actualizar_estado() -> None:
        barrera.wait()
        try:
            for i in range(25):
                repository.update_device_status(device.id, "online" if i % 2 else "offline")
        except Exception as exc:  # noqa: BLE001
            errores.append(exc)

    hilos = [
        threading.Thread(target=escribir_eventos),
        threading.Thread(target=escribir_eventos),
        threading.Thread(target=actualizar_estado),
        threading.Thread(target=actualizar_estado),
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30.0)

    assert not any(h.is_alive() for h in hilos), "algun hilo quedo colgado"
    assert errores == [], f"escrituras concurrentes fallaron: {errores}"
    assert len(repository.list_alarm_events(limit=500)) == 50
