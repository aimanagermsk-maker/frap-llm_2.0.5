#!/usr/bin/env bash
# Локальная сборка и запуск (Linux / macOS / Git Bash на Windows).

set -euo pipefail

IMAGE_NAME=frap-llm-helper-img
CONTAINER_NAME=frap-llm-helper
APP_PORT=8000
PYTHON_PROFILE="${PYTHON_PROFILES_ACTIVE:-sandbox}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

read_kafka_setting() {
  local config_file="$1"
  local setting_name="$2"
  [[ -f "$config_file" ]] || return 0

  awk -v setting_name="$setting_name" '
    /^[^[:space:]#][^:]*:/ { in_kafka=0 }
    /^[[:space:]]*kafka:[[:space:]]*$/ { in_kafka=1; next }
    in_kafka && $0 ~ "^[[:space:]]*" setting_name ":[[:space:]]*" {
      sub("^[[:space:]]*" setting_name ":[[:space:]]*", "")
      gsub(/^[ "\047]+|[ "\047]+$/, "")
      print
      exit
    }
  ' "$config_file"
}

DOCUMENTS_HOME_DIR="$(
  read_kafka_setting "$SCRIPT_DIR/settings/user_settings.yaml" "documents_home_dir"
)"
OUTPUT_DIR="$(
  read_kafka_setting "$SCRIPT_DIR/settings/user_settings.yaml" "output_dir"
)"

resolve_mount_paths() {
  local configured_path="$1"
  local host_var_name="$2"
  local container_var_name="$3"

  if [[ "$configured_path" = /* ]]; then
    printf -v "$host_var_name" "%s" "$configured_path"
    printf -v "$container_var_name" "%s" "$configured_path"
  else
    configured_path="${configured_path#./}"
    printf -v "$host_var_name" "%s" "$SCRIPT_DIR/$configured_path"
    printf -v "$container_var_name" "%s" "/app/$configured_path"
  fi
}

DOCKER_VOLUME_ARGS=()
if [[ -n "$DOCUMENTS_HOME_DIR" ]]; then
  resolve_mount_paths "$DOCUMENTS_HOME_DIR" HOST_DOCUMENTS_HOME_DIR CONTAINER_DOCUMENTS_HOME_DIR
  mkdir -p "$HOST_DOCUMENTS_HOME_DIR"
  DOCKER_VOLUME_ARGS+=(-v "${HOST_DOCUMENTS_HOME_DIR}:${CONTAINER_DOCUMENTS_HOME_DIR}:ro")
fi
if [[ -n "$OUTPUT_DIR" ]]; then
  resolve_mount_paths "$OUTPUT_DIR" HOST_OUTPUT_DIR CONTAINER_OUTPUT_DIR
  mkdir -p "$HOST_OUTPUT_DIR"
  DOCKER_VOLUME_ARGS+=(-v "${HOST_OUTPUT_DIR}:${CONTAINER_OUTPUT_DIR}:rw")
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
if [[ -n "$OUTPUT_DIR" ]]; then
  echo "Output mounted: ${HOST_OUTPUT_DIR} -> ${CONTAINER_OUTPUT_DIR}"
fi
echo "http://localhost:${APP_PORT}/hello"
echo "http://localhost:${APP_PORT}/docs"
