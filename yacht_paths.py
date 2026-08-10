"""Paths and URLs for page-ready yacht images (jferments output)."""

from __future__ import annotations

from pathlib import Path

# Filesystem (repo root relative)
YACHTS_SOURCE_ROOT = Path("yachts")
PUBLIC_YACHTS_ROOT = Path("public/yachts")

# Web path when `public/` is served at site root
PAGE_YACHTS_PREFIX = "/yachts"


def yacht_folder(slug: str, yacht_id: str) -> str:
    """Directory name under yachts/ and public/yachts/, e.g. defiance-47450."""
    return f"{slug}-{yacht_id}"


def yacht_folder_from_metadata(meta: dict) -> str:
    return yacht_folder(meta["slug"], str(meta["id"]))


def page_image_path(folder: str, file: str) -> str:
    """
    URL path for a gallery image.

    file comes from metadata downloaded_images[].file, e.g. images/001.jpg
    → /yachts/defiance-47450/images/001.jpg
    """
    return f"{PAGE_YACHTS_PREFIX}/{folder}/{file}"


def page_image_path_from_metadata(meta: dict, file: str) -> str:
    return page_image_path(yacht_folder_from_metadata(meta), file)


def public_image_file(folder: str, file: str) -> Path:
    """On-disk path for a cleaned image."""
    return PUBLIC_YACHTS_ROOT / folder / file
