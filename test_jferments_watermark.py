#!/usr/bin/env python3
"""
Ad-hoc batch tester. Production output uses remove_watermarks.py → public/yachts/.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from simple_lama_inpainting import SimpleLama
from ultralytics import YOLO

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def pick_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda:0")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def find_images(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(path)
    if recursive:
        files = [
            p
            for p in path.rglob("*")
            if p.is_file() and p.suffix.lower() in IMG_EXTS
        ]
    else:
        files = [
            p
            for p in path.iterdir()
            if p.is_file() and p.suffix.lower() in IMG_EXTS
        ]
    return sorted(files)


def load_models(weights: Path, device: torch.device) -> tuple[YOLO, SimpleLama]:
    yolo = YOLO(str(weights))
    yolo.to(device)
    lama = SimpleLama(device=device)
    return yolo, lama


def process_image(
    image_path: Path,
    output_path: Path,
    *,
    yolo: YOLO,
    lama: SimpleLama,
    conf: float,
    dilate: int,
    debug_dir: Path | None,
) -> dict:
    img_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise FileNotFoundError(image_path)

    t0 = time.perf_counter()
    predictions = yolo(img_bgr, conf=conf, verbose=False)[0]
    boxes = predictions.boxes.xyxy.cpu().numpy() if len(predictions.boxes.xyxy) else []

    mask = np.zeros(img_bgr.shape[:2], dtype=np.uint8)
    for box in boxes:
        x1, y1, x2, y2 = map(int, box)
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, thickness=-1)

    if dilate > 0 and mask.any():
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate))
        mask = cv2.dilate(mask, kernel, iterations=1)

    stem = image_path.stem
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(debug_dir / f"{stem}_mask_raw.png"), mask)
        preview = np.zeros_like(img_bgr)
        preview[mask == 255] = [0, 0, 255]
        cv2.imwrite(
            str(debug_dir / f"{stem}_mask_preview.png"),
            cv2.addWeighted(img_bgr, 0.7, preview, 0.3, 0),
        )

    if mask.any():
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        result_rgb = np.array(lama(Image.fromarray(rgb), Image.fromarray(mask)))
        result_bgr = cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)
        inpainted = True
    else:
        result_bgr = img_bgr
        inpainted = False

    elapsed = time.perf_counter() - t0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), result_bgr)

    return {
        "detections": len(boxes),
        "inpainted": inpainted,
        "seconds": round(elapsed, 2),
    }


def main() -> None:
    root = Path(__file__).resolve().parent
    default_weights = root / "jferments-watermark-remover" / "yolo11x-train28-best.pt"

    parser = argparse.ArgumentParser(description="YOLO + LaMa watermark removal (Mac-friendly)")
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        default=root / "watermark-test/jferments-input",
        help="Image file or folder",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=root / "watermark-test/output/jferments",
        help="Output file (single image) or output directory (batch)",
    )
    parser.add_argument("-w", "--weights", type=Path, default=default_weights)
    parser.add_argument("--conf", type=float, default=0.08, help="Lower = more detections")
    parser.add_argument("--dilate", type=int, default=18)
    parser.add_argument("-R", "--recursive", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max images to process (0 = all)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if not args.weights.is_file():
        print(f"Missing weights: {args.weights}", file=sys.stderr)
        raise SystemExit(1)

    images = find_images(args.input, args.recursive)
    if args.limit > 0:
        images = images[: args.limit]
    if not images:
        print(f"No images found under {args.input}", file=sys.stderr)
        raise SystemExit(1)

    batch = len(images) > 1 or args.input.is_dir()
    device = pick_device()
    print(f"Device: {device}")
    print(f"Images: {len(images)}  conf={args.conf}  dilate={args.dilate}")

    yolo, lama = load_models(args.weights, device)
    debug_dir = args.output / "debug" if args.debug and batch else (
        args.output.parent / "jferments_debug" if args.debug else None
    )

    total_det = 0
    total_inpaint = 0
    t_start = time.perf_counter()

    for i, image_path in enumerate(images, 1):
        if batch:
            if args.input.is_dir():
                rel = image_path.relative_to(args.input)
                out_path = args.output / rel
            else:
                out_path = args.output / image_path.name
        else:
            out_path = args.output

        try:
            stats = process_image(
                image_path,
                out_path,
                yolo=yolo,
                lama=lama,
                conf=args.conf,
                dilate=args.dilate,
                debug_dir=debug_dir,
            )
        except Exception as exc:
            print(f"[{i}/{len(images)}] FAIL {image_path.name}: {exc}", file=sys.stderr)
            continue

        total_det += stats["detections"]
        total_inpaint += int(stats["inpainted"])
        flag = "inpaint" if stats["inpainted"] else "no-wm"
        print(
            f"[{i}/{len(images)}] {image_path.name}: "
            f"{stats['detections']} det, {flag}, {stats['seconds']}s -> {out_path.name}"
        )

    elapsed = time.perf_counter() - t_start
    print(
        f"Done: {len(images)} images in {elapsed:.1f}s, "
        f"{total_inpaint} inpainted, {total_det} total detections"
    )


if __name__ == "__main__":
    main()
