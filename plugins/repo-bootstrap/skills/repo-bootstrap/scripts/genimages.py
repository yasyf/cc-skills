#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pillow"]
# ///
"""Generate a repo's brand images — mascot logo, README banner, social card — with gpt-image-2.

Generates the mascot first (transparent 1024x1024), then composes a 1536x1024 banner
source from it via the images-edits endpoint so the character matches, and center-crops
the source twice: a 1536x512 band for the README banner and a 1536x768 (2:1) band for
the GitHub social-preview card. Every output is lossy-compressed locally to under 1 MiB:
logo.png (quantized PNG — Great Docs only detects svg/png), readme-banner.webp, and
social-preview.jpg (GitHub's social-preview upload accepts only PNG/JPG/GIF under 1 MB).
With --from-logo, reuses the existing logo.png and regenerates only banner + social.
Requires OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from io import BytesIO
from pathlib import Path

API_BASE = "https://api.openai.com/v1"
# gpt-image-2 rejects background=transparent; gpt-image-1.5 supports it natively.
# The banner still matches the mascot because the edits call sees the logo image.
LOGO_MODEL = "gpt-image-1.5"
BANNER_MODEL = "gpt-image-2"
LOGO_SIZE = "1024x1024"
BANNER_SOURCE_SIZE = "1536x1024"
BANNER_HEIGHT = 512
SOCIAL_HEIGHT = 768
MAX_BYTES = 1 << 20

MASCOT_PROMPT = (
    'A cute {concept} mascot character for a software project called "{name}". '
    "Flat illustration, bold clean shapes, thick outlines, friendly expression, "
    "full body, centered, no text."
)
BANNER_PROMPT = (
    'A wide README header banner for the software project "{name}". '
    "Very dark background (near #0d1117) with subtle texture. "
    'The project name "{name}" in large clean white type on the left, with the '
    'tagline "{tagline}" in smaller muted type beneath it. The mascot character '
    "from the input image on the right, same flat style. Compose all content "
    "inside the central horizontal band of the image; keep the top quarter and "
    "bottom quarter plain background."
)


def _client():
    import httpx

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("ERROR: OPENAI_API_KEY is not set")
    return httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {key}"},
        timeout=300.0,
    )


def _image_bytes(response) -> bytes:
    if response.status_code != 200:
        sys.exit(f"ERROR: Images API returned {response.status_code}: {response.text}")
    return base64.b64decode(response.json()["data"][0]["b64_json"])


def generate_logo(client, name: str, concept: str, quality: str) -> bytes:
    response = client.post(
        "/images/generations",
        json={
            "model": LOGO_MODEL,
            "prompt": MASCOT_PROMPT.format(concept=concept, name=name),
            "size": LOGO_SIZE,
            "background": "transparent",
            "output_format": "png",
            "quality": quality,
        },
    )
    return _image_bytes(response)


def generate_banner(client, logo: bytes, name: str, tagline: str, quality: str) -> bytes:
    response = client.post(
        "/images/edits",
        data={
            "model": BANNER_MODEL,
            "prompt": BANNER_PROMPT.format(name=name, tagline=tagline),
            "size": BANNER_SOURCE_SIZE,
            "output_format": "png",
            "quality": quality,
        },
        files={"image": ("logo.png", logo, "image/png")},
    )
    return _image_bytes(response)


def crop_band(source: bytes, height: int):
    from PIL import Image

    image = Image.open(BytesIO(source))
    width, full_height = image.size
    top = (full_height - height) // 2
    return image.crop((0, top, width, top + height))


def encode_logo(logo: bytes) -> bytes:
    from PIL import Image

    with Image.open(BytesIO(logo)) as image:
        quantized = image.convert("RGBA").quantize(256, method=Image.Quantize.FASTOCTREE)
        out = BytesIO()
        quantized.save(out, format="PNG", optimize=True)
    if out.tell() >= MAX_BYTES:
        sys.exit(f"ERROR: quantized logo is {out.tell()} bytes, still >= 1 MiB")
    return out.getvalue()


def encode_under_limit(image, fmt: str) -> bytes:
    converted = image.convert("RGB")
    for quality in range(92, 45, -5):
        out = BytesIO()
        converted.save(out, format=fmt, quality=quality)
        if out.tell() < MAX_BYTES:
            return out.getvalue()
    sys.exit(f"ERROR: could not encode {fmt} under 1 MiB even at quality 50")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="project name")
    parser.add_argument("--tagline", required=True, help="one-line description")
    parser.add_argument("--concept", help='mascot concept, e.g. "robot pup" (required unless --from-logo)')
    parser.add_argument(
        "--from-logo",
        action="store_true",
        help="reuse the existing logo.png and regenerate only banner + social card",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("docs/assets"))
    parser.add_argument("--quality", default="high", choices=["low", "medium", "high", "auto"])
    args = parser.parse_args()

    if not args.from_logo and not args.concept:
        parser.error("--concept is required unless --from-logo")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    logo_path = args.out_dir / "logo.png"
    if args.from_logo and not logo_path.is_file():
        sys.exit(f"ERROR: {logo_path} not found — run without --from-logo first")

    with _client() as client:
        if args.from_logo:
            logo = logo_path.read_bytes()
            if len(logo) >= MAX_BYTES:
                logo_path.write_bytes(encode_logo(logo))
                print(logo_path)
        else:
            logo = generate_logo(client, args.name, args.concept, args.quality)
            logo_path.write_bytes(encode_logo(logo))
            print(logo_path)

        source = generate_banner(client, logo, args.name, args.tagline, args.quality)

    banner_path = args.out_dir / "readme-banner.webp"
    banner_path.write_bytes(encode_under_limit(crop_band(source, BANNER_HEIGHT), "WEBP"))
    print(banner_path)

    social_path = args.out_dir / "social-preview.jpg"
    social_path.write_bytes(encode_under_limit(crop_band(source, SOCIAL_HEIGHT), "JPEG"))
    print(social_path)


if __name__ == "__main__":
    main()
