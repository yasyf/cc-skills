"""Local post-processing: center-band crops and size-capped encoders.

The single home of the <1 MiB convention — quantized PNG for logos,
quality-stepping WEBP/JPEG for everything else. Every byte committed to a
repo goes through one of these encoders. Pure PIL; no network.
"""

from __future__ import annotations

import sys
from io import BytesIO

from PIL import Image

MAX_BYTES = 1 << 20


def crop_band(source: bytes, height: int) -> Image.Image:
    """Crop the vertically-centered full-width band of the given height."""
    image = Image.open(BytesIO(source))
    width, full_height = image.size
    top = (full_height - height) // 2
    return image.crop((0, top, width, top + height))


def encode_logo(logo: bytes) -> bytes:
    """Quantize to a 256-color PNG (alpha preserved); dies if still >= 1 MiB."""
    with Image.open(BytesIO(logo)) as image:
        quantized = image.convert("RGBA").quantize(256, method=Image.Quantize.FASTOCTREE)
        out = BytesIO()
        quantized.save(out, format="PNG", optimize=True)
    if out.tell() >= MAX_BYTES:
        sys.exit(f"ERROR: quantized logo is {out.tell()} bytes, still >= 1 MiB")
    return out.getvalue()


def encode_under_limit(image: Image.Image, fmt: str) -> bytes:
    """Encode as WEBP or JPEG, stepping quality down from 92 until under 1 MiB."""
    converted = image.convert("RGB")
    for quality in range(92, 45, -5):
        out = BytesIO()
        converted.save(out, format=fmt, quality=quality)
        if out.tell() < MAX_BYTES:
            return out.getvalue()
    sys.exit(f"ERROR: could not encode {fmt} under 1 MiB even at quality 50")


def reencode_under_limit(source: bytes, fmt: str) -> bytes:
    """encode_under_limit for already-encoded image bytes."""
    return encode_under_limit(Image.open(BytesIO(source)), fmt)
