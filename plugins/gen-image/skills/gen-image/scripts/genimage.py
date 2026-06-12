#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pillow"]
# ///
"""gen-image CLI — one entry point for the whole skill.

    genimage.py generate --prompt P --size WxH [--transparent] [--edit-from IMG] [--quality Q] --out PATH
    genimage.py logo     --name N --concept C [--quality Q] --out PATH
    genimage.py banner   --name N --tagline T [--logo IMG] [--variant dark|light|both] [--height H] [--quality Q] --out-dir DIR
    genimage.py brand    --name N --tagline T --concept C [--from-logo] [--quality Q] --out-dir DIR

generate is the raw primitive: one prompt, one image (--transparent forces
gpt-image-1.5; --edit-from switches to the edits endpoint with an input image).
logo and banner are presets built on it. brand is the full pipeline repo-bootstrap
uses: mascot logo, 1536x512 README banner, and 1536x768 social card composed from
the same character via the edits endpoint. Every output is lossy-compressed
locally to under 1 MiB. Requires OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from genimage_lib import api, post, presets

SIZE_RE = re.compile(r"^\d+x\d+$")
QUALITIES = ("low", "medium", "high", "auto")
GENERATE_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genimage.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="raw primitive: one prompt, one image")
    gen.add_argument("--prompt", required=True, help="full prompt text")
    gen.add_argument("--size", required=True, help="WxH; the API supports 1024x1024, 1536x1024, 1024x1536")
    gen.add_argument("--transparent", action="store_true", help="transparent background (forces gpt-image-1.5)")
    gen.add_argument("--edit-from", type=Path, metavar="IMG", help="input image; switches to the edits endpoint")
    gen.add_argument("--quality", default="high", choices=QUALITIES)
    gen.add_argument(
        "--out", type=Path, required=True, help=".png written as returned; .webp/.jpg re-encoded under 1 MiB"
    )

    logo = sub.add_parser("logo", help="mascot preset: transparent quantized PNG under 1 MiB")
    logo.add_argument("--name", required=True, help="project name")
    logo.add_argument("--concept", required=True, help='mascot concept, e.g. "robot pup"')
    logo.add_argument("--quality", default="high", choices=QUALITIES)
    logo.add_argument("--out", type=Path, required=True, help="output path (must end in .png)")

    banner = sub.add_parser("banner", help="README banner preset: dark/light/both WEBP under 1 MiB")
    banner.add_argument("--name", required=True, help="project or profile name")
    banner.add_argument("--tagline", required=True, help="one-line description")
    banner.add_argument("--logo", type=Path, metavar="IMG", help="existing logo; composes via the edits endpoint")
    banner.add_argument("--variant", default="dark", choices=("dark", "light", "both"))
    banner.add_argument("--height", type=int, default=presets.BANNER_HEIGHT, help="banner height (default 512)")
    banner.add_argument("--quality", default="high", choices=QUALITIES)
    banner.add_argument("--out-dir", type=Path, required=True)

    brand = sub.add_parser("brand", help="full pipeline: logo.png + readme-banner.webp + social-preview.jpg")
    brand.add_argument("--name", required=True, help="project name")
    brand.add_argument("--tagline", required=True, help="one-line description")
    brand.add_argument("--concept", help='mascot concept, e.g. "robot pup" (required unless --from-logo)')
    brand.add_argument(
        "--from-logo",
        action="store_true",
        help="reuse the existing logo.png and regenerate only banner + social card",
    )
    brand.add_argument("--quality", default="high", choices=QUALITIES)
    brand.add_argument("--out-dir", type=Path, default=Path("docs/assets"))

    return parser


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(path)


def _require_file(path: Path) -> bytes:
    if not path.is_file():
        sys.exit(f"ERROR: {path} not found")
    return path.read_bytes()


def cmd_generate(args: argparse.Namespace) -> int:
    edit_from = _require_file(args.edit_from) if args.edit_from else None
    with api.client() as client:
        if edit_from is not None:
            raw = api.edit(client, prompt=args.prompt, size=args.size, quality=args.quality, image=edit_from)
        else:
            raw = api.generate(
                client, prompt=args.prompt, size=args.size, quality=args.quality, transparent=args.transparent
            )
    suffix = args.out.suffix.lower()
    if suffix == ".webp":
        raw = post.reencode_under_limit(raw, "WEBP")
    elif suffix in (".jpg", ".jpeg"):
        raw = post.reencode_under_limit(raw, "JPEG")
    _write(args.out, raw)
    return 0


def cmd_logo(args: argparse.Namespace) -> int:
    with api.client() as client:
        raw = api.generate(
            client,
            prompt=presets.mascot_prompt(args.name, args.concept),
            size=api.LOGO_SIZE,
            quality=args.quality,
            transparent=True,
        )
    _write(args.out, post.encode_logo(raw))
    return 0


def cmd_banner(args: argparse.Namespace) -> int:
    variants = ("dark", "light") if args.variant == "both" else (args.variant,)
    logo = _require_file(args.logo) if args.logo else None
    with api.client() as client:
        for variant in variants:
            prompt = presets.banner_prompt(args.name, args.tagline, variant, with_logo=logo is not None)
            if logo is not None:
                source = api.edit(client, prompt=prompt, size=api.BANNER_SOURCE_SIZE, quality=args.quality, image=logo)
            else:
                source = api.generate(client, prompt=prompt, size=api.BANNER_SOURCE_SIZE, quality=args.quality)
            band = post.crop_band(source, args.height)
            _write(args.out_dir / presets.VARIANT_FILENAMES[variant], post.encode_under_limit(band, "WEBP"))
    return 0


def cmd_brand(args: argparse.Namespace) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    logo_path = args.out_dir / presets.BRAND_LOGO
    if args.from_logo and not logo_path.is_file():
        sys.exit(f"ERROR: {logo_path} not found — run without --from-logo first")

    with api.client() as client:
        if args.from_logo:
            logo = logo_path.read_bytes()
            if len(logo) >= post.MAX_BYTES:
                logo_path.write_bytes(post.encode_logo(logo))
                print(logo_path)
        else:
            logo = api.generate(
                client,
                prompt=presets.mascot_prompt(args.name, args.concept),
                size=api.LOGO_SIZE,
                quality=args.quality,
                transparent=True,
            )
            logo_path.write_bytes(post.encode_logo(logo))
            print(logo_path)

        source = api.edit(
            client,
            prompt=presets.banner_prompt(args.name, args.tagline, "dark", with_logo=True),
            size=api.BANNER_SOURCE_SIZE,
            quality=args.quality,
            image=logo,
        )

    _write(args.out_dir / presets.BRAND_BANNER, post.encode_under_limit(post.crop_band(source, presets.BANNER_HEIGHT), "WEBP"))
    _write(args.out_dir / presets.BRAND_SOCIAL, post.encode_under_limit(post.crop_band(source, presets.SOCIAL_HEIGHT), "JPEG"))
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "generate":
        if not SIZE_RE.match(args.size):
            parser.error("--size must be WxH, e.g. 1536x1024")
        if args.transparent and args.edit_from:
            parser.error("--transparent cannot be combined with --edit-from (the edits model has no transparent mode)")
        if args.out.suffix.lower() not in GENERATE_SUFFIXES:
            parser.error("--out must end in .png, .webp, .jpg, or .jpeg")
        return cmd_generate(args)
    if args.command == "logo":
        if args.out.suffix.lower() != ".png":
            parser.error("--out must end in .png (logos stay PNG)")
        return cmd_logo(args)
    if args.command == "banner":
        if not 0 < args.height <= 1024:
            parser.error("--height must be between 1 and 1024 (the banner source is 1536x1024)")
        return cmd_banner(args)
    if args.command == "brand":
        if not args.from_logo and not args.concept:
            parser.error("--concept is required unless --from-logo")
        return cmd_brand(args)
    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    sys.exit(main())
