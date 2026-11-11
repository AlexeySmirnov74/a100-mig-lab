#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo env GPU_ID=0 bash scripts/01_enable_mig.sh" >&2
  exit 1
fi
select_gpu
CURRENT="$(nvidia-smi -i "$GPU_ID" --query-gpu=mig.mode.current --format=csv,noheader | tr -d '[:space:]')"
if [[ "$CURRENT" == "Enabled" ]]; then
  echo "MIG already enabled on GPU $GPU_ID ($GPU_UUID)."
  exit 0
fi

echo "Enabling MIG on GPU $GPU_ID ($GPU_UUID). Stop its GPU clients first."
nvidia-smi -i "$GPU_ID" -mig 1
AFTER="$(nvidia-smi -i "$GPU_ID" --query-gpu=mig.mode.current --format=csv,noheader | tr -d '[:space:]')"
if [[ "$AFTER" != "Enabled" ]]; then
  echo "MIG is pending. A host reboot may be required; rerun this script afterward." >&2
  exit 2
fi
echo "MIG is enabled on GPU $GPU_ID."
