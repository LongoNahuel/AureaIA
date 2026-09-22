"""Tests del panel de rostros como renderer.

Cubre lo que NO puede vivir en core/face_catalog.py: que el widget refleje
fielmente lo que el catálogo le devuelve (mismos índices en las dos listas)
y que no vuelva a consultar la DB en cada evento de detección.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from aurea_vms.core.events import Detection, DetectionEvent
from aurea_vms.models import repository
from aurea_vms.ui.widgets import face_gallery as fg_module
from aurea_vms.ui.widgets.face_gallery import FaceGallery

UMBRAL = 0.05


@pytest.fixture()
def camara(temp_db):
    device = repository.add_device(name="Cam", ip="10.0.0.1", rtsp_main_url="rtsp://c/x")
    repository.upsert_analytics_config(
        device.id,
        "face_detection",
        enabled=True,
        params={"max_captures_per_face": 1, "capture_diff_threshold": UMBRAL},
    )
    return device


@pytest.fixture()
def firmas():
    """Controla qué firma devuelve cada recorte.

    El widget se testea como ESPEJO del catálogo, no como algoritmo de
    identidad -- eso lo cubren los 39 tests de test_face_catalog.py. Dejar
    que la firma salga del contenido del frame haría estos tests rehenes de
    equalizeHist: con un gradiente de fondo, dos recortes cualesquiera dan
    la misma firma y "seis caras distintas" terminan siendo una sola.
    """

    class Firmas:
        def __init__(self) -> None:
            self.contador = itertools.count()

        def distintas(self, _crop):
            return np.full((24, 24), next(self.contador) * 0.1, dtype=np.float32)

        def siempre_la_misma(self, _crop):
            return np.zeros((24, 24), dtype=np.float32)

    return Firmas()


@pytest.fixture()
def galeria(qtbot, camara, monkeypatch, firmas):
    """Panel con una cámara asignada y un frame fijo detrás."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    monkeypatch.setattr(
        fg_module.stream_manager,
        "get_worker",
        lambda _id: type("W", (), {"get_latest_frame": staticmethod(lambda: frame)})(),
    )
    monkeypatch.setattr(fg_module, "face_signature", firmas.distintas)
    widget = FaceGallery()
    qtbot.addWidget(widget)
    widget.set_device(camara.id)
    return widget


def _evento(device_id, cajas, timestamp=100.0):
    return DetectionEvent(
        device_id=device_id,
        analyzer_name="face_detection",
        timestamp=timestamp,
        detections=tuple(Detection(label="cara", confidence=0.9, bbox=caja) for caja in cajas),
    )


class TestEspejoDelCatalogo:
    def test_una_cara_nueva_agrega_una_miniatura(self, galeria, camara):
        galeria._on_detection(_evento(camara.id, [(10, 10, 60, 60)]))

        assert galeria.list_widget.count() == 1
        assert len(galeria._catalog.captures) == 1
        assert galeria.counter_label.text() == "IDs catalogados: 1"

    def test_las_dos_listas_quedan_con_el_mismo_largo(self, galeria, camara):
        """El widget aplica removed_index e inserta en 0 igual que el
        catálogo: si se desincronizan, las miniaturas dejan de corresponder
        a las identidades."""
        for i in range(6):
            galeria._on_detection(_evento(camara.id, [(i * 80, 10, 60, 60)]))

        assert galeria.list_widget.count() == len(galeria._catalog.captures)

    def test_el_id_de_la_miniatura_es_el_del_catalogo(self, galeria, camara):
        galeria._on_detection(_evento(camara.id, [(10, 10, 60, 60)]))
        galeria._on_detection(_evento(camara.id, [(400, 10, 60, 60)]))

        textos = [galeria.list_widget.item(i).text() for i in range(galeria.list_widget.count())]
        esperados = [f"ID #{c.track_id}" for c in galeria._catalog.captures]
        assert textos == esperados

    def test_una_deteccion_descartada_no_agrega_miniatura(
        self, galeria, camara, monkeypatch, firmas
    ):
        """Misma cara, más chica: el catálogo la descarta y el widget no
        tiene que pintar nada."""
        monkeypatch.setattr(fg_module, "face_signature", firmas.siempre_la_misma)

        galeria._on_detection(_evento(camara.id, [(10, 10, 120, 120)]))
        galeria._on_detection(_evento(camara.id, [(10, 10, 40, 40)]))

        assert galeria.list_widget.count() == 1
        assert [c.area for c in galeria._catalog.captures] == [120 * 120]

    def test_una_toma_mejor_reemplaza_la_miniatura(self, galeria, camara, monkeypatch, firmas):
        monkeypatch.setattr(fg_module, "face_signature", firmas.siempre_la_misma)

        galeria._on_detection(_evento(camara.id, [(10, 10, 40, 40)]))
        galeria._on_detection(_evento(camara.id, [(10, 10, 120, 120)]))

        assert galeria.list_widget.count() == 1
        assert [c.area for c in galeria._catalog.captures] == [120 * 120]

    def test_la_poda_recorta_las_dos_listas(self, galeria, camara, monkeypatch):
        monkeypatch.setattr(galeria._catalog, "_max_items", 3)

        for i in range(6):
            galeria._on_detection(_evento(camara.id, [(i * 90, 10, 60, 60)]))

        assert galeria.list_widget.count() == 3
        assert len(galeria._catalog.captures) == 3

    def test_ignora_eventos_de_otra_camara_o_analitica(self, galeria, camara):
        galeria._on_detection(_evento(camara.id + 99, [(10, 10, 60, 60)]))
        otra = _evento(camara.id, [(10, 10, 60, 60)])
        galeria._on_detection(
            DetectionEvent(
                device_id=camara.id,
                analyzer_name="people_counting",
                timestamp=otra.timestamp,
                detections=otra.detections,
            )
        )

        assert galeria.list_widget.count() == 0

    def test_cambiar_de_camara_vacia_todo(self, galeria, camara):
        galeria._on_detection(_evento(camara.id, [(10, 10, 60, 60)]))

        galeria.set_device(None)

        assert galeria.list_widget.count() == 0
        assert galeria._catalog.captures == ()
        assert galeria.counter_label.text() == "IDs catalogados: 0"


class TestCacheDeConfiguracion:
    def test_no_consulta_la_db_en_cada_evento(self, galeria, camara, monkeypatch):
        """Leerla por evento costaba 768 us en el hilo de la GUI, o sea ~25
        consultas por segundo y por cámara contra la misma base que escriben
        los hilos de analítica."""
        consultas: list[int] = []
        original = repository.get_analytics_config_for
        monkeypatch.setattr(
            repository,
            "get_analytics_config_for",
            lambda device_id, name: (consultas.append(device_id), original(device_id, name))[1],
        )

        for i in range(10):
            galeria._on_detection(_evento(camara.id, [(i * 60, 10, 50, 50)]))

        assert len(consultas) == 1

    def test_guardar_la_config_de_esta_camara_invalida_el_cache(self, galeria, camara):
        galeria._face_settings()
        assert galeria._settings is not None

        galeria._on_analytics_config_changed(camara.id)

        assert galeria._settings is None

    def test_la_config_de_otra_camara_no_invalida_nada(self, galeria, camara):
        galeria._face_settings()

        galeria._on_analytics_config_changed(camara.id + 99)

        assert galeria._settings is not None

    def test_lee_lo_que_quedo_guardado_despues_de_invalidar(self, galeria, camara):
        assert galeria._face_settings().max_captures_per_face == 1

        repository.upsert_analytics_config(
            camara.id, "face_detection", params={"max_captures_per_face": 4}
        )
        galeria._on_analytics_config_changed(camara.id)

        assert galeria._face_settings().max_captures_per_face == 4

    def test_sin_camara_asignada_usa_los_defaults(self, qtbot, temp_db):
        widget = FaceGallery()
        qtbot.addWidget(widget)

        assert widget._face_settings().max_captures_per_face == 1
