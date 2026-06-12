---
name: gen-image
description: Generate project images — mascot logos, README banners (dark/light variants), GitHub social-preview cards, and arbitrary illustrations — via the OpenAI Images API, compressed locally to under 1 MiB. Use when asked to generate a logo, mascot, banner, hero image, social card, brand images, or any illustration for a repo, docs site, or profile; also invoked by repo-bootstrap and gh-profile for their image phases.
---

# Generate Project Images

Generate brand and illustration images through one CLI; all post-processing
(center-crops, format choice, <1 MiB compression) happens locally. The whole
skill is driven by a single command:

```bash
GENIMAGE="uv run ${CLAUDE_PLUGIN_ROOT}/skills/gen-image/scripts/genimage.py"
$GENIMAGE generate | logo | banner | brand [flags]
```

## 1 — Resolve the API key

Every subcommand needs `OPENAI_API_KEY`. Resolve it in order:

1. Already in the environment — use it.
2. 1Password: `export OPENAI_API_KEY=$(op read "op://OpenClaw/OpenAI API Key/notesPlain")`
3. Neither works — ask the user for a key, or use the codex fallback below.

The script never prompts; without a key it dies with
`ERROR: OPENAI_API_KEY is not set`.

**No key at all?** Use the **codex** skill's "Generating Images ($imagegen)"
workflow instead — it needs codex signed in with a ChatGPT plan, no API key.
You still own placement, naming, and the compression conventions below
(codex's output is raw; run it through `sips`/Pillow per that skill's notes).

**Exit criteria:** `OPENAI_API_KEY` exported, or the codex fallback chosen.

## 2 — Generate

### `generate` — raw primitive (any prompt, any supported size)

```bash
$GENIMAGE generate --prompt "..." --size 1536x1024 \
  [--transparent] [--edit-from IMG] [--quality low|medium|high|auto] --out PATH
```

- Generations endpoint by default; `--edit-from IMG` switches to the edits
  endpoint with `IMG` as the input image — use it to keep a character or
  palette consistent across related images.
- `--transparent` forces gpt-image-1.5 (gpt-image-2 rejects transparent
  backgrounds); it cannot combine with `--edit-from`.
- The API renders 1024x1024, 1536x1024, and 1024x1536. Other shapes: generate
  a supported size and crop — the presets below do exactly that.
- `--out` extension picks the encoding: `.png` is written as returned;
  `.webp`/`.jpg` are re-encoded under 1 MiB.

### `logo` — mascot preset

```bash
$GENIMAGE logo --name PROJECT --concept "robot pup" --out assets/logo.png
```

Square 1024x1024 transparent mascot, quantized to a 256-color PNG under 1 MiB.
Pick a concept that puns on the project's name or purpose (a crab for a fleet
tool, an octopus for an orchestrator).

### `banner` — README banner preset

```bash
$GENIMAGE banner --name PROJECT --tagline "..." \
  [--logo assets/logo.png] [--variant dark|light|both] [--height 512] \
  [--quality low|medium|high|auto] --out-dir assets
```

Renders a 1536x1024 source per variant, center-crops the middle
1536x`height` band, writes WEBP under 1 MiB:

| `--variant` | Files written | Look |
|---|---|---|
| `dark` (default) | `banner-dark.webp` | near-#0d1117 background, white type |
| `light` | `banner-light.webp` | near-#ffffff background, dark type |
| `both` | both files | pair in a `<picture>` block via `prefers-color-scheme` |

With `--logo`, composition goes through the edits endpoint so the banner's
mascot matches the logo; without it, the banner gets a generic flat motif.

### `brand` — full pipeline (logo + banner + social card)

```bash
$GENIMAGE brand --name PROJECT --tagline "..." --concept "robot pup" --out-dir docs/assets
```

Writes exactly three files into `--out-dir`: `logo.png` (transparent mascot),
`readme-banner.webp` (1536x512), and `social-preview.jpg` (1536x768 — GitHub's
social-preview upload accepts only PNG/JPG/GIF under 1 MB). The mascot is
generated first, then a banner source is composed from it via the edits
endpoint so the character matches, then center-cropped twice. On re-runs,
`--from-logo` reuses the existing `logo.png` (re-encoding it in place if over
1 MiB) and regenerates only banner + social card — `--concept` not needed.

**Exit criteria:** the command exited 0 and printed every output path.

## 3 — Review with Read

**View every output with Read** (it renders images) and judge: is the mascot
on concept? Is the in-image name/tagline text exactly right? Is the
composition centered? On a miss, re-run with a refined `--concept`/`--prompt`;
wrong text usually fixes itself on a regeneration (accuracy varies per
attempt — always quote the exact strings in the prompt). Never ship an image
you haven't looked at.

**Exit criteria:** every generated file viewed and on-spec.

## Conventions

- Every output is lossy-compressed locally to **under 1 MiB** — quantized PNG
  logos, quality-stepping WEBP banners, JPEG social cards. Small enough for
  jj's snapshot limit and GitHub's upload caps, visually identical for flat
  illustration.
- Logos stay **PNG** (Great Docs logo auto-detection matches only svg/png);
  banners are **WEBP**; social cards are **JPEG**.
- Generate related images in one run — or chain them with `--edit-from` /
  `--logo` — so the character and palette stay consistent.

## Calling from other skills

repo-bootstrap and gh-profile invoke this skill instead of bundling image
code. Exact invocations and the files that come back:

| Caller | Invocation | Files written |
|---|---|---|
| repo-bootstrap (brand images) | `$GENIMAGE brand --name N --tagline "T" --concept "C" --out-dir docs/assets` | `docs/assets/logo.png`, `docs/assets/readme-banner.webp`, `docs/assets/social-preview.jpg` |
| repo-bootstrap (re-run) | `$GENIMAGE brand --name N --tagline "T" --from-logo --out-dir docs/assets` | banner + social regenerated; existing logo reused |
| gh-profile (hero banner) | `$GENIMAGE banner --name LOGIN --tagline "T" --variant both --out-dir assets` | `assets/banner-dark.webp`, `assets/banner-light.webp` |

These filenames are a contract — callers reference them in READMEs and verify
steps. Never rename the outputs.

## Common issues

**`ERROR: OPENAI_API_KEY is not set`**: walk the key chain in step 1; the
script intentionally never prompts.

**`Images API returned 4xx`**: 401 — bad or expired key; 400 mentioning size —
use a supported size and crop locally; 429 — the env key may be billing-capped;
prefer the 1Password key over a stale env var.

**`quantized logo is N bytes, still >= 1 MiB`**: the mascot came out
photographic or noisy — regenerate with "flat illustration, bold clean shapes"
wording in the concept.

**`could not encode ... under 1 MiB`**: same cause for banners — regenerate
with a flatter, less textured prompt.

**Wrong text in the image**: quote the exact name/tagline strings in the
prompt and regenerate; text accuracy varies per generation.

**`uv: command not found`**: install uv (https://docs.astral.sh/uv/) — the
script declares its own dependencies inline, so uv is the only prerequisite.
