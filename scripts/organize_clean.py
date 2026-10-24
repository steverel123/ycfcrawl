#!/usr/bin/env python3
"""Build one self-contained folder per yacht from the cleaned output.

    public/yachts/<yacht-id>/images/NNN.jpg  ->  clean-yachts/<yacht-id>/NNN.jpg
    yachts/<yacht-id>/metadata.json          ->  clean-yachts/<yacht-id>/metadata.json

Images are hard links (no extra disk space). Safe to re-run: existing files are
left alone, so each run only adds newly downloaded images.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLEANED = ROOT / "public" / "yachts"
ORIGINALS = ROOT / "yachts"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "clean-yachts"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> None:
    added = yachts = 0
    for yacht_dir in sorted(p for p in CLEANED.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        images = sorted(
            p for p in (yacht_dir / "images").glob("*")
            if p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")
        )
        if not images:
            continue
        dest = OUT / yacht_dir.name
        dest.mkdir(parents=True, exist_ok=True)
        yachts += 1
        for src in images:
            target = dest / src.name
            if target.exists():
                continue
            try:
                os.link(src, target)
            except OSError:
                shutil.copy2(src, target)
            added += 1
        for extra in ("metadata.json", "README.md"):
            src = ORIGINALS / yacht_dir.name / extra
            if src.is_file() and not (dest / extra).exists():
                shutil.copy2(src, dest / extra)
    total = sum(1 for p in OUT.rglob("*") if p.suffix.lower() in IMAGE_EXTS)
    print(f"clean-yachts: {yachts} yachts, {total} images (+{added} new) -> {OUT}")


if __name__ == "__main__":
    main()
