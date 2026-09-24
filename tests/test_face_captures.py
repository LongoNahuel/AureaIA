"""Capturas de rostro (2026-09-23): puntaje de calidad, puerta de "rostro
visible" calibrada sobre los clips de la demo, politica de tomas del
analizador (solo la mejor de cada paso) y la tira del dashboard. No hay
identidad ni conteo de personas unicas."""

from __future__ import annotations

import numpy as np
import pytest

import aurea_vms.core.analytics.face_detection_analyzer as fd_module
from aurea_vms.core.analytics.face_detection_analyzer import FaceDetectionAnalyzer
from aurea_vms.core.analytics.face_quality import (
    display_crop,
    face_quality,
    frontal_score,
    is_face_visible,
    sharpness_score,
    size_score,
)
from aurea_vms.core.events import DetectionEvent, FaceShot


def _row(x=100.0, y=100.0, size=120.0, nose_dx=0.0, score=0.95) -> np.ndarray:
    """Fila de YuNet (15 valores) para una cara frontal de `size` px."""
    eye_y = y + size * 0.38
    return np.array(
        [
            x, y, size, size,
            x + size * 0.32, eye_y,  # ojo derecho
            x + size * 0.68, eye_y,  # ojo izquierdo
            x + size * 0.5 + nose_dx, y + size * 0.58,  # nariz
            x + size * 0.36, y + size * 0.78,  # comisura der
            x + size * 0.64, y + size * 0.78,  # comisura izq
            score,
        ],
        dtype=np.float32,
    )  # fmt: skip


def _textured(size=112, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (size, size, 3), dtype=np.uint8)


class TestCalidad:
    def test_de_frente_puntua_mas_que_de_perfil(self):
        assert frontal_score(_row()) == pytest.approx(1.0)
        assert frontal_score(_row(nose_dx=30)) < 0.2

    def test_nitida_puntua_mas_que_borrosa(self):
        sharp = _textured()[:, :, 0]
        blurred = np.full_like(sharp, 120)
        assert sharpness_score(sharp) > 0.9
        assert sharpness_score(blurred) == pytest.approx(0.0)

    def test_cara_chica_no_suma_por_tamaño(self):
        assert size_score(_row(size=30)) == 0.0
        assert size_score(_row(size=200)) == 1.0

    def test_el_puntaje_queda_entre_cero_y_uno(self):
        quality = face_quality(_row(), _textured())
        assert 0.0 <= quality <= 1.0

    def test_el_recorte_de_galeria_es_cuadrado_y_con_margen(self):
        image = _textured(400)
        crop = display_crop(image, (150, 150, 80, 100))
        assert crop.shape[0] == crop.shape[1] == 145  # lado mayor 100 * 1.45


def _details(**overrides) -> dict:
    """Una cara real bien vista, como las del clip cam4 de la demo."""
    details = {"confianza": 0.92, "frontal": 0.95, "distancia_ojos_px": 57.0, "nitidez": 0.7}
    details.update(overrides)
    return details


class TestRostroVisible:
    def test_una_cara_real_bien_vista_pasa(self):
        assert is_face_visible(_details())

    def test_falso_positivo_nitido_con_confianza_baja_no_pasa(self):
        """El centro de la ruleta (cam3) y las pantallas de tragamonedas
        (cam1) salian con puntaje 0,92 y confianza 0,52-0,64."""
        assert not is_face_visible(_details(confianza=0.58, frontal=0.99, nitidez=0.97))

    @pytest.mark.parametrize(
        "motivo",
        [
            {"frontal": 0.3},  # de perfil
            {"distancia_ojos_px": 10.0},  # muy chica / lejana
            {"nitidez": 0.1},  # movida
        ],
    )
    def test_de_perfil_chica_o_movida_no_pasa(self, motivo):
        assert not is_face_visible(_details(**motivo))


class _FakeYunet:
    def __init__(self) -> None:
        self.rows: list[np.ndarray] = []

    def setInputSize(self, _size):  # noqa: N802 - API de OpenCV
        pass

    def detect(self, _image):
        return 1, (np.stack(self.rows) if self.rows else None)


@pytest.fixture()
def analyzer(monkeypatch):
    fake = _FakeYunet()
    monkeypatch.setattr(fd_module.cv2.FaceDetectorYN, "create", lambda *a, **k: fake)
    face_analyzer = FaceDetectionAnalyzer(confirmation_frames=1, min_pupillary_distance_px=0)
    return face_analyzer, fake


class TestTomasDelAnalizador:
    FRAME = np.random.default_rng(1).integers(0, 255, (480, 640, 3), dtype=np.uint8)

    def test_publica_la_toma_de_una_cara_visible(self, analyzer):
        face_analyzer, fake = analyzer
        fake.rows = [_row()]
        result = face_analyzer.process_frame(self.FRAME, 0.0)

        (shot,) = result.metrics["face_shots"]
        assert shot.track_id == 1
        assert shot.image.shape[0] == shot.image.shape[1]
        assert 0.0 < shot.quality <= 1.0
        assert shot.details["confianza"] == pytest.approx(0.95)

    def test_no_repite_tomas_que_no_mejoran(self, analyzer):
        face_analyzer, fake = analyzer
        fake.rows = [_row()]
        face_analyzer.process_frame(self.FRAME, 0.0)
        again = face_analyzer.process_frame(self.FRAME, 0.5)  # mismo track, pasado el intervalo

        assert "face_shots" not in again.metrics

    def test_detecta_pero_no_guarda_una_cara_poco_visible(self, analyzer):
        """La sensibilidad del analizador decide que se marca en el video;
        la puerta de visibilidad, que se guarda."""
        face_analyzer, fake = analyzer
        fake.rows = [_row(score=0.6)]  # nitida y de frente, pero detector dudoso
        result = face_analyzer.process_frame(self.FRAME, 0.0)

        assert result.metrics["caras"] == 1
        assert "face_shots" not in result.metrics


class TestTiraDeRostros:
    """La tira del dashboard junta las capturas de todas las camaras."""

    @staticmethod
    def _shot(track_id: int, quality: float = 0.6) -> FaceShot:
        return FaceShot(
            track_id=track_id,
            image=_textured(96, track_id),
            quality=quality,
            confidence=0.9,
            bbox=(0, 0, 60, 60),
        )

    @pytest.fixture()
    def strip(self, qtbot):
        from aurea_vms.ui.face_registry import face_registry
        from aurea_vms.ui.widgets.face_strip import FaceStrip

        face_registry.reset()
        for device_id in (1, 2):
            face_registry._names[device_id] = f"Cam {device_id}"
        widget = FaceStrip()
        qtbot.addWidget(widget)
        yield widget
        # El registro es global: si la tira queda conectada, sigue
        # redibujandose (y consultando la DB) en los tests siguientes.
        face_registry.captures_changed.disconnect(widget._rebuild)
        face_registry.reset()

    @staticmethod
    def _event(device_id: int, *shots, timestamp: float = 0.0) -> DetectionEvent:
        return DetectionEvent(
            device_id, "face_detection", timestamp, metrics={"face_shots": list(shots)}
        )

    def test_muestra_capturas_de_varias_camaras(self, strip):
        strip._on_detection(self._event(1, self._shot(1)))
        strip._on_detection(self._event(2, self._shot(1)))

        assert strip.list_widget.count() == 2
        assert strip.count_label.text() == "2 capturas"

    def test_una_toma_mejor_del_mismo_paso_reemplaza_y_no_duplica(self, strip):
        strip._on_detection(self._event(1, self._shot(1, quality=0.5)))
        strip._on_detection(self._event(1, self._shot(1, quality=0.9), timestamp=1.0))

        assert strip.list_widget.count() == 1

    def test_ignora_otras_analiticas(self, strip):
        strip._on_detection(DetectionEvent(1, "people_counting", 0.0, metrics={"occupancy": 3}))
        assert strip.list_widget.count() == 0
        assert strip.empty_label.isVisibleTo(strip)
