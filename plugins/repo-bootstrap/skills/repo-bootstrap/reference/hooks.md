# Hooks: understanding, testing, and tailoring the scaffolded capt-hook setup

## capt-hook in ten lines

[capt-hook](https://pypi.org/project/capt-hook/) (the captain-hook project) is a declarative
hook framework for Claude Code: hooks are data, declared with primitives like `block_command`,
`nudge`, `gate`, `hook`, `styleguide`, and `@on`, and each carries inline `tests = {...}` that
run anywhere — locally, in CI if you wire them in — in the same shape they run in
production. The scaffold wires it through
`.claude/settings.json`, which runs `uvx capt-hook run <Event>` for `PreToolUse`,
`PostToolUse`, `PostToolUseFailure`, and `Stop`. `uvx` fetches capt-hook into a throwaway
environment, so nothing is added to `pyproject.toml` and there is zero install step. The hooks
themselves ship inside capt-hook as two builtin **packs** — `general` (the base-layer hooks)
and `python` — which the scaffold enables through `.claude/hooks/packs.toml`. A project can
add its own local hooks under `.claude/hooks/*.py` — the default `--hooks` directory — and you
verify the enabled packs (plus any local hooks) with `uvx capt-hook test` from the repo root.

## The session reviewer

Phase 2 runs `uvx capt-hook review enable`, which arms capt-hook's **session reviewer** for
the repo. When a Claude Code session ends, it mines the transcript for the durable corrections
you gave and the hooks that misfired, judges each, and — once a pattern clears its thresholds —
opens a pull request that adds or fixes a hook. `review enable` does three things: vendors the
reviewer's skills into `.claude/skills/` (committed in Phase 6), wires a `review run` hook onto
`SessionEnd` in `.claude/settings.local.json` (machine-local, gitignored), and starts watching
the repo. It needs an authenticated `claude` and `gh`. Tune it with the `HOOKS_REVIEW_*`
environment variables and turn it off per repo with `uvx capt-hook review disable`. Full guide:
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

These hooks ship inside capt-hook as two builtin **packs** — `general` (the base-layer hooks)
and `python` — enabled per repo through `.claude/hooks/packs.toml`. Their behavior is
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
  doesn't use Codex, override this nudge with a local `commands` hook and remove the
  `enabledPlugins`/`extraKnownMarketplaces` keys in settings together.

### `stewardship` (general pack)

NLP-signal nudge against dismissing pre-existing issues. Fires when weighted signals cross
`threshold=2` within a 15-message window: regex `(?i)(?:pre-existing|preexisting)` (weight
2), `(?i)(?:outside|beyond) (?:the )?scope` (weight 1), plus two `NlpSignal` clause sets
("change didn't cause/introduce", "issue is existing/present/previous").
`skip_if=[TypeCheckerContext()]` suppresses the nudge when recent assistant text mentions
pyright/mypy/type errors/LSP diagnostics, so trivial type-checker noise (which AGENTS.md
explicitly excuses) doesn't trigger it. The message cites AGENTS.md § Code Stewardship.

Tailor `threshold`/`window`, or extend `TypeCheckerContext.PATTERN` for other suppression
contexts. **Note:** `NlpSignal` needs the spaCy `en_core_web_sm` model and the wn
`oewn:2025` lexicon provisioned at runtime and test time.

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
override it with a local `prompts` hook and remove the `llm-prompts@skills`/`slop-cop@skills`
keys from `enabledPlugins` (keep `slop-cop@skills` if the `docs` nudge remains — its nudge runs
slop-cop too). Tailor by extending `PROMPT_MARKERS` for other prompt dialects.

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
a local `docs` hook and remove the `writing-docs@skills` key from `enabledPlugins` (keep
`slop-cop@skills` if the `prompts` nudge remains).

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

### `toolchain` (python pack)

- Blocks manual `ruff` (`block_command(r"^ruff\b", ...)`) — "mechanical linting is
  auto-fixed by tooling", hint cites AGENTS.md § Mechanical Linting. `prek run
  --all-files` (the sanctioned pre-commit cleanup) stays allowed.
- Nudges `uv sync --extra dev` on `ModuleNotFoundError`/`ImportError`, explicitly telling
  the agent not to make imports lazy or restructure code to avoid the import. Capped at
  `max_fires=2`. Update the text if the project's dev extra is named differently.

## Cross-reference invariant

Hook messages cite doc sections by exact heading:

- `stewardship.py`: **AGENTS.md § Code Stewardship**
- `toolchain.py`: **AGENTS.md § Mechanical Linting**
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
its own checkout — pin `capt-hook` as a dev dependency and switch every hook command in
`.claude/settings.json` from `uvx capt-hook run <Event>` to:

```
uv run --project "$CLAUDE_PROJECT_DIR" capt-hook run <Event>
```

This resolves capt-hook from the project's own environment instead of a throwaway uvx env.
This is exactly what captain-hook's own `.claude/settings.json` does, since renaming its
entry point under uvx would run a stale published version against its own hooks.

## Troubleshooting

- **First hook event is slow.** `uvx` cold-starts by resolving and installing capt-hook into
  a fresh environment on first use; subsequent events hit the cache.
- **Hooks silently not firing.** capt-hook discovers hooks in `.claude/hooks` by default;
  if files were moved, pass `--hooks <dir>` to `capt-hook` commands and keep the
  settings commands in sync.
- **Edited `.claude/settings.json` but nothing changed.** Claude Code reads hook wiring at
  session start; settings changes need a session restart to take effect.
- **`stewardship.py` raises `spaCy model 'en_core_web_sm' is not installed`.** capt-hook
  refuses to silently download the model from a live hook. Provision it once per machine
  (the wn `oewn:2025` lexicon auto-downloads on first use):

  ```bash
  uvx --from capt-hook python -c "from captain_hook.util.model_cache import ensure_spacy_model; ensure_spacy_model()"
  ```

  `bootstrap.py verify` runs this automatically before the hook tests.
