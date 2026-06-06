# Hooks: understanding, testing, and tailoring the scaffolded capt-hook setup

## capt-hook in ten lines

[capt-hook](https://pypi.org/project/capt-hook/) (the captain-hook project) is a declarative
hook framework for Claude Code: hooks are data, declared with primitives like `block_command`,
`nudge`, `gate`, `hook`, `styleguide`, and `@on`, and each carries inline `tests = {...}` that
run anywhere — locally, in CI if you wire them in — in the same shape they run in
production. The scaffold wires it through
`.claude/settings.json`, which runs `uvx capt-hook run <Event>` for `PreToolUse`,
`PostToolUse`, `PostToolUseFailure`, and `Stop`. `uvx` fetches capt-hook into a throwaway
environment, so nothing is added to `pyproject.toml` and there is zero install step. Hook
files live in `.claude/hooks/*.py` — the default `--hooks` directory — and you verify them
with `uvx capt-hook test` from the repo root.

## IMPORTANT: minimum version

These hook files target **capt-hook >= 0.3**: `style.py` imports the styleguide Matcher DSL
(`from captain_hook.primitives.styleguide import Matcher as M`), which does not exist in
older releases. Against an older version, `style.py` fails to import with
`ModuleNotFoundError`. If `uvx` resolves a stale cached version, force resolution with:

```bash
uvx capt-hook@latest test
```

## Hook inventory

### `.claude/hooks/audit.py` (base layer)

One line of substance: `audit(Event.PreToolUse | Event.PostToolUse | Event.Stop)` — keeps an
audit log of tool events. Tailor by dropping events from the union, or delete the file to
disable logging entirely.

### `.claude/hooks/commands.py` (base layer)

- Blocks `git stash` — reason "git stash is not allowed", hint "Commit your changes to a
  branch instead".
- Blocks unpiped `grep` — "Use ripgrep (rg) instead of grep". The `UnpipedGrep`
  `CustomCondition` still allows the stream-filter idiom (`… | grep`) and `git log --grep`;
  it blocks grep used for file searching, whether standalone, heading a pipe, or in a
  `&&`/`;` chain.
- Nudges `/codex` after 2+ tool failures in a turn without a codex invocation. **Requires
  the codex plugin** (`/plugin install codex@skills`); the `UsedSkill("codex|codex:codex")`
  alternation covers both the bare and plugin-namespaced skill name. Delete this nudge if
  the project doesn't use Codex.

### `.claude/hooks/stewardship.py` (base layer)

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

### `.claude/hooks/testing.py` (python layer)

- Nudges isolating the minimal failing test case (node-id suffix, `-k`, `--last-failed`)
  when editing a test file, instead of broad re-runs.
- `commit_test_gate`: a `@on(Event.PreToolUse, ...)` hook that blocks `git commit` of
  Python changes without a prior `uv run pytest` in the session
  (`skip_if=[RanCommand(r"uv run pytest")]`). Exempt: edits entirely under `docs/`,
  `.claude/`, `.github/`; an explicit user "commit"/"just commit"; non-`.py` commits get a
  warn rather than a block.

If the project's test command differs, change the `RanCommand` pattern and both messages.

### `.claude/hooks/style.py` (python layer)

Seven rules enforcing STYLEGUIDE.md, built on the Matcher DSL and registered with
`styleguide(...)`:

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
  are allowed).

Plus a `gate(...)` Stop hook demanding a STYLEGUIDE.md review before stopping when package
code changed. The gate's glob is `**/<package>/**/*.py` where `<package>` is the package
name supplied at scaffold time (e.g. `captain_hook`); test files are exempt via
`not f.is_test`.

### `.claude/hooks/toolchain.py` (python layer)

- Blocks manual `ruff` (`block_command(r"^ruff\b", ...)`) — "mechanical linting is
  auto-fixed by tooling", hint cites AGENTS.md § Mechanical Linting. `pre-commit run
  --hook ruff` stays allowed.
- Nudges `uv sync --extra dev` on `ModuleNotFoundError`/`ImportError`, explicitly telling
  the agent not to make imports lazy or restructure code to avoid the import. Capped at
  `max_fires=2`. Update the text if the project's dev extra is named differently.

`.claude/hooks/__init__.py` is intentionally near-empty (just the future-annotations import).

## Cross-reference invariant

Hook messages cite doc sections by exact heading:

- `stewardship.py`: **AGENTS.md § Code Stewardship**
- `toolchain.py`: **AGENTS.md § Mechanical Linting**
- `style.py` rule docstrings: **STYLEGUIDE.md § Code Organization** and **STYLEGUIDE.md § Type Annotations**

If you rename or remove those sections while tailoring the scaffolded AGENTS.md or
STYLEGUIDE.md, update the hook messages in the same edit — a citation pointing at a
nonexistent section sends future agents on a dead-end lookup.

## Adding and removing rules

Each `.py` in `.claude/hooks/` is independent: deleting a file drops all its rules with no
other changes needed. Within `style.py`, a rule must be removed from both its class
definition and the `styleguide(...)` registration call.

The inline tests are the regression net. After **any** edit to a hook file, run:

```bash
uvx capt-hook test
```

New rules should ship tests in the same shape the existing ones use — `Input(...)` keys
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
transcript-driven nudges take `transcript=[...]` message dicts (see `stewardship.py` for the
exact shape).

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
- **`ModuleNotFoundError: captain_hook.primitives.styleguide`.** Stale cached version; see
  the version note above — `uvx capt-hook@latest test`.
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

  `verify.sh` runs this automatically before the hook tests.
