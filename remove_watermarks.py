#!/usr/bin/env python3
"""
Batch watermark removal with jferments pipeline (YOLO detect + LaMa inpaint).

Page-ready output mirrors the scrape layout:

  yachts/defiance-47450/images/001.jpg   (source, watermarked)
  public/yachts/defiance-47450/images/001.jpg   (clean, for web)

Served from `public/` at URL: /yachts/defiance-47450/images/001.jpg

Metadata `downloaded_images[].file` stays `images/001.jpg`; pages join:
  /yachts/{folder_name}/ + file
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import zlib
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from simple_lama_inpainting import SimpleLama
from ultralytics import YOLO

from trident_logo import (
    LOGO_MATCH_THRESHOLD,
    PUBLISH_MAX_LOGO_SCORE,
    build_ycf_geom_mask,
    find_ycf_logo,
)

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
DEFAULT_WEIGHTS = (
    Path(__file__).resolve().parent
    / "jferments-watermark-remover"
    / "yolo11x-train28-best.pt"
)


def pick_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda:0")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_processed(log_path: Path) -> set[str]:
    if not log_path.is_file():
        return set()
    return {line.strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()}


def discover_yacht_images(
    yachts_root: Path,
    *,
    yacht_filter: str | None,
    per_yacht_max: int | None = None,
    per_yacht_skip: int = 0,
) -> list[tuple[Path, Path]]:
    """Return (source_path, rel_path) where rel_path is e.g. defiance-47450/images/001.jpg."""
    pairs: list[tuple[Path, Path]] = []
    for yacht_dir in sorted(yachts_root.iterdir()):
        if not yacht_dir.is_dir():
            continue
        if yacht_filter and yacht_dir.name != yacht_filter:
            continue
        images_dir = yacht_dir / "images"
        if not images_dir.is_dir():
            continue
        yacht_images: list[tuple[Path, Path]] = []
        for image_path in sorted(images_dir.iterdir()):
            if image_path.suffix.lower() not in IMG_EXTS:
                continue
            rel = Path(yacht_dir.name) / "images" / image_path.name
            yacht_images.append((image_path, rel))
        if per_yacht_skip:
            yacht_images = yacht_images[per_yacht_skip:]
        if per_yacht_max is not None:
            yacht_images = yacht_images[:per_yacht_max]
        pairs.extend(yacht_images)
    return pairs


def load_models(weights: Path, device: torch.device) -> tuple[YOLO, SimpleLama]:
    yolo = YOLO(str(weights))
    yolo.to(device)
    return yolo, SimpleLama(device=device)


def _logo_and_geom_mask(img_bgr: np.ndarray, dilate: int) -> np.ndarray:
    """YOLO often misses the trident; template + geometry cover the bottom-left mark."""
    h, w = img_bgr.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate)) if dilate > 0 else None

    score, box = find_ycf_logo(img_bgr)
    if score >= LOGO_MATCH_THRESHOLD * 0.6 and box:
        x1, y1, x2, y2 = box
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, thickness=-1)

    geom = build_ycf_geom_mask(img_bgr, fallback=True)
    mask |= geom

    if kernel is not None and mask.any():
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def _inpaint(img_bgr: np.ndarray, mask: np.ndarray, lama: SimpleLama) -> np.ndarray:
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    result_rgb = np.array(lama(Image.fromarray(rgb), Image.fromarray(mask)))
    h, w = img_bgr.shape[:2]
    return cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)[:h, :w]


def remove_watermark(
    img_bgr: np.ndarray,
    *,
    yolo: YOLO,
    lama: SimpleLama,
    conf: float,
    dilate: int,
) -> tuple[np.ndarray, int, float]:
    predictions = yolo(img_bgr, conf=conf, verbose=False)[0]
    boxes = predictions.boxes.xyxy.cpu().numpy() if len(predictions.boxes.xyxy) else []

    mask = np.zeros(img_bgr.shape[:2], dtype=np.uint8)
    for box in boxes:
        x1, y1, x2, y2 = map(int, box)
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, thickness=-1)

    if dilate > 0 and mask.any():
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate))
        mask = cv2.dilate(mask, kernel, iterations=1)

    mask |= _logo_and_geom_mask(img_bgr, dilate)

    if not mask.any():
        after = find_ycf_logo(img_bgr)[0]
        return img_bgr, len(boxes), after

    result = _inpaint(img_bgr, mask, lama)
    after = find_ycf_logo(result)[0]
    if after >= PUBLISH_MAX_LOGO_SCORE:
        # Second pass: inpaint only the logo region (YOLO already applied above).
        logo_only = _logo_and_geom_mask(img_bgr, dilate + 12)
        if logo_only.any():
            result = _inpaint(result, logo_only, lama)
            after = find_ycf_logo(result)[0]
    return result, len(boxes), after


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Remove watermarks into public/yachts/ for page use"
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=root / "yachts",
        help="Scraped yachts root (default: yachts/)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=root / "public/yachts",
        help="Page-ready output root (default: public/yachts/)",
    )
    parser.add_argument("-w", "--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--conf", type=float, default=0.08)
    parser.add_argument(
        "--dilate",
        type=int,
        default=35,
        help="Pixels to expand detected mask (larger = wider erasure)",
    )
    parser.add_argument(
        "--yacht",
        help="Process one yacht folder only, e.g. defiance-47450",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max images (0 = all)")
    parser.add_argument(
        "--per-yacht-max",
        type=int,
        default=0,
        help="Only first N images per yacht folder (0 = no cap). Use 5 for hero pass.",
    )
    parser.add_argument(
        "--per-yacht-skip",
        type=int,
        default=0,
        help="Skip first N images per yacht (e.g. 5 after hero pass).",
    )
    parser.add_argument(
        "--phase",
        choices=("heroes", "rest", "all"),
        default="all",
        help="heroes = first 5/yacht; rest = after first 5; all = every image (default)",
    )
    parser.add_argument(
        "--shard",
        default="0/1",
        help="i/n: only process yacht folders assigned to worker i of n (for parallel workers)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    shard_i, shard_n = (int(x) for x in args.shard.split("/"))

    if args.phase == "heroes":
        args.per_yacht_max = 5
        args.per_yacht_skip = 0
    elif args.phase == "rest":
        if args.per_yacht_skip == 0:
            args.per_yacht_skip = 5
        args.per_yacht_max = 0

    if not args.weights.is_file():
        print(f"Missing YOLO weights: {args.weights}", file=sys.stderr)
        raise SystemExit(1)
    if not args.input.is_dir():
        print(f"Input not found: {args.input}", file=sys.stderr)
        raise SystemExit(1)

    args.output.mkdir(parents=True, exist_ok=True)
    log_path = args.output / ".processing_log.txt"
    done = load_processed(log_path)

    per_max = args.per_yacht_max if args.per_yacht_max > 0 else None
    work = discover_yacht_images(
        args.input,
        yacht_filter=args.yacht,
        per_yacht_max=per_max,
        per_yacht_skip=args.per_yacht_skip,
    )
    work = [(src, rel) for src, rel in work if rel.as_posix() not in done]
    if shard_n > 1:
        work = [
            (src, rel) for src, rel in work
            if zlib.crc32(rel.parts[0].encode()) % shard_n == shard_i
        ]
    if args.limit > 0:
        work = work[: args.limit]

    if not work:
        print("Nothing to process (all done or no images found).")
        return

    print(f"Output root: {args.output}")
    print(f"Page URL pattern: /yachts/{{folder}}/images/{{file}}")
    phase_note = args.phase if args.phase != "all" else "all images"
    cap = f", max {args.per_yacht_max}/yacht" if per_max else ""
    skip = f", skip {args.per_yacht_skip}/yacht" if args.per_yacht_skip else ""
    print(f"Phase: {phase_note}{cap}{skip}")
    print(f"Pending images: {len(work)}  conf={args.conf}  dilate={args.dilate}")

    if args.dry_run:
        for _, rel in work[:10]:
            print(f"  would write {args.output / rel}")
        if len(work) > 10:
            print(f"  ... and {len(work) - 10} more")
        return

    device = pick_device()
    print(f"Device: {device}", flush=True)
    print("Loading YOLO weights and LaMa inpainter…", flush=True)
    yolo, lama = load_models(args.weights, device)
    print("Models ready. Processing images…", flush=True)

    inpainted = 0
    detections = 0
    t0 = time.perf_counter()
    progress_path = args.output / ".processing_progress.log"
    trident_fail = args.output / ".trident_quarantine.txt"

    worker = f"[w{shard_i}] " if shard_n > 1 else ""

    def log_progress(msg: str) -> None:
        msg = worker + msg
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n"
        with progress_path.open("a", encoding="utf-8") as pf:
            pf.write(line)
            pf.flush()
        print(msg, flush=True)

    log_progress(f"session start pending={len(work)} device={device}")

    with log_path.open("a", encoding="utf-8") as log_file:
        for i, (src, rel) in enumerate(work, 1):
            out_path = args.output / rel
            img_bgr = cv2.imread(str(src), cv2.IMREAD_COLOR)
            if img_bgr is None:
                print(f"[{i}/{len(work)}] skip unreadable {rel}", file=sys.stderr)
                continue

            t1 = time.perf_counter()
            tmp_path = out_path.with_name(f".{out_path.stem}.tmp{out_path.suffix}")
            try:
                result, n_det, logo_after = remove_watermark(
                    img_bgr, yolo=yolo, lama=lama, conf=args.conf, dilate=args.dilate
                )
                if logo_after >= PUBLISH_MAX_LOGO_SCORE:
                    with trident_fail.open("a", encoding="utf-8") as qf:
                        qf.write(f"{rel.as_posix()}\t{logo_after:.3f}\n")
                    log_progress(
                        f"[{i}/{len(work)}] QUARANTINE {rel.as_posix()}: trident score {logo_after:.2f}"
                    )
                    continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(tmp_path), result):
                    raise OSError("cv2.imwrite returned False")
                os.replace(tmp_path, out_path)
            except Exception as exc:
                tmp_path.unlink(missing_ok=True)
                log_progress(f"[{i}/{len(work)}] ERROR {rel.as_posix()}: {exc} (retry next pass)")
                continue
            elapsed = time.perf_counter() - t1
            detections += n_det
            if n_det > 0:
                inpainted += 1

            log_file.write(f"{rel.as_posix()}\n")
            log_file.flush()

            status = "inpaint" if n_det else "copy"
            log_progress(
                f"[{i}/{len(work)}] {rel.as_posix()}: {n_det} det, {status}, logo={logo_after:.2f}, {elapsed:.1f}s"
            )

    total = time.perf_counter() - t0
    log_progress(
        f"session done in {total:.1f}s — {len(work)} images, "
        f"{inpainted} inpainted, {detections} detections"
    )
    log_progress(f"clean root: {args.output.resolve()}")


if __name__ == "__main__":
    main()
