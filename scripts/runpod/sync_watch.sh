#!/usr/bin/env bash
# Pull cleaned images from RunPod while the batch runs (safe to rerun).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export SYNC_INTERVAL="${SYNC_INTERVAL:-120}"
exec "$SCRIPT_DIR/sync_down.sh" --watch
