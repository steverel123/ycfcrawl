#!/usr/bin/env bash
# Run on your Mac: upload data, bootstrap GPU env, start full batch on RunPod.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSH_KEY="${SSH_KEY/#\~/$HOME}"
REMOTE_ROOT="${REMOTE_ROOT:-/workspace/ycfcrawl}"

if [[ "${SYNC_SEQUENTIAL:-}" == "1" ]]; then
  "$REPO_ROOT/scripts/runpod/sync_up.sh"
else
  SYNC_PARALLEL="${SYNC_PARALLEL:-12}" "$REPO_ROOT/scripts/runpod/sync_up_parallel.sh"
fi

if [[ -z "${RUNPOD_TRANSFER_SSH:-}" || -z "${RUNPOD_TRANSFER_PORT:-}" ]]; then
  echo "RUNPOD_DIRECT_HOST/PORT required for bootstrap." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$SCRIPT_DIR/transfer_opts.sh"
init_transfer_ssh
TRANSFER_OPTS=("${SSH_OPTS[@]}")
TRANSFER_TARGET="$SSH_TARGET"

echo "==> Bootstrap + start jferments batch on RunPod"
ssh "${TRANSFER_OPTS[@]}" "$TRANSFER_TARGET" bash -s <<REMOTE
set -euo pipefail
export YCFCrawl_ROOT='$REMOTE_ROOT'
export WM_CONF='${WM_CONF:-0.08}'
export WM_DILATE='${WM_DILATE:-35}'
chmod +x '$REMOTE_ROOT/scripts/runpod/'*.sh
'$REMOTE_ROOT/scripts/runpod/bootstrap.sh'
'$REMOTE_ROOT/scripts/runpod/start_batch.sh'
REMOTE

echo ""
echo "Batch is running on RunPod. Monitor:"
echo "  ssh -i $SSH_KEY -p $RUNPOD_TRANSFER_PORT $TRANSFER_TARGET -t tmux attach -t watermark"
echo "  ssh -i $SSH_KEY -p $RUNPOD_TRANSFER_PORT $TRANSFER_TARGET tail -f $REMOTE_ROOT/watermark_remove.log"
echo ""
echo "When done, download results:"
echo "  source scripts/runpod/env.sh && ./scripts/runpod/sync_down.sh"
