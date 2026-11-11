#!/usr/bin/env bash
# Shared paths and literal .env reading; do not execute .env as shell code.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

env_value() {
  local key="$1" fallback="${2:-}" value
  if [[ -n "${!key:-}" ]]; then
    printf '%s\n' "${!key}"
    return
  fi
  value=""
  if [[ -f "$ENV_FILE" ]]; then
    value="$(awk -v key="$key" '
      index($0, key "=") == 1 {
        value = substr($0, length(key) + 2)
        sub(/\r$/, "", value)
      }
      END { print value }
    ' "$ENV_FILE")"
  fi
  if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s\n' "${value:-$fallback}"
}

PROJECT_NAME="$(env_value COMPOSE_PROJECT_NAME mig-lab)"
if [[ ! "$PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  echo "ERROR: invalid COMPOSE_PROJECT_NAME: $PROJECT_NAME" >&2
  exit 1
fi

require_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found. Run scripts/03_export_env.sh first." >&2
    exit 1
  fi
}

compose() {
  require_env
  OPENAI_API_KEY="$(env_value OPENAI_API_KEY dummy-local-key)" \
    docker compose --project-name "$PROJECT_NAME" \
      --project-directory "$ROOT_DIR/compose" --env-file "$ENV_FILE" \
      -f "$ROOT_DIR/compose/docker-compose.yml" "$@"
}

select_gpu() {
  if [[ -n "${GPU_ID:-}" && -n "${GPU_INDEX:-}" && "$GPU_ID" != "$GPU_INDEX" ]]; then
    echo "ERROR: GPU_ID and GPU_INDEX disagree. Set only GPU_ID." >&2
    exit 1
  fi
  GPU_ID="${GPU_ID:-${GPU_INDEX:-0}}"
  GPU_UUID="$(nvidia-smi -i "$GPU_ID" --query-gpu=uuid --format=csv,noheader | tr -d '[:space:]')"
  if [[ ! "$GPU_UUID" =~ ^GPU-[[:alnum:]-]+$ ]]; then
    echo "ERROR: GPU_ID must select exactly one physical GPU." >&2
    exit 1
  fi
}

gpu_mig_lines() {
  nvidia-smi -L | awk -v uuid="$GPU_UUID" '
    /^GPU / { selected = index($0, "UUID: " uuid ")") > 0 }
    selected && /^[[:space:]]+MIG / { print }
  '
}
