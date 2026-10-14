@echo off
setlocal
title ParqueaderoPRO - Prueba LPR Webcam
cd /d "%~dp0"

set "CAMERA_ROLE=entrada"
set "CAMERA_URL=0"
set "BACKEND_URL=https://parqueaderopro-api.onrender.com"
set "LPR_USERNAME=admin"
set "LPR_DEBUG=1"

echo.
echo ParqueaderoPRO - prueba con webcam USB
echo Backend: %BACKEND_URL%
echo.
set /p "LPR_PASSWORD=Contraseña del usuario admin: "
if not defined LPR_PASSWORD (
    echo ERROR: Debes indicar la contraseña del usuario admin.
    pause
    exit /b 1
)

if exist "%~dp0..\.venv_lpr\Scripts\python.exe" (
    set "PYTHON=%~dp0..\.venv_lpr\Scripts\python.exe"
) else (
    echo ERROR: No existe .venv_lpr.
    echo Crea el entorno Python 3.11 e instala LPR_Cliente\requirements.txt.
    pause
    exit /b 1
)

echo.
echo Iniciando webcam USB con indice 0...
echo Coloca una placa dentro del recuadro verde.
echo Cierra la ventana de video para detener la prueba.
echo.
"%PYTHON%" -X utf8 "%~dp0lpr_test.py"
pause
