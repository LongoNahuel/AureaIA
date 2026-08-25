# Build del .exe de Windows

## Camino normal: GitHub Actions

1. En GitHub → Actions → **Build Windows** → *Run workflow* (o pushear
   un tag `vX.Y.Z`).
2. Al terminar, bajar el artifact **AureaVMS-win64** (zip con la carpeta
   `AureaVMS/`, dentro está `AureaVMS.exe`).
3. En la máquina destino: descomprimir donde sea (no requiere
   instalación) y ejecutar `AureaVMS.exe`. Los datos (DB, media, logs)
   van a `%LOCALAPPDATA%\AureaVMS`.

Requisito de la máquina destino: **Visual C++ Redistributable 2015+**
(lo piden mediapipe/opencv; en Windows 10/11 actualizado suele estar).

## Plan B: compilar a mano en una máquina Windows

```bat
git clone git@github.com:LongoNahuel/AureaIA.git && cd AureaIA
py -3.12 -m venv .venv && .venv\Scripts\activate
pip install -e . pyinstaller
python -m aurea_vms.main --smoke      &rem valida el entorno completo
pyinstaller aurea_vms.spec --noconfirm
dist\AureaVMS\AureaVMS.exe --smoke    &rem valida el bundle
```

El resultado queda en `dist\AureaVMS\`.

## Notas

- El spec es **onedir** a propósito (onefile se autoextrae ~1 GB a
  %TEMP% en cada arranque; lento y conflictivo con antivirus/políticas).
- `console=True` mientras el build madura (se ven los errores de
  arranque); flip a `False` en `aurea_vms.spec` para la entrega final.
- El smoke (`--smoke`) crea los 4 analizadores con los modelos reales y
  procesa un frame: es la única forma de validar que las DLL de
  MediaPipe quedaron bien empaquetadas (cargan tarde — importar el
  módulo no alcanza).
- `AUREA_DATA_DIR` (env) redirige el directorio de datos (útil para
  probar sin tocar `%LOCALAPPDATA%`).
