# skills

A [Claude Code](https://claude.com/claude-code) plugin marketplace of small,
focused skills. Each plugin installs independently.

## Plugins

| Plugin     | What it does                                                                 | Prerequisites |
| ---------- | ---------------------------------------------------------------------------- | ------------- |
| `slop-cop` | Check a file or text for LLM-generated prose tells and report the violations. | None; a `SessionStart` hook bootstraps its prebuilt binary. |
| `codex`    | Get a second opinion from OpenAI's Codex CLI on hard debugging or design problems. | The `codex` CLI on `PATH`. |
| `repo-bootstrap` | Scaffold a new repo with proven conventions: agent docs, Claude Code settings, guard hooks, plus an opinionated Python packaging layer. | `uv` (for the hooks and the Python layer); `gh` recommended. |

## Install

Add the marketplace, then install the plugins you want:

```
/plugin marketplace add yasyf/skills
/plugin install slop-cop@skills
/plugin install codex@skills
/plugin install repo-bootstrap@skills
```

To try it from a local checkout before publishing:

```
/plugin marketplace add ~/Code/skills
```

## slop-cop

Wraps the [`slop-cop`](https://github.com/yasyf/slop-cop) CLI. Ask the agent to
"check this file for slop" (or name a path) and it runs `slop-cop check`,
auto-detecting the input language and masking non-prose regions, then reports
the violations grouped by category. It only rewrites the file if you ask. A
`SessionStart` hook fetches the host-matched binary from the latest
`yasyf/slop-cop` release into the plugin's persistent data dir, so no Go
toolchain is needed and there is no first-call download stall.

## codex

A second-opinion escape hatch for when you're stuck after a couple of failed
attempts. The skill gathers full context, hands it to `codex exec`, and returns
a structured summary you can verify. It needs the OpenAI Codex CLI installed and
authenticated on your machine.

## repo-bootstrap

Bootstraps a new project or repo from battle-tested conventions, distilled from
[captain-hook](https://github.com/yasyf/captain-hook)'s setup history. A base
layer every repo gets (AGENTS.md/CLAUDE.md/STYLEGUIDE.md, README skeleton,
Claude Code settings, [semble](https://pypi.org/project/semble/) code search,
[capt-hook](https://github.com/yasyf/captain-hook) guard hooks) plus an optional
Python layer (uv with the `uv_build` backend, Click CLI, loguru, pytest, pyright
strict, [Great Docs](https://posit-dev.github.io/great-docs/) on GitHub Pages,
and tag-driven PyPI releases via trusted publishing). Templates render through a
deterministic scaffold script; the agent fills in naming, prose, and follow-up
edits. Say "bootstrap a new repo" or "scaffold a new Python package".

## License

MIT. See [LICENSE](LICENSE).
