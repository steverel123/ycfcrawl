#!/usr/bin/env bash
# Shared SSH/rsync settings for RunPod network volumes. Source after env.sh.
init_transfer_ssh() {
  SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
  SSH_KEY="${SSH_KEY/#\~/$HOME}"

  RUNPOD_SSH_CONTROL="${RUNPOD_SSH_CONTROL:-/tmp/runpod-ssh-${RUNPOD_TRANSFER_PORT}}"
  mkdir -p "$RUNPOD_SSH_CONTROL"

  SSH_OPTS=(
    -i "$SSH_KEY"
    -p "$RUNPOD_TRANSFER_PORT"
    -o StrictHostKeyChecking=accept-new
    -o ControlMaster=auto
    -o "ControlPath=${RUNPOD_SSH_CONTROL}/%r@%h:%p-$$"
    -o ControlPersist=600
    -o Compression=no
  )

  # scp uses -P for port (not ssh's -p).
  SCP_OPTS=(
    -i "$SSH_KEY"
    -P "$RUNPOD_TRANSFER_PORT"
    -o StrictHostKeyChecking=accept-new
  )

  # Network FS on RunPod rejects chown; skip perms. No -z: JPEGs do not compress.
  RSYNC_OPTS=( -a --no-perms --no-owner --no-group --omit-dir-times )
  # Some network volumes also reject setting file mtimes, so uploads skip times
  # and resume by comparing sizes.
  RSYNC_UP_OPTS=( -rl --no-perms --no-owner --no-group --no-times --size-only )
  RSYNC_SSH="ssh ${SSH_OPTS[*]}"
  # Parallel uploads: no multiplex (RunPod rejects many sessions on one control socket).
  RSYNC_SSH_PARALLEL="ssh -i $SSH_KEY -p $RUNPOD_TRANSFER_PORT -o StrictHostKeyChecking=accept-new -o Compression=no -o ControlMaster=no"
  SSH_TARGET="${RUNPOD_TRANSFER_USER}@${RUNPOD_TRANSFER_HOST}"
}

cleanup_transfer_ssh() {
  if [[ -n "${SSH_TARGET:-}" && -n "${SSH_OPTS[*]:-}" ]]; then
    ssh "${SSH_OPTS[@]}" -O exit "$SSH_TARGET" 2>/dev/null || true
  fi
}
