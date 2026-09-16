from __future__ import annotations

from aurea_vms.core.analytics.face_detection_analyzer import FaceDetectionAnalyzer


def _face(
    box=(0, 0, 100, 100),
    right_eye=(0, 0),
    left_eye=(0, 0),
    nose=(0, 0),
    mouth_r=(0, 0),
    mouth_l=(0, 0),
    score=0.9,
) -> list[float]:
    """Arma el array Nx15 que devuelve cv2.FaceDetectorYN.detect() para
    una sola cara: bbox (4) + 5 landmarks (10) + score (1)."""
    bx, by, bw, bh = box
    return [bx, by, bw, bh, *right_eye, *left_eye, *nose, *mouth_r, *mouth_l, score]


# Cara frontal "de referencia": pasa los tres filtros a la vez.
FRONTAL = dict(
    right_eye=(70, 80),
    left_eye=(110, 80),
    nose=(90, 100),
    mouth_r=(75, 120),
    mouth_l=(105, 120),
)


class TestFiltroGeometrico:
    def test_cara_frontal_pasa(self):
        assert FaceDetectionAnalyzer._passes_geometry_filter(_face(**FRONTAL)) is True

    def test_cara_levemente_inclinada_pasa(self):
        face = _face(
            right_eye=(65, 82),
            left_eye=(115, 76),
            nose=(92, 100),
            mouth_r=(78, 122),
            mouth_l=(108, 118),
        )
        assert FaceDetectionAnalyzer._passes_geometry_filter(face) is True

    def test_ojos_mas_verticales_que_horizontales_se_rechaza(self):
        face = _face(**{**FRONTAL, "right_eye": (90, 60), "left_eye": (95, 140)})
        assert FaceDetectionAnalyzer._passes_geometry_filter(face) is False

    def test_ojos_superpuestos_en_x_se_rechaza(self):
        face = _face(**{**FRONTAL, "right_eye": (90, 80), "left_eye": (90, 81)})
        assert FaceDetectionAnalyzer._passes_geometry_filter(face) is False

    def test_orden_vertical_invertido_se_rechaza(self):
        face = _face(**{**FRONTAL, "nose": (90, 120), "mouth_r": (75, 100), "mouth_l": (105, 100)})
        assert FaceDetectionAnalyzer._passes_geometry_filter(face) is False

    def test_nariz_muy_descentrada_se_rechaza(self):
        """El margen es laxo a proposito (NOSE_SLACK_RATIO, calibrado
        contra deteccion real) -- hace falta una nariz MUY lejos de ojos
        y boca para disparar este rechazo."""
        face = _face(**{**FRONTAL, "nose": (900, 100)})
        assert FaceDetectionAnalyzer._passes_geometry_filter(face) is False

    def test_boca_muy_angosta_respecto_a_los_ojos_se_rechaza(self):
        face = _face(**{**FRONTAL, "mouth_r": (89, 120), "mouth_l": (91, 120)})
        assert FaceDetectionAnalyzer._passes_geometry_filter(face) is False

    def test_boca_muy_ancha_respecto_a_los_ojos_se_rechaza(self):
        face = _face(**{**FRONTAL, "mouth_r": (-100, 120), "mouth_l": (300, 120)})
        assert FaceDetectionAnalyzer._passes_geometry_filter(face) is False


class TestFiltroDeFormaDeCaja:
    def test_caja_cuadrada_pasa(self):
        assert FaceDetectionAnalyzer._passes_box_shape_filter(_face(box=(0, 0, 100, 120))) is True

    def test_caja_muy_angosta_se_rechaza(self):
        assert FaceDetectionAnalyzer._passes_box_shape_filter(_face(box=(0, 0, 20, 200))) is False

    def test_caja_muy_chata_se_rechaza(self):
        assert FaceDetectionAnalyzer._passes_box_shape_filter(_face(box=(0, 0, 300, 50))) is False

    def test_altura_cero_se_rechaza(self):
        assert FaceDetectionAnalyzer._passes_box_shape_filter(_face(box=(0, 0, 100, 0))) is False


class TestFiltroDeAlineacionConLaCabeza:
    """Caja de cabeza en pixeles (50, 40, 100, 120) -> x en [50,150], y en
    [40,160]."""

    BOX = (50, 40, 100, 120)

    def test_puntos_bien_alineados_con_la_cabeza_pasan(self):
        face = _face(
            box=self.BOX,
            right_eye=(80, 76),
            left_eye=(120, 76),
            nose=(100, 90),
            mouth_r=(85, 124),
            mouth_l=(115, 124),
        )
        assert FaceDetectionAnalyzer._passes_head_alignment_filter(face) is True

    def test_ojos_en_la_mitad_inferior_de_una_caja_mas_alta_se_rechaza(self):
        face = _face(
            box=self.BOX,
            right_eye=(80, 148),
            left_eye=(120, 148),
            nose=(100, 150),
            mouth_r=(85, 152),
            mouth_l=(115, 152),
        )
        assert FaceDetectionAnalyzer._passes_head_alignment_filter(face) is False

    def test_boca_pegada_a_los_ojos_no_llega_a_la_mitad_inferior_se_rechaza(self):
        face = _face(
            box=self.BOX,
            right_eye=(80, 40),
            left_eye=(120, 40),
            nose=(100, 42),
            mouth_r=(85, 44),
            mouth_l=(115, 44),
        )
        assert FaceDetectionAnalyzer._passes_head_alignment_filter(face) is False

    def test_nariz_muy_fuera_del_ancho_de_la_caja_se_rechaza(self):
        face = _face(
            box=self.BOX,
            right_eye=(80, 76),
            left_eye=(120, 76),
            nose=(245, 90),
            mouth_r=(85, 124),
            mouth_l=(115, 124),
        )
        assert FaceDetectionAnalyzer._passes_head_alignment_filter(face) is False

    def test_caja_sin_area_no_se_puede_validar_pasa(self):
        face = _face(box=(50, 40, 0, 0), **FRONTAL)
        assert FaceDetectionAnalyzer._passes_head_alignment_filter(face) is True
