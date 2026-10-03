# Rig de demo — cámaras RTSP falsas + seed

Simula cámaras IP sirviendo clips en loop por RTSP (una por cada
`media/camN.mp4`: hoy 7), y deja la base
lista con sitios, cámaras, analíticas, reglas de alarma y usuarios por
rol. Sirve para:

- Desarrollar y ensayar sin cámaras físicas (también en Linux).
- **Plan B en vivo**: si las cámaras reales fallan el día de la demo,
  se apunta la app a `127.0.0.1` y la demo sigue idéntica.

## Requisitos

- [mediamtx](https://github.com/bluenviron/mediamtx/releases) (servidor
  RTSP, un solo binario, sin instalación).
- `ffmpeg` en el PATH.
- Clips `cam1.mp4`, `cam2.mp4`, … en `tools/demo/media/` (carpeta
  gitignorada). Cada uno sale como `rtsp://127.0.0.1:8554/camN`; mediamtx
  escucha en todas las direcciones locales, así que cada cámara simulada
  usa su propia IP de loopback (son distintas para que no choquen como
  dispositivos). En la base de desarrollo están dadas de alta así (el seed,
  en cambio, crea solo `cam1`-`cam4`, todas en 127.0.0.1 y con las
  analíticas que hoy están ocultas):

  | Clip | Dispositivo | URL que tiene dada de alta |
  |---|---|---|
  | `cam1.mp4` | #3 Sabotaje de máquina | `rtsp://127.0.0.1:8554/cam1` |
  | `cam2.mp4` | #4 Sabotaje de ticket | `rtsp://127.0.0.2:8554/cam2` |
  | `cam3.mp4` | #5 Monitor roto | `rtsp://127.0.0.3:8554/cam3` |
  | `cam4.mp4` | #6 Ruleta AR 4171 | `rtsp://127.0.0.4:8554/cam4` |
  | `cam5.mp4` | #8 Consumo de sustancias | `rtsp://127.0.0.5:8554/cam5` |
  | `cam6.mp4` | #9 Sala ESP SUB 08-5 | `rtsp://127.0.0.6:8554/cam6` |
  | `cam7.mp4` | #10 Ruleta RA-04 | `rtsp://127.0.0.7:8554/cam7` |

- **Se publican sin recodificar** (`-c:v copy`).
  - Los clips son H.264 con un cuadro clave cada 2 s (medido el 2026-10-02),
    así que un cliente ve el primer cuadro en ~2 s.
  - Publicar las 7 cuesta ~0 % de CPU por cámara (mediamtx ~9 %). Antes se
    recodificaba cada una con libx264, y con varias de 2048×1536 se comía el
    CPU de las analíticas.
  - Un clip nuevo tiene que ser H.264 con cuadros clave seguidos. Si no,
    convertirlo una vez (`ffmpeg -i in.mkv -c:v libx264 -g 50 -an camN.mp4`)
    o publicar con `REENCODE=1`.

## Uso

```bash
# 1. Todas las cámaras falsas (Linux/macOS). Si no hay servidor RTSP en
#    RTSP_HOST, levanta el mediamtx de esta carpeta y lo baja al salir.
#    Una publicación que se corta se relanza sola. Ctrl+C detiene todo.
tools/demo/start_fake_cams.sh
#    o en Windows (levanta mediamtx.exe si está al lado y no corre):
tools\demo\start_fake_cams.bat

# 2. Seed de la base (idempotente: se puede correr mil veces)
python tools/demo/seed_demo.py

# 3. La app
python -m aurea_vms.main
```

Usuarios que crea el seed (contraseña de demo para todos: `Aurea123!x`):
`admin` (Administrador), `supervisor`, `operador`, `auditor`.

Variables útiles: `CLIPS_DIR` (carpeta de clips), `RTSP_HOST`
(default `127.0.0.1:8554`) y `REENCODE=1` (recodificar con libx264).
