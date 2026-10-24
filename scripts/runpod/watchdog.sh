#!/bin/bash
# Run on your Mac (launchd every 5 min, or --loop). Backup supervisor: the pod heals
# itself via pod_supervisor.sh; this only covers what the pod cannot do on its own.
#   - starts the pod if stopped, refreshes host/port in .env when they change
#   - keeps pod scripts + S3 credentials current, keeps pod_supervisor.sh running
#   - finishes the raw upload (push_priority.sh writes .upload_complete)
#   - keeps sync_down.sh --watch running for the local clean-yachts copy
# Every stage resumes from its own checkpoint, so restarting is always safe.
#
#   ./scripts/runpod/watchdog.sh          one check
#   ./scripts/runpod/watchdog.sh --loop   check every WATCHDOG_INTERVAL seconds, keeps Mac awake
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

INTERVAL="${WATCHDOG_INTERVAL:-300}"
POD_ID="${RUNPOD_POD_ID:-l39uyxeg1qriab}"
AWS_PROFILE_NAME="${RUNPOD_S3_PROFILE:-runpod}"
R2_ENV_FILE="${R2_ENV_FILE:-$REPO_ROOT/../ycharter/.env.local}"
# The pod pushes straight to the volume and R2; a local copy is optional (needs ~30 GB).
MAC_DOWNLOAD="${MAC_DOWNLOAD:-0}"
LOG="$REPO_ROOT/watchdog.log"
STATE="$REPO_ROOT/.watchdog"
mkdir -p "$STATE"

log() { echo "$(date '+%F %T') $*" >>"$LOG"; }
notify() {
  log "ALERT: $*"
  osascript -e "display notification \"$*\" with title \"ycfcrawl watchdog\"" >/dev/null 2>&1 || true
}
running() { pgrep -f "$1" >/dev/null 2>&1; }

load_env() {
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/env.sh"
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/transfer_opts.sh"
  init_transfer_ssh
}

runpod_api() { curl -fsS -m 30 -H "Authorization: Bearer ${RUNPOD_API_KEY:-}" "$@"; }

json_field() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

set_env_var() {
  if grep -q "^$1=" .env; then sed -i '' "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >>.env; fi
}

ensure_pod() {
  local info status ip port
  info="$(runpod_api "https://rest.runpod.io/v1/pods/$POD_ID")" || { notify "RunPod API unreachable"; return 1; }
  status="$(json_field 'd.get("desiredStatus","")' <<<"$info")"
  if [[ "$status" != "RUNNING" ]]; then
    notify "pod was $status, starting it"
    runpod_api -X POST "https://rest.runpod.io/v1/pods/$POD_ID/start" >/dev/null \
      || notify "could not start pod $POD_ID"
    return 1
  fi
  ip="$(json_field 'd.get("publicIp") or ""' <<<"$info")"
  port="$(json_field '(d.get("portMappings") or {}).get("22","")' <<<"$info")"
  [[ -z "$ip" || -z "$port" ]] && { log "pod running, SSH not ready yet"; return 1; }
  if [[ "$ip" != "${RUNPOD_DIRECT_HOST:-}" || "$port" != "${RUNPOD_DIRECT_PORT:-}" ]]; then
    log "pod address changed to $ip:$port, updating .env"
    set_env_var RUNPOD_DIRECT_HOST "$ip"
    set_env_var RUNPOD_DIRECT_PORT "$port"
    load_env
  fi
}

remote() { ssh "${SSH_OPTS[@]}" -o ConnectTimeout=20 -o BatchMode=yes "$SSH_TARGET" "$@"; }

push_scripts_and_creds() {
  rsync -rl --no-perms --no-owner --no-group --no-times --checksum -e "$RSYNC_SSH" \
    "$SCRIPT_DIR/" "$SSH_TARGET:$REMOTE_ROOT/scripts/runpod/" || return 1
  local ak sk
  ak="$(aws_cfg aws_access_key_id)"; sk="$(aws_cfg aws_secret_access_key)"
  [[ -z "$ak" || -z "$sk" ]] && { notify "no [$AWS_PROFILE_NAME] keys in ~/.aws/credentials"; return 1; }
  { printf 'RUNPOD_S3_ACCESS_KEY_ID=%s\nRUNPOD_S3_SECRET_ACCESS_KEY=%s\n' "$ak" "$sk"
    grep -E '^R2_(ACCOUNT_ID|BUCKET|ACCESS_KEY_ID|SECRET_ACCESS_KEY)=' "$R2_ENV_FILE" 2>/dev/null
  } | remote "cat > '$REMOTE_ROOT/.s3.env.tmp' && mv '$REMOTE_ROOT/.s3.env.tmp' '$REMOTE_ROOT/.s3.env'"
}

aws_cfg() {
  awk -v p="[$AWS_PROFILE_NAME]" -v k="$1" '
    $0 == p { on = 1; next } /^\[/ { on = 0 }
    on && $1 == k { sub(/^[^=]*= */, ""); print; exit }' ~/.aws/credentials 2>/dev/null
}

# Prints: <SUPERVISOR|NOSUPERVISOR> <UPLOADED|NOTUPLOADED> <DONE|NOTDONE>
pod_state() {
  remote "cd '$REMOTE_ROOT'
    pgrep -f '^bash scripts/runpod/pod_supervisor\.sh' >/dev/null && s=SUPERVISOR || s=NOSUPERVISOR
    [ -f .upload_complete ] && u=UPLOADED || u=NOTUPLOADED
    [ -f .volume_done ] && d=DONE || d=NOTDONE
    echo \$s \$u \$d"
}

start_supervisor() {
  log "starting pod_supervisor.sh"
  remote "cd '$REMOTE_ROOT' && tmux kill-session -t wm-supervisor 2>/dev/null;
    tmux new-session -d -s wm-supervisor 'bash scripts/runpod/pod_supervisor.sh 2>&1 | tee -a supervisor.log'" \
    || notify "failed to start pod_supervisor.sh"
}

check_once() {
  [[ -f "$STATE/notified_done" ]] && return 2
  caffeinate -ims -t $((INTERVAL * 2)) >/dev/null 2>&1 &
  load_env
  ensure_pod || return 0

  local state sup uploaded vol
  state="$(pod_state)" || { notify "pod SSH unreachable ($SSH_TARGET:$RUNPOD_TRANSFER_PORT)"; return 0; }
  read -r sup uploaded vol <<<"$state"
  log "pod: $sup $uploaded volume=$vol"

  if [[ "$vol" == NOTDONE && "$sup" == NOSUPERVISOR ]]; then
    push_scripts_and_creds || notify "could not push scripts/credentials to pod"
    start_supervisor
  fi

  if [[ "$uploaded" == NOTUPLOADED ]] && ! running '[p]ush_priority.sh'; then
    log "starting push_priority.sh"
    nohup bash "$SCRIPT_DIR/push_priority.sh" >>"$REPO_ROOT/push_priority.log" 2>&1 &
  fi

  if [[ "$MAC_DOWNLOAD" == 0 ]]; then
    touch "$STATE/download_done"
  elif [[ ! -f "$STATE/download_done" ]] && ! running '[s]ync_down.sh'; then
    log "starting sync_down.sh --watch"
    nohup bash -c "'$SCRIPT_DIR/sync_down.sh' --watch && touch '$STATE/download_done'" \
      >>"$REPO_ROOT/sync_down.log" 2>&1 &
  fi

  if [[ "$vol" == DONE && -f "$STATE/download_done" ]]; then
    [[ -f "$STATE/notified_done" ]] || notify "All done: cleaned and uploaded to the volume and R2"
    touch "$STATE/notified_done"
    return 2
  fi
}

locked_check() {
  local lock="$STATE/check.lock"
  if ! mkdir "$lock" 2>/dev/null; then
    # Stale lock from a crashed run: older than two intervals.
    [[ -n "$(find "$lock" -maxdepth 0 -mmin +$((INTERVAL / 30)) 2>/dev/null)" ]] || return 0
    rm -rf "$lock"; mkdir "$lock" || return 0
  fi
  check_once
  local rc=$?
  rmdir "$lock" 2>/dev/null
  return $rc
}

if [[ "${1:-}" == "--loop" ]]; then
  echo $$ >"$STATE/loop.pid"
  caffeinate -ims -w $$ &
  log "watchdog loop started (every ${INTERVAL}s)"
  while true; do
    locked_check
    [[ $? -eq 2 ]] && break
    sleep "$INTERVAL"
  done
  rm -f "$STATE/loop.pid"
  log "watchdog loop finished"
else
  locked_check
fi
