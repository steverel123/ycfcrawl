"""Shared YCF trident / logo detection (template match, bottom-left)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_COMMON = _ROOT / "scripts" / "local_watermark" / "common.py"

_spec = importlib.util.spec_from_file_location("wm_common", _COMMON)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

find_ycf_logo = _mod.find_ycf_logo
build_ycf_geom_mask = _mod.build_ycf_geom_mask
LOGO_MATCH_THRESHOLD = _mod.LOGO_MATCH_THRESHOLD

# Stricter than batch inpaint review; used before publishing to R2.
PUBLISH_MAX_LOGO_SCORE = 0.16
