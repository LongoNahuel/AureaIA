@echo off
rem Publica 4 clips en loop como camaras RTSP contra un mediamtx corriendo.
rem Requiere: mediamtx ya levantado (ver README.md) y ffmpeg en el PATH.
setlocal

set "DIR=%~dp0"
if "%CLIPS_DIR%"=="" set "CLIPS_DIR=%DIR%media"
if "%RTSP_HOST%"=="" set "RTSP_HOST=127.0.0.1:8554"

for %%i in (1 2 3 4) do (
    if exist "%CLIPS_DIR%\cam%%i.mp4" (
        start "cam%%i" /min ffmpeg -nostdin -hide_banner -loglevel error ^
            -re -stream_loop -1 -i "%CLIPS_DIR%\cam%%i.mp4" ^
            -c:v libx264 -preset veryfast -tune zerolatency -g 50 -an ^
            -f rtsp -rtsp_transport tcp rtsp://%RTSP_HOST%/cam%%i
        echo cam%%i -^> rtsp://%RTSP_HOST%/cam%%i
    ) else (
        echo AVISO: falta %CLIPS_DIR%\cam%%i.mp4 ^(se omite cam%%i^)
    )
)

echo Camaras publicando en ventanas minimizadas; cerralas para detener.
endlocal
