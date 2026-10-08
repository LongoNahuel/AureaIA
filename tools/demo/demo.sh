#!/usr/bin/env bash
# Demo del VMS en esta laptop (2026-10-07): levanta las camaras simuladas si no
# estan publicando y abre el VMS sobre los datos de la demo, nunca sobre data/.
#
#   tools/demo/demo.sh             el sistema cargado ($DEMO_DIR/datos)
#   tools/demo/demo.sh nuevo       desde cero: base vacia, arranca el asistente
#                                  de primer uso ($DEMO_DIR/nuevo, se vacia)
#   tools/demo/demo.sh restaurar   vuelve $DEMO_DIR/datos a como se preparo
#
# Los datos se arman con tools/demo/preparar_demo.py --clave <clave del admin>.
# Las camaras quedan publicando al cerrar el VMS (para volver a abrirlo al
# toque); se detienen con: pkill -f start_fake_cams.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
DEMO_DIR="${DEMO_DIR:-$HOME/AureaIA_demo}"
MARKER=".demo_aurea"

fresh_dir() {  # fresh_dir <carpeta>: la vacia solo si es de la demo
    local path="$1"
    if [[ -e "$path" && ! -e "$path/$MARKER" ]]; then
        echo "$path existe y no es una carpeta de demo: no la toco" >&2
        exit 1
    fi
    rm -rf "$path"
    mkdir -p "$path"
    echo "carpeta de la demo; la arma tools/demo/demo.sh" > "$path/$MARKER"
}

# Antes de todo: "nuevo" y "restaurar" borran carpetas que un VMS abierto
# podria estar usando, y dos VMS a la vez compiten por el CPU.
# Anclado: el proceso es "<python> <ruta>/aurea_run.py". Sin anclar tambien
# coincidia con cualquier shell cuyo comando mencionara las dos palabras.
if pgrep -f "^[^ ]*python[0-9.]* [^ ]*aurea_run\.py" >/dev/null; then
    echo "Ya hay un VMS abierto: cerralo antes." >&2
    exit 1
fi

mode="${1:-}"
case "$mode" in
    "") data="$DEMO_DIR/datos" ;;
    nuevo)
        data="$DEMO_DIR/nuevo"
        fresh_dir "$data"
        ln -s "$REPO/data/models" "$data/models"  # sin internet: los modelos del repo
        ;;
    restaurar)
        if [[ ! -e "$DEMO_DIR/datos_original/$MARKER" ]]; then
            echo "No hay $DEMO_DIR/datos_original: correr tools/demo/preparar_demo.py" >&2
            exit 1
        fi
        fresh_dir "$DEMO_DIR/datos"
        cp -a "$DEMO_DIR/datos_original/." "$DEMO_DIR/datos/"
        echo "Datos de la demo restaurados."
        exit 0
        ;;
    *)
        echo "uso: $0 [nuevo|restaurar]" >&2
        exit 2
        ;;
esac

if [[ ! -e "$data/$MARKER" ]]; then
    echo "No hay datos de demo en $data: correr tools/demo/preparar_demo.py" >&2
    exit 1
fi

# Camaras simuladas: start_fake_cams.sh levanta mediamtx si hace falta y
# relanza cada publicacion que se corte.
if ! pgrep -f "^[^ ]*bash [^ ]*start_fake_cams\.sh" >/dev/null; then
    echo "Levantando las camaras simuladas..."
    nohup "$DIR/start_fake_cams.sh" > "$DEMO_DIR/camaras.log" 2>&1 &
fi
for _ in $(seq 1 30); do
    if timeout 5 ffprobe -v error -rtsp_transport tcp -show_entries stream=codec_name \
        rtsp://127.0.0.1:8554/cam7 >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "Abriendo el VMS sobre $data"
cd "$REPO"
AUREA_DATA_DIR="$data" exec .venv/bin/python aurea_run.py
