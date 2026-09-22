"""Tests de la resolución de modelos .onnx.

El docstring de core/analytics/model_assets.py promete que "la demo puede
correr SIN internet, así que el download es el último recurso". Hasta hoy
esa promesa no la sostenía nada: con AUREA_DATA_DIR redirigido los dos
modelos se bajaban de internet, porque el paso "copiar del bundle"
resolvía contra PROJECT_ROOT/models y los .onnx del repo viven en
PROJECT_ROOT/data/models. Se reprodujo en el smoke del 2026-09-22.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aurea_vms.core.analytics import model_assets

YUNET = "face_detection_yunet_2023mar.onnx"  # 228KB: el liviano de los dos


@pytest.fixture()
def data_dir_vacio(tmp_path, monkeypatch):
    """Data-dir redirigido, como el del smoke del CI de Windows."""
    monkeypatch.setattr(model_assets, "settings", SimpleNamespace(data_dir=tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _sin_internet(monkeypatch):
    """Cualquier descarga es una falla del test: la demo tiene que arrancar
    sin conexión."""

    def explota(*_args, **_kwargs):
        raise AssertionError("ensure_model intentó descargar de internet")

    monkeypatch.setattr(model_assets.urllib.request, "urlretrieve", explota)


class TestEnsureModel:
    def test_con_el_data_dir_redirigido_toma_el_modelo_del_repo(self, data_dir_vacio):
        ruta = model_assets.ensure_model(YUNET, "http://no-se-debe-usar")

        copiado = data_dir_vacio / "models" / YUNET
        assert ruta == str(copiado)
        assert copiado.exists()
        assert copiado.stat().st_size == (model_assets.REPO_MODELS_DIR / YUNET).stat().st_size

    def test_si_ya_esta_en_el_data_dir_no_lo_vuelve_a_copiar(self, data_dir_vacio):
        ya_estaba = data_dir_vacio / "models" / YUNET
        ya_estaba.parent.mkdir(parents=True)
        ya_estaba.write_bytes(b"modelo de mentira")

        ruta = model_assets.ensure_model(YUNET, "http://no-se-debe-usar")

        assert ruta == str(ya_estaba)
        assert ya_estaba.read_bytes() == b"modelo de mentira"  # intacto

    def test_el_bundle_tiene_prioridad_sobre_el_repo(self, data_dir_vacio, tmp_path, monkeypatch):
        """Empaquetado, el modelo bueno es el del bundle: PROJECT_ROOT no
        significa nada dentro del .exe."""
        bundle = tmp_path / "bundle" / "models"
        bundle.mkdir(parents=True)
        (bundle / YUNET).write_bytes(b"el del bundle")
        monkeypatch.setattr(
            model_assets.resources, "bundled_path", lambda rel: tmp_path / "bundle" / rel
        )

        model_assets.ensure_model(YUNET, "http://no-se-debe-usar")

        assert (data_dir_vacio / "models" / YUNET).read_bytes() == b"el del bundle"

    def test_sin_modelo_en_ningun_lado_recien_ahi_descarga(self, data_dir_vacio, monkeypatch):
        """El paso 3 sigue existiendo para un clon fresco sin los .onnx."""
        monkeypatch.setattr(model_assets, "REPO_MODELS_DIR", data_dir_vacio / "no-existe")
        descargas: list[tuple] = []
        monkeypatch.setattr(
            model_assets.urllib.request,
            "urlretrieve",
            lambda url, dest: descargas.append((url, dest)),
        )

        model_assets.ensure_model("inventado.onnx", "http://ejemplo/inventado.onnx")

        assert descargas == [
            ("http://ejemplo/inventado.onnx", data_dir_vacio / "models" / "inventado.onnx")
        ]
