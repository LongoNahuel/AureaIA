"""Incidentes en casinos, modo ruleta (2026-09-30): la rueda se ubica sola,
se mide su velocidad y se vigilan las zonas de fichas. Se prueba con una
rueda sintetica (37 bolsillos rojos y negros, el cero verde) que gira a una
velocidad conocida; la calibracion contra video real esta en el docstring de
core/analytics/roulette_analyzer.py."""

from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from aurea_vms.core.analytics.registry import create_analyzer
from aurea_vms.core.analytics.roulette_analyzer import (
    ANGLE_BINS,
    POCKET_DEG,
    ChipZone,
    RouletteAnalyzer,
    WheelEllipse,
    WheelSpeedMeter,
    detect_wheel,
    rectify_matrix,
    refine_shift,
)

SIZE = (480, 640)  # alto, ancho
CENTER = (320, 240)
RADIUS = 150
FPS = 5.0


def _wheel_frame(angle_deg: float, center=CENTER, radius=RADIUS) -> np.ndarray:
    """Fondo gris quieto y un plato de 37 bolsillos girado `angle_deg`
    (positivo = horario en la imagen)."""
    frame = np.full((*SIZE, 3), 90, dtype=np.uint8)
    cv2.circle(frame, center, radius + 25, (40, 60, 90), -1)  # tazon de madera, quieto
    for pocket in range(37):
        start = angle_deg + pocket * POCKET_DEG
        color = (60, 170, 40) if pocket == 0 else ((30, 30, 200) if pocket % 2 else (25, 25, 25))
        cv2.ellipse(frame, center, (radius, radius), 0, start, start + POCKET_DEG, color, -1)
        # separador blanco y "numero" para que el perfil no sea puramente periodico
        edge = np.deg2rad(start)
        tip = (int(center[0] + radius * np.cos(edge)), int(center[1] + radius * np.sin(edge)))
        cv2.line(frame, center, tip, (235, 235, 235), 1)
        mid = np.deg2rad(start + POCKET_DEG / 2)
        text_r = radius * 0.85
        spot = (int(center[0] + text_r * np.cos(mid)), int(center[1] + text_r * np.sin(mid)))
        cv2.circle(frame, spot, 1 + pocket % 3, (230, 230, 230), -1)
    cv2.circle(frame, center, int(radius * 0.45), (15, 15, 15), -1)  # cono
    return frame


def _ellipse() -> WheelEllipse:
    return WheelEllipse(CENTER[0], CENTER[1], 2 * RADIUS, 2 * RADIUS, 0.0)


class TestGeometria:
    def test_la_elipse_va_y_vuelve_como_lista(self):
        wheel = WheelEllipse(935.0, 1007.5, 433.9, 512.1, 92.9)
        assert WheelEllipse.from_list(wheel.as_list()) == wheel
        assert WheelEllipse.from_list([1, 2]) is None

    def test_el_rectificado_lleva_la_elipse_a_un_circulo(self):
        wheel = WheelEllipse(500.0, 400.0, 300.0, 200.0, 30.0)
        matrix = rectify_matrix(wheel, radius=100)
        box = cv2.ellipse2Poly((500, 400), (150, 100), 30, 0, 360, 10).astype(np.float64)
        mapped = np.c_[box, np.ones(len(box))] @ matrix.T
        distances = np.linalg.norm(mapped - (100, 100), axis=1)
        assert np.allclose(distances, 100, atol=2.0)

    def test_encuentra_la_rueda_que_gira_y_no_el_fondo(self):
        frames = [_wheel_frame(26.0 * i) for i in range(15)]
        wheel = detect_wheel(frames)

        assert wheel is not None
        assert abs(wheel.cx - CENTER[0]) < 6 and abs(wheel.cy - CENTER[1]) < 6
        assert abs(wheel.width / 2 - RADIUS) < 10

    def test_una_escena_quieta_no_tiene_rueda(self):
        assert detect_wheel([_wheel_frame(0.0)] * 15) is None


class TestVelocidad:
    def test_la_correlacion_sola_se_confunde_de_bolsillo_y_el_cero_la_corrige(self):
        """A 5 fps el plato gira ~26 grados entre muestras: la correlacion de
        bolsillos repetidos cada 9,73 grados tiene picos a +3,2 y -26."""
        meter = WheelSpeedMeter(_ellipse())
        before = meter.ring(_wheel_frame(0.0)).mean(axis=1).mean(axis=1)
        after = meter.ring(_wheel_frame(-26.0)).mean(axis=1).mean(axis=1)
        profile = lambda values: (values - values.mean()) / values.std()  # noqa: E731

        refined = refine_shift(profile(before), profile(after), coarse_deg=-25.0)
        assert refined == pytest.approx(-26.0, abs=1.0)

    @pytest.mark.parametrize("speed", [130.0, -160.0])
    def test_mide_grados_por_segundo_y_sentido(self, speed):
        meter = WheelSpeedMeter(_ellipse())
        reading = None
        for i in range(12):
            reading = meter.update(_wheel_frame(speed / FPS * i), i / FPS)

        assert reading == pytest.approx(speed, rel=0.06)

    def test_sin_ver_el_cero_sigue_con_la_ultima_velocidad(self):
        meter = WheelSpeedMeter(_ellipse())
        for i in range(6):
            meter.update(_wheel_frame(26.0 * i), i / FPS)
        hidden = _wheel_frame(26.0 * 6)
        hidden[hidden[..., 1] > 150] = (25, 25, 25)  # tapa el cero verde

        assert meter.update(hidden, 6 / FPS) == pytest.approx(130.0, rel=0.08)

    def test_la_tira_tiene_una_columna_por_medio_grado(self):
        assert WheelSpeedMeter(_ellipse()).ring(_wheel_frame(0.0)).shape[0] == ANGLE_BINS


def _table(chips: list[tuple[int, int]], hand: tuple[int, int] | None = None) -> np.ndarray:
    frame = np.full((*SIZE, 3), (200, 215, 225), dtype=np.uint8)  # paño crema
    for x, y in chips:
        cv2.circle(frame, (x, y), 9, (40, 40, 180), -1)
    if hand is not None:
        cv2.ellipse(frame, hand, (40, 22), 0, 0, 360, (90, 130, 190), -1)
    return frame


class TestZonasDeFichas:
    ZONE = (100, 100, 300, 200)

    def _run(self, frames: list[np.ndarray]) -> list[int]:
        zone = ChipZone(self.ZONE)
        return [i for i, frame in enumerate(frames) if zone.update(frame, i / FPS) is not None]

    def test_una_ficha_nueva_se_avisa_cuando_la_zona_vuelve_a_quedar_quieta(self):
        quiet = [_table([(150, 150)])] * 6
        hand = [_table([(150, 150)], hand=(200 + 15 * i, 200)) for i in range(4)]
        after = [_table([(150, 150), (260, 220)])] * 10

        changes = self._run(quiet + hand + after)

        assert len(changes) == 1
        assert changes[0] >= len(quiet) + len(hand) + 5  # tras 1 s quieta

    def test_una_mano_que_pasa_sin_dejar_fichas_no_avisa(self):
        quiet = [_table([(150, 150)])] * 6
        hand = [_table([(150, 150)], hand=(200 + 15 * i, 200)) for i in range(4)]

        assert self._run(quiet + hand + quiet * 2) == []


class TestAnalizador:
    def test_ubica_la_rueda_sola_y_mide(self):
        analyzer = RouletteAnalyzer(zones=[(0, 0, 100, 100)])
        metrics = None
        for i in range(30):
            metrics = analyzer.process_frame(_wheel_frame(26.0 * i), i / FPS).metrics

        assert metrics["modo"] == "ruleta"
        assert metrics["rueda"] is not None
        assert metrics["velocidad_deg_s"] == pytest.approx(130.0, rel=0.08)
        assert metrics["rpm"] == pytest.approx(130.0 / 6, rel=0.08)
        assert metrics["sentido"] == "horario"
        assert metrics["zonas"][0]["estado"] == "normal"

    def test_modo_ruleta_en_el_registro(self):
        config = SimpleNamespace(
            analyzer_name="monitor_tamper",
            params={
                "modo": "ruleta",
                "zones": [[10, 10, 50, 50]],
                "rueda_zona": [100, 100, 300, 300],
                "rueda": [250.0, 250.0, 300.0, 300.0, 0.0],
            },
            roi_x=None,
            roi_y=None,
            roi_w=None,
            roi_h=None,
        )
        analyzer = create_analyzer(config)

        assert isinstance(analyzer, RouletteAnalyzer)
        assert analyzer.wheel == WheelEllipse(250.0, 250.0, 300.0, 300.0, 0.0)

    def test_blackjack_todavia_no(self):
        config = SimpleNamespace(
            analyzer_name="monitor_tamper",
            params={"modo": "blackjack", "zones": [[10, 10, 50, 50]]},
            roi_x=None,
            roi_y=None,
            roi_w=None,
            roi_h=None,
        )
        with pytest.raises(ValueError, match="BlackJack"):
            create_analyzer(config)


class TestDialogo:
    @pytest.fixture()
    def dialog(self, qtbot, temp_db):
        from aurea_vms.models import repository
        from aurea_vms.ui.dialogs.monitor_tamper_config_dialog import MonitorTamperConfigDialog

        device = repository.add_device(name="Mesa", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
        repository.upsert_analytics_config(
            device.id,
            "monitor_tamper",
            params={
                "modo": "ruleta",
                "rueda_zona": [700, 800, 500, 500],
                "zones": [[728, 140, 368, 512]],
            },
        )
        widget = MonitorTamperConfigDialog(device)
        qtbot.addWidget(widget)
        return widget

    def test_la_rueda_es_el_primer_rectangulo(self, dialog):
        assert dialog.selector_widget.get_rects() == [(700, 800, 500, 500), (728, 140, 368, 512)]
        params = dialog.zone_params()
        assert params["rueda_zona"] == [700, 800, 500, 500]
        assert params["zones"] == [[728, 140, 368, 512]]

    def test_la_deteccion_propone_rueda_y_grilla(self, dialog, monkeypatch):
        import aurea_vms.ui.dialogs.monitor_tamper_config_dialog as dialog_module

        wheel = WheelEllipse(935.0, 1007.0, 434.0, 512.0, 92.0)
        monkeypatch.setattr(dialog_module, "detect_wheel", lambda _frames: wheel)
        monkeypatch.setattr(dialog_module, "detect_layout_grid", lambda _f: (728, 140, 368, 512))

        dialog._finish_detection([np.zeros((10, 10, 3), np.uint8)])

        rects = dialog.selector_widget.get_rects()
        assert rects[0] == wheel.bounding_rect()
        assert dialog.zone_params()["rueda"] == wheel.as_list()

    def test_las_manos_se_marcan_salvo_que_se_apaguen(self, dialog):
        assert dialog.build_params()["manos"] is True

        dialog.table_hands_check.setChecked(False)

        assert dialog.build_params()["manos"] is False

    def test_el_no_va_mas_se_configura(self, dialog):
        assert dialog.build_params()["no_va_mas_deg_s"] == 200.0

        dialog.no_more_bets_spin.setValue(250)

        assert dialog.build_params()["no_va_mas_deg_s"] == 250.0

    def test_blackjack_no_se_puede_guardar(self, dialog):
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("blackjack"))
        assert "BlackJack" in dialog.validate()
