#!/usr/bin/env bash
# Run on your Mac. Uploads source images + checkpoint log to RunPod.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/transfer_opts.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REMOTE_ROOT="${REMOTE_ROOT:-/workspace/ycfcrawl}"

require_transfer_ssh() {
  if [[ -n "${RUNPOD_TRANSFER_SSH:-}" && -n "${RUNPOD_TRANSFER_PORT:-}" ]]; then
    return 0
  fi
  echo "ERROR: ssh.runpod.io cannot transfer files (shell only)." >&2
  echo "" >&2
  echo "In RunPod Dashboard → your Pod → Connect:" >&2
  echo "  1. Find 'Direct TCP Ports' (e.g. 69.x.x.x:25634 -> :22)" >&2
  echo "  2. Add to .env:" >&2
  echo "       RUNPOD_DIRECT_HOST=69.x.x.x" >&2
  echo "       RUNPOD_DIRECT_PORT=25634" >&2
  echo "  3. Re-run: source scripts/runpod/env.sh && ./scripts/runpod/sync_up.sh" >&2
  echo "" >&2
  echo "Faster: SYNC_PARALLEL=12 ./scripts/runpod/sync_up_parallel.sh" >&2
  exit 1
}

require_transfer_ssh
init_transfer_ssh
trap cleanup_transfer_ssh EXIT

run_remote() {
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"
}

echo "==> Transfer target: $SSH_TARGET (port $RUNPOD_TRANSFER_PORT)"
echo "==> Creating remote dirs"
run_remote "mkdir -p '$REMOTE_ROOT/public/yachts' '$REMOTE_ROOT/scripts/runpod'"

echo "==> Uploading yachts/ (~17 GB). Tip: use sync_up_parallel.sh for faster upload."
RSYNC_EXTRA=()
if [[ "${SYNC_VERBOSE:-}" == "1" ]]; then
  RSYNC_EXTRA+=(--info=progress2)
fi

if command -v rsync >/dev/null 2>&1; then
  rsync "${RSYNC_UP_OPTS[@]}" "${RSYNC_EXTRA[@]}" \
    -e "$RSYNC_SSH" \
    "$REPO_ROOT/yachts/" "$SSH_TARGET:$REMOTE_ROOT/yachts/"
else
  echo "rsync not found; streaming tar over direct SSH"
  COPYFILE_DISABLE=1 tar -C "$REPO_ROOT" --no-xattrs -cf - yachts \
    | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "mkdir -p '$REMOTE_ROOT' && tar -C '$REMOTE_ROOT' -xf -"
fi

if [[ -f "$REPO_ROOT/public/yachts/.processing_log.txt" ]]; then
  echo "==> Uploading checkpoint log ($(wc -l < "$REPO_ROOT/public/yachts/.processing_log.txt") lines)"
  scp "${SCP_OPTS[@]}" \
    "$REPO_ROOT/public/yachts/.processing_log.txt" \
    "$SSH_TARGET:$REMOTE_ROOT/public/yachts/.processing_log.txt"
fi

echo "==> Uploading runpod scripts"
rsync "${RSYNC_UP_OPTS[@]}" -e "$RSYNC_SSH" \
  "$REPO_ROOT/scripts/runpod/" "$SSH_TARGET:$REMOTE_ROOT/scripts/runpod/"

echo "Upload complete."
