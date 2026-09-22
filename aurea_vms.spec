# -*- mode: python ; coding: utf-8 -*-
"""Spec de PyInstaller para el build de Windows (onedir).

onedir y NO onefile a proposito: el bundle ronda el GB descomprimido
(onnxruntime + PySide6 + opencv); onefile se autoextrae a %TEMP% en CADA
arranque (30-60s de espera, re-escaneo del antivirus, y muchas maquinas
corporativas bloquean ejecucion desde TEMP). onedir arranca rapido y se
distribuye como zip.

Build:  pyinstaller aurea_vms.spec --noconfirm
Smoke:  dist/AureaVMS/AureaVMS.exe --smoke   (con AUREA_DATA_DIR a un tmp)
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

import onvif

datas = [
    # Assets de la UI: icons.py resuelve Path(__file__).parent/"assets",
    # que bajo el bundle equivale a _internal/aurea_vms/ui/assets.
    ("aurea_vms/ui/assets", "aurea_vms/ui/assets"),
    # Modelos (.onnx de YuNet/YOLOX): model_assets.ensure_model() los
    # copia del bundle al data-dir del usuario en el primer arranque (la
    # maquina de demo puede no tener internet -- el download es solo
    # ultimo recurso).
    ("data/models", "models"),
    # Revisiones de Alembic: la app migra la base del cliente al arrancar
    # (models/db.py::migrar), asi que sin estos archivos el .exe no puede
    # aplicar un cambio de esquema -- y no hay nadie que corra
    # "alembic upgrade" a mano en una sala. El smoke del ejecutable es la
    # prueba de que llegaron: init_db() corre dentro de --smoke.
    ("aurea_vms/migrations", "aurea_vms/migrations"),
]

# WSDL de onvif-zeep: viven en site-packages/wsdl (fuera del paquete
# onvif) y sin ellos ONVIFCamera muere; resources.onvif_camera_kwargs()
# apunta a este directorio cuando corre frozen.
_wsdl_dir = Path(onvif.__file__).resolve().parent.parent / "wsdl"
if _wsdl_dir.exists():
    datas.append((str(_wsdl_dir), "wsdl"))

binaries = []
hiddenimports = collect_submodules("onvif") + ["zeep"]

# Alembic carga env.py y cada revision con importlib, por ruta: el analisis
# estatico de PyInstaller no ve ninguno de esos imports.
hiddenimports += collect_submodules("alembic") + ["logging.config"]

# onnxruntime: sus .dll/.pyd + datas internos cargan en runtime; sin
# collect_all la sesion de inferencia muere recien al crear el primer
# detector (misma leccion que dejo MediaPipe antes de sacarlo).
# cryptography va en la lista por la misma razon que onnxruntime: trae
# binarios nativos (_rust) que cargan en runtime, y sin ellos el primer
# intento de descifrar una credencial muere en el .exe y no antes.
for package in ("onnxruntime", "qfluentwidgets", "cryptography"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["aurea_vms/main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="AureaVMS",
    debug=False,
    strip=False,
    upx=False,
    # console=True hasta que el build este maduro: con consola se ven
    # los stack traces de arranque. Flip a False para la entrega final.
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AureaVMS",
)
