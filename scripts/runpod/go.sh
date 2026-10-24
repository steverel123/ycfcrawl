#!/usr/bin/env bash
# Upload yachts/, bootstrap GPU, start batch. Run sync_watch.sh in another terminal.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
export SYNC_PARALLEL="${SYNC_PARALLEL:-12}"
echo "Upload + batch (keep Mac awake). In another terminal:"
echo "  cd $REPO_ROOT && ./scripts/runpod/sync_watch.sh"
exec caffeinate -dims "$SCRIPT_DIR/run_all_no_watch.sh"
