"""Resumable full-collection watermark removal (YOLO + LaMa)."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

from common import (
    DEFAULT_WEIGHTS,
    LOGO_MATCH_THRESHOLD,
    TMP_PREFIX,
    atomic_write_image,
    discover_images,
    find_ycf_logo,
    inpaint_lama,
    load_models,
    pick_device,
    read_image_raw,
    yolo_mask,
)


def load_status(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def append_status(path: Path, rel: str, status: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{rel}\t{status}\n")
        f.flush()


def run_batch(
    input_root: Path,
    output_root: Path,
    *,
    weights: Path = DEFAULT_WEIGHTS,
    conf: float = 0.08,
    dilate: int = 35,
    limit: int = 0,
    max_mask_frac: float = 0.15,
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    status_path = output_root / ".batch_status.tsv"
    review_path = output_root / "needs_review.txt"
    done = load_status(status_path)

    work = discover_images(input_root)
    work = [(s, r) for s, r in work if r.as_posix() not in done]
    if limit > 0:
        work = work[:limit]

    if not work:
        print("Batch: nothing pending.")
        return

    device = pick_device()
    print(f"Batch device: {device}, pending: {len(work)}")
    yolo, lama = load_models(weights, device)

    counts = {"completed": 0, "unchanged": 0, "review": 0, "failed": 0}

    def flag_review(rel_s: str, reason: str) -> None:
        with review_path.open("a", encoding="utf-8") as rf:
            rf.write(f"{rel_s}\t{reason}\n")
        append_status(status_path, rel_s, f"review:{reason}")
        counts["review"] += 1

    for i, (src, rel) in enumerate(work, 1):
        rel_s = rel.as_posix()
        out_path = output_root / rel
        try:
            img = read_image_raw(src)
            if img is None:
                append_status(status_path, rel_s, "failed:unreadable")
                counts["failed"] += 1
                continue

            mask, n_det = yolo_mask(img, yolo, conf=conf, dilate=dilate)

            logo_score, logo_box = find_ycf_logo(img)
            if logo_score >= LOGO_MATCH_THRESHOLD and logo_box:
                x1, y1, x2, y2 = logo_box
                if cv2.countNonZero(mask[y1:y2, x1:x2]) < 0.9 * (x2 - x1) * (y2 - y1):
                    logo_mask = np.zeros_like(mask)
                    cv2.rectangle(logo_mask, (x1, y1), (x2, y2), 255, thickness=-1)
                    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate))
                    mask |= cv2.dilate(logo_mask, kernel)

            if not mask.any():
                out_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, out_path)
                append_status(status_path, rel_s, "unchanged:no_watermark")
                counts["unchanged"] += 1
                continue

            mask_frac = cv2.countNonZero(mask) / mask.size
            if mask_frac > max_mask_frac:
                flag_review(rel_s, f"mask_too_large:{mask_frac:.3f}")
                continue

            result = inpaint_lama(img, mask, lama)
            after_score, _ = find_ycf_logo(result)
            if after_score >= LOGO_MATCH_THRESHOLD:
                flag_review(rel_s, f"logo_still_visible:{after_score:.2f}")
                continue
            atomic_write_image(out_path, result, src)
            append_status(status_path, rel_s, "completed")
            counts["completed"] += 1
        except Exception as exc:
            append_status(status_path, rel_s, f"failed:{exc}")
            counts["failed"] += 1
            print(f"FAIL {rel_s}: {exc}", file=sys.stderr)
            continue

        if i % 25 == 0 or i == len(work):
            print(
                f"[{i}/{len(work)}] ok={counts['completed']} unchanged={counts['unchanged']} "
                f"review={counts['review']} fail={counts['failed']}",
                flush=True,
            )

    summary = {
        "pending_start": len(work),
        **counts,
        "output_root": str(output_root.resolve()),
    }
    (output_root / ".batch_last_run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("Batch pass done:", summary)


if __name__ == "__main__":
    run_batch(Path(sys.argv[1]), Path(sys.argv[2]))
