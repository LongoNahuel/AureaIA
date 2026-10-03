@echo off
rem Publica cada clip media\camN.mp4 en loop como una camara RTSP
rem (rtsp://RTSP_HOST/camN): todas las simulaciones quedan como canales.
rem Sin recodificar (los clips son H.264 con un cuadro clave cada 2 s);
rem REENCODE=1 vuelve a recodificar con libx264. Si mediamtx.exe esta al lado
rem y no esta corriendo, lo levanta. Requiere ffmpeg en el PATH.
setlocal enabledelayedexpansion

set "DIR=%~dp0"
if "%CLIPS_DIR%"=="" set "CLIPS_DIR=%DIR%media"
if "%RTSP_HOST%"=="" set "RTSP_HOST=127.0.0.1:8554"
if "%REENCODE%"=="1" (
    set "CODEC=-c:v libx264 -preset veryfast -tune zerolatency -g 50"
) else (
    set "CODEC=-c:v copy"
)

tasklist /fi "imagename eq mediamtx.exe" | find /i "mediamtx.exe" >nul
if errorlevel 1 if exist "%DIR%mediamtx.exe" (
    start "mediamtx" /min "%DIR%mediamtx.exe" "%DIR%mediamtx.yml"
    echo mediamtx levantado en %RTSP_HOST%
    timeout /t 1 >nul
)

set COUNT=0
for %%f in ("%CLIPS_DIR%\cam*.mp4") do (
    set "CAM=%%~nf"
    start "!CAM!" /min ffmpeg -nostdin -hide_banner -loglevel error ^
        -re -stream_loop -1 -i "%%f" !CODEC! -an ^
        -f rtsp -rtsp_transport tcp rtsp://%RTSP_HOST%/!CAM!
    echo !CAM! -^> rtsp://%RTSP_HOST%/!CAM!
    set /a COUNT+=1
)
if %COUNT%==0 echo No hay clips cam*.mp4 en %CLIPS_DIR% (ver README.md)

echo %COUNT% camaras publicando en ventanas minimizadas; cerralas para detener.
endlocal
