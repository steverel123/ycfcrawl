#!/usr/bin/env bash
# Run ON the pod (inside tmux). Processes images as they arrive: hero images
# (first 5 per yacht) every cycle before the rest, until the upload is complete
# and nothing is pending. WORKERS parallel processes share the GPU; each owns a
# fixed subset of yacht folders (--shard i/n), so they never touch the same file.
set -uo pipefail

ROOT="${YCFCrawl_ROOT:-/workspace/ycfcrawl}"
PY="${WM_PYTHON:-/root/wmvenv/bin/python}"
WORKERS="${WORKERS:-4}"
cd "$ROOT"

run_phase() {
  "$PY" -u remove_watermarks.py -i yachts -o public/yachts --phase "$1" --shard "$2/$WORKERS"
}

worker() {
  local i="$1"
  while true; do
    run_phase heroes "$i"
    run_phase rest "$i"
    if [[ -f .upload_complete ]]; then
      run_phase heroes "$i"
      run_phase rest "$i"
      break
    fi
    sleep 30
  done
  echo "[w$i] finished"
}

rm -f public/yachts/.batch_done
for ((i = 0; i < WORKERS; i++)); do
  worker "$i" &
done
wait

touch public/yachts/.batch_done
echo "All uploaded images processed: $(wc -l <public/yachts/.processing_log.txt) logged"
