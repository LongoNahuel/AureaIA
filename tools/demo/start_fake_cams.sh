#!/usr/bin/env bash
# Publica 4 clips en loop como camaras RTSP contra un mediamtx corriendo.
# Requiere: mediamtx ya levantado (ver README.md) y ffmpeg en el PATH.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CLIPS_DIR="${CLIPS_DIR:-$DIR/media}"
RTSP_HOST="${RTSP_HOST:-127.0.0.1:8554}"
FFMPEG="${FFMPEG:-ffmpeg}"

pids=()
for i in 1 2 3 4; do
    clip="$CLIPS_DIR/cam$i.mp4"
    if [[ ! -f "$clip" ]]; then
        echo "AVISO: falta $clip (se omite cam$i)" >&2
        continue
    fi
    # -re: tiempo real; -stream_loop -1: loop infinito; se re-encodea a
    # H.264 con GOP corto para que la conexion RTSP arranque rapido.
    "$FFMPEG" -nostdin -hide_banner -loglevel error \
        -re -stream_loop -1 -i "$clip" \
        -c:v libx264 -preset veryfast -tune zerolatency -g 50 -an \
        -f rtsp -rtsp_transport tcp "rtsp://$RTSP_HOST/cam$i" &
    pids+=($!)
    echo "cam$i -> rtsp://$RTSP_HOST/cam$i (pid ${pids[-1]})"
done

if [[ ${#pids[@]} -eq 0 ]]; then
    echo "No se publico ninguna camara: copia clips a $CLIPS_DIR (ver README.md)" >&2
    exit 1
fi

trap 'kill "${pids[@]}" 2>/dev/null || true' INT TERM
echo "Ctrl+C para detener las ${#pids[@]} camaras."
wait
