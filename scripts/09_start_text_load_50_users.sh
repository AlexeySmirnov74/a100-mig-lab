#!/usr/bin/env bash
set -euo pipefail
export LOAD_USERS="${LOAD_USERS:-50}"
export LOAD_DURATION_SEC="${LOAD_DURATION_SEC:-180}"
export LOAD_THINK_TIME_SEC="${LOAD_THINK_TIME_SEC:-0.2}"
export LOAD_MODEL_MODE="${LOAD_MODEL_MODE:-all}"
exec bash "$(dirname "${BASH_SOURCE[0]}")/09_start_text_load.sh"
