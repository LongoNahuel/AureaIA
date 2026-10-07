"""La disposicion de ventanas por usuario (Fase V4, 2026-10-07): se guarda al
cerrar y se restaura despues del login, y lo que no se puede restaurar se
degrada en vez de fallar."""

from __future__ import annotations

import logging

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect
from PySide6.QtGui import QGuiApplication
from shiboken6 import isValid

import aurea_vms.ui.widgets.video_tile as vt_module
from aurea_vms.core import auth
from aurea_vms.models import repository
from aurea_vms.ui import layout_store, pestanas
from aurea_vms.ui import main_window as mw
from aurea_vms.ui.modules.live_view import LiveViewModule
from aurea_vms.ui.modules.system_module import SystemModule
from aurea_vms.ui.modules.user_management_module import UserManagementModule


def _borrados_pendientes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture()
def sin_streams(monkeypatch):
    """Asignar una camara no abre RTSP: solo se cuenta."""
    monkeypatch.setattr(vt_module.stream_manager, "acquire", lambda *_a, **_k: None)
    monkeypatch.setattr(vt_module.stream_manager, "release", lambda *_a, **_k: None)


@pytest.fixture()
def usuario(temp_db):
    user = repository.add_user(username="op", password_hash="h", salt="", role="admin")
    auth.current_user = user
    return user


@pytest.fixture()
def abrir_ventana(qapp, usuario, sin_streams):
    abiertas: list = []

    def abrir():
        window = mw.MainWindow()
        window.show()
        abiertas.append(window)
        return window

    yield abrir
    for window in abiertas:
        if isValid(window):
            window.close()
    _borrados_pendientes()


def _cerrar(window) -> None:
    window.close()
    _borrados_pendientes()


def _abrir(window, module_cls):
    return window.open_module_by_index(mw.module_index(module_cls))


def _camara(nombre: str = "Cam") -> int:
    return repository.add_device(name=nombre, ip="10.0.0.1", rtsp_main_url="rtsp://x/1").id


class TestIdaYVuelta:
    def test_ventanas_pestañas_grilla_y_camaras_vuelven_igual(self, abrir_ventana):
        cam_a, cam_b = _camara("A"), _camara("B")
        primera = abrir_ventana()
        vivo = _abrir(primera, LiveViewModule)
        vivo.set_state({"grilla": 2, "camaras": [cam_a, None, cam_b]})
        sistema = _abrir(primera, SystemModule)
        segunda = primera.nueva_ventana()
        pestanas.mover(vivo, primera, segunda)
        segunda.show()
        primera.tabs.setCurrentWidget(sistema)
        _cerrar(primera)

        restaurada = abrir_ventana()
        restaurada.restaurar_disposicion()

        secundarias = restaurada.ventanas.secundarias()
        assert len(secundarias) == 1
        vivo_nuevo = secundarias[0].tabs.currentWidget()
        assert isinstance(vivo_nuevo, LiveViewModule)
        assert vivo_nuevo.get_state()["grilla"] == 2
        assert vivo_nuevo.get_state()["camaras"][:3] == [cam_a, None, cam_b]
        assert isinstance(restaurada.tabs.currentWidget(), SystemModule)
        assert secundarias[0].isVisible()

    def test_se_guarda_antes_de_soltar_las_camaras(self, abrir_ventana):
        """on_window_closed vacia los recuadros: guardar despues perdia todo."""
        cam = _camara()
        window = abrir_ventana()
        _abrir(window, LiveViewModule).set_state({"camaras": [cam]})
        usuario_id = window.user_id

        _cerrar(window)

        pestana = repository.get_ui_layout(usuario_id).data["ventanas"][0]["pestanas"][0]
        assert pestana["modulo"] == "LiveViewModule"
        assert pestana["estado"]["camaras"][0] == cam

    def test_cerrar_sesion_guarda_aunque_auth_ya_no_tenga_usuario(self, abrir_ventana, usuario):
        window = abrir_ventana()
        _abrir(window, SystemModule)
        auth.current_user = None  # lo que hace _on_logout antes de close()

        _cerrar(window)

        assert repository.get_ui_layout(usuario.id) is not None

    def test_sin_disposicion_guardada_queda_inicio(self, abrir_ventana):
        window = abrir_ventana()
        window.restaurar_disposicion()

        assert window.tabs.count() == 1
        assert window.ventanas.secundarias() == []

    def test_el_autoguardado_espera_y_guarda(self, qtbot, abrir_ventana, usuario):
        window = abrir_ventana()
        window._autoguardado.setInterval(20)

        _abrir(window, SystemModule)  # la pestaña nueva marca el cambio

        qtbot.waitUntil(lambda: repository.get_ui_layout(usuario.id) is not None, timeout=2000)


class TestDegradacion:
    def _guardar(self, usuario, ventanas, version: int = layout_store.SCHEMA_VERSION) -> None:
        repository.save_ui_layout(usuario.id, version, {"ventanas": ventanas})

    def test_una_camara_borrada_deja_el_recuadro_vacio(self, abrir_ventana, usuario):
        cam = _camara()
        self._guardar(
            usuario,
            [
                {
                    "principal": True,
                    "pestanas": [{"modulo": "LiveViewModule", "estado": {"camaras": [cam, 999]}}],
                }
            ],
        )
        window = abrir_ventana()

        window.restaurar_disposicion()

        vivo = window.ventanas.buscar_modulo(LiveViewModule)[1]
        assert vivo.get_state()["camaras"][:2] == [cam, None]

    def test_un_modulo_sin_permiso_no_se_abre(self, abrir_ventana, usuario):
        """El rol cambio desde que se guardo: Usuarios era de admin."""
        self._guardar(
            usuario,
            [
                {
                    "principal": True,
                    "pestanas": [{"modulo": "UserManagementModule"}, {"modulo": "LiveViewModule"}],
                }
            ],
        )
        repository.update_user(usuario.id, role="operador")
        auth.current_user = repository.get_user_by_username("op")
        window = abrir_ventana()
        avisos: list = []
        window_warn = mw.warn
        mw.warn = lambda *a, **k: avisos.append(a)
        try:
            window.restaurar_disposicion()
        finally:
            mw.warn = window_warn

        assert window.ventanas.buscar_modulo(UserManagementModule) is None
        assert window.ventanas.buscar_modulo(LiveViewModule) is not None
        assert avisos == []  # sin cartel de "acceso restringido" al entrar

    def test_un_modulo_que_ya_no_existe_se_saltea(self, abrir_ventana, usuario):
        self._guardar(
            usuario,
            [
                {
                    "principal": True,
                    "pestanas": [{"modulo": "ModuloBorrado"}, {"modulo": "SystemModule"}],
                }
            ],
        )
        window = abrir_ventana()

        window.restaurar_disposicion()

        assert window.ventanas.buscar_modulo(SystemModule) is not None

    def test_una_secundaria_que_queda_vacia_no_se_arma(self, abrir_ventana, usuario):
        self._guardar(
            usuario,
            [
                {"principal": True, "pestanas": []},
                {"principal": False, "pestanas": [{"modulo": "ModuloBorrado"}]},
            ],
        )
        window = abrir_ventana()

        window.restaurar_disposicion()
        _borrados_pendientes()

        assert window.ventanas.secundarias() == []

    def test_un_formato_desconocido_se_ignora_y_se_loguea(self, abrir_ventana, usuario, caplog):
        self._guardar(
            usuario, [{"principal": True, "pestanas": [{"modulo": "SystemModule"}]}], version=99
        )
        window = abrir_ventana()

        with caplog.at_level(logging.WARNING, logger=layout_store.__name__):
            window.restaurar_disposicion()

        assert window.tabs.count() == 1
        assert "formato 99" in caplog.text

    def test_basura_no_rompe_el_login(self, abrir_ventana, usuario):
        repository.save_ui_layout(
            usuario.id, layout_store.SCHEMA_VERSION, {"ventanas": "no es una lista"}
        )
        window = abrir_ventana()
        window.restaurar_disposicion()

        repository.save_ui_layout(
            usuario.id,
            layout_store.SCHEMA_VERSION,
            {
                "ventanas": [
                    42,
                    {"principal": True, "pestanas": [7, {"modulo": None}], "geometria": "x"},
                ]
            },
        )
        window.restaurar_disposicion()

        assert window.tabs.count() == 1

    def test_un_modulo_que_falla_al_restaurarse_no_rompe_el_login(
        self, abrir_ventana, usuario, monkeypatch, caplog
    ):
        """Lo que no anticipan los chequeos de forma (un set_state que
        explota) igual se loguea y el login sigue."""
        self._guardar(
            usuario,
            [
                {
                    "principal": True,
                    "pestanas": [{"modulo": "LiveViewModule", "estado": {"grilla": 1}}],
                }
            ],
        )

        def explota(_self, _estado):
            raise ValueError("estado roto")

        monkeypatch.setattr(LiveViewModule, "set_state", explota)
        window = abrir_ventana()

        window.restaurar_disposicion()

        assert "estado roto" in caplog.text

    def test_una_lectura_que_falla_no_rompe_el_login(self, abrir_ventana, monkeypatch, caplog):
        def explota(_user_id):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(layout_store.repository, "get_ui_layout", explota)
        window = abrir_ventana()

        window.restaurar_disposicion()

        assert "database is locked" in caplog.text


class TestPantallas:
    def test_un_monitor_que_ya_no_esta_cae_en_el_principal(self):
        principal = QGuiApplication.primaryScreen().availableGeometry()

        rect = layout_store.rect_en_pantalla(QRect(5000, 5000, 400, 300), "HDMI-que-no-esta")

        assert principal.contains(rect.center())
        assert rect.size().width() == 400

    def test_un_rect_afuera_de_su_pantalla_se_centra_en_ella(self):
        pantalla = QGuiApplication.primaryScreen()

        rect = layout_store.rect_en_pantalla(QRect(-9000, -9000, 400, 300), pantalla.name())

        assert pantalla.availableGeometry().contains(rect.center())

    def test_un_rect_en_su_pantalla_queda_igual(self):
        pantalla = QGuiApplication.primaryScreen()
        adentro = QRect(
            pantalla.availableGeometry().topLeft(), pantalla.availableGeometry().size() / 2
        )

        assert layout_store.rect_en_pantalla(adentro, pantalla.name()) == adentro

    def test_mas_grande_que_la_pantalla_se_achica(self):
        disponible = QGuiApplication.primaryScreen().availableGeometry()

        rect = layout_store.rect_en_pantalla(QRect(0, 0, 99999, 99999), "otra")

        assert rect.width() <= disponible.width() and rect.height() <= disponible.height()
