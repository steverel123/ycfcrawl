#!/usr/bin/env bash
# Cursor MCP stdio entrypoint: loads RUNPOD_API_KEY from project .env
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${RUNPOD_API_KEY:-}" ]]; then
  echo "RUNPOD_API_KEY missing. Add it to $ENV_FILE" >&2
  exit 1
fi

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

NPX="$(command -v npx || true)"
if [[ -z "$NPX" ]]; then
  echo "npx not found on PATH" >&2
  exit 1
fi

exec "$NPX" -y @runpod/mcp-server@latest
