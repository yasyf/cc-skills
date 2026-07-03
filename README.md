# skills

![cc-skills banner](docs/assets/readme-banner.webp)

A [Claude Code](https://claude.com/claude-code) plugin marketplace of small,
focused skills. Each plugin installs independently.

## Plugins

| Plugin     | What it does                                                                 | Prerequisites |
| ---------- | ---------------------------------------------------------------------------- | ------------- |
| `slop-cop` | Check a file or text for LLM-generated prose tells and report the violations. | None; a `SessionStart` hook bootstraps its prebuilt binary. |
| `codex`    | Get a second opinion from OpenAI's Codex CLI on hard debugging or design problems, or generate images with its `$imagegen` skill. | The `codex` CLI on `PATH`. |
| `repo-bootstrap` | Scaffold a new repo with proven conventions: agent docs, Claude Code settings, guard hooks, plus opinionated Python, Go, and Swift layers (SPM package/CLI or SwiftUI iOS app) with opt-in docs/release features. | `uv` (for the hooks and the Python layer); `gh` recommended; `gen-image` for brand images; the language toolchain for a language layer (Xcode + brew `swiftformat`/`swiftlint` for Swift). |
| `llm-prompts` | Guidance for writing effective LLM prompts and agent instructions, refreshed with current per-provider model behaviors. | None; `slop-cop` recommended for the post-edit prose check. |
| `writing-docs` | Write docs in Diataxis modes with a technical-builder voice, runnable code-sample rules, and a slop-cop prose pass. | None; `slop-cop` recommended for the prose pass. |
| `gen-image` | Generate project images — mascot logos, README banners (dark/light), social cards, illustrations — compressed locally to under 1 MiB. | `uv`; an `OPENAI_API_KEY` (the `codex` plugin's `$imagegen` is the no-key fallback). |
| `gh-profile` | Create or refresh a fancy GitHub profile README from your real repos and activity, with cron Actions that keep it fresh and an opt-in daily Claude refresh that summarizes recent commits and releases. | `gh` authenticated with `repo` + `workflow` scopes; `gen-image` for the banner; `repo-summaries` for the daily Claude refresh. |
| `repo-summaries` | Maintain a Claude-written summaries sidecar in any repo: a committed read-side module plus a config-driven daily refresh skill that turns real commit and release data into one-line suffixes. | `gh` for the raw-material recipes. |
| `cli-demo` | Generate an animated SVG terminal demo of a CLI with `evp`: write a `.tape`, render it, inspect the keyframes, and refine in a loop. | Docker (`linux/amd64`) on macOS/ARM; native on Linux x86_64. A `SessionStart` hook bootstraps the `evp` binary. |
| `agent-browser-with-cookies` | Run authenticated `agent-browser` sessions by reusing your local browser login: one Touch ID tap streams the sites' cookies into a fresh session. | macOS; the `cookiesync` CLI with its resident daemon; the `agent-browser` skill. |

## Install

Add the marketplace, then install the plugins you want:

```
/plugin marketplace add yasyf/cc-skills
/plugin install slop-cop@skills
/plugin install codex@skills
/plugin install repo-bootstrap@skills
```

To try it from a local checkout before publishing:

```
/plugin marketplace add ~/Code/cc-skills
```

Each plugin keeps its own README under `plugins/<name>/` for the details.

## License

MIT. See [LICENSE](LICENSE).
