#!/usr/bin/env python3
"""Second-pass scan of every image already on R2 (or RunPod volume). Deletes failures.

  python scripts/audit_r2_publish.py --target r2
  python scripts/audit_r2_publish.py --target volume --dry-run

Env: same as push_clean (.s3.env on pod, or .env.local keys when run from Mac).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from publish_gate import passes_publish_gate_bytes  # noqa: E402

PREFIX = os.environ.get("RUNPOD_S3_PREFIX", "clean-yachts")


def client(target: str):
    if target == "volume":
        return boto3.client(
            "s3",
            endpoint_url=os.environ.get("RUNPOD_S3_ENDPOINT", "https://s3api-us-ca-2.runpod.io"),
            region_name=os.environ.get("RUNPOD_S3_REGION", "us-ca-2"),
            aws_access_key_id=os.environ["RUNPOD_S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["RUNPOD_S3_SECRET_ACCESS_KEY"],
            config=Config(max_pool_connections=32),
        ), os.environ.get("RUNPOD_S3_BUCKET", "jc7mhii8r8")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        region_name="auto",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(max_pool_connections=32),
    ), os.environ["R2_BUCKET"]


def list_jpg_keys(s3, bucket: str) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": f"{PREFIX}/"}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.lower().endswith((".jpg", ".jpeg")):
                keys.append(k)
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    return keys


def check_one(s3, bucket: str, key: str) -> tuple[str, str, float]:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    ok, score = passes_publish_gate_bytes(body)
    return key, "ok" if ok else "trident", score


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["r2", "volume"], default="r2")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    s3, bucket = client(args.target)
    keys = list_jpg_keys(s3, bucket)
    if args.limit:
        keys = keys[: args.limit]
    print(f"audit {args.target}: {len(keys)} images under {PREFIX}/", flush=True)

    report = ROOT / f"audit_publish_{args.target}.csv"
    blocked = 0
    with report.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["key", "status", "logo_score"])
        with ThreadPoolExecutor(args.workers) as pool:
            futures = {pool.submit(check_one, s3, bucket, k): k for k in keys}
            for i, fut in enumerate(as_completed(futures), 1):
                key, status, score = fut.result()
                wr.writerow([key, status, f"{score:.3f}"])
                if status == "trident":
                    blocked += 1
                    if not args.dry_run:
                        s3.delete_object(Bucket=bucket, Key=key)
                if i % 500 == 0:
                    print(f"  {i}/{len(keys)} blocked={blocked}", flush=True)

    print(f"done: blocked={blocked} report={report}", flush=True)
    if blocked and not args.dry_run:
        print("Removed blocked objects from bucket. Re-run GPU clean for those paths.", flush=True)


if __name__ == "__main__":
    main()
