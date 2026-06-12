"""CLI: argparse validation, preset prompt assembly, output-name contracts. Zero network —
every command-flow test monkeypatches the api boundary (client/generate/edit)."""

from __future__ import annotations

import sys
from io import BytesIO

import genimage
import pytest
from genimage_lib import api, post, presets
from PIL import Image

# --- argparse validation ---


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["generate", "--size", "1024x1024", "--out", "x.png"],
        ["generate", "--prompt", "p", "--out", "x.png"],
        ["generate", "--prompt", "p", "--size", "1024x1024"],
        ["logo", "--name", "x", "--out", "x.png"],
        ["logo", "--concept", "c", "--out", "x.png"],
        ["banner", "--name", "x", "--out-dir", "d"],
        ["banner", "--name", "x", "--tagline", "t"],
        ["brand", "--tagline", "t", "--concept", "c"],
    ],
    ids=[
        "no-subcommand",
        "generate-no-prompt",
        "generate-no-size",
        "generate-no-out",
        "logo-no-concept",
        "logo-no-name",
        "banner-no-tagline",
        "banner-no-out-dir",
        "brand-no-name",
    ],
)
def test_missing_required_flags_exit_nonzero(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["genimage.py", *argv])
    with pytest.raises(SystemExit) as exc:
        genimage.main()
    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["generate", "--prompt", "p", "--size", "big", "--out", "x.png"], "--size must be WxH"),
        (
            ["generate", "--prompt", "p", "--size", "1024x1024", "--transparent", "--edit-from", "l.png", "--out", "x.png"],
            "--transparent cannot be combined with --edit-from",
        ),
        (["generate", "--prompt", "p", "--size", "1024x1024", "--out", "x.bmp"], "--out must end in"),
        (["logo", "--name", "x", "--concept", "c", "--out", "logo.webp"], "logos stay PNG"),
        (["banner", "--name", "x", "--tagline", "t", "--height", "2048", "--out-dir", "d"], "--height must be"),
        (["brand", "--name", "x", "--tagline", "t"], "--concept is required unless --from-logo"),
    ],
    ids=["bad-size", "transparent-edit-from", "bad-out-suffix", "logo-not-png", "height-too-tall", "brand-no-concept"],
)
def test_cross_flag_validation(monkeypatch, capsys, argv, message):
    monkeypatch.setattr(sys, "argv", ["genimage.py", *argv])
    with pytest.raises(SystemExit) as exc:
        genimage.main()
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_brand_without_key_dies_loudly(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        sys, "argv",
        ["genimage.py", "brand", "--name", "x", "--tagline", "y", "--concept", "c", "--out-dir", str(tmp_path)],
    )
    with pytest.raises(SystemExit) as exc:
        genimage.main()
    assert exc.value.code == "ERROR: OPENAI_API_KEY is not set"


def test_brand_from_logo_requires_existing_logo(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # must fail on the logo, not the key
    monkeypatch.setattr(
        sys, "argv",
        ["genimage.py", "brand", "--name", "x", "--tagline", "y", "--from-logo", "--out-dir", str(tmp_path)],
    )
    with pytest.raises(SystemExit, match="run without --from-logo first"):
        genimage.main()


# --- preset prompt assembly ---


def test_dark_banner_prompt_matches_brand_pipeline_verbatim():
    """The dark + with_logo assembly must equal repo-bootstrap's original BANNER_PROMPT."""
    expected = (
        'A wide README header banner for the software project "demo". '
        "Very dark background (near #0d1117) with subtle texture. "
        'The project name "demo" in large clean white type on the left, with the '
        'tagline "Does things." in smaller muted type beneath it. The mascot character '
        "from the input image on the right, same flat style. Compose all content "
        "inside the central horizontal band of the image; keep the top quarter and "
        "bottom quarter plain background."
    )
    assert presets.banner_prompt("demo", "Does things.", "dark", with_logo=True) == expected


def test_light_and_dark_banner_prompts_differ_as_specced():
    dark = presets.banner_prompt("demo", "t", "dark", with_logo=False)
    light = presets.banner_prompt("demo", "t", "light", with_logo=False)
    assert "#0d1117" in dark and "white type" in dark
    assert "#ffffff" in light and "dark type" in light
    assert "light background" in light.lower()


def test_banner_prompt_logo_toggle():
    with_logo = presets.banner_prompt("demo", "t", "dark", with_logo=True)
    without = presets.banner_prompt("demo", "t", "dark", with_logo=False)
    assert "input image" in with_logo
    assert "input image" not in without


def test_mascot_prompt_includes_name_and_concept():
    prompt = presets.mascot_prompt("demo", "robot pup")
    assert 'called "demo"' in prompt
    assert "robot pup mascot" in prompt


# --- output-name contracts ---


def test_brand_output_names():
    assert presets.BRAND_OUTPUTS == ("logo.png", "readme-banner.webp", "social-preview.jpg")


def test_variant_filename_mapping():
    assert presets.VARIANT_FILENAMES == {"dark": "banner-dark.webp", "light": "banner-light.webp"}


# --- command flow with the api boundary monkeypatched ---


class _FakeClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _png(size: tuple[int, int], mode: str = "RGB") -> bytes:
    out = BytesIO()
    Image.new(mode, size, (13, 17, 23) if mode == "RGB" else (13, 17, 23, 255)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def fake_api(monkeypatch):
    """Replace the api boundary with PIL-backed fakes; records (endpoint, prompt, size) calls."""
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-never-used")

    def _dimensions(size: str) -> tuple[int, int]:
        width, height = size.split("x")
        return int(width), int(height)

    def fake_generate(client, *, prompt, size, quality, transparent=False):
        calls.append(("generate", prompt, size))
        return _png(_dimensions(size), mode="RGBA" if transparent else "RGB")

    def fake_edit(client, *, prompt, size, quality, image):
        calls.append(("edit", prompt, size))
        return _png(_dimensions(size))

    monkeypatch.setattr(api, "client", lambda: _FakeClient())
    monkeypatch.setattr(api, "generate", fake_generate)
    monkeypatch.setattr(api, "edit", fake_edit)
    return calls


def test_generate_writes_png_as_returned(fake_api, monkeypatch, tmp_path):
    out = tmp_path / "art.png"
    monkeypatch.setattr(
        sys, "argv", ["genimage.py", "generate", "--prompt", "a thing", "--size", "1024x1024", "--out", str(out)]
    )
    assert genimage.main() == 0
    assert Image.open(out).format == "PNG"
    assert fake_api == [("generate", "a thing", "1024x1024")]


def test_generate_edit_from_uses_edits_endpoint(fake_api, monkeypatch, tmp_path):
    source = tmp_path / "in.png"
    source.write_bytes(_png((1024, 1024)))
    out = tmp_path / "art.webp"
    monkeypatch.setattr(
        sys, "argv",
        ["genimage.py", "generate", "--prompt", "p", "--size", "1536x1024", "--edit-from", str(source), "--out", str(out)],
    )
    assert genimage.main() == 0
    assert fake_api[0][0] == "edit"
    assert Image.open(out).format == "WEBP"
    assert out.stat().st_size < post.MAX_BYTES


def test_logo_preset_writes_quantized_png(fake_api, monkeypatch, tmp_path):
    out = tmp_path / "logo.png"
    monkeypatch.setattr(
        sys, "argv", ["genimage.py", "logo", "--name", "demo", "--concept", "robot pup", "--out", str(out)]
    )
    assert genimage.main() == 0
    assert fake_api == [("generate", presets.mascot_prompt("demo", "robot pup"), api.LOGO_SIZE)]
    assert Image.open(out).format == "PNG"
    assert out.stat().st_size < post.MAX_BYTES


def test_banner_both_writes_dark_and_light(fake_api, monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv",
        ["genimage.py", "banner", "--name", "demo", "--tagline", "t", "--variant", "both", "--out-dir", str(tmp_path)],
    )
    assert genimage.main() == 0
    for name in ("banner-dark.webp", "banner-light.webp"):
        out = tmp_path / name
        assert out.is_file()
        with Image.open(out) as image:
            assert image.format == "WEBP"
            assert image.size == (1536, presets.BANNER_HEIGHT)
        assert out.stat().st_size < post.MAX_BYTES
    prompts = [prompt for endpoint, prompt, _ in fake_api if endpoint == "generate"]
    assert any("#0d1117" in prompt for prompt in prompts)
    assert any("#ffffff" in prompt for prompt in prompts)


def test_banner_with_logo_composes_via_edits(fake_api, monkeypatch, tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(_png((1024, 1024), mode="RGBA"))
    monkeypatch.setattr(
        sys, "argv",
        ["genimage.py", "banner", "--name", "demo", "--tagline", "t", "--logo", str(logo), "--out-dir", str(tmp_path)],
    )
    assert genimage.main() == 0
    assert fake_api[0][0] == "edit"
    assert "input image" in fake_api[0][1]
    assert (tmp_path / "banner-dark.webp").is_file()  # dark is the default variant


def test_banner_custom_height(fake_api, monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv",
        ["genimage.py", "banner", "--name", "demo", "--tagline", "t", "--height", "320", "--out-dir", str(tmp_path)],
    )
    assert genimage.main() == 0
    with Image.open(tmp_path / "banner-dark.webp") as image:
        assert image.size == (1536, 320)


def test_brand_writes_exact_output_names(fake_api, monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv",
        ["genimage.py", "brand", "--name", "demo", "--tagline", "t", "--concept", "robot pup", "--out-dir", str(tmp_path)],
    )
    assert genimage.main() == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(presets.BRAND_OUTPUTS)
    with Image.open(tmp_path / "logo.png") as logo:
        assert logo.format == "PNG"
    with Image.open(tmp_path / "readme-banner.webp") as banner:
        assert banner.format == "WEBP"
        assert banner.size == (1536, presets.BANNER_HEIGHT)
    with Image.open(tmp_path / "social-preview.jpg") as social:
        assert social.format == "JPEG"
        assert social.size == (1536, presets.SOCIAL_HEIGHT)
    for path in tmp_path.iterdir():
        assert path.stat().st_size < post.MAX_BYTES
    assert [endpoint for endpoint, _, _ in fake_api] == ["generate", "edit"]  # logo, then matched banner


def test_brand_from_logo_skips_logo_generation(fake_api, monkeypatch, tmp_path):
    (tmp_path / "logo.png").write_bytes(_png((1024, 1024), mode="RGBA"))
    monkeypatch.setattr(
        sys, "argv",
        ["genimage.py", "brand", "--name", "demo", "--tagline", "t", "--from-logo", "--out-dir", str(tmp_path)],
    )
    assert genimage.main() == 0
    assert [endpoint for endpoint, _, _ in fake_api] == ["edit"]  # no logo generation
    assert (tmp_path / "readme-banner.webp").is_file()
    assert (tmp_path / "social-preview.jpg").is_file()
