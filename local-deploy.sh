#!/usr/bin/env bash
# Локальная сборка и запуск (Linux / macOS / Git Bash на Windows).

set -euo pipefail

IMAGE_NAME=frap-llm-helper-img
CONTAINER_NAME=frap-llm-helper
APP_PORT=8000
PYTHON_PROFILE="${PYTHON_PROFILES_ACTIVE:-sandbox}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

read_kafka_documents_home_dir() {
  local config_file="$1"
  [[ -f "$config_file" ]] || return 0

  awk '
    /^[^[:space:]#][^:]*:/ { in_kafka=0 }
    /^[[:space:]]*kafka:[[:space:]]*$/ { in_kafka=1; next }
    in_kafka && /^[[:space:]]*documents_home_dir:[[:space:]]*/ {
      sub(/^[[:space:]]*documents_home_dir:[[:space:]]*/, "")
      gsub(/^[ "\047]+|[ "\047]+$/, "")
      print
      exit
    }
  ' "$config_file"
}

DOCUMENTS_HOME_DIR="$(
  read_kafka_documents_home_dir "$SCRIPT_DIR/settings/user_settings.yaml"
)"

DOCKER_VOLUME_ARGS=()
if [[ -n "$DOCUMENTS_HOME_DIR" ]]; then
  if [[ "$DOCUMENTS_HOME_DIR" = /* ]]; then
    HOST_DOCUMENTS_HOME_DIR="$DOCUMENTS_HOME_DIR"
    CONTAINER_DOCUMENTS_HOME_DIR="$DOCUMENTS_HOME_DIR"
  else
    DOCUMENTS_HOME_DIR="${DOCUMENTS_HOME_DIR#./}"
    HOST_DOCUMENTS_HOME_DIR="$SCRIPT_DIR/$DOCUMENTS_HOME_DIR"
    CONTAINER_DOCUMENTS_HOME_DIR="/app/$DOCUMENTS_HOME_DIR"
  fi

  mkdir -p "$HOST_DOCUMENTS_HOME_DIR"
  DOCKER_VOLUME_ARGS=(-v "${HOST_DOCUMENTS_HOME_DIR}:${CONTAINER_DOCUMENTS_HOME_DIR}:ro")
fi

docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true
docker image rm -f "$IMAGE_NAME" 2>/dev/null || true

docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"

docker run -d \
  -p "${APP_PORT}:${APP_PORT}" \
  --restart unless-stopped \
  --name "$CONTAINER_NAME" \
  -e "PYTHON_PROFILES_ACTIVE=${PYTHON_PROFILE}" \
  "${DOCKER_VOLUME_ARGS[@]}" \
  "$IMAGE_NAME"

echo "Started ${CONTAINER_NAME} with PYTHON_PROFILES_ACTIVE=${PYTHON_PROFILE}"
if [[ -n "$DOCUMENTS_HOME_DIR" ]]; then
  echo "Documents home mounted: ${HOST_DOCUMENTS_HOME_DIR} -> ${CONTAINER_DOCUMENTS_HOME_DIR}"
fi
echo "http://localhost:${APP_PORT}/hello"
echo "http://localhost:${APP_PORT}/docs"
