"""Recursos empaquetados: resolucion de rutas bajo PyInstaller vs dev.

PyInstaller expone la raiz del bundle en sys._MEIPASS (en onedir apunta
al directorio _internal junto al .exe). Los datas del spec (modelos
.onnx, WSDL de ONVIF, assets) se resuelven contra esa raiz; en
desarrollo, contra la raiz del repo -- ojo que en dev los modelos del
repo NO estan en PROJECT_ROOT/models sino en PROJECT_ROOT/data/models, y
por eso `model_assets.ensure_model` prueba los dos lugares.
"""

from __future__ import annotations

import sys
from pathlib import Path

from aurea_vms.config.settings import PROJECT_ROOT


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return base / relative


def onvif_camera_kwargs() -> dict:
    """kwargs extra para ONVIFCamera. onvif-zeep carga sus WSDL desde
    site-packages/wsdl, que no existe dentro de un bundle: el spec los
    empaqueta como datas en "wsdl" y aca se apunta ahi. En dev no se
    pasa nada (usa su default)."""
    if not is_frozen():
        return {}
    wsdl = bundled_path("wsdl")
    return {"wsdl_dir": str(wsdl)} if wsdl.exists() else {}
