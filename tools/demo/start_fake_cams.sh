#!/usr/bin/env bash
# Publica cada clip tools/demo/media/camN.mp4 en loop como una camara RTSP
# (rtsp://RTSP_HOST/camN): todas las simulaciones quedan como canales de
# video para probar las analiticas en cualquiera.
#
# - Sin recodificar (-c:v copy): los clips de la demo son H.264 con un cuadro
#   clave cada 2 s (medido el 2026-10-02), asi que un cliente arranca a ver en
#   2 s como mucho y publicar 7 camaras casi no usa CPU. Antes se recodificaba
#   cada una con libx264, y con varias de 2048x1536 se comia el CPU que
#   necesitan las analiticas. REENCODE=1 vuelve a recodificar (para un clip
#   con cuadros clave muy espaciados o que no sea H.264).
# - Si una publicacion se corta (se reinicio mediamtx), se relanza sola.
# - Si no hay servidor RTSP escuchando, levanta el mediamtx del repo
#   (tools/demo/mediamtx con mediamtx.yml) y lo baja al salir.
#
# Requiere ffmpeg en el PATH. Ctrl+C detiene todo.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CLIPS_DIR="${CLIPS_DIR:-$DIR/media}"
RTSP_HOST="${RTSP_HOST:-127.0.0.1:8554}"
FFMPEG="${FFMPEG:-ffmpeg}"
REENCODE="${REENCODE:-0}"

pids=()
cleanup() {
    # Primero el bucle de cada camara (para que no relance) y despues su
    # ffmpeg, que no es hijo directo de este script. Al ffmpeg, -9: publicando
    # por RTSP ignora SIGTERM (medido: seguia vivo), y no tiene nada que guardar.
    for pid in "${pids[@]}"; do
        local children
        children="$(pgrep -P "$pid" || true)"
        kill "$pid" 2>/dev/null || true
        [[ -n "$children" ]] && kill -9 $children 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

host="${RTSP_HOST%%:*}"
port="${RTSP_HOST##*:}"
if ! (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then
    if [[ -x "$DIR/mediamtx" ]]; then
        "$DIR/mediamtx" "$DIR/mediamtx.yml" >/dev/null 2>&1 &
        pids+=($!)
        echo "mediamtx levantado en $RTSP_HOST (pid ${pids[-1]})"
        sleep 1
    else
        echo "No hay servidor RTSP en $RTSP_HOST ni $DIR/mediamtx (ver README.md)" >&2
        exit 1
    fi
fi

if [[ "$REENCODE" == "1" ]]; then
    codec=(-c:v libx264 -preset veryfast -tune zerolatency -g 50)
else
    codec=(-c:v copy)
fi

publish() {  # publish <clip> <path>: en loop, y relanza si se corta
    local clip="$1" path="$2"
    while true; do
        "$FFMPEG" -nostdin -hide_banner -loglevel error \
            -re -stream_loop -1 -i "$clip" "${codec[@]}" -an \
            -f rtsp -rtsp_transport tcp "rtsp://$RTSP_HOST/$path" || true
        echo "$path: la publicacion se corto, se relanza en 2 s" >&2
        sleep 2
    done
}

shopt -s nullglob
clips=("$CLIPS_DIR"/cam*.mp4)
if [[ ${#clips[@]} -eq 0 ]]; then
    echo "No hay clips cam*.mp4 en $CLIPS_DIR (ver README.md)" >&2
    exit 1
fi
mapfile -t clips < <(printf '%s\n' "${clips[@]}" | sort -V)
for clip in "${clips[@]}"; do
    path="$(basename "$clip" .mp4)"
    publish "$clip" "$path" &
    pids+=($!)
    echo "$path -> rtsp://$RTSP_HOST/$path"
done

echo "${#clips[@]} camaras publicando. Ctrl+C para detener."
wait
