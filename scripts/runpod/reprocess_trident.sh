#!/usr/bin/env bash
# Run on the pod after Mac-side R2 delete, or standalone from blocked/quarantine logs.
set -euo pipefail
ROOT="${YCFCrawl_ROOT:-/workspace/ycfcrawl}"
cd "$ROOT"
set -a
[[ -f .s3.env ]] && source .s3.env
set +a
exec python3 scripts/reprocess_trident.py "$@"
