# Honesty gates: docs claims are tested, not proofread

Every docs change asserts facts about a package: that a symbol exists with a shape, that a snippet runs, that a command prints what the page shows. Before the change ships, those assertions get executed — imported, run, regenerated — not re-read. A wrong doc is worse than a missing one, because readers trust and act on it; and proofreading cannot catch drift, because a renamed property reads exactly as plausibly as the current one. The gates are mechanical on purpose.

The examples below come from the captain-hook docs rebuild; each gate carries the real catch that earned it.

## Gate 1: the reflection gate

Import the real package and assert every documented symbol, attribute, and signature. Not the ones you suspect — every one the page names. The shape, from a real work order: "`uv run python` assertions that every documented symbol exists with the documented shape: Session.tool_calls property; ToolCallQuery.named/.touching/.under/.failed … after(tool=)/before(tool=) keyword-only (inspect.signature) … ANY missing symbol → STOP and report (the draft is wrong, not the code)."

In code, three assertion kinds cover nearly everything:

```python
import inspect
from cc_transcript.query import Session, ToolCallQuery

assert isinstance(inspect.getattr_static(Session, "tool_calls"), property)
for name in ("named", "touching", "under", "failed", "where", "where_input"):
    assert callable(getattr(ToolCallQuery, name)), name
assert inspect.signature(Session.after).parameters["tool"].kind is inspect.Parameter.KEYWORD_ONLY
```

The real catch: a captain-hook draft documented `turn.user_text` and `turn.edited_files`. The real API was `turn.prompt` and `turn.edits`. The draft read fine; an audit had even verified those names against the pinned dependency earlier — and the dependency moved between audit and landing. The gate runs at landing precisely because verification has a timestamp and code doesn't hold still. An audit-verified draft still takes the gate.

Two extensions:

- **STOP means stop.** A missing symbol is never patched around by the slice executor ("maybe it's the new name…"). The draft is wrong, not the code; the finding goes back to the writer with the real signature attached.
- **Negative claims gate too.** A page claiming machinery *doesn't* exist gets a mechanical check: the captain-hook session-reviewer rewrite claimed native dispatch had replaced an explicit hook command, gated by "grep captain_hook/hooks/hooks.json for absence of 'review run'". Absence is as assertable as presence.

## Gate 2: extract and run

Every code block on the page runs through the strongest applicable tier:

1. **Run** — a block with inline tests executes through the real engine (for capt-hook pages, `capt-hook --hooks <dir> test`; for other stacks, the project's example runner). The engine's verdict is the gate, not your reading of the block.
2. **Compile** — a complete file without tests gets `py_compile` plus an import check, so a bad import or syntax slip still fails ("py_compile every extracted runnable block (imports resolve: `from cc_transcript.query import Session` … — verify)").
3. **Syntax-only** — a deliberate fragment (a handler body shown without its `def`, a snippet using variables the page's context provides) gets wrapped and syntax-checked, and the work order says so up front: "the `evt.command.calls` and `llm_evaluate` snippets are handler-context fragments — syntax-only."

The tier assignment happens when the work is planned, not discovered mid-check — "state.qmd's examples carry none — syntax-check those" is a work-order line, so "I couldn't run it" is never an on-the-spot excuse.

## Gate 3: regenerate sample output

Shown CLI output is pasted from a live run in the current tree — never hand-typed, never copied forward from the old page. The catch that earned this: a captain-hook page showed `pack list` output claiming a pack had "2 hooks" and a plugin "4 hooks"; the live run said 3 and 32. Illustrative excerpts drift silently because nothing fails when they do — regeneration is the only gate they have. The work-order form: "REGENERATE live: run `uv run capt-hook pack list` in the worktree and paste the real output (trim to a representative excerpt if huge, marked with a real comment)."

Trim with a real language comment (`# … 29 more packs`), never a bare ellipsis, and normalize genuinely variable values — ports, session ids, timestamps — to visible placeholders. Everything else stays byte-real.

## Gate 4: parity-gated demos

Demo media — GIFs, animated SVGs, interactive widgets — obeys the same law as text: it shows a real run, and something mechanical fails when it stops being real.

- A live in-page demo (the captain-hook tutorial embeds an interactive port of the dispatch engine) is **parity-tested**: every preset a reader can click is a row in a test that replays the same input through the real engine and asserts the same verdict. When the port can't model an input, it says so instead of guessing — the page states it: "When a command uses shell features the port doesn't model (substitution, heredocs), the widget says so instead of guessing."
- Canned material is **recorded, labeled, never faked live**. Recorded rows come from actual engine runs ("record actual engine verdicts, do not hand-author"), and anything illustrative-only is marked as such (`verified: false`) so the reader — and the parity suite — knows its standing.
- Terminal media shows a real run of the exact command it sits under, with the generator committed so it regenerates when output changes (`readme.md`, media hierarchy).

Parity testing cuts both ways: while wiring the captain-hook tutorial's parity rows, the team found the engine blocked `sh -c "git stash"` — the plan's own assumption that the wrapper escaped the parser was wrong, and the page's table was corrected to teach what the engine actually does. Gates fix the plan, not just the prose.

## When a gate fails

Stop. The draft is wrong, not the code — the gate exists because the code is the authority. A slice executor who hits a failing gate reports the finding with the evidence (the real signature, the real output, the failing row) and 2-4 options, and does not improvise a fix to unfamiliar prose; the writer re-drafts against reality. The only in-slice fixes are the ones the work order already authorized with exact replacement text.
