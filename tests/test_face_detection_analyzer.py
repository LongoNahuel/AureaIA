from __future__ import annotations

from dataclasses import dataclass

from aurea_vms.core.analytics.face_detection_analyzer import FaceDetectionAnalyzer


@dataclass
class _Kp:
    x: float
    y: float


def _face(right_eye, left_eye, nose, mouth) -> list[_Kp]:
    return [_Kp(*right_eye), _Kp(*left_eye), _Kp(*nose), _Kp(*mouth)]


def _face6(right_eye, left_eye, nose, mouth, right_ear, left_ear) -> list[_Kp]:
    return [_Kp(*p) for p in (right_eye, left_eye, nose, mouth, right_ear, left_ear)]


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


class TestFiltroDeOrejas:
    """Los 4 puntos centrales (ojos/nariz/boca) de un objeto plano -- una
    caja, un cartel -- a veces caen por casualidad en una disposicion que
    parece una cara. Que ADEMAS las orejas caigan en el lugar anatomico
    correcto es mucho mas raro por azar; estos tests simulan justamente
    ese caso: los 4 puntos centrales pasarian solos, pero las orejas los
    delatan."""

    BASE = {
        "right_eye": (0.35, 0.4),
        "left_eye": (0.55, 0.4),
        "nose": (0.45, 0.5),
        "mouth": (0.45, 0.6),
    }

    def test_cara_con_orejas_bien_ubicadas_pasa(self):
        kps = _face6(**self.BASE, right_ear=(0.25, 0.42), left_ear=(0.65, 0.42))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is True

    def test_oreja_derecha_mas_cerca_del_centro_que_el_ojo_se_rechaza(self):
        """La "oreja" cae del lado de adentro del ojo -- imposible en una
        cara real, tipico de un punto espurio sobre una caja."""
        kps = _face6(**self.BASE, right_ear=(0.4, 0.42), left_ear=(0.65, 0.42))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is False

    def test_oreja_izquierda_mas_cerca_del_centro_que_el_ojo_se_rechaza(self):
        kps = _face6(**self.BASE, right_ear=(0.25, 0.42), left_ear=(0.5, 0.42))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is False

    def test_oreja_muy_por_encima_de_la_cara_se_rechaza(self):
        kps = _face6(**self.BASE, right_ear=(0.25, 0.02), left_ear=(0.65, 0.42))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is False

    def test_oreja_muy_por_debajo_de_la_cara_se_rechaza(self):
        kps = _face6(**self.BASE, right_ear=(0.25, 0.42), left_ear=(0.65, 0.98))
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is False

    def test_sin_puntos_de_orejas_no_se_puede_validar_pasa(self):
        """Solo 4 keypoints (sin orejas): el chequeo se omite, no rechaza
        por defecto."""
        kps = _face(**self.BASE)
        assert FaceDetectionAnalyzer._passes_geometry_filter(kps) is True


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


@dataclass
class _HeadBox:
    origin_x: float
    origin_y: float
    width: float
    height: float


class TestFiltroDeAlineacionConLaCabeza:
    """crop 200x200; caja de cabeza en pixeles (50, 40, 100, 120) ->
    normalizada: x en [0.25, 0.75], y en [0.2, 0.8]."""

    CROP = (200, 200)
    BOX = _HeadBox(50, 40, 100, 120)

    def test_puntos_bien_alineados_con_la_cabeza_pasan(self):
        kps = _face((0.4, 0.38), (0.6, 0.38), (0.5, 0.5), (0.5, 0.62))
        assert (
            FaceDetectionAnalyzer._passes_head_alignment_filter(kps, self.BOX, *self.CROP) is True
        )

    def test_ojos_en_la_mitad_inferior_de_una_caja_mas_alta_se_rechaza(self):
        """Los puntos son autoconsistentes entre si, pero estan
        amontonados en la parte de abajo de una caja mucho mas grande --
        no es donde deberia estar la cabeza."""
        kps = _face((0.4, 0.72), (0.6, 0.72), (0.5, 0.76), (0.5, 0.79))
        assert (
            FaceDetectionAnalyzer._passes_head_alignment_filter(kps, self.BOX, *self.CROP) is False
        )

    def test_boca_pegada_a_los_ojos_no_llega_a_la_mitad_inferior_se_rechaza(self):
        """Ojos en el borde superior de la caja (todavia valido) pero la
        boca queda pegada a los ojos en vez de bajar hasta la mitad
        inferior -- no describe una cabeza real."""
        kps = _face((0.4, 0.2), (0.6, 0.2), (0.5, 0.21), (0.5, 0.22))
        assert (
            FaceDetectionAnalyzer._passes_head_alignment_filter(kps, self.BOX, *self.CROP) is False
        )

    def test_nariz_muy_fuera_del_ancho_de_la_caja_se_rechaza(self):
        kps = _face((0.4, 0.38), (0.6, 0.38), (0.95, 0.5), (0.5, 0.62))
        assert (
            FaceDetectionAnalyzer._passes_head_alignment_filter(kps, self.BOX, *self.CROP) is False
        )

    def test_menos_de_4_puntos_no_se_puede_validar_pasa(self):
        assert FaceDetectionAnalyzer._passes_head_alignment_filter([], self.BOX, *self.CROP) is True

    def test_caja_sin_area_no_se_puede_validar_pasa(self):
        kps = _face((0.4, 0.38), (0.6, 0.38), (0.5, 0.5), (0.5, 0.62))
        empty_box = _HeadBox(50, 40, 0, 0)
        assert (
            FaceDetectionAnalyzer._passes_head_alignment_filter(kps, empty_box, *self.CROP) is True
        )
