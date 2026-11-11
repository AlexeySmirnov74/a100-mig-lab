#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
select_gpu

MIG_LINES="$(gpu_mig_lines)"
if [[ "$(printf '%s\n' "$MIG_LINES" | awk 'NF { count++ } END { print count+0 }')" != "4" ]]; then
  echo "ERROR: expected exactly four MIG devices on GPU $GPU_ID ($GPU_UUID)." >&2
  printf '%s\n' "$MIG_LINES" >&2
  exit 1
fi
pick_uuid() {
  awk -v profile="$1" -v occurrence="${2:-1}" '
    $1 == "MIG" && $2 == profile {
      count++
      if (count == occurrence) { sub(/^.*UUID: /, ""); sub(/\).*$/, ""); print }
    }
  ' <<< "$MIG_LINES"
}
GENERAL_UUID="$(pick_uuid 3g.20gb)"
FAST_UUID="$(pick_uuid 2g.10gb)"
TINY_UUID="$(pick_uuid 1g.5gb)"
STRESS_UUID="$(pick_uuid 1g.5gb 2)"
for var in GENERAL_UUID FAST_UUID TINY_UUID STRESS_UUID; do
  if [[ ! "${!var}" =~ ^MIG-[[:alnum:]/-]+$ ]]; then
    echo "ERROR: failed to detect $var on GPU $GPU_ID. Existing .env was preserved." >&2
    exit 1
  fi
done

umask 077
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ROOT_DIR/.env.example" "$ENV_FILE"
fi
TEMP_ENV="$(mktemp "$ROOT_DIR/.env.XXXXXX")"
trap 'rm -f "$TEMP_ENV"' EXIT
awk -v general="$GENERAL_UUID" -v fast="$FAST_UUID" -v tiny="$TINY_UUID" -v stress="$STRESS_UUID" '
  BEGIN { values["MIG_GENERAL_UUID"]=general; values["MIG_FAST_UUID"]=fast;
          values["MIG_TINY_UUID"]=tiny; values["MIG_STRESS_UUID"]=stress }
  { key=$0; sub(/=.*/, "", key)
    if (key in values) { if (!(key in seen)) print key "=" values[key]; seen[key]=1 }
    else print }
  END { for (key in values) if (!(key in seen)) print key "=" values[key] }
' "$ENV_FILE" > "$TEMP_ENV"
# Keep the existing file owner; repair ownership when invoked through sudo.
cat "$TEMP_ENV" > "$ENV_FILE"
chmod 600 "$ENV_FILE"
if [[ ${EUID} -eq 0 && -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
  chown "$SUDO_UID:$SUDO_GID" "$ENV_FILE"
fi
echo "Updated $ENV_FILE using only GPU $GPU_ID ($GPU_UUID):"
grep '^MIG_' "$ENV_FILE"
