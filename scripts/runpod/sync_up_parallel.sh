#!/usr/bin/env bash
# Run on your Mac. Fast parallel upload: batched rsync + SSH connection reuse.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/transfer_opts.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REMOTE_ROOT="${REMOTE_ROOT:-/workspace/ycfcrawl}"
PARALLEL_JOBS="${PARALLEL_JOBS:-${SYNC_PARALLEL:-12}}"
SYNC_BATCH_SIZE="${SYNC_BATCH_SIZE:-50}"

require_transfer_ssh() {
  if [[ -n "${RUNPOD_TRANSFER_SSH:-}" && -n "${RUNPOD_TRANSFER_PORT:-}" ]]; then
    return 0
  fi
  echo "ERROR: Set RUNPOD_DIRECT_HOST and RUNPOD_DIRECT_PORT in .env (see sync_up.sh)." >&2
  exit 1
}

require_transfer_ssh
init_transfer_ssh

run_remote() {
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"
}

trap cleanup_transfer_ssh EXIT

echo "==> Transfer target: $SSH_TARGET (port $RUNPOD_TRANSFER_PORT)"
echo "==> Parallel jobs: $PARALLEL_JOBS, batch size: $SYNC_BATCH_SIZE folders"
echo "==> Creating remote dirs"
run_remote "mkdir -p '$REMOTE_ROOT/public/yachts' '$REMOTE_ROOT/scripts/runpod'"

echo "==> Warming SSH connection (multiplex)"
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "true"

batch_dir="$(mktemp -d)"
fail_log="$(mktemp)"
trap 'rm -rf "$batch_dir" "$fail_log"; cleanup_transfer_ssh' EXIT

current_batch=""
count_in_batch=0
batch_total=0
yacht_total=0

start_batch() {
  current_batch="$batch_dir/batch_$(printf '%03d' "$batch_total")"
  : >"$current_batch"
  ((batch_total++)) || true
  count_in_batch=0
}

append_yacht_files() {
  local dir="$1"
  local rel prefix="${REPO_ROOT}/"
  while IFS= read -r -d '' file; do
    rel="${file#"$prefix"}"
    printf '%s\n' "$rel"
  done < <(find "$dir" -type f -print0) >>"$current_batch"
}

echo "==> Building batch file lists (all image paths)..."
while IFS= read -r dir; do
  ((yacht_total++)) || true
  if (( count_in_batch == 0 )); then
    start_batch
  fi
  append_yacht_files "$dir"
  ((count_in_batch++)) || true
  if (( count_in_batch >= SYNC_BATCH_SIZE )); then
    count_in_batch=0
  fi
done < <(find "$REPO_ROOT/yachts" -mindepth 1 -maxdepth 1 -type d | sort)

if (( yacht_total == 0 )); then
  echo "No yacht folders under $REPO_ROOT/yachts" >&2
  exit 1
fi

if [[ -n "$current_batch" && ! -s "$current_batch" ]]; then
  rm -f "$current_batch"
  ((batch_total--)) || true
fi

echo "==> Uploading $yacht_total yacht folders in $batch_total batches (~17 GB, no per-file progress)"
started=0

sync_batch() {
  local list_file="$1"
  local batch_id="$2"
  local lines attempt max_attempts=3
  lines="$(wc -l <"$list_file" | tr -d ' ')"
  for attempt in $(seq 1 "$max_attempts"); do
    if rsync "${RSYNC_UP_OPTS[@]}" --files-from="$list_file" -e "${RSYNC_SSH_PARALLEL:-$RSYNC_SSH}" \
      "$REPO_ROOT/" "$SSH_TARGET:$REMOTE_ROOT/"; then
      echo "ok $batch_id ($lines files)"
      return 0
    fi
    if (( attempt < max_attempts )); then
      sleep $((attempt * 3))
    fi
  done
  echo "$batch_id" >>"$fail_log"
  echo "FAIL $batch_id ($lines files)" >&2
}

for list_file in "$batch_dir"/batch_*; do
  [[ -f "$list_file" ]] || continue
  while (( $(jobs -rp | wc -l | tr -d ' ') >= PARALLEL_JOBS )); do
    sleep 0.1
  done
  sync_batch "$list_file" "$(basename "$list_file")" &
  ((started++)) || true
done

wait

if [[ -s "$fail_log" ]]; then
  echo "==> $(wc -l <"$fail_log" | tr -d ' ') batch(es) failed. Re-run to retry:" >&2
  cat "$fail_log" >&2
  exit 1
fi

echo "==> All $yacht_total yacht folders uploaded ($batch_total batches)"

echo "==> Uploading scripts"
run_remote "mkdir -p '$REMOTE_ROOT/scripts/local_watermark'"
rsync "${RSYNC_UP_OPTS[@]}" -e "$RSYNC_SSH" \
  "$REPO_ROOT/scripts/runpod/" "$SSH_TARGET:$REMOTE_ROOT/scripts/runpod/"
rsync "${RSYNC_UP_OPTS[@]}" -e "$RSYNC_SSH" --exclude __pycache__ \
  "$REPO_ROOT/scripts/local_watermark/" "$SSH_TARGET:$REMOTE_ROOT/scripts/local_watermark/"
rsync "${RSYNC_UP_OPTS[@]}" -e "$RSYNC_SSH" \
  "$REPO_ROOT/requirements-local-watermark.txt" "$SSH_TARGET:$REMOTE_ROOT/"

echo "Upload complete."
