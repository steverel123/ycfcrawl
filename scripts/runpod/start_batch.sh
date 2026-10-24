#!/usr/bin/env bash
# Run ON the RunPod GPU machine. Starts the resumable batch in tmux.
set -euo pipefail

ROOT="${YCFCrawl_ROOT:-/workspace/ycfcrawl}"
CONF="${WM_CONF:-0.08}"
DILATE="${WM_DILATE:-35}"
SESSION="${WM_TMUX_SESSION:-watermark}"
VENV="${WM_VENV:-/root/wm-venv}"

if [[ ! -d "$ROOT/yachts" ]]; then
  echo "Missing $ROOT/yachts — upload yachts/ first." >&2
  exit 1
fi

if pgrep -f '^bash scripts/runpod/(remote_loop|pod_supervisor)\.sh' >/dev/null; then
  echo "remote_loop.sh / pod_supervisor.sh is running; refusing to start the legacy batch." >&2
  exit 1
fi

tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" bash -lc "
  export WM_TMPDIR=/tmp PATH='$VENV/bin':\"\$PATH\"
  cd '$ROOT' && \
  '$VENV/bin/python' -u scripts/local_watermark/run.py \
    -i '$ROOT/yachts' \
    -o '$ROOT/public/yachts' \
    batch --conf $CONF --dilate $DILATE \
    2>&1 | tee -a '$ROOT/watermark_remove.log'
"

echo "Batch started in tmux session: $SESSION"
echo "Attach:   tmux attach -t $SESSION"
echo "Log:      tail -f $ROOT/watermark_remove.log"
echo "Progress: wc -l $ROOT/public/yachts/.batch_status.tsv"
