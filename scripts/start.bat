@echo off
cd /d "%~dp0.."
docker compose up -d --build --wait && echo Fantasy Agent: http://localhost:8080
