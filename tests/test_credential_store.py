"""Tests del cifrado en reposo de las credenciales de cámara.

Lo que se prueba acá es el alcance real de la protección, que está escrito
sin adornos en el docstring del módulo: la clave vive al lado de la base, así
que esto **no es secreto** contra alguien que ya entró al equipo. Lo que sí
hace es sacar las contraseñas del `.sqlite3` — o sea de cualquier backup,
volcado o inspección casual durante un soporte.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
from types import SimpleNamespace

import pytest

from aurea_vms.core import credential_store
from aurea_vms.models import db as db_module
from aurea_vms.models import repository


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Data-dir aislado: la clave es un global de módulo."""
    monkeypatch.setattr(
        credential_store,
        "settings",
        SimpleNamespace(data_dir=tmp_path, ensure_dirs=lambda: None),
    )
    credential_store.reset_cache()
    yield tmp_path
    credential_store.reset_cache()


class TestCifrado:
    def test_ida_y_vuelta(self, data_dir):
        assert credential_store.descifrar(credential_store.cifrar("hola")) == "hola"

    def test_el_texto_cifrado_no_contiene_el_original(self, data_dir):
        cifrada = credential_store.cifrar("secreto-de-camara")

        assert "secreto-de-camara" not in cifrada
        assert cifrada.startswith(credential_store.PREFIJO)

    def test_dos_cifrados_del_mismo_valor_son_distintos(self, data_dir):
        """Fernet lleva IV y timestamp: si dos contraseñas iguales dieran el
        mismo token, mirar la base diría quién comparte contraseña."""
        assert credential_store.cifrar("misma") != credential_store.cifrar("misma")

    def test_una_cadena_vacia_se_deja_como_esta(self, data_dir):
        """Cifrar el vacío solo gasta bytes y delata que el campo existe."""
        assert credential_store.cifrar("") == ""
        assert credential_store.descifrar("") == ""

    def test_cifrar_es_idempotente(self, data_dir):
        una_vez = credential_store.cifrar("hola")

        assert credential_store.cifrar(una_vez) == una_vez

    def test_soporta_acentos_y_simbolos(self, data_dir):
        clave = "contraseña-ñÑ-€-\"'<>&"

        assert credential_store.descifrar(credential_store.cifrar(clave)) == clave


class TestFilasLegadas:
    def test_un_valor_sin_prefijo_se_devuelve_tal_cual(self, data_dir):
        """La migración a cifrado es perezosa: una fila que todavía está en
        claro se lee sin romper nada."""
        assert credential_store.descifrar("contraseña-vieja") == "contraseña-vieja"

    def test_esta_cifrado_distingue_los_dos_formatos(self, data_dir):
        assert not credential_store.esta_cifrado("en-claro")
        assert credential_store.esta_cifrado(credential_store.cifrar("x"))


class TestLaClave:
    def test_se_crea_una_sola_vez_y_se_reusa(self, data_dir):
        cifrada = credential_store.cifrar("hola")
        credential_store.reset_cache()

        assert credential_store.descifrar(cifrada) == "hola"

    @pytest.mark.skipif(sys.platform == "win32", reason="permisos POSIX")
    def test_se_escribe_solo_para_el_dueno(self, data_dir):
        """0600. Y se crea CON los permisos puestos, no con un chmod
        después: entre el write y el chmod hay una ventana en la que la
        clave es legible por cualquiera."""
        credential_store.cifrar("hola")

        modo = os.stat(data_dir / credential_store.KEY_FILENAME).st_mode

        assert stat.S_IMODE(modo) == 0o600

    def test_con_la_clave_cambiada_no_rompe_la_app(self, data_dir, caplog):
        """Que no se pueda descifrar UNA contraseña no puede dejar la lista
        de cámaras sin abrir: se devuelve el token intacto (no el vacío, con
        el que el stream conectaba) y se loguea. El detalle de la clave
        perdida está en tests/test_clave_perdida.py."""
        cifrada = credential_store.cifrar("secreto")
        (data_dir / credential_store.KEY_FILENAME).unlink()
        credential_store.reset_cache()

        with caplog.at_level("ERROR", logger=credential_store.__name__):
            assert credential_store.descifrar(cifrada) == cifrada

        assert credential_store.es_ilegible(cifrada)
        assert "no se pudo descifrar" in caplog.text.lower()


class TestEnLaBaseDeDatos:
    """El TypeDecorator: cifrado en la base, texto plano en Python."""

    def test_la_contrasena_no_queda_en_claro_en_el_archivo(self, temp_db):
        repository.add_device(
            name="Cam",
            ip="10.0.0.1",
            rtsp_main_url="rtsp://c/x",
            username="admin",
            password="secreto-de-camara",
        )

        ruta = db_module._engine.url.database
        con = sqlite3.connect(ruta)
        try:
            guardada = con.execute("SELECT password FROM devices").fetchone()[0]
        finally:
            con.close()

        assert "secreto-de-camara" not in guardada
        assert credential_store.esta_cifrado(guardada)

    def test_en_python_sigue_siendo_texto_plano(self, temp_db):
        """Los doce lugares que leen device.password no cambiaron: el
        cifrado vive en el tipo de columna."""
        repository.add_device(
            name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x", password="secreto"
        )

        assert repository.list_devices()[0].password == "secreto"

    def test_la_url_rtsp_se_arma_bien(self, temp_db):
        """La prueba de que no se rompió nada aguas abajo: la URL
        autenticada que consume el StreamWorker."""
        from aurea_vms.core.device_manager import build_authenticated_url

        repository.add_device(
            name="Cam",
            ip="10.0.0.1",
            rtsp_main_url="rtsp://10.0.0.1/live",
            username="admin",
            password="p@ss:word",
        )
        device = repository.list_devices()[0]

        url = build_authenticated_url(device.rtsp_main_url, device.username, device.password)

        assert url == "rtsp://admin:p%40ss%3Aword@10.0.0.1/live"

    def test_una_camara_sin_contrasena_sigue_funcionando(self, temp_db):
        repository.add_device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")

        assert repository.list_devices()[0].password == ""

    def test_cambiar_la_contrasena_la_re_cifra(self, temp_db):
        device = repository.add_device(
            name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x", password="vieja"
        )

        repository.update_device(device.id, password="nueva")

        assert repository.get_device(device.id).password == "nueva"
        ruta = db_module._engine.url.database
        con = sqlite3.connect(ruta)
        try:
            assert "nueva" not in con.execute("SELECT password FROM devices").fetchone()[0]
        finally:
            con.close()
