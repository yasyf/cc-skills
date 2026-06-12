"""genimages: offline tests for crop geometry, size-capped encoders, and CLI validation."""

from __future__ import annotations

import sys
from io import BytesIO

import genimages
import pytest
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
    [(genimages.BANNER_HEIGHT, 256), (genimages.SOCIAL_HEIGHT, 384)],
    ids=["banner", "social"],
)
def test_crop_band_is_centered(height, marker_row):
    band = genimages.crop_band(_banner_source(), height)
    assert band.size == (1536, height)
    assert band.getpixel((0, marker_row)) == MARKER
    assert band.getpixel((0, marker_row - 1)) != MARKER


@pytest.mark.parametrize("fmt", ["WEBP", "JPEG"])
def test_encode_under_limit_steps_down_quality(fmt, monkeypatch):
    image = Image.effect_noise((1536, 768), 64).convert("RGB")
    at_q92 = BytesIO()
    image.save(at_q92, format=fmt, quality=92)
    monkeypatch.setattr(genimages, "MAX_BYTES", at_q92.tell())  # force at least one step-down
    encoded = genimages.encode_under_limit(image, fmt)
    assert len(encoded) < at_q92.tell()
    assert Image.open(BytesIO(encoded)).format == fmt


def test_encode_under_limit_exits_when_impossible(monkeypatch):
    monkeypatch.setattr(genimages, "MAX_BYTES", 100)
    with pytest.raises(SystemExit, match="under 1 MiB"):
        genimages.encode_under_limit(Image.effect_noise((1536, 768), 64), "JPEG")


def test_encode_logo_quantizes_and_keeps_alpha():
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    for x in range(256):
        for y in range(256):
            image.putpixel((x + 384, y + 384), (x, y, 128, 255))
    raw = BytesIO()
    image.save(raw, format="PNG")
    encoded = genimages.encode_logo(raw.getvalue())
    assert len(encoded) < genimages.MAX_BYTES
    assert len(encoded) < raw.tell()
    reopened = Image.open(BytesIO(encoded)).convert("RGBA")
    assert reopened.getpixel((0, 0))[3] == 0  # transparency preserved
    assert reopened.getpixel((512, 512))[3] == 255


def test_concept_required_without_from_logo(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["genimages.py", "--name", "x", "--tagline", "y"])
    with pytest.raises(SystemExit) as exc:
        genimages.main()
    assert exc.value.code == 2
    assert "--concept is required unless --from-logo" in capsys.readouterr().err


def test_from_logo_requires_existing_logo(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # must fail on the logo, not the key
    monkeypatch.setattr(
        sys, "argv",
        ["genimages.py", "--name", "x", "--tagline", "y", "--from-logo", "--out-dir", str(tmp_path)],
    )
    with pytest.raises(SystemExit, match="run without --from-logo first"):
        genimages.main()
