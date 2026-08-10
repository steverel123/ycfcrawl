#!/usr/bin/env python3
"""
Remove YachtCharterFleet watermarks via OpenAI Images API (masked edit).

Requires OPENAI_API_KEY in the environment. Does not store keys on disk.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

DEFAULT_PROMPT = (
    "Remove only the YachtCharterFleet logo and text watermark in the bottom-left "
    "corner over the water. Restore natural ocean water texture and ripples in "
    "that small area. Do not change the yacht, sky, coastline, lighting, or any "
    "other part of the image."
)


def build_ycf_mask(image: np.ndarray) -> np.ndarray:
    """Mask the YachtCharterFleet logo block (bottom-left over water only)."""
    h, w = image.shape[:2]
    mask = np.zeros((h, w), np.uint8)

    y0 = int(h * 0.84)
    x1 = int(w * 0.40)
    region = image[y0:, :x1]

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
        rh, rw = region.shape[:2]
        sub[int(rh * 0.05) :, : int(rw * 0.95)] = 255

    mask[y0:, :x1] = sub
    return mask


def mask_to_openai_png(binary_mask: np.ndarray) -> bytes:
    """OpenAI mask: transparent pixels = edit, opaque pixels = keep."""
    h, w = binary_mask.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, :3] = 255
    rgba[:, :, 3] = 255
    rgba[binary_mask > 0, 3] = 0
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def image_to_png_bytes(path: Path) -> bytes:
    with Image.open(path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def _pad_square(image: Image.Image, mask: Image.Image) -> tuple[Image.Image, Image.Image, tuple[int, int, int, int]]:
    """Pad image and RGBA mask to a square (dall-e-2 requirement). Returns crop box."""
    w, h = image.size
    side = max(w, h)
    pad_left = (side - w) // 2
    pad_top = (side - h) // 2
    image_sq = Image.new("RGB", (side, side), (0, 0, 0))
    image_sq.paste(image, (pad_left, pad_top))
    mask_sq = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    mask_sq.paste(mask, (pad_left, pad_top))
    crop = (pad_left, pad_top, pad_left + w, pad_top + h)
    return image_sq, mask_sq, crop


def remove_watermark_openai(
    image_path: Path,
    output_path: Path,
    *,
    model: str = "gpt-image-1",
    prompt: str = DEFAULT_PROMPT,
    save_mask: bool = False,
) -> Path:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY in your environment")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install the SDK: pip install openai") from exc

    image_bgr = cv2.imread(str(image_path))
    if image_bgr is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    binary_mask = build_ycf_mask(image_bgr)
    if cv2.countNonZero(binary_mask) == 0:
        raise RuntimeError(f"No watermark mask detected for {image_path}")

    if save_mask:
        mask_path = output_path.with_name(output_path.stem + "_openai_mask.png")
        cv2.imwrite(str(mask_path), binary_mask)

    client = OpenAI(api_key=api_key)

    pil_image = Image.open(image_path).convert("RGB")
    pil_mask = Image.open(io.BytesIO(mask_to_openai_png(binary_mask)))

    crop_box: tuple[int, int, int, int] | None = None
    edit_size: str | None = None
    if model == "dall-e-2":
        # DALL·E 2 edits require square PNG; see:
        # https://developers.openai.com/api/docs/models/dall-e-2
        pil_image, pil_mask, crop_box = _pad_square(pil_image, pil_mask)
        px = 1024 if max(pil_image.size) > 512 else 512
        edit_size = f"{px}x{px}"
        pil_image = pil_image.resize((px, px), Image.Resampling.LANCZOS)
        pil_mask = pil_mask.resize((px, px), Image.Resampling.NEAREST)

    def _png(img: Image.Image) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    image_png = _png(pil_image)
    mask_png = _png(pil_mask)
    kwargs: dict = {
        "model": model,
        "image": ("image.png", image_png, "image/png"),
        "mask": ("mask.png", mask_png, "image/png"),
        "prompt": prompt,
        "n": 1,
    }
    if model.startswith("gpt-image"):
        kwargs["input_fidelity"] = "high"
    if edit_size is not None:
        kwargs["size"] = edit_size

    try:
        response = client.images.edit(**kwargs)
    except Exception as exc:
        err = str(exc).lower()
        if model == "dall-e-2" and ("does not exist" in err or "not found" in err):
            raise RuntimeError(
                "dall-e-2 is not enabled on this API key or org. "
                "Use --model gpt-image-1, or check model access at "
                "https://platform.openai.com/docs/models/dall-e-2"
            ) from exc
        raise
    if not response.data:
        raise RuntimeError("OpenAI returned no image data")

    item = response.data[0]
    if item.b64_json:
        out_bytes = base64.b64decode(item.b64_json)
    elif item.url:
        import urllib.request

        out_bytes = urllib.request.urlopen(item.url).read()
    else:
        raise RuntimeError("OpenAI response missing b64_json and url")

    result = Image.open(io.BytesIO(out_bytes)).convert("RGB")
    if crop_box is not None:
        # Map crop box from padded square back to original aspect (after resize).
        orig = Image.open(image_path).convert("RGB")
        result = result.resize((max(orig.size), max(orig.size)), Image.Resampling.LANCZOS)
        side = max(orig.size)
        pad_left = (side - orig.size[0]) // 2
        pad_top = (side - orig.size[1]) // 2
        result = result.crop(
            (pad_left, pad_top, pad_left + orig.size[0], pad_top + orig.size[1])
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path, format="JPEG", quality=92)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove watermarks via OpenAI Images API")
    parser.add_argument("image", type=Path, help="Input image path")
    parser.add_argument("-o", "--output", type=Path, help="Output image path")
    parser.add_argument(
        "--model",
        default="dall-e-2",
        choices=["dall-e-2", "gpt-image-1", "gpt-image-1-mini"],
        help="dall-e-2: masked inpainting (~$0.016–0.02/img); gpt-image-1 if dall-e-2 unavailable",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Edit prompt")
    parser.add_argument("--save-mask", action="store_true", help="Save binary mask PNG")
    args = parser.parse_args()

    output = args.output or args.image.with_name(args.image.stem + "_openai" + args.image.suffix)

    try:
        out = remove_watermark_openai(
            args.image,
            output,
            model=args.model,
            prompt=args.prompt,
            save_mask=args.save_mask,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Saved: {out}")
    if args.save_mask:
        print(f"Mask:  {out.with_name(out.stem + '_openai_mask.png')}")


if __name__ == "__main__":
    main()
