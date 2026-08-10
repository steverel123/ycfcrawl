#!/usr/bin/env bash
# Source this before runpod scripts:  source scripts/runpod/env.sh
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

# Interactive shell via RunPod proxy (no file transfers).
export RUNPOD_SSH="${RUNPOD_SSH:-0v82h5ftrvnel7-644111c3@ssh.runpod.io}"
export RUNPOD_SSH_PORT="${RUNPOD_SSH_PORT:-22}"

# File transfers require Direct TCP SSH from Pod → Connect → Direct TCP Ports.
# Example: RUNPOD_DIRECT_HOST=69.48.159.6  RUNPOD_DIRECT_PORT=25634
export RUNPOD_DIRECT_USER="${RUNPOD_DIRECT_USER:-root}"
export RUNPOD_DIRECT_HOST="${RUNPOD_DIRECT_HOST:-}"
export RUNPOD_DIRECT_PORT="${RUNPOD_DIRECT_PORT:-}"

export SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_KEY="${SSH_KEY/#\~/$HOME}"
export SSH_KEY
export REMOTE_ROOT="${REMOTE_ROOT:-/workspace/ycfcrawl}"

# Used by sync_* scripts: direct TCP when set, else same as shell host (non-proxy only).
if [[ -n "$RUNPOD_DIRECT_HOST" && -n "$RUNPOD_DIRECT_PORT" ]]; then
  export RUNPOD_TRANSFER_HOST="$RUNPOD_DIRECT_HOST"
  export RUNPOD_TRANSFER_PORT="$RUNPOD_DIRECT_PORT"
  export RUNPOD_TRANSFER_USER="$RUNPOD_DIRECT_USER"
  export RUNPOD_TRANSFER_SSH="${RUNPOD_DIRECT_USER}@${RUNPOD_DIRECT_HOST}"
else
  export RUNPOD_TRANSFER_HOST=""
  export RUNPOD_TRANSFER_PORT=""
  export RUNPOD_TRANSFER_USER=""
  export RUNPOD_TRANSFER_SSH=""
fi
