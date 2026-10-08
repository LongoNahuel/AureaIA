"""Clic en el popup de una alarma (2026-10-03): lleva al modulo Alarmas con
ese incidente en el detalle, aunque los filtros o la pagina lo escondieran."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from qfluentwidgets import HyperlinkButton, TransparentToolButton

import aurea_vms.ui.main_window as mw_module
import aurea_vms.ui.widgets.global_alert_popup as popup_module
from aurea_vms.core import auth
from aurea_vms.core.events import AlarmEvent
from aurea_vms.models import repository
from aurea_vms.models.alarm_event import STATUS_ACKNOWLEDGED, STATUS_NEW
from aurea_vms.models.user import ROLE_ADMIN, User
from aurea_vms.ui.main_window import ALARMS_INDEX, MODULES, MainWindow
from aurea_vms.ui.widgets.global_alert_popup import GlobalAlertPopupLayer


def _login() -> None:
    auth.current_user = User(username="admin", password_hash="h", salt="s", role=ROLE_ADMIN)


def _alarm_dto(alarm_event_id: int = 42) -> AlarmEvent:
    return AlarmEvent(
        alarm_event_id=alarm_event_id,
        rule_id=1,
        device_id=8,
        timestamp=time.time(),
        object_class="preparacion_consumo",
        confidence=1.0,
        severity="critico",
    )


class TestTarjeta:
    @pytest.fixture()
    def layer(self, qtbot):
        _login()
        parent = popup_module.QWidget()
        parent.resize(800, 600)
        qtbot.addWidget(parent)
        layer = GlobalAlertPopupLayer(parent)
        layer.show_alarm(_alarm_dto(), "SIM Casino · Consumo de sustancias")
        parent.show()
        yield layer  # con yield, `parent` (y sus hijos) vive hasta el final del test

    def test_clic_en_la_tarjeta_pide_abrir_el_incidente(self, layer, qtbot):
        (card,) = layer._cards
        with qtbot.waitSignal(layer.open_alarm_requested) as signal:
            qtbot.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(60, 40))

        assert signal.args == [42]
        assert layer._cards == []  # la tarjeta se cierra

    def test_reconocer_no_abre_alarmas(self, layer, qtbot, monkeypatch):
        statuses: list = []
        monkeypatch.setattr(
            popup_module.repository,
            "set_alarm_event_status",
            lambda event_id, status: statuses.append((event_id, status)),
        )
        opened: list = []
        layer.open_alarm_requested.connect(opened.append)
        (card,) = layer._cards

        card.findChild(HyperlinkButton).click()

        assert statuses == [(42, STATUS_ACKNOWLEDGED)] and opened == []

    def test_cerrar_no_abre_alarmas(self, layer, qtbot):
        opened: list = []
        layer.open_alarm_requested.connect(opened.append)
        (card,) = layer._cards

        card.findChild(TransparentToolButton).click()

        assert opened == [] and layer._cards == []

    def test_la_tarjeta_se_ve_como_un_enlace(self, layer):
        (card,) = layer._cards
        assert card.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert card.toolTip() == "Ver el incidente en Alarmas"


class TestModuloAlarmas:
    @pytest.fixture()
    def setup(self, qtbot, temp_db):
        from aurea_vms.ui.modules.alarm_module import AlarmModule

        _login()
        consumo = repository.add_device(name="Consumo", ip="10.0.0.8", rtsp_main_url="rtsp://c/8")
        ruleta = repository.add_device(name="Ruleta", ip="10.0.0.7", rtsp_main_url="rtsp://c/7")
        target = repository.add_alarm_event(
            device_id=consumo.id,
            timestamp=time.time() - 60,
            object_class="preparacion_consumo",
            confidence=1.0,
            severity="critico",
            status=STATUS_NEW,
        )
        for i in range(5):
            repository.add_alarm_event(
                device_id=ruleta.id,
                timestamp=time.time() - i,
                object_class="fichas_tras_no_va_mas",
                confidence=1.0,
                severity="critico",
                status=STATUS_ACKNOWLEDGED,
            )

        def create():
            module = AlarmModule()
            qtbot.addWidget(module)
            return module

        return SimpleNamespace(create=create, target=target, consumo=consumo, ruleta=ruleta)

    def test_muestra_el_incidente_en_el_detalle(self, setup):
        module = setup.create()

        assert module.focus_event(setup.target.id)

        assert module._selected_event().id == setup.target.id
        assert "Consumo" in module.detail_title.text() or "Consumo" in module.detail_info.text()

    def test_saca_los_filtros_que_lo_esconden(self, setup):
        module = setup.create()
        module.channel_combo.setCurrentIndex(module.channel_combo.findData(setup.ruleta.id))
        module.status_combo.setCurrentIndex(module.status_combo.findData(STATUS_ACKNOWLEDGED))
        assert module.table.rowCount() == 5

        assert module.focus_event(setup.target.id)

        assert module.channel_combo.currentData() is None
        assert module.status_combo.currentData() is None
        assert module._selected_event().id == setup.target.id

    def test_un_filtro_que_no_lo_esconde_queda(self, setup):
        module = setup.create()
        module.channel_combo.setCurrentIndex(module.channel_combo.findData(setup.consumo.id))

        assert module.focus_event(setup.target.id)

        assert module.channel_combo.currentData() == setup.consumo.id

    def test_carga_paginas_hasta_encontrarlo(self, setup, monkeypatch):
        import aurea_vms.ui.modules.alarm_module as alarm_module

        monkeypatch.setattr(alarm_module, "PAGE_SIZE", 2)
        module = setup.create()
        assert module.table.rowCount() == 2  # el incidente es el mas viejo

        assert module.focus_event(setup.target.id)

        assert module.table.rowCount() == 6
        assert module._selected_event().id == setup.target.id

    def test_un_incidente_que_no_existe(self, setup):
        assert not setup.create().focus_event(999_999)


class TestVentanaPrincipal:
    def test_alarmas_es_el_modulo_de_alarmas(self):
        assert MODULES[ALARMS_INDEX][0] == "Alarmas"

    def _window(self, module):
        """Como la ventana real, open_module_by_index devuelve el modulo (o
        None sin permiso): con varias ventanas, la pestaña actual de la
        principal puede no ser Alarmas."""
        opened: list[int] = []

        def abrir(index, destino=None):
            opened.append(index)
            return module

        return SimpleNamespace(open_module_by_index=abrir), opened

    def test_abre_alarmas_y_enfoca_el_incidente(self):
        focused: list[int] = []
        module = SimpleNamespace(focus_event=lambda event_id: focused.append(event_id) or True)
        window, opened = self._window(module)

        MainWindow._on_open_alarm_requested(window, 42)

        assert opened == [ALARMS_INDEX] and focused == [42]

    def test_avisa_si_no_lo_encuentra(self, monkeypatch):
        warnings: list[str] = []
        monkeypatch.setattr(mw_module, "warn", lambda _p, _t, text: warnings.append(text))
        window, _opened = self._window(SimpleNamespace(focus_event=lambda _id: False))

        MainWindow._on_open_alarm_requested(window, 42)

        assert "sitio" in warnings[0]

    def test_sin_permiso_no_hace_nada_mas(self, monkeypatch):
        # Sin permiso, open_module_by_index ya avisa y devuelve None.
        warnings: list[str] = []
        monkeypatch.setattr(mw_module, "warn", lambda _p, _t, text: warnings.append(text))
        window, opened = self._window(None)

        MainWindow._on_open_alarm_requested(window, 42)

        assert opened == [ALARMS_INDEX] and warnings == []
