# The method

Assumptions first, then decisions, each with a stable ID and a paper trail. The register is the design; the document is a rendering of it.

## Registers and lifecycle

`registers.json` is canonical: if a fact has a stable ID, it lives there and nowhere else. The companion files each have one job: `qa-log.json` holds the verbatim question rounds, `NOTES.md` holds prose that doesn't fit structure, `design-doc.html` renders the JSON, `build-pdf.py` prints it. Editing the JSON updates the doc, its Markdown exports, and (after a rerun) the PDF, so there is exactly one place to change a fact.

An entry is never deleted and never edited into a different claim. It is revised in place only for wording; when the *substance* changes, the old entry is superseded:

- A decision gets a successor: new `DQ#`, and the old one gets `s: "superseded"` plus `by: "<successor>"`. `check` enforces the pair in both directions.
- An assumption that gets revised keeps its ID and records the history in its `n` field ("Revised once, 2026-07-21: the original 8 KB bound fell at the first consumer survey").

The point of supersession is that a reader can watch the design change its mind. A register that only shows final answers hides exactly the reasoning a reviewer needs.

## The round protocol

Every design fork goes through an AskUserQuestion round. The shape that works:

- Prefix the question with the register ID it will settle ("DQ4: How do workers receive jobs?"), so the log and the register cross-reference themselves.
- Each option carries a consequence description — what choosing it costs and buys — not just a label. Exactly one option ends with "(Recommended)".
- The last option is always **"Add to open list"** with a description like "I don't know yet — record it as an open question." When chosen, actually enqueue a `Q#` entry under the right owner group. The escape hatch exists so the user is never forced to decide with insufficient information; an escape that silently drops the question would teach them to stop using it.
- One round can carry several related questions; keep unrelated forks in separate rounds so the log stays legible.

Log every round to `qa-log.json` verbatim: the question, every option with its description, and the answer as given. Two rules keep the log honest — clean up spelling and grammar in answers but change nothing of substance, and skip explain-only exchanges entirely (it is a decision log, not a transcript). A custom free-text answer is recorded as-is; `check` flags it as a warning only so you can confirm it was intended.

After the round, distill it: the decision's `round` field points at a condensed `{q, a, n}` entry in the registers `rounds` dict, which the doc shows inline under the decision.

## Open items

Open questions group by **owner** — the person or team who can actually answer, plus a group for spikes. An open list without owners is a wish list; with owners it is an agenda. The `openGroups` map defines the groups and their display order.

## The adversarial review

Run it against the middle draft, after the shape is set but before polish: flaws found late cost a rewrite of prose that was written around them. The reviewer needs no context on why decisions were made, only what they are; a fresh perspective is the point.

- Preferred reviewer: the `codex` plugin skill (an independent model attacking the design). Fallback: a fresh-context subagent with the same brief.
- The brief, roughly: "Adversarial review of a design proposal. Read the registers in this directory. Attack it as a skeptical senior engineer: find correctness bugs, missing failure modes, unjustified numbers, and decisions that don't survive their own stated assumptions. Number your findings and rate severity."
- Save the raw output verbatim as `<reviewer>-review-<date>.md` with a one-line provenance header (tool, model, date, scope).
- Index each finding in the `findings` register as `[n, severity, title, decisionRef]`. This register is data only — the doc never renders it. The review's value ships as the decisions it drove; a "review findings" section in the doc reads as showing off and gives the reader nothing actionable.
- Disposition every finding: a new or superseding `DQ#`, an open item, or a recorded rejection (a sentence in the relevant decision's `x` field saying why the finding doesn't bite). A finding with no disposition is an open bug in the design.

## Timings, ceilings, spikes

Estimates are honest when they are labeled and falsifiable:

- Every number that hasn't been measured is marked `(E)`, and every `(E)` is gated on a named spike `V#` in the open list. The pairing is what separates an estimate from a guess.
- Request paths decompose into segments (`[step, p50, p95, description]`); the doc sums them and animates a trace, so a reader can see where the budget goes.
- Each load ceiling is a row of four: the resource, the ceiling, the first observable symptom, and the guard in front of it. Naming the first symptom is what makes the ceiling operational: it tells the on-call what they'll see.
- The degradation rule: under overload the system backpressures, rejects, or serves stale. If any proposed mechanism has a corruption-shaped failure mode, the mechanism is wrong, not the load.

## NOTES.md

The prose overflow, with a fixed skeleton: **Where things live** (the artifact map), **Method** (one paragraph), **The diagnosis** (root causes of the current system's problems — written in Phase 0, before any design), **Derivations too long for a register field** (bold lead-in paragraphs, one per argument), **Changelog** (dated bullets for milestones), **Loose notes**. When a register field wants three paragraphs of argument, the field gets the conclusion and NOTES.md gets the argument.
