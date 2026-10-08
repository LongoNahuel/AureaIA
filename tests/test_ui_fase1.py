"""Fase 1 de la interfaz (2026-10-08): las mismas cifras con la misma tarjeta
en todas las pantallas, "sin reconocer" con un solo significado, severidad
como punto, sin fondos decorativos ni secciones "Próximamente"."""

from __future__ import annotations

import time

import pytest

from aurea_vms.core import app_state, auth
from aurea_vms.models import repository
from aurea_vms.models.alarm_event import (
    STATUS_ACKNOWLEDGED,
    STATUS_INVESTIGATING,
    STATUS_NEW,
    STATUS_RESOLVED,
)
from aurea_vms.models.user import User
from aurea_vms.ui.theme import TONES
from aurea_vms.ui.widgets.dashboard_panel import DashboardPanel, alarm_summary, cameras_summary
from aurea_vms.ui.widgets.kpi_tile import KpiTile


def _login() -> None:
    auth.current_user = User(username="admin", password_hash="h", salt="s", role="admin")


def _incidente(device_id: int, status: str, severity: str = "critico") -> None:
    repository.add_alarm_event(
        device_id=device_id,
        timestamp=time.time(),
        object_class="consumo_sustancias",
        confidence=1.0,
        severity=severity,
        status=status,
    )


@pytest.fixture()
def sala(temp_db, monkeypatch):
    """Dos sitios: en la sala, una camara en linea y otra caida, con un
    incidente en cada estado; en el anexo, uno nuevo que no es de la sala."""
    monkeypatch.setattr(app_state, "current_site_id", None)
    sala = repository.add_site(name="Sala")
    anexo = repository.add_site(name="Anexo")
    zona_sala = repository.add_zone(name="Mesas", site_id=sala.id)
    zona_anexo = repository.add_zone(name="Barra", site_id=anexo.id)
    ruleta = repository.add_device(
        name="Ruleta",
        ip="10.0.0.1",
        rtsp_main_url="rtsp://r",
        zone_id=zona_sala.id,
        status="online",
    )
    repository.add_device(
        name="Caja", ip="10.0.0.2", rtsp_main_url="rtsp://c", zone_id=zona_sala.id, status="offline"
    )
    barra = repository.add_device(
        name="Barra",
        ip="10.0.0.3",
        rtsp_main_url="rtsp://b",
        zone_id=zona_anexo.id,
        status="online",
    )
    for status in (STATUS_NEW, STATUS_NEW, STATUS_ACKNOWLEDGED, STATUS_INVESTIGATING):
        _incidente(ruleta.id, status)
    _incidente(ruleta.id, STATUS_RESOLVED)
    _incidente(barra.id, STATUS_NEW)
    return sala, anexo


class TestCifras:
    def test_sin_reconocer_son_solo_los_nuevos_y_respetan_el_sitio(self, sala):
        assert alarm_summary(sala[0].id) == (2, 1)
        assert alarm_summary(None) == (3, 1)  # todos los sitios

    def test_camaras_en_linea(self, sala):
        assert cameras_summary(sala[0].id) == ("1/2", "alert", "1 desconectada")
        assert cameras_summary(sala[1].id) == ("1/1", "ok", "")

    def test_inicio_sigue_el_sitio_elegido(self, qtbot, sala, monkeypatch):
        panel = DashboardPanel()
        qtbot.addWidget(panel)
        assert panel.unacknowledged_tile.value_label.text() == "3"

        monkeypatch.setattr(app_state, "current_site_id", sala[0].id)
        panel.refresh()

        assert panel.unacknowledged_tile.value_label.text() == "2"
        assert panel.unacknowledged_tile.tone == "alert"
        assert panel.investigating_tile.tone == "warn"
        assert panel.cameras_tile.value_label.text() == "1/2"

    def test_sin_nada_pendiente_queda_en_verde(self, qtbot, temp_db, monkeypatch):
        monkeypatch.setattr(app_state, "current_site_id", None)
        panel = DashboardPanel()
        qtbot.addWidget(panel)

        assert panel.unacknowledged_tile.value_label.text() == "0"
        assert panel.unacknowledged_tile.tone == "ok"


class TestKpiTile:
    def test_el_tono_pinta_solo_el_numero(self, qtbot):
        tile = KpiTile("Sin reconocer")
        qtbot.addWidget(tile)

        tile.set_value(4, "alert", "de 9 abiertas")

        assert tile.tone == "alert"
        assert TONES["alert"] in tile.styleSheet() and "border-left" in tile.styleSheet()
        assert tile.detail_label.text() == "de 9 abiertas"
        assert not tile.detail_label.isHidden()

    def test_el_estilo_no_se_derrama_en_los_textos(self, qtbot):
        """Los selectores genericos (QWidget/QFrame) le ponian borde a los
        QLabel de adentro: cajas dentro de cajas."""
        tile = KpiTile("Cámaras")
        qtbot.addWidget(tile)

        assert "QFrame {" not in tile.styleSheet() and "QWidget {" not in tile.styleSheet()
        assert tile.detail_label.isHidden()


class TestTablas:
    def test_el_dashboard_dice_el_estado_de_verdad(self, qtbot, sala):
        from aurea_vms.ui.modules.event_dashboard import EventDashboardModule

        modulo = EventDashboardModule()
        qtbot.addWidget(modulo)

        estados = {modulo.table.item(r, 5).text() for r in range(modulo.table.rowCount())}
        assert estados == {"Sin reconocer", "Reconocida", "En investigación", "Resuelta"}
        assert modulo.active_card.value_label.text() == "3"

    def test_la_severidad_es_un_punto_sin_fondo(self, qtbot, sala):
        from aurea_vms.ui.modules.event_dashboard import EventDashboardModule

        modulo = EventDashboardModule()
        qtbot.addWidget(modulo)
        severidad = modulo.table.item(0, 4)

        assert severidad.text() == "Crítico"
        assert not severidad.icon().isNull()
        assert severidad.background().style().name == "NoBrush"

    def test_alarmas_tambien(self, qtbot, sala):
        from aurea_vms.ui.modules.alarm_module import AlarmModule

        _login()
        modulo = AlarmModule()
        qtbot.addWidget(modulo)
        severidad = modulo.table.item(0, 3)

        assert not severidad.icon().isNull()
        assert severidad.background().style().name == "NoBrush"
        assert "Sin reconocer" in {
            modulo.table.item(r, 4).text() for r in range(modulo.table.rowCount())
        }


class TestPantallasLimpias:
    def test_inicio_sin_video_de_fondo(self, qtbot, temp_db):
        from aurea_vms.ui import launcher_page
        from aurea_vms.ui.main_window import CATEGORIES, MODULES

        _login()
        page = launcher_page.LauncherPage(MODULES, CATEGORIES)
        qtbot.addWidget(page)

        assert not hasattr(launcher_page, "VideoBackground")
        assert page.findChild(DashboardPanel) is not None

    def test_vista_inteligente_sin_fondo_y_con_las_mismas_tarjetas(self, qtbot, temp_db):
        from aurea_vms.ui.modules import live_view

        _login()
        vista = live_view.LiveViewModule(smart_only=True)
        qtbot.addWidget(vista)

        assert not hasattr(live_view, "BrandedBackground")
        titulos = [t.title_label.text() for t in vista.findChildren(KpiTile)]
        assert titulos == ["Cámaras en línea", "Sin reconocer", "Analíticas activas"]

    def test_sistema_sin_proximamente(self, qtbot, temp_db):
        from aurea_vms.ui.modules.system_module import SystemModule

        _login()
        modulo = SystemModule()
        qtbot.addWidget(modulo)
        hojas = {
            modulo.nav_tree.topLevelItem(g).child(h).text(0)
            for g in range(modulo.nav_tree.topLevelItemCount())
            for h in range(modulo.nav_tree.topLevelItem(g).childCount())
        }

        assert {"Alarma", "Servicio", "Visualización de atributos"}.isdisjoint(hojas)
        assert {"Video", "Grabando", "PTZ"} <= hojas


def test_la_marca_solo_en_el_modo_inteligente(qtbot, monkeypatch):
    from types import SimpleNamespace

    from aurea_vms.ui.widgets.video_tile import VideoTile

    tile = VideoTile(0)
    qtbot.addWidget(tile)
    tile._device = SimpleNamespace(name="Cam", id=1)
    monkeypatch.setattr(tile, "_branding_enabled", lambda: True)

    assert tile._escena().marca is False
    tile._intelligent_mode = True
    assert tile._escena().marca is True


def test_vista_inteligente_lleva_la_marca_y_vista_en_vivo_no(qtbot, temp_db):
    from aurea_vms.ui.modules.live_view import LiveViewModule

    _login()
    inteligente, en_vivo = LiveViewModule(smart_only=True), LiveViewModule()
    qtbot.addWidget(inteligente)
    qtbot.addWidget(en_vivo)

    assert all(tile._intelligent_mode for tile in inteligente.tiles)
    assert not any(tile._intelligent_mode for tile in en_vivo.tiles)


def test_un_valor_de_texto_va_a_tamaño_de_titulo(qtbot):
    from aurea_vms.ui.theme import FONT_PX

    tile = KpiTile("Analítica dominante")
    qtbot.addWidget(tile)

    tile.set_value(47)
    assert f"font-size: {FONT_PX['kpi']}px" in tile.styleSheet()
    tile.set_value("Incidentes en casinos")
    assert f"font-size: {FONT_PX['title']}px" in tile.styleSheet()
