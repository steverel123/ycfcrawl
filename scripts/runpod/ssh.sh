#!/usr/bin/env bash
# Interactive shell on RunPod (use Terminal.app, not Cursor agent).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"
exec ssh -tt -i "$SSH_KEY" -p "$SSH_PORT" -o StrictHostKeyChecking=accept-new "$RUNPOD_SSH"
