#!/usr/bin/env bash
# Run ON the pod (tmux session wm-supervisor). Self-heals the pipeline without the Mac:
#   - (re)builds the worker venv if the container disk was reset
#   - keeps remote_loop.sh workers running; restarts them if their log goes quiet
#   - pushes newly cleaned images to each target (RunPod volume, Cloudflare R2 when
#     R2_* is set in .s3.env) every PUSH_EVERY seconds, one process per target
# Exits after the workers finish (.batch_done) and a final full push to every target succeeds.
set -uo pipefail

ROOT="${YCFCrawl_ROOT:-/workspace/ycfcrawl}"
VENV="${WM_VENV:-/root/wmvenv}"
CHECK_EVERY="${CHECK_EVERY:-60}"
PUSH_EVERY="${PUSH_EVERY:-300}"
STALL_MINUTES="${STALL_MINUTES:-20}"
S3_ENV="$ROOT/.s3.env"
cd "$ROOT"

log() { echo "$(date -u +%FT%TZ) [supervisor] $*"; }

# Anchored patterns so tmux server/client argv (which embed these names) never match.
workers_running() { pgrep -f '^bash scripts/runpod/remote_loop\.sh' >/dev/null; }
push_running() { pgrep -f "^$VENV/bin/python -u scripts/runpod/push_clean\.py --target $1" >/dev/null; }

targets() {
  echo volume
  grep -q '^R2_BUCKET=.' "$S3_ENV" 2>/dev/null && echo r2
}

ensure_venv() {
  if [[ ! -x "$VENV/bin/python" ]]; then
    log "venv missing, bootstrapping"
    WM_VENV="$VENV" bash scripts/runpod/bootstrap.sh || return 1
  fi
  "$VENV/bin/python" -c "import boto3" 2>/dev/null || "$VENV/bin/pip" install -q boto3
}

start_workers() {
  log "starting workers ($1)"
  tmux kill-session -t wm-shards 2>/dev/null
  tmux kill-session -t watermark 2>/dev/null
  pkill -f '^bash scripts/runpod/remote_loop\.sh'
  pkill -f "^$VENV/bin/python -u remove_watermarks\.py"
  sleep 3
  tmux new-session -d -s wm-shards \
    "WM_PYTHON='$VENV/bin/python' bash scripts/runpod/remote_loop.sh 2>&1 | tee -a remote_loop.log"
}

# push <target> [--full]; output goes to push_<target>.log
push() {
  [[ -f "$S3_ENV" ]] || { log "no $S3_ENV, skipping push"; return 1; }
  (
    set -a; source "$S3_ENV"; set +a
    "$VENV/bin/python" -u scripts/runpod/push_clean.py --target "$@"
  ) >>"push_$1.log" 2>&1
}

ensure_venv || log "bootstrap failed, will retry"
last_push=0
while true; do
  if [[ -f public/yachts/.batch_done ]]; then
    all_ok=1
    for t in $(targets); do
      [[ -f ".final_push_$t" ]] && continue
      while push_running "$t"; do sleep 10; done
      log "workers finished; final full push to $t"
      if push "$t" --full; then touch ".final_push_$t"; else all_ok=0; fi
    done
    if (( all_ok )); then
      touch .volume_done
      log "all done"
      exit 0
    fi
    log "final push failed, retrying"
  else
    ensure_venv || { log "bootstrap failed, retrying"; sleep "$CHECK_EVERY"; continue; }
    if ! workers_running; then
      start_workers "not running"
    elif [[ -n "$(find remote_loop.log -mmin +"$STALL_MINUTES" 2>/dev/null)" ]]; then
      start_workers "no output for ${STALL_MINUTES}m"
    fi
    now=$(date +%s)
    if (( now - last_push >= PUSH_EVERY )); then
      last_push=$now
      for t in $(targets); do
        push_running "$t" || push "$t" &
      done
    fi
  fi
  sleep "$CHECK_EVERY"
done
