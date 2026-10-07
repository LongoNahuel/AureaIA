"""Evalua las reglas de alarma configuradas contra cada DetectionEvent
publicado por los analizadores activos, y dispara AlarmEvent respetando un
cooldown por regla (evita spam de alarmas por detecciones repetidas del
mismo evento sostenido en el tiempo).

**En que hilo corre** (verificado el 2026-09-24,
tests/test_hilos.py::TestAlarmEngineEnQueHilo). No es un QObject, y
PySide6 entrega un signal a un callable comun a traves de un receptor que
vive en el hilo principal: como los AnalyticsWorker emiten desde su hilo, la
conexion queda ENCOLADA y `_on_detection` corre en el **hilo de la GUI**, no
en el del worker.

Por eso el trabajo se parte en dos (Fase 6b, 2026-10-07):
- **Evaluar** (reglas, horario, mejor deteccion, cooldown) queda en
  `_on_detection`, en la GUI: ~0.7-1.4 ms por evento, medido.
- **Disparar** (insert, snapshot JPEG, clip y el emit del AlarmEvent) corre
  en el hilo `AlarmTrigger`, que consume una cola. Antes eran ~35-41 ms de
  GUI congelada por disparo (mediana; 64 ms de maximo).

El cuadro del snapshot se toma al ENCOLAR, no al disparar: es el del momento
de la deteccion aunque la cola este atrasada. Los StreamWorker reemplazan
el array en cada cuadro (no lo pisan), asi que retener la referencia es
seguro.

Consecuencias:
- El cooldown se reserva al encolar. Si el disparo falla en el worker, la
  reserva se devuelve: el proximo evento EVALUADO DESPUES de la falla
  reintenta. Los que ya estaban en vuelo siguen frenados por la reserva.
- Una excepcion que se escape de `_on_detection` sube por el event loop de
  Qt; igual se captura todo (el patron de stream_manager y retention).
- Despues de stop() pueden quedar eventos encolados en Qt: `_on_detection`
  corta si el motor no esta activo. Los disparos que YA estaban en la cola
  propia si se procesan (son alarmas reales): stop() la vacia con timeout.
- Este modulo no construye widgets: las acciones que llegan al escritorio
  (`play_sound`, `notify_desktop`) viajan como flags del AlarmEvent y las
  ejecuta la UI. El emit sale del hilo AlarmTrigger y los receptores de la
  UI estan conectados con QueuedConnection, asi que corren en la GUI.
"""

from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import time
from dataclasses import dataclass
from time import monotonic
from typing import Any

from aurea_vms.core import clip_recorder, media_store
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import AlarmEvent as AlarmEventDTO
from aurea_vms.core.events import Detection, DetectionEvent
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository
from aurea_vms.models.alarm_rule import AlarmRule

logger = logging.getLogger(__name__)

# Cada disparo encolado retiene un cuadro (1080p BGR = ~6 MB). El cooldown de
# cada regla ya limita el ritmo; el tope es para que un insert trabado (lock
# de SQLite) no se coma la RAM. Lleno, el disparo se descarta, se loguea y
# se devuelve la reserva de cooldown, para que el proximo evento reintente.
MAX_DISPAROS_EN_COLA = 32
STOP_TIMEOUT_S = 5.0

_SIN_CUADRO: Any = object()  # "no me pasaron cuadro": lo busca _trigger


@dataclass(frozen=True)
class _Disparo:
    rule: AlarmRule
    event: DetectionEvent
    detection: Detection
    frame: Any
    reservado_en: float
    anterior: float | None


_FIN = object()  # sentinel de la cola


class AlarmEngine:
    def __init__(self) -> None:
        self._last_triggered: dict[int, float] = {}
        # El cooldown se toca desde la GUI (reserva) y desde AlarmTrigger
        # (devolucion si el disparo falla): siempre bajo este lock.
        self._cooldown_lock = threading.Lock()
        self._active = False
        self._detenido = False
        self._cola: queue.Queue = queue.Queue(maxsize=MAX_DISPAROS_EN_COLA)
        self._hilo: threading.Thread | None = None
        self._hilo_lock = threading.Lock()

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._detenido = False
        event_bus.detection.connect(self._on_detection)

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        self._detenido = True
        event_bus.detection.disconnect(self._on_detection)
        self._detener_hilo(STOP_TIMEOUT_S)

    def esperar_disparos(self, timeout: float = STOP_TIMEOUT_S) -> bool:
        """Bloquea hasta que la cola de disparos quede vacia y procesada.
        Devuelve False si vencio el timeout. Para tests y mediciones: en la
        app, quien espera es stop(). Usa `monotonic` importado aparte: los
        tests reemplazan el modulo `time` de aca por un reloj falso."""
        limite = monotonic() + timeout
        with self._cola.all_tasks_done:
            while self._cola.unfinished_tasks:
                restante = limite - monotonic()
                if restante <= 0:
                    return False
                self._cola.all_tasks_done.wait(restante)
        return True

    def _detener_hilo(self, timeout: float) -> None:
        with self._hilo_lock:
            hilo, self._hilo = self._hilo, None
        if hilo is None or not hilo.is_alive():
            return
        # El sentinel va DETRAS de los disparos pendientes: se procesan
        # todos (son alarmas reales) y despues el hilo sale.
        self._cola.put(_FIN)
        hilo.join(timeout)
        if hilo.is_alive():
            logger.error(
                "El hilo de disparos de alarmas no termino en %.0fs (quedan %d en cola)",
                timeout,
                self._cola.qsize(),
            )

    def _asegurar_hilo(self) -> None:
        """Arranca AlarmTrigger si no esta vivo. Tambien lo reemplaza si
        murio: `_procesar` captura todo, pero si algo se escapa (el patron
        de los workers de la Fase 5) la cola no puede quedar sin consumidor."""
        with self._hilo_lock:
            if self._hilo is not None and self._hilo.is_alive():
                return
            if self._hilo is not None:
                logger.error("El hilo de disparos de alarmas murio; se reemplaza")
            self._hilo = threading.Thread(target=self._consumir, name="AlarmTrigger", daemon=True)
            self._hilo.start()

    def _consumir(self) -> None:
        while True:
            item = self._cola.get()
            try:
                if item is _FIN:
                    return
                self._procesar(item)
            finally:
                self._cola.task_done()

    def _procesar(self, disparo: _Disparo) -> None:
        try:
            self._trigger(disparo.rule, disparo.event, disparo.detection, disparo.frame)
        except Exception:
            # Una regla que falla no puede dejar la regla muda hasta que
            # venza el cooldown: si el insert fallo por un lock transitorio,
            # se devuelve la reserva y el proximo evento reintenta.
            self._devolver_reserva(disparo.rule.id, disparo.reservado_en, disparo.anterior)
            logger.exception(
                "No se pudo disparar la alarma de la regla %s (cámara %s)",
                disparo.rule.id,
                disparo.event.device_id,
            )

    def _devolver_reserva(self, rule_id: int, reservado_en: float, anterior: float | None) -> None:
        with self._cooldown_lock:
            if self._last_triggered.get(rule_id) == reservado_en:
                if anterior is None:
                    self._last_triggered.pop(rule_id, None)
                else:
                    self._last_triggered[rule_id] = anterior

    def _encolar(self, disparo: _Disparo) -> None:
        self._asegurar_hilo()
        try:
            self._cola.put_nowait(disparo)
        except queue.Full:
            self._devolver_reserva(disparo.rule.id, disparo.reservado_en, disparo.anterior)
            logger.error(
                "Cola de disparos llena (%d): se descarta la alarma de la regla %s (cámara %s)",
                MAX_DISPAROS_EN_COLA,
                disparo.rule.id,
                disparo.event.device_id,
            )

    def _on_detection(self, event: DetectionEvent) -> None:
        if self._detenido:
            # Eventos que quedaron encolados antes del stop() (logout): la
            # conexion es encolada (ver el docstring), asi que desconectar
            # no descarta los que ya estaban en la cola.
            return
        candidates = event.triggers if event.triggers is not None else event.detections
        if not candidates:
            return

        # Corre en la GUI (ver el docstring). Una excepcion que se escape de
        # aca sube cruda por el event loop de Qt. Todo acceso a la DB va
        # protegido y logueado -- el mismo patron que
        # stream_manager._report_status y retention.
        try:
            rules = repository.list_alarm_rules_for(event.device_id, event.analyzer_name)
        except Exception:
            logger.exception(
                "No se pudieron leer las reglas de alarma de la cámara %s (%s)",
                event.device_id,
                event.analyzer_name,
            )
            return

        for rule in rules:
            if not self._within_schedule(rule):
                continue

            match = self._best_match(rule, candidates)
            if match is None:
                continue

            now = time.time()
            # Chequear y reservar el cooldown es UNA operacion bajo el lock:
            # separadas, dos eventos a la vez pasaban los dos el chequeo y
            # la regla disparaba dos alarmas.
            with self._cooldown_lock:
                anterior = self._last_triggered.get(rule.id)
                if now - (anterior or 0.0) < rule.cooldown_seconds:
                    continue
                self._last_triggered[rule.id] = now

            try:
                # El cuadro se toma ahora, en la GUI: es una referencia, no
                # una copia (ver el docstring), asi que cuesta microsegundos.
                worker = stream_manager.get_worker(event.device_id)
                frame = worker.get_latest_frame() if worker else None
                self._encolar(_Disparo(rule, event, match, frame, now, anterior))
            except Exception:
                self._devolver_reserva(rule.id, now, anterior)
                logger.exception(
                    "No se pudo encolar la alarma de la regla %s (cámara %s)",
                    rule.id,
                    event.device_id,
                )

    @staticmethod
    def _within_schedule(rule: AlarmRule) -> bool:
        """Vacio en dias/horario = sin restriccion (siempre activa)."""
        now = dt.datetime.now()
        if rule.schedule_days and now.weekday() not in rule.schedule_days:
            return False
        if rule.schedule_start and rule.schedule_end:
            current = now.strftime("%H:%M")
            if rule.schedule_start <= rule.schedule_end:
                if not (rule.schedule_start <= current <= rule.schedule_end):
                    return False
            # rango que cruza medianoche (ej. 22:00 a 06:00)
            elif not (current >= rule.schedule_start or current <= rule.schedule_end):
                return False
        return True

    @staticmethod
    def _best_match(rule: AlarmRule, detections: tuple[Detection, ...]) -> Detection | None:
        allowed = set(rule.object_classes) if rule.object_classes else None
        candidates = [
            d
            for d in detections
            if d.confidence >= rule.min_confidence and (allowed is None or d.label in allowed)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda d: d.confidence)

    @staticmethod
    def _trigger(
        rule: AlarmRule,
        event: DetectionEvent,
        detection: Detection,
        frame: Any = _SIN_CUADRO,
    ) -> None:
        """Corre en el hilo AlarmTrigger. `frame` es el cuadro tomado al
        encolar; sin el, se busca el ultimo del stream (llamada directa)."""
        row = repository.add_alarm_event(
            rule_id=rule.id,
            device_id=event.device_id,
            timestamp=event.timestamp,
            object_class=detection.label,
            confidence=detection.confidence,
            severity=rule.severity,
        )
        logger.info(
            "ALARMA regla=%s cámara=%s clase=%s confianza=%.2f severidad=%s",
            rule.id,
            event.device_id,
            detection.label,
            detection.confidence,
            rule.severity,
        )

        snapshot_path = None
        if frame is _SIN_CUADRO:
            worker = stream_manager.get_worker(event.device_id)
            frame = worker.get_latest_frame() if worker else None
        if frame is not None:
            # Queda registrada en media_assets (vinculada al evento); el DTO
            # lleva la ruta absoluta solo para el thumbnail del popup.
            # Puede devolver None: una captura que no se pudo escribir se
            # loguea y se descarta, pero el incidente sigue su curso. Antes
            # la excepcion subia hasta el except por regla de _on_detection y
            # se perdia la alarma ENTERA por no haber podido escribir un jpg.
            asset = clip_recorder.save_snapshot(event.device_id, row.id, frame)
            if asset is not None:
                snapshot_path = str(media_store.absolute_path(asset.rel_path))

        if (rule.actions or {}).get("save_clip"):
            clip_recorder.record_clip_async(event.device_id, row.id)

        event_bus.alarm.emit(
            AlarmEventDTO(
                alarm_event_id=row.id,
                rule_id=rule.id,
                device_id=event.device_id,
                timestamp=event.timestamp,
                object_class=detection.label,
                confidence=detection.confidence,
                severity=rule.severity,
                snapshot_path=snapshot_path,
                # Las dos acciones que tocan el escritorio (beep y globo de
                # bandeja) viajan como flags: las ejecuta la UI en su hilo.
                # Este metodo corre en el hilo AlarmTrigger, donde construir
                # un widget de Qt es comportamiento indefinido.
                play_sound=bool((rule.actions or {}).get("play_sound")),
                notify_desktop=bool((rule.actions or {}).get("notify_desktop")),
            )
        )


alarm_engine = AlarmEngine()
