"""OpenAI Images API boundary: client, generations + edits calls, model/size constants.

The only module that talks to the network. Everything above it (CLI, presets,
tests) treats these three functions as the seam to monkeypatch.
"""

from __future__ import annotations

import base64
import os
import sys

import httpx

API_BASE = "https://api.openai.com/v1"
# gpt-image-2 rejects background=transparent; gpt-image-1.5 supports it natively.
# Edits-based compositions still match a mascot because the call sees the logo image.
TRANSPARENT_MODEL = "gpt-image-1.5"
DEFAULT_MODEL = "gpt-image-2"
LOGO_SIZE = "1024x1024"
BANNER_SOURCE_SIZE = "1536x1024"


def client() -> httpx.Client:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("ERROR: OPENAI_API_KEY is not set")
    return httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {key}"},
        timeout=300.0,
    )


def _image_bytes(response: httpx.Response) -> bytes:
    if response.status_code != 200:
        sys.exit(f"ERROR: Images API returned {response.status_code}: {response.text}")
    return base64.b64decode(response.json()["data"][0]["b64_json"])


def generate(client: httpx.Client, *, prompt: str, size: str, quality: str, transparent: bool = False) -> bytes:
    """Call the generations endpoint; returns PNG bytes. Transparency forces TRANSPARENT_MODEL."""
    payload = {
        "model": TRANSPARENT_MODEL if transparent else DEFAULT_MODEL,
        "prompt": prompt,
        "size": size,
        "output_format": "png",
        "quality": quality,
    }
    if transparent:
        payload["background"] = "transparent"
    return _image_bytes(client.post("/images/generations", json=payload))


def edit(client: httpx.Client, *, prompt: str, size: str, quality: str, image: bytes) -> bytes:
    """Call the edits endpoint with one input image (multipart form); returns PNG bytes."""
    response = client.post(
        "/images/edits",
        data={
            "model": DEFAULT_MODEL,
            "prompt": prompt,
            "size": size,
            "output_format": "png",
            "quality": quality,
        },
        files={"image": ("image.png", image, "image/png")},
    )
    return _image_bytes(response)
