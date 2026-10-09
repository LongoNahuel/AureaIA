# Asistente operacional — prototipo (2026-10-08)

Prototipo para medir si un asistente IA dentro del VMS sirve, antes de
llevarlo a `core/`: cuánto acierta, cuánto tarda y cuánto cuesta por pregunta,
y si respeta los permisos y no obedece texto plantado en los datos.

## Qué es

- `asistente.py`: asistente de **solo lectura** con Claude Opus 5.5
  (`claude-opus-5-5`).
  - **Herramientas:** 7, sobre el repository: cámaras, incidentes (lista,
    detalle y resumen), estado de las analíticas, reglas de alerta y captura
    de un incidente.
  - **Permisos:** cada herramienta declara el suyo
    (`core/permissions.py`) y al modelo solo se le ofrecen las que el rol
    puede usar.
  - **Auditoría:** cada consulta queda en `Asistente.auditoria`.
  - **Pedido al modelo:** caché del prompt, esfuerzo configurable y
    respaldo ante rechazos (`fallbacks`).
  - **Privacidad:** viaja solo texto; una captura sale únicamente si el
    usuario la pide.
- `preguntas.py`: 20 preguntas de operador. Cada una calcula lo esperado
  desde la base al evaluar. Incluye permisos, solo lectura, dato
  inexistente y dos trampas de prompt injection (una nota de operador y un
  nombre de cámara).
- `evaluar.py`: corre las preguntas sobre una **copia** de los datos y deja
  `informe.md` (con las respuestas completas), `resultados.jsonl` y
  `auditoria.jsonl`.

## Cómo correrlo

Necesita el SDK (`pip install anthropic`). No es dependencia del proyecto:
los tests (`tests/test_asistente_poc.py`) usan un modelo falso y no lo
necesitan.

La clave va en un archivo **fuera del repo**:

```bash
mkdir -p ~/.config/aureaia && chmod 700 ~/.config/aureaia
nano ~/.config/aureaia/anthropic_api_key   # pegar la clave
chmod 600 ~/.config/aureaia/anthropic_api_key
```

```bash
python tools/asistente/evaluar.py --datos ~/AureaIA_demo/datos \
    --salida /tmp/eval_asistente --clave-archivo ~/.config/aureaia/anthropic_api_key
```

**Llama a la API real y cuesta plata:** se estima US$1–2 las 20 preguntas
con esfuerzo medio. Los datos de origen no se tocan.

## Estado

Escrito y testeado con un modelo falso. La evaluación con el modelo real
está pendiente de la clave.
