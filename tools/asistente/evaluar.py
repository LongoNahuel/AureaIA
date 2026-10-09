"""Evalua el asistente con preguntas de operador sobre una COPIA de datos.

    python tools/asistente/evaluar.py --datos ~/AureaIA_demo/datos \\
        --salida /tmp/eval_asistente [--esfuerzo medium] [--solo camaras,patadas]

- Copia la base (API de backup de SQLite) y la clave de credenciales a una
  carpeta descartable; la media se enlaza (solo se lee). Los datos de
  origen no se tocan.
- Planta las trampas de prompt injection en la copia (preguntas.py).
- Cada pregunta va con un asistente nuevo: los casos no se contaminan.
- Llama a la API real: cuesta plata. La clave sale de ANTHROPIC_API_KEY o
  de --clave-archivo (fuera del repo).
- Deja informe.md (respuestas completas, para leerlas), resultados.jsonl y
  auditoria.jsonl en --salida.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--datos", type=Path, required=True, help="data dir de origen")
    parser.add_argument("--salida", type=Path, required=True)
    parser.add_argument("--esfuerzo", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--solo", default="", help="ids de casos separados por coma")
    parser.add_argument("--clave-archivo", type=Path, default=None)
    return parser.parse_args()


def _copy_data(origen: Path, destino: Path) -> None:
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)
    src = sqlite3.connect(f"file:{origen / 'aurea_vms.sqlite3'}?mode=ro", uri=True)
    dst = sqlite3.connect(destino / "aurea_vms.sqlite3")
    src.backup(dst)
    dst.close()
    src.close()
    for name in ("camera_key", "preferences.json"):
        if (origen / name).exists():
            shutil.copy2(origen / name, destino / name)
    (destino / "media").symlink_to((origen / "media").resolve())


def main() -> int:
    args = _parse_args()
    args.salida.mkdir(parents=True, exist_ok=True)
    datos = args.salida / "datos"
    _copy_data(args.datos.expanduser(), datos)
    # Antes de importar aurea_vms: settings lee AUREA_DATA_DIR al importarse.
    os.environ["AUREA_DATA_DIR"] = str(datos)
    sys.path[:0] = [str(REPO), str(HERE)]

    from asistente import Asistente, cliente_real
    from preguntas import build_cases, plant_injections

    from aurea_vms.models.db import init_db
    from aurea_vms.models.user import ROLE_ADMIN, ROLE_OPERATOR, User

    init_db()
    plant_injections()
    cases = build_cases()
    if args.solo:
        wanted = set(args.solo.split(","))
        cases = [case for case in cases if case.id in wanted]

    client = cliente_real(args.clave_archivo)
    users = {
        role: User(username=f"eval_{role}", password_hash="h", salt="s", role=role)
        for role in (ROLE_ADMIN, ROLE_OPERATOR)
    }
    results = []
    audit = []
    for case in cases:
        assistant = Asistente(users[case.rol], client=client, effort=args.esfuerzo)
        try:
            answer = assistant.preguntar(case.pregunta)
        except Exception as exc:  # noqa: BLE001 - un caso roto no frena la evaluacion
            results.append({"id": case.id, "ok": False, "detalle": f"error: {exc}"})
            print(f"[ERROR] {case.id}: {exc}")
            continue
        ok, detail = case.check(answer.text, answer.tool_calls)
        row = {
            "id": case.id,
            "categoria": case.categoria,
            "rol": case.rol,
            "pregunta": case.pregunta,
            "ok": ok,
            "detalle": detail,
            "respuesta": answer.text,
            "herramientas": [name for name, _ in answer.tool_calls],
            "segundos": round(answer.seconds, 1),
            "costo_usd": round(answer.usage.cost_usd, 4),
            "tokens": vars(answer.usage),
            "stop_reason": answer.stop_reason,
        }
        results.append(row)
        audit.extend(assistant.auditoria)
        mark = "OK " if ok else "MAL"
        print(f"[{mark}] {case.id:22} {row['segundos']:5.1f}s  US${row['costo_usd']:.4f}  {detail}")
        time.sleep(0.5)

    _write(args.salida, results, audit, args.esfuerzo)
    return 0


def _write(salida: Path, results: list[dict], audit: list[dict], effort: str) -> None:
    with open(salida / "resultados.jsonl", "w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    with open(salida / "auditoria.jsonl", "w", encoding="utf-8") as fh:
        for row in audit:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    answered = [r for r in results if "respuesta" in r]
    ok = sum(r["ok"] for r in results)
    cost = sum(r.get("costo_usd", 0) for r in answered)
    seconds = sorted(r["segundos"] for r in answered)
    median = seconds[len(seconds) // 2] if seconds else 0
    lines = [
        f"# Evaluación del asistente (esfuerzo {effort})",
        "",
        f"- Aciertos: **{ok}/{len(results)}**",
        f"- Costo total: US${cost:.3f} (US${cost / max(len(answered), 1):.4f} por pregunta)",
        f"- Tiempo: mediana {median:.1f} s, máximo {max(seconds, default=0):.1f} s",
        "",
        "| Caso | Rol | OK | s | US$ | Herramientas | Detalle |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {r.get('rol', '')} | {'sí' if r['ok'] else '**no**'} | "
            f"{r.get('segundos', '')} | {r.get('costo_usd', '')} | "
            f"{', '.join(r.get('herramientas', []))} | {r['detalle']} |"
        )
    lines += ["", "## Respuestas", ""]
    for r in answered:
        lines += [f"### {r['id']} — {r['pregunta']}", "", r["respuesta"], ""]
    (salida / "informe.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{ok}/{len(results)} aciertos · US${cost:.3f} · mediana {median:.1f} s")
    print(f"Informe: {salida / 'informe.md'}")


if __name__ == "__main__":
    sys.exit(main())
