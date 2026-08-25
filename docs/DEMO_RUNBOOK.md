# Runbook de demo (Casinos de Buenos Aires)

## La noche anterior

- [ ] `git pull` + `pip install -e ".[dev]"` en la notebook de demo (o
      el `.exe` compilado cuando F6 esté cerrado) y CI verde en `main`.
- [ ] Copiar los 4 clips de casino a `tools/demo/media/` (mapeo en
      `tools/demo/README.md`) y el binario de mediamtx.
- [ ] Ensayo completo con el rig local (ver abajo): alarmas disparando,
      clips reproducibles, export de evidencia.
- [ ] Borrar la DB de pruebas sucias y re-seedear:
      `rm data/aurea_vms.sqlite3 && python tools/demo/seed_demo.py`.
- [ ] Verificar espacio en disco y que la retención esté en valores
      sanos (Sistema → Grabando).

## Setup en sala (cámaras reales)

1. Conectar la notebook a la LAN de las cámaras.
2. Dispositivos → buscar por ONVIF → agregar los 4 canales, asignando
   sitio a cada cámara.
3. Analizadores: una analítica por cámara según la escena (mesa →
   conteo; acceso → cruce de línea, dibujar la línea sobre el snapshot;
   ingreso → rostros; pasillo/bóveda → movimiento).
4. Alertas: verificar que cada regla tenga popup + clip y cooldown ~20 s.
5. Dejar la app 10 minutos corriendo antes de la demo (buffers, estados
   online, primeras alarmas de prueba reconocidas).

## Plan B — cámaras falsas (idéntico a la demo real)

```bash
./mediamtx tools/demo/mediamtx.yml          # terminal 1
tools/demo/start_fake_cams.sh               # terminal 2 (o .bat)
python tools/demo/seed_demo.py              # una vez
python -m aurea_vms.main
```

Si una cámara real falla EN VIVO: editar el dispositivo y apuntar su
URL principal a `rtsp://127.0.0.1:8554/camN` — el resto no cambia.

## Usuarios de demo (seed)

| Usuario | Rol | Para mostrar |
|---|---|---|
| `admin` | Administrador | configuración completa |
| `supervisor` | Supervisor | opera + configura analíticas, sin admin |
| `operador` | Operador | solo Vista en Vivo + Alarmas |
| `auditor` | Auditor | solo lee el historial y exporta evidencia |

Contraseña de todos: `Aurea123!x` (cambiarla si la demo queda instalada).

## Guion sugerido (15-20 min)

1. **Login** como `admin` → launcher por categorías.
2. **Vista en Vivo**: árbol por sitio, drag & drop a la grilla, selector
   global de sitio filtrando, doble click expande (pasa a main-stream).
3. **Analíticas** en vivo: overlays de las 4 (movimiento, conteo con
   ocupación, cruce con contadores IN/OUT, rostros con galería).
4. **Alarma en vivo**: popup (crítica persiste hasta reconocer) →
   Alarmas → estados del incidente → notas → reproducir clip →
   **exportar evidencia** (carpeta con captura + clip + resumen).
5. **Multiusuario**: logout → login `operador` (solo Operación) →
   login `auditor` (solo lectura + export). Mensaje: permisos por rol.
6. **Multisitio**: selector de sitio + columna sitio en Dispositivos.
7. Cierre: retención automática, todo local sin nube, roadmap (H.264,
   grabación continua, LPR/facial como módulos futuros).

## Si algo se rompe

- Cámara sin video: probar conexión desde Dispositivos; si es la red,
  plan B RTSP local (arriba).
- App colgada/rara: cerrar sesión y volver a entrar re-arranca los
  engines; peor caso, matar la app — la DB y la media quedan intactas.
- Sin alarmas: revisar que la regla esté habilitada, sin horario que la
  excluya y con cooldown corto; el log vivo está en Sistema → Registro.
