#!/usr/bin/env bash
# Sync clean-yachts/ to the RunPod network volume over its S3 API.
# Skips byte-identical images within a yacht (keeps the lowest-numbered copy)
# and anything already on the volume, so re-runs only upload new files.
#
# Credentials: `aws configure --profile runpod`
#   Access key = RunPod user ID (user_...), Secret = RunPod S3 API key.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${SRC:-$ROOT/clean-yachts}"
BUCKET="${RUNPOD_BUCKET:-jc7mhii8r8}"
PREFIX="${RUNPOD_PREFIX:-clean-yachts}"
REGION="${RUNPOD_REGION:-us-ca-2}"
ENDPOINT="${RUNPOD_ENDPOINT:-https://s3api-us-ca-2.runpod.io}"
PROFILE="${AWS_PROFILE:-runpod}"
AWS="${AWS_BIN:-$(command -v aws || echo "$HOME/.venvs/awscli/bin/aws")}"

cd "$SRC"

EXCLUDES=()
while IFS= read -r f; do
  EXCLUDES+=(--exclude "$f")
done < <(
  find . -type f -iname '*.jpg' -print0 | xargs -0 md5 -r | sed 's# \./# #' | sort -k2 \
    | awk '{ if (seen[$1]++) print $2 }'
)
echo "Skipping $(( ${#EXCLUDES[@]} / 2 )) duplicate images"

"$AWS" s3 sync . "s3://$BUCKET/$PREFIX/" \
  --profile "$PROFILE" --region "$REGION" --endpoint-url "$ENDPOINT" \
  --exclude ".*" --exclude "*/.*" "${EXCLUDES[@]}" \
  "$@"
