#!/usr/bin/env bash
# One-time: authorize your Mac SSH key for Direct TCP (213.x:port).
# RunPod proxy works without this; direct TCP/rsync does not.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"

PUBKEY_FILE="${SSH_KEY}.pub"
if [[ ! -f "$PUBKEY_FILE" ]]; then
  echo "Missing $PUBKEY_FILE" >&2
  exit 1
fi

PUBKEY="$(cat "$PUBKEY_FILE")"

echo "==> Adding your public key to the pod via RunPod proxy..."
ssh -tt -i "$SSH_KEY" -p "$RUNPOD_SSH_PORT" -o StrictHostKeyChecking=accept-new \
  "$RUNPOD_SSH" bash -s <<REMOTE
set -euo pipefail
mkdir -p /root/.ssh
chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
grep -qxF '$PUBKEY' /root/.ssh/authorized_keys || echo '$PUBKEY' >> /root/.ssh/authorized_keys
echo "authorized_keys lines: \$(wc -l < /root/.ssh/authorized_keys)"
exit
REMOTE

if [[ -n "${RUNPOD_DIRECT_HOST:-}" && -n "${RUNPOD_DIRECT_PORT:-}" ]]; then
  echo "==> Testing direct TCP: $RUNPOD_DIRECT_USER@$RUNPOD_DIRECT_HOST:$RUNPOD_DIRECT_PORT"
  ssh -i "$SSH_KEY" -p "$RUNPOD_DIRECT_PORT" -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
    "${RUNPOD_DIRECT_USER}@${RUNPOD_DIRECT_HOST}" "echo direct_tcp_ok && hostname"
  echo "Direct TCP SSH is ready. Run: ./scripts/runpod/sync_up.sh"
else
  echo "Set RUNPOD_DIRECT_HOST and RUNPOD_DIRECT_PORT in .env, then re-run this script."
fi
