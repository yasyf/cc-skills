# Work orders: delegating docs slices without losing the prose

Delegated prose drifts — a paraphrased instruction produces paraphrased docs. The fix is structural: the lead writer drafts every page, and delegated slices land those drafts **verbatim**, doing the mechanical work around them — file ops, link retargets, gate execution, shipping. A work order is the contract that makes that split executable. The two captain-hook slice orders quoted below ran real slices of a multi-page rebuild; treat their shape as the template.

## Who writes what

- **The lead writer**: every page draft, every authored replacement sentence for an in-flight fix, the commit messages, the retarget map. If a sentence will exist in the shipped docs, the lead wrote it.
- **The slice executor**: file operations, alias frontmatter already specified on the drafts, link retargets per the map, gate runs, the ship. Its own words are confined to retargeted link labels and glue — and even those are governed by the skill, which the order makes it read at the source: "Land drafts VERBATIM; the writing-docs skill governs any words of your own (read it first: …/SKILL.md + references/)."

Point the executor at the skill; never paraphrase the skill into the prompt. A paraphrase drifts, and the delegate obeys the prompt it was given — the skill's delegation section carries the incident that proved it.

## The anatomy of a work order

Six parts, each quoted from a real order.

**1. Header: prereq, worktree, the verbatim rule.** One line each — "Prereq: slice 2 pushed (Writing hooks + Testing on main). Worktree: /private/tmp/capt-hook-docs-wave reset to origin/main." The executor starts from a known tree state or not at all.

**2. Exact file operations.** Named files, named actions, no discretion: "Replace docs/guide/llm-hooks.qmd with drafts/guide/llm-hooks.qmd; DELETE docs/guide/signals.qmd + docs/guide/transcript.qmd (aliases are on the draft frontmatter)." Note the parenthetical — the order pre-answers the question the op would raise.

**3. Surgical fixes, each tied to an audit item, replacement text authored.** An in-flight fix is only in the order because an audit flagged it, and the order carries the lead's exact words, not an instruction to compose some:

> packs.qmd:33 imprecision — replace the sentence with (verbatim, authored): "To turn every capt-hook behavior off, disable the captain-hook plugin — its wiring dispatches every event, so without it no hook runs, builtin or plugin."

When the fix is code rather than prose, the order names the canonical source to mirror instead: "open that file at HEAD and copy its current form" beats describing the pattern.

**4. The gate list, with expected outcomes.** Every honesty gate (`honesty-gates.md`) the slice must run, with per-item tier assignments so nothing is decided mid-slice: the reflection gate's full symbol list, "Extract-and-run every `tests={` block … (state.qmd's examples carry none — syntax-check those)", the link-sweep grep with its retarget map, slop-cop scope ("on every page you edited beyond verbatim landing"), and the repo's own suites ("`uv run pytest tests/test_tutorial_parity.py -q` … still green").

**5. Ship steps, commit messages authored, discretion scoped.** The lead writes the messages — "Commits: (a) `docs(guide): merge LLM hooks, signals & the transcript` (b) …" — and where the executor gets a call, the order grants it explicitly and demands it be reported: "or one commit if the link sweep entangles them; your call, say which."

**6. The check-back contract.** The closing line of every order: "Structural surprises → stop, options, report." A draft that doesn't match reality — a failing reflection gate, a gem the rewrite can't place, a file op that collides with concurrent work — ends the slice early with findings plus 2-4 concrete options. The executor never improvises a detour through prose it didn't write.

## Verify the landing, don't trust it

"Landed verbatim" is a checkable claim, so check it: diff the landed page against the draft. Byte-sensitive blocks get their own line in the order — "Mermaid check: the lifecycle diagram block survived the landing byte-intact (diff against the draft)" — because an executor's editor, formatter, or retarget sweep can mangle content nobody re-reads.

## Template

```markdown
# <Slice name> work order — <the pages this slice lands>

Prereq: <tree state>. Worktree: <path> reset to <ref>. Land drafts VERBATIM;
the writing-docs skill governs any words of your own (read it first: <path to SKILL.md>).

## 1. File operations
- Replace <target> with <draft>; DELETE <killed pages> (aliases on the draft frontmatter).

## 2. Surgical fixes (each audit-flagged)
- <file:line> <defect> — replace with (verbatim, authored): "<exact text>"
- <file:line> <stale output> — REGENERATE live: run `<command>` and paste the real output.

## 3. Verification gates
- Reflection gate: assert <full symbol list> (ANY missing symbol → STOP; the draft is wrong, not the code).
- Extract-and-run every tests={} block; <named blocks> are fragments — syntax-only.
- Gems survival: <the audit's gem list for each merged page>.
- Link sweep: `git grep -nE "<killed-page regex>" -- docs/ README.md <other surfaces>` — retarget: <explicit map>.
- slop-cop on pages edited beyond verbatim landing; triage per the skill.
- <repo test suites> still green.

## 4. Ship
Commits: <authored messages>. <granted discretion, if any — say which>.
Fetch/rebase/push. Report shas + gate outputs. Structural surprises → stop, options, report.
```
