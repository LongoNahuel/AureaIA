"""Opciones de la sesion ONNX compartida (Fase P, 2026-10-08).

Los hilos intra-op de onnxruntime giran esperando trabajo por default. Con
las analiticas a 5 fps eso quemaba entre un 35 y un 47 % del CPU del proceso
sin inferir mas rapido (medicion en sesiones/2026-10-08.md).

Sin modelo: se captura lo que `_crear_sesion` le pasa a `InferenceSession`.
Va aparte de test_sesion_onnx.py, cuya fixture autouse reemplaza
`_crear_sesion` entera.
"""

from __future__ import annotations

import aurea_vms.core.analytics.object_detector_backend as odb


def _opciones_de_la_sesion(monkeypatch):
    capturadas = {}

    def sesion_falsa(ruta, sess_options, providers):
        capturadas.update(ruta=ruta, opciones=sess_options, providers=providers)
        return object()

    monkeypatch.setattr(odb.onnxruntime, "InferenceSession", sesion_falsa)
    odb._crear_sesion("/modelos/yolox.onnx")
    return capturadas


def test_los_hilos_no_giran_esperando_trabajo(monkeypatch):
    opciones = _opciones_de_la_sesion(monkeypatch)["opciones"]

    assert opciones.get_session_config_entry(odb.ALLOW_SPINNING) == "0"


def test_se_mantienen_dos_hilos_intra_op_en_cpu(monkeypatch):
    capturadas = _opciones_de_la_sesion(monkeypatch)

    assert capturadas["opciones"].intra_op_num_threads == 2
    assert capturadas["providers"] == ["CPUExecutionProvider"]


def test_es_una_clave_que_onnxruntime_conoce():
    """Una clave mal escrita no da error: onnxruntime la ignora en
    silencio. Se fija el nombre documentado."""
    assert odb.ALLOW_SPINNING == "session.intra_op.allow_spinning"
