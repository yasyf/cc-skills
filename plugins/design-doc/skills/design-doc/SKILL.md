---
name: design-doc
description: Run an assumptions-first architecture design and ship it as an interactive design doc. Ground truths land in a register with stable IDs, every design fork goes through a question round with a recorded escape hatch, an adversarial review attacks the middle draft, and timing estimates are gated on named spikes. The deliverable is a registers.json-driven single-file HTML doc plus a generated PDF, written as a humble proposal that explains and asks for feedback. Use when asked to "write a design doc", "architecture proposal", "help me design <system>", "redesign <system>", "assumptions-first design", or to turn a systems discussion into a reviewable design document.
allowed-tools: Bash(python3:*, ls:*, cat:*, pdftoppm:*, wrangler:*, npm:*, open:*, wlm:*, slop-cop:*, uvx:*, ssh:*, rsync:*), Read, Write, Edit, Glob, Grep, AskUserQuestion
---

# design-doc

An architecture design is a stack of decisions on top of a stack of assumptions. This skill runs the design as a conversation — the user decides everything — and renders the result as a document where every claim traces back to an assumption, a decision, and the question round that produced it.

One command drives the mechanical parts:

```bash
TOOL="python3 ${CLAUDE_PLUGIN_ROOT}/skills/design-doc/scripts/design.py"
$TOOL scaffold <dir> [--title X] [--slug x]   # new project from the empty starter
$TOOL scaffold <dir> --example                # tinyq, a small filled-in worked example
$TOOL check <dir>                             # lint the registers; errors exit non-zero
```

Read [reference/method.md](reference/method.md) before Phase 1, [reference/writing.md](reference/writing.md) before Phase 5, and [reference/publish.md](reference/publish.md) before Phase 6 — the method file is the round/register protocol, the writing file is the voice contract, the publish file is the hosting flow. [reference/schema.md](reference/schema.md) is the field-by-field contract for the JSON files; scaffold the tinyq example when you want a filled register next to the schema.

## Terminology

- **Register** — a structured list in `registers.json` whose entries have stable IDs: `A#` assumptions, `DQ#` decisions, `Q#` open questions, `V#` spikes, `c-<slug>` architecture cards.
- **Round** — one AskUserQuestion exchange that settles one or more design questions, logged verbatim in `qa-log.json`.
- **Supersession** — a changed decision gets a new entry and the old one gets `s: "superseded"` plus a `by` pointer. History stays legible because nothing is edited in place.
- **Star** (`★`) — marks the load-bearing assumption, the one whose failure invalidates the document.
- **Spike** — a named, time-boxed experiment (`V#`) that turns an estimate marked `(E)` into a measured number.
- Statuses: decisions are `resolved | superseded | open`; assumptions are `working | validate` ("needs validation" — someone outside the document has to say yes).

## Scope

This skill stops at the design. Its outputs are a decision record and a document; implementation code is a different task that starts after the proposal survives review. Four gates keep the record honest:

- Design forks are the user's to decide. Every fork goes through a round, even when one option looks obviously right: the record of *why* is worth more than the saved exchange.
- `qa-log.json` is verbatim and append-only. Clean up spelling in answers, change nothing of substance, and skip explain-only exchanges; it is a decision log, not a transcript.
- The adversarial review artifact stays out of the rendered doc. Only the decisions it drove ship; a findings section reads as self-congratulation and tells the reader nothing.
- The doc carries no vanity counts ("27 entries", "9 spikes pending"). Numbers appear when they describe the system, not the effort.

## Phase 0 — Scaffold and diagnosis

Run `$TOOL scaffold <dir> --title <name>`. Interview the user about the current system before proposing anything: what exists, what hurts, and why. Write the diagnosis into NOTES.md as root causes rather than symptoms ("durability latency is S3 latency" rather than "writes are slow"), then explain it back and let the user correct it. Designing against a wrong diagnosis wastes every later phase.

**Exit criteria:** the project directory exists; the user has read the diagnosis and agrees with it.

## Phase 1 — Assumptions

Before the first round, check the available-skills list for `cc-present:present` (the `/cc-present` board skill) and invoke it with the Skill tool when listed; the interview runs as a live board from the very first question, with AskUserQuestion only as the no-cc-present fallback. Collect ground truths through rounds: constraints the design must satisfy, facts about scale and workload, things the user believes but hasn't verified. Each becomes an `A#` entry with status `working` or `validate`; star the load-bearing one, and record who has to confirm each `validate` entry as an open item. When the user flags an assumption as shaky, that flag goes in the entry; resolving it on their behalf would defeat the point of the register.

**Exit criteria:** the user confirms the register covers what they know; `$TOOL check` is clean.

## Phase 2 — Design rounds

Design by question rounds, one fork at a time. Rounds run on a live `cc-present` board: before the first round (in Phase 1 and again here), check the available-skills list for `cc-present:present` (the `/cc-present` board skill) and invoke it with the Skill tool — its instructions govern composing and pushing the board, and every round from then on goes through a board (one card per question, a choice block whose option hints carry the consequences). AskUserQuestion is the fallback surface, used only when `cc-present:present` is absent from the skill list. Either way the shape holds: options carry real consequence descriptions, exactly one is marked "(Recommended)", and the last option is always "Add to open list" — an escape that actually enqueues a `Q#` entry rather than forcing a choice. The rounds are an interview, not a survey: point out flaws in the current draft, suggest alternatives the user didn't name, push back where the evidence disagrees, and decide nothing yourself. Each answer becomes a `DQ#` with the resolution, the rejected alternatives, and the round number. When a later round changes an earlier decision, supersede: a new `DQ#`, a `by` pointer on the old one.

**Exit criteria:** no undecided fork remains outside the open list; every `DQ#` traces to a round in `qa-log.json`.

## Phase 3 — Adversarial review

Attack the middle draft, before polish makes flaws harder to see. Use the `codex` plugin skill when it's available; otherwise spawn a fresh-context subagent with no stake in the design and a brief to attack it as a skeptical senior engineer: correctness bugs, missing failure modes, unjustified numbers. Save the output verbatim as `<reviewer>-review-<date>.md`, index each finding in the `findings` register (data only — never rendered), and disposition every one: a new decision, an open item, or a recorded rejection with a reason. Then run the reviewer again on the updated registers: dispositions change the design, and a changed design grows new flaws. One pass is the floor, not the norm.

**Exit criteria:** every finding has a disposition, and the latest pass produced nothing that changes a decision.

## Phase 4 — Timings, ceilings, spikes

Skip this phase when the system has no latency or load story — the doc hides empty sections. Otherwise: give each request path p50/p95 segments marked `(E)`, give each resource a ceiling row (ceiling, first symptom, guard), and hold the degradation rule: under load the system backpressures, rejects, or goes stale; a design whose overload mode corrupts data is off-design. Every `(E)` is gated on a named `V#` spike, because an estimate nobody plans to measure is a guess wearing a costume.

**Exit criteria:** no `(E)` without a spike; every ceiling row has a guard.

## Phase 5 — The document

Read [reference/writing.md](reference/writing.md) first; the voice contract lives there. Fill `meta` in registers.json (title, date, slug, tagline, banner for the starred assumption). Hand-draw the system SVG and replace the placeholder between the `<!--SYSD-->` markers. The diagram is the one part of the HTML you edit; everything else renders from the JSON. Write the doc content in two passes: structure and de-jargoning first, then a separate tone pass whose test for every sentence is "does this solicit feedback, or make a claim?"; the doc exists to be corrected, not admired. When `wlm profile list` shows a profile for the author, write against their style card and run `wlm adversary critique` on the exported Markdown; the exact invocations are in [reference/writing.md](reference/writing.md). Run `slop-cop check` after each pass. Then `python3 build-pdf.py` and look at the pages with `pdftoppm`: a structural check tells you the PDF exists; only your eyes tell you it renders.

**Exit criteria:** `$TOOL check` is clean; the doc renders over `python3 -m http.server 8641`; the PDF is built and visually inspected.

## Phase 6 — Publish and handoff

Stage a clean deploy folder holding only the files meant to ship:

```bash
mkdir -p dist
cp design-doc.html dist/index.html
cp registers.json qa-log.json NOTES.md design-doc.pdf dist/
```

Ask the user where it goes, then follow [reference/publish.md](reference/publish.md): local serving is `python3 -m http.server 8641`; public hosting is `wrangler deploy` when authenticated, or `wrangler deploy --temporary`, which returns a claim URL that expires in 60 minutes; hand that to the user immediately. After deploying, one lightweight check (the page loads with the right title) is enough; exhaustive per-asset probing after a confirmed deploy is noise. Add a changelog entry to NOTES.md.

**Exit criteria:** the user has the URL or serve command; the changelog records what shipped.

## Common issues

- **PDF step fails with "no Chrome found"** — install Chrome/Chromium or set `CHROME=/path/to/chrome`. `build-pdf.py` exits 2 with instructions.
- **Doc shows a "Data not loaded" screen** — it was opened as `file://`; browsers block local-file fetch. Serve the folder over HTTP as the screen says.
- **wlm voice profile absent** — the writing contract in `reference/writing.md` includes a standalone voice fallback; the wlm style card is an upgrade, not a dependency.
