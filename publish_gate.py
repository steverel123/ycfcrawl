"""Second-pass QA: every processed image is checked again before publish."""
from __future__ import annotations

import os

import cv2
import numpy as np

from trident_logo import PUBLISH_MAX_LOGO_SCORE, find_ycf_logo


def publish_max_score() -> float:
    raw = os.environ.get("PUBLISH_MAX_LOGO_SCORE", "")
    return float(raw) if raw else PUBLISH_MAX_LOGO_SCORE


def logo_score_bgr(img_bgr: np.ndarray) -> float:
    return float(find_ycf_logo(img_bgr)[0])


def passes_publish_gate(img_bgr: np.ndarray, *, max_score: float | None = None) -> tuple[bool, float]:
    max_score = publish_max_score() if max_score is None else max_score
    score = logo_score_bgr(img_bgr)
    return score < max_score, score


def passes_publish_gate_bytes(data: bytes, *, max_score: float | None = None) -> tuple[bool, float]:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return False, 1.0
    return passes_publish_gate(img, max_score=max_score)


def passes_publish_gate_file(path: str, *, max_score: float | None = None) -> tuple[bool, float]:
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        return False, 1.0
    return passes_publish_gate(img, max_score=max_score)
