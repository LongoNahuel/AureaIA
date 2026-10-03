# Actualización 02/10/2026 — Incidentes en casinos

Rama `ruleta-no-va-mas-manos` (del 24/09 al 02/10/2026). Este documento
explica qué cambió para quien usa y mantiene el VMS. Lo que toca el
backend, con el detalle de cada archivo, está en
[`CAMBIOS_PARA_DANIEL.txt`](CAMBIOS_PARA_DANIEL.txt).

## En resumen

- **Una sola analítica visible**: "Incidentes en casinos", con cuatro
  modos:
  - golpes al monitor;
  - consumo de sustancias;
  - ruleta;
  - BlackJack, pendiente de video.
- **Ruleta**:
  - el "no va más" se canta solo, cuando la bola frena;
  - desde ahí, mover fichas en el paño dispara una alarma, salvo que lo
    haga el crupier.
- **Manos** del crupier y de los jugadores, marcadas con un recuadro que
  acompaña al video hasta 60 fps.
- **Alarmas**:
  - botón "Limpiar incidentes";
  - histórico completo por canal;
  - cada incidente con su clip, que se reproduce dentro de la app.
- **Las 7 cámaras simuladas** publican video, para probar las analíticas en
  cualquiera.

## Ruleta

### Qué mide

- **La rueda**: se ubica sola sobre unos segundos de video. Se mide la
  velocidad del plato (°/s, rpm y sentido).
- **La bola**: se la sigue en la pista del tazón. Sale a ~500 °/s y cae al
  plato a ~120–145 °/s.
- **El "no va más" lo marca la bola, no el plato.** El plato casi no frena
  en una tirada (en la RA-04, de 182 a 142 °/s en 160 s). La bola frena de
  forma pareja.

### La ronda

| Estado | Cuándo |
|---|---|
| Apuestas abiertas | No hay bola en la pista |
| Bola en juego | La bola gira por encima del umbral (por defecto 200 °/s) |
| **NO VA MÁS** | La bola baja del umbral: **el paño se arma** |
| Resultado · esperando la marca | La bola cayó; el paño sigue armado hasta que el crupier toca el paño |

- **La rueda marca la jugada.** Cuando el crupier frena o re-impulsa el
  plato (cambio de sentido o de velocidad sostenido 1 s), empieza una jugada
  nueva: se reabren las apuestas y se desarma el paño.
- **La bola gira contra el plato.** Una "bola" que va en el mismo sentido
  que el plato no cuenta: así se descartan reflejos y manos.

### La alerta: fichas tras el no va más

Con el paño armado, se dispara la alarma **"Fichas tras el no va más"**
cuando cambian las fichas de una zona:

- **Con la bola todavía girando**: cuenta cualquier movimiento que no sea
  solo del crupier.
- **Con la bola ya caída**: hace falta ver la mano de un jugador, porque el
  crupier marca y cobra.

En el video se ve:

- la zona en rojo;
- un anillo sobre **cada ficha que cambió**: se comparan color y bordes y
  se buscan manchas del tamaño de una ficha, así que se detecta también una
  ficha crema sobre el paño crema;
- el cartel de incidente.

Medido sobre los dos clips de la demo:

- **cero alertas falsas**;
- un past-post simulado da la alarma en la posición exacta de la ficha.

## Manos

- **Crupier** (violeta): quien toca la rueda. Su rol queda pegado a su
  persona aunque después no la toque.
- **Jugador** (celeste): quien puso una mano sobre la mesa.
- **Persona** (gris): todavía sin rol; por ejemplo, el espectador de
  detrás de la soga.
- **En el video, un recuadro por mano.** Se marca más fuerte cuando la mano
  toca la rueda o una zona de fichas.
- **21 puntos por mano** (modelo RTMW de cuerpo entero) para el crupier y
  quien tenga las manos en el paño o la rueda; el resto, con el modelo
  liviano.
- **Fluidez**:
  - la detección completa corre en su propio hilo (5–6 veces por segundo
    en esta máquina);
  - entre detección y detección, cada punto de la mano se sigue cuadro a
    cuadro con flujo óptico;
  - el recuadro acompaña al video a la velocidad del stream.

## Video

- El tile redibuja hasta **60 fps** (antes 25 fijos), solo cuando hay
  cuadro o marcas nuevas.
- Achicar cada cuadro cuesta 0,5–2 ms (antes 14–20 ms en el hilo de la
  interfaz).
- Las marcas se dibujan **sobre el cuadro en que se calcularon**: el
  recuadro no queda atrás de una mano rápida.

## Alarmas

### Limpiar incidentes

Botón en el panel de "Incidentes en casinos" de la Vista Inteligente.
Pensado para mostrar limpio el próximo incidente:

- pone en cero los contadores y las alertas en vivo de esa cámara (en
  ruleta, la ronda en curso sigue);
- marca como **Reconocidas** sus alarmas pendientes (con permiso de
  gestión).
- **El histórico no se borra.**

### Regla automática

- Un incidente queda en el histórico solo si hay una regla de alarma para
  su cámara.
- Al habilitar "Incidentes en casinos" en una cámara sin regla, **se crea
  sola** con:
  - todas las clases de incidente;
  - "guardar clip";
  - confianza mínima 0,3;
  - 5 s entre alarmas;
  - severidad crítica.
- Se edita en **Alertas** como cualquier otra.

### Módulo Alarmas

- **Filtros**:
  - por **canal**, con cuántos incidentes tiene cada cámara;
  - por **tipo de incidente**;
  - por **estado**.
- **Todo el histórico**, de a 100 ("Cargar más"), con fecha y hora completa.
- **Detalle de un incidente**:
  - la captura grande;
  - **el clip del evento reproducido adentro** (unos segundos antes y
    después), con play/pausa y barra de avance;
  - los datos;
  - Reconocer, En investigación y Resolver;
  - notas;
  - Exportar evidencia.

## Configuración (Incidentes en casinos → Ruleta)

| Opción | Por defecto | Para qué |
|---|---|---|
| Marcar las manos del crupier y de los jugadores | Sí | Detección de manos y roles |
| Detección completa de manos | 5 /s | Cada cuánto se recalculan personas y manos (el resto se sigue cuadro a cuadro) |
| No va más con la bola bajo | 200 °/s | Cuándo se arma el paño |
| FPS de análisis | 25 (hasta 60) | A cuántos cuadros por segundo se siguen la bola y las manos |

## Cámaras simuladas

`tools/demo/start_fake_cams.sh` (o `.bat` en Windows):

- publica cada `tools/demo/media/camN.mp4` como `rtsp://127.0.0.1:8554/camN`;
- **sin recodificar**: los clips son H.264 con un cuadro clave cada 2 s; ~0 %
  de CPU por cámara;
- relanza una publicación que se corta;
- levanta mediamtx si no está corriendo.

| Canal | Dispositivo |
|---|---|
| cam1 | #3 Sabotaje de máquina |
| cam2 | #4 Sabotaje de ticket |
| cam3 | #5 Monitor roto |
| cam4 | #6 Ruleta AR 4171 |
| cam5 | #8 Consumo de sustancias |
| cam6 | #9 Sala ESP SUB 08-5 |
| cam7 | #10 Ruleta RA-04 |

## Herramientas de validación

- `tools/ruleta/linea_de_tiempo.py`: sobre un clip, la ronda, las jugadas,
  los movimientos de fichas y las alarmas, con el costo por muestra.
- `tools/ruleta/past_post_simulado.py`: pega una ficha en el paño y
  verifica que salga la alarma.

## Pendiente

- **Puesto del crupier en la configuración.** En la AR 4171 el crupier
  nunca toca la rueda (la bola ya viene lanzada) y no se lo identifica: con
  la bola girando, cualquier mano en el paño es alerta.
- **BlackJack**: falta video de una mesa.
- **Para Daniel** (ver [`CAMBIOS_PARA_DANIEL.txt`](CAMBIOS_PARA_DANIEL.txt)):
  - la revisión de datos para las analíticas ocultas;
  - `alarm_events.details`;
  - el `allow_spinning` de ONNX;
  - el RTMW en el bundle.

## Tests

878 tests en verde. Nuevos:

- `test_roulette`, `test_roulette_round`, `test_no_va_mas`;
- `test_table_hands`, `test_hand_flow`;
- `test_incidentes_historico`, `test_monitor_tamper_panel`;
- `test_consumption`.

Detalle y mediciones: `sesiones/2026-09-30.md` y `sesiones/2026-10-02.md`.
