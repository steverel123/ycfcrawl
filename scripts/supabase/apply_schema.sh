#!/usr/bin/env bash
# Apply supabase/schema.sql using DATABASE_URL from .env
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
  echo "Create .env from .env.example and set DATABASE_URL (Session pooler, not direct db.* host)." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

DB_URL="${DATABASE_URL:-}"
if [[ -z "$DB_URL" ]]; then
  echo "DATABASE_URL is not set in .env" >&2
  exit 1
fi

if [[ "$DB_URL" == *"@db."*".supabase.co"* ]]; then
  echo "WARNING: db.*.supabase.co is IPv6-only. Many Mac/network setups cannot resolve or reach it." >&2
  echo "Use the Session pooler URL from Supabase Dashboard → Database → Connection string." >&2
  echo "  Host looks like: aws-0-REGION.pooler.supabase.com:5432" >&2
  echo "  User looks like: postgres.cqnjlcosmjzuquaczmav" >&2
  echo "" >&2
  if ! dig +short "${DB_URL#*@}" A 2>/dev/null | grep -q .; then
    echo "No IPv4 (A) record for DB host. Replace DATABASE_URL with the pooler string, or run" >&2
    echo "supabase/schema.sql in the Supabase SQL Editor (Dashboard)." >&2
    exit 1
  fi
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "Install psql: brew install libpq && brew link --force libpq" >&2
  exit 1
fi

run_psql() {
  local file="$1"
  if ! psql "$DB_URL" -v ON_ERROR_STOP=1 -f "$file" 2> >(tee /tmp/ycfcrawl_psql.err >&2); then
    if grep -q "password authentication failed" /tmp/ycfcrawl_psql.err 2>/dev/null; then
      echo "" >&2
      echo "Password rejected by the pooler. Fix DATABASE_URL:" >&2
      echo "  1. Supabase Dashboard → Project Settings → Database → Reset database password" >&2
      echo "  2. Database → Connection string → Session pooler → URI (region eu-west-1)" >&2
      echo "  3. Paste the full URI into .env as DATABASE_URL and rerun this script." >&2
      echo "Or run supabase/schema.sql and supabase/storage_policy.sql in the SQL Editor." >&2
    fi
    exit 1
  fi
}

echo "Applying supabase/schema.sql ..."
run_psql supabase/schema.sql
echo "Applying supabase/storage_policy.sql ..."
run_psql supabase/storage_policy.sql
echo "Done."
