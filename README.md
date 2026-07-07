# ![cc-skills](docs/assets/readme-banner.webp)

**The twelve skills Claude Code forgot to ship.** cc-skills is a plugin marketplace where one add covers prose linting, repo scaffolding, animated CLI demos, and image gen; each plugin installs on its own.

[![Claude Code marketplace](https://img.shields.io/badge/claude--code-marketplace-blueviolet)](https://code.claude.com/docs/en/plugin-marketplaces)
[![MIT license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

## Get started

```text
/plugin marketplace add yasyf/cc-skills
```

That one add registers the marketplace as `skills`. Pick plugins from the `/plugin` browser, or install one directly:

```text
/plugin install slop-cop@skills
```

Driving with an agent? Paste this:

```text
/plugin marketplace add yasyf/cc-skills
/plugin install slop-cop@skills
/plugin install repo-bootstrap@skills
/plugin install cli-demo@skills
```

---

## Use cases

### Catch LLM prose tells before they ship

You stopped noticing the hedge stacks, em-dash pileups, and `delve`s in your own READMEs and PR descriptions months ago. With slop-cop installed, ask:

```text
Run slop-cop on README.md
```

The skill runs the prebuilt `slop-cop` binary and reports each violation with its line and rule. A `SessionStart` hook fetches the binary, so no Go toolchain is required. Report-only by default; it rewrites only when you ask.

### Scaffold a Python, Go, or Swift repo with docs, hooks, and releases wired in

Every new repo means re-deriving the same conventions by hand, from agent docs and Claude Code settings to lint config and a release pipeline. With repo-bootstrap installed, ask:

```text
Bootstrap a new Python CLI repo called trimmy, with docs and PyPI releases
```

One pass renders `AGENTS.md`, Claude Code settings, guard hooks, brand images, and a uv Python layer with Click, pytest, ruff, and ty, plus a Great Docs site and tag-driven PyPI trusted publishing wired in. The Go and Swift layers ship Homebrew releases the same way.

### Record an animated SVG terminal demo of your CLI

Hand-typed demo GIFs lie, and they go stale the first time your output changes. With cli-demo installed, ask:

```text
Record an animated terminal demo of `trimmy --help` for the README
```

The skill writes a `.tape` script, renders it with `evp`, inspects still keyframes, and refines in a loop. On Linux x86_64 the render runs natively; on macOS/ARM it runs under Docker `linux/amd64`. The tape is committed, so the demo regenerates whenever the output drifts.

---

## Plugins

The table below is the full catalog; the `SKILL.md` inside each plugin dir carries prerequisites and the full flow.

| Plugin | What you get |
|---|---|
| [slop-cop](plugins/slop-cop) | Catches LLM prose tells in any file, from filler intensifiers to negation pivots. A `SessionStart` hook fetches the prebuilt binary. |
| [codex](plugins/codex) | A second opinion from OpenAI's Codex CLI when you're stuck, plus image generation via its `$imagegen` skill. Needs `codex` on `PATH`. |
| [repo-bootstrap](plugins/repo-bootstrap) | A new repo with agent docs, guard hooks, brand images, and opinionated Python, Go, or Swift layers, scaffolded in one pass. |
| [llm-prompts](plugins/llm-prompts) | Research-backed prompt-writing guidance, refreshed with current per-provider model behaviors. |
| [writing-docs](plugins/writing-docs) | Diataxis modes, a technical-builder voice, and the README skeleton this file follows. |
| [gen-image](plugins/gen-image) | Mascot logos, README banners, social cards, and illustrations, compressed locally to under 1 MiB. Needs an `OPENAI_API_KEY`. |
| [gh-profile](plugins/gh-profile) | A fancy GitHub profile README built from your real repos and activity, kept fresh by cron Actions. |
| [repo-summaries](plugins/repo-summaries) | A Claude-written summaries sidecar that turns real commit and release data into one-line suffixes. |
| [cli-demo](plugins/cli-demo) | Animated SVG terminal demos via `evp`. Write a tape, render, inspect keyframes, refine. |
| [agent-browser-with-cookies](plugins/agent-browser-with-cookies) | Authenticated `agent-browser` sessions off your local browser login, one Touch ID tap. macOS only. |
| [show](plugins/show) | Picks the right delivery surface for a deliverable, whether prose, AskUserQuestion, an Artifact page, or a live cc-present board. A hook flags wall-of-text dumps. |
| [cc-context](https://github.com/yasyf/cc-context) | Compact codebase-context tools, `ccx` over semble and tilth. Lives in its own repo, installs from this marketplace. |

## Hack on a local checkout

Point the marketplace at your clone to try changes before pushing:

```text
/plugin marketplace add ~/Code/cc-skills
```

cc-skills is licensed under [MIT](LICENSE).
