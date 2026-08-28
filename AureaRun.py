"""Lanzador de AureaIA VMS -- corre bien sin importar desde donde se
ejecute (doble click, "Run" del editor, o `python AureaRun.py` parado en
cualquier carpeta). Se para en la raiz del proyecto y arma sys.path con
esta carpeta antes de importar aurea_vms, asi el paquete se encuentra
aunque no este instalado (`pip install -e .`) en el entorno activo.

Equivalente a `python -m aurea_vms.main`, pero sin depender de estar
parado en la raiz del repo ni de tener el paquete instalado.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aurea_vms.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
