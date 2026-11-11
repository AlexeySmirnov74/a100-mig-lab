#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/../scripts/common.sh"
BASE_URL=${BASE_URL:-http://127.0.0.1:$(env_value CONTEXT_GUARD_PORT 4010)/v1}
KEY=$(env_value OPENAI_API_KEY dummy-local-key)

for model in general-chat fast-chat tiny-chat; do
  echo "Testing $model"
  curl -fsS "$BASE_URL/chat/completions" \
    -H "Authorization: Bearer $KEY" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in one short sentence.\"}],\"max_tokens\":64}" | jq -er '.choices[0].message.content | select(type == "string" and length > 0)'
done
