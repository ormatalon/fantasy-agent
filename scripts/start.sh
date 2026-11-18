#!/bin/sh
cd "$(dirname "$0")/.." || exit 1
docker compose up -d --build --wait && echo "Fantasy Agent: http://localhost:8080"
