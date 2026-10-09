"""Fase 2 de la interfaz (2026-10-08): riel de navegacion, barra superior con
el estado global, Inicio como resumen operativo ("Requiere atención" y
camaras) y Alarmas con la lista primero."""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from shiboken6 import isValid

from aurea_vms.core import app_state, auth
from aurea_vms.models import repository
from aurea_vms.models.alarm_event import (
    STATUS_ACKNOWLEDGED,
    STATUS_INVESTIGATING,
    STATUS_NEW,
)
from aurea_vms.models.user import ROLE_ADMIN, ROLE_OPERATOR, User
from aurea_vms.ui import main_window as mw
from aurea_vms.ui.theme import rgba
from aurea_vms.ui.widgets.attention_list import AttentionList, time_ago
from aurea_vms.ui.widgets.camera_status_list import CameraStatusList
from aurea_vms.ui.widgets.nav_rail import HOME, NavRail
from aurea_vms.ui.widgets.top_bar import TopBar


def _login(role: str = ROLE_ADMIN) -> None:
    auth.current_user = User(username="ana perez", password_hash="h", salt="s", role=role)


def _incidente(device_id: int, status: str, seconds_ago: float = 60) -> int:
    return repository.add_alarm_event(
        device_id=device_id,
        timestamp=time.time() - seconds_ago,
        object_class="fichas_tras_no_va_mas",
        confidence=1.0,
        severity="critico",
        status=status,
    ).id


@pytest.fixture()
def casino(temp_db, monkeypatch):
    monkeypatch.setattr(app_state, "current_site_id", None)
    ruleta = repository.add_device(
        name="Ruleta RA-04", ip="10.0.0.1", rtsp_main_url="rtsp://r", status="online"
    )
    caja = repository.add_device(
        name="Caja", ip="10.0.0.2", rtsp_main_url="rtsp://c", status="offline"
    )
    ids = {
        "vieja": _incidente(ruleta.id, STATUS_NEW, 7200),
        "nueva": _incidente(ruleta.id, STATUS_NEW, 30),
        "investigando": _incidente(ruleta.id, STATUS_INVESTIGATING, 600),
        "reconocida": _incidente(ruleta.id, STATUS_ACKNOWLEDGED, 10),
    }
    return ruleta, caja, ids


@pytest.fixture()
def ventana(qtbot, casino):
    _login()
    window = mw.MainWindow()
    qtbot.addWidget(window)
    yield window
    if isValid(window):
        window.close()


class TestRiel:
    def test_marca_el_modulo_de_la_pestaña_activa(self, ventana):
        assert ventana.rail.active_index() == HOME

        ventana.open_module_by_index(mw.ALARMS_INDEX)
        assert ventana.rail.active_index() == mw.ALARMS_INDEX

        ventana.tabs.setCurrentWidget(ventana.launcher)
        assert ventana.rail.active_index() == HOME

    def test_un_clic_abre_el_modulo(self, ventana, qtbot):
        qtbot.mouseClick(ventana.rail.buttons[mw.ALARMS_INDEX], Qt.MouseButton.LeftButton)

        assert type(ventana.tabs.currentWidget()).__name__ == "AlarmModule"

    def test_inicio_vuelve_al_resumen(self, ventana):
        ventana.open_module_by_index(mw.ALARMS_INDEX)
        ventana._on_rail_requested(HOME)

        assert ventana.tabs.currentWidget() is ventana.launcher

    def test_la_insignia_cuenta_los_sin_reconocer(self, ventana):
        assert ventana.rail.buttons[mw.ALARMS_INDEX].badge == 2
        assert "2 sin reconocer" in ventana.rail.buttons[mw.ALARMS_INDEX].toolTip()

    def test_un_operador_solo_ve_lo_que_puede_abrir(self, qtbot, casino):
        _login(ROLE_OPERATOR)
        window = mw.MainWindow()
        qtbot.addWidget(window)
        labels = {button.label for button in window.rail.buttons.values()}

        assert labels == {"Inicio", "En vivo", "Inteligente", "Alarmas", "Eventos"}
        window.close()

    def test_dispositivos_y_no_camaras(self, ventana):
        """Ahi se cargan camaras, XVR y NVR (pedido de Nahuel, 2026-10-09)."""
        labels = [button.label for button in ventana.rail.buttons.values()]

        assert "Dispositivos" in labels and "Cámaras" not in labels

    def test_rotulos_y_activo_sin_ventana(self, qtbot):
        from aurea_vms.ui import icons

        rail = NavRail([(HOME, "Inicio", icons.icon_home)], [(3, "Sistema", icons.icon_system)])
        qtbot.addWidget(rail)
        pedidos: list[int] = []
        rail.requested.connect(pedidos.append)

        qtbot.mouseClick(rail.buttons[3], Qt.MouseButton.LeftButton)
        rail.set_active(3)

        assert pedidos == [3] and rail.active_index() == 3
        rail.set_badge(3, 0)
        assert rail.buttons[3].toolTip() == "Sistema"


class TestBarraSuperior:
    def test_estado_global(self, ventana):
        bar = ventana.top_bar
        assert bar.cameras_chip.text() == "Cámaras 1/2 · 1 desconectada"
        assert bar.cameras_chip.tone == "alert"
        assert bar.alerts_chip.text() == "2 sin reconocer"
        assert bar.alerts_chip.tone == "alert"

    def test_las_alertas_abren_alarmas_filtrada(self, ventana):
        ventana.top_bar.alerts_chip.click()

        modulo = ventana.tabs.currentWidget()
        assert type(modulo).__name__ == "AlarmModule"
        assert modulo.status_combo.currentData() == STATUS_NEW

    def test_sin_alertas(self, qtbot):
        bar = TopBar("ana perez", "Administrador")
        qtbot.addWidget(bar)

        bar.set_status(("3/3", "ok", ""), 0)

        assert bar.alerts_chip.text() == "Sin alertas pendientes"
        assert bar.alerts_chip.tone == "ok" and bar.cameras_chip.tone == "ok"

    def test_cerrar_sesion_desde_el_menu_del_usuario(self, ventana, monkeypatch):
        preguntas: list[str] = []
        monkeypatch.setattr(mw, "confirm", lambda _p, _t, text: preguntas.append(text) or False)

        ventana.top_bar.logout_requested.emit()

        assert preguntas and not ventana.logout_requested

    def test_el_sitio_sigue_en_la_barra(self, ventana):
        assert ventana.site_combo is ventana.top_bar.site_combo
        assert ventana.site_combo.itemText(0) == "Todos los sitios"


class TestInicio:
    def test_requiere_atencion_solo_lo_abierto_y_lo_mas_nuevo_primero(self, qtbot, casino):
        _ruleta, _caja, ids = casino
        lista = AttentionList()
        qtbot.addWidget(lista)

        assert [row.event_id for row in lista.rows] == [
            ids["nueva"],
            ids["investigando"],
            ids["vieja"],
        ]

    def test_un_clic_pide_el_incidente(self, qtbot, casino):
        lista = AttentionList()
        qtbot.addWidget(lista)
        pedidos: list[int] = []
        lista.incident_requested.connect(pedidos.append)

        qtbot.mouseClick(lista.rows[0], Qt.MouseButton.LeftButton)

        assert pedidos == [casino[2]["nueva"]]

    def test_un_cambio_de_estado_rearma_la_fila(self, qtbot, casino):
        lista = AttentionList()
        qtbot.addWidget(lista)
        repository.set_alarm_event_status(casino[2]["nueva"], STATUS_INVESTIGATING)

        lista.refresh()

        assert [row.status for row in lista.rows][0] == STATUS_INVESTIGATING

    def test_todo_en_orden(self, qtbot, temp_db, monkeypatch):
        monkeypatch.setattr(app_state, "current_site_id", None)
        lista = AttentionList()
        qtbot.addWidget(lista)

        assert lista.rows == [] and lista.empty_state is not None

    def test_camaras_caidas_primero(self, qtbot, casino):
        ruleta, caja, _ids = casino
        camaras = CameraStatusList()
        qtbot.addWidget(camaras)
        pedidos: list[int] = []
        camaras.camera_requested.connect(pedidos.append)

        assert [row.device_id for row in camaras.rows] == [caja.id, ruleta.id]
        qtbot.mouseClick(camaras.rows[1], Qt.MouseButton.LeftButton)
        assert pedidos == [ruleta.id]

    def test_ver_todo_lleva_a_alarmas(self, ventana):
        from PySide6.QtWidgets import QPushButton

        link = next(
            b for b in ventana.launcher.findChildren(QPushButton) if b.text().startswith("Ver todo")
        )
        link.click()

        assert type(ventana.tabs.currentWidget()).__name__ == "AlarmModule"

    def test_un_incidente_de_inicio_se_abre_en_alarmas(self, ventana, monkeypatch, casino):
        from aurea_vms.ui.modules.alarm_module import AlarmModule

        enfocados: list[int] = []
        monkeypatch.setattr(
            AlarmModule, "focus_event", lambda _s, event_id: enfocados.append(event_id) or True
        )
        ventana.launcher.attention.incident_requested.emit(casino[2]["nueva"])

        assert enfocados == [casino[2]["nueva"]]


def test_alarmas_lista_primero(qtbot, casino):
    from aurea_vms.ui.modules.alarm_module import AlarmModule

    _login()
    modulo = AlarmModule()
    qtbot.addWidget(modulo)
    modulo.resize(1600, 900)
    modulo.show()
    qtbot.waitExposed(modulo)

    lista, detalle = modulo.splitter.sizes()
    assert lista > detalle


def test_hace_cuanto():
    now = 1_000_000.0
    assert time_ago(now - 20, now) == "recién"
    assert time_ago(now - 600, now) == "hace 10 min"
    assert time_ago(now - 7200, now) == "hace 2 h"


def test_rgba_no_es_aarrggbb():
    """Qt lee "#f59e0b22" como #AARRGGBB (rojo oscuro), no ambar."""
    assert rgba("#f59e0b", 0.14) == "rgba(245, 158, 11, 0.14)"
    assert QColor("#f59e0b").red() == 245
