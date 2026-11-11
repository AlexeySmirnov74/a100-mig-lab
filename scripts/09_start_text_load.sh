#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
require_env

LOAD_USERS="$(env_value LOAD_USERS 10)"
LOAD_DURATION_SEC="$(env_value LOAD_DURATION_SEC 120)"
LOAD_THINK_TIME_SEC="$(env_value LOAD_THINK_TIME_SEC 0.5)"
LOAD_MODEL_MODE="$(env_value LOAD_MODEL_MODE all)"
LOAD_MAX_TOKENS="$(env_value LOAD_MAX_TOKENS 64)"
LOAD_TIMEOUT_SEC="$(env_value LOAD_TIMEOUT_SEC 120)"

docker build -t mig-lab-text-load:local "$ROOT_DIR/workloads/text-load"
docker rm -f "${PROJECT_NAME}-text-load" >/dev/null 2>&1 || true
echo "Starting text load: users=$LOAD_USERS duration=${LOAD_DURATION_SEC}s mode=$LOAD_MODEL_MODE"
docker run -d --name "${PROJECT_NAME}-text-load" --restart=no \
  --network "${PROJECT_NAME}_default" \
  -e LOAD_API_BASE=http://context-guard:4010/v1 \
  -e LOAD_API_KEY="$(env_value OPENAI_API_KEY dummy-local-key)" \
  -e LOAD_USERS="$LOAD_USERS" -e LOAD_DURATION_SEC="$LOAD_DURATION_SEC" \
  -e LOAD_THINK_TIME_SEC="$LOAD_THINK_TIME_SEC" -e LOAD_MODEL_MODE="$LOAD_MODEL_MODE" \
  -e LOAD_MAX_TOKENS="$LOAD_MAX_TOKENS" -e LOAD_TIMEOUT_SEC="$LOAD_TIMEOUT_SEC" \
  mig-lab-text-load:local
echo "Watch logs: docker logs -f ${PROJECT_NAME}-text-load"
echo "Stop: bash scripts/10_stop_text_load.sh"
