"""Pilot: compare YOLO+LaMa vs geometry mask + LaMa (IOPaint uses the same LaMa family)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

from common import (
    DEFAULT_WEIGHTS,
    build_ycf_geom_mask,
    discover_images,
    inpaint_lama,
    load_models,
    mask_preview,
    pick_device,
    sample_pilot_images,
    yolo_mask,
)


def run_pilot(
    input_root: Path,
    pilot_dir: Path,
    *,
    weights: Path = DEFAULT_WEIGHTS,
    conf: float = 0.08,
    dilate: int = 35,
) -> dict:
    pilot_dir.mkdir(parents=True, exist_ok=True)
    pairs = discover_images(input_root)
    if not pairs:
        raise SystemExit(f"No images under {input_root}")

    sample = pairs if len(pairs) <= 40 else sample_pilot_images(pairs, n_yachts=14, per_yacht=2)
    device = pick_device()
    print(f"Pilot device: {device}, samples: {len(sample)}")
    yolo, lama = load_models(weights, device)

    results = []
    for src, rel in sample:
        img = cv2.imread(str(src), cv2.IMREAD_COLOR)
        if img is None:
            continue
        stem = rel.as_posix().replace("/", "__")
        cv2.imwrite(str(pilot_dir / f"{stem}_before.jpg"), img)

        y_mask, n_det = yolo_mask(img, yolo, conf=conf, dilate=dilate)
        g_mask = build_ycf_geom_mask(img)

        cv2.imwrite(str(pilot_dir / f"{stem}_mask_yolo.png"), y_mask)
        cv2.imwrite(str(pilot_dir / f"{stem}_mask_geom.png"), g_mask)
        cv2.imwrite(str(pilot_dir / f"{stem}_preview_yolo.jpg"), mask_preview(img, y_mask))
        cv2.imwrite(str(pilot_dir / f"{stem}_preview_geom.jpg"), mask_preview(img, g_mask))

        y_out = inpaint_lama(img, y_mask, lama) if y_mask.any() else img.copy()
        g_out = inpaint_lama(img, g_mask, lama) if g_mask.any() else img.copy()
        cv2.imwrite(str(pilot_dir / f"{stem}_after_yolo.jpg"), y_out)
        cv2.imwrite(str(pilot_dir / f"{stem}_after_geom.jpg"), g_out)

        # Side-by-side contact strip (before | yolo | geom)
        h, w = img.shape[:2]
        strip = np.zeros((h, w * 3, 3), dtype=np.uint8)
        strip[:, :w] = img
        strip[:, w : 2 * w] = y_out
        strip[:, 2 * w :] = g_out
        cv2.imwrite(str(pilot_dir / f"{stem}_compare.jpg"), strip)

        y_frac = float(cv2.countNonZero(y_mask)) / (h * w)
        g_frac = float(cv2.countNonZero(g_mask)) / (h * w)
        results.append(
            {
                "rel": rel.as_posix(),
                "yolo_detections": n_det,
                "yolo_mask_frac": round(y_frac, 5),
                "geom_mask_frac": round(g_frac, 5),
            }
        )
        print(f"  {rel.as_posix()}: yolo det={n_det}, mask frac y={y_frac:.4f} g={g_frac:.4f}")

    # Heuristic recommendation
    yolo_hits = sum(1 for r in results if r["yolo_detections"] > 0)
    summary = {
        "method_recommended": "yolo_lama",
        "reason": (
            "Custom YOLO finds variable watermark positions; geometry mask is fixed "
            "bottom-left only. Both use Simple LaMa (same model class as IOPaint LaMa)."
        ),
        "samples": len(results),
        "yolo_detect_rate": round(yolo_hits / max(len(results), 1), 3),
        "conf": conf,
        "dilate": dilate,
        "per_image": results,
    }
    (pilot_dir / "pilot_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    inp = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "yachts"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "local_watermark_pilot"
    run_pilot(inp, out)
