#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
require_env
docker run --rm --network "${PROJECT_NAME}_default" \
  -e BASE_URL=http://context-guard:4010/v1 \
  -e OPENAI_API_KEY="$(env_value OPENAI_API_KEY dummy-local-key)" \
  mig-lab-smoke-client:local
