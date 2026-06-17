# evp 0.13.0 — verified behavior notes

evp's shipped README documents only the three output formats. The tape language is
"VHS-format", but evp implements a **subset** of VHS, so copying VHS examples produces
tapes that silently misbehave (a literal `Enter` gets typed, a `Wait` hangs, an `Output`
is rejected). Everything below was verified against the v0.13.0 binary with `evp validate`
and real renders — trust this over VHS docs.

## Directive support (verified)

**Works:** `Output` (`.svg` / `.gif` / `.json`), `Require`, `Sleep`, `Hide` / `Show`,
`Type`, `Enter`, `Wait`, `Wait /regex/`, `Screenshot` (`.png` only), `Env`, and
`Set Shell | Theme | FontSize | Width | Height | Padding | Margin | MarginFill |
WindowBar | TypingSpeed | Framerate | WaitTimeout`.

**Rejected (VHS features evp does not implement):**
- `Output` with `.stats`, `.svgz`, `.mp4`, `.webm`, `.png`, `.txt`, `.ascii` — only
  `.svg` / `.gif` / `.json` render.
- `Screenshot foo.svg` / `foo.json` — Screenshot **must** end in `.png`.
- `Wait+Screen`, `Wait+Line`, `Wait@2s` — `+Screen`/`@2s` parse as key combos, not Wait
  modifiers. Only bare `Wait` and `Wait /regex/` exist.

**Source of truth:** `evp validate <tape>` parses without rendering and prints the first
error — use it to check any directive you're unsure about, rather than guessing from VHS.
When in doubt about whether a `Set` key exists, validate a one-line tape that uses it.

## Two rules that bite

- **One key per line.** `Type "cmd"` then `Enter` on the next line. `Type "cmd" Enter`
  types the literal word "Enter" and the command never runs (the keyframe shows
  `$ cmd Enter` with no output).
- **`Wait` scans the *visible* screen.** It matches the pattern only while it is on
  screen, so:
  - Match *trailing* output (the last thing printed), never an early line of long output —
    e.g. `Wait /Usage/` on a 50-line `--help` times out because `Usage:` scrolled off.
  - A timed-out `Wait` pauses the **rendered animation** for the whole `WaitTimeout`, not
    just the build — keep `WaitTimeout` modest and prefer `Sleep` for fast commands.
  - The pattern must not appear in the line you typed, or `Wait` matches the echoed input
    and returns immediately.

## CLI surface (verified via `evp --help`)

- `evp <tape>` — render (output formats come from the tape's `Output` directives).
- `evp validate <tape>` — parse-only syntax check.
- `evp themes` — list the bundled theme presets (349 in 0.13.0; names are case- and
  space-sensitive, e.g. `Catppuccin Mocha`).
- `evp --run-test-script` — render the built-in demo to `./evp-test.gif`; a zero-dependency
  way to confirm an install works.
- Flags: `-o/--output <path>` (override/add outputs, repeatable), `--dump-json <path>`,
  `--no-embed-fonts`, `--no-system-fonts`, `--mimic-vhs`, `--log-level`.

## Running in a container (the macOS/ARM path)

- The only published artifact is `x86_64-unknown-linux-musl` (a ~13.75 MB static-pie ELF).
  It runs fine on a glibc `debian:stable-slim` base — no Alpine needed.
- `Set Shell bash` spawns the **container's** bash, so the base image must have `bash`.
- evp is headless (embedded libghostty, fonts embedded) — no X/GPU/ffmpeg needed; the first
  keyframe inspection will surface a render problem if one ever appears.
- Outputs are written by the container; `evp-run.sh` runs `--user $(id -u):$(id -g)` with
  `HOME=/tmp` so files land user-owned and CLIs have a writable HOME.

### Getting the demoed CLI onto PATH (`--install` gotchas)
The install command runs on `debian:stable-slim`, so it must bring its own runtime:
- apt tools land on `/usr/bin` (on PATH): `apt-get update && apt-get install -y jq`.
- Some packages install outside PATH — e.g. `cowsay` → `/usr/games`. Add it:
  `... && ln -s /usr/games/cowsay /usr/local/bin/`.
- Python tools: Debian is PEP-668 externally-managed, so
  `apt-get install -y python3-pip && pip3 install --break-system-packages <pkg>`.
- A host binary built on macOS is Mach-O/arm64 and gives `exec format error` in the
  linux/amd64 container — only `--mount-bin` a binary built for linux/amd64.

## Output sizing levers

For the same demo, the **SVG is smaller than the GIF** (the figlet demo: ~21 KB SVG vs
~54 KB GIF), and SVG text stays crisp/selectable — so SVG is the default deliverable. If an
SVG is still too big, in rough order of preference: shorten the demo (fewer/shorter
`Sleep`s), lower `Set Framerate`, shrink `Width`/`Height`, then as a last resort render with
`--no-embed-fonts` (via `evp-run.sh … -- --no-embed-fonts`). `--no-embed-fonts` drops the
embedded font data — smaller, but the SVG then relies on the viewer having the font, so it's
less portable; keep fonts embedded for anything you publish.
