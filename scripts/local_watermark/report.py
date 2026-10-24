"""Inventory verification, report, and contact sheet."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from common import discover_images, run_inventory


def load_status(output_root: Path) -> Counter[str]:
    path = output_root / ".batch_status.tsv"
    c: Counter[str] = Counter()
    if not path.is_file():
        return c
    for line in path.read_text(encoding="utf-8").splitlines():
        if "\t" not in line:
            continue
        _, status = line.split("\t", 1)
        key = status.split(":", 1)[0]
        c[key] += 1
    return c


def verify_accounting(input_root: Path, output_root: Path) -> dict:
    inputs = {rel.as_posix() for _, rel in discover_images(input_root)}
    status_path = output_root / ".batch_status.tsv"
    accounted: set[str] = set()
    if status_path.is_file():
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                accounted.add(line.split("\t", 1)[0])
    missing = sorted(inputs - accounted)
    return {
        "input_images": len(inputs),
        "accounted": len(accounted),
        "missing_from_status": len(missing),
        "missing_sample": missing[:20],
    }


def build_contact_sheet(pilot_dir: Path, out_path: Path, max_rows: int = 6) -> None:
    compares = sorted(pilot_dir.glob("*_compare.jpg"))[:max_rows]
    if not compares:
        return
    thumbs = []
    for p in compares:
        im = Image.open(p)
        w, h = im.size
        scale = 900 / w
        im = im.resize((900, int(h * scale)), Image.Resampling.LANCZOS)
        thumbs.append(im)
    total_h = sum(t.height for t in thumbs)
    sheet = Image.new("RGB", (900, total_h), (20, 20, 20))
    y = 0
    for t in thumbs:
        sheet.paste(t, (0, y))
        y += t.height
    sheet.save(out_path, quality=92)


def write_report(
    input_root: Path,
    output_root: Path,
    report_dir: Path,
    *,
    method: str,
    pilot_dir: Path,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    inv = run_inventory(input_root)
    status = load_status(output_root)
    verify = verify_accounting(input_root, output_root)

    out_bytes = 0
    out_count = 0
    if output_root.is_dir():
        for p in output_root.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                out_count += 1
                out_bytes += p.stat().st_size

    contact = report_dir / "contact_sheet.jpg"
    if pilot_dir.is_dir():
        build_contact_sheet(pilot_dir, contact)

    review_list = []
    review_path = output_root / "needs_review.txt"
    if review_path.is_file():
        review_list = review_path.read_text(encoding="utf-8").splitlines()

    body = {
        "method": method,
        "input_root": str(input_root.resolve()),
        "output_root": str(output_root.resolve()),
        "inventory": json.loads(inv.to_json()),
        "status_counts": dict(status),
        "verification": verify,
        "output_images_written": out_count,
        "output_bytes": out_bytes,
        "limitations": [
            "LaMa invents plausible texture; detail fully hidden under the logo (e.g. spray at a hull waterline) is not recovered.",
            "The detector also removes small photographer credits, not just the YachtCharterFleet logo.",
            "Inpainted JPEGs are re-encoded once at quality 95 (4:4:4) with EXIF and ICC copied; images with no watermark are byte-for-byte copies.",
        ],
        "needs_review_count": len(review_list),
        "needs_review_sample": review_list[:30],
    }
    report_path = report_dir / "watermark_removal_report.json"
    report_path.write_text(json.dumps(body, indent=2), encoding="utf-8")

    md = report_dir / "watermark_removal_report.md"
    md.write_text(
        f"""# Local watermark removal report

## Method
{method}

## Counts
- Input images: {inv.image_count} ({inv.total_bytes / 1e9:.2f} GB)
- Watermark removed: {status.get('completed', 0)}
- No watermark, copied unchanged: {status.get('unchanged', 0)}
- Review needed (withheld from output): {status.get('review', 0)}
- Failed: {status.get('failed', 0)}
- Output files on disk: {out_count} ({out_bytes / 1e9:.2f} GB)

## Verification
- Accounted in status log: {verify['accounted']} / {verify['input_images']}
- Still unprocessed: {verify['missing_from_status']}

## Pilot contact sheet
{contact.resolve() if contact.is_file() else 'Run pilot first'}

## Review list
{output_root / 'needs_review.txt'}
""",
        encoding="utf-8",
    )
    return report_path
