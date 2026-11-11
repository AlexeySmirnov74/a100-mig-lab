#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
require_env

echo "Pulling published service images..."
compose pull --ignore-buildable
echo "Building the context guard and workload images..."
compose build --pull
# Build the CUDA/PyTorch stress image only when the stress demo is requested.
docker build -t mig-lab-smoke-client:local "$ROOT_DIR/workloads/smoke-client"
echo "Starting stack (model downloads and warmup can take several minutes)..."
compose up -d --wait --wait-timeout "${STARTUP_TIMEOUT_SEC:-1800}"
compose ps
echo "Use bash get_logs.sh to inspect logs."
