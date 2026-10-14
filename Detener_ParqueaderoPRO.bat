@echo off
cd /d "%~dp0"
docker compose -f docker-compose.local.yml --env-file .env.local down
pause
