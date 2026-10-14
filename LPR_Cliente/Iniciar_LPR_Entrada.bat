@echo off
title ParqueaderoPRO - LPR Entrada
cd /d "%~dp0"

set "LPR_ROLE=entrada"
set "CAMERA_ROLE=entrada"
set "CAMERA_URL=rtsp://192.168.1.101:554/Streaming/Channels/101"
set "CAMERA_USER=admin"
set "CAMERA_PASS=CAMBIA_ESTA_CLAVE"
set "BACKEND_URL=http://127.0.0.1:8000"
set "LPR_USERNAME=admin"
set "LPR_PASSWORD=AdminLocal2026!"

if exist "%~dp0..\.venv_lpr\Scripts\python.exe" (
    set "PYTHON=%~dp0..\.venv_lpr\Scripts\python.exe"
) else (
    echo ERROR: No existe .venv_lpr. Cree el entorno Python 3.11 e instale requirements.txt.
    pause
    exit /b 1
)

echo Iniciando camara de ENTRADA: %CAMERA_URL%
"%PYTHON%" -X utf8 "%~dp0lpr_test.py"
pause
