#!/usr/bin/env bash
# Run on your Mac. Uploads yachts/ to RunPod, first 5 images per yacht before the rest.
# Files already on the pod (same size) are skipped, so re-running only sends what is new.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/transfer_opts.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REMOTE_ROOT="${REMOTE_ROOT:-/workspace/ycfcrawl}"
PARALLEL_JOBS="${SYNC_PARALLEL:-12}"
CHUNK_FILES="${SYNC_CHUNK_FILES:-400}"
HERO_COUNT="${HERO_COUNT:-5}"

if [[ -z "${RUNPOD_TRANSFER_SSH:-}" || -z "${RUNPOD_TRANSFER_PORT:-}" ]]; then
  echo "Set RUNPOD_DIRECT_HOST and RUNPOD_DIRECT_PORT in .env" >&2
  exit 1
fi

init_transfer_ssh
work_dir="$(mktemp -d)"
fail_log="$work_dir/failed.txt"
trap 'rm -rf "$work_dir"; cleanup_transfer_ssh' EXIT

run_remote() {
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"
}

echo "==> Target: $SSH_TARGET:$RUNPOD_TRANSFER_PORT"
run_remote "mkdir -p '$REMOTE_ROOT/public/yachts' '$REMOTE_ROOT/scripts/runpod' && rm -f '$REMOTE_ROOT/.upload_complete'"

echo "==> Uploading code"
rsync "${RSYNC_UP_OPTS[@]}" -e "$RSYNC_SSH" \
  "$REPO_ROOT/remove_watermarks.py" "$SSH_TARGET:$REMOTE_ROOT/"
rsync "${RSYNC_UP_OPTS[@]}" -e "$RSYNC_SSH" \
  "$REPO_ROOT/scripts/runpod/" "$SSH_TARGET:$REMOTE_ROOT/scripts/runpod/"

local_log="$REPO_ROOT/public/yachts/.processing_log.txt"
if [[ -f "$local_log" ]]; then
  echo "==> Merging Mac progress log ($(wc -l <"$local_log" | tr -d ' ') images) so the pod skips them"
  rsync "${RSYNC_UP_OPTS[@]}" -e "$RSYNC_SSH" \
    "$local_log" "$SSH_TARGET:$REMOTE_ROOT/public/yachts/.mac_processing_log.txt"
  run_remote "cd '$REMOTE_ROOT/public/yachts' && touch .processing_log.txt && \
    sort -u .processing_log.txt .mac_processing_log.txt -o .processing_log.txt"
fi

echo "==> Building upload lists (heroes first)"
python3 - "$REPO_ROOT" "$HERO_COUNT" "$work_dir" <<'PY'
import sys
from pathlib import Path

repo, hero_count, out = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
heroes, rest = [], []
for yacht in sorted((repo / "yachts").iterdir()):
    if not yacht.is_dir() or yacht.name.startswith("."):
        continue
    for extra in sorted(yacht.iterdir()):
        if extra.is_file() and not extra.name.startswith("."):
            heroes.append(extra.relative_to(repo).as_posix())
    images = yacht / "images"
    if not images.is_dir():
        continue
    files = sorted(f for f in images.iterdir() if f.is_file() and f.suffix.lower() in exts)
    heroes += [f.relative_to(repo).as_posix() for f in files[:hero_count]]
    rest += [f.relative_to(repo).as_posix() for f in files[hero_count:]]
(out / "heroes.txt").write_text("\n".join(heroes) + "\n")
(out / "rest.txt").write_text("\n".join(rest) + "\n")
print(f"heroes+metadata: {len(heroes)} files, rest: {len(rest)} files")
PY

upload_list() {
  local label="$1" list="$2"
  local chunk_dir="$work_dir/chunks_$label"
  mkdir -p "$chunk_dir"
  split -l "$CHUNK_FILES" "$list" "$chunk_dir/chunk_"
  local total done=0
  total="$(find "$chunk_dir" -type f | wc -l | tr -d ' ')"
  echo "==> Uploading $label in $total chunks ($PARALLEL_JOBS parallel)"

  sync_chunk() {
    local chunk="$1" attempt
    for attempt in 1 2 3; do
      if rsync "${RSYNC_UP_OPTS[@]}" --files-from="$chunk" -e "$RSYNC_SSH_PARALLEL" \
        "$REPO_ROOT/" "$SSH_TARGET:$REMOTE_ROOT/" 2>/dev/null; then
        return 0
      fi
      sleep $((attempt * 3))
    done
    echo "$chunk" >>"$fail_log"
  }

  for chunk in "$chunk_dir"/chunk_*; do
    while (( $(jobs -rp | wc -l | tr -d ' ') >= PARALLEL_JOBS )); do
      sleep 0.2
    done
    sync_chunk "$chunk" &
    done=$((done + 1))
    if (( done % 10 == 0 )); then
      echo "  $label: queued $done/$total chunks ($(date '+%H:%M:%S'))"
    fi
  done
  wait
  echo "==> $label upload finished"
}

upload_list heroes "$work_dir/heroes.txt"
upload_list rest "$work_dir/rest.txt"

if [[ -s "$fail_log" ]]; then
  echo "==> $(wc -l <"$fail_log" | tr -d ' ') chunk(s) failed. Re-run this script to retry; finished files are skipped." >&2
  exit 1
fi

run_remote "touch '$REMOTE_ROOT/.upload_complete'"
echo "==> Upload complete"
