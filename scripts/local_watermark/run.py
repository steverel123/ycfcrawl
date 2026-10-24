#!/usr/bin/env python3
"""Local watermark pipeline: inventory → pilot → batch → report."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from batch import run_batch  # noqa: E402
from common import DEFAULT_WEIGHTS, run_inventory  # noqa: E402
from pilot import run_pilot  # noqa: E402
from report import write_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Local YOLO+LaMa watermark removal")
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=ROOT / "yachts",
        help="Original images (default: yachts/)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=ROOT / "public" / "yachts",
        help="Cleaned output (default: public/yachts/)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("inventory", help="Scan input folder")
    p_pilot = sub.add_parser("pilot", help="Run style pilot and comparisons")
    p_pilot.add_argument(
        "--pilot-dir",
        type=Path,
        default=ROOT / "local_watermark_pilot",
    )
    p_batch = sub.add_parser("batch", help="Resumable full batch")
    p_batch.add_argument("--limit", type=int, default=0)
    p_batch.add_argument("--conf", type=float, default=0.08)
    p_batch.add_argument("--dilate", type=int, default=35)
    p_rep = sub.add_parser("report", help="Write verification report")
    p_rep.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "local_watermark_report",
    )
    p_rep.add_argument(
        "--pilot-dir",
        type=Path,
        default=ROOT / "local_watermark_pilot",
    )
    sub.add_parser("all-pilot", help="inventory + pilot only")

    args = parser.parse_args()

    if args.cmd == "inventory":
        inv = run_inventory(args.input)
        print(inv.to_json())
        return

    if args.cmd == "pilot":
        if not DEFAULT_WEIGHTS.is_file():
            raise SystemExit(f"Missing weights: {DEFAULT_WEIGHTS}")
        summary = run_pilot(args.input, args.pilot_dir)
        print("Pilot summary:", summary["method_recommended"], summary["yolo_detect_rate"])
        return

    if args.cmd == "batch":
        if not DEFAULT_WEIGHTS.is_file():
            raise SystemExit(f"Missing weights: {DEFAULT_WEIGHTS}")
        run_batch(
            args.input,
            args.output,
            conf=args.conf,
            dilate=args.dilate,
            limit=args.limit,
        )
        return

    if args.cmd == "report":
        path = write_report(
            args.input,
            args.output,
            args.report_dir,
            method="YOLO11 detector (yolo11x-train28-best, conf 0.08, mask dilation 35 px) + LaMa inpainting via simple-lama-inpainting, on Apple MPS",
            pilot_dir=args.pilot_dir,
        )
        print("Report:", path)
        return

    if args.cmd == "all-pilot":
        print(run_inventory(args.input).to_json())
        if not DEFAULT_WEIGHTS.is_file():
            raise SystemExit(f"Missing weights: {DEFAULT_WEIGHTS}")
        run_pilot(args.input, ROOT / "local_watermark_pilot")
        return


if __name__ == "__main__":
    main()
