@echo off
cd /d "%~dp0"
docker compose -f docker-compose.local.yml --env-file .env.local up -d
if errorlevel 1 (
  echo No se pudieron iniciar los servicios. Verifica Docker Desktop.
  pause
  exit /b 1
)
start "" "http://localhost:8080"
