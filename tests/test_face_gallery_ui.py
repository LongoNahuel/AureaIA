"""Tests del panel de rostros como espejo del registro de capturas.

Cubre lo que NO puede vivir en ui/face_registry.py: que el widget refleje
fielmente las capturas del registro para la camara enfocada, que cambiar de
camara muestre las de esa camara y que no vuelva a consultar la DB en cada
evento de deteccion.
"""

from __future__ import annotations

import numpy as np
import pytest

from aurea_vms.core.events import DetectionEvent, FaceShot
from aurea_vms.models import repository
from aurea_vms.ui.face_registry import face_registry
from aurea_vms.ui.widgets.face_gallery import FaceGallery


@pytest.fixture(autouse=True)
def registro_limpio():
    """El registro es compartido por toda la UI: cada test arranca vacío."""
    face_registry.reset()
    yield
    face_registry.reset()


@pytest.fixture()
def camara(temp_db):
    device = repository.add_device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
    repository.upsert_analytics_config(device.id, "face_detection", enabled=True, params={})
    return device


@pytest.fixture()
def galeria(qtbot, camara):
    widget = FaceGallery()
    qtbot.addWidget(widget)
    widget.set_device(camara.id)
    return widget


def _toma(track_id: int, quality: float = 0.6) -> FaceShot:
    image = np.random.default_rng(track_id).integers(0, 255, (96, 96, 3), dtype=np.uint8)
    return FaceShot(
        track_id=track_id, image=image, quality=quality, confidence=0.9, bbox=(0, 0, 60, 60)
    )


def _evento(device_id, *tomas, timestamp=100.0, caras=1):
    return DetectionEvent(
        device_id=device_id,
        analyzer_name="face_detection",
        timestamp=timestamp,
        metrics={"caras": caras, "face_shots": list(tomas)},
    )


class TestEspejoDelRegistro:
    def test_una_captura_nueva_agrega_una_miniatura(self, galeria, camara):
        galeria._on_detection(_evento(camara.id, _toma(1)))

        assert galeria.list_widget.count() == 1
        assert galeria.counter_label.text() == "1"
        assert galeria.hero.isVisibleTo(galeria)

    def test_la_grilla_tiene_las_mismas_capturas_que_el_registro(self, galeria, camara):
        for track in range(1, 5):
            galeria._on_detection(_evento(camara.id, _toma(track), timestamp=100.0 + track))

        assert galeria.list_widget.count() == len(face_registry.records(camara.id)) == 4

    def test_la_mas_reciente_va_primero_y_se_rotula_con_la_hora(self, galeria, camara):
        galeria._on_detection(_evento(camara.id, _toma(1), timestamp=100.0))
        galeria._on_detection(_evento(camara.id, _toma(2), timestamp=200.0))

        primera = face_registry.records(camara.id)[0]
        assert primera.track_id == 2
        assert galeria.list_widget.item(0).text() == primera.when

    def test_una_toma_mejor_del_mismo_paso_reemplaza_la_miniatura(self, galeria, camara):
        galeria._on_detection(_evento(camara.id, _toma(1, quality=0.5)))
        galeria._on_detection(_evento(camara.id, _toma(1, quality=0.9), timestamp=101.0))

        assert galeria.list_widget.count() == 1
        assert face_registry.records(camara.id)[0].quality == 0.9

    def test_muestra_las_caras_en_cuadro(self, galeria, camara):
        galeria._on_detection(_evento(camara.id, caras=3))

        assert galeria.in_frame_label.text() == "3"

    def test_ignora_eventos_de_otra_camara_o_analitica(self, galeria, camara):
        galeria._on_detection(_evento(camara.id + 99, _toma(1)))
        galeria._on_detection(
            DetectionEvent(
                device_id=camara.id,
                analyzer_name="people_counting",
                timestamp=100.0,
                metrics={"face_shots": [_toma(2)]},
            )
        )

        assert galeria.list_widget.count() == 0

    def test_cambiar_de_camara_muestra_las_de_esa_camara(self, galeria, camara):
        galeria._on_detection(_evento(camara.id, _toma(1)))

        galeria.set_device(None)
        assert galeria.list_widget.count() == 0
        assert galeria.counter_label.text() == "0"

        galeria.set_device(camara.id)
        assert galeria.list_widget.count() == 1

    def test_vaciar_borra_las_capturas_de_la_camara(self, galeria, camara):
        galeria._on_detection(_evento(camara.id, _toma(1)))
        galeria._clear_captures()

        assert galeria.list_widget.count() == 0
        assert face_registry.capture_count(camara.id) == 0


class TestSinConsultasPorEvento:
    def test_no_consulta_la_db_en_cada_evento(self, galeria, camara, monkeypatch):
        """Leer la DB por evento costaba 768 us en el hilo de la GUI, o sea
        ~25 consultas por segundo y por cámara contra la misma base que
        escriben los hilos de analítica."""
        consultas: list[str] = []
        for nombre in ("get_analytics_config_for", "get_device"):
            original = getattr(repository, nombre)
            monkeypatch.setattr(
                repository,
                nombre,
                lambda *args, _n=nombre, _o=original: (consultas.append(_n), _o(*args))[1],
            )

        for track in range(10):
            galeria._on_detection(_evento(camara.id, _toma(track), timestamp=100.0 + track))

        assert consultas == []
