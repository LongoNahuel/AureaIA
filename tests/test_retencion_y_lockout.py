"""Fase 4 (2026-09-24): retención que no borra evidencia, y lockout sin trampas.

- preferences.json: escritura atomica, y "ilegible" no es "no existe".
- La retencion no poda si no puede leer lo que configuro el operador.
- Lockout: tope si el reloj retrocede, reset al vencer, sin enumeracion por
  tiempo, hash corrupto = contraseña incorrecta, contador atomico.
"""

from __future__ import annotations

import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from aurea_vms.core import app_prefs, auth, media_store, retention
from aurea_vms.models import repository
from aurea_vms.models.user import ROLE_OPERATOR

BUENA = "Correcta123!"


@pytest.fixture(autouse=True)
def _prefs_en_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(app_prefs, "_PREFS_PATH", tmp_path / "preferences.json")
    monkeypatch.setattr(app_prefs, "_avisado", False)


def _prefs() -> object:
    return app_prefs._PREFS_PATH


class TestEscrituraAtomica:
    def test_un_corte_a_mitad_de_la_escritura_deja_el_archivo_anterior(self, monkeypatch):
        app_prefs.set_retention_days(90)
        antes = _prefs().read_text(encoding="utf-8")

        def dump_cortado(data, handle, **_kwargs):
            handle.write('{"retention_days": ')  # lo que quedaria tras un corte
            raise OSError("corte de luz simulado")

        monkeypatch.setattr(app_prefs.json, "dump", dump_cortado)
        with pytest.raises(OSError):
            app_prefs.set_retention_days(30)

        assert _prefs().read_text(encoding="utf-8") == antes
        assert list(_prefs().parent.glob(".preferences.json.*")) == []

    def test_guardar_sobre_un_archivo_ilegible_lo_aparta(self):
        _prefs().write_text('{"retention_days": 9', encoding="utf-8")

        app_prefs.set_theme("light")

        (apartado,) = _prefs().parent.glob("preferences.json.ilegible-*")
        assert apartado.read_text(encoding="utf-8") == '{"retention_days": 9'
        assert json.loads(_prefs().read_text(encoding="utf-8"))["theme"] == "light"


class TestLecturaEstricta:
    def test_sin_archivo_son_los_defaults(self):
        assert app_prefs.leer_retencion() == (7.0, 5.0)

    def test_con_valores_guardados(self):
        app_prefs.set_retention_days(90)
        app_prefs.set_retention_max_gb(500)

        assert app_prefs.leer_retencion() == (90.0, 500.0)

    @pytest.mark.parametrize(
        "contenido",
        [
            '{"retention_days": 90, "retention_max',  # truncado
            "",  # vacio
            "[1, 2]",  # JSON que no es un objeto
            '{"retention_days": "noventa"}',
            '{"retention_days": 0}',  # podar con 0 dias es borrar todo
            '{"retention_max_gb": 0.1}',  # debajo del minimo de la UI
            '{"retention_days": null}',
        ],
    )
    def test_un_archivo_ilegible_o_invalido_levanta(self, contenido):
        _prefs().write_text(contenido, encoding="utf-8")

        with pytest.raises(app_prefs.PrefsIlegibles):
            app_prefs.leer_retencion()

    def test_bytes_que_no_son_utf8_levantan(self):
        _prefs().write_bytes(b"\xff\xfe\x00basura")

        with pytest.raises(app_prefs.PrefsIlegibles):
            app_prefs.leer_retencion()

    def test_la_ui_sigue_abriendo_y_lo_avisa_una_vez(self, caplog):
        _prefs().write_text("[1, 2]", encoding="utf-8")

        with caplog.at_level("ERROR", logger=app_prefs.__name__):
            assert app_prefs.get_theme() == "dark"
            assert app_prefs.intelligent_branding_enabled() is True

        assert len([r for r in caplog.records if "ilegibles" in r.message]) == 1


@pytest.fixture()
def camara_con_media_vieja(temp_db, tmp_path, monkeypatch):
    """Una camara con un clip de hace 10 dias en disco y en el indice."""
    monkeypatch.setattr(media_store, "settings", SimpleNamespace(media_dir=tmp_path / "media"))
    device = repository.add_device(name="Cam", ip="10.0.0.9", rtsp_main_url="rtsp://c/x")
    ts = time.time() - 10 * 86400
    rel = media_store.build_rel_path("clip", device.id, int(ts), ts, ".bin")
    path = media_store.prepare_path(rel)
    path.write_bytes(b"x" * 100)
    repository.add_media_asset(
        kind="clip", device_id=device.id, timestamp=ts, rel_path=rel, size_bytes=100
    )
    return path


def _una_pasada() -> None:
    worker = retention.RetentionWorker(interval_s=60, first_delay_s=0)
    worker.start()
    time.sleep(0.3)
    worker.stop()
    worker.join(2)
    assert not worker.is_alive()


class TestRetencion:
    def test_con_el_json_truncado_no_poda_nada(self, camara_con_media_vieja, caplog):
        """El caso que motivo la fase: el operador configuro 90 dias, un corte
        trunco el JSON, y los defaults (7 dias) se llevaban el clip de hace 10."""
        app_prefs.set_retention_days(90)
        _prefs().write_text('{"retention_days": 90, "reten', encoding="utf-8")

        with caplog.at_level("ERROR", logger=retention.__name__):
            _una_pasada()

        assert camara_con_media_vieja.exists()
        assert len(repository.list_media()) == 1
        assert "Retención suspendida" in caplog.text

    def test_con_las_preferencias_sanas_poda_lo_que_corresponde(self, camara_con_media_vieja):
        """Control: el mismo clip, con 7 dias configurados, se va."""
        app_prefs.set_retention_days(7)

        _una_pasada()

        assert not camara_con_media_vieja.exists()

    def test_la_poda_usa_los_valores_del_operador(self, monkeypatch):
        llamadas: list[dict] = []
        monkeypatch.setattr(
            retention, "prune", lambda **kw: llamadas.append(kw) or {"deleted": 0, "freed_bytes": 0}
        )
        app_prefs.set_retention_days(90)
        app_prefs.set_retention_max_gb(500)

        _una_pasada()

        assert llamadas == [{"max_age_days": 90.0, "max_total_gb": 500.0}]


def _usuario(username: str = "op"):
    return auth.create_user(username, BUENA, ROLE_OPERATOR)


def _contar_pbkdf2(monkeypatch) -> list[int]:
    iteraciones: list[int] = []
    real = hashlib.pbkdf2_hmac

    def contado(nombre, password, salt, n, *args):
        iteraciones.append(n)
        return real(nombre, password, salt, n, *args)

    monkeypatch.setattr(auth.hashlib, "pbkdf2_hmac", contado)
    return iteraciones


class TestLockout:
    def test_si_el_reloj_retrocede_el_bloqueo_no_pasa_del_maximo(self, temp_db):
        user = _usuario()
        # locked_until "diez dias en el futuro": lo que queda si el reloj
        # vuelve atras despues de bloquear.
        repository.update_user(
            user.id, failed_attempts=auth.MAX_INTENTOS, locked_until=time.time() + 10 * 86400
        )

        with pytest.raises(auth.CuentaBloqueada) as bloqueo:
            auth.authenticate("op", BUENA)

        assert bloqueo.value.segundos_restantes == auth.BLOQUEO_SEGUNDOS

    def test_vencido_el_bloqueo_un_error_no_vuelve_a_bloquear(self, temp_db):
        user = _usuario()
        for _ in range(auth.MAX_INTENTOS):
            auth.authenticate("op", "incorrecta1!")
        repository.update_user(user.id, locked_until=time.time() - 1)

        auth.authenticate("op", "incorrecta1!")

        # El contador arranco de cero tras vencer, no siguio desde 5...
        assert repository.get_user_by_username("op").failed_attempts == 1
        # ...asi que la contraseña correcta entra, en vez de CuentaBloqueada.
        assert auth.authenticate("op", BUENA) is not None

    def test_dos_fallos_con_datos_viejos_cuentan_dos(self, temp_db):
        """Dos intentos que leyeron el mismo contador (dos instancias, o el
        login y el cambio de contraseña a la vez): antes uno se perdia."""
        _usuario()
        leido_1 = repository.get_user_by_username("op")
        leido_2 = repository.get_user_by_username("op")

        auth._registrar_fallo(leido_1)
        auth._registrar_fallo(leido_2)

        assert repository.get_user_by_username("op").failed_attempts == 2


class TestEnumeracion:
    def test_un_usuario_inexistente_gasta_el_mismo_pbkdf2(self, temp_db, monkeypatch):
        _usuario()
        iteraciones = _contar_pbkdf2(monkeypatch)

        auth.authenticate("op", "incorrecta1!")
        real = list(iteraciones)
        iteraciones.clear()
        auth.authenticate("fantasma", "incorrecta1!")

        assert iteraciones == real == [auth.ITERACIONES]

    def test_tambien_en_el_cambio_de_contrasena(self, temp_db, monkeypatch):
        iteraciones = _contar_pbkdf2(monkeypatch)

        mensaje = auth.change_password("fantasma", "incorrecta1!", "Nueva123!x")

        assert mensaje == "La contraseña actual no es correcta."
        assert iteraciones == [auth.ITERACIONES]


class TestHashCorrupto:
    @pytest.mark.parametrize(
        "guardado",
        [
            "pbkdf2_sha256$muchas$00ff$abcd",  # iteraciones que no son numero
            "pbkdf2_sha256$1000$no-es-hex$abcd",  # salt ilegible
            "pbkdf2_sha256$0$00ff$abcd",
            "pbkdf2_sha256$-5$00ff$abcd",
        ],
    )
    def test_es_contrasena_incorrecta_y_no_una_excepcion(self, temp_db, guardado):
        user = _usuario()
        repository.update_user(user.id, password_hash=guardado)

        assert auth.authenticate("op", BUENA) is None
        assert repository.get_user_by_username("op").failed_attempts == 1

    def test_un_coste_absurdo_no_se_calcula(self, temp_db, monkeypatch):
        """1e12 iteraciones congelarian el login por horas."""
        user = _usuario()
        repository.update_user(user.id, password_hash="pbkdf2_sha256$1000000000000$00ff$abcd")
        iteraciones = _contar_pbkdf2(monkeypatch)

        assert auth.authenticate("op", BUENA) is None
        assert all(n <= auth.ITERACIONES_MAXIMAS for n in iteraciones)

    def test_un_salt_legado_ilegible_es_contrasena_incorrecta(self, temp_db):
        user = _usuario()
        repository.update_user(user.id, password_hash="ab" * 32, salt="no-es-hex")

        assert auth.authenticate("op", BUENA) is None
