"""Rename flagged clean images to <name>__trident.jpg (or undo with --undo).

  tag_trident.py <ycfcrawl> <full.csv> <manifest.csv>          rename + write manifest
  tag_trident.py <ycfcrawl> <full.csv> <manifest.csv> --undo   restore from manifest
"""
import csv
import os
import sys

YCF, REPORT, MANIFEST = sys.argv[1:4]
UNDO = "--undo" in sys.argv
TAG = "__trident"

if UNDO:
    restored = 0
    with open(MANIFEST) as fh:
        for row in csv.DictReader(fh):
            if os.path.exists(row["new"]) and not os.path.exists(row["old"]):
                os.rename(row["new"], row["old"])
                restored += 1
    print(f"restored {restored}")
    sys.exit(0)

renamed = skipped = 0
with open(REPORT) as fh, open(MANIFEST, "w", newline="") as out:
    wr = csv.writer(out)
    wr.writerow(["old", "new"])
    for row in csv.DictReader(fh):
        if row["status"] != "trident":
            continue
        stem, ext = os.path.splitext(row["file"])
        old = f"{YCF}/clean-yachts/{row['slug']}/{row['file']}"
        new = f"{YCF}/clean-yachts/{row['slug']}/{stem}{TAG}{ext}"
        if not os.path.exists(old) or os.path.exists(new):
            skipped += 1
            continue
        os.rename(old, new)
        wr.writerow([old, new])
        renamed += 1
print(f"renamed {renamed}, skipped {skipped}")
