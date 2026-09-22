"""Tests del pipeline de YOLOX-Tiny, sin modelo.

El riesgo que cubre este archivo está escrito en docs/ROADMAP.md: "una
regresión en el letterbox, la decodificación de cabezas, el NMS o YuNet no
la detecta nadie". Los unitarios de los analizadores reemplazan
`YoloxDetector` entero por un doble (tests/test_counting_analyzers.py) y el
test de integración sólo construía los analizadores. O sea que todo lo que
hay entre el frame y las cajas no lo ejercitaba nada: object_detector_backend
estaba al 40%.

Acá no se carga el modelo. Se fabrican los tensores a mano y se verifica el
pre y el post-procesamiento, que es código nuestro y es donde una regresión
se manifiesta como cajas corridas -- no como una excepción.
"""

from __future__ import annotations

import numpy as np
import pytest

from aurea_vms.core.analytics.object_detector_backend import (
    COCO_CLASSES,
    INPUT_SIZE,
    STRIDES,
    YoloxDetector,
    _decode_outputs,
    _multiclass_nms,
    _nms,
    _preprocess,
)

# 52*52 + 26*26 + 13*13 para una entrada de 416 con strides 8/16/32.
ANCHORS_POR_CABEZA = [(INPUT_SIZE[0] // s) * (INPUT_SIZE[1] // s) for s in STRIDES]
TOTAL_ANCHORS = sum(ANCHORS_POR_CABEZA)


class TestPreprocess:
    def test_devuelve_chw_float32_del_tamano_de_entrada(self):
        chw, _scale = _preprocess(np.zeros((720, 1280, 3), dtype=np.uint8))

        assert chw.shape == (3, INPUT_SIZE[0], INPUT_SIZE[1])
        assert chw.dtype == np.float32

    def test_la_escala_preserva_la_relacion_de_aspecto(self):
        """Una sola escala para los dos ejes: la que hace entrar el lado más
        largo. Si se escalara cada eje por su cuenta, las cajas volverían
        deformadas al frame original."""
        _chw, scale = _preprocess(np.zeros((640, 480, 3), dtype=np.uint8))

        assert scale == pytest.approx(min(INPUT_SIZE[0] / 640, INPUT_SIZE[1] / 480))

    def test_el_relleno_gris_va_abajo_y_a_la_derecha(self):
        """La convención del export oficial de YOLOX es alinear arriba-izquierda,
        NO centrar. Si se centrara, el `/= scale` de detect() devolvería todas
        las cajas corridas por medio relleno."""
        frame = np.full((640, 480, 3), 10, dtype=np.uint8)  # alto > ancho
        chw, scale = _preprocess(frame)

        ancho_util = int(480 * scale)
        assert ancho_util < INPUT_SIZE[1]  # tiene que sobrar lugar a la derecha

        assert chw[0, 0, 0] == 10  # arriba-izquierda es imagen
        assert chw[0, 0, ancho_util - 1] == 10  # última columna de imagen
        assert chw[0, 0, ancho_util] == 114  # y de ahí en adelante, gris
        assert chw[0, 0, INPUT_SIZE[1] - 1] == 114

    def test_no_normaliza_a_0_1(self):
        """El modelo espera 0-255. Normalizar acá daría detecciones vacías
        sin ningún error visible."""
        chw, _scale = _preprocess(np.full((416, 416, 3), 255, dtype=np.uint8))

        assert chw.max() == 255.0

    def test_un_frame_cuadrado_del_tamano_justo_no_lleva_relleno(self):
        chw, scale = _preprocess(np.full((416, 416, 3), 200, dtype=np.uint8))

        assert scale == 1.0
        assert np.all(chw == 200)

    def test_la_escala_deshace_el_letterbox(self):
        """El invariante del que depende `boxes_xyxy /= scale` en detect():
        una caja ubicada en el frame original tiene que volver a su lugar
        después de dividir por la escala."""
        frame = np.full((640, 480, 3), 10, dtype=np.uint8)
        frame[100:200, 50:150] = 255
        chw, scale = _preprocess(frame)

        marcado = np.argwhere(chw[0] >= 200)  # (fila, columna) en la imagen 416
        y0, x0 = marcado.min(axis=0)
        y1, x1 = marcado.max(axis=0)

        # De vuelta a píxeles del frame original. Bordes inclusivos (el
        # slice de numpy es exclusivo) y tolerancia de 3px porque el
        # reescalado bilineal difumina el borde del rectángulo.
        assert x0 / scale == pytest.approx(50, abs=3)
        assert y0 / scale == pytest.approx(100, abs=3)
        assert x1 / scale == pytest.approx(149, abs=3)
        assert y1 / scale == pytest.approx(199, abs=3)


class TestDecodeOutputs:
    def _salida_cruda(self) -> np.ndarray:
        """Salida del modelo con todo en cero: el centro cae justo sobre la
        celda de la grilla y el tamaño es exp(0)=1 por el stride."""
        return np.zeros((1, TOTAL_ANCHORS, 85), dtype=np.float32)

    def test_la_grilla_cubre_las_tres_cabezas(self):
        assert ANCHORS_POR_CABEZA == [2704, 676, 169]
        assert TOTAL_ANCHORS == 3549

    def test_cada_cabeza_decodifica_con_su_stride(self):
        decoded = _decode_outputs(self._salida_cruda())[0]

        primera_de_cada_cabeza = [
            0,
            ANCHORS_POR_CABEZA[0],
            ANCHORS_POR_CABEZA[0] + ANCHORS_POR_CABEZA[1],
        ]
        for anchor, stride in zip(primera_de_cada_cabeza, STRIDES, strict=True):
            cx, cy, w, h = decoded[anchor][:4]
            assert (cx, cy) == (0.0, 0.0)  # celda (0,0) de esa cabeza
            assert (w, h) == (stride, stride)  # exp(0) * stride

    def test_la_grilla_avanza_primero_en_x(self):
        """El orden del meshgrid importa: si se invirtieran x e y, las cajas
        saldrían transpuestas y nada lo detectaría salvo mirando el video."""
        decoded = _decode_outputs(self._salida_cruda())[0]
        stride = STRIDES[0]
        ancho_grilla = INPUT_SIZE[1] // stride

        assert tuple(decoded[1][:2]) == (stride, 0.0)  # celda (1,0)
        assert tuple(decoded[ancho_grilla][:2]) == (0.0, stride)  # celda (0,1)

    def test_el_ultimo_anchor_cae_en_la_esquina_de_la_cabeza_gruesa(self):
        decoded = _decode_outputs(self._salida_cruda())[0]
        stride = STRIDES[-1]
        ultima_celda = INPUT_SIZE[0] // stride - 1

        assert tuple(decoded[-1][:2]) == (ultima_celda * stride, ultima_celda * stride)

    def test_el_tamano_es_exponencial(self):
        outputs = self._salida_cruda()
        outputs[0, 0, 2:4] = np.log(4.0)  # exp(log 4) = 4 celdas de ancho

        decoded = _decode_outputs(outputs)[0]

        assert decoded[0][2] == pytest.approx(4.0 * STRIDES[0])

    def test_muta_el_array_de_entrada(self):
        """Documenta un filo: _decode_outputs escribe sobre el array que
        recibe. Llamarlo dos veces sobre la misma salida la decodifica dos
        veces y las cajas se van a la loma."""
        outputs = self._salida_cruda()
        decoded = _decode_outputs(outputs)

        assert decoded is outputs
        assert _decode_outputs(outputs)[0][1][0] != STRIDES[0]


class TestNms:
    def test_suprime_la_caja_de_menor_score_cuando_se_superponen(self):
        boxes = np.array([[0.0, 0.0, 10.0, 10.0], [0.0, 0.0, 10.0, 10.0]])
        scores = np.array([0.9, 0.5])

        assert _nms(boxes, scores, 0.5) == [0]

    def test_conserva_cajas_disjuntas_ordenadas_por_score(self):
        boxes = np.array([[0.0, 0.0, 10.0, 10.0], [100.0, 100.0, 110.0, 110.0]])
        scores = np.array([0.5, 0.9])

        assert _nms(boxes, scores, 0.5) == [1, 0]

    def test_el_umbral_decide_en_un_solape_parcial(self):
        """Solape real de estas dos cajas: IoU ~= 0.175 (con la convención
        +1 de areas que usa el demo oficial de YOLOX)."""
        boxes = np.array([[0.0, 0.0, 10.0, 10.0], [5.0, 5.0, 15.0, 15.0]])
        scores = np.array([0.9, 0.8])

        assert _nms(boxes, scores, 0.10) == [0]  # suprime
        assert _nms(boxes, scores, 0.50) == [0, 1]  # no suprime


class TestMulticlassNms:
    def _dos_cajas_superpuestas(self) -> np.ndarray:
        return np.array([[0.0, 0.0, 10.0, 10.0], [1.0, 1.0, 11.0, 11.0]])

    def test_sin_nada_sobre_el_umbral_devuelve_none(self):
        boxes = self._dos_cajas_superpuestas()
        scores = np.full((2, 3), 0.3)

        assert _multiclass_nms(boxes, scores, 0.5, score_thr=0.5) is None

    def test_toma_la_clase_de_mayor_score_de_cada_caja(self):
        boxes = np.array([[0.0, 0.0, 10.0, 10.0]])
        scores = np.array([[0.1, 0.2, 0.85]])

        dets = _multiclass_nms(boxes, scores, 0.5, score_thr=0.5)

        assert dets.shape == (1, 6)  # x1,y1,x2,y2,score,clase
        assert dets[0][4] == pytest.approx(0.85)
        assert int(dets[0][5]) == 2

    def test_es_class_agnostic(self):
        """Dos cajas superpuestas de CLASES DISTINTAS: igual se suprime una.
        Es lo que hace multiclass_nms_class_agnostic del demo oficial, y es
        deliberado -- una persona y una moto no ocupan el mismo pixel."""
        boxes = self._dos_cajas_superpuestas()
        scores = np.array([[0.9, 0.0], [0.0, 0.8]])

        dets = _multiclass_nms(boxes, scores, nms_thr=0.5, score_thr=0.5)

        assert dets.shape == (1, 6)
        assert int(dets[0][5]) == 0  # sobrevive la de mayor score

    def test_cajas_lejanas_de_distinta_clase_sobreviven_las_dos(self):
        boxes = np.array([[0.0, 0.0, 10.0, 10.0], [100.0, 100.0, 110.0, 110.0]])
        scores = np.array([[0.9, 0.0], [0.0, 0.8]])

        dets = _multiclass_nms(boxes, scores, nms_thr=0.5, score_thr=0.5)

        assert dets.shape == (2, 6)
        assert sorted(int(d[5]) for d in dets) == [0, 1]


class FakeSession:
    """Doble de onnxruntime.InferenceSession: devuelve una salida cruda
    fabricada, con una sola caja en un anchor conocido."""

    def __init__(self, outputs: np.ndarray) -> None:
        self._outputs = outputs
        self.entradas: list[np.ndarray] = []

    def run(self, _output_names, feed):
        self.entradas.append(next(iter(feed.values())))
        return [self._outputs.copy()]


def _salida_con_una_caja(
    *, cx: float, cy: float, lado: float, clase: int, score: float
) -> np.ndarray:
    """Salida cruda del modelo con una única detección: centro y tamaño en
    píxeles de la imagen 416, expresados como los espera _decode_outputs
    (offset dentro de la celda + log del tamaño en celdas)."""
    outputs = np.zeros((1, TOTAL_ANCHORS, 85), dtype=np.float32)

    stride = STRIDES[-1]  # la cabeza gruesa: 13x13
    ancho_grilla = INPUT_SIZE[1] // stride
    celda_x, celda_y = int(cx // stride), int(cy // stride)
    anchor = ANCHORS_POR_CABEZA[0] + ANCHORS_POR_CABEZA[1] + celda_y * ancho_grilla + celda_x

    outputs[0, anchor, 0] = cx / stride - celda_x
    outputs[0, anchor, 1] = cy / stride - celda_y
    outputs[0, anchor, 2] = np.log(lado / stride)
    outputs[0, anchor, 3] = np.log(lado / stride)
    outputs[0, anchor, 4] = 1.0  # objectness
    outputs[0, anchor, 5 + clase] = score
    return outputs


def _detector_con_sesion(outputs: np.ndarray) -> tuple[YoloxDetector, FakeSession]:
    """YoloxDetector sin cargar el modelo: __init__ abriría una
    InferenceSession real de 20MB."""
    detector = object.__new__(YoloxDetector)
    session = FakeSession(outputs)
    detector._session = session
    detector._input_name = "images"
    return detector, session


class TestDetectDePuntaAPunta:
    """El camino completo preprocess -> sesión -> decode -> NMS -> des-letterbox,
    con la sesión reemplazada. Es el test que hubiera cazado una regresión en
    cualquiera de esos cuatro pasos: una caja conocida tiene que volver al
    píxel correcto del frame ORIGINAL, no del 416x416."""

    def _frame(self) -> np.ndarray:
        return np.full((640, 480, 3), 10, dtype=np.uint8)

    def test_la_caja_vuelve_a_coordenadas_del_frame_original(self):
        persona = COCO_CLASSES.index("person")
        outputs = _salida_con_una_caja(cx=208.0, cy=208.0, lado=64.0, clase=persona, score=0.9)
        detector, _session = _detector_con_sesion(outputs)
        frame = self._frame()
        _chw, scale = _preprocess(frame)

        detections = detector.detect(frame, ["person"], 0.3)

        assert len(detections) == 1
        det = detections[0]
        assert det.label == "person"
        assert det.confidence == pytest.approx(0.9, abs=1e-5)
        # (208-32, 208-32) con lado 64 en la imagen 416, dividido por la escala.
        esperado = (176 / scale, 176 / scale, 64 / scale, 64 / scale)
        assert det.bbox == pytest.approx(esperado, abs=1)

    def test_la_caja_cae_dentro_del_frame(self):
        """Una regresión del letterbox se manifiesta justo así: coordenadas
        fuera del frame, o corridas medio relleno."""
        persona = COCO_CLASSES.index("person")
        outputs = _salida_con_una_caja(cx=208.0, cy=208.0, lado=64.0, clase=persona, score=0.9)
        detector, _session = _detector_con_sesion(outputs)
        frame = self._frame()
        alto, ancho = frame.shape[:2]

        x, y, w, h = detector.detect(frame, ["person"], 0.3)[0].bbox

        assert x >= 0 and y >= 0
        assert x + w <= ancho
        assert y + h <= alto

    def test_el_allowlist_filtra_por_clase(self):
        persona = COCO_CLASSES.index("person")
        outputs = _salida_con_una_caja(cx=208.0, cy=208.0, lado=64.0, clase=persona, score=0.9)
        detector, _session = _detector_con_sesion(outputs)

        assert detector.detect(self._frame(), ["car"], 0.3) == []
        assert len(detector.detect(self._frame(), ["person", "car"], 0.3)) == 1

    def test_el_umbral_de_confianza_se_aplica_por_llamada(self):
        persona = COCO_CLASSES.index("person")
        outputs = _salida_con_una_caja(cx=208.0, cy=208.0, lado=64.0, clase=persona, score=0.4)
        detector, _session = _detector_con_sesion(outputs)

        assert detector.detect(self._frame(), ["person"], 0.3) != []
        assert detector.detect(self._frame(), ["person"], 0.5) == []

    def test_a_la_sesion_le_llega_el_tensor_con_batch(self):
        detector, session = _detector_con_sesion(np.zeros((1, TOTAL_ANCHORS, 85), dtype=np.float32))

        detector.detect(self._frame(), ["person"], 0.3)

        assert session.entradas[0].shape == (1, 3, INPUT_SIZE[0], INPUT_SIZE[1])

    def test_sin_nada_sobre_el_umbral_devuelve_lista_vacia(self):
        detector, _session = _detector_con_sesion(
            np.zeros((1, TOTAL_ANCHORS, 85), dtype=np.float32)
        )

        assert detector.detect(self._frame(), ["person"], 0.3) == []
