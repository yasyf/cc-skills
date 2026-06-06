# Base Layer Conventions

How to edit the base-layer files after scaffolding. Every `TODO(bootstrap)` marker
must be resolved before the first commit — find leftovers with
`rg -n 'TODO\(bootstrap\)'`. Note: the python layer overrides `AGENTS.md`,
`STYLEGUIDE.md`, `README.md`, and `.claude/settings.json` at the same destinations,
so when that layer is active, edit those four against its richer versions instead.
Worked example throughout: project `captain-hook`, dist+CLI `capt-hook`, package
`captain_hook`.

## AGENTS.md anatomy

The single canonical agent-conventions doc. Section by section:

- **Title + description.** `# {PROJECT_NAME} Development Guide` followed by the
  one-line description supplied at scaffold time. Expand the description to 1-3
  sentences naming what the project is and how it's consumed (e.g. captain-hook:
  "Published to PyPI as `capt-hook`; the CLI is `capt-hook`, run as `uvx capt-hook`").
- **Repository Structure.** ASCII tree with a trailing `# comment` per entry. The
  `TODO(bootstrap)` line wants the real top-level directories filled in — one line
  each, what *lives* there, not what it's named (`captain_hook/  # The package —
  events, conditions, primitives, transcript, CLI`). Keep `AGENTS.md` and
  `README.md` entries. Update this tree whenever directories move.
- **Ask Before Assuming.** Keep verbatim. Behavior contract: ambiguity → propose
  2-4 options or list assumptions; never guess.
- **Code Review Response (Plan Re-Entry).** Keep verbatim, including the
  "Plan follow-up questions" subsection. It encodes the full review-feedback
  protocol: delegate cite-gathering to an `Explore` subagent, draft a plan (not
  edits), inline every comment verbatim with anchors, cluster >5 comments into
  themes, end with a `# | file:line | verbatim | cluster` mapping table, and never
  implement before `ExitPlanMode`.
- **Parallelize Independent Work.** Keep verbatim. Dispatch table: dynamic
  workflow for substantive multi-step work, parallel subagent calls in one message
  for ad-hoc investigations, `TeamCreate` for long-running peers; single-step
  exception allows one subagent call.
- **Code Search.** Keep verbatim; it depends on `.mcp.json` (below). The decision
  table: `semble.search` for intent/symbol questions ("How do we do X?",
  "Where is `Foo` defined?"), `semble.find_related` for "code like this", LSP
  (`findReferences`/`incomingCalls`/`hover`/`goToImplementation`) when the answer
  must be exhaustive or structural, `Grep` only for literal string/comment content
  and non-source files, `Glob` for file patterns.
- **Style.** Exactly `@STYLEGUIDE.md` under `## Style` — an embed, not a link.
  Don't duplicate style rules into AGENTS.md.
- **General Rules.** Bold-bullet block: each rule is `**Name.** One or two
  sentences.` Keep the stock rules (Minimal changes; Match surrounding code; No
  defensive coding; Search before writing; Code stewardship; Observe, don't
  infer; Don't use external failures as an excuse to stop; Mechanical linting;
  Git). The **Testing** rule carries a `TODO(bootstrap)`: fill in where the suite
  lives and the exact command (captain-hook: "The suite lives in `tests/`; run it
  with `uv run pytest`"). Add project-specific rules in the same format — e.g.
  captain-hook adds a **Docs** rule ("Any public API change must keep
  `uv run great-docs build` green") and a **Releases** rule.

## CLAUDE.md

Exactly one line: `@AGENTS.md`. Rationale: every agent tool reads a different
filename, so AGENTS.md is the single source of truth and CLAUDE.md is a pointer.
Never add content here; it would fork the conventions.

## STYLEGUIDE.md

The base skeleton is language-agnostic: four **Core Principles** (Fail fast, fail
loud; Make invalid states unrepresentable; Minimal changes; Match surrounding
code) plus **Error Handling**, **Code Organization**, **Comments & Docstrings**,
and **Testing** sections phrased without language-specific syntax. Keep all of
these. The `TODO(bootstrap)` wants language-specific rules — naming, organization,
error handling, idioms — each with Good/Bad code examples. Grow Core Principles
to ~7 by prepending language idioms (the python layer's `STYLEGUIDE.md` is the
worked example: "Functional over imperative", "Match for dispatch", "Type
everything", most with `# Good` / `# Bad` fenced blocks). The skeleton's
guidance generalizes: minimal error-handling blocks, no catch-all handlers,
module order (imports, constants, type aliases, helpers, classes, functions),
export-control over naming conventions, strict test assertions with mocked
boundaries only.

## .mcp.json

```json
{ "mcpServers": { "semble": { "command": "uvx", "args": ["--from", "semble[mcp]", "semble"] } } }
```

Project-scoped MCP server giving semantic code search with zero install — `uvx`
fetches it on first use. The AGENTS.md Code Search section and the General Rules
"Search before writing" rule both assume this server exists; if you remove
`.mcp.json`, rewrite those sections too.

## .superset/config.json (extra `superset`)

Worktree bootstrap for the superset tool, scaffolded when the `superset` extra is
chosen: its `setup` commands run when a new worktree is cloned — copy `.env*`
from the root checkout, append `DEBUG=1` to `.env.local`, `direnv allow`,
`uv sync --extra dev --inexact` (python layer only; the scaffold strips uv lines
on base), then `jj git init` and the jj user identity (rendered from the author
name/email supplied at scaffold time). `teardown` and `run` stay empty until
needed.

## README.md

Fixed structure, each section with a `TODO(bootstrap)` describing what good looks
like:

1. **Badges row** — CI shield pointing at `actions/workflows/ci.yml` on `main`,
   and a license badge, both built from the values supplied at scaffold time
   (GitHub user, project name, license ID). Add more (PyPI, docs) as they exist.
2. **Pitch** — expand the one-line description into two sentences: what it is,
   and the one property that makes it worth using.
3. **Install** — the shortest path from zero to running, one copy-pasteable
   command (captain-hook: `uvx capt-hook`).
4. **Quickstart** — a complete working example runnable in under 30 seconds,
   with expected output shown. Not a feature tour.
5. **What problems does this solve?** — 3-4 bullets, each naming a concrete pain
   and how this addresses it. Pains, not features.
6. **License** — license ID + link to `LICENSE` on the repo.

## CHANGELOG.md

Keep a Changelog format, Semantic Versioning. Versions are git tags: when a `v*`
tag is cut, rename `[Unreleased]` to the version, start a fresh `[Unreleased]`
above it, and add a link ref at the bottom (the scaffold seeds
`[Unreleased]: {REPO_URL}/commits/main`; released versions link to
`/compare/vX..vY` or the tag). Group entries under `### Added` / `### Changed` /
`### Fixed` / `### Removed`. Entries describe user-visible change, not commits.

## Commits

Atomic and scoped — one logical change per commit, conventional prefixes. Real
examples from captain-hook:

- `refactor: convert the CLI from argparse to Click`
- `build(docs): add Great Docs toolchain (config, Quarto extensions, deps)`
- `chore: standalone repo scaffolding (…)`

## License

- **PolyForm-Noncommercial-1.0.0** (default): source-available, noncommercial
  use only — what captain-hook itself uses. The scaffold renders `LICENSE` from
  its bundled template, prepending a `Required Notice:` line with the author
  name and repo URL supplied at scaffold time.
- **MIT**: for permissive open source. Also rendered from a bundled template,
  filling the year and author name.
- **Apache-2.0**: when an explicit patent grant matters.

For any ID without a bundled template the scaffold writes nothing and prints a
`MANUAL` line; fetch the text from the SPDX license list using the exact SPDX id:

```bash
curl -fsS https://raw.githubusercontent.com/spdx/license-list-data/main/text/<SPDX-ID>.txt > LICENSE
```

(`gh api /licenses/<id> -q .body` also works, but only for the choosealicense set —
it 404s on PolyForm and other niche licenses.) Keep the README badge and License
section in sync with the chosen ID.

## .claude/settings.json

Field by field:

- `"effortLevel": "max"`, `"ultracode": true`, `"alwaysThinkingEnabled": true`,
  `"showThinkingSummaries": true` — maximum reasoning effort on every turn, with
  thinking summaries surfaced. Keep unless the project is trivial.
- `"includeGitInstructions": false` — drop the built-in git boilerplate; AGENTS.md
  § General Rules carries the git conventions instead.
- `"env"`: `"ENABLE_TOOL_SEARCH": "true"` (deferred-tool discovery),
  `"ENABLE_LSP_TOOL": "1"` (the LSP tool that AGENTS.md Code Search routes
  structural queries to), `"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"` (enables
  `TeamCreate` for the Parallelize Independent Work section),
  `"CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1"` (always think at full depth),
  `"TY_OUTPUT_FORMAT": "concise"` (trims ty LSP diagnostics if that plugin is
  installed), and `"JJ_CONFIG": ".claude/jj-config.toml"` — points jj at the
  scaffolded repo-local config (user identity, `difft` diffs, `mergiraf` merges,
  watchman snapshot triggers off).
- `"permissions"`: `"allow"` lists only read-only commands (`cat`, `find`,
  `gh api`, `gh pr diff`, `gh pr view`, `git log`, `git status`, `head`, `jq`,
  `ls`, `rg`, `wc` — each as `Bash(cmd:*)`). Philosophy: the checked-in allowlist
  never grants writes; anything mutating still prompts. `"defaultMode": "auto"`.
- `"extraKnownMarketplaces"` + `"enabledPlugins"`: registers the
  [yasyf/cc-skills](https://github.com/yasyf/cc-skills) plugin marketplace (with
  `"autoUpdate": true` so clones stay fresh) and enables `codex@skills` — the
  second-opinion skill that the `commands.py` failure nudge points at. Anyone who
  trusts the folder gets the marketplace registered and the plugin enabled after a
  one-time consent prompt. Removing the codex nudge from `commands.py`? Remove
  these two keys in the same edit.
- `"hooks"`: four events — `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
  `Stop` — each running `uvx capt-hook run <Event>`. capt-hook discovers the hook
  definitions in `.claude/hooks/`: `audit.py` (event audit log), `commands.py`
  (blocks `git stash` and unpiped `grep`, nudges toward `/codex` after 2
  failures — delete that nudge if the codex plugin isn't installed), and
  `stewardship.py` (NLP nudge against dismissing issues as "pre-existing").
  Add project rules as new files in `.claude/hooks/`; each carries inline
  `tests = {...}` runnable with `uvx capt-hook test`.

**settings.local.json pattern**: `.claude/settings.local.json` is gitignored
(see `.gitignore`, alongside `.context/` and `.env*`). Personal, per-machine
config goes there — typically a broader permissions allowlist; the checked-in
`settings.json` stays conservative and shared.
