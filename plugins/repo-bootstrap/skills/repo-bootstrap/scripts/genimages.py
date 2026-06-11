#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pillow"]
# ///
"""Generate a repo's brand images — mascot logo + README banner — with gpt-image-2.

Generates the mascot first (transparent 1024x1024), then composes the banner from
it via the images-edits endpoint so the character matches, and center-crops the
result to a 1536x512 band. Requires OPENAI_API_KEY.
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


def crop_banner(banner: bytes) -> bytes:
    from PIL import Image

    with Image.open(BytesIO(banner)) as image:
        width, height = image.size
        top = (height - BANNER_HEIGHT) // 2
        band = image.crop((0, top, width, top + BANNER_HEIGHT))
        out = BytesIO()
        band.save(out, format="PNG")
    return out.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="project name")
    parser.add_argument("--tagline", required=True, help="one-line description")
    parser.add_argument("--concept", required=True, help='mascot concept, e.g. "robot pup"')
    parser.add_argument("--out-dir", type=Path, default=Path("docs/assets"))
    parser.add_argument("--quality", default="high", choices=["low", "medium", "high", "auto"])
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with _client() as client:
        logo = generate_logo(client, args.name, args.concept, args.quality)
        logo_path = args.out_dir / "logo.png"
        logo_path.write_bytes(logo)
        print(logo_path)

        banner = generate_banner(client, logo, args.name, args.tagline, args.quality)
        banner_path = args.out_dir / "readme-banner.png"
        banner_path.write_bytes(crop_banner(banner))
        print(banner_path)


if __name__ == "__main__":
    main()
