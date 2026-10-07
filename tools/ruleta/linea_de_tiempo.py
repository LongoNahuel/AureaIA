"""Linea de tiempo del modo ruleta sobre un clip: la ronda, las jugadas que
abre la rueda, los movimientos de fichas, las alarmas y quien es el crupier,
con el costo por muestra. Sirve para recalibrar con videos nuevos y para
comprobar que un cambio no agregue alertas falsas: el 2026-09-30 la RA-04
(44-218 s) y la AR 4171 dieron cero.

La deteccion de manos corre en linea (no en su hilo), para que el resultado
no dependa de la carga de la maquina.

    python tools/ruleta/linea_de_tiempo.py tools/demo/media/cam7.mp4 \\
        --rueda 1167.7 1063.7 442.4 556.1 87.3 --zona 1004 24 404 668 --desde 44

    python tools/ruleta/linea_de_tiempo.py tools/demo/media/cam4.mp4 \\
        --rueda 934.3 1005.0 426.9 514.3 90.8 --zona 728 140 368 512
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aurea_vms.core.analytics.roulette_analyzer import (  # noqa: E402
    RouletteAnalyzer,
    WheelEllipse,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clip")
    parser.add_argument(
        "--rueda",
        nargs=5,
        type=float,
        metavar=("CX", "CY", "ANCHO", "ALTO", "ANGULO"),
        help="la elipse del plato (params['rueda'] de la config); sin ella se busca sola",
    )
    parser.add_argument(
        "--zona", nargs=4, type=int, action="append", default=[], metavar=("X", "Y", "W", "H")
    )
    parser.add_argument("--desde", type=float, default=0.0, help="segundo del clip")
    parser.add_argument("--hasta", type=float, default=None)
    parser.add_argument("--fps", type=float, default=10.0, help="muestras por segundo")
    parser.add_argument("--no-va-mas", type=float, default=200.0, help="grados/s de la bola")
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.clip)
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_MSEC, args.desde * 1000)
    analyzer = RouletteAnalyzer(
        zones=[tuple(zone) for zone in args.zona],
        wheel=WheelEllipse(*args.rueda) if args.rueda else None,
        no_more_bets_deg_s=args.no_va_mas,
        background_hands=False,
    )
    step = video_fps / args.fps
    index, next_sample = 0, 0.0
    last_round, last_croupier, costs, alarms = None, None, [], 0
    try:
        while True:
            ok, frame = cap.read()
            t = args.desde + index / video_fps
            index += 1
            if not ok or (args.hasta is not None and t > args.hasta):
                break
            if index - 1 < next_sample:
                continue
            next_sample += step
            start = time.perf_counter()
            result = analyzer.process_frame(frame, t)
            costs.append(time.perf_counter() - start)
            metrics = result.metrics
            ronda = metrics["ronda"]
            game = ronda.get("ultima_jugada")
            if game and game["t"] == t:
                print(f"{t:7.1f}s  JUGADA NUEVA  la rueda: {game['tipo']}")
            state = (ronda["estado"], ronda["pano_armado"])
            if state != last_round:
                speed = ronda["bola_deg_s"]
                ball = f"  bola {speed:.0f} °/s" if speed else ""
                armed = "  paño ARMADO" if ronda["pano_armado"] else ""
                print(f"{t:7.1f}s  ronda {ronda['estado']}{armed}{ball}")
                last_round = state
            for detection in result.detections:
                alarm = detection in result.triggers
                alarms += alarm
                hands = [zone["manos"] for zone in metrics["zonas"]]
                print(
                    f"{t:7.1f}s  {'ALARMA ' if alarm else ''}{detection.label} en "
                    f"{detection.bbox}  manos en las zonas: {hands}"
                )
            croupier = next((p["id"] for p in metrics["personas"] if p["rol"] == "crupier"), None)
            if croupier != last_croupier:
                print(f"{t:7.1f}s  crupier: pista {croupier}")
                last_croupier = croupier
    finally:
        analyzer.close()
    if costs:
        costs.sort()
        print(
            f"alarmas: {alarms} · ms por muestra: media {1000 * sum(costs) / len(costs):.0f}, "
            f"p95 {1000 * costs[int(0.95 * (len(costs) - 1))]:.0f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
