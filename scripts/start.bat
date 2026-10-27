@echo off
REM Starts Fantasy Agent at http://localhost:8000.
REM --build builds the image from this repo instead of downloading it.
cd /d "%~dp0.."
if "%~1"=="--build" (
  docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build || goto :error
) else (
  docker compose pull || goto :error
  docker compose up -d || goto :error
)
echo Fantasy Agent is running at http://localhost:8000
start "" http://localhost:8000
goto :eof
:error
echo Could not start Fantasy Agent. Is Docker Desktop running?
pause
exit /b 1
