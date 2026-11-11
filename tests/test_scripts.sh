#!/usr/bin/env bash
# GPU-free regression tests: all NVIDIA/Docker commands are replaced by fixtures.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
mkdir -p "$SANDBOX/project/scripts" "$SANDBOX/project/compose" "$SANDBOX/bin" "$SANDBOX/elsewhere"
cp "$ROOT_DIR"/scripts/*.sh "$SANDBOX/project/scripts/"
cp "$ROOT_DIR"/{up.sh,down.sh,get_logs.sh,.env.example} "$SANDBOX/project/"
cp "$ROOT_DIR/.env.example" "$SANDBOX/project/.env"
export COMMAND_LOG="$SANDBOX/commands.log"
export PATH="$SANDBOX/bin:$PATH"
export GPU_ID=2
unset GPU_INDEX || true

cat > "$SANDBOX/bin/nvidia-smi" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'nvidia-smi %s\n' "$*" >> "$COMMAND_LOG"
case " $* " in
  *" --query-gpu=uuid "*) echo GPU-selected ;;
  *" --query-gpu=mig.mode.current "*) echo Enabled ;;
  *" -L "*)
    cat <<'DEVICES'
GPU 0: NVIDIA A100 (UUID: GPU-other)
  MIG 3g.20gb Device 0: (UUID: MIG-other-general)
  MIG 2g.10gb Device 1: (UUID: MIG-other-fast)
  MIG 1g.5gb Device 2: (UUID: MIG-other-tiny)
  MIG 1g.5gb Device 3: (UUID: MIG-other-stress)
GPU 2: NVIDIA A100 (UUID: GPU-selected)
  MIG 1g.5gb Device 0: (UUID: MIG-selected-tiny)
  MIG 3g.20gb Device 1: (UUID: MIG-selected-general)
  MIG 1g.5gb Device 2: (UUID: MIG-selected-stress)
  MIG 2g.10gb Device 3: (UUID: MIG-selected-fast)
GPU 3: NVIDIA A100 (UUID: GPU-after)
  MIG 3g.20gb Device 0: (UUID: MIG-after-general)
DEVICES
    ;;
  *" -lgip "*)
    if [[ "${MISSING_PROFILES:-0}" == 1 ]]; then
      echo '| 2 MIG 3g.40gb 99 2/2 |'
    else
      printf '%s\n' '| 2 MIG 1g.5gb+me 88 1/1 |' '| 2 MIG 3g.20gb 31 2/2 |' '| 2 MIG 2g.10gb 42 3/3 |' '| 2 MIG 1g.5gb 53 7/7 |'
    fi
    ;;
  *" -lgi "*)
    if [[ "${HAS_INSTANCES:-0}" == 1 ]]; then
      echo '| 2 MIG 3g.20gb 31 1 0:4 |'
    else
      echo 'No GPU instances found.'
    fi
    ;;
esac
MOCK
cat > "$SANDBOX/bin/docker" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'docker' >> "$COMMAND_LOG"
printf ' <%s>' "$@" >> "$COMMAND_LOG"
printf '\n' >> "$COMMAND_LOG"
if [[ "${SMOKE_FAIL:-0}" == 1 && "$*" == *"mig-lab-smoke-client:local"* ]]; then
  exit 9
fi
MOCK
chmod +x "$SANDBOX/bin/nvidia-smi" "$SANDBOX/bin/docker"
cd "$SANDBOX/elsewhere"
LAB="$SANDBOX/project"

pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

bash "$LAB/scripts/03_export_env.sh" >/dev/null
for pair in GENERAL:general FAST:fast TINY:tiny STRESS:stress; do
  grep -qx "MIG_${pair%:*}_UUID=MIG-selected-${pair#*:}" "$LAB/.env" || fail 'UUID export must select only GPU 2 and match exact profiles'
done
case "$(uname -s)" in
  MINGW*|MSYS*) printf 'SKIP: POSIX .env mode check on Windows/NTFS\n' ;;
  *) [[ "$(stat -c %a "$LAB/.env")" == 600 ]] || fail '.env must be private' ;;
esac
pass 'multi-GPU UUID export, shuffled device order, and .env generation'

: > "$COMMAND_LOG"
MISSING_PROFILES=1 MIG_RESET=1 bash "$LAB/scripts/02_create_layout.sh" >/dev/null 2>&1 && fail 'missing profiles must fail'
! grep -Eq -- '-dci|-dgi|-cgi' "$COMMAND_LOG" || fail 'profile failure changed layout'
pass 'unsupported profiles fail before any mutation, without hardcoded IDs'

: > "$COMMAND_LOG"
HAS_INSTANCES=1 bash "$LAB/scripts/02_create_layout.sh" >/dev/null 2>&1 && fail 'existing layout must require reset'
! grep -Eq -- '-dci|-dgi|-cgi' "$COMMAND_LOG" || fail 'existing layout was changed without explicit reset'
pass 'existing instances require explicit MIG_RESET=1'

: > "$COMMAND_LOG"
HAS_INSTANCES=1 MIG_RESET=1 bash "$LAB/scripts/02_create_layout.sh" >/dev/null
mapfile -t mutations < <(grep -E -- '-dci|-dgi|-cgi' "$COMMAND_LOG")
[[ "${mutations[0]}" == 'nvidia-smi mig -i 2 -dci' ]] || fail 'wrong compute reset scope'
[[ "${mutations[1]}" == 'nvidia-smi mig -i 2 -dgi' ]] || fail 'wrong GPU instance reset scope'
[[ "${mutations[2]}" == 'nvidia-smi mig -i 2 -cgi 31,42,53,53 -C' ]] || fail 'wrong discovered IDs or placement order'
! grep -q docker "$COMMAND_LOG" || fail 'layout must not stop unrelated containers'
pass 'GPU-scoped reset and 3g-first profile creation using discovered IDs'

: > "$COMMAND_LOG"
GPU_INDEX=0 bash "$LAB/scripts/03_export_env.sh" >/dev/null 2>&1 && fail 'conflicting GPU selectors must fail'
[[ ! -s "$COMMAND_LOG" ]] || fail 'conflicting selectors reached NVIDIA commands'
pass 'conflicting GPU_ID/GPU_INDEX rejected'

: > "$COMMAND_LOG"
bash "$LAB/scripts/04_validate_host.sh" >/dev/null
[[ "$(grep -c '<run>' "$COMMAND_LOG")" == 4 ]] || fail 'all slices must be validated'
cp "$LAB/.env" "$SANDBOX/valid.env"
sed -i 's/MIG_STRESS_UUID=.*/MIG_STRESS_UUID=MIG-selected-tiny/' "$LAB/.env"
: > "$COMMAND_LOG"
bash "$LAB/scripts/04_validate_host.sh" >/dev/null 2>&1 && fail 'duplicate UUID validation must fail'
! grep -q docker "$COMMAND_LOG" || fail 'duplicate UUIDs reached Docker'
bash "$LAB/scripts/07_start_stress.sh" >/dev/null 2>&1 && fail 'stress must reject inference UUID'
cp "$SANDBOX/valid.env" "$LAB/.env"
pass 'validation and stress reject shared inference/stress devices'

# .env contents are literal configuration, never shell instructions.
printf '\nOPENAI_API_KEY=$(touch %s)\n' "$SANDBOX/executed" >> "$LAB/.env"
: > "$COMMAND_LOG"
bash "$LAB/get_logs.sh" >/dev/null
[[ ! -e "$SANDBOX/executed" ]] || fail '.env executed shell code'
grep -q '<--project-name> <mig-lab>' "$COMMAND_LOG" || fail 'inconsistent compose project'
grep -q '<--env-file>' "$COMMAND_LOG" || fail 'missing explicit env file'
pass 'wrappers work from another directory and .env is not executed'

: > "$COMMAND_LOG"
bash "$LAB/scripts/09_start_text_load_50_users.sh" >/dev/null
grep -q '<LOAD_USERS=50>' "$COMMAND_LOG" || fail '50-user wrapper lost defaults'
grep -q '<--network> <mig-lab_default>' "$COMMAND_LOG" || fail 'load network does not match compose project'
pass '50-user wrapper works from another directory with the compose network'

: > "$COMMAND_LOG"
SMOKE_FAIL=1 bash "$LAB/scripts/06_demo_queries.sh" >/dev/null 2>&1 && fail 'smoke failure must propagate'
grep -q '<BASE_URL=http://context-guard:4010/v1>' "$COMMAND_LOG" || fail 'smoke bypassed context guard'
pass 'smoke goes through context guard and propagates failure'

: > "$COMMAND_LOG"
bash "$LAB/down.sh" >/dev/null
! grep -q '<-v>' "$COMMAND_LOG" || fail 'cleanup removed persistent volumes'
grep -q '<mig-lab-stress-worker>' "$COMMAND_LOG" || fail 'cleanup did not stop stress worker'
grep -q '<mig-lab-text-load>' "$COMMAND_LOG" || fail 'cleanup did not stop text load'
pass 'cleanup preserves data and stops both project workload containers'

for script in "$ROOT_DIR"/scripts/*.sh "$ROOT_DIR"/*.sh "$ROOT_DIR"/tests/*.sh; do
  bash -n "$script"
done
pass 'all shell scripts parse successfully'
