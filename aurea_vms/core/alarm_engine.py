"""Evalua las reglas de alarma configuradas contra cada DetectionEvent
publicado por los analizadores activos, y dispara AlarmEvent respetando un
cooldown por regla (evita spam de alarmas por detecciones repetidas del
mismo evento sostenido en el tiempo).

No es un QObject: se conecta directamente a la signal `detection` del
EventBus, asi que corre en el mismo thread que emite ese evento (el
AnalyticsWorker correspondiente) -- el trabajo que hace (un insert en la
DB + re-emitir un evento) es liviano, no hace falta marshalear a otro hilo.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

from aurea_vms.core import clip_recorder, desktop_notify, media_store
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
        if not event.detections:
            return

        for rule in repository.list_alarm_rules_for(event.device_id, event.analyzer_name):
            if not self._within_schedule(rule):
                continue

            now = time.time()
            if now - self._last_triggered.get(rule.id, 0.0) < rule.cooldown_seconds:
                continue

            match = self._best_match(rule, event.detections)
            if match is None:
                continue

            self._last_triggered[rule.id] = now
            self._trigger(rule, event, match)

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
            asset = clip_recorder.save_snapshot(event.device_id, row.id, frame)
            snapshot_path = str(media_store.absolute_path(asset.rel_path))

        if (rule.actions or {}).get("save_clip"):
            clip_recorder.record_clip_async(event.device_id, row.id)

        if (rule.actions or {}).get("notify_desktop"):
            device = repository.get_device(event.device_id)
            device_name = device.name if device else f"Cámara {event.device_id}"
            desktop_notify.notify(
                f"Alarma ({rule.severity}) — {device_name}",
                f"{detection.label} detectado con {detection.confidence:.0%} de confianza.",
            )

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
                play_sound=bool((rule.actions or {}).get("play_sound")),
            )
        )


alarm_engine = AlarmEngine()
