from __future__ import annotations

from aurea_vms.core.analytics.object_detector_backend import (
    deduplicate_by_iou,
    passes_box_shape_filter,
    passes_min_area_filter,
)
from aurea_vms.core.events import Detection


def _det(x, y, w, h, label="person", confidence=0.8) -> Detection:
    return Detection(label=label, confidence=confidence, bbox=(x, y, w, h))


class TestFiltroDeFormaDeCaja:
    def test_caja_de_persona_pasa(self):
        assert passes_box_shape_filter(40, 100) is True

    def test_caja_de_auto_pasa(self):
        assert passes_box_shape_filter(180, 70) is True

    def test_caja_degenerada_sin_ancho_se_rechaza(self):
        assert passes_box_shape_filter(0, 100) is False

    def test_caja_degenerada_sin_alto_se_rechaza(self):
        assert passes_box_shape_filter(100, 0) is False

    def test_tira_extremadamente_angosta_se_rechaza(self):
        assert passes_box_shape_filter(2, 100) is False

    def test_rectangulo_extremadamente_chato_se_rechaza(self):
        assert passes_box_shape_filter(100, 2) is False


class TestFiltroDeAreaMinima:
    def test_deteccion_grande_pasa(self):
        assert passes_min_area_filter(100, 100, frame_area=100_000, min_area_percent=0.15) is True

    def test_deteccion_chica_se_rechaza(self):
        assert passes_min_area_filter(5, 5, frame_area=100_000, min_area_percent=0.15) is False

    def test_umbral_cero_desactiva_el_filtro(self):
        assert passes_min_area_filter(1, 1, frame_area=100_000, min_area_percent=0.0) is True

    def test_area_de_frame_invalida_no_filtra(self):
        assert passes_min_area_filter(1, 1, frame_area=0, min_area_percent=0.15) is True


class TestDeduplicacionPorIou:
    def test_cajas_muy_superpuestas_se_deduplican(self):
        detections = [
            _det(100, 100, 50, 100, confidence=0.7),
            _det(103, 101, 50, 100, confidence=0.9),  # casi la misma caja
        ]
        result = deduplicate_by_iou(detections)

        assert len(result) == 1
        assert result[0].confidence == 0.9  # se queda con la de mayor confianza

    def test_cajas_separadas_no_se_deduplican(self):
        detections = [_det(0, 0, 50, 100), _det(500, 500, 50, 100)]
        result = deduplicate_by_iou(detections)
        assert len(result) == 2

    def test_clases_distintas_no_se_deduplican_aunque_se_superpongan(self):
        detections = [
            _det(100, 100, 50, 100, label="person"),
            _det(100, 100, 50, 100, label="car"),
        ]
        result = deduplicate_by_iou(detections)
        assert len(result) == 2

    def test_lista_vacia(self):
        assert deduplicate_by_iou([]) == []
