#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
compose down --remove-orphans
bash "$ROOT_DIR/scripts/08_stop_stress.sh"
bash "$ROOT_DIR/scripts/10_stop_text_load.sh"
echo "Stack stopped. Model cache, monitoring data, and MIG layout were preserved."
