#!/bin/sh
# Stops Fantasy Agent. Your settings, league data and chats are kept.
set -e
cd "$(dirname "$0")/.."
docker compose down
