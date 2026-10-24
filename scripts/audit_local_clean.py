#!/usr/bin/env python3
"""Second-pass scan of every local cleaned image (clean-yachts/ or public/yachts/)."""
from __future__ import annotations

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from publish_gate import passes_publish_gate_file  # noqa: E402


def jobs(root: Path):
    for yacht in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        images = yacht / "images"
        base = images if images.is_dir() else yacht
        for f in sorted(base.glob("*.jpg")):
            rel = f.relative_to(root)
            yield str(f), str(rel)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT / "clean-yachts")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--requeue", action="store_true", help="Drop blocked paths from public/yachts/.processing_log.txt")
    args = ap.parse_args()

    work = list(jobs(args.root))
    print(f"audit local: {len(work)} images in {args.root}", flush=True)
    blocked: list[str] = []
    report = ROOT / "audit_publish_local.csv"

    def one(item: tuple[str, str]) -> tuple[str, str, float]:
        path, rel = item
        ok, score = passes_publish_gate_file(path)
        return rel, "ok" if ok else "trident", score

    with report.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["path", "status", "logo_score"])
        with ThreadPoolExecutor(args.workers) as pool:
            for row in pool.map(one, work, chunksize=32):
                wr.writerow([row[0], row[1], f"{row[2]:.3f}"])
                if row[1] == "trident":
                    blocked.append(row[0])

    print(f"blocked={len(blocked)} report={report}", flush=True)
    if args.requeue and blocked:
        log = ROOT / "public" / "yachts" / ".processing_log.txt"
        if log.is_file():
            drop = set(blocked)
            for rel in blocked:
                parts = Path(rel).parts
                if len(parts) == 2:
                    drop.add(f"{parts[0]}/images/{parts[1]}")
            keep = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip() not in drop]
            log.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
            print(f"requeued {len(drop)} paths (removed from {log})", flush=True)


if __name__ == "__main__":
    main()
