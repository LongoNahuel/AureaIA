# Roadmap / deuda técnica

Priorizado. Lo marcado 🔴 debería resolverse antes de instalar en un
cliente real; lo marcado 🟠 antes de la próxima demo en vivo.

Los ítems con referencia `(archivo:línea)` salieron de la validación de
backend del 2026-09-21 — el detalle completo, con evidencia, está en
[`sesiones/2026-09-21.md`](../sesiones/2026-09-21.md).

## Robustez (bloqueantes de demo)

- Los hilos daemon de `device_manager.test_rtsp_connection`/`grab_snapshot`
  (`device_manager.py:86-104,126-150`) no tienen techo: contra un host
  inalcanzable, cada reintento del usuario deja uno colgado.

## Seguridad

- 🔴 **Credenciales de cámara en texto plano** (`devices.password`,
  `models/device.py:24-25`). Cifrar en reposo (clave derivada de una master
  key local o DPAPI en Windows) o al menos mover el data-dir a un perfil
  con ACLs.
- 🔴 **PBKDF2 con 260.000 iteraciones** (`core/auth.py:18`), por debajo del
  mínimo OWASP actual (≥600.000). El salt de 16 bytes por usuario sí es
  correcto. Subir la constante sola no re-hashea nada: hace falta
  rehash-on-login, y persistir las iteraciones junto al hash.
- 🔴 Rate-limit / lockout de intentos de login (`core/auth.py:52-69`).

## Datos

- **Alembic**: hoy las migraciones son ad-hoc (`models/db.py:66,77-131`),
  sin tabla de versión de esquema — no se puede saber en qué estado está
  una DB de campo salvo inspeccionando columnas, y una columna agregada al
  modelo pero olvidada en `_ADHOC_COLUMNS` rompe la DB vieja en silencio.
- Dos `UPDATE` de migración de datos corren en **cada** arranque, para
  siempre (`models/db.py:123-130`). Pertenecen a una revisión versionada.
- **`AlarmRule` usa la API legacy `Column`/`Any`** (`models/alarm_rule.py:19-40`)
  mientras los otros 7 modelos usan `Mapped`. Consecuencia real:
  `analyzer_name`, `min_confidence`, `cooldown_seconds`, `severity`,
  `actions` y `enabled` quedaron **nullables** a nivel de esquema.
- Falta `UNIQUE(device_id, analyzer_name)` en `analytics_configs`, que
  `upsert_analytics_config`/`get_analytics_config_for` asumen con
  `.one_or_none()` (`repository.py:177-187,190-211`). Un duplicado rompe el
  arranque de analíticas con `MultipleResultsFound`.
- Falta índice en `alarm_events.status` (lo filtra el dashboard,
  `repository.py:299-307`); sobra el `index=True` de `alarm_events.device_id`,
  ya cubierto por `ix_alarm_events_device_ts`. Falta `UNIQUE(site_id, name)`
  en `zones`.
- Los seis `update_*` de `repository` hacen `setattr` sin whitelist: un typo
  en un kwarg se pierde en silencio al commitear.
- Migrar timestamps float → DateTime UTC unificado.
- Si aparece multisede real con servidor central: nodo central en
  PostgreSQL (la capa SQLAlchemy ya es portable), grabadores por sitio
  en SQLite.
- `users.custom_permissions JSON` que overridee el rol (matriz editable
  por usuario en la UI).

## Tests

- **El único test de integración no corre inferencia** (`tests/test_registry.py:57-73`):
  construye los 4 analizadores y llama `close()`, nunca `process_frame`. Una
  regresión en el letterbox, la decodificación de cabezas, el NMS o YuNet no
  la detecta nadie — los unitarios mockean el modelo y el de integración no
  lo ejercita.
- Módulos sin cobertura real: `object_detector_backend` 40%,
  `device_manager` 44% (toda su I/O sin test), `ptz_control` 29%,
  `logging_setup` 37%.
- `device_manager.refresh_device_status` (`device_manager.py:153-169`) no
  tiene ningún caller en el repo: código muerto.
- El gate de cobertura mide `core`/`models`/`config`; sumar `aurea_vms/ui`
  (~54% del código) a medida que avance el backfill con pytest-qt.
- `build-windows.yml` corre solo por tag o a mano: una regresión de
  empaquetado puede vivir en `main` hasta el próximo release.

## Empaquetado y despliegue

- El spec de PyInstaller empaqueta el WSDL de ONVIF condicionalmente
  (`aurea_vms.spec:30-33`, `if _wsdl_dir.exists()`) y sin fail-fast: si el
  layout del paquete `onvif` cambia, compila igual y falla recién al usar
  ONVIF desde el `.exe`.
- `resources.bundled_path("models/x")` resuelve a `PROJECT_ROOT/models/` en
  desarrollo (`config/resources.py:24-26`), pero los modelos viven en
  `PROJECT_ROOT/data/models/`. El paso "copiar del bundle" de `ensure_model`
  nunca acierta en dev: con `AUREA_DATA_DIR` redirigido se cae a descargar
  de internet, contra la promesa explícita de `model_assets.py:4-5` de que
  la demo puede correr sin conexión.
- `console=True` en el spec mientras el build madura; flip a `False` para la
  entrega final.
- `main()` nunca llama a `app.setWindowIcon()` (`main.py:157-160`), así que el
  globo de la bandeja sale con icono nulo (`desktop_notify.py:70`). Cosmético,
  pero toca los assets de marca: va con el lane de Nahuel.
- El CI sigue instalando `libgles2` (`.github/workflows/ci.yml`), que entro por
  MediaPipe. Ya nada declara `NEEDED` contra `libGLESv2.so.2` — ni onnxruntime,
  ni cv2, ni PySide6 — pero Qt puede cargarla por `dlopen`. Sacarla y confirmar
  en un run real de Actions; no se puede verificar en una maquina que ya la
  tiene instalada.

## Video / analíticas

- Clips: re-encodear a H.264 (PyAV/imageio-ffmpeg) — hoy mp4v a 5 fps
  con doble recompresión JPEG; usar los timestamps reales guardados.
- Grabación continua en anillo (el `kind="recording"` de `media_assets`
  ya está reservado).
- Reproductor embebido (hoy abre el reproductor del SO) y captura
  manual con `created_by`.
- **Compartir la sesión de ONNX entre analíticas**: hoy cada analizador crea
  su propia `InferenceSession` de YOLOX-Tiny (`object_detector_backend.py:271-287`,
  `people_counting_analyzer.py:62`, `line_crossing_analyzer.py:55`), o sea 20 MB
  de modelo por analítica y por cámara. Mitigado a medias con
  `intra_op_num_threads=2`.
- `analytics_fps` por cámara (hoy global, con override en `params["fps"]`).
  Evaluar un execution provider con GPU si el hardware de sala lo permite.
- Tracker: matching greedy por centroide dependiente del orden de las
  detecciones (`tracker.py:67-91`) — puede hacer swaps de identidad con
  objetos cercanos. Evaluar asignación óptima o ByteTrack-lite si el conteo
  en escenas densas lo pide.
- El cooldown de las reglas vive en un dict en memoria
  (`alarm_engine.py:31,56-58`): se resetea en cada reinicio, y se lee/escribe
  sin lock entre hilos.
- Reconocimiento facial real (hoy solo detección; la galería usa una
  firma de similitud, no un embedding).

## Rendimiento UI

- Dashboard: reemplazar el fetch de 200 eventos + todos los dispositivos cada
  5 s por consultas `COUNT` agregadas (`ui/modules/event_dashboard.py:123-134`,
  `ui/widgets/dashboard_panel.py:154-171`). Esas funciones no existen todavía
  en `repository`.
- **La galería de rostros hace trabajo de CPU en el hilo de la GUI**
  (`ui/widgets/face_gallery.py:194,244-300`): por cada cara y cada frame
  calcula firma, geometría y comparación contra toda la galería. Ese
  algoritmo es lógica de dominio viviendo en un widget — mover a `core/`,
  donde entra al scope de cobertura y se puede reusar.
- `ui/dialogs/device_dialog.py:18` importa `sqlalchemy.exc.IntegrityError`:
  el ORM se filtra hasta el widget porque `repository` no expone una
  excepción de dominio.

## Producto (ideas de NOVA a evaluar)

- "Legajo de evidencia" en PDF (QPrinter) con el spec del prototipo.
- Zonas poligonales con roles (hoy: ROI rectangular + línea).
- Jerarquía completa Organización → Sitios → Zonas → Cámaras (hoy:
  Sitios → Zonas → Cámaras).

## Hecho

- ~~Reconexión RTSP sin backoff, y ningún watchdog de stream congelado~~ —
  `_reconnect_delay()` (`stream_manager.py:64-82`) hace backoff exponencial de
  3 s a 30 s con jitter de +25%, para que N cámaras que se caen juntas (un
  switch que se reinicia) no reintenten en fase. El contador se resetea solo
  cuando la conexión **entregó frames**, no con `isOpened()`: una cámara que
  abre el socket y se muere al instante no puede quedarse en el delay mínimo
  (`stream_manager.py:136-147`). Y `_capture_loop` corta el stream si la firma
  submuestreada del frame es idéntica por más de `FROZEN_STREAM_S`
  (`stream_manager.py:85-95,165-215`): un decoder colgado devuelve `ok=True`
  con la misma foto y el corte por `read()` fallido no llegaba nunca. Medido
  contra un puerto cerrado con cv2 real: 4 s → 7 s → 13 s, y `stop()` corta el
  hilo en 0,00 s aunque esté en medio del backoff.
  `tests/test_stream_manager.py::TestBackoff`, `::TestReconexion`,
  `::TestWatchdogDeCongelado`, `::TestParadaOrdenada` — con el primer doble de
  `cv2.VideoCapture` del repo, que llevó `StreamWorker.run()` de 0% a cubierto.

- ~~Un clip que no se pudo escribir se registra igual como evidencia~~ —
  `save_snapshot` mira el retorno de `cv2.imwrite` y `_write_mp4` chequea
  `writer.isOpened()`, que el primer frame decodifique y que se haya escrito
  al menos uno; los dos devuelven `None` con el motivo en el log y **borran el
  archivo a medias**, porque un archivo fuera de `media_assets` es invisible
  para el `RetentionWorker` y no lo limpia nadie
  (`clip_recorder.py:44-57,74-94,166-244`). `media_store.register()` valida
  existencia y tamaño y levanta `MediaWriteError` en vez de un
  `FileNotFoundError` crudo (`media_store.py:23-33,50-64`). `duration_s` sale
  ahora de los frames escritos, no de los intentados, y `_record_clip` ya no
  emite `clip_ready` si no hay clip. Efecto lateral importante: una captura
  fallida **ya no se lleva puesta la alarma** — antes la excepción subía al
  `except` por regla de `_on_detection` y el incidente no llegaba nunca a la UI
  (`alarm_engine.py:148-158`). `wait_for_pending()` deriva su tope de
  `settings.clip_post_seconds` en vez del 15.0 fijo que truncaba clips en el
  apagado. `tests/test_clip_recorder.py::TestEvidenciaQueNoMiente`,
  `::TestWaitForPending`, `tests/test_media_store.py::TestRegister`.

- ~~`QSystemTrayIcon` creado desde un hilo de analítica~~ — la acción
  `notify_desktop` de una regla viaja ahora como flag del `AlarmEvent`
  (`events.py:54-58`) y la ejecuta `MainWindow._on_global_alarm`
  (`main_window.py:214-228`, `QueuedConnection`), el mismo camino que ya usaba
  `play_sound`. De paso se saca del hilo de analítica el `repository.get_device`
  que alimentaba el texto, porque el slot de UI ya resolvía el nombre de cámara.
  `desktop_notify.notify()` quedó con guard de hilo explícito: una llamada desde
  fuera de la GUI se loguea y no construye nada
  (`desktop_notify.py:39-50,74-83`). Los dos casos que antes eran no-op mudo
  (sin `QApplication`, sin bandeja) ahora dejan línea en el log, y el icono se
  crea con la app como padre. `tests/test_desktop_notify.py`,
  `tests/test_alarm_trigger.py::TestTrigger`.

- ~~SQLite sin WAL ni `busy_timeout`~~ — `models/db.py:27-70` aplica
  `journal_mode=WAL`, `busy_timeout=5000` y `synchronous=NORMAL` por
  conexión, junto al `foreign_keys=ON` que ya estaba.
  `tests/test_db_pragmas.py` fija los pragmas y cubre el modo de falla real
  (un lector con la transacción abierta contra un escritor de fondo).
- ~~`alarm_engine._trigger` sin `try/except`~~ — `alarm_engine.py:54-92`
  protege por separado la lectura de reglas y cada disparo, y **consume el
  cooldown recién después de un disparo exitoso**, para que un lock
  transitorio no deje la regla muda hasta que venza.
  `tests/test_alarm_engine.py::TestResilienciaDeHilo`.
- ~~`settings.py` → `%LOCALAPPDATA%/AureaVMS` cuando corre frozen~~ —
  resuelto en `config/settings.py:24-33`, con override `AUREA_DATA_DIR` y
  test en `tests/test_packaging_paths.py`.
- ~~Spec de PyInstaller onedir + workflow `windows-latest` con smoke~~ —
  `aurea_vms.spec` y `.github/workflows/build-windows.yml`. El smoke crea
  los 4 detectores reales y procesa un frame.
- ~~Assets de marca (logo/fondos/videos)~~ — commiteados en `f94f32f`.
- ~~Módulo Alarmas: cachear nombres de cámara en la recarga~~ — resuelto en
  `ui/modules/alarm_module.py:144-147`.
- ~~Design tokens de severidad en `ui/theme.py` + números tabulares~~ —
  `a509f76` y `95911a0`.
