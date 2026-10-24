#!/usr/bin/env python3
"""Run ON the pod. Upload newly cleaned images to an S3-compatible target.

Reads only the lines appended to public/yachts/.processing_log.txt since the last
run (offset kept per target in .push_offset_<target>), so it never scans the network FS.

    public/yachts/<yacht>/images/NNN.jpg  ->  <prefix>/<yacht>/NNN.jpg
    yachts/<yacht>/{metadata.json,README.md} -> <prefix>/<yacht>/

Skips keys already in the target and images whose MD5 matches another image of
the same yacht (single-part ETags are MD5 on both RunPod and R2).

    python push_clean.py --target volume     RunPod network volume (RUNPOD_S3_*)
    python push_clean.py --target r2         Cloudflare R2 (R2_*)
    add --full to re-check every line of the log

R2 is first seeded from the volume over S3 (no network-FS reads, and it includes
images cleaned on the Mac that the pod never had); --full repeats that seed.
Reads from the network FS stay at --workers (default 8) so the GPU workers,
which share that FS, are not starved.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(os.environ.get("YCFCrawl_ROOT", "/workspace/ycfcrawl"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from publish_gate import passes_publish_gate_bytes  # noqa: E402

BLOCKED_LOG = ROOT / ".publish_blocked.txt"
CLEANED = ROOT / "public" / "yachts"
ORIGINALS = ROOT / "yachts"
LOG = CLEANED / ".processing_log.txt"
PREFIX = os.environ.get("CLEAN_PREFIX", "clean-yachts")
EXTRAS = {"metadata.json": "application/json", "README.md": "text/markdown; charset=utf-8"}


def make_client(cfg: dict):
    return boto3.client(
        "s3", **cfg,
        config=Config(
            max_pool_connections=64,
            retries={"max_attempts": 8, "mode": "adaptive"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def seed_r2_from_volume(r2, r2_bucket: str, r2_keys: set[str]) -> int:
    """Copy volume objects missing from R2 over S3. Returns the number of failures."""
    vol_cfg, vol_bucket = target_config("volume")
    vol = make_client(vol_cfg)
    todo = []
    for page in vol.get_paginator("list_objects_v2").paginate(Bucket=vol_bucket, Prefix=f"{PREFIX}/"):
        for obj in page.get("Contents", []):
            if obj["Key"] not in r2_keys:
                todo.append(obj["Key"])
    print(f"seed r2: {len(todo)} objects to copy from the volume", flush=True)

    def copy(key: str) -> None:
        res = vol.get_object(Bucket=vol_bucket, Key=key)
        body = res["Body"].read()
        r2.put_object(Bucket=r2_bucket, Key=key, Body=body,
                      ContentType=res.get("ContentType") or "application/octet-stream")

    failed = 0
    with ThreadPoolExecutor(32) as pool:
        futures = {pool.submit(copy, key): key for key in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            key = futures[fut]
            try:
                fut.result()
                r2_keys.add(key)
            except Exception as exc:
                failed += 1
                print(f"seed r2: failed {key}: {exc}", file=sys.stderr, flush=True)
            if i % 1000 == 0:
                print(f"seed r2: {i}/{len(todo)} failed={failed}", flush=True)
    print(f"seed r2: done, {len(todo) - failed} copied, {failed} failed", flush=True)
    return failed


def target_config(name: str) -> tuple[dict, str]:
    if name == "volume":
        return {
            "endpoint_url": os.environ.get("RUNPOD_S3_ENDPOINT", "https://s3api-us-ca-2.runpod.io"),
            "region_name": os.environ.get("RUNPOD_S3_REGION", "us-ca-2"),
            "aws_access_key_id": os.environ["RUNPOD_S3_ACCESS_KEY_ID"],
            "aws_secret_access_key": os.environ["RUNPOD_S3_SECRET_ACCESS_KEY"],
        }, os.environ.get("RUNPOD_S3_BUCKET", "jc7mhii8r8")
    if name == "r2":
        return {
            "endpoint_url": f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            "region_name": "auto",
            "aws_access_key_id": os.environ["R2_ACCESS_KEY_ID"],
            "aws_secret_access_key": os.environ["R2_SECRET_ACCESS_KEY"],
        }, os.environ["R2_BUCKET"]
    raise SystemExit(f"unknown target {name}")


def list_target(s3, bucket: str) -> tuple[set[str], dict[str, set[str]]]:
    keys: set[str] = set()
    hashes: dict[str, set[str]] = defaultdict(set)
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"{PREFIX}/"):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
            hashes[obj["Key"].split("/")[1]].add(obj["ETag"].strip('"'))
    return keys, hashes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["volume", "r2"], required=True)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    offset_file = ROOT / f".push_offset_{args.target}"
    tag = f"push {args.target}"
    cfg, bucket = target_config(args.target)
    s3 = make_client(cfg)

    if args.target == "r2" and (args.full or not offset_file.is_file()):
        # The volume is complete up to its own offset; seed from it, then continue from there.
        vol_offset_file = ROOT / ".push_offset_volume"
        vol_offset = vol_offset_file.read_text().strip() if vol_offset_file.is_file() else "0"
        keys, _ = list_target(s3, bucket)
        if seed_r2_from_volume(s3, bucket, keys):
            raise SystemExit(1)
        if not args.full:
            offset_file.write_text(vol_offset)

    lines = [l.strip() for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    start = 0 if args.full or not offset_file.is_file() else int(offset_file.read_text().strip() or 0)
    new = lines[start:]
    if not new:
        print(f"{tag}: nothing new (log at {len(lines)})", flush=True)
        return

    keys, hashes = list_target(s3, bucket)
    print(f"{tag}: {len(new)} new log lines, {len(keys)} objects in target", flush=True)

    images: list[tuple[str, Path, str]] = []
    extras: list[tuple[Path, str, str]] = []
    yachts_seen: set[str] = set()
    for rel in new:
        parts = rel.split("/")
        if len(parts) != 3 or parts[1] != "images":
            continue
        yacht, name = parts[0], parts[2]
        yachts_seen.add(yacht)
        key = f"{PREFIX}/{yacht}/{name}"
        if key not in keys:
            keys.add(key)
            images.append((yacht, CLEANED / rel, key))
    for yacht in sorted(yachts_seen):
        for extra, ctype in EXTRAS.items():
            key = f"{PREFIX}/{yacht}/{extra}"
            if key not in keys:
                keys.add(key)
                extras.append((ORIGINALS / yacht / extra, key, ctype))

    lock = threading.Lock()

    def put_image(yacht: str, src: Path, key: str) -> str:
        # Network FS reads are latency-bound: read once, hash and upload from memory.
        data = src.read_bytes()
        ok, score = passes_publish_gate_bytes(data)
        if not ok:
            with lock:
                with BLOCKED_LOG.open("a", encoding="utf-8") as bf:
                    bf.write(f"{key}\t{score:.3f}\t{src}\n")
            return "blocked"
        digest = hashlib.md5(data).hexdigest()
        with lock:
            if digest in hashes[yacht]:
                return "dupe"
            hashes[yacht].add(digest)
        s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType="image/jpeg")
        return "ok"

    def put_extra(src: Path, key: str, ctype: str) -> str:
        s3.put_object(Bucket=bucket, Key=key, Body=src.read_bytes(), ContentType=ctype)
        return "ok"

    counts = {"ok": 0, "dupe": 0, "blocked": 0, "missing": 0, "failed": 0}
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(put_image, *job): job[2] for job in images}
        futures.update({pool.submit(put_extra, *job): job[1] for job in extras})
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                counts[fut.result()] += 1
            except FileNotFoundError:
                counts["missing"] += 1
            except Exception as exc:
                counts["failed"] += 1
                print(f"{tag}: failed {futures[fut]}: {exc}", file=sys.stderr, flush=True)
            if i % 1000 == 0:
                print(f"{tag}: {i}/{len(futures)} {counts}", flush=True)

    print(f"{tag}: done {counts}", flush=True)
    if counts["failed"]:
        raise SystemExit(1)
    offset_file.write_text(str(len(lines)))


if __name__ == "__main__":
    main()
