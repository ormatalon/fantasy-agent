@echo off
REM Stops Fantasy Agent. Your settings, league data and chats are kept.
cd /d "%~dp0.."
docker compose down || pause
