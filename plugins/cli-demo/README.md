# cli-demo

**Hand-typed demo GIFs lie, and they go stale.** Render an animated SVG terminal demo from a real CLI run instead of re-recording by hand whenever your output changes.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install cli-demo@skills
```

- Docker on macOS or Linux ARM, where renders run in a `linux/amd64` container; native Linux `x86_64` needs no Docker.
- On Windows, run inside WSL2 with Docker; there's no native path.
- `evp` 0.13.0 is fetched automatically into the plugin's persistent data dir on session start, so no manual install is needed.

## Use it

### Record a README demo for a published CLI

Hand-typed demo GIFs lie, and they go stale the first time your output changes. With `cli-demo` installed, ask:

```text
Record an animated terminal demo of `trimmy --help` for the README
```

The skill writes `.cli-demo/demo.tape` and renders it with `evp`, running natively on Linux `x86_64` or inside a cached `linux/amd64` Docker image elsewhere. It inspects still keyframes `f1.png`, `f2.png`, and `f3.png` for setup leakage and a payoff that actually rendered. It edits the tape and re-renders in a loop for up to about 4-5 passes. Once clean, it delivers `.cli-demo/demo.svg` and offers to copy it to `docs/demo.svg` and wire it into your README.

### Demo an unpublished CLI from the current repo

Recording a tool you haven't published yet usually means fighting container paths and leaking build output into the recording. With `cli-demo` installed, ask:

```text
Make a terminal demo of this repo's CLI running its main command
```

The skill mounts your repo at `/work` inside the render container and installs your CLI inside a hidden setup block in `.cli-demo/demo.tape`, keeping `cd`, `clear`, and install commands out of the rendered frames. The first Docker render builds and caches a base image and a per-install demo image so subsequent passes in the refine loop reuse them.

## What's inside

| Piece | Kind | What it does |
| :--- | :--- | :--- |
| `cli-demo:cli-demo` | skill | Writes an evp `.tape` for your CLI, renders it, inspects keyframes, and refines until the demo is clean |
| `SessionStart` hook | hook | Fetches the evp binary into the plugin's data dir so the first render doesn't wait on a download |
| `scripts/evp-run.sh` | script | Renders a tape natively on Linux `x86_64` or inside a cached `linux/amd64` Docker image elsewhere; flags `--install`, `--mount-bin`, `--workdir` |
| `scripts/install-binary.sh` | script | Downloads the pinned evp binary and prints its path; idempotent |

Part of [cc-skills](../../README.md).
