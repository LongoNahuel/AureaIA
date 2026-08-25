"""Sonido de alarma (accion "play_sound" de una regla).

QSoundEffect con instancia persistente a nivel de modulo: si se crea una
por reproduccion, el GC la puede matar antes de que termine el beep. El
import de QtMultimedia y la reproduccion van envueltos en try/except:
una maquina sin backend de audio no debe romper el flujo de alarmas.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QUrl

logger = logging.getLogger(__name__)

_effect = None
_ALARM_WAV = Path(__file__).parent / "assets" / "sounds" / "alarm.wav"


def play_alarm() -> None:
    global _effect
    try:
        if _effect is None:
            from PySide6.QtMultimedia import QSoundEffect

            if not _ALARM_WAV.exists():
                logger.warning("Sonido de alarma ausente: %s", _ALARM_WAV)
                return
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(str(_ALARM_WAV)))
            effect.setVolume(0.9)
            _effect = effect
        _effect.play()
    except Exception:
        logger.exception("No se pudo reproducir el sonido de alarma")
