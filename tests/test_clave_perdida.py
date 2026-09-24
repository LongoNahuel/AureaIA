"""Perder camera_key no regenera la clave ni borra credenciales (Fase 3).

Ver el docstring de core/credential_store.py: sin una clave valida la app
arranca degradada (CLAVE_PERDIDA), las credenciales quedan como token
intacto (`es_ilegible`), nada conecta con una contraseña basura o vacia, y
salir del estado es una decision explicita.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from cryptography.fernet import Fernet

import aurea_vms.core.stream_manager as sm_module
import aurea_vms.main as main_module
from aurea_vms.core import credential_store, device_manager
from aurea_vms.core.credential_store import ClavePerdidaError
from aurea_vms.models import db as db_module
from aurea_vms.models import repository
from aurea_vms.models.device import Device


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Data-dir aislado para la clave (es un global de modulo)."""
    settings = SimpleNamespace(data_dir=tmp_path, ensure_dirs=lambda: None)
    monkeypatch.setattr(credential_store, "settings", settings)
    credential_store.reset_cache()
    yield tmp_path
    credential_store.reset_cache()


def _clave(data_dir: Path) -> Path:
    return data_dir / credential_store.KEY_FILENAME


def _token_con_otra_clave(texto: str = "secreto") -> str:
    return credential_store.PREFIJO + Fernet(Fernet.generate_key()).encrypt(texto.encode()).decode()


class TestEstado:
    def test_sin_clave_y_con_credenciales_queda_perdida_y_no_crea_archivo(self, data_dir):
        token = _token_con_otra_clave()

        assert credential_store.verificar([token]) == credential_store.ESTADO_CLAVE_PERDIDA
        assert not _clave(data_dir).exists()
        assert credential_store.credenciales_ilegibles() == 1
        with pytest.raises(ClavePerdidaError):
            credential_store.cifrar("nueva")
        assert not _clave(data_dir).exists()

    def test_sin_clave_y_sin_credenciales_la_crea_al_guardar_la_primera(self, data_dir):
        """Instalacion nueva: no hay nada que perder."""
        assert credential_store.verificar([]) == credential_store.ESTADO_OK

        token = credential_store.cifrar("hola")

        assert _clave(data_dir).exists()
        assert credential_store.descifrar(token) == "hola"

    @pytest.mark.parametrize("contenido", [b"", b"\n", b"abc123", Fernet.generate_key()[:30]])
    def test_un_archivo_vacio_o_truncado_es_clave_perdida(self, data_dir, contenido):
        _clave(data_dir).write_bytes(contenido)

        assert credential_store.verificar([]) == credential_store.ESTADO_CLAVE_PERDIDA
        assert "vacía o dañada" in credential_store.motivo()
        assert _clave(data_dir).read_bytes() == contenido  # no se pisa

    def test_un_archivo_vacio_sin_verificar_da_clave_perdida_y_no_value_error(self, data_dir):
        """Antes, Fernet(b"") levantaba ValueError en el primer cifrar."""
        _clave(data_dir).write_bytes(b"")

        with pytest.raises(ClavePerdidaError):
            credential_store.cifrar("x")

    def test_una_clave_que_no_descifra_ninguna_credencial_es_otra_clave(self, data_dir):
        _clave(data_dir).write_bytes(Fernet.generate_key())

        estado = credential_store.verificar([_token_con_otra_clave(), _token_con_otra_clave()])

        assert estado == credential_store.ESTADO_CLAVE_PERDIDA
        assert "otra clave" in credential_store.motivo()
        with pytest.raises(ClavePerdidaError):
            credential_store.cifrar("nueva")

    def test_si_descifra_alguna_sigue_ok_y_cuenta_las_ilegibles(self, data_dir):
        buena = credential_store.cifrar("buena")

        estado = credential_store.verificar([buena, _token_con_otra_clave()])

        assert estado == credential_store.ESTADO_OK
        assert credential_store.credenciales_ilegibles() == 1
        assert credential_store.descifrar(buena) == "buena"


class TestDescifrar:
    def test_sin_clave_devuelve_el_token_y_no_crea_una(self, data_dir):
        token = _token_con_otra_clave()

        assert credential_store.descifrar(token) == token
        assert credential_store.es_ilegible(token)
        assert not _clave(data_dir).exists()

    def test_avisa_una_sola_vez(self, data_dir, caplog):
        """A6: una lectura por camara y por refresco inundaba el log."""
        token = _token_con_otra_clave()

        with caplog.at_level("ERROR", logger=credential_store.__name__):
            for _ in range(5):
                credential_store.descifrar(token)

        assert len([r for r in caplog.records if "No se pudo descifrar" in r.message]) == 1

    def test_una_contrasena_en_claro_no_es_ilegible(self, data_dir):
        assert not credential_store.es_ilegible("en-claro")
        assert not credential_store.es_ilegible("")
        assert not credential_store.es_ilegible(None)


class TestCreacionDeLaClave:
    def test_dos_hilos_a_la_vez_crean_una_sola_clave(self, data_dir, monkeypatch):
        """Con un generate_key lento se ensancha la ventana de la carrera.
        Medido: sin el lock NI la publicacion con os.link, 4 de 8 tokens
        quedaban cifrados con una clave que despues se piso. Cualquiera de
        las dos protecciones alcanza entre hilos; entre procesos solo os.link
        (ver el test siguiente)."""
        generar = Fernet.generate_key
        monkeypatch.setattr(
            credential_store.Fernet,
            "generate_key",
            staticmethod(lambda: time.sleep(0.05) or generar()),
        )
        barrera = threading.Barrier(8)
        tokens: list[str] = []

        def cifrar():
            barrera.wait()
            tokens.append(credential_store.cifrar("x"))

        hilos = [threading.Thread(target=cifrar) for _ in range(8)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()

        credential_store.reset_cache()
        assert [credential_store.descifrar(t) for t in tokens] == ["x"] * 8

    def test_no_pisa_una_clave_que_otro_proceso_creo_primero(self, data_dir, monkeypatch):
        """El otro proceso gana la carrera entre el `exists()` y la
        publicacion: os.link falla y se usa la suya."""
        ajena = Fernet.generate_key()
        link_real = credential_store.os.link

        def link_con_carrera(origen, destino):
            Path(destino).write_bytes(ajena)
            return link_real(origen, destino)

        monkeypatch.setattr(credential_store.os, "link", link_con_carrera)

        token = credential_store.cifrar("hola")

        assert _clave(data_dir).read_bytes() == ajena
        assert Fernet(ajena).decrypt(token[len(credential_store.PREFIJO) :].encode()) == b"hola"
        assert list(data_dir.glob(".camera_key.*.tmp")) == []


class TestRegenerar:
    def test_aparta_la_clave_danada_y_vuelve_a_poder_guardar(self, data_dir):
        _clave(data_dir).write_bytes(b"roto")
        credential_store.verificar([])

        apartada = credential_store.regenerar_clave()

        assert apartada is not None and apartada.read_bytes() == b"roto"
        assert credential_store.estado() == credential_store.ESTADO_OK
        assert credential_store.descifrar(credential_store.cifrar("nueva")) == "nueva"

    def test_las_credenciales_viejas_siguen_ilegibles(self, data_dir):
        token = _token_con_otra_clave()
        credential_store.verificar([token])

        assert credential_store.regenerar_clave() is None  # no habia archivo

        assert credential_store.es_ilegible(credential_store.descifrar(token))


class TestEnLaBase:
    """init_db verifica la clave contra las credenciales de la base."""

    def _camara_con_clave_borrada(self, tmp_path, data_dir) -> tuple[Path, int, str]:
        ruta = tmp_path / "base.sqlite3"
        db_module.init_db(ruta, force=True)
        device = repository.add_device(
            name="Cam",
            ip="10.0.0.1",
            rtsp_main_url="rtsp://c/x",
            username="admin",
            password="secreto",
        )
        clave = _clave(data_dir).read_bytes()
        _clave(data_dir).unlink()
        db_module.init_db(ruta, force=True)
        return ruta, device.id, clave

    def test_init_db_detecta_la_clave_perdida(self, tmp_path, data_dir):
        self._camara_con_clave_borrada(tmp_path, data_dir)

        assert credential_store.estado() == credential_store.ESTADO_CLAVE_PERDIDA
        assert credential_store.credenciales_ilegibles() == 1
        assert not _clave(data_dir).exists()

    def test_guardar_el_dispositivo_sin_tocar_la_contrasena_conserva_el_token(
        self, tmp_path, data_dir
    ):
        ruta, device_id, _ = self._camara_con_clave_borrada(tmp_path, data_dir)
        token = _crudo(ruta)

        assert repository.get_device(device_id).password == token
        repository.update_device(device_id, name="Renombrada")

        assert _crudo(ruta) == token

    def test_guardar_una_contrasena_nueva_falla_de_forma_visible(self, tmp_path, data_dir):
        _, device_id, _ = self._camara_con_clave_borrada(tmp_path, data_dir)

        with pytest.raises(ClavePerdidaError):
            repository.update_device(device_id, password="nueva")

    def test_restaurar_la_clave_recupera_las_contrasenas(self, tmp_path, data_dir):
        """Por esto el token se conserva en vez de pisarse con el vacio."""
        ruta, device_id, clave = self._camara_con_clave_borrada(tmp_path, data_dir)
        repository.update_device(device_id, name="Renombrada")
        _clave(data_dir).write_bytes(clave)

        db_module.init_db(ruta, force=True)

        assert credential_store.estado() == credential_store.ESTADO_OK
        assert repository.get_device(device_id).password == "secreto"


def _crudo(ruta: Path) -> str:
    con = sqlite3.connect(ruta)
    try:
        return con.execute("SELECT password FROM devices").fetchone()[0]
    finally:
        con.close()


@pytest.fixture(autouse=True)
def _engine_limpio():
    yield
    if db_module._engine is not None:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionLocal = None


def _device_con_password(password: str) -> Device:
    device = Device(
        name="Cam",
        ip="10.0.0.10",
        rtsp_main_url="rtsp://cam/main",
        username="admin",
        password=password,
    )
    device.id = 7
    return device


class TestNadaConectaConUnaCredencialIlegible:
    def test_el_stream_no_abre_la_camara_y_queda_offline_con_motivo(self, data_dir, monkeypatch):
        aperturas: list[str] = []
        monkeypatch.setattr(sm_module.cv2, "VideoCapture", lambda url, *_a: aperturas.append(url))
        estados: list = []
        # Doble del bus: el signal real se emite desde el hilo del worker y
        # la conexion encolada no se entrega sin event loop.
        monkeypatch.setattr(
            sm_module,
            "event_bus",
            SimpleNamespace(device_status=SimpleNamespace(emit=estados.append)),
        )
        persistidos: list = []
        monkeypatch.setattr(
            sm_module.repository, "update_device_status", lambda *a: persistidos.append(a)
        )
        worker = sm_module.StreamWorker(_device_con_password(_token_con_otra_clave()))

        worker.start()
        # Se espera el reporte antes del stop(): despues de stop() el worker
        # ya no informa estado (Fase 5), y un stop inmediato le ganaba.
        limite = time.monotonic() + 2
        while not estados and time.monotonic() < limite:
            time.sleep(0.01)
        worker.stop()
        worker.join(2)

        assert not worker.is_alive()
        assert aperturas == []
        assert estados and not estados[0].online
        assert "Sin credencial" in estados[0].detail
        assert persistidos == [(7, "offline")]

    def test_una_contrasena_legible_si_conecta(self, data_dir, monkeypatch):
        """Control: el guard no frena a una camara normal."""
        aperturas: list[str] = []

        class Cerrada:
            def isOpened(self):  # noqa: N802 - API de cv2
                return False

            def release(self):
                pass

        def abrir(url, *_a):
            aperturas.append(url)
            return Cerrada()

        monkeypatch.setattr(sm_module.cv2, "VideoCapture", abrir)
        monkeypatch.setattr(sm_module.repository, "update_device_status", lambda *_a: None)
        worker = sm_module.StreamWorker(_device_con_password("clave-legible"))

        worker.start()
        worker.stop()
        worker.join(2)

        assert aperturas and "clave-legible" in aperturas[0]

    def test_la_prueba_de_conexion_no_abre_la_camara(self, data_dir, monkeypatch):
        monkeypatch.setattr(
            device_manager.threading,
            "Thread",
            lambda *a, **k: pytest.fail("se intentó abrir la cámara"),
        )
        monkeypatch.setattr(sm_module.stream_manager, "get_worker", lambda _id: None)

        frame, mensaje = device_manager.grab_snapshot(_device_con_password(_token_con_otra_clave()))

        assert frame is None
        assert "no se puede descifrar" in mensaje


class TestAvisoDelArranque:
    @pytest.fixture()
    def perdida(self, data_dir, tmp_path, monkeypatch):
        monkeypatch.setattr(
            main_module,
            "settings",
            SimpleNamespace(data_dir=data_dir, db_path=tmp_path / "aurea_vms.sqlite3"),
        )
        credential_store.verificar([_token_con_otra_clave()])

    def _no_pregunta(self, _texto):
        pytest.fail("no tenía que preguntar")

    def test_sin_clave_perdida_no_pregunta(self, data_dir):
        credential_store.verificar([])

        assert main_module._resolver_clave_perdida(preguntar=self._no_pregunta) is True

    def test_salir(self, perdida):
        assert main_module._resolver_clave_perdida(preguntar=lambda _t: main_module.SALIR) is False

    def test_continuar_sigue_degradada(self, perdida):
        assert main_module._resolver_clave_perdida(preguntar=lambda _t: main_module.CONTINUAR)
        assert credential_store.estado() == credential_store.ESTADO_CLAVE_PERDIDA

    def test_regenerar_sin_confirmar_sigue_degradada(self, perdida):
        main_module._resolver_clave_perdida(
            preguntar=lambda _t: main_module.REGENERAR, confirmar=lambda _n: False
        )

        assert credential_store.estado() == credential_store.ESTADO_CLAVE_PERDIDA

    def test_regenerar_confirmado_vuelve_a_ok(self, perdida, data_dir):
        confirmadas: list[int] = []

        main_module._resolver_clave_perdida(
            preguntar=lambda _t: main_module.REGENERAR,
            confirmar=lambda n: confirmadas.append(n) or True,
        )

        assert confirmadas == [1]  # la confirmacion dice cuantas se pierden
        assert credential_store.estado() == credential_store.ESTADO_OK
        assert _clave(data_dir).exists()

    def test_el_aviso_dice_donde_va_la_clave_y_lista_los_backups(self, perdida, data_dir, tmp_path):
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "aurea_vms-0005-20260924.camera_key").write_bytes(b"k")

        texto = main_module._texto_clave_perdida()

        assert str(_clave(data_dir)) in texto
        assert "aurea_vms-0005-20260924.camera_key" in texto
        assert "Cámaras afectadas: 1" in texto


class TestDowngradeDe0005:
    def test_con_la_clave_equivocada_se_niega_y_no_pierde_la_contrasena(self, tmp_path, data_dir):
        ruta = tmp_path / "base.sqlite3"
        config = db_module._config_de_alembic(ruta)
        command.upgrade(config, "0004_constraints")
        con = sqlite3.connect(ruta)
        con.execute(
            "INSERT INTO devices (name, device_type, channel, ip, port, username, password,"
            " rtsp_main_url, has_ptz, status) VALUES ('C', 'ipc', 1, '1.1.1.1', 554, 'admin',"
            " 'secreto', 'rtsp://x', 0, 'unknown')"
        )
        con.commit()
        con.close()
        command.upgrade(config, "0005_seguridad")  # cifra con la clave del data_dir
        token = _crudo(ruta)
        _clave(data_dir).write_bytes(Fernet.generate_key())
        credential_store.reset_cache()

        with pytest.raises(RuntimeError, match="no se puede descifrar"):
            command.downgrade(config, "0004_constraints")

        assert _crudo(ruta) == token
        con = sqlite3.connect(ruta)
        try:
            assert con.execute("SELECT version_num FROM alembic_version").fetchall() == [
                ("0005_seguridad",)
            ]
        finally:
            con.close()
