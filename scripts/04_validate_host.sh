#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
require_env
select_gpu
MIG_LINES="$(gpu_mig_lines)"
UUIDS=()
for key in MIG_GENERAL_UUID MIG_FAST_UUID MIG_TINY_UUID MIG_STRESS_UUID; do
  uuid="$(env_value "$key")"
  if [[ -z "$uuid" || "$MIG_LINES" != *"UUID: $uuid)"* ]]; then
    echo "ERROR: $key is missing or does not belong to GPU $GPU_ID. Re-export UUIDs." >&2
    exit 1
  fi
  for existing in "${UUIDS[@]}"; do
    if [[ "$existing" == "$uuid" ]]; then
      echo "ERROR: multiple services share $uuid. Re-export UUIDs." >&2
      exit 1
    fi
  done
  UUIDS+=("$uuid")
done
echo "Testing Docker access to all four MIG UUIDs on GPU $GPU_ID ($GPU_UUID)."
for uuid in "${UUIDS[@]}"; do
  docker run --rm --runtime=nvidia \
    -e NVIDIA_VISIBLE_DEVICES="$uuid" -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L
done
echo "Host validation finished successfully."
