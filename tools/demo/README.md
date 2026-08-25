# Rig de demo — cámaras RTSP falsas + seed

Simula 4 cámaras IP sirviendo clips en loop por RTSP, y deja la base
lista con sitios, cámaras, analíticas, reglas de alarma y usuarios por
rol. Sirve para:

- Desarrollar y ensayar sin cámaras físicas (también en Linux).
- **Plan B en vivo**: si las cámaras reales fallan el día de la demo,
  se apunta la app a `127.0.0.1` y la demo sigue idéntica.

## Requisitos

- [mediamtx](https://github.com/bluenviron/mediamtx/releases) (servidor
  RTSP, un solo binario, sin instalación).
- `ffmpeg` en el PATH.
- 4 clips `cam1.mp4` … `cam4.mp4` en `tools/demo/media/` (carpeta
  gitignorada). Sugerencia de mapeo con los clips stock de casino del
  repo NOVA (`Aurea_DEMO_Lean/assets/cams/`):

  | Archivo destino | Clip sugerido | Analítica que luce |
  |---|---|---|
  | `cam1.mp4` | `mesas-01.mp4` | Conteo de personas |
  | `cam2.mp4` | `acceso-hall.mp4` | Cruce de línea |
  | `cam3.mp4` | `boveda-acceso.mp4` | Detección facial |
  | `cam4.mp4` | `zonasur-sala.mp4` | Detección de movimiento |

## Uso

```bash
# 1. Servidor RTSP (terminal aparte; config opcional incluida)
./mediamtx tools/demo/mediamtx.yml

# 2. Las 4 cámaras falsas (Linux/macOS)
tools/demo/start_fake_cams.sh
#    o en Windows:
tools\demo\start_fake_cams.bat

# 3. Seed de la base (idempotente: se puede correr mil veces)
python tools/demo/seed_demo.py

# 4. La app
python -m aurea_vms.main
```

Usuarios que crea el seed (contraseña de demo para todos: `Aurea123!x`):
`admin` (Administrador), `supervisor`, `operador`, `auditor`.

Variables útiles: `CLIPS_DIR` (carpeta de clips), `RTSP_HOST`
(default `127.0.0.1:8554`).
