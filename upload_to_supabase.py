#!/usr/bin/env python3
"""
Upload yacht metadata + images to Supabase.

- Postgres: yachts, yacht_images (see supabase/schema.sql)
- Storage: public bucket (default yacht-images)

Prefers cleaned images from public/yachts/ when present, else yachts/.

Env (.env or shell):
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
  SUPABASE_STORAGE_BUCKET  (optional, default yacht-images)
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

from yacht_paths import (
    PUBLIC_YACHTS_ROOT,
    YACHTS_SOURCE_ROOT,
    yacht_folder_from_metadata,
)

CHECKPOINT = Path(".supabase_upload_log.txt")
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def get_client() -> tuple[Client, str]:
    load_dotenv()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "yacht-images").strip()
    if not url or not key:
        print(
            "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return create_client(url, key), bucket


def load_checkpoint() -> set[str]:
    if not CHECKPOINT.is_file():
        return set()
    return {
        line.strip()
        for line in CHECKPOINT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def public_storage_url(supabase_url: str, bucket: str, storage_path: str) -> str:
    base = supabase_url.rstrip("/")
    return f"{base}/storage/v1/object/public/{bucket}/{storage_path}"


def yacht_row(meta: dict, folder: str) -> dict:
    return {
        "id": str(meta["id"]),
        "slug": meta["slug"],
        "folder_name": folder,
        "name": meta["name"],
        "source_url": meta.get("url"),
        "length": meta.get("length"),
        "builder": meta.get("builder"),
        "year_built": meta.get("year_built"),
        "guests": meta.get("guests"),
        "cabins": meta.get("cabins"),
        "price": meta.get("price"),
        "specifications": meta.get("specifications"),
        "specs_normalized": meta.get("specs_normalized"),
        "amenities": meta.get("amenities") or [],
        "toys": meta.get("toys"),
        "description": meta.get("description"),
        "description_sections": meta.get("description_sections"),
        "meta_description": meta.get("meta_description"),
        "metadata": meta,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def pick_image_path(folder: str, file_rel: str) -> tuple[Path, bool] | None:
    clean = PUBLIC_YACHTS_ROOT / folder / file_rel
    raw = YACHTS_SOURCE_ROOT / folder / file_rel
    if clean.is_file():
        return clean, True
    if raw.is_file():
        return raw, False
    return None


def upsert_yacht(client: Client, row: dict) -> None:
    client.table("yachts").upsert(row, on_conflict="id").execute()


def upsert_image_row(client: Client, row: dict) -> None:
    client.table("yacht_images").upsert(row, on_conflict="storage_path").execute()


def upload_file(
    client: Client,
    bucket: str,
    storage_path: str,
    local_path: Path,
) -> None:
    content_type = mimetypes.guess_type(local_path.name)[0] or "image/jpeg"
    data = local_path.read_bytes()
    client.storage.from_(bucket).upload(
        storage_path,
        data,
        file_options={"content-type": content_type, "upsert": "true"},
    )


def discover_yacht_dirs(yachts_root: Path, yacht_filter: str | None) -> list[Path]:
    dirs = []
    for path in sorted(yachts_root.iterdir()):
        if not path.is_dir():
            continue
        if yacht_filter and path.name != yacht_filter:
            continue
        if (path / "metadata.json").is_file():
            dirs.append(path)
    return dirs


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload yachts to Supabase")
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=YACHTS_SOURCE_ROOT,
        help="yachts/ root with metadata.json per folder",
    )
    parser.add_argument("--yacht", help="Single folder e.g. defiance-47450")
    parser.add_argument("--limit", type=int, default=0, help="Max yachts (0=all)")
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Upsert yacht rows only, skip Storage uploads",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client, bucket = get_client()
    supabase_url = os.environ["SUPABASE_URL"].strip()
    done = load_checkpoint()
    yacht_dirs = discover_yacht_dirs(args.input, args.yacht)
    if args.limit > 0:
        yacht_dirs = yacht_dirs[: args.limit]

    if not yacht_dirs:
        print("No yacht folders found.")
        return

    print(f"Yachts: {len(yacht_dirs)}  bucket: {bucket}  checkpoint skips: {len(done)}")

    uploaded = 0
    skipped = 0
    errors = 0

    with CHECKPOINT.open("a", encoding="utf-8") as log:
        for yi, yacht_dir in enumerate(yacht_dirs, 1):
            meta_path = yacht_dir / "metadata.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            folder = yacht_folder_from_metadata(meta)
            yacht_id = str(meta["id"])

            row = yacht_row(meta, folder)
            if args.dry_run:
                print(f"[{yi}] would upsert yacht {folder}")
            else:
                upsert_yacht(client, row)

            images = meta.get("downloaded_images") or []
            for sort_order, img in enumerate(images, 1):
                file_rel = img["file"]
                storage_path = f"{folder}/{file_rel}"
                if storage_path in done:
                    skipped += 1
                    continue

                picked = pick_image_path(folder, file_rel)
                if picked is None:
                    print(f"  missing file {storage_path}", file=sys.stderr)
                    errors += 1
                    continue

                local_path, cleaned = picked
                public_url = public_storage_url(supabase_url, bucket, storage_path)

                if args.dry_run:
                    print(f"  would upload {storage_path} ({'clean' if cleaned else 'raw'})")
                    continue

                if not args.metadata_only:
                    try:
                        upload_file(client, bucket, storage_path, local_path)
                    except Exception as exc:
                        print(f"  upload failed {storage_path}: {exc}", file=sys.stderr)
                        errors += 1
                        continue

                image_row = {
                    "yacht_id": yacht_id,
                    "sort_order": sort_order,
                    "file_name": file_rel,
                    "storage_path": storage_path,
                    "public_url": public_url,
                    "source_url": img.get("source_url"),
                    "cleaned": cleaned,
                }
                upsert_image_row(client, image_row)
                log.write(f"{storage_path}\n")
                log.flush()
                done.add(storage_path)
                uploaded += 1

            if yi % 25 == 0 or yi == len(yacht_dirs):
                print(
                    f"[{yi}/{len(yacht_dirs)}] yachts processed | "
                    f"uploaded {uploaded} skipped {skipped} errors {errors}"
                )

    print(f"Done. uploaded={uploaded} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    main()
