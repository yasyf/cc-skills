"""Prompt templates and output-name contracts for the logo/banner/brand presets.

Pure data and string assembly; no I/O. The output-name constants are a contract
with calling skills (repo-bootstrap, gh-profile) — never rename them.
"""

from __future__ import annotations

BANNER_HEIGHT = 512
SOCIAL_HEIGHT = 768

# `brand` writes exactly these names into --out-dir (repo-bootstrap depends on them).
BRAND_OUTPUTS = ("logo.png", "readme-banner.webp", "social-preview.jpg")
BRAND_LOGO, BRAND_BANNER, BRAND_SOCIAL = BRAND_OUTPUTS

# `banner` writes one or both of these into --out-dir (gh-profile depends on them).
VARIANT_FILENAMES = {"dark": "banner-dark.webp", "light": "banner-light.webp"}

MASCOT_PROMPT = (
    'A cute {concept} mascot character for a software project called "{name}". '
    "Flat illustration, bold clean shapes, thick outlines, friendly expression, "
    "full body, centered, no text."
)

_BANNER_OPENING = 'A wide README header banner for the software project "{name}". '
_BANNER_BACKGROUND = {
    "dark": "Very dark background (near #0d1117) with subtle texture. ",
    "light": "Very light background (near #ffffff) with subtle texture. ",
}
_BANNER_TYPE_COLOR = {"dark": "white", "light": "dark"}
_BANNER_TEXT = (
    'The project name "{name}" in large clean {type_color} type on the left, with the '
    'tagline "{tagline}" in smaller muted type beneath it. '
)
_BANNER_MASCOT = "The mascot character from the input image on the right, same flat style. "
_BANNER_MOTIF = "A simple flat-illustration motif that suits the project on the right. "
_BANNER_LAYOUT = (
    "Compose all content inside the central horizontal band of the image; keep the "
    "top quarter and bottom quarter plain background."
)


def mascot_prompt(name: str, concept: str) -> str:
    return MASCOT_PROMPT.format(concept=concept, name=name)


def banner_prompt(name: str, tagline: str, variant: str, *, with_logo: bool) -> str:
    """Assemble the banner prompt for a dark or light variant.

    The dark + with_logo assembly is pinned by test — editing any fragment it
    uses changes the brand pipeline's banner output for every caller.
    """
    return (
        _BANNER_OPENING.format(name=name)
        + _BANNER_BACKGROUND[variant]
        + _BANNER_TEXT.format(name=name, type_color=_BANNER_TYPE_COLOR[variant], tagline=tagline)
        + (_BANNER_MASCOT if with_logo else _BANNER_MOTIF)
        + _BANNER_LAYOUT
    )
