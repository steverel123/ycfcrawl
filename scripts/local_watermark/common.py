"""Shared helpers for local watermark removal on Mac."""
from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
import torch
from PIL import Image, JpegImagePlugin
from simple_lama_inpainting import SimpleLama
from ultralytics import YOLO

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
RAW_EXTS = {".raw", ".cr2", ".nef", ".arw", ".dng", ".orf", ".rw2"}
ANIM_EXTS = {".gif", ".webp"}  # webp may be animated; flagged in inventory

# In-progress writes; sync_down.sh excludes this prefix.
TMP_PREFIX = ".wm-tmp-"

DEFAULT_WEIGHTS = (
    Path(__file__).resolve().parents[2]
    / "jferments-watermark-remover"
    / "yolo11x-train28-best.pt"
)


def pick_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda:0")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def discover_images(input_root: Path) -> list[tuple[Path, Path]]:
    """(absolute_path, rel_path) under input_root."""
    pairs: list[tuple[Path, Path]] = []
    for image_path in sorted(input_root.rglob("*")):
        if not image_path.is_file() or image_path.suffix.lower() not in IMG_EXTS:
            continue
        rel = image_path.relative_to(input_root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        pairs.append((image_path, rel))
    return pairs


def build_ycf_geom_mask(image_bgr: np.ndarray, *, fallback: bool = True) -> np.ndarray:
    """Bottom-left YachtCharterFleet-style logo (geometry + color heuristic)."""
    h, w = image_bgr.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    y0 = int(h * 0.84)
    x1 = int(w * 0.40)
    region = image_bgr[y0:, :x1]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    bright = gray >= 168
    low_sat = hsv[:, :, 1] <= 85
    dark_water = gray < 200
    local = bright & low_sat & dark_water
    sub = np.zeros((region.shape[0], region.shape[1]), np.uint8)
    sub[local] = 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    sub = cv2.dilate(sub, kernel, iterations=2)
    sub = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, kernel, iterations=2)
    if cv2.countNonZero(sub) < 250:
        sub[:] = 0
        if fallback:
            rh, rw = region.shape[:2]
            sub[int(rh * 0.05) :, : int(rw * 0.95)] = 255
    mask[y0:, :x1] = sub
    return mask


def yolo_mask(
    img_bgr: np.ndarray,
    yolo: YOLO,
    *,
    conf: float,
    dilate: int,
) -> tuple[np.ndarray, int]:
    predictions = yolo(img_bgr, conf=conf, verbose=False)[0]
    boxes = predictions.boxes.xyxy.cpu().numpy() if len(predictions.boxes.xyxy) else []
    h, w = img_bgr.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for box in boxes:
        x1, y1, x2, y2 = map(int, box)
        bw, bh = x2 - x1, y2 - y1
        # The detector often boxes only the YachtCharterFleet wordmark; the trident
        # icon sits immediately to its left and slightly above.
        if bh > 0 and bw / bh > 4 and x1 < w * 0.45:
            x1 = max(0, x1 - int(bw * 0.22))
            y1 = max(0, y1 - int(bh * 0.35))
            y2 = min(h, y2 + int(bh * 0.15))
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, thickness=-1)
    if dilate > 0 and mask.any():
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate, dilate))
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask, len(boxes)


LOGO_TEMPLATE = Path(__file__).resolve().parent / "ycf_logo_template.png"
LOGO_MATCH_THRESHOLD = 0.2
_logo_edges: np.ndarray | None = None


def find_ycf_logo(img_bgr: np.ndarray) -> tuple[float, tuple[int, int, int, int] | None]:
    """Edge template match for the YachtCharterFleet logo in the bottom-left.

    Template was cut from a 1200 px wide image; returns (score, x1, y1, x2, y2).
    """
    global _logo_edges
    if _logo_edges is None:
        _logo_edges = cv2.imread(str(LOGO_TEMPLATE), cv2.IMREAD_GRAYSCALE)
    h, w = img_bgr.shape[:2]
    oy = int(h * 0.6)
    region = cv2.Canny(cv2.cvtColor(img_bgr[oy:, : int(w * 0.55)], cv2.COLOR_BGR2GRAY), 60, 160)
    best: tuple[float, tuple[int, int, int, int] | None] = (0.0, None)
    for s in np.linspace(0.75, 1.15, 9) * (w / 1200):
        t = cv2.resize(_logo_edges, None, fx=s, fy=s)
        if t.shape[0] >= region.shape[0] or t.shape[1] >= region.shape[1]:
            continue
        r = cv2.matchTemplate(region, cv2.Canny(t, 60, 160), cv2.TM_CCOEFF_NORMED)
        _, mx, _, (x, y) = cv2.minMaxLoc(r)
        if mx > best[0]:
            best = (float(mx), (x, oy + y, x + t.shape[1], oy + y + t.shape[0]))
    return best


def inpaint_lama(img_bgr: np.ndarray, mask: np.ndarray, lama: SimpleLama) -> np.ndarray:
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w = img_bgr.shape[:2]
    # SimpleLama pads to a multiple of 8 and returns the padded size.
    result_rgb = np.array(lama(Image.fromarray(rgb), Image.fromarray(mask)))[:h, :w]
    return cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)


def mask_preview(img_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    overlay = np.zeros_like(img_bgr)
    overlay[mask == 255] = (0, 0, 255)
    return cv2.addWeighted(img_bgr, 0.72, overlay, 0.28, 0)


def load_models(weights: Path, device: torch.device) -> tuple[YOLO, SimpleLama]:
    yolo = YOLO(str(weights))
    yolo.to(device)
    return yolo, SimpleLama(device=device)


def write_jpeg_preserve(
    out_path: Path,
    result_bgr: np.ndarray,
    src_path: Path,
) -> None:
    """Write JPEG with the source's quantization tables, subsampling, EXIF, and ICC."""
    rgb = cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    save_kw: dict = {"quality": 95, "subsampling": 0}
    with Image.open(src_path) as src:
        if src.format == "JPEG" and getattr(src, "quantization", None):
            save_kw = {
                "qtables": src.quantization,
                "subsampling": JpegImagePlugin.get_sampling(src),
            }
        if src.info.get("exif"):
            save_kw["exif"] = src.info["exif"]
        if src.info.get("icc_profile"):
            save_kw["icc_profile"] = src.info["icc_profile"]
    img.save(out_path, format="JPEG", **save_kw)


def read_image_raw(path: Path) -> np.ndarray | None:
    """Stored pixel orientation; EXIF orientation tag is carried over on write."""
    return cv2.imread(str(path), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)


def atomic_write_image(
    out_path: Path,
    result_bgr: np.ndarray,
    src_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = out_path.suffix.lower()
    tmp_root = Path(os.environ.get("WM_TMPDIR", "/tmp"))
    tmp_root.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, suffix=suffix, dir=tmp_root)
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        if suffix in {".jpg", ".jpeg"}:
            write_jpeg_preserve(tmp_path, result_bgr, src_path)
        else:
            cv2.imwrite(str(tmp_path), result_bgr)
        check = read_image_raw(tmp_path)
        if check is None:
            raise RuntimeError("output not decodable")
        if check.shape != result_bgr.shape:
            raise RuntimeError(f"dimension mismatch {check.shape} vs {result_bgr.shape}")
        # Network volumes (RunPod) often reject rename-in-place; copy then remove temp.
        shutil.copy2(tmp_path, out_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def sample_pilot_images(
    pairs: list[tuple[Path, Path]],
    *,
    n_yachts: int = 12,
    per_yacht: int = 2,
    seed: int = 42,
) -> list[tuple[Path, Path]]:
    by_yacht: dict[str, list[tuple[Path, Path]]] = defaultdict(list)
    for src, rel in pairs:
        by_yacht[rel.parts[0]].append((src, rel))
    rng = random.Random(seed)
    yacht_names = sorted(by_yacht.keys())
    rng.shuffle(yacht_names)
    chosen: list[tuple[Path, Path]] = []
    for name in yacht_names[:n_yachts]:
        bucket = by_yacht[name]
        rng.shuffle(bucket)
        chosen.extend(bucket[:per_yacht])
    return chosen


@dataclass
class InventoryReport:
    image_count: int
    total_bytes: int
    formats: dict[str, int]
    dimensions: dict[str, int]
    duplicate_names: int
    raw_files: int
    animated_candidates: int
    yacht_folders: int
    sample_paths: list[str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def run_inventory(input_root: Path, sample_n: int = 8) -> InventoryReport:
    formats: Counter[str] = Counter()
    dims: Counter[str] = Counter()
    names: Counter[str] = Counter()
    raw_files = 0
    anim = 0
    total_bytes = 0
    count = 0
    samples: list[str] = []

    folders: set[Path] = set()
    for p in sorted(input_root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext in RAW_EXTS:
            raw_files += 1
            continue
        if ext not in IMG_EXTS:
            continue
        if ext in ANIM_EXTS:
            anim += 1
        count += 1
        total_bytes += p.stat().st_size
        formats[ext] += 1
        names[p.name] += 1
        folders.add(p.parent)
        try:
            with Image.open(p) as im:
                dims[f"{im.width}x{im.height}"] += 1
        except Exception:
            dims["unreadable"] += 1
        if len(samples) < sample_n:
            samples.append(str(p.relative_to(input_root)))

    yacht_folders = len(folders)
    dup = sum(1 for c in names.values() if c > 1)

    return InventoryReport(
        image_count=count,
        total_bytes=total_bytes,
        formats=dict(formats),
        dimensions=dict(dims),
        duplicate_names=dup,
        raw_files=raw_files,
        animated_candidates=anim,
        yacht_folders=yacht_folders,
        sample_paths=samples,
    )
