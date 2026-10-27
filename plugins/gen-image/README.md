# gen-image

**A mascot, README banner, and social card from one prompt, each under 1 MiB.** Images come from the OpenAI Images API and are cropped and compressed locally.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install gen-image@skills
```

Prerequisites:

- `uv` to run the bundled `genimage.py` script, which declares its dependencies inline.
- `OPENAI_API_KEY` in your environment. Without one, the skill asks you for a key or falls back to the codex plugin's `$imagegen` workflow when codex is signed in with a ChatGPT plan.

## Use it

### Generate a matching brand kit for a new repo

Starting a repository leaves you needing a mascot logo, a README header, and a social preview image that share the same character and meet GitHub's upload limits.

With gen-image installed, ask:

```text
Generate a full brand image set for my project "bark", a CLI log watcher, using a robot pup mascot, and save it to docs/assets.
```

The skill runs the `brand` subcommand and writes `logo.png`, a `1536x512` `readme-banner.webp` composed from the mascot, and a `1536x768` `social-preview.jpg`, each under 1 MiB. It inspects every output with `Read` to verify the mascot matches the concept, the in-image name and tagline text are exact, and the composition is centered. On a miss, it re-runs with a refined concept or prompt instead of shipping an uninspected image. On later runs, passing `--from-logo` reuses the existing `logo.png` and regenerates only the banner and social card.

### Create dark and light README banners from an existing logo

A single banner clashes when readers switch between dark and light GitHub themes, and generating a fresh banner from scratch loses your existing mascot.

With gen-image installed, ask:

```text
Make dark and light README banners for bark using assets/logo.png and put them in assets/.
```

The skill runs the `banner` subcommand with `--logo assets/logo.png` and `--variant both`. Passing `--logo` keeps the banner's mascot consistent with `assets/logo.png`. Setting `--variant both` writes `banner-dark.webp` and `banner-light.webp` at height 512 and under 1 MiB each, ready to pair in a `<picture>` block with `prefers-color-scheme`. The skill views both generated files with `Read` before finishing.

### Generate a custom illustration with a consistent palette

You need a custom hero image or docs illustration at a specific aspect ratio while keeping the character or palette from an earlier image.

With gen-image installed, ask:

```text
Generate a 1536x1024 illustration of the robot pup inspecting server racks, matching assets/logo.png, and save it to assets/hero.webp.
```

The skill runs the `generate` subcommand with `--size 1536x1024`, `--edit-from assets/logo.png`, and `--out assets/hero.webp`. Because the output path ends in `.webp`, the script re-encodes the image locally to under 1 MiB. Outputs ending in `.jpg` are also re-encoded under 1 MiB, while `.png` writes the image as returned. The skill reads `assets/hero.webp` with `Read` to check composition and accuracy, re-running with a refined prompt if needed.

## What's inside

| Component | Type | What it does |
| --- | --- | --- |
| `gen-image:gen-image` | Skill | Generates and inspects logos, banners, social cards, and illustrations for repos, docs sites, and profiles. Also called by `repo-bootstrap` and `gh-profile`. |
| `genimage.py` | CLI | Bundled Python script run through `uv run` with `generate`, `logo`, `banner`, and `brand` subcommands for OpenAI Images API calls and local sub-1 MiB compression. |

Part of [cc-skills](../../README.md).
