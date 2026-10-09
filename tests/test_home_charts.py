"""Graficos de Inicio (2026-10-09): los numeros (ui/home_stats.py) y los
graficos (widgets/home_charts.py): torta, lineas, barras y leyenda."""

from __future__ import annotations

import datetime as dt
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPointF

from aurea_vms.core import app_state, auth
from aurea_vms.models import repository
from aurea_vms.models.alarm_event import (
    STATUS_ACKNOWLEDGED,
    STATUS_INVESTIGATING,
    STATUS_NEW,
    STATUS_RESOLVED,
)
from aurea_vms.models.user import ROLE_ADMIN, User
from aurea_vms.ui import home_stats
from aurea_vms.ui.theme import TONES
from aurea_vms.ui.widgets.home_charts import BarList, ChartLegend, DonutChart, LineChart, nice_max

NOW = dt.datetime(2026, 10, 9, 15, 30).timestamp()
DAY = 86400


def _event(device_id: int, kind: str, seconds_ago: float, status: str = STATUS_ACKNOWLEDGED):
    repository.add_alarm_event(
        device_id=device_id,
        timestamp=NOW - seconds_ago,
        object_class=kind,
        confidence=1.0,
        severity="critico",
        status=status,
    )


@pytest.fixture()
def datos(temp_db, monkeypatch):
    monkeypatch.setattr(app_state, "current_site_id", None)
    sala = repository.add_site(name="Sala")
    zona = repository.add_zone(name="Mesas", site_id=sala.id)
    ruleta = repository.add_device(
        name="Ruleta", ip="10.0.0.1", rtsp_main_url="rtsp://r", zone_id=zona.id
    )
    barra = repository.add_device(name="Barra", ip="10.0.0.2", rtsp_main_url="rtsp://b")
    # Hoy: 3 fichas (1 nueva), 1 consumo en investigacion; ayer: 2 patadas;
    # hace 3 dias: 1 preparacion resuelta; hace 10 dias: 1 golpe (fuera de 7 dias).
    _event(ruleta.id, "fichas_tras_no_va_mas", 600, STATUS_NEW)
    _event(ruleta.id, "fichas_tras_no_va_mas", 1200)
    _event(ruleta.id, "fichas_tras_no_va_mas", 3 * 3600)
    _event(barra.id, "consumo_sustancias", 1800, STATUS_INVESTIGATING)
    _event(barra.id, "patada_monitor", DAY)
    _event(barra.id, "patada_monitor", DAY + 60)
    _event(barra.id, "preparacion_consumo", 3 * DAY, STATUS_RESOLVED)
    _event(barra.id, "golpe_monitor", 10 * DAY)
    return sala, ruleta, barra


class TestNumeros:
    def test_siete_dias_por_dia(self, datos):
        stats = home_stats.compute(None, "7d", now=NOW)

        assert stats.labels == ["03/10", "04/10", "05/10", "06/10", "07/10", "08/10", "09/10"]
        assert stats.total == 7 and stats.open_total == 2
        assert stats.unit == "día" and stats.range_label == "7 días"

    def test_las_lineas_son_las_tres_que_mas_pesan_y_el_resto_va_a_otros(self, datos):
        stats = home_stats.compute(None, "7d", now=NOW)
        lines = {label: values for label, values, _color in stats.series}

        assert lines["Fichas tras el no va más"][-1] == 3
        assert lines["Patada al monitor"][-2] == 2
        # Consumo (1) y preparacion (1) empatan: entra uno, el otro va a "Otros".
        assert sum(lines[home_stats.OTHER_LABEL]) == 1
        assert sum(sum(values) for values in lines.values()) == 7

    def test_el_color_sigue_al_tipo_no_al_ranking(self, datos):
        week = home_stats.compute(None, "7d", now=NOW)
        month = home_stats.compute(None, "30d", now=NOW)

        colors = {label: color for label, _v, color in week.by_type}
        assert colors["Fichas tras el no va más"] == home_stats.TYPE_COLORS["fichas_tras_no_va_mas"]
        assert colors["Patada al monitor"] == home_stats.TYPE_COLORS["patada_monitor"]
        assert {label: c for label, _v, c in month.by_type}["Patada al monitor"] == (
            colors["Patada al monitor"]
        )

    def test_estados_en_orden_y_con_su_color(self, datos):
        stats = home_stats.compute(None, "7d", now=NOW)

        assert [(label, value) for label, value, _c in stats.by_status] == [
            ("Sin reconocer", 1),
            ("En investigación", 1),
            ("Reconocida", 4),
            ("Resuelta", 1),
        ]
        assert stats.by_status[0][2] == TONES["alert"]
        assert stats.by_status[3][2] == TONES["ok"]

    def test_24_horas_por_hora(self, datos):
        stats = home_stats.compute(None, "24h", now=NOW)

        assert len(stats.labels) == 24 and stats.labels[-1] == "15:00"
        assert stats.total == 4  # las patadas de ayer quedan afuera

    def test_camaras_y_sitio(self, datos):
        sala, ruleta, _barra = datos
        stats = home_stats.compute(sala.id, "30d", now=NOW)

        assert stats.by_camera == [("Ruleta", 3)]
        assert stats.total == 3

    def test_mas_tipos_que_el_tope_se_pliegan(self, datos):
        _sala, ruleta, _barra = datos
        _event(ruleta.id, "fichas_movidas", 100)
        stats = home_stats.compute(None, "30d", now=NOW)

        assert len(stats.by_type) == home_stats.MAX_TYPES + 1
        assert stats.by_type[-1][0] == home_stats.OTHER_LABEL
        assert sum(value for _l, value, _c in stats.by_type) == stats.total


class TestGraficos:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0, (4.0, 1.0)), (3, (3, 1)), (22, (25, 5)), (47, (50, 10)), (120, (125, 25))],
    )
    def test_tope_del_eje_justo_y_redondo(self, value, expected):
        assert nice_max(value) == expected

    def test_torta_segmento_bajo_el_mouse(self, qtbot):
        donut = DonutChart()
        qtbot.addWidget(donut)
        donut.resize(200, 200)
        donut.set_data([("A", 1, "#3987e5"), ("B", 1, "#d95926")], "2")

        center, radius = donut._ring()
        ring = radius - donut.THICKNESS / 2
        assert donut.segment_at(QPointF(center.x() + ring, center.y())) == 0  # a las 3
        assert donut.segment_at(QPointF(center.x() - ring, center.y())) == 1  # a las 9
        assert donut.segment_at(center) is None  # el agujero

    def test_torta_sin_datos(self, qtbot):
        donut = DonutChart()
        qtbot.addWidget(donut)
        donut.set_data([("A", 0, "#3987e5")], "0")

        assert donut.total == 0 and donut.segments == []
        donut.grab()  # pinta el anillo vacio sin romper

    def test_linea_engancha_el_punto_mas_cercano(self, qtbot):
        line = LineChart()
        qtbot.addWidget(line)
        line.resize(600, 240)
        line.set_data(
            ["a", "b", "c"], [("Fichas", [1, 4, 2], "#3987e5"), ("Otros", [0, 1, 0], "#6b7280")]
        )
        plot = line._plot()

        assert line.index_at(plot.left() + plot.width() * 0.45) == 1
        assert line.tooltip_text(1) == "b\n4  ·  Fichas\n1  ·  Otros"
        assert line.peak() == 4
        line.grab()

    def test_barras_fila_bajo_el_mouse(self, qtbot):
        bars = BarList("#3b82f6")
        qtbot.addWidget(bars)
        bars.resize(400, 200)
        bars.set_data([("Ruleta", 3), ("Barra", 1)])

        assert bars.row_at(BarList.ROW_HEIGHT * 1.5) == 1
        assert bars.row_at(BarList.ROW_HEIGHT * 5) is None
        bars.grab()

    def test_leyenda_con_porcentaje_y_en_fila(self, qtbot):
        column = ChartLegend()
        row = ChartLegend(marker="line", inline=True)
        qtbot.addWidget(column)
        qtbot.addWidget(row)

        column.set_items([("A", 3, "#3987e5"), ("B", 1, "#d95926")], percent_of=4)
        row.set_items([("A", 3, "#3987e5")])

        assert column.rows == [("A", "3"), ("B", "1")]
        assert row.rows == [("A", "3")]


def test_inicio_cambia_de_periodo(qtbot, datos, monkeypatch):
    from aurea_vms.ui import main_window as mw
    from aurea_vms.ui.launcher_page import LauncherPage

    # Solo el reloj de home_stats (no el modulo time de todo el proceso).
    monkeypatch.setattr(home_stats, "time", SimpleNamespace(time=lambda: NOW))
    auth.current_user = User(username="admin", password_hash="h", salt="s", role=ROLE_ADMIN)
    page = LauncherPage(mw.MODULES, mw.CATEGORIES)
    qtbot.addWidget(page)

    assert page.types_donut.center_value == "7"
    assert "7 días" in page.timeline_card.subtitle.text()

    page.set_range("24h")

    assert page.types_donut.center_value == "4"
    assert "24 h" in page.timeline_card.subtitle.text()
    assert page.period.currentRouteKey() == "24h"
    assert page.status_donut.center_value == "2"
    assert time.time() > NOW  # el resto del proceso sigue con el reloj real
