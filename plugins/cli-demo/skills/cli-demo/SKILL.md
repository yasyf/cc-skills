---
name: cli-demo
description: Generate an animated SVG terminal demo of a CLI using evp — write a .tape, render it, inspect the keyframes, and refine in a loop until it looks good. Use when the user asks to "make/record a terminal demo of <cli>", wants an "animated SVG/GIF demo of a command", a "VHS-style terminal recording", or a demo GIF/SVG for a README. Renders natively on Linux x86_64 and via Docker (linux/amd64) on macOS/ARM.
allowed-tools: Bash, Read, Write, Edit
---

# CLI Demo (evp)

Produce a polished **animated SVG** of a CLI in action, and iterate until it looks
good. Under the hood this drives [`evp`](https://github.com/HalFrgrd/evp): you write a
`.tape` script, render it, look at still keyframes, and refine. It is a **loop**, not a
one-shot — most demos take a few passes.

Two facts shape the workflow: evp ships only a Linux x86_64 binary, so on macOS/ARM the
render runs inside a `linux/amd64` Docker container (handled for you by `evp-run.sh`);
and evp runs the demoed command inside its **own** embedded shell, so the CLI must be
present there — on the host PATH natively, or installed into the container.

## 1 — Gather guidance

Settle these before writing a tape (ask the user what you can't infer — never guess the
install command):

- **Invoke & payoff** — the one command worth showing, and the distinctive output that is
  the demo's "wow" (also handy as a `Wait` pattern if you pace a slow command with `Wait`).
- **How the CLI reaches the render environment** (pick one):
  - *Published tool* → a container install command, e.g.
    `--install "apt-get update && apt-get install -y httpie"`. The image is
    `debian:stable-slim`, so bring the runtime too for language tools
    (`... && apt-get install -y python3-pip && pip3 install --break-system-packages <pkg>`).
  - *This repo's own (unpublished) CLI* → install the runtime via `--install`, then
    install the package from the mounted repo inside a **Hide** block in the tape (the
    repo is mounted at `/work`): `pip3 install --break-system-packages -e /work`,
    `npm link`, `cargo install --path /work`, etc.
  - *Prebuilt linux/amd64 binary* → `--mount-bin ./dist/tool` (must be built for
    linux/amd64 — a macOS build gives `exec format error` in the container).
  - *Native Linux x86_64 host* → nothing; evp uses the host PATH directly.
- **Story & look** — 2–4 beats (setup hidden → action → payoff). Optionally a theme
  (`evp themes` lists them), dimensions, font size.

## 2 — Resolve evp

The plugin's `SessionStart` hook pre-fetches the binary, and `evp-run.sh` downloads it
lazily if needed, so you usually don't touch this. To get the path directly:

```bash
EVP="$(bash "${CLAUDE_PLUGIN_ROOT}/scripts/install-binary.sh")"   # prints abs path
```

## 3 — Write the tape

Set up the working dir and copy the template:

```bash
mkdir -p .cli-demo
cp "${CLAUDE_PLUGIN_ROOT}/skills/cli-demo/assets/demo.tape.tpl" .cli-demo/demo.tape
```

Edit `.cli-demo/demo.tape`: replace the `<PLACEHOLDERS>`, shape the beats, and keep every
`Output`/`Screenshot` path under `.cli-demo/`. The conventions (already in the template):

- `Hide` every setup line — cd, clear, package install, local `pip/npm/cargo install` —
  and `Show` before the first real command.
- Fixed clean prompt via `Env PS1 "$ "`; `Require <tool>` so a missing CLI fails fast
  instead of hanging.
- Precede each `Screenshot fN.png` with a `Sleep` (deterministic) so the captured frame is
  stable; reach for `Wait` only for unpredictable-timing commands (see gotchas).
- End on the payoff held ~2–3s — a clean loop point — not mid-action.

**evp grammar gotchas** (it runs VHS-format tapes but implements only a subset, so don't
copy VHS examples blindly — verified against evp 0.13.0):

- One key per line: write `Type "..."` then `Enter` on the **next** line. `Type "..." Enter`
  types the literal word "Enter" and the command never runs.
- Wait is only `Wait` or `Wait /regex/` — no `Wait+Screen`, `Wait+Line`, or `Wait@2s`. It
  scans the **visible** screen, so match *trailing* output that's still on screen — never an
  early line of long output (a `--help`'s `Usage:` scrolls off and the Wait then burns the
  whole `WaitTimeout` *into the animation*). The regex must also not appear in the line you
  typed. Prefer `Sleep` for fast commands; use `Wait` for unpredictable ones.
- `Output`: `.svg` / `.gif` / `.json` only. `Screenshot`: `.png` only. No `.stats`/`.svgz`.
- Themes: `Set Theme "<name>"` from `evp themes` (300+ presets). `evp validate <tape>` parses
  a tape without rendering — a cheap syntax check before the slow render.

Typing ~45ms, sleeps 0.5–2s between commands, dimensions ~1200×600 at FontSize 22 are sane
defaults. For the full verified directive matrix, CLI flags, and container/`--install`
gotchas, see [`reference/evp-notes.md`](reference/evp-notes.md).

## 4 — Render

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/evp-run.sh" .cli-demo/demo.tape \
  --install "<container install command>"
```

Omit `--install` on a native Linux host or when using `--mount-bin <path>`. Add
`--workdir <dir>` if the demo shouldn't run from the repo root. The first Docker render
builds and **caches** the base image plus a per-install demo image (slow under
emulation); subsequent renders reuse them, so the inspect-and-refine loop is fast.

## 5 — Inspect

You can't watch an animated SVG, so inspect the stills and the file:

```bash
ls -la .cli-demo/demo.svg          # produced? reasonable size?
```

`Read` `.cli-demo/f1.png`, `f2.png`, `f3.png` and check:

- **No setup leakage** — no cd/clear/install/prompt churn visible (all should be `Hide`n).
- Prompt is the clean `$ `; the typed command isn't clipped by `Width`.
- **Payoff actually rendered** — no `command not found`, no traceback, not empty/half-streamed,
  and the command actually *ran* (a stray literal `Enter` on screen means a `Type "..." Enter`
  one-liner — split it onto two lines).
- Output isn't truncated vertically or ugly-wrapped; theme is legible (this catches
  headless-render problems early).
- `f3` is a clean held frame, not mid-animation.
- **Metrics**: keep total duration ~8–20s (estimate from the tape: sum of `Sleep`s plus
  roughly `TypingSpeed` × characters typed). SVG size reasonable — warn >1 MB, flag >2 MB →
  trim duration, lower `Set Framerate`, shrink `Width`/`Height`, or as a last resort render
  with embedded fonts dropped (`evp-run.sh … -- --no-embed-fonts`). A `wait timed out` line in
  the render output (or an unexpected multi-second freeze in the animation) means a `Wait`
  pattern never appeared — switch that `Wait` to `Sleep` or fix the pattern.

## 6 — Refine & repeat

Edit **only the tape** (the install/image is cached, so re-renders are quick) and re-run
step 4. Loop until the checklist passes — cap at ~4–5 iterations, then present the best
result and name any residual issues rather than looping forever on slow rebuilds.

## 7 — Deliver

- The deliverable: `.cli-demo/demo.svg`. Offer to copy it somewhere durable (e.g.
  `docs/demo.svg`) and to wire it into the README.
- An inline preview by `Read`-ing the final keyframe (`f3.png`) — an animated SVG can't
  render inline.
- One line of stats: duration · dimensions · file size.
- The tape path, so the user can hand-tweak and re-render with `evp-run.sh`.

Offer to add `.cli-demo/` to `.gitignore` (keep only the exported SVG). On Windows, run
inside WSL2 with Docker — there is no native path.
