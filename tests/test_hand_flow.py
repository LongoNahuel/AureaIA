"""Manos cuadro a cuadro (2026-09-30): entre dos detecciones completas, cada
punto de la mano se sigue con flujo optico, para que las marcas acompañen al
video a los fps del stream (hasta 60) aunque la deteccion corra a 5 por
segundo."""

from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from aurea_vms.core.analytics.hand_flow import flow_gray, track_people
from aurea_vms.core.analytics.pose_backend import LEFT_WRIST, Pose
from aurea_vms.core.analytics.roulette_analyzer import RouletteAnalyzer
from aurea_vms.core.analytics.table_hands import Hand, Person
from aurea_vms.core.events import Detection

SIZE = (480, 640)
PATCH = 60


def _texture() -> np.ndarray:
    rng = np.random.default_rng(7)
    patch = rng.integers(0, 255, (PATCH, PATCH, 3), dtype=np.uint8)
    return cv2.GaussianBlur(patch, (3, 3), 0)


TEXTURE = _texture()


def _frame(x: int, y: int) -> np.ndarray:
    """Fondo liso con un parche con textura (la mano) con su esquina en (x, y)."""
    frame = np.full((*SIZE, 3), 120, np.uint8)
    frame[y : y + PATCH, x : x + PATCH] = TEXTURE
    return frame


def _person(points) -> Person:
    hand = Hand(points[0][0], points[0][1], 0.9, "izq", tuple(points[1:]))
    return Person((0, 0, 200, 300), [hand], role="jugador", track=1)


class TestFlujo:
    def test_los_puntos_acompañan_a_la_mano(self):
        points = [(130.0, 130.0), (115.0, 110.0), (145.0, 112.0), None, (125.0, 145.0)]
        people = [_person(points)]
        previous = flow_gray(_frame(100, 100))

        tracked = track_people(previous, flow_gray(_frame(112, 106)), people)

        hand = tracked[0].hands[0]
        assert (hand.x, hand.y) == pytest.approx((142.0, 136.0), abs=1.5)
        moved = [p for p in hand.points if p is not None]
        expected = [(127.0, 116.0), (157.0, 118.0), (137.0, 151.0)]
        assert np.allclose(moved, expected, atol=1.5)
        assert hand.points[2] is None  # el que no se veia sigue sin verse
        assert people[0].hands[0].x == 130.0  # no toca la original

    def test_un_punto_que_no_se_puede_seguir_va_con_su_mano(self):
        # El ultimo punto cae en el fondo liso: el flujo no lo encuentra.
        points = [(130.0, 130.0), (115.0, 110.0), (145.0, 112.0), (400.0, 400.0)]
        tracked = track_people(
            flow_gray(_frame(100, 100)), flow_gray(_frame(110, 100)), [_person(points)]
        )

        assert tracked[0].hands[0].points[-1] == pytest.approx((410.0, 400.0), abs=1.5)

    def test_un_dedo_sobre_la_rueda_no_se_va_girando_con_ella(self):
        # La mano (parche de la izquierda) se corre +10 en x; el dedo apoyado
        # en la rueda (otro parche con textura) ve el plato correrse -10.
        wheel = np.ascontiguousarray(TEXTURE[:, ::-1])

        def frame(hand_x: int, wheel_x: int) -> np.ndarray:
            image = _frame(hand_x, 100)
            image[200 : 200 + PATCH, wheel_x : wheel_x + PATCH] = wheel
            return image

        points = [(130.0, 130.0), (115.0, 110.0), (145.0, 112.0), (125.0, 145.0), (330.0, 230.0)]
        tracked = track_people(
            flow_gray(frame(100, 300)), flow_gray(frame(110, 290)), [_person(points)]
        )

        finger = tracked[0].hands[0].points[-1]
        assert finger[0] >= 330.0 + 10.0 - 6.0 - 1.0  # con la mano, con un desvio acotado

    def test_sin_movimiento_no_se_mueve(self):
        gray = flow_gray(_frame(100, 100))
        people = [_person([(130.0, 130.0), (115.0, 110.0)])]

        tracked = track_people(gray, gray, people)

        assert (tracked[0].hands[0].x, tracked[0].hands[0].y) == pytest.approx((130.0, 130.0))


class _Detector:
    def detect(self, _frame, _classes, _min_confidence):
        return [Detection("person", 0.9, (60, 60, 160, 200))]

    def close(self):
        pass


class _Estimator:
    """La muñeca siempre donde estaba el parche en el primer cuadro (130, 130),
    tardando `delay`: la deteccion completa es lenta y queda vieja."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls = 0

    def estimate(self, _frame, _box):
        self.calls += 1
        time.sleep(self.delay)
        points = np.zeros((17, 2), np.float32)
        scores = np.zeros(17, np.float32)
        points[LEFT_WRIST], scores[LEFT_WRIST] = (130.0, 130.0), 0.9
        return Pose(points=points, scores=scores)

    def close(self):
        pass


def _hand(metrics) -> tuple[float, float]:
    hand = metrics["manos"][0]
    return hand["x"], hand["y"]


class TestAnalizador:
    def test_entre_detecciones_las_manos_siguen_al_video(self):
        estimator = _Estimator()
        analyzer = RouletteAnalyzer(
            zones=[], hands_fps=0.5, detector=_Detector(), estimator=estimator
        )
        analyzer.process_frame(_frame(100, 100), 0.0)  # deteccion completa
        metrics = None
        for step in range(1, 6):  # 5 cuadros a 25 fps, sin deteccion nueva
            metrics = analyzer.process_frame(_frame(100 + 6 * step, 100), step / 25).metrics

        assert estimator.calls == 1
        assert _hand(metrics) == pytest.approx((160.0, 130.0), abs=2.0)

    def test_la_deteccion_en_su_hilo_no_frena_los_cuadros_y_llega_al_actual(self):
        estimator = _Estimator(delay=0.3)
        analyzer = RouletteAnalyzer(
            zones=[],
            hands_fps=0.5,
            detector=_Detector(),
            estimator=estimator,
            background_hands=True,
        )
        try:
            start = time.monotonic()
            analyzer.process_frame(_frame(100, 100), 0.0)  # se larga la deteccion
            assert time.monotonic() - start < 0.2  # no espera a la pose
            metrics, step = None, 0
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                step += 1
                x = 100 + min(6 * step, 60)
                metrics = analyzer.process_frame(_frame(x, 100), step / 25).metrics
                if metrics["manos"] and step > 3:
                    break
                time.sleep(0.02)
            # La deteccion (calculada sobre el cuadro 0) se llevo hasta el
            # cuadro actual: la mano esta donde esta el parche ahora.
            assert metrics["manos"]
            assert _hand(metrics)[0] == pytest.approx(130.0 + (x - 100), abs=2.5)
        finally:
            analyzer.close()


class TestDialogo:
    @pytest.fixture()
    def dialog(self, qtbot, temp_db):
        from aurea_vms.models import repository
        from aurea_vms.ui.dialogs.monitor_tamper_config_dialog import MonitorTamperConfigDialog

        device = repository.add_device(name="Mesa", ip="10.0.0.2", rtsp_main_url="rtsp://c/y")
        repository.upsert_analytics_config(
            device.id, "monitor_tamper", params={"modo": "ruleta", "zones": [[1, 1, 50, 50]]}
        )
        widget = MonitorTamperConfigDialog(device)
        qtbot.addWidget(widget)
        return widget

    def test_en_ruleta_se_llega_a_60_fps(self, dialog):
        assert dialog.fps_spin.maximum() == 60
        assert dialog.fps_spin.value() == 25

        dialog.fps_spin.setValue(60)
        dialog.hands_fps_spin.setValue(8)

        params = dialog.build_params()
        assert params["fps"] == 60 and params["manos_fps"] == 8.0

    def test_en_los_otros_modos_el_tope_sigue_en_15(self, dialog):
        dialog.fps_spin.setValue(60)
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("golpes"))

        assert dialog.fps_spin.maximum() == 15 and dialog.fps_spin.value() == 15


class TestRecuadro:
    def _analyzer(self):
        return RouletteAnalyzer(zones=[], detector=_Detector(), estimator=_Estimator())

    def test_cada_mano_trae_su_recuadro(self):
        metrics = self._analyzer().process_frame(_frame(100, 100), 0.0).metrics

        hand = metrics["manos"][0]
        x, y, w, h = hand["caja"]
        # Mano de un punto: cuadrado de 0,1 diagonales de la persona (160x200).
        assert w == h == pytest.approx(0.1 * np.hypot(160, 200), abs=1)
        assert (x + w / 2, y + h / 2) == pytest.approx((130, 130), abs=1)
        assert hand["puntos_vistos"] == 0

    def test_el_tamaño_se_suaviza_y_el_centro_sigue_a_la_mano(self):
        analyzer = self._analyzer()
        single = Hand(130.0, 130.0, 0.9, "izq")
        points = tuple((110.0 + 4 * i, 90.0 + 4 * i) for i in range(21))  # 80x80
        detailed = Hand(150.0, 130.0, 0.9, "izq", points)

        first = analyzer._hand_box((1, "izq"), single, 256.0, 0.0)
        second = analyzer._hand_box((1, "izq"), detailed, 256.0, 0.04)

        assert first[2] == 26
        raw = 80 * 1.4  # los 21 puntos con el margen
        assert first[2] < second[2] < raw  # no salta de golpe
        assert second[2] == pytest.approx(26 + 0.3 * (raw - 26), abs=1.5)

    def test_la_escena_corre_hasta_25_veces_por_segundo_y_las_manos_en_cada_cuadro(
        self, monkeypatch
    ):
        analyzer = self._analyzer()
        scenes: list[float] = []
        original = analyzer._update_scene
        monkeypatch.setattr(
            analyzer, "_update_scene", lambda f, t: (scenes.append(t), original(f, t))
        )
        hands = 0
        for i in range(60):  # un segundo a 60 fps
            metrics = analyzer.process_frame(_frame(100 + i, 100), i / 60).metrics
            hands += bool(metrics["manos"])

        assert 24 <= len(scenes) <= 26
        assert hands == 60
