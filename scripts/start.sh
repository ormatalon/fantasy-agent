#!/bin/sh
# Starts Fantasy Agent at http://localhost:8000.
# --build builds the image from this repo instead of downloading it.
set -e
cd "$(dirname "$0")/.."
if [ "$1" = "--build" ]; then
  docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
else
  docker compose pull
  docker compose up -d
fi
echo "Fantasy Agent is running at http://localhost:8000"
if command -v open >/dev/null 2>&1; then open http://localhost:8000
elif command -v xdg-open >/dev/null 2>&1; then xdg-open http://localhost:8000
fi
