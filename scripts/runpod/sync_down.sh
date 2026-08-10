#!/usr/bin/env bash
# Run on your Mac. Downloads cleaned images from RunPod.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/transfer_opts.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REMOTE_ROOT="${REMOTE_ROOT:-/workspace/ycfcrawl}"

if [[ -z "${RUNPOD_TRANSFER_SSH:-}" || -z "${RUNPOD_TRANSFER_PORT:-}" ]]; then
  echo "Set RUNPOD_DIRECT_HOST and RUNPOD_DIRECT_PORT in .env (see sync_up.sh)." >&2
  exit 1
fi

init_transfer_ssh
trap cleanup_transfer_ssh EXIT

mkdir -p "$REPO_ROOT/public/yachts"

echo "==> Downloading public/yachts/ from $SSH_TARGET"
RSYNC_EXTRA=()
[[ "${SYNC_VERBOSE:-}" == "1" ]] && RSYNC_EXTRA+=(--info=progress2)
rsync "${RSYNC_OPTS[@]}" "${RSYNC_EXTRA[@]}" \
  -e "$RSYNC_SSH" \
  "$SSH_TARGET:$REMOTE_ROOT/public/yachts/" "$REPO_ROOT/public/yachts/"

echo "Download complete: $REPO_ROOT/public/yachts/"
