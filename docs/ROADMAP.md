# Roadmap / deuda técnica

Priorizado. Lo marcado 🔴 debería resolverse antes de instalar en un
cliente real.

## Seguridad

- 🔴 **Credenciales de cámara en texto plano** (`devices.password`).
  Cifrar en reposo (clave derivada de una master key local o DPAPI en
  Windows) o al menos mover el data-dir a un perfil con ACLs. Nota: la
  auth de usuarios ya es correcta (PBKDF2 260k + salt).
- Rate-limit / lockout de intentos de login.

## Empaquetado y despliegue (F6)

- `settings.py` → `%LOCALAPPDATA%/AureaVMS` cuando corre frozen
  (`sys.frozen`), con override `AUREA_DATA_DIR`; hoy escribe dentro del
  árbol del proyecto (fatal bajo Program Files).
- Spec de PyInstaller **onedir** + workflow `windows-latest` con smoke
  test (`--smoke` debe crear un detector real de MediaPipe, no solo
  importarlo — las DLL cargan tarde; lección del CI con libgles2).
  WSDL de onvif-zeep y `.tflite` como datas; `collect_all` de mediapipe
  y qfluentwidgets. Máquina destino: VC++ Redistributable.
- Assets de marca (logo/fondos/videos): los tiene Nahuel; el
  `.gitignore` ya tiene la excepción `!aurea_vms/ui/assets/**` para
  commitearlos.

## Datos

- **Alembic** cuando el esquema se asiente (hoy: DB descartable +
  guard ad-hoc para `devices.site_id`).
- Migrar timestamps float → DateTime UTC unificado.
- Si aparece multisede real con servidor central: nodo central en
  PostgreSQL (la capa SQLAlchemy ya es portable), grabadores por sitio
  en SQLite.
- `users.custom_permissions JSON` que overridee el rol (matriz editable
  por usuario en la UI).

## Video / analíticas

- Clips: re-encodear a H.264 (PyAV/imageio-ffmpeg) — hoy mp4v a 5 fps
  con doble recompresión JPEG; usar los timestamps reales guardados.
- Grabación continua en anillo (el `kind="recording"` de `media_assets`
  ya está reservado).
- Reproductor embebido (hoy abre el reproductor del SO) y captura
  manual con `created_by`.
- Compartir la instancia de EfficientDet entre analíticas de la misma
  cámara (hoy: un modelo de 12 MB por analítica).
- `analytics_fps` por cámara (hoy global). Delegate GPU de MediaPipe si
  el hardware de sala lo permite.
- Tracker: evaluar ByteTrack-lite si el conteo en escenas densas lo pide.
- Reconocimiento facial real (hoy solo detección; la galería usa una
  firma de similitud, no un embedding).

## Rendimiento UI

- Dashboard: reemplazar el fetch de 500 eventos cada 5 s por consultas
  COUNT agregadas.
- Módulo Alarmas: cachear nombres de cámara en la recarga (hoy un
  `get_device()` por fila). La media ya se resuelve en una sola query.

## Producto (ideas de NOVA a evaluar)

- Design tokens de severidad en `ui/theme.py` + números tabulares.
- "Legajo de evidencia" en PDF (QPrinter) con el spec del prototipo.
- Zonas poligonales con roles (hoy: ROI rectangular + línea).
- Jerarquía completa Organización → Sitios → Zonas → Cámaras (hoy:
  Sitios → Cámaras).
