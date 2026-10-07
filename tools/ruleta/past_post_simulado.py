"""Past-post simulado sobre un clip real: en el segundo --en, una "mano" pasa
por el paño y deja una ficha copiada de otra parte de la mesa. Tiene que
salir la alarma "fichas_tras_no_va_mas" con la posicion de la ficha si en ese
momento el paño esta armado (despues del no va mas) y no si esta libre.

El 2026-09-30, con una ficha crema pegada a los 68,4 s de la RA-04 (bola
todavia girando): alarma a los 69,9 s en (1062, 498, 46, 50); la ficha estaba
en (1062, 500, 44, 44). Los clips de la demo no traen un past-post real.

    python tools/ruleta/past_post_simulado.py tools/demo/media/cam7.mp4 \\
        --rueda 1167.7 1063.7 442.4 556.1 87.3 --zona 1004 24 404 668 \\
        --desde 44 --hasta 72 --en 68 --ficha 1118 296 --destino 1062 500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aurea_vms.core.analytics.roulette_analyzer import (  # noqa: E402
    RouletteAnalyzer,
    WheelEllipse,
)

CHIP = 44  # px del cuadro: una ficha de la RA-04
HAND_PASS_S = 0.8


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clip")
    parser.add_argument("--rueda", nargs=5, type=float, required=True)
    parser.add_argument("--zona", nargs=4, type=int, action="append", required=True)
    parser.add_argument("--desde", type=float, default=0.0)
    parser.add_argument("--hasta", type=float, required=True)
    parser.add_argument("--en", type=float, required=True, help="cuando pasa la mano")
    parser.add_argument("--ficha", nargs=2, type=int, required=True, help="de donde copiarla")
    parser.add_argument("--destino", nargs=2, type=int, required=True, help="donde dejarla")
    parser.add_argument("--fps", type=float, default=10.0)
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.clip)
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_MSEC, args.desde * 1000)
    analyzer = RouletteAnalyzer(
        zones=[tuple(zone) for zone in args.zona],
        wheel=WheelEllipse(*args.rueda),
        background_hands=False,
    )
    (sx, sy), (dx, dy) = args.ficha, args.destino
    chip = None
    index, next_sample, alarms = 0, 0.0, 0
    try:
        while True:
            ok, frame = cap.read()
            t = args.desde + index / video_fps
            index += 1
            if not ok or t > args.hasta:
                break
            if chip is None:
                chip = frame[sy : sy + CHIP, sx : sx + CHIP].copy()
            if index - 1 < next_sample:
                continue
            next_sample += video_fps / args.fps
            frame = frame.copy()
            if t >= args.en + HAND_PASS_S / 2:
                frame[dy : dy + CHIP, dx : dx + CHIP] = chip
            if args.en <= t < args.en + HAND_PASS_S:
                k = (t - args.en) / HAND_PASS_S
                center = (int(dx + 90 - 90 * k), int(dy + 60 - 40 * k))
                cv2.ellipse(frame, center, (80, 45), 30, 0, 360, (120, 150, 200), -1)
            result = analyzer.process_frame(frame, t)
            ronda = result.metrics["ronda"]
            for detection in result.detections:
                alarm = detection in result.triggers
                alarms += alarm
                print(
                    f"{t:7.1f}s  {'ALARMA ' if alarm else ''}{detection.label} en {detection.bbox}"
                    f"  (ronda {ronda['estado']}, paño {'armado' if ronda['pano_armado'] else 'libre'})"
                )
    finally:
        analyzer.close()
    print(f"alarmas: {alarms}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
