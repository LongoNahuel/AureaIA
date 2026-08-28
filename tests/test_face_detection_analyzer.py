from __future__ import annotations

from dataclasses import dataclass

from aurea_vms.core.analytics.face_detection_analyzer import FaceDetectionAnalyzer


@dataclass
class _Kp:
    x: float
    y: float


def _face(right_eye, left_eye, nose, mouth) -> list[_Kp]:
    return [_Kp(*right_eye), _Kp(*left_eye), _Kp(*nose), _Kp(*mouth)]


class TestFiltroGeometrico:
    def test_cara_frontal_pasa(self):
        kps = _face((0.35, 0.4), (0.55, 0.4), (0.45, 0.5), (0.45, 0.6))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is True

    def test_cara_levemente_inclinada_pasa(self):
        kps = _face((0.3, 0.42), (0.6, 0.38), (0.47, 0.55), (0.45, 0.65))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is True

    def test_menos_de_4_puntos_no_se_puede_validar_pasa(self):
        assert FaceDetectionAnalyzer._passes_geometry_filter([]) is True
        assert FaceDetectionAnalyzer._passes_geometry_filter([_Kp(0.5, 0.5)]) is True

    def test_ojos_casi_en_vertical_se_rechaza(self):
        """Un par de "ojos" apilados verticalmente es geometricamente
        imposible en una cara real -- tipico de una deteccion espuria."""
        kps = _face((0.5, 0.3), (0.5, 0.6), (0.5, 0.7), (0.5, 0.8))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is False

    def test_orden_vertical_invertido_se_rechaza(self):
        kps = _face((0.35, 0.4), (0.55, 0.4), (0.45, 0.6), (0.45, 0.5))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is False

    def test_nariz_muy_descentrada_se_rechaza(self):
        kps = _face((0.35, 0.4), (0.55, 0.4), (0.9, 0.5), (0.45, 0.6))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is False

    def test_ojos_superpuestos_en_x_se_rechaza(self):
        kps = _face((0.5, 0.4), (0.5, 0.41), (0.5, 0.5), (0.5, 0.6))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is False

    def test_boca_muy_asimetrica_respecto_a_los_ojos_se_rechaza(self):
        """Pasa orden vertical, linea de ojos y nariz centrada, pero la
        boca queda pegada a un ojo y lejos del otro -- no es una cara."""
        kps = _face((0.3, 0.4), (0.5, 0.4), (0.4, 0.42), (0.31, 0.44))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is False


@dataclass
class _Box:
    width: float
    height: float


class TestFiltroDeFormaDeCaja:
    def test_caja_cuadrada_pasa(self):
        assert FaceDetectionAnalyzer._passes_box_shape_filter(_Box(100, 120)) is True

    def test_caja_muy_angosta_se_rechaza(self):
        assert FaceDetectionAnalyzer._passes_box_shape_filter(_Box(20, 200)) is False

    def test_caja_muy_chata_se_rechaza(self):
        assert FaceDetectionAnalyzer._passes_box_shape_filter(_Box(300, 50)) is False

    def test_altura_cero_se_rechaza(self):
        assert FaceDetectionAnalyzer._passes_box_shape_filter(_Box(100, 0)) is False
