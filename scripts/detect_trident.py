"""Flag cleaned images where the YCF trident survived.

For each clean-yachts/<slug>/<n>.jpg, locate the trident in the raw original
(yachts/<slug>/images/<n>.jpg) by multi-scale template matching in the
bottom-left corner, then measure how much of the trident's contrast against
its surroundings is still present in the cleaned image.

  residual = (clean_on_mask - clean_ring) / (raw_on_mask - raw_ring)

~0 means erased, ~1 means untouched. Writes a CSV of every image.
"""
import csv
import os
import sys
from multiprocessing import Pool

import cv2
import numpy as np

YCF, MASK_PATH, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
SAMPLE = int(sys.argv[4]) if len(sys.argv) > 4 else 0

BASE = cv2.imread(MASK_PATH, cv2.IMREAD_GRAYSCALE)  # 4x upscaled binary mask
BASE = cv2.resize(BASE, (BASE.shape[1] // 4, BASE.shape[0] // 4), interpolation=cv2.INTER_AREA)
BASE_W = 1200  # template was cut from a 1200px-wide frame


def scaled_mask(scale):
    w = max(8, round(BASE.shape[1] * scale))
    h = max(14, round(BASE.shape[0] * scale))
    return (cv2.resize(BASE, (w, h), interpolation=cv2.INTER_AREA) > 127).astype(np.uint8)


def locate(raw_gray):
    h, w = raw_gray.shape
    region = raw_gray[int(h * 0.6):, : int(w * 0.25)]
    best = (-1, None, None)
    expected = w / BASE_W
    for f in (0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4):
        m = scaled_mask(expected * f)
        if m.shape[0] >= region.shape[0] or m.shape[1] >= region.shape[1]:
            continue
        res = cv2.matchTemplate(region, (m * 255).astype(np.uint8), cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score > best[0]:
            best = (score, (loc[0], loc[1] + int(h * 0.6)), m)
    return best


def contrast(gray, x, y, m):
    h, w = m.shape
    patch = gray[y : y + h, x : x + w].astype(np.float32)
    ring = cv2.dilate(m, np.ones((5, 5), np.uint8)) - m
    return patch[m > 0].mean() - patch[ring > 0].mean()


def check(job):
    slug, name = job
    raw_p = f"{YCF}/yachts/{slug}/images/{name}"
    clean_p = f"{YCF}/clean-yachts/{slug}/{name}"
    raw = cv2.imread(raw_p, cv2.IMREAD_GRAYSCALE)
    clean = cv2.imread(clean_p, cv2.IMREAD_GRAYSCALE)
    if raw is None or clean is None:
        return (slug, name, "", "", "", "unreadable")
    # LaMa pads to a multiple of 8; padding sits on the bottom/right edge.
    dh, dw = clean.shape[0] - raw.shape[0], clean.shape[1] - raw.shape[1]
    if not (0 <= dh < 16 and 0 <= dw < 16):
        return (slug, name, "", "", "", "resized")
    clean = clean[: raw.shape[0], : raw.shape[1]]
    score, loc, m = locate(raw)
    if loc is None or score < 0.35:
        return (slug, name, f"{score:.3f}", "", "", "no_trident_in_raw")
    x, y = loc
    rc = contrast(raw, x, y, m)
    if rc < 12:
        return (slug, name, f"{score:.3f}", f"{rc:.1f}", "", "faint_in_raw")
    cc = contrast(clean, x, y, m)
    residual = cc / rc
    remnant = remnant_px(raw, clean, x, y, m)
    status = "trident" if residual > 0.1 or remnant >= REMNANT_MIN else "clean"
    return (slug, name, f"{score:.3f}", f"{rc:.1f}", f"{residual:.3f}", status, remnant)


REMNANT_MIN = int(os.environ.get("REMNANT_MIN", "6"))


def remnant_px(raw, clean, x, y, m):
    """Mask pixels where the clean image keeps over half the raw mark's local lift."""
    h, w = m.shape
    rp = raw[y : y + h, x : x + w].astype(np.float32)
    cp = clean[y : y + h, x : x + w].astype(np.float32)
    bg = cv2.medianBlur(cp.astype(np.uint8), 9).astype(np.float32)
    lift_raw = rp - bg
    lift_clean = cp - bg
    hit = (m > 0) & (lift_raw > 25) & (lift_clean > 0.5 * lift_raw) & (lift_clean > 12)
    return int(hit.sum())


def jobs():
    root = f"{YCF}/clean-yachts"
    for slug in sorted(os.listdir(root)):
        d = f"{root}/{slug}"
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.lower().endswith(".jpg"):
                yield slug, name


if __name__ == "__main__":
    all_jobs = list(jobs())
    if SAMPLE:
        all_jobs = all_jobs[:: max(1, len(all_jobs) // SAMPLE)]
    with Pool() as pool, open(OUT, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["slug", "file", "match", "raw_contrast", "residual", "status", "remnant_px"])
        for row in pool.imap_unordered(check, all_jobs, chunksize=32):
            wr.writerow(row)
