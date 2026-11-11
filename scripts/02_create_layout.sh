#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

select_gpu
MIG_MODE="$(nvidia-smi -i "$GPU_ID" --query-gpu=mig.mode.current --format=csv,noheader | tr -d '[:space:]')"
if [[ "$MIG_MODE" != "Enabled" ]]; then
  echo "ERROR: MIG is not enabled on GPU $GPU_ID. Run scripts/01_enable_mig.sh first." >&2
  exit 1
fi

# Discover profiles before touching the current layout. IDs vary by GPU.
PROFILE_TABLE="$(nvidia-smi mig -i "$GPU_ID" -lgip)"
profile_id() {
  awk -v target="$1" '
    { for (i = 1; i < NF; i++) if ($i == "MIG" && $(i+1) == target) { print $(i+2); exit } }
  ' <<< "$PROFILE_TABLE"
}
PROFILE_3G="$(profile_id 3g.20gb)"
PROFILE_2G="$(profile_id 2g.10gb)"
PROFILE_1G="$(profile_id 1g.5gb)"
if [[ ! "$PROFILE_3G" =~ ^[0-9]+$ || ! "$PROFILE_2G" =~ ^[0-9]+$ || ! "$PROFILE_1G" =~ ^[0-9]+$ ]]; then
  echo "ERROR: required A100 40 GB profiles are unavailable. Existing instances were preserved." >&2
  printf '%s\n' "$PROFILE_TABLE" >&2
  exit 1
fi

INSTANCE_TABLE="$(nvidia-smi mig -i "$GPU_ID" -lgi)"
if awk '{ for (i = 1; i < NF; i++) if ($i == "MIG") found = 1 } END { exit !found }' <<< "$INSTANCE_TABLE"; then
  if [[ "${MIG_RESET:-0}" != "1" ]]; then
    echo "ERROR: GPU $GPU_ID has existing GPU instances. Stop its clients and rerun with MIG_RESET=1 to replace them." >&2
    exit 1
  fi
  echo "Replacing existing instances on GPU $GPU_ID ($GPU_UUID)."
  nvidia-smi mig -i "$GPU_ID" -dci
  nvidia-smi mig -i "$GPU_ID" -dgi
fi

# NVIDIA placement constraints require the 3g profile before the 2g profile.
LAYOUT="${PROFILE_3G},${PROFILE_2G},${PROFILE_1G},${PROFILE_1G}"
echo "Creating 3g.20gb + 2g.10gb + 1g.5gb + 1g.5gb on GPU $GPU_ID ($GPU_UUID)."
nvidia-smi mig -i "$GPU_ID" -cgi "$LAYOUT" -C
nvidia-smi mig -i "$GPU_ID" -lgi
nvidia-smi mig -i "$GPU_ID" -lci
echo "Next: bash scripts/03_export_env.sh (regular user, with the same GPU_ID)."
