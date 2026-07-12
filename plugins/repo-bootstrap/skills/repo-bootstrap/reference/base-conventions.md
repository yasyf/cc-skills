# Base Layer Conventions

How to edit the base-layer files after scaffolding. Every `TODO(bootstrap)` marker
must be resolved before the first commit — find leftovers with
`rg -n 'TODO\(bootstrap\)'`. Note: the python layer overrides `AGENTS.md`,
`STYLEGUIDE.md`, `README.md`, and `.claude/settings.json` at the same destinations,
so when that layer is active, edit those four against its richer versions instead.
Worked example throughout: project `captain-hook`, dist+CLI `capt-hook`, package
`captain_hook`.

**Rendered artifacts.** `AGENTS.md`, `CLAUDE.md`, and `.claude/settings.json` are
generated, never hand-edited. Scaffold writes a `.claude/fragments/<target>/` layout
dir — a `layout.toml` listing repo-local `*.fragment.*` prose pieces plus imports of
shared `cc-skills:<name>` fragments (`cc-skills:ccx`, `cc-skills:claude-rules`,
`cc-skills:version-control`, `cc-skills:settings-base`, …), with an explicit `[sources.cc-skills]` pointing at
`github:yasyf/cc-skills@main` — then the post-write step runs `cc-guides render`, which
composes each `<target>`: markdown/shell get a line-1 `GENERATED …` marker, JSON is
deep-merged and written raw (tracked in `.claude/fragments/cc-guides.lock`). Edit the
fragments and re-run `cc-guides render`; a `Guides` GitHub workflow
(`.github/workflows/guides.yml`) checks freshness on push and re-renders daily / on a
cc-guides release. The layout schema, composition rules, and marker contract live in
the [cc-guides](https://github.com/yasyf/cc-guides) README — this repo keeps no
rendering or checking machinery. The README seeds under `templates/_partials/*.md` are a
separate mechanism: they carry no cc-guides directives and are inlined at scaffold time
(scaffold-once material customized per repo).

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
- **Compact Context (ccx).** The `cc-skills:ccx` import in every layer's
  `AGENTS.md` layout, composed by `cc-guides render` (the fragment body lives upstream
  in `cc-skills` `plugin/guides/md/ccx.md`) where the old per-project `## Code Search` section
  used to sit. It makes `cc-context` — the `ccx` CLI and the `mcp__cc-context__*`
  MCP tools — the default for reading/searching/reviewing code, because it returns
  token-bounded output and the `ccx` capt-hook guard pack blocks the token-heavy
  primitives. The ladder: `ccx repo overview` (orient), `ccx code search` (intent,
  semble-backed), `ccx code symbol`/`grok` (a named symbol), `ccx code grep`
  (literal), `ccx repo find` (list files), `ccx code outline` + `ccx code read
  --section` (read), `ccx code edit` (hash-verified write), `ccx vcs diff`/`show`/
  `history` (review changes, one commit, a file's evolution), `ccx repo locate`
  (find a repo/module/package on disk), `ccx vcs ship` (commit + push + watch CI),
  `ccx exec` (compose/post-process), `ccx format` (re-encode JSON output). The MCP
  covers the query surface (entries 1–8) plus exec and format (as `BashFormat`) —
  **not** `ccx vcs ship`/`show`/`history` or `ccx repo locate`, which are CLI-only.
  Durable prose cites code as `path:line#hash`; ccx re-anchors the cite by content
  even after the file drifts. LSP for exhaustive/structural answers, `Grep`/`Glob`
  only for literal content in non-source files. The facade (semble + tilth) ships inside the
  `cc-context@cc-context` plugin enabled in `.claude/settings.json`, **not** a per-project
  `.mcp.json` server — and the same plugin attaches the `ccx` guard pack per session, so
  the `ccx` heading and the `cc-context@cc-context` plugin are the cross-reference invariant,
  not `.mcp.json`. The fragment body is owned by `cc-skills`; change it there, not here.
- **Style.** Exactly `@STYLEGUIDE.md` under `## Style` — an embed, not a link.
  Don't duplicate style rules into AGENTS.md.
- **General Rules.** Bold-bullet block: each rule is `**Name.** One or two
  sentences.` Keep the stock rules (Minimal changes; Match surrounding code; No
  defensive coding; Search before writing; Code stewardship; Observe, don't
  infer; Don't use external failures as an excuse to stop; Mechanical linting;
  Writing docs; Version control; Watch CI after every push). The **Version
  control** and **Watch CI** rules ship as the `cc-skills:version-control` import
  in every layer's AGENTS.md layout, composed by `cc-guides render` (jj-preferred;
  watch CI with `gh run watch` after every push). The **Testing** rule carries a `TODO(bootstrap)`: fill in where the suite
  lives and the exact command (captain-hook: "The suite lives in `tests/`; run it
  with `uv run pytest`"). Add project-specific rules in the same format — e.g.
  captain-hook adds a **Docs** rule ("Any public API change must keep
  `uv run great-docs build` green") and a **Releases** rule.

## CLAUDE.md

The layout imports the shared `cc-skills:claude-rules` guide instead of shipping a repo-local
fragment, so every repo renders the identical block from one upstream source. It renders as `@AGENTS.md` (so
AGENTS.md stays the single, tool-agnostic source of conventions) followed by a short
**Claude-only** block — guidance that names Claude-specific tools and so doesn't belong in the
shared AGENTS.md:

- `## Claude-Specific Rules` — one bullet mandating `AskUserQuestion` for the clarifying
  questions AGENTS.md § Ask Before Assuming calls for (concrete picks beat inline prose).
- `## Task Tracking` — the `pending → in_progress → completed` flow via `TaskCreate`/
  `TaskUpdate`; cited by the `tasks.py` Stop gate (keep the heading in sync with that hook).
- `## Plan Execution & Orchestration` — keep verbatim. The session-level orchestrator
  contract: substantive work runs as dynamic workflows (`Workflow` tool, standing
  authorization); only trivial edits, single reads, and single targeted lookups stay
  at the main-agent level; delegated agents are routed by the **Models** table —
  opus-4.8 `xhigh` by default (when in doubt, opus; implementation delegates here
  rather than editing inline on fable), fable-5 for orchestration, design review,
  hard planning, all prose/writing (never down-route writing), sensitive or
  error-prone implementation, review-findings synthesis, and as the escalation
  target for every lane (context-window pressure is not a routing cue), sonnet-5
  for recon (never haiku except single-fact mechanical steps), gpt-5.6-sol via the
  codex skill for code/diff review, security review/audit and verification of
  security-sensitive code (auth, input validation, crypto, secrets — implementing
  it stays fable), bug diagnosis, well-scoped edits to existing code, second
  opinions, imagegen, and rote throwaway work (gpt-5.6-luna sanctioned for the
  rote/bulk lane; from workflow routing stages: the
  `codex:codex-wrapper` agent; `Skill(codex)` works everywhere); defaults, not limits —
  escalation means fable;
  the unexpected checks back — a delegated agent hitting a task-shape surprise
  (scope change, invalidated assumption, task not as described) stops and
  returns findings plus 2-4 options for the fable orchestrator to pick, never
  improvising a detour or punting the decision to a cheaper model (transient
  failures stay autonomous); effort `xhigh` by default (fable implementation may
  run `high`), `max` only after xhigh falls short, verification at same-or-higher
  tier with table-routed gpt-5.6-sol lanes counting as same-tier; every plan's
  `## Workflow Plan` table names each phase's model and effort (AGENTS.md
  § Writing Plans).

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
{ "mcpServers": {} }
```

Empty by default. Code search no longer ships a per-project `semble` MCP server here —
the `cc-context` facade (semble + tilth, surfaced as `ccx` and the
`mcp__cc-context__*` MCP tools) ships inside the `cc-context@cc-context` plugin enabled in
`.claude/settings.json`, so every trusted clone gets it without a project-scoped
server. The AGENTS.md **Compact Context (ccx)** section and the General Rules
"Search before writing" rule both point at `ccx`, not at this file; `.mcp.json` stays
here only as the seam for any genuinely project-specific MCP server a repo later adds.

## .superset/config.json (extra `superset`)

Worktree bootstrap for the superset tool, scaffolded when the `superset` extra is
chosen: its `setup` commands run when a new worktree is cloned — copy `.env*`
from the root checkout, append `DEBUG=1` to `.env`, `direnv allow`,
`uv sync --extra dev --inexact` (python layer only; the scaffold strips uv lines
on base), then `jj git init` and the jj user identity (rendered from the author
name/email supplied at scaffold time). `teardown` and `run` stay empty until
needed.

## README.md

The skeleton — section order, opener register, get-started rules, use cases,
previews, footer — lives in the writing-docs skill's `references/readme.md`.
Write every section through that spec; this file carries only the bootstrap
mechanics layered on top:

- **Banner path + raw-URL prefix.** The template renders the banner inside the
  H1 from `docs/assets/readme-banner.webp`, generated in Phase 3 by the
  gen-image skill's brand pipeline. With feature `pypi` the python template
  prefixes `{REPO_URL}/raw/main/` on the banner and demo `src` — relative paths
  never render on PyPI. Escape hatch: image generation skipped → strip the
  image from the H1, leaving `# {PROJECT_NAME}`.
- **Private-repo shields caveat.** shields.io can't read workflow status on a
  private repo — drop the CI/docs badges or expect them broken.
- **TODO map.** The scaffolded `TODO(bootstrap)` markers, top to bottom: the
  opener (fragment + expansion sentence), the get-started demo (real run,
  generator committed at `docs/scripts/demo.sh` or `.cli-demo/demo.tape`), the
  agent block's first concrete goal, the use cases, then either the docs
  teasers (feature `docs`) or the inline tail, and the optional `Status:` line.
- **Feature `docs` off → inline branch.** No "More in the docs" teaser; the
  README carries its how-to and reference content inline, per the writing-docs
  skill's `references/standalone-readme.md`.

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

This is a **rendered artifact**, composed by `cc-guides render` from
`.claude/fragments/.claude/settings.json/`: `cc-skills:settings-base` deep-merged with
the layer variant (`settings-python` / `settings-go` / `settings-swift`) and a
placeholder-free `{}` `settings-overrides.fragment.json` for repo-specific additions. Never
hand-edit `.claude/settings.json`; the shared fields below live in the `cc-skills` pack
(`plugin/guides/json/`) — edit them there — and per-repo overrides go in
`settings-overrides.fragment.json` (then re-run `cc-guides render`). Field by field:

- `"effortLevel": "max"`, `"ultracode": true`, `"alwaysThinkingEnabled": true`,
  `"showThinkingSummaries": true` — maximum reasoning effort on every turn, with
  thinking summaries surfaced. Keep unless the project is trivial.
- `"includeGitInstructions": false` — drop the built-in git boilerplate; AGENTS.md
  § General Rules carries the version-control conventions instead (jj-preferred).
- `"env"`: `"ENABLE_TOOL_SEARCH": "true"` (deferred-tool discovery),
  `"ENABLE_LSP_TOOL": "1"` (the LSP tool that AGENTS.md Compact Context (ccx) routes
  structural queries to), `"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"` (enables
  `TeamCreate` for the Parallelize Independent Work section),
  `"CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1"` (always think at full depth), and
  `"JJ_CONFIG": ".claude/jj-config.toml"` (the python variant also sets
  `"TY_OUTPUT_FORMAT": "concise"` + `"TY_CONFIG_FILE"` to quiet ty) — points jj at the
  scaffolded repo-local config (user identity, `difft` diffs, `mergiraf` merges,
  watchman snapshot triggers off). Phase 0 creates the colocated jj repo this
  config governs (`jj git init --git-repo .`, run right after `git init -b main`),
  so jj is live from the first commit; jj writes its own `.jj/.gitignore`, so
  `.jj/` needs no `.gitignore` entry.
- `"permissions"`: `"allow"` lists only read-only commands (`cat`, `find`,
  `gh api`, `gh pr diff`, `gh pr view`, `git log`, `git status`, `head`, `jq`,
  `ls`, `rg`, `wc` — each as `Bash(cmd:*)`). Philosophy: the checked-in allowlist
  never grants writes; anything mutating still prompts. `"defaultMode": "auto"`.
- `"extraKnownMarketplaces"` + `"enabledPlugins"`: registers the
  [yasyf/cc-skills](https://github.com/yasyf/cc-skills) plugin marketplace (with
  `"autoUpdate": true` so clones stay fresh) and enables `codex@skills` (the
  `commands.py` failure nudge), `slop-cop@skills` + `llm-prompts@skills` (the
  `prompts.py` nudge), `writing-docs@skills` (the `docs.py` nudge), and
  `cc-context@cc-context` (the `ccx` code-search facade the AGENTS.md Compact Context
  section routes to; the same plugin also attaches the `ccx` guard pack per session). It also
  registers the [yasyf/cc-notes](https://github.com/yasyf/cc-notes) and
  [yasyf/cc-context](https://github.com/yasyf/cc-context) marketplaces and
  enables `cc-notes@cc-notes`, the git-native notes/tasks layer — so the
  `using-cc-notes` skill loads on folder-trust even before `cc-notes init` runs.
  Anyone who trusts the folder gets the marketplaces registered and the plugins
  enabled after a one-time consent prompt. Removing a nudge from its hook file?
  Remove its plugin keys in the same edit (n.b. `slop-cop@skills` is shared by
  `prompts.py` and `docs.py`).
- No `"hooks"` key: `.claude/settings.json` carries no hook wiring. Hook dispatch
  is registered globally by the captain-hook plugin; which hooks fire per repo is
  selected by the packs enabled in `.claude/hooks/packs.toml` (see
  `reference/hooks.md`). Project-local rules still live in `.claude/hooks/*.py`,
  each carrying inline `tests = {...}` runnable with `uvx capt-hook test`.

**settings.local.json pattern**: `.claude/settings.local.json` is gitignored
(see `.gitignore`, alongside `.context/` and `.env*`). Personal, per-machine
config goes there — typically a broader permissions allowlist.
