#!/usr/bin/env python3
"""Delete or replace images that failed trident QA.

Replace = delete published copies + remove from processing log so GPU workers
run remove_watermarks.py again on the raw originals.

  # Scan R2, delete failures, requeue on pod log (run from Mac with .env.local)
  python scripts/reprocess_trident.py --scan-r2 --delete r2 --requeue

  # From latest audit CSV only
  python scripts/reprocess_trident.py --from-csv audit_publish_r2.csv --delete r2 volume --requeue

  # From pod blocked / quarantine logs (paths on pod filesystem when run there)
  python scripts/reprocess_trident.py --from-blocked --from-quarantine --delete volume --requeue --delete-local

  --dry-run prints actions without deleting or editing logs.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.s3_targets import (  # noqa: E402
    PREFIX,
    key_to_processing_rel,
    processing_rel_to_key,
)

PUBLIC_OUT = ROOT / "public" / "yachts"
CLEAN_FLAT = ROOT / "clean-yachts"
PROC_LOG = PUBLIC_OUT / ".processing_log.txt"
BLOCKED_LOG = ROOT / ".publish_blocked.txt"
QUARANTINE_LOG = PUBLIC_OUT / ".trident_quarantine.txt"
REPROCESS_MANIFEST = ROOT / ".reprocess_trident_manifest.txt"


def load_keys_from_csv(path: Path) -> set[str]:
    keys: set[str] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("status") == "trident" and row.get("key"):
                keys.add(row["key"])
    return keys


def load_keys_from_manifest(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.is_file():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        k = line.strip()
        if k.startswith(f"{PREFIX}/"):
            keys.add(k)
    return keys


def load_keys_from_blocked(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.is_file():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("\t")
        if parts and parts[0].startswith(f"{PREFIX}/"):
            keys.add(parts[0])
    return keys


def load_rels_from_quarantine(path: Path) -> set[str]:
    rels: set[str] = set()
    if not path.is_file():
        return rels
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("\t")
        if parts and parts[0]:
            rels.add(parts[0])
    return rels


def rels_to_keys(rels: set[str]) -> set[str]:
    keys: set[str] = set()
    for rel in rels:
        try:
            keys.add(processing_rel_to_key(rel))
        except ValueError:
            continue
    return keys


def scan_r2_keys(limit: int = 0) -> set[str]:
    from publish_gate import passes_publish_gate_bytes  # noqa: E402

    s3, bucket = make_client("r2")
    keys: list[str] = []
    token = None
    while True:
        page = s3.list_objects_v2(Bucket=bucket, Prefix=f"{PREFIX}/", ContinuationToken=token)
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.lower().endswith((".jpg", ".jpeg")):
                keys.append(k)
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    if limit:
        keys = keys[:limit]

    bad: set[str] = set()
    for i, key in enumerate(keys, 1):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        ok, _score = passes_publish_gate_bytes(body)
        if not ok:
            bad.add(key)
        if i % 500 == 0:
            print(f"  scanned {i}/{len(keys)} bad={len(bad)}", flush=True)
    return bad


def delete_keys(target: str, keys: set[str], dry: bool) -> int:
    from scripts.s3_targets import make_client

    if not keys:
        return 0
    s3, bucket = make_client(target)
    n = 0
    for key in sorted(keys):
        if dry:
            print(f"  would delete {target}:/{key}")
        else:
            s3.delete_object(Bucket=bucket, Key=key)
        n += 1
    return n


def delete_local(keys: set[str], dry: bool) -> int:
    n = 0
    total = len(keys)
    for i, key in enumerate(sorted(keys), 1):
        rel = key_to_processing_rel(key)
        if not rel:
            continue
        slug, _, name = rel.partition("/images/")
        paths = [
            PUBLIC_OUT / rel,
            CLEAN_FLAT / slug / name,
        ]
        for p in paths:
            if p.is_file():
                if dry:
                    print(f"  would delete local {p}")
                else:
                    p.unlink()
                n += 1
        if i % 500 == 0:
            print(f"  local delete {i}/{total}", flush=True)
    return n


def requeue_processing_log(keys: set[str], dry: bool) -> int:
    if not PROC_LOG.is_file():
        print(f"warning: no {PROC_LOG}", flush=True)
        return 0
    drop = {key_to_processing_rel(k) for k in keys}
    drop.discard(None)
    lines = [ln.strip() for ln in PROC_LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]
    kept = [ln for ln in lines if ln not in drop]
    removed = len(lines) - len(kept)
    if removed and not dry:
        PROC_LOG.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return removed


def write_manifest(keys: set[str], dry: bool) -> None:
    if dry or not keys:
        return
    REPROCESS_MANIFEST.write_text("\n".join(sorted(keys)) + "\n", encoding="utf-8")


def clear_block_logs(dry: bool) -> None:
    for path in (BLOCKED_LOG, QUARANTINE_LOG):
        if path.is_file() and not dry:
            path.write_text("", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Delete failed trident images and requeue for replace")
    ap.add_argument("--scan-r2", action="store_true", help="Scan entire R2 prefix (slow)")
    ap.add_argument("--scan-limit", type=int, default=0)
    ap.add_argument("--from-csv", type=Path, action="append", default=[])
    ap.add_argument("--from-blocked", action="store_true")
    ap.add_argument("--from-quarantine", action="store_true")
    ap.add_argument("--from-manifest", action="store_true", help=f"Read keys from {REPROCESS_MANIFEST.name}")
    ap.add_argument(
        "--delete",
        nargs="+",
        choices=["r2", "volume"],
        default=[],
        help="Remove objects from these buckets (default: none unless --scan-r2)",
    )
    ap.add_argument("--delete-local", action="store_true")
    ap.add_argument("--requeue", action="store_true", help="Remove paths from .processing_log.txt for GPU replace")
    ap.add_argument(
        "--replace",
        action="store_true",
        help="Shorthand: --delete r2 volume --delete-local --requeue (use on pod)",
    )
    ap.add_argument(
        "--requeue-only",
        action="store_true",
        help="After Mac audit: local file delete + processing log only (no boto3)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.requeue_only:
        args.delete_local = True
        args.requeue = True
    elif args.replace:
        args.delete = list(set(args.delete) | {"r2", "volume"})
        args.delete_local = True
        args.requeue = True

    keys: set[str] = set()
    if args.scan_r2:
        print("scanning R2 for tridents…", flush=True)
        keys |= scan_r2_keys(args.scan_limit)
    for csv_path in args.from_csv:
        keys |= load_keys_from_csv(ROOT / csv_path if not csv_path.is_absolute() else csv_path)
    keys |= load_keys_from_blocked(BLOCKED_LOG)
    if args.from_quarantine:
        keys |= rels_to_keys(load_rels_from_quarantine(QUARANTINE_LOG))
    if args.from_manifest:
        keys |= load_keys_from_manifest(REPROCESS_MANIFEST)

    if not keys:
        print("nothing to reprocess (no keys collected)", flush=True)
        return

    targets = args.delete or (["r2"] if args.scan_r2 else [])
    print(f"reprocess: {len(keys)} images, delete targets={targets or 'none'}", flush=True)

    for t in targets:
        n = delete_keys(t, keys, args.dry_run)
        print(f"  deleted {n} from {t}", flush=True)

    if args.delete_local:
        n = delete_local(keys, args.dry_run)
        print(f"  deleted {n} local files", flush=True)

    if args.requeue:
        n = requeue_processing_log(keys, args.dry_run)
        print(f"  requeued {n} processing-log entries (GPU will replace)", flush=True)

    write_manifest(keys, args.dry_run)
    if args.requeue and not args.dry_run:
        clear_block_logs(args.dry_run)

    print("done. Workers pick up requeued images on next remove_watermarks pass.", flush=True)


if __name__ == "__main__":
    main()
