---
name: show
description: Choose the right surface before delivering anything to the human — plain prose, AskUserQuestion, a static Artifact page, or a live cc-present board — and present instead of dumping walls of text. Use when about to deliver findings, a review, a report, a comparison, a plan, or a set of options; when the user says "show me", "present this", or "which one"; or at any moment the response is about to exceed a screen of structured text.
---

# Show: pick the surface before you deliver

Presenting is the default for structured deliverables, not a special occasion. A
wall of text asks the human to be the renderer; a surface does that job for them
and hands back structure — a click, a pick, a verdict — instead of prose you must
re-parse. One deliverable, one surface. Chat prose is for answers, not artifacts.

## The dispatch

Classify the deliverable first; the surface follows.

| Deliverable | Surface | How |
|---|---|---|
| Direct answer, linear reasoning, fits in about a screen | Chat prose | Write it and stop. No surface beats a good paragraph. |
| One decision, 2–4 discrete options, nothing to inspect | AskUserQuestion | Concrete picks, related questions batched into one call. Use option previews when the choices are visual. |
| Read-only: a report, comparison, walkthrough, architecture overview, or data visualization | Static Artifact page | Load the `artifact-design` skill FIRST (bundled with Claude Code — it calibrates the visual treatment), then write the HTML and publish with the Artifact tool. Keep the title and favicon stable across redeploys. For charts, also load `dataviz`. |
| Per-item human verdicts: approve N items, pick between drafts, give structured feedback, gate a sign-off | cc-present live board | Defer wholly to the `cc-present:present` skill: compose typed JSON blocks (never hand-written HTML), serve the board, and keep working while clicks stream back into the session as events. |

## Tie-breakers

- More than a screen of structured text wants a page, not prose.
- Three or more items each needing its own verdict want a board, not AskUserQuestion.
- Options whose tradeoffs deserve annotation want board choice blocks; four or fewer simple options stay AskUserQuestion.
- A mid-task micro-confirmation stays AskUserQuestion. Never spin up a board for one yes/no.
- The user asked for plain text? Plain text. Their instruction beats this skill.

The read-only/interactive line is the load-bearing one: the moment the human must
*decide something per item*, a static page stops being enough — decisions typed
back into chat ("card 14, option B") are the failure mode this skill exists to end.

## Dependencies

`artifact-design` and `dataviz` ship inside Claude Code; invoke them with the
Skill tool, nothing to install. The board surface needs the cc-present plugin —
if `cc-present:present` is not in your skill list, install it:

```
claude plugin marketplace add yasyf/cc-present
claude plugin install cc-present@cc-present
```

(Settings-file equivalent: `extraKnownMarketplaces: {"cc-present": {"source": {"source": "github", "repo": "yasyf/cc-present"}}}` plus `enabledPlugins: {"cc-present@cc-present": true}`.) If it cannot be installed, degrade deliberately: an Artifact page carries the content and AskUserQuestion collects the verdicts.

Per-surface mechanics live in [reference/surfaces.md](reference/surfaces.md).

## Anti-patterns

- Three or more options as numbered prose ending "which one?" — that was a board, or AskUserQuestion when the options are simple.
- A report written to a scratch `.md`/`.html` with "open it and let me know" — that was an Artifact page.
- Re-listing in chat what the board or page already shows — link it, don't mirror it.
- Two surfaces carrying one deliverable. Pick one.
- Parking on a board waiting for clicks — the loop is live; keep working.
