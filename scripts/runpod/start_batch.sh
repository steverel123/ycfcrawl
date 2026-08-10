#!/usr/bin/env bash
# Run ON the RunPod GPU machine. Starts multi-GPU batch in tmux (resumable).
set -euo pipefail

ROOT="${YCFCrawl_ROOT:-/workspace/ycfcrawl}"
CONF="${WM_CONF:-0.08}"
DILATE="${WM_DILATE:-35}"
SESSION="${WM_TMUX_SESSION:-watermark}"

cd "$ROOT/jferments-watermark-remover"
# shellcheck disable=SC1091
source venv/bin/activate

if [[ ! -d "$ROOT/yachts" ]]; then
  echo "Missing $ROOT/yachts — upload yachts/ first." >&2
  exit 1
fi

tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" bash -lc "
  cd '$ROOT/jferments-watermark-remover' && source venv/bin/activate && \
  python3 -u watermark_remover.py \
    -i '$ROOT/yachts' \
    -o '$ROOT/public/yachts' \
    -w yolo11x-train28-best.pt \
    -R --conf $CONF --dilate $DILATE \
    2>&1 | tee '$ROOT/watermark_remove.log'
"

echo "Batch started in tmux session: $SESSION"
echo "Attach: tmux attach -t $SESSION"
echo "Log:    tail -f $ROOT/watermark_remove.log"
