#!/usr/bin/env bash
# Run ON the pod (tmux session r2-copy). Copies /workspace to Cloudflare R2 with rclone,
# then repeats every R2_COPY_EVERY seconds so newly cleaned images follow. After the
# watermark pipeline finishes (.volume_done), one last pass runs and the loop exits.
# Needs an "r2" remote in ~/.config/rclone/rclone.conf. Already-copied files are skipped.
set -uo pipefail

SRC="${R2_COPY_SRC:-/workspace}"
DEST="${R2_COPY_DEST:-r2:ycf}"
EVERY="${R2_COPY_EVERY:-600}"
LOG="${R2_COPY_LOG:-/root/r2-copy.log}"
DONE_FLAG="${SRC}/ycfcrawl/.volume_done"

log() { echo "$(date -u +%FT%TZ) [r2] $*" | tee -a "$LOG"; }

pass() {
  log "copy pass starting: $SRC -> $DEST"
  # --size-only: the network volume does not keep reliable mtimes.
  # Live progress logs change mid-upload and fail the checksum, so they are skipped.
  rclone copy "$SRC" "$DEST" \
    --size-only --transfers 32 --checkers 64 --fast-list --retries 1 \
    --exclude '**/__pycache__/**' --exclude '**/.venv/**' --exclude '**/.s3.env*' \
    --exclude '**/.processing_*' --exclude '*.log' \
    --stats 60s --stats-one-line --log-level NOTICE --log-file "$LOG"
  local rc=$?
  log "copy pass finished (exit $rc)"
  return $rc
}

while true; do
  finished=0
  [[ -f "$DONE_FLAG" ]] && finished=1
  pass
  if (( finished )); then
    log "pipeline was done before this pass; all files copied, exiting"
    touch /root/.r2_copy_done
    exit 0
  fi
  sleep "$EVERY"
done
