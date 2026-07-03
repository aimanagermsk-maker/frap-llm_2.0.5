#!/usr/bin/env bash
# Локальная сборка и запуск (Linux / macOS / Git Bash на Windows).

set -euo pipefail

IMAGE_NAME=frap-llm-helper-img
CONTAINER_NAME=frap-llm-helper
APP_PORT=8000
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true
docker image rm -f "$IMAGE_NAME" 2>/dev/null || true

docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"

docker run -d \
  -p "${APP_PORT}:${APP_PORT}" \
  --restart unless-stopped \
  --name "$CONTAINER_NAME" \
  -e "PYTHON_PROFILES_ACTIVE=${PYTHON_PROFILES_ACTIVE}" \
  "$IMAGE_NAME"

echo "Started ${CONTAINER_NAME} with PYTHON_PROFILES_ACTIVE=${PYTHON_PROFILES_ACTIVE}"
echo "http://localhost:${APP_PORT}/hello"
echo "http://localhost:${APP_PORT}/docs"
