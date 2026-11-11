#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
require_env
STRESS_UUID="$(env_value MIG_STRESS_UUID)"
if [[ -z "$STRESS_UUID" ]]; then
  echo "ERROR: MIG_STRESS_UUID is empty. Run scripts/03_export_env.sh first." >&2
  exit 1
fi
for key in MIG_GENERAL_UUID MIG_FAST_UUID MIG_TINY_UUID; do
  if [[ "$(env_value "$key")" == "$STRESS_UUID" ]]; then
    echo "ERROR: the stress slice must be different from every inference slice." >&2
    exit 1
  fi
done

docker build -t mig-lab-stress-worker:local "$ROOT_DIR/workloads/stress-worker"
docker rm -f "${PROJECT_NAME}-stress-worker" >/dev/null 2>&1 || true
echo "Starting OOM demo only on MIG_STRESS_UUID=$STRESS_UUID."
docker run -d --name "${PROJECT_NAME}-stress-worker" --restart=no --runtime=nvidia \
  -e NVIDIA_VISIBLE_DEVICES="$STRESS_UUID" -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  -e STRESS_DTYPE=float16 -e STRESS_STEP_MIB=128 -e STRESS_SLEEP_SEC=2 \
  -e STRESS_HOLD_AFTER_OOM_SEC=15 mig-lab-stress-worker:local
echo "Watch logs: docker logs -f ${PROJECT_NAME}-stress-worker"
