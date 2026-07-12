# Hooks: understanding, testing, and tailoring the scaffolded capt-hook setup

## capt-hook in ten lines

[capt-hook](https://pypi.org/project/capt-hook/) (the captain-hook project) is a declarative
hook framework for Claude Code: hooks are data, declared with primitives like `block_command`,
`nudge`, `gate`, `hook`, `styleguide`, and `@on`, and each carries inline `tests = {...}` that
run anywhere — locally, in CI if you wire them in — in the same shape they run in
production. The captain-hook Claude Code plugin registers the wiring, not the repo: it ships a
static `hooks.json` that subscribes all 12 hook events globally, each running
`uvx capt-hook run <Event>`. So `.claude/settings.json` carries no hook entries — the scaffolded
settings fragment is hooks-free, and an event no enabled pack subscribes to dispatches as a clean
no-op. `uvx` fetches capt-hook into a throwaway environment, so nothing is added to
`pyproject.toml` and there is zero install step. The hooks themselves ship inside capt-hook as
builtin **packs** — `general` (the base-layer hooks), `fixes` (workarounds for upstream Claude
Code issues), the language layer (`python` or `go`), and `steering` (judgment nudges) — which the
scaffold enables through `.claude/hooks/packs.toml`, the sole per-repo control surface. A project
can add its own local hooks under `.claude/hooks/*.py` — the default `--hooks` directory — and you
verify the enabled packs (plus any local hooks) with `uvx capt-hook test` from the repo root.

## The session reviewer

Phase 6 runs `uvx capt-hook review enable` (after `gh repo create`, since the reviewer opens PRs
against `origin` and refuses a repo with no `origin` remote), which arms capt-hook's **session
reviewer** for the repo. When a Claude Code session ends, it mines the transcript for the durable
corrections you gave and the hooks that misfired, judges each, and — once a pattern clears its
thresholds — opens a pull request that adds or fixes a hook. `review enable` does two things:
registers the captain-hook plugin (committed in the same publish step) and starts watching the
repo. It writes no settings hook — the plugin's global `SessionEnd` entry already runs
`uvx capt-hook review run`, which self-guards by checking the watch list before doing any work. It
needs an authenticated `claude` and `gh`. Tune it with the `HOOKS_REVIEW_*` environment variables
and turn it off per repo with `uvx capt-hook review disable`, which stops watching. Full guide:
<https://yasyf.github.io/captain-hook/docs/guide/session-reviewer.html>.

## Two hook systems: don't conflate them

The python layer ships **two** unrelated hook systems that both go by "hooks":

- **capt-hook** (everything else in this doc) — gates *Claude-session* tool events
  (`PreToolUse`, `Stop`, …) from capt-hook packs and `.claude/hooks/*.py`. It never touches git.
- **The git commit hooks** (next section) — run ruff and ty on `git commit` from
  `.pre-commit-config.yaml`. They have nothing to do with Claude sessions.

## Git-level commit hooks (prek: ruff + ty)

`.pre-commit-config.yaml` pins two Astral hook repos and is driven by
[prek](https://github.com/j178/prek) — a fast Rust drop-in for pre-commit that reads
`.pre-commit-config.yaml` unchanged and ships as a single binary, so running it through
`uvx prek` adds nothing to `pyproject.toml`:

- [`astral-sh/ruff-pre-commit`](https://github.com/astral-sh/ruff-pre-commit) runs
  `ruff check --fix` + `ruff format` on staged files.
- [`astral-sh/ty-pre-commit`](https://github.com/astral-sh/ty-pre-commit) type-checks the
  **whole project** (the hook entry is `uv check`; the rev pins the ty version). **Warnings
  only**: `[tool.ty.rules]` sets `all = "warn"`, so diagnostics print (the hook sets
  `verbose: true` — passing hooks hide output otherwise) and the commit always proceeds.
  Inside Claude sessions even the warnings are silenced: the hook inherits
  `TY_CONFIG_FILE=.claude/ty-quiet.toml` from the session env.

Activate once per clone:

```bash
uvx prek install
```

After that, every commit auto-fixes mechanical issues and prints type warnings. When ruff
**rewrites** a file the commit aborts (prek exits non-zero so you can review the change) —
re-`git add` the fixed files and commit again. To clean everything up-front instead, run
`uvx prek run --all-files` (allowed by `toolchain.py`'s ruff guard). The pinned `rev`s are the
single source of truth for the hooks' ruff and ty versions across every clone — bump them with
`uvx prek autoupdate`. The first commit after `uvx prek install` is slow (prek clones the hook
repos and builds their envs; cached afterwards). CI does **not** run ruff — the commit hook is
the only mechanical-lint enforcement — but it **does** re-run the ty hook
(`uvx prek run ty --all-files`, advisory) as the backstop for clones that never ran
`uvx prek install`. To drop both hooks, run `uvx prek uninstall` **and** delete
`.pre-commit-config.yaml` — deleting the config alone leaves the installed
`.git/hooks/pre-commit` orphaned, which aborts every commit with
`No prek.toml or .pre-commit-config.yaml found` (recover with `uvx prek uninstall`, or a
one-off `PREK_ALLOW_NO_CONFIG=1 git commit`). To drop only ty, delete its `repo:` block and
the CI ty step.

## Hook inventory

These hooks ship inside capt-hook as builtin **packs** — `general` (the base-layer hooks), `fixes`
(workarounds for upstream Claude Code issues), the language layer (`python` or `go`), and
`steering` (judgment nudges) — enabled per repo through
`.claude/hooks/packs.toml`. Their behavior is
unchanged; only the delivery moved from vendored `.py` files to packs, so the "tailor" and
"remove" notes below route through the pack model: override a hook with a local
`.claude/hooks/<name>.py`, or manage packs in `packs.toml` (see *Adding and removing rules*).

### `commands` (general pack)

- Blocks `git stash` — reason "git stash is not allowed", hint "Commit your changes to a
  branch instead".
- Blocks unpiped `grep` — "Use ripgrep (rg) instead of grep". The `UnpipedGrep`
  `CustomCondition` still allows the stream-filter idiom (`… | grep`) and `git log --grep`;
  it blocks grep used for file searching, whether standalone, heading a pipe, or in a
  `&&`/`;` chain.
- Nudges `/codex` after 2+ tool failures in a turn without a codex invocation. Requires
  the codex plugin — the scaffolded `.claude/settings.json` already registers the
  `yasyf/cc-skills` marketplace and enables `codex@skills`, so it activates when the folder
  is trusted (no manual `/plugin install`). The `UsedSkill("codex|codex:codex")`
  alternation covers both the bare and plugin-namespaced skill name. If the project
  doesn't use Codex, override this nudge with a local `commands` hook and set
  `"codex@skills": false` under `enabledPlugins` in `settings-overrides.fragment.json`,
  then re-render (`cc-guides render`).

### `models` (general pack)

Enforces the CLAUDE.md **Models** routing table (§ Plan Execution & Orchestration) at
PreToolUse. Eight hooks:

- **Haiku gate (block).** Denies an `Agent`/`Task` call that explicitly passes a
  haiku-tier `model`, unless the prompt reads as single-fact mechanical work
  (classify/label/tag one thing per item). Recovery is in the deny message: use
  `sonnet`, or drop `model` to inherit the session model.
- **Prose gate (block).** Denies an `Agent`/`Task` call that pins haiku/sonnet/opus
  on a writing-shaped prompt — all prose routes to fable; drop `model` or pass
  `model: fable`.
- **Explore auto-upgrade (rewrite).** An `Explore` or `claude-code-guide` subagent
  spawned without a `model` param silently runs haiku; this rewrite fills in
  `model: sonnet` (never touching an explicit choice) and notes the upgrade in
  context.
- **Fable-implementation nudge (LLM, warn).** An `Agent`/`Task` spawn that would run
  on fable (unpinned or `model: fable`) with an implementation-shaped prompt gets an
  LLM-judged reminder that implementation defaults to opus `xhigh` (or gpt-5.6-sol
  via the `codex:codex-wrapper` agent for well-scoped edits). Judged, not pattern-matched,
  because fable is often intentional — design/prose review, writing, hard planning,
  and sensitive implementation stay there (code/diff and security review route to
  gpt-5.6-sol via their own nudges); when uncertain it stays silent.
- **Delegated review/diagnosis nudge (LLM, warn).** An `Agent`/`Task` spawn that would
  run code/diff review, a security review/audit or verification of security-sensitive
  code, or bug diagnosis on fable gets a reminder that these route to gpt-5.6-sol via
  the `codex:codex-wrapper` agent (spawn it with the self-contained question);
  `Skill(codex)` works from the main conversation. Design review, prose review, and findings
  synthesis stay on fable; when uncertain it stays silent.
- **Workflow review/diagnosis nudge (LLM, warn).** The same reminder for a `Workflow`
  whose finder, refuter, security-audit, or diagnosis stages would run on fable.
- **Workflow haiku nudge (warn).** A `Workflow` whose script (inline or via
  `scriptPath`) pins `agent()` steps to haiku gets a reminder that haiku is for
  mechanical map/apply steps only — judgment-bearing stages inherit or route up.
- **Workflow prose nudge (warn).** A `Workflow` whose script pins a non-fable model
  on prose-shaped stages gets the matching reminder that writing routes to fable.

Remove or tailor by overriding with a local `models` hook. If a repo legitimately
runs haiku fleets (bulk single-fact classification), the gate already allows
prompts that say so.

### Steering pack (`steering`)

Three judgment nudges, all registered directly in the pack's `steering.py`. The pack exposes no
importable building blocks — tailor a nudge by overriding it with a local `.claude/hooks/<name>.py`,
not by importing pack internals.

- **Pre-existing-issue nudge.** NLP-signal nudge against dismissing pre-existing issues. Fires
  when weighted signals cross `threshold=2` within a 15-message window: regex
  `(?i)(?:pre-existing|preexisting)` (weight 2), `(?i)(?:outside|beyond) (?:the )?scope`
  (weight 1), plus two `NlpSignal` clause sets ("change didn't cause/introduce", "issue is
  existing/present/previous"). `skip_if=[TypeCheckerContext()]` suppresses it when recent
  assistant text mentions pyright/mypy/type errors/LSP diagnostics, so trivial type-checker
  noise (which AGENTS.md explicitly excuses) doesn't trigger it. The message cites AGENTS.md §
  Code Stewardship. **Note:** `NlpSignal` needs the spaCy `en_core_web_sm` model and the wn
  `oewn:2025` lexicon provisioned at runtime and test time.
- **Trivial-type nudge.** Stops the chase after trivial pyright/typing warnings (cached_property
  vs property, minor override mismatches, descriptor protocol), `skip_if` the project's
  type-check command already ran. Cites AGENTS.md § General Rules.
- **Band-aid-plan nudge.** An `llm_nudge` on `PostToolUse` of `ExitPlanMode` (`max_fires=1`,
  agent mode): it reads the submitted plan and the user's opening request, inspects the cited
  code, and flags a plan that treats the symptom instead of removing the root cause — pointing
  the agent back at a first-principles fix. Advisory only.

`TypeCheckerContext` is internal to the pack (not a public import). To broaden type-noise
suppression, copy that condition into your local override and extend its `PATTERN`.

### `prompts` (general pack)

Non-blocking nudge that fires when an Edit/Write's content looks like an LLM prompt:
semantic XML tags (`<instruction>`, `<system>`, `<examples>`, `<success_criteria>`, …),
classic system-prompt openers (`You are a…`, `Your task is to…`), prompt-ish identifiers
(`def …prompt(`, "system/developer prompt"), and chat-message shapes (`messages = [`,
`"role": "system"|"user"|"assistant"|"developer"`). The `PROMPT_MARKERS` regex is
language-agnostic — `Content(..., project_only=False)` matches the new content of any file,
not just Python. The message points at the `llm-prompts` skill (positive framing, XML
structure, per-provider model behavior) and a follow-up `/slop-cop-check` on the edited
file. `skip_if` suppresses the nudge once the `llm-prompts` or `slop-cop` skill has been
used in the session.

Needs the `llm-prompts@skills` and `slop-cop@skills` plugins — the scaffolded
`.claude/settings.json` already enables both from the `yasyf/cc-skills` marketplace, so they
activate when the folder is trusted (no manual `/plugin install`). To remove the nudge,
override it with a local `prompts` hook and set `"llm-prompts@skills"` / `"slop-cop@skills"`
to `false` under `enabledPlugins` in `settings-overrides.fragment.json`, then re-render (keep
`slop-cop@skills` if the `docs` nudge remains — its nudge runs slop-cop too). Tailor by
extending `PROMPT_MARKERS` for other prompt dialects.

### `docs` (general pack)

Advisory nudge on the first Edit/Write to documentation (`**/*.md`, `**/*.qmd`,
`docs/**`, `README.md`): consult the `writing-docs` skill — Diataxis modes, the
technical-builder voice, runnable code-sample rules — then run
`slop-cop check <file> --lang=markdown`. `max_fires=1` per session, and
`skip_if=[UsedSkill("writing-docs|writing-docs:writing-docs")]` stands it down once
the skill has been used. Advisory only; it never blocks an edit.

Needs the `writing-docs@skills` plugin — the scaffolded `.claude/settings.json`
already enables it from the `yasyf/cc-skills` marketplace, so it activates when the
folder is trusted (no manual `/plugin install`). To remove the nudge, override it with
a local `docs` hook and set `"writing-docs@skills": false` under `enabledPlugins` in
`settings-overrides.fragment.json`, then re-render (keep `slop-cop@skills` if the
`prompts` nudge remains).

### `tasks` (general pack)

End-of-turn task discipline, built entirely from declarative primitives + local
`CustomCondition`s — task state reads from `evt.tasks` (Claude Code's native task store, the
source of truth that also reflects subagent/teammate/resumed updates), never from counting
transcript tool uses.

- A Stop `gate(...)` that blocks while the session has open tasks
  (`only_if=[TasksIncomplete()]`), so the agent must mark each finished task
  `status='completed'` (or defer with a note) before stopping. `skip_if=[Waiting(),
  Acknowledged(OVERRIDE_TOKEN)]` — it never fires on a turn that ends waiting on the user
  (e.g. an `AskUserQuestion`), and emitting `REMAINING_TASKS_ACKNOWLEDGED` is the deliberate
  escape (invalidated by further edits). Completion-only: a session with no tasks never
  blocks — it never forces task creation.
- A PostToolUse `nudge(...)` (`only_if=[Tool("Edit|Write"), DriftedFromTasks()]`) that warns
  when there are open tasks and many exploration/action calls have happened since the last
  task interaction (`TASK_DRIFT_THRESHOLD`).
- Two more `nudge(...)`s: on `ExitPlanMode` ("break the plan into tasks") and on a
  multi-request `UserPromptSubmit` (numbered/bulleted/imperative `Signals`, skipped in plan
  mode).

All messages cite CLAUDE.md § Task Tracking. Inline tests inject task state via
`Input(tasks=[...])`, exercising the real block/warn paths. Tailor `OVERRIDE_TOKEN` /
`TASK_DRIFT_THRESHOLD` in a local override, or drop the hook (see *Adding and removing rules*).

Stop gates are wait-aware by default: any `gate(...)` / `llm_gate(...)` on `Stop` with no
`skip_if` automatically skips while the agent waits on background work such as a running
`Workflow`, an async sub-agent, or a `ScheduleWakeup` loop, and re-fires once it resumes. The
moment you pass your own `skip_if` that default switches off, so include `Waiting()` in the
list yourself, as the task gate above does.

### `plans` (general pack)

A `PreToolUse` `hook(...)` that blocks rewriting a plan with `Write` once it has already been
written this session — use `Edit` for incremental changes instead. The `RewritingExistingPlan`
`CustomCondition` fires only for `.md` files under a `plans/` or `specs/` directory, reads from
`evt.ctx.prior` (so the pending Write is never counted as the prior edit), and stands down in
two cases: the first write of the plan (nothing to rewrite yet) and a write after a fresh
`EnterPlanMode` (a new plan cycle may legitimately start over). The message cites no doc
section. Tailor the `plans/`/`specs/` scope in a local override, or drop the hook (see *Adding
and removing rules*).

### `review` (general pack)

A `Stop` `gate(...)` demanding a correctness + STYLEGUIDE.md review before stopping when the
session changed source. The `EditedSource` `CustomCondition` fires when any edited file is not
a test (`is_test`), not prose/config (`NON_SOURCE_SUFFIXES` — `.md`, `.json`, `.toml`, …), and
not under `docs/`, `.claude/`, or `.github/`. `skip_if=[Waiting()]` keeps it quiet while the
agent waits on background work (see the wait-aware note above). It is the language-agnostic
counterpart to the python layer's `style` gate, so every bootstrapped repo — not just
Python ones — gets a review-before-stop gate. Tailor `NON_SOURCE_GLOBS` / the excluded dirs in
a local override to scope what counts as source, or drop the hook (see *Adding and removing
rules*).

### `testing` (python pack)

- Nudges isolating the minimal failing test case (node-id suffix, `-k`, `--last-failed`)
  when editing a test file, instead of broad re-runs.
- A `git commit` test gate, expressed declaratively as a `gate(...)` (block) + `nudge(...)`
  (warn) pair sharing `only_if=[Tool("Bash"), Command(r"git\s+commit")]` and
  `skip_if=[RanCommand(r"uv run pytest"), UserSaid("commit", "just commit"),
  AllEditsUnder("docs/", ".claude/", ".github/")]`. Three local `CustomCondition`s carry the
  logic: `UserSaid`, `AllEditsUnder`, and `CommitsPython` (the command names a `.py` path).
  A `.py` commit without a prior `uv run pytest` blocks; a non-`.py` commit warns; the skip
  conditions exempt both.

If the project's test command differs, change the `RanCommand` pattern and both messages.

### `style` (python pack)

Seven rules enforcing STYLEGUIDE.md, built on the matchers DSL (`captain_hook.style.matchers`,
imported `as M`) and registered with `styleguide(...)`:

- `NoUnderscorePrefixes` — no underscore-prefixed classes or module constants; use
  `__all__` instead.
- `NoNestedImports` — lazy imports go at the top of the function body, never inside
  if/for/try/with (`TYPE_CHECKING` blocks exempt).
- `ZipStrict` — `zip(...)` requires `strict=True`.
- `LateModuleConstants` — module constants before any class or function.
- `LateClassConstants` — class-body assignments before any method.
- `NoQuotedAnnotations` — no quoted annotations under `from __future__ import annotations`.
- `NoWeakeningToAny` — a `StyleDiffRule`: fires only when the diff *introduces* `Any` into
  a previously typed slot (`*args: Any`, `dict[str, Any]` aliases, and pre-existing `Any`
  are allowed). It overrides `check()` to diff by a custom node identity (`any_label`)
  instead of the default unparsed-source identity.

The review-before-stop Stop gate is **not** in the `style` hook — it lives in the general
pack's `review` hook (a language-agnostic gate, so it covers Python too). The `style` hook
ships only the seven AST rules above.

### `ccx` (plugin-attached pack — `cc-context@skills`)

Guard pack that makes the `cc-context` facade (`ccx` / its MCP tools) the
default for reading and searching code. It **blocks the token-heavy primitives** the
facade replaces so an agent reaches for `ccx` first, citing the AGENTS.md **Compact
Context (ccx)** heading in its block reasons. Unlike the builtin `general`/`python`/`go`
packs, `ccx` ships inside the `cc-context@skills` plugin the scaffolded
`.claude/settings.json` already enables. The plugin's SessionStart hook runs
`uvx capt-hook pack attach` once to register the pack for the session, and the canonical
`uvx capt-hook run <Event>` commands pick it up during dispatch — so a scaffolded repo
gets the guard with no `.claude/hooks/packs.toml` entry to pin or refresh, and nothing
skews when cc-context cuts a new release.

The trade-off: an attached pack reaches only contributors who have the
`cc-context@skills` plugin enabled. A repo that wants the guard enforced for **every**
contributor can still pin it repo-scoped — `uvx capt-hook pack add
github:yasyf/cc-context@<tag>` writes a pinned entry in `.claude/hooks/packs.toml` that
every clone hydrates. A repo-scoped pin beats the ambient attach (the same-name pack
resolves to the pin and the attach is dropped), so the two never double-fire. To drop
the guard entirely (a repo that wants raw `Read`/`Grep` back), set
`"cc-context@skills": false` under `enabledPlugins` in `settings-overrides.fragment.json`,
re-render (`cc-guides render`), and replace the AGENTS.md Compact Context section with
plain Code-Search guidance.

### `cc-notes` (external pack — `github:yasyf/cc-notes@latest`)

Nudge pack for repos that adopt [cc-notes](https://github.com/yasyf/cc-notes), the
git-native durable tasks/notes layer (`refs/cc-notes/*`). Its hooks **only nudge,
never gate** — they teach when to reach for native task tracking versus durable,
git-synced cc-notes entities, float this session's open tasks and the notes relevant
to a file you just read, and prompt `cc-notes sync`/`reconcile` after commits and
merges. Every nudge gates on the `CcNotesAvailable` condition — exactly the `cc-notes`
binary on PATH, with no `refs/cc-notes/*` check — so the pack is **silent on any
machine without the binary**. Unlike the builtin `general`/`python`/`go` packs, it is
an **external** pack tracking `@latest`: the per-repo opt-in is the `[packs.cc-notes]`
entry's **presence** in `.claude/hooks/packs.toml`, which `cc-notes init` records (it
also installs the `refs/cc-notes/*` refspecs and a reconcile CI workflow). capt-hook
auto-fetches the declared pack on the next hook event, so there is no manual
`uvx capt-hook pack update` after `init` or after a fresh clone.

Because the pack is silent without the binary, adoption is **conditional on
`cc-notes` being installed** — Phase 6 runs `cc-notes init` (after the repo is published, so its
`refs/cc-notes/*` refspecs have an `origin` to target) only when `command -v cc-notes` resolves. **Never declare `[packs.cc-notes]` in a template's `packs.toml`**:
that would impose it on every bootstrapped repo, and capt-hook would auto-fetch the
pack on the first hook event even for users who don't run cc-notes. The
`.claude/settings.json` template registers the `cc-notes@cc-notes` plugin so the
`using-cc-notes` skill loads on folder-trust regardless; the *pack* is the opt-in
half, gated behind `cc-notes init`. To drop the nudges from an adopted repo, delete
the `[packs.cc-notes]` entry from `packs.toml`.

### `toolchain` (python pack)

- Nudges `uv sync --extra dev` on `ModuleNotFoundError`/`ImportError`, explicitly telling
  the agent not to make imports lazy or restructure code to avoid the import. Capped at
  `max_fires=2`. Update the text if the project's dev extra is named differently.

## Cross-reference invariant

Hook messages cite doc sections by exact heading:

- steering pack (pre-existing-issue nudge): **AGENTS.md § Code Stewardship**
- `ccx` pack: **AGENTS.md § Compact Context (ccx)**
- `tasks.py`: **CLAUDE.md § Task Tracking**
- `style.py` rule docstrings: **STYLEGUIDE.md § Code Organization** and **STYLEGUIDE.md § Type Annotations**
- `review.py`: **STYLEGUIDE.md**

If you rename or remove those sections while tailoring the scaffolded AGENTS.md, CLAUDE.md, or
STYLEGUIDE.md, update the hook messages in the same edit — a citation pointing at a
nonexistent section sends future agents on a dead-end lookup.

## Adding and removing rules

The hooks ship as capt-hook packs, managed through `.claude/hooks/packs.toml` and the
`uvx capt-hook pack` CLI:

- **List** the enabled packs with `uvx capt-hook pack list`.
- **Add** a pack with `uvx capt-hook pack add <name>` — a builtin (`general`, `python`) or
  `github:owner/repo@ref` for a third-party pack; `uvx capt-hook pack update` refreshes the
  pinned ones.
- **Drop** a whole pack's hooks with `uvx capt-hook pack remove <name>`, or delete its
  `[packs.<name>]` entry from `packs.toml` by hand.

To **change a pack hook's behavior**, add a local `.claude/hooks/<name>.py` that registers on
the same event. Local modules load before packs, and dispatch returns on the first `allow` or
`block`, so a local hook that issues a terminal decision pre-empts the pack hooks on that event
— a `warn` (or returning `None`) falls through and the pack hook still fires, so a no-op does
not silence anything. To drop a *single* pack hook cleanly, fork the pack and delete that hook
file; removing a `style` rule means deleting it from both its class definition and the
`styleguide(...)` registration call.

The inline tests are the regression net — `uvx capt-hook test` runs every enabled pack's tests
plus any local hook's. After adding, overriding, or removing a hook, run:

```bash
uvx capt-hook test
```

New local hooks should ship tests in the same shape the packs use — `Input(...)` keys
mapping to `Allow()`, `Block()`, or `Warn()` values:

```python
tests = {
    Input(command="git stash"): Block(),
    Input(command="git status"): Allow(),
    Input(file="m.py", content="_MAX = 3\n"): Warn(),
}
```

`StyleRule` tests take `file=` + `content=`; `StyleDiffRule` tests add `old=` so the rule can
distinguish introduced violations from pre-existing ones. Command hooks take `command=`;
transcript-driven nudges take `transcript=[...]` message dicts (see the `stewardship` hook for
the exact shape).

## Project-local escape hatch (pin instead of uvx)

If the project should control its capt-hook version — or, like captain-hook itself, dogfoods
its own checkout — pin `capt-hook` as a dev dependency, take the repo off the plugin's global
wiring, and point it at the project's own environment. Disable the captain-hook plugin for the
repo by setting `"captain-hook@captain-hook": false` under `enabledPlugins`, and wire a `"hooks"`
block registering every event with the venv binary directly:

```
"$CLAUDE_PROJECT_DIR"/.venv/bin/capt-hook run <Event>
```

Both live in the settings-overrides overlay fragment (then re-run `cc-guides render`). Disabling
the plugin drops its global hook registration, so this block is the only wiring left in play.
This is exactly what the captain-hook repo commits in its own `.claude/settings.json`: gating its
hooks through the published uvx version would run stale code, and the venv-direct form skips the
per-event `uv run` resolve (~55ms/event).

## Troubleshooting

- **First hook event is slow.** `uvx` cold-starts by resolving and installing capt-hook into
  a fresh environment on first use; subsequent events hit the cache.
- **Hooks silently not firing.** capt-hook discovers hooks in `.claude/hooks` by default;
  if files were moved, pass `--hooks <dir>` to `capt-hook test` and other CLI commands so
  they find them.
- **Changed a pack or plugin but nothing happened.** Claude Code reads hook registrations at
  session start, so enabling a pack in `packs.toml` or toggling the captain-hook plugin needs a
  session restart to take effect. `.claude/settings.json` carries no hook wiring to edit — the
  registration lives in the captain-hook plugin.
- **`stewardship.py` raises `spaCy model 'en_core_web_sm' is not installed`.** capt-hook
  refuses to silently download the model from a live hook. Provision it once per machine
  (the wn `oewn:2025` lexicon auto-downloads on first use):

  ```bash
  uvx --from capt-hook python -c "from captain_hook.util.model_cache import ensure_spacy_model; ensure_spacy_model()"
  ```

  `bootstrap.py verify` runs this automatically before the hook tests.
