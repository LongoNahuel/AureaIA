"""Resolucion de los modelos .onnx (YOLOX-Tiny, YuNet) que usan los
analizadores.

Orden de resolucion (la demo puede correr SIN internet, asi que el
download es el ultimo recurso, no el camino normal):

1. Ya esta en <data_dir>/models/ (dev: los pesa el repo en data/models/;
   frozen: quedo copiado de una corrida anterior).
2. Esta en el bundle de PyInstaller (frozen) o en el repo (dev) -> se
   copia al data_dir.
3. Descarga desde el repo de origen del modelo (releases de YOLOX,
   OpenCV Zoo): solo dev recien clonado sin modelos, o bundle roto.

El paso 2 mira DOS lugares a proposito. `bundled_path` resuelve contra la
raiz del bundle, que en dev es PROJECT_ROOT, pero los modelos versionados
viven en PROJECT_ROOT/data/models/. Con un solo candidato, cualquier
corrida con AUREA_DATA_DIR redirigido -- el smoke del workflow de Windows,
sin ir mas lejos, que usa un data-dir vacio -- se caia al paso 3 y bajaba
20MB de internet, en contra de la promesa de arriba.
"""

from __future__ import annotations

import logging
import shutil
import urllib.request

from aurea_vms.config import resources
from aurea_vms.config.settings import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

# Los .onnx versionados en el repo (20MB YOLOX + 228KB YuNet).
REPO_MODELS_DIR = PROJECT_ROOT / "data" / "models"


def ensure_model(filename: str, url: str) -> str:
    model_path = settings.data_dir / "models" / filename
    if model_path.exists():
        return str(model_path)

    model_path.parent.mkdir(parents=True, exist_ok=True)

    # El primero acierta cuando corre empaquetado (datas del spec); el
    # segundo, en desarrollo, donde los .onnx versionados viven bajo data/.
    for source in (resources.bundled_path(f"models/{filename}"), REPO_MODELS_DIR / filename):
        if source.exists():
            shutil.copy2(source, model_path)
            logger.info("Modelo %s copiado desde %s", filename, source.parent)
            return str(model_path)

    logger.warning("Modelo %s ausente: descargando de %s", filename, url)
    urllib.request.urlretrieve(url, model_path)  # noqa: S310 - URL fija, pasada por el caller
    return str(model_path)
