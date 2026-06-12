"""post: offline tests for crop geometry and the size-capped encoders. Pure PIL, no network."""

from __future__ import annotations

from io import BytesIO

import pytest
from genimage_lib import post, presets
from PIL import Image

MARKER = (255, 0, 0)


def _banner_source() -> bytes:
    """A 1536x1024 banner source with a red marker row at the vertical center."""
    image = Image.new("RGB", (1536, 1024), (13, 17, 23))
    for x in range(1536):
        image.putpixel((x, 512), MARKER)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize(
    ("height", "marker_row"),
    [(presets.BANNER_HEIGHT, 256), (presets.SOCIAL_HEIGHT, 384)],
    ids=["banner", "social"],
)
def test_crop_band_is_centered(height, marker_row):
    band = post.crop_band(_banner_source(), height)
    assert band.size == (1536, height)
    assert band.getpixel((0, marker_row)) == MARKER
    assert band.getpixel((0, marker_row - 1)) != MARKER


@pytest.mark.parametrize("fmt", ["WEBP", "JPEG"])
def test_encode_under_limit_steps_down_quality(fmt, monkeypatch):
    image = Image.effect_noise((1536, 768), 64).convert("RGB")
    at_q92 = BytesIO()
    image.save(at_q92, format=fmt, quality=92)
    monkeypatch.setattr(post, "MAX_BYTES", at_q92.tell())  # force at least one step-down
    encoded = post.encode_under_limit(image, fmt)
    assert len(encoded) < at_q92.tell()
    assert Image.open(BytesIO(encoded)).format == fmt


@pytest.mark.parametrize("fmt", ["WEBP", "JPEG"])
def test_encode_under_limit_holds_real_limit_for_noise(fmt):
    """Random noise is the worst case for lossy encoders — still lands under 1 MiB."""
    image = Image.effect_noise((1536, 768), 100).convert("RGB")
    encoded = post.encode_under_limit(image, fmt)
    assert len(encoded) < post.MAX_BYTES
    assert Image.open(BytesIO(encoded)).format == fmt


def test_encode_under_limit_exits_when_impossible(monkeypatch):
    monkeypatch.setattr(post, "MAX_BYTES", 100)
    with pytest.raises(SystemExit, match="under 1 MiB"):
        post.encode_under_limit(Image.effect_noise((1536, 768), 64), "JPEG")


def test_reencode_under_limit_accepts_bytes(monkeypatch):
    encoded = post.reencode_under_limit(_banner_source(), "WEBP")
    assert len(encoded) < post.MAX_BYTES
    assert Image.open(BytesIO(encoded)).format == "WEBP"


def _gradient_logo_png() -> BytesIO:
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    for x in range(256):
        for y in range(256):
            image.putpixel((x + 384, y + 384), (x, y, 128, 255))
    raw = BytesIO()
    image.save(raw, format="PNG")
    return raw


def test_encode_logo_is_quantized_png_under_limit():
    raw = _gradient_logo_png()
    encoded = post.encode_logo(raw.getvalue())
    assert len(encoded) < post.MAX_BYTES
    assert len(encoded) < raw.tell()  # quantization actually shrank it
    reopened = Image.open(BytesIO(encoded))
    assert reopened.format == "PNG"
    converted = reopened.convert("RGBA")
    assert converted.getpixel((0, 0))[3] == 0  # transparency preserved
    assert converted.getpixel((512, 512))[3] == 255


def test_encode_logo_exits_when_still_over_limit(monkeypatch):
    monkeypatch.setattr(post, "MAX_BYTES", 100)
    with pytest.raises(SystemExit, match="still >= 1 MiB"):
        post.encode_logo(_gradient_logo_png().getvalue())
