"""Evalua las reglas de alarma configuradas contra cada DetectionEvent
publicado por los analizadores activos, y dispara AlarmEvent respetando un
cooldown por regla (evita spam de alarmas por detecciones repetidas del
mismo evento sostenido en el tiempo).

No es un QObject: se conecta directamente a la signal `detection` del
EventBus, asi que corre en el mismo thread que emite ese evento (el
AnalyticsWorker correspondiente) -- el trabajo que hace (un insert en la
DB + re-emitir un evento) es liviano, no hace falta marshalear a otro hilo.
Como contrapartida, `_on_detection` es la frontera del hilo: nada puede
escaparse de ahi sin capturar, o se cae el AnalyticsWorker que lo llamo. Y
por el mismo motivo este modulo no construye ni toca un solo widget: las
acciones de una regla que llegan al escritorio (`play_sound`,
`notify_desktop`) viajan como flags del AlarmEvent y las ejecuta la UI.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

from aurea_vms.core import clip_recorder, media_store
from aurea_vms.core.event_bus import event_bus
from aurea_vms.core.events import AlarmEvent as AlarmEventDTO
from aurea_vms.core.events import Detection, DetectionEvent
from aurea_vms.core.stream_manager import stream_manager
from aurea_vms.models import repository
from aurea_vms.models.alarm_rule import AlarmRule

logger = logging.getLogger(__name__)


class AlarmEngine:
    def __init__(self) -> None:
        self._last_triggered: dict[int, float] = {}
        self._active = False

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        event_bus.detection.connect(self._on_detection)

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        event_bus.detection.disconnect(self._on_detection)

    def _on_detection(self, event: DetectionEvent) -> None:
        candidates = event.triggers if event.triggers is not None else event.detections
        if not candidates:
            return

        # Este metodo es un slot conectado a una signal que emiten los
        # AnalyticsWorker: corre en SU hilo. Una excepcion que se escape de
        # aca sube cruda por el slot de Qt y se lleva puesto el hilo de la
        # analitica. Todo acceso a la DB va protegido y logueado -- el mismo
        # patron que stream_manager._report_status y retention.
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

            now = time.time()
            if now - self._last_triggered.get(rule.id, 0.0) < rule.cooldown_seconds:
                continue

            match = self._best_match(rule, candidates)
            if match is None:
                continue

            try:
                self._trigger(rule, event, match)
            except Exception:
                # Una regla que falla no puede cortar la evaluacion de las
                # demas, ni matar el hilo de la analitica.
                logger.exception(
                    "No se pudo disparar la alarma de la regla %s (cámara %s)",
                    rule.id,
                    event.device_id,
                )
                continue

            # El cooldown se consume DESPUES de disparar bien: si el insert
            # fallo por un lock transitorio, el proximo frame reintenta en
            # vez de quedarse mudo hasta que venza el cooldown.
            self._last_triggered[rule.id] = now

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
    def _trigger(rule: AlarmRule, event: DetectionEvent, detection: Detection) -> None:
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
                # Este metodo corre en el hilo del AnalyticsWorker, donde
                # construir un widget de Qt es comportamiento indefinido.
                play_sound=bool((rule.actions or {}).get("play_sound")),
                notify_desktop=bool((rule.actions or {}).get("notify_desktop")),
            )
        )


alarm_engine = AlarmEngine()
