from __future__ import annotations

import numpy as np

from aurea_vms.core.analytics.base import (
    MAX_ANALYSIS_DIMENSION,
    bbox_center_in_roi,
    crop_to_roi,
    rescale_bbox,
    resize_for_inference,
)


class TestResizeForInference:
    def test_imagen_grande_se_reduce_al_lado_maximo(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        resized, scale = resize_for_inference(frame)

        assert scale == MAX_ANALYSIS_DIMENSION / 1280
        assert resized.shape[1] == MAX_ANALYSIS_DIMENSION
        assert resized.shape[0] == round(720 * scale)

    def test_imagen_chica_queda_igual(self):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        resized, scale = resize_for_inference(frame)

        assert scale == 1.0
        assert resized is frame

    def test_imagen_vertical_usa_el_lado_mayor(self):
        frame = np.zeros((1280, 720, 3), dtype=np.uint8)
        resized, _scale = resize_for_inference(frame)
        assert resized.shape[0] == MAX_ANALYSIS_DIMENSION


class TestRescaleBbox:
    def test_deshace_escala_y_suma_offset(self):
        assert rescale_bbox((10, 20, 30, 40), inv_scale=2.0, offset_x=5, offset_y=7) == (
            25,
            47,
            60,
            80,
        )

    def test_identidad(self):
        assert rescale_bbox((10, 20, 30, 40), inv_scale=1.0) == (10, 20, 30, 40)

    def test_roundtrip_con_resize(self):
        """Un bbox detectado sobre la imagen reescalada debe volver a
        coordenadas del frame nativo con error de a lo sumo el redondeo."""
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        _resized, scale = resize_for_inference(frame)

        bbox_nativo = (100, 200, 60, 80)
        bbox_modelo = tuple(round(v * scale) for v in bbox_nativo)
        recuperado = rescale_bbox(bbox_modelo, inv_scale=1.0 / scale)

        for original, vuelto in zip(bbox_nativo, recuperado, strict=True):
            assert abs(original - vuelto) <= 2


class TestCropToRoi:
    def test_sin_roi_devuelve_el_frame_entero(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        crop, ox, oy = crop_to_roi(frame, None)

        assert crop is frame
        assert (ox, oy) == (0, 0)

    def test_con_roi_recorta_y_devuelve_offsets(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        crop, ox, oy = crop_to_roi(frame, (10, 20, 50, 40))

        assert crop.shape == (40, 50, 3)
        assert (ox, oy) == (10, 20)


class TestBboxCenterInRoi:
    def test_centro_dentro(self):
        assert bbox_center_in_roi((10, 10, 20, 20), (0, 0, 50, 50)) is True

    def test_centro_fuera(self):
        assert bbox_center_in_roi((100, 100, 20, 20), (0, 0, 50, 50)) is False

    def test_bbox_grande_con_centro_dentro(self):
        # Solo importa el centro, no que el bbox entre completo en el ROI.
        assert bbox_center_in_roi((0, 0, 100, 100), (40, 40, 20, 20)) is True
