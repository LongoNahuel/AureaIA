# AureaIA VMS

VMS (Video Management System) de escritorio con analíticas de IA
embebidas, orientado a operación de seguridad en casinos y salas de
juego. Corre 100% local ("SuperCliente LAN"): descubre cámaras
ONVIF/RTSP en la red, muestra video en vivo multi-canal y ejecuta las
analíticas en CPU sobre la misma máquina, sin nube.

## Funcionalidad

- **Cámaras**: descubrimiento ONVIF (WS-Discovery + perfiles de medios),
  alta manual con plantillas RTSP, prueba de conexión, PTZ, reboot.
- **Vista en vivo**: grilla 1/4/9/16 tiles + vista inteligente, drag &
  drop desde el árbol de cámaras agrupado por sitio, sub-stream en
  grilla y main-stream al expandir.
- **4 analíticas** (MediaPipe/OpenCV, CPU): detección de movimiento
  (MOG2), conteo de personas, cruce de línea e intrusión, detección
  facial. ROI por analítica, umbrales y parámetros configurables.
- **Alarmas**: reglas por cámara o globales (clases, confianza mínima,
  horario — incluso rangos que cruzan medianoche—, cooldown, severidad),
  popups (críticos persisten hasta reconocer), notificación de
  escritorio, clip de evento con pre-buffer.
- **Incidentes**: estados nueva → reconocida → en investigación →
  resuelta, notas, exportación de evidencia.
- **Media indexada**: clips y capturas en `media/<tipo>/<fecha>/<cámara>/`
  con índice en DB (`media_assets`) — buscar nunca escanea el disco — y
  retención automática por edad y tamaño.
- **Multisitio**: sitios → cámaras, selector global que filtra toda la app.
- **Multiusuario**: 4 roles (Administrador, Supervisor, Operador,
  Auditor) con matriz de permisos.

## Stack

Python 3.11+ · PySide6 + QFluentWidgets · OpenCV (RTSP/FFmpeg) ·
MediaPipe Tasks (modelos .tflite incluidos en `data/models/`) ·
SQLAlchemy 2 + SQLite (portable a otro motor; ver ARQUITECTURA).

## Desarrollo

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m aurea_vms.main          # primera vez: wizard de Super Administrador
```

Sin cámaras físicas: usar el rig de cámaras RTSP falsas + seed —
ver [`tools/demo/README.md`](tools/demo/README.md).

### Calidad

```bash
ruff check aurea_vms tests && ruff format --check aurea_vms tests
pytest                    # unit (rápidos)
pytest -m integration     # cargan los modelos de IA reales
pytest --cov              # gate de cobertura (falla bajo el mínimo)
```

El CI de GitHub Actions corre lint + tests + cobertura en cada push/PR
a `main`. En Linux headless los tests usan `QT_QPA_PLATFORM=offscreen`;
MediaPipe necesita `libgles2` además de las libs de Qt (ver
`.github/workflows/ci.yml`).

## Documentación

- [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) — capas, threading, DB, pipeline de video.
- [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md) — checklist y guion para demos.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — deuda técnica y evolución prevista.
