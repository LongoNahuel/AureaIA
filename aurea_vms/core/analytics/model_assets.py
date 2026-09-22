"""Resolucion de los modelos .onnx (YOLOX-Tiny, YuNet) que usan los
analizadores.

Orden de resolucion (la demo puede correr SIN internet, asi que el
download es el ultimo recurso, no el camino normal):

1. Ya esta en <data_dir>/models/ (dev: los pesa el repo en data/models/;
   frozen: quedo copiado de una corrida anterior).
2. Viene empaquetado en el bundle de PyInstaller -> se copia al data_dir.
3. Descarga desde el repo de origen del modelo (releases de YOLOX,
   OpenCV Zoo): solo dev recien clonado sin modelos, o bundle roto.

OJO (ver ROADMAP): el paso 2 nunca acierta en desarrollo, porque
`bundled_path` resuelve contra PROJECT_ROOT y los modelos del repo viven
en PROJECT_ROOT/data/models/. Sin AUREA_DATA_DIR no se nota (el paso 1
los encuentra), pero con el data-dir redirigido y sin internet las
analiticas no arrancan.
"""

from __future__ import annotations

import logging
import shutil
import urllib.request

from aurea_vms.config import resources
from aurea_vms.config.settings import settings

logger = logging.getLogger(__name__)


def ensure_model(filename: str, url: str) -> str:
    model_path = settings.data_dir / "models" / filename
    if model_path.exists():
        return str(model_path)

    model_path.parent.mkdir(parents=True, exist_ok=True)

    bundled = resources.bundled_path(f"models/{filename}")
    if bundled.exists():
        shutil.copy2(bundled, model_path)
        logger.info("Modelo %s copiado desde el bundle", filename)
        return str(model_path)

    logger.warning("Modelo %s ausente: descargando de %s", filename, url)
    urllib.request.urlretrieve(url, model_path)  # noqa: S310 - URL fija, pasada por el caller
    return str(model_path)
