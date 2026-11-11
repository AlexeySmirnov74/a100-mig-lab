#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
docker rm -f "${PROJECT_NAME}-stress-worker" >/dev/null 2>&1 || true
echo "${PROJECT_NAME}-stress-worker stopped."
