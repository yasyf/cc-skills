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
- **Parallelize Independent Work.** Keep verbatim. Stance: the main session is an
  orchestrator, not an executor — sequential is the exception; when unsure, fan
  out. Dispatch ladder, cheapest first: batch independent tool calls in one
  message, parallel subagent calls for ad-hoc investigations, dynamic workflow as
  the default for substantive multi-step work (detailed in CLAUDE.md § Plan
  Execution & Orchestration), `TeamCreate` for long-running peers; the
  single-step exception still routes through one subagent call, never the
  orchestrator acting directly.
- **Writing Plans.** Keep verbatim. The five-part plan shape (Context, Approach,
  Potential Pitfalls, Workflow Plan, Verification). The Workflow Plan part is
  required in every plan — `Phase | Shape | Agents | Verification` table, or one
  line saying everything stays at the main-agent level; a plan without it is
  incomplete.
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
  Writing docs; Git). The **Testing** rule carries a `TODO(bootstrap)`: fill in where the suite
  lives and the exact command (captain-hook: "The suite lives in `tests/`; run it
  with `uv run pytest`"). Add project-specific rules in the same format — e.g.
  captain-hook adds a **Docs** rule ("Any public API change must keep
  `uv run great-docs build` green") and a **Releases** rule.

## CLAUDE.md

`@AGENTS.md` (so AGENTS.md stays the single, tool-agnostic source of conventions) followed by
a short **Claude-only** block — guidance that names Claude-specific tools and so doesn't
belong in the shared AGENTS.md:

- `## Claude-Specific Rules` — one bullet mandating `AskUserQuestion` for the clarifying
  questions AGENTS.md § Ask Before Assuming calls for (concrete picks beat inline prose).
- `## Task Tracking` — the `pending → in_progress → completed` flow via `TaskCreate`/
  `TaskUpdate`; cited by the `tasks.py` Stop gate (keep the heading in sync with that hook).
- `## Plan Execution & Orchestration` — keep verbatim. The session-level orchestrator
  contract: substantive work runs as dynamic workflows (`Workflow` tool, standing
  authorization); only trivial edits, single reads, and single targeted lookups stay
  at the main-agent level; every delegated agent runs at max model/effort; every plan
  carries the `## Workflow Plan` section AGENTS.md § Writing Plans requires.

Keep all three terse. Anything tool-agnostic still belongs in AGENTS.md, not here.

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
from the root checkout, append `DEBUG=1` to `.env`, `direnv allow`,
`uv sync --extra dev --inexact` (python layer only; the scaffold strips uv lines
on base), then `jj git init` and the jj user identity (rendered from the author
name/email supplied at scaffold time). `teardown` and `run` stay empty until
needed.

## README.md

Fixed structure, each section with a `TODO(bootstrap)` describing what good looks
like. Write the section prose through the `writing-docs` skill — its
technical-builder voice governs the pitch and why-bullets; procedure steps stay
imperative.

1. **Banner** — `![{PROJECT_NAME} banner](docs/assets/readme-banner.png)` directly
   under the H1, generated during bootstrap by the skill's `genimages.py`
   (project name + tagline left, mascot right, dark background).
   With feature `pypi` the python template renders an absolute
   `{REPO_URL}/raw/main/` prefix so PyPI shows it too — relative paths never
   render on PyPI. Delete the line if image generation was skipped.
2. **Badges row** — CI shield pointing at `actions/workflows/ci.yml` on `main`,
   and a license badge (omitted with license `none`), both built from the values
   supplied at scaffold time (GitHub user, project name, license ID). Add more
   (PyPI, docs) as they exist. On a private repo, shields.io can't read workflow
   status — drop the CI/docs badges or expect them broken.
3. **Pitch** — expand the one-line description into two sentences: what it is,
   and the one property that makes it worth using.
4. **Install** — the shortest path from zero to running, one copy-pasteable
   command (captain-hook: `uvx capt-hook`).
5. **Quickstart** — a complete working example runnable in under 30 seconds,
   with expected output shown. Not a feature tour.
6. **What problems does this solve?** — 3-4 bullets, each naming a concrete pain
   and how this addresses it. Pains, not features.
7. **License** — license ID + link to `LICENSE` on the repo. Omitted with
   license `none`.

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

- **PolyForm-Noncommercial-1.0.0** (default for public repos): source-available, noncommercial
  use only — what captain-hook itself uses. The scaffold renders `LICENSE` from
  its bundled template, prepending a `Required Notice:` line with the author
  name and repo URL supplied at scaffold time.
- **MIT**: for permissive open source. Also rendered from a bundled template,
  filling the year and author name.
- **Apache-2.0**: when an explicit patent grant matters.
- **none** (default for private repos): pass `LICENSE_ID=none` — no LICENSE file,
  no README badge or License section, no `license` field in `pyproject.toml`.
  Unlicensed means all rights reserved.

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
  `"autoUpdate": true` so clones stay fresh) and enables `codex@skills` (the
  `commands.py` failure nudge), `slop-cop@skills` + `llm-prompts@skills` (the
  `prompts.py` nudge), and `writing-docs@skills` (the `docs.py` nudge). Anyone who
  trusts the folder gets the marketplace registered and the plugins enabled after a
  one-time consent prompt. Removing a nudge from its hook file? Remove its plugin
  keys in the same edit (n.b. `slop-cop@skills` is shared by `prompts.py` and
  `docs.py`).
- `"hooks"`: four events — `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
  `Stop` — each running `uvx capt-hook run <Event>`. capt-hook discovers the hook
  definitions in `.claude/hooks/`: `commands.py`
  (blocks `git stash` and unpiped `grep`, nudges toward `/codex` after 2
  failures — delete that nudge if the codex plugin isn't installed),
  `stewardship.py` (NLP nudge against dismissing issues as "pre-existing"),
  `prompts.py` (llm-prompts nudge on prompt-shaped edits), `docs.py`
  (writing-docs nudge on doc edits), and `tasks.py` (end-of-turn task
  discipline) — see `reference/hooks.md` for each.
  Add project rules as new files in `.claude/hooks/`; each carries inline
  `tests = {...}` runnable with `uvx capt-hook test`.

**settings.local.json pattern**: `.claude/settings.local.json` is gitignored
(see `.gitignore`, alongside `.context/` and `.env*`). Personal, per-machine
config goes there — typically a broader permissions allowlist; the checked-in
`settings.json` stays conservative and shared.
