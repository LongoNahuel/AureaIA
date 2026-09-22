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
  join para que el `close()` del analizador libere su sesión de
  onnxruntime antes del teardown del intérprete; `stop_all()` de streams
  hace join para no acumular sockets zombies en logout→login.

## Analíticas

Interfaz pluggable `core/analytics/base.py` (`Analyzer.process_frame` →
`AnalysisResult`), registry/factory en `core/analytics/registry.py`.

Las cuatro que expone el registry son `door_state`, `people_counting`,
`line_crossing` y `face_detection`. `motion_detection` (MOG2) sigue
construible desde el registry por compatibilidad con configuraciones
viejas, pero la UI ya no lo ofrece: lo reemplazó `door_state`.

- Estado de puerta: umbral morfológico sobre un ROI estático contra una
  línea de base, con `confirmation_frames` de histéresis. Solo reporta
  **transiciones** — el `AlarmEngine` descarta el evento si no las trae.
- Conteo de personas y cruce de línea: **YOLOX-Tiny** (COCO, ONNX vía
  onnxruntime CPU) + `CentroidTracker` con histéresis (`min_hits`) y
  tolerancia a oclusiones (`max_age_s`).
- Rostros: **YuNet** (`cv2.FaceDetectorYN`, OpenCV Zoo 2023mar), con 5
  landmarks y score propio del modelo, más tres filtros geométricos
  baratos (forma de caja, disposición de los puntos entre sí, y cruce de
  esos puntos contra la caja de la cabeza).
- Preprocesado: el backend de objetos hace letterbox a 416×416 con
  relleno gris 114 alineado arriba-izquierda y decodifica las 3 cabezas
  por stride 8/16/32 con NMS class-agnostic — **replica exactamente el
  demo oficial de ONNXRuntime de YOLOX**; un detalle mal portado ahí
  decodifica cajas en cualquier lado sin ningún error que lo delate. Los
  analizadores que no usan el modelo (puerta) recortan a ROI **antes** de
  `resize_for_inference` (máx. 640 px de lado) y usan `rescale_bbox` para
  volver a coordenadas nativas.
- `Analyzer.close()` libera el modelo nativo (lo llama el worker al
  detenerse).

**Licencias**: YOLOX (Megvii) y YuNet (OpenCV Zoo) son Apache 2.0.
**No reintroducir `ultralytics`** (YOLOv5/v8) sin decisión comercial
explícita: es AGPL-3.0, copyleft fuerte. Fue la razón por la que se lo
sacó del proyecto, y sigue vigente aunque el stack haya vuelto a la
familia YOLO.

## Base de datos

SQLite vía SQLAlchemy 2.0 (`models/db.py`), los 8 modelos con
`Mapped`/`mapped_column`. **Reglas de portabilidad**
(la DB puede cambiar de motor a futuro): tipos estándar, cero SQL crudo
en la lógica, todo lo SQLite-específico vive en listeners/guards del
engine (los `PRAGMA` de `_apply_sqlite_pragmas`) y en las revisiones de
migración.

**El esquema está versionado con Alembic** (`aurea_vms/migrations/`). La app
migra sola al arrancar (`models/db.py::migrar`): en una instalación de
cliente no hay nadie que corra `alembic upgrade` a mano. Tres caminos, y los
tres terminan en la última revisión — base nueva (la crea la baseline), base
ya versionada (aplica lo que falte, y si no falta nada corta temprano), y
base **anterior** a Alembic, que se adopta una sola vez
(`migrations/adopcion.py`) y se marca en la baseline.

Las revisiones viven **dentro del paquete**, no en la raíz del repo, para
que PyInstaller las empaquete: sin ellas el `.exe` no puede migrar la base
del cliente. `Base.metadata` lleva una `naming_convention` porque SQLite no
sabe alterar una tabla — Alembic lo emula recreándola con
`batch_alter_table`, y para eso necesita poder referenciar cada constraint
por nombre.

Para agregar un cambio de esquema: tocar el modelo y correr
`alembic revision --autogenerate -m "lo que cambia"` (el `alembic.ini` de la
raíz es solo para esto; la app arma su config en memoria).

8 tablas: `sites`, `zones`, `devices` (credenciales de cámara cifradas en
reposo — ver `core/credential_store.py` para el alcance real de esa
protección), `analytics_configs`, `alarm_rules`,
`alarm_events`, `media_assets`, `users`. La jerarquía es
Sitio → Zona → Cámara.

Cascadas: borrar cámara → CASCADE en configs/reglas propias/eventos/media;
borrar regla → `alarm_events.rule_id=NULL` (el historial no se pierde;
la severidad se copia al evento al disparar); borrar sitio → CASCADE en
sus zonas, y sus cámaras quedan "Sin zona" (`devices.zone_id` a NULL);
borrar usuario → media queda como sistema.

`repository.delete_site`/`delete_zone` además nulifican a mano lo que el
`ondelete` ya declara: una DB migrada por `ALTER TABLE` puede tener el FK
sin acción de borrado, y con `PRAGMA foreign_keys=ON` el CASCADE fallaría
con `IntegrityError`.

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
