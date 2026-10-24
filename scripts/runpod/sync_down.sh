#!/usr/bin/env bash
# Run on your Mac. Downloads cleaned images from RunPod into public/yachts/.
# Never overwrites files that already exist locally, and merges the pod's
# progress log into the local one so a later Mac run skips pod-cleaned images.
#   ./sync_down.sh           one pass
#   ./sync_down.sh --watch   pull every SYNC_INTERVAL seconds until the pod batch
#                            is done, then one final pass
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/transfer_opts.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REMOTE_ROOT="${REMOTE_ROOT:-/workspace/ycfcrawl}"
INTERVAL="${SYNC_INTERVAL:-120}"
PARALLEL="${SYNC_DOWN_PARALLEL:-8}"
WATCH=0
[[ "${1:-}" == "--watch" ]] && WATCH=1

if [[ -z "${RUNPOD_TRANSFER_SSH:-}" || -z "${RUNPOD_TRANSFER_PORT:-}" ]]; then
  echo "Set RUNPOD_DIRECT_HOST and RUNPOD_DIRECT_PORT in .env" >&2
  exit 1
fi

init_transfer_ssh
trap cleanup_transfer_ssh EXIT

LOCAL_OUT="$REPO_ROOT/public/yachts"
mkdir -p "$LOCAL_OUT"

OFFSET_FILE="$LOCAL_OUT/.pod_log_offset"

fetch_pod_log() {
  rsync "${RSYNC_OPTS[@]}" -e "$RSYNC_SSH" \
    "$SSH_TARGET:$REMOTE_ROOT/public/yachts/.processing_log.txt" "$LOCAL_OUT/.pod_processing_log.txt"
}

merge_and_organize() {
  touch "$LOCAL_OUT/.processing_log.txt"
  sort -u "$LOCAL_OUT/.processing_log.txt" "$LOCAL_OUT/.pod_processing_log.txt" \
    -o "$LOCAL_OUT/.processing_log.txt"
  python3 "$REPO_ROOT/scripts/organize_clean.py" || echo "organize_clean failed (will retry)" >&2
}

# Fetches only images appended to the pod's log since the last pass (no remote tree scan).
pull_new() {
  if [[ ! -f "$OFFSET_FILE" ]]; then
    # Seed from the log copied by the last full pass, minus a margin for images
    # logged while that pass was running.
    local seen=0
    [[ -f "$LOCAL_OUT/.pod_processing_log.txt" ]] && seen=$(wc -l <"$LOCAL_OUT/.pod_processing_log.txt" | tr -d ' ')
    echo $(( seen > 6000 ? seen - 6000 : 0 )) >"$OFFSET_FILE"
  fi
  fetch_pod_log
  local offset total list
  offset=$(cat "$OFFSET_FILE")
  total=$(wc -l <"$LOCAL_OUT/.pod_processing_log.txt" | tr -d ' ')
  if (( total > offset )); then
    local dir part failed=0
    dir="$(mktemp -d)"
    tail -n +"$((offset + 1))" "$LOCAL_OUT/.pod_processing_log.txt" | head -n "$((total - offset))" \
      | awk 'NR == FNR { have[$0] = 1; next } !($0 in have)' \
          <(cd "$LOCAL_OUT" && find . -path './*/images/*' -type f | sed 's#^\./##') - \
      | awk -v n="$PARALLEL" -v d="$dir" '{ print > (d "/part_" (NR % n)) }'
    for part in "$dir"/part_*; do
      [[ -s "$part" ]] || continue
      rsync "${RSYNC_OPTS[@]}" --ignore-existing --files-from="$part" -e "$RSYNC_SSH_PARALLEL" \
        "$SSH_TARGET:$REMOTE_ROOT/public/yachts/" "$LOCAL_OUT/" &
    done
    for part in $(jobs -p); do wait "$part" || failed=1; done
    rm -rf "$dir"
    (( failed )) && return 1
    echo "$total" >"$OFFSET_FILE"
  fi
  merge_and_organize
}

# Full tree comparison; slow on the network volume, used once at the end as a safety net.
pull_full() {
  rsync "${RSYNC_OPTS[@]}" --ignore-existing \
    --exclude '.*' --exclude '*.log' \
    -e "$RSYNC_SSH" \
    "$SSH_TARGET:$REMOTE_ROOT/public/yachts/" "$LOCAL_OUT/"
  fetch_pod_log
  merge_and_organize
}

progress() {
  echo "$(date '+%H:%M:%S') pulled; cleaned images logged locally: $(wc -l <"$LOCAL_OUT/.processing_log.txt" | tr -d ' ')"
}

batch_done() {
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "test -f '$REMOTE_ROOT/public/yachts/.batch_done'" 2>/dev/null
}

echo "==> Downloading cleaned images from $SSH_TARGET"
if (( WATCH )); then
  until batch_done; do
    pull_new || echo "pull failed (will retry)" >&2
    progress
    sleep "$INTERVAL"
  done
  echo "==> Pod batch finished; final full pass"
  pull_new
  pull_full
else
  pull_new
fi
progress
echo "Download complete: $LOCAL_OUT/"
