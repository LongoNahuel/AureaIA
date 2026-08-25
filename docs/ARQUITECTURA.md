# Arquitectura

Cliente de escritorio monolítico (un proceso). Cuatro capas con una
regla dura: **`core/` y `models/` no conocen widgets; la UI no crea
threads de trabajo.**

```
aurea_vms/
├── config/    settings (paths, constantes de build)
├── models/    SQLAlchemy 2.0 + repository de funciones (sin relationship())
├── core/      engines en threads + lógica de negocio (sin Qt, salvo el bus)
└── ui/        PySide6 + QFluentWidgets (ventana única con pestañas)
```

## Concurrencia y comunicación

Todo hilo→UI pasa por **`core/event_bus.py`**: un `QObject` con señales
tipadas por dataclasses inmutables (`core/events.py`). Los módulos de UI
se conectan con `QueuedConnection`; los engines emiten desde sus threads
y jamás tocan widgets.

| Hilo | Qué hace | Archivo |
|---|---|---|
| `StreamWorker` (1 por cámara+calidad) | decodifica RTSP, guarda "último frame" + pre-buffer JPEG | `core/stream_manager.py` |
| `AnalyticsWorker` (1 por analítica activa) | muestrea a `analytics_fps` (5), corre el analizador, publica `DetectionEvent` | `core/analytics_engine.py` |
| `ClipWriter` (efímero) | pre-buffer + post-captura → mp4 + registro en `media_assets` | `core/clip_recorder.py` |
| `RetentionWorker` | purga media por edad/tamaño cada 30 min | `core/retention.py` |
| `FunctionWorker` (QThread) | I/O de red disparado desde la UI (probe RTSP, ONVIF) | `ui/workers.py` |
| Hilo principal | Qt + render de tiles vía QTimer a 25 fps | `ui/…` |

Puntos finos ya resueltos (no romper):

- `StreamManager` protege `_workers/_refcounts` con un `RLock` — UI y
  analíticas hacen `acquire/release` concurrentes. Ref-counting: misma
  cámara+calidad comparte conexión; "sub" sin sub-stream cae a "main".
- `VideoCapture` se abre con `OPEN/READ_TIMEOUT_MSEC=10s` y
  `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp` (seteado en
  `main.py` antes de cualquier captura): un `read()` colgado devuelve
  False y el loop de reconexión actúa de watchdog.
- Apagado ordenado (`main._stop_background_engines`):
  `clip_recorder.wait_for_pending()` **antes** de cortar streams (el
  post-buffer necesita el stream vivo); `AnalyticsEngine.stop()` hace
  join para que el `close()` de MediaPipe corra antes del teardown del
  intérprete; `stop_all()` de streams hace join para no acumular
  sockets zombies en logout→login.

## Analíticas

Interfaz pluggable `core/analytics/base.py` (`Analyzer.process_frame` →
`AnalysisResult`), registry/factory en `core/analytics/registry.py`.

- Movimiento: MOG2 + morfología, área mínima en % del cuadro, contorno
  simplificado para dibujar silueta.
- Conteo de personas y cruce de línea: EfficientDet-Lite2 (COCO) vía
  MediaPipe Tasks + `CentroidTracker` con histéresis (`min_hits`) y
  tolerancia a oclusiones (`max_age_s`).
- Rostros: BlazeFace full-range + filtros de distancia pupilar y ángulo.
- Preprocesado común: recorte a ROI **antes** de `resize_for_inference`
  (máx. 640 px de lado) y `rescale_bbox` para volver a coordenadas
  nativas.
- `Analyzer.close()` libera el modelo nativo (lo llama el worker al
  detenerse). **MediaPipe se eligió sobre ultralytics/YOLO para evitar
  la licencia AGPL-3.0** — no reintroducir ultralytics sin decisión
  comercial explícita.

## Base de datos

SQLite vía SQLAlchemy 2.0 (`models/db.py`). **Reglas de portabilidad**
(la DB puede cambiar de motor a futuro): tipos estándar, cero SQL crudo
en la lógica, todo lo SQLite-específico vive en listeners/guards del
engine (`PRAGMA foreign_keys=ON`, migración ad-hoc de `devices.site_id`).
Alembic entra cuando el esquema se estabilice, antes de la primera
instalación en campo.

7 tablas: `sites`, `devices` (credenciales de cámara — **hoy en texto
plano, ver ROADMAP**), `analytics_configs`, `alarm_rules`,
`alarm_events`, `media_assets`, `users`.

Cascadas: borrar cámara → CASCADE en configs/reglas propias/eventos/media;
borrar regla → `alarm_events.rule_id=NULL` (el historial no se pierde;
la severidad se copia al evento al disparar); borrar sitio → cámaras a
"Sin sitio"; borrar usuario → media queda como sistema.

## Storage de media

Regla de oro: **buscar un archivo nunca recorre el filesystem**. La
tabla `media_assets` (índices por `(kind, timestamp)`,
`(device_id, timestamp)`, evento, usuario) es el único índice; el disco
solo se toca para cargar. Layout navegable a mano:
`data/media/<tipo>/<AAAA>/<MM>/<DD>/<cámara>/<HHMMSS>_<evento>.<ext>`,
con ruta **relativa** en DB (el data-dir puede moverse).
`core/media_store.py` construye/resuelve rutas y registra;
`core/retention.py` purga decidiendo contra la DB.

## Sesión, sitios y permisos

- `core/auth.py`: PBKDF2-HMAC-SHA256 (260k iteraciones, salt por
  usuario); `current_user` es global de módulo (proceso único).
- `core/permissions.py`: 8 permisos × 4 roles fijos; `can(perm)` falla
  cerrado. El enforcement es de UI (suficiente para monolito; una capa
  cliente/servidor futura debe repetirlo en el servidor).
- `core/app_state.py`: filtro global de sitio + señal
  `site_filter_changed`; Vista en Vivo/Dispositivos/Alarmas se recargan
  al cambiarlo.

## Convenciones

- Docstrings de módulo explican *por qué* (decisiones, trade-offs), no
  mecánica. Mantener ese estándar.
- Español en UI, logs, docstrings y tests.
- Cada cambio = commit atómico con cuerpo explicativo; CI (lint ruff +
  pytest + cobertura) debe estar verde en cada push a `main`.
