# Roadmap / deuda técnica

Priorizado. Lo marcado 🔴 debería resolverse antes de instalar en un
cliente real; lo marcado 🟠 antes de la próxima demo en vivo.

Los ítems con referencia `(archivo:línea)` salieron de la validación de
backend del 2026-09-21 — el detalle completo, con evidencia, está en
[`sesiones/2026-09-21.md`](../sesiones/2026-09-21.md).

## Robustez (bloqueantes de demo)

Sin ítems abiertos: los cinco bloqueantes del informe del 2026-09-21 (B1 a B5)
se cerraron entre el 21 y el 22 de septiembre. El detalle está en "Hecho".

## Seguridad

- **Master password para las credenciales de cámara.** Hoy la clave de
  cifrado vive en un archivo 0600 al lado de la base
  (`core/credential_store.py`), lo que saca las contraseñas de los backups y
  de cualquier inspección casual, pero **no es secreto** contra alguien que
  ya entró al equipo con el usuario de la app. Secreto real necesita que el
  usuario tipee una master password al arrancar — y eso choca con que un VMS
  de sala tiene que levantar solo después de un corte de luz. Decisión
  consciente, a revisar si un cliente lo pide por contrato.
- El enforcement de permisos es **solo de UI** (`core/permissions.py:9-11`):
  nada impide llamar al repositorio directo desde un script.


## Datos

- Migrar timestamps float → DateTime UTC unificado.
- Si aparece multisede real con servidor central: nodo central en
  PostgreSQL (la capa SQLAlchemy ya es portable), grabadores por sitio
  en SQLite.
- `users.custom_permissions JSON` que overridee el rol (matriz editable
  por usuario en la UI).

## Tests

- Módulos sin cobertura real: `ptz_control` 29%, `logging_setup` 37%, y la
  I/O de ONVIF de `device_manager` (`discover_onvif`, `fetch_onvif_profiles`,
  `reboot_device`), que no tiene ningún doble.
- El gate de cobertura mide `core`/`models`/`config`; sumar `aurea_vms/ui`
  (~54% del código) a medida que avance el backfill con pytest-qt.
- `build-windows.yml` corre solo por tag o a mano: una regresión de
  empaquetado puede vivir en `main` hasta el próximo release.

## Empaquetado y despliegue

- El spec de PyInstaller empaqueta el WSDL de ONVIF condicionalmente
  (`aurea_vms.spec:30-33`, `if _wsdl_dir.exists()`) y sin fail-fast: si el
  layout del paquete `onvif` cambia, compila igual y falla recién al usar
  ONVIF desde el `.exe`.
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

- **La galería de rostros calcula en el hilo de la GUI — medido, y no hace
  falta moverlo.** Números por evento de detección, en la máquina de
  desarrollo: antes eran 768 µs de consulta a la DB + 195 µs por cara; con la
  configuración cacheada y `geometry_signature` vectorizada quedó en **65 µs
  por cara y ninguna consulta**, o sea 0,3 % del hilo de la GUI con 2 caras a
  25 fps (antes 2,9 %). Lo que queda no conviene mover: el 77 % del costo de
  publicar una miniatura es armar el `QPixmap` con su overlay (654 µs), y eso
  **no puede** salir del hilo de la GUI. Un worker con cola compraría 0,3
  puntos a cambio de un actor y superficie de concurrencia nueva. Revisar solo
  si aparece un caso con muchas más caras por frame.

## Producto (ideas de NOVA a evaluar)

- **"Probar conexión" de una cámara, sin abrirla.** Al borrar la cadena muerta
  `refresh_device_status` (Fase 6) quedó explícito que el estado de una cámara
  solo lo persiste `stream_manager`, y solo mientras alguien la está mirando:
  una cámara que nadie abre se queda en "Sin probar" para siempre y el tile del
  dashboard la cuenta ahí. El botón existía en el docstring del módulo de
  Dispositivos pero no estaba cableado a nada.

- "Legajo de evidencia" en PDF (QPrinter) con el spec del prototipo.
- Zonas poligonales con roles (hoy: ROI rectangular + línea).
- Jerarquía completa Organización → Sitios → Zonas → Cámaras (hoy:
  Sitios → Zonas → Cámaras).

## Hecho

- ~~Cada analizador crea su propia `InferenceSession` de 20 MB~~ — una sesión
  por **archivo de modelo**, con refcount y lock porque los analizadores se
  construyen desde hilos distintos. `close()` dejó de ser un hook vacío: lo
  implementan `PeopleCountingAnalyzer`, `LineCrossingAnalyzer` y
  `FaceDetectionAnalyzer`, y es idempotente (un doble decremento liberaría
  una sesión en uso). Medido con 4 cámaras × 2 analíticas sobre 12 cores:
  **50 MB en vez de 150 MB, y 25% más rápido** (17,8 contra 14,2
  inferencias/s) — no hubo trade-off RAM/CPU, se ganó en las dos, porque 8
  sesiones × 2 threads peleaban por los mismos cores que la captura RTSP y la
  UI. YuNet **no** se comparte a propósito: guarda el tamaño de entrada
  adentro (`setInputSize` por frame), así que dos cámaras con recortes
  distintos se lo pisarían, y pesa 228 KB contra 20 MB.

- ~~Credenciales de cámara en texto plano~~ — cifradas en reposo con Fernet,
  clave en `<data_dir>/camera_key` creada con 0600 de entrada (no con un
  `chmod` después: entre el `write` y el `chmod` hay una ventana en la que la
  clave es legible). El cifrado vive en un `TypeDecorator`
  (`models/types.py`), así que `device.password` sigue siendo texto plano en
  Python y **ninguno de los doce lugares que lo leen cambió**. La revisión
  `0005` ensancha la columna y cifra lo que ya estaba. Si la clave no
  corresponde se devuelve vacío y se loguea, en vez de dejar la lista de
  cámaras sin abrir.

- ~~PBKDF2 con 260.000 iteraciones~~ — 600.000, con el hash en formato
  auto-descriptivo `pbkdf2_sha256$<iteraciones>$<salt>$<hash>`. Que el coste
  viaje adentro es lo que permite subirlo sin resetearle la contraseña a
  nadie: `authenticate` valida contra los parámetros de ESE hash y lo
  re-calcula al vuelo si quedaron viejos. La comparación pasó a
  `hmac.compare_digest`, y `create_user` valida la política de contraseña —
  era el único camino de alta que no lo hacía.

- ~~Rate-limit / lockout de intentos de login~~ — `failed_attempts` y
  `locked_until` en `users`, persistidos y no en memoria: un dict de módulo
  se resetea cerrando y abriendo la app, que es justo lo que puede hacer
  quien está sentado frente a la máquina. 5 intentos, 15 minutos, **por
  ventana de tiempo y nunca permanente**. Un reset de admin levanta el
  bloqueo. El diálogo de login distingue "te equivocaste" de "esperá".

- ~~Constraints e índices que faltaban, y kwargs que se perdían en silencio~~ —
  revisión `0004_constraints`. `AlarmRule` salió de la API legacy `Column` y
  sus ocho columnas dejaron de ser nullables; `UNIQUE(device_id, analyzer_name)`
  en `analytics_configs` y `UNIQUE(site_id, name)` en `zones`; índice en
  `alarm_events.status` y se fue el de `device_id`, redundante con el
  compuesto. La revisión **sanea antes de restringir** — rellena NULLs con los
  defaults del modelo, deduplica configuraciones quedándose con la más
  reciente, y fusiona zonas duplicadas **reapuntando sus cámaras antes** de
  borrar, porque `Device.zone_id` es SET NULL y borrar primero las dejaría
  "Sin zona". Los seis `update_*` y el upsert levantan `ValueError` ante un
  kwarg que no es columna, igual que ya hacían los `add_*`.
  `tests/test_constraints.py` y `tests/test_repository_kwargs.py`, que incluye
  la guarda sobre los 10 call-sites que pasan `**dict` desde los diálogos.

- ~~Migraciones ad-hoc sin versionado de esquema~~ — Alembic, con las
  revisiones dentro del paquete (`aurea_vms/migrations/`) para que el `.exe`
  se las lleve. `init_db` cubre los tres caminos: base nueva, base ya
  versionada (con corte temprano, 73 ms → <1 ms en cada arranque a partir del
  segundo) y base **anterior** a Alembic, que se adopta una sola vez y se
  marca en la baseline. Los dos `UPDATE` de datos y el backfill de zonas
  dejaron de correr en cada arranque: son la revisión `0002_datos_legados`.
  Se fue además la columna legada `devices.site_id` (`0003`), y
  `Base.metadata` lleva `naming_convention`, que es lo que la fase de
  constraints va a necesitar para `batch_alter_table`.
  Al adoptar apareció que el sistema ad-hoc **agregaba columnas pero nunca
  sus índices**: la base de desarrollo venía sin `ix_devices_zone_id` desde
  que existen las zonas. La adopción los crea. `tests/test_db_migrations.py`,
  18 tests, incluido uno que corre `compare_metadata` y exige que la base
  migrada no tenga ninguna deriva contra los modelos.

- ~~La galería de rostros tiene lógica de dominio dentro de un widget~~ —
  `core/face_catalog.py` es dueño de las capturas, el dedup por umbral, la
  asignación de `track_id`, el reemplazo por área y el reinicio diario (con el
  reloj inyectable, que era lo que lo hacía intesteable). El `QListWidget`
  dejó de ser el modelo de datos: el widget aplica el `CatalogUpdate` que le
  devuelve el catálogo y las dos listas quedan con los mismos índices.
  `tests/test_face_catalog.py`, 39 tests donde no había ninguno.

- ~~Dashboard: reemplazar el fetch de 200 eventos + todos los dispositivos
  cada 5 s por consultas `COUNT`~~ — `count_alarm_events()` (con filtros de
  cámara, sitio, severidad y estado), `count_devices_by_status()` y
  `list_alarm_events(site_id=…, device_id=…)`. No era sólo costo: se cerraron
  **dos bugs de corrección**. La tarjeta "Eventos registrados" mostraba `len()`
  de la página de 200, o sea 200 para siempre en cuanto hubiera más; y con el
  filtro global de sitio activo la tabla mostraba un subconjunto de los últimos
  200 **globales** en vez de los últimos 200 de ese sitio. De paso,
  `list_devices(site_id=…)` pasó de dos consultas a un JOIN.

- ~~El ORM se filtra hasta la UI~~ — `models/errors.py` con `RepositoryError`
  y `DuplicateError`; `repository` traduce la `IntegrityError` en su frontera
  (`_escritura()`, que envuelve las altas y las modificaciones). `grep -rn
  sqlalchemy aurea_vms/ui/` ya no devuelve nada, y hay un test que lo fija.
  De yapa, los siete `add_*` y los seis `update_*` pasaron a compartir
  `_insert`/`_update`: eran trece copias del mismo bloque de cinco líneas.

- ~~El único test de integración no corre inferencia~~ — `tests/test_registry.py`
  ejercita ahora `process_frame` sobre los 5 analizadores con sus modelos reales
  (10 tests parametrizados, 4 s), con aserciones estructurales: cajas dentro del
  frame, confianzas en rango, y determinismo entre dos instancias limpias. El
  grueso del trabajo quedó en `tests/test_yolox_pipeline.py`, que cubre sin
  modelo el letterbox, la decodificación de las 3 cabezas por stride, el NMS
  class-agnostic y el camino completo de `detect()` con la sesión reemplazada
  por un doble. `object_detector_backend` pasó de **40% a 95%**.

- ~~`device_manager.refresh_device_status` es código muerto~~ — se fueron
  también `test_rtsp_connection` y `_open_and_read`, que solo usaba él. Efecto
  estructural: `device_manager` dejó de importar `repository` y `event_bus`, o
  sea quedó siendo I/O de red pura, y hay un test que lo fija.

- ~~Los hilos daemon de `grab_snapshot` no tienen techo~~ —
  `MAX_CONCURRENT_PROBES = 4` con un `BoundedSemaphore` que libera el propio
  thread de sondeo, no el caller: el thread sobrevive al timeout de
  `grab_snapshot` (el backend FFmpeg tarda ~30 s contra un host inalcanzable
  pese a los timeouts configurados). Con las ranuras tomadas no se abre una
  conexión más y se avisa al usuario (`device_manager.py:26-37,126-160`).

- ~~`resources.bundled_path("models/x")` no encuentra los modelos en dev~~ —
  `ensure_model` prueba dos candidatos, el del bundle y el del repo
  (`model_assets.py:32-50`). Verificado: el smoke con `AUREA_DATA_DIR`
  redirigido ahora loguea "copiado desde .../data/models" en vez de bajar 20 MB.
  Lo pagaba cada corrida de `build-windows.yml`, que usa un data-dir vacío.

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
