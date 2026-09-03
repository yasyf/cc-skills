---
name: design-doc
description: Run an assumptions-first architecture design and ship it as an interactive design doc. Ground truths land in a register with stable IDs, every design fork goes through a question round with a recorded escape hatch, an adversarial review attacks the middle draft, and every estimate is gated on a named spike. The deliverable is a registers.json-driven single-file HTML doc that opens on a hand-written executive summary, cites every entry by a human-readable handle, carries declared interactive components, and answers questions in the page when an AI config is deployed beside it, plus a generated PDF, written as a humble proposal that explains and asks for feedback. Use when asked to "write a design doc", "architecture proposal", "help me design <system>", "redesign <system>", "assumptions-first design", or to turn a systems discussion into a reviewable design document.
allowed-tools: Bash(python3:*, ls:*, cat:*, pdftoppm:*, wrangler:*, npm:*, open:*, wlm:*, slop-cop:*, uvx:*, ssh:*, rsync:*, cc-present:*), Read, Write, Edit, Glob, Grep, AskUserQuestion
---

# design-doc

An architecture design is a stack of decisions on top of a stack of assumptions. This skill runs the design as a conversation — the user decides everything — and renders the result as a document where every claim traces back to an assumption, a decision, and the question round that produced it.

One command drives the mechanical parts:

```bash
TOOL="python3 ${CLAUDE_PLUGIN_ROOT}/skills/design-doc/scripts/design.py"
$TOOL scaffold --title <name>   # fresh ./<slug>/ directory for this one design doc
$TOOL scaffold --example        # ./tinyq/, a small filled-in worked example
$TOOL check <dir>               # lint the registers, the plain twins, the diagram source and the summary deck; errors exit non-zero, --strict makes the publish-blocking warnings errors too
$TOOL summary-text <dir>        # the deck as Markdown, one ## section per panel; the gates' input
$TOOL plainify <dir> [--only DQ3,A2] [--dry-run] # draft a plain twin and a handle for every entry that lacks one; review every line it writes
$TOOL glossary <dir>            # terms the prose uses and the glossary lacks, as candidate entries
$TOOL build <dir>               # compile components/*.tsx into components.js; only for a doc with a components/ directory
$TOOL render-check <dir>        # render every Mermaid block in headless Chrome and fail on a parse error
$TOOL pdf <dir>                 # print the doc through the template's print stylesheet into <dir>/design-doc.pdf
$TOOL snapshot <dir> --note "…" --item "…" # stamp a revision the changes-since view diffs against; note is the reader-facing headline, each --item one change
```

Read [reference/method.md](reference/method.md) before Phase 1, [reference/writing.md](reference/writing.md) before Phase 5, and [reference/publish.md](reference/publish.md) before Phase 6 — the method file is the round/register protocol, the writing file is the voice contract, the publish file is the hosting flow. [reference/schema.md](reference/schema.md) is the field-by-field contract for the JSON files; scaffold the tinyq example when you want a filled register next to the schema.

## Terminology

- **Register** — a structured list in `registers.json` whose entries have stable IDs: `A#` assumptions, `DQ#` decisions, `Q#` open questions, `V#` spikes, `c-<slug>` architecture cards.
- **Round** — one AskUserQuestion exchange that settles one or more design questions, logged verbatim in `qa-log.json`.
- **Supersession** — a changed decision gets a new entry and the old one gets `s: "superseded"` plus a `by` pointer, which the doc renders as "Replaced by" and the successor's title. History stays legible because nothing is edited in place.
- **Star** (`★`) — marks the load-bearing assumption, the one whose failure invalidates the document.
- **Spike** — a named, time-boxed experiment (`V#`) that turns an estimate marked `(E)` into a measured number.
- **Executive summary** — `summary.html`, a hand-written HTML fragment beside `registers.json`, for the reader who reads nothing else: a deck of full-screen panels by default, one poster when the design is one idea and one figure. Never parsed as data; `check` lints its shape, its word budgets, and its citations.
- **Plain twin** — the `p` field beside an entry's precise wording, one or two short sentences saying what the entry means to someone who will not read it. The doc shows twins by default and the exact wording one toggle away.
- **Key** and **theme** — `key: true` marks the 3–8 decisions and assumptions the doc opens on as cards; `theme` files every entry under one of the design's 3–5 themes, named in Phase 0 and defined in the `themes` map.
- **Diagram** — the system diagram's Mermaid source, in the `diagram` register key; the doc renders it, the reader pans and hovers it, and the request paths step through it. `diagram.overview` is the ten-node subset the Overview card draws, with the full graph one click away. A hand-drawn `sysd.svg` stays valid as `diagram.kind: "svg"`.
- **Handle** — the `h` field on every decision, assumption, open item, arch card, and numbers table: what a reader would call it, two to five words. Citations and chip rows show the handle; the ID lives in the hover card.
- **Component** — an interactive figure declared in the `components` map (`dd.tabs`, `dd.before-after`, `dd.whatif`, `dd.steps`, `dd.timeline`, `dd.matrix`) and placed by id in the deck or on an entry; a TSX file under `components/` is the escape hatch `build` compiles.
- Statuses: decisions are `resolved | superseded | open`, which the doc renders as Decided, Replaced, and Still open; assumptions are `working | validate`, rendered Assumed and Needs someone to confirm (someone outside the document has to say yes). Prose uses the rendered words, JSON the values.

## Scope

This skill stops at the design. Its outputs are a decision record and a document; implementation code is a different task that starts after the proposal survives review. Four gates keep the record honest:

- Design forks are the user's to decide. Every fork goes through a round, even when one option looks obviously right: the record of *why* is worth more than the saved exchange.
- `qa-log.json` is verbatim and append-only. Clean up spelling in answers, change nothing of substance, and skip explain-only exchanges; it is a decision log, not a transcript.
- The adversarial review artifact stays out of the rendered doc. Only the decisions it drove ship; a findings section reads as self-congratulation and tells the reader nothing.
- The doc carries no vanity counts ("27 entries", "9 spikes pending"). Numbers appear when they describe the system, not the effort.

## Phase 0 — Scaffold and diagnosis

Run `$TOOL scaffold --title <name>`. Every design doc lives in its own fresh directory — scaffold creates `./<slug>/` (pass an explicit path as a positional argument to put it elsewhere) and refuses a non-empty target, so one design never mixes into another's files or an existing project's. Interview the user about the current system before proposing anything: what exists, what hurts, and why. Write the diagnosis into NOTES.md as root causes, not symptoms ("durability latency is S3 latency", not "writes are slow"). Put it back to the user as one `design-doc.claims` block — a claim per root cause, its evidence in `because`, and what it costs to be wrong in `ifFalse` — and fold every correction back in the user's own words. Close the diagnosis with the one classification the executive summary hangs on: a **change** to a system that exists today, or **net-new**, with nothing behind it but requirements. Name the design's three to five themes in the same breath ("storage", "dispatch", "tenancy"): they become the `themes` map the decisions and assumptions file under, and a theme the diagnosis cannot name is a theme the design does not have. Designing against a wrong diagnosis wastes every later phase.

**Exit criteria:** the project directory exists; the user has read the diagnosis, change or net-new and the themes included, and agrees with it.

## Phase 1 — Assumptions

Before the first round, check the available-skills list for `cc-present:present` (the `/cc-present` board skill) and invoke it with the Skill tool when listed; the interview runs as a live board from the very first question, with AskUserQuestion only as the no-cc-present fallback. This plugin ships its own block pack — run `cc-present pack list` and read `design-doc`'s reference fragment; the three blocks it adds are the interview surface.

Sweep candidate ground truths through one `design-doc.claims` block instead of a question each, and pin a `design-doc.registers` block on every board so the human answering round five can still see what rounds one through four established. A claim's `label` is the assumption as a sentence a person can confirm cold, never an ID like "A7"; the register assigns the ID after the answer. Collect ground truths through rounds: constraints the design must satisfy, facts about scale and workload, things the user believes but hasn't verified. Each becomes an `A#` entry with status `working` or `validate`; star the load-bearing one, and record who has to confirm each `validate` entry as an open item. When the user flags an assumption as shaky, that flag goes in the entry; resolving it on their behalf would defeat the point of the register.

**Exit criteria:** the user confirms the register covers what they know; `$TOOL check` is clean.

## Phase 2 — Design rounds

Design by question rounds, one fork at a time. Rounds run on a live `cc-present` board: before the first round (in Phase 1 and again here), check the available-skills list for `cc-present:present` (the `/cc-present` board skill) and invoke it with the Skill tool — its instructions govern composing and pushing the board, and every round from then on goes through a board. One fork is one `design-doc.fork` block, whose options carry their consequence inline and whose escape hatch returns `{defer: {title, why}}`: the human names the open question, so the `Q#` lands without spending another round asking what to call it. Where the pack is missing, a plain `choice` block carries the same shape by hand — consequence descriptions on every option, exactly one marked "(Recommended)", and a last option "Add to open list" that actually enqueues a `Q#` — and AskUserQuestion is the surface of last resort, used only when `cc-present:present` is absent from the skill list. Ask each fork as a plain question a person can answer cold ("How should workers receive jobs?"). The register ID it settles goes in the block's `decides` field, or trails the AskUserQuestion `header`, never in the question text; an option's consequence says what happens, not which entry it supersedes. The rounds are an interview, not a survey: point out flaws in the current draft, suggest alternatives the user didn't name, push back where the evidence disagrees, and decide nothing yourself. Each answer becomes a `DQ#` with the resolution, the rejected alternatives, and the round number. When a later round changes an earlier decision, supersede: a new `DQ#`, a `by` pointer on the old one.

**Exit criteria:** no undecided fork remains outside the open list; every `DQ#` traces to a round in `qa-log.json`.

## Phase 3 — Adversarial review

Attack the middle draft, before polish makes flaws harder to see. Use the `codex` plugin skill when it's available; otherwise spawn a fresh-context subagent with no stake in the design and a brief to attack it as a skeptical senior engineer: correctness bugs, missing failure modes, unjustified numbers. Save the output verbatim as `<reviewer>-review-<date>.md`, index each finding in the `findings` register (data only — never rendered), and disposition every one: a new decision, an open item, or a recorded rejection with a reason. A finding that needs the user's call becomes a round like any other, whose question says what the reviewer found, in words; the finding number stays in the register. Then run the reviewer again on the updated registers: dispositions change the design, and a changed design grows new flaws. One pass is the floor, not the norm.

The register stops moving in this phase, so this is where every rendered entry gets its plain twin and its handle. `p` sits beside the precise wording of each tl;dr line, ground rule, decision, assumption, and open item, written by the hand that wrote the entry while the reasoning is fresh; `h` is the two-to-five-word name a citation shows for it. The twin says what the entry means to someone who will not read it, the handle what they would call it; both contracts are in [reference/writing.md](reference/writing.md). `$TOOL plainify <dir>` drafts a twin and a handle for every entry that lacks one and prints a review table. Read each draft against its original and edit it before moving on; a twin that drifts from its entry is a second claim the registers do not back. In the same pass, rewrite every decision title that is still a question as the answer, a noun phrase of at most ten words; the question stays in `rounds[].q`.

**Exit criteria:** every finding has a disposition, and the latest pass produced nothing that changes a decision; every rendered entry carries a twin and a handle you have read, no decision title ends in a question mark, and `$TOOL check` is clean.

## Phase 4 — The quantitative story

Latency is one axis a design can be measured on, not the default. The doc carries a small library of quantitative components; pick the ones that describe this system, skip the rest (the doc hides empty sections), and skip the phase entirely when the design has no quantitative story:

- **Request paths** (`paths`, plus the `scaleMarks` strip) — for designs whose story is latency: p50/p95 segments per step, summed and traceable.
- **Load ceilings** (`ceilings`) — for designs whose story is load: each resource gets a ceiling, its first observable symptom, and the guard in front of it.
- **Number tables** (`numbers`) — any other axis, each table with its own columns: a throughput budget, storage growth, a cost model, freshness windows.

Two rules hold whichever components are in play. Every unmeasured number is marked `(E)` and gated on a named `V#` spike, because an estimate nobody plans to measure is a guess wearing a costume. And under load the system backpressures, rejects, or goes stale; a design whose overload mode corrupts data is off-design.

**Exit criteria:** no `(E)` without a spike; every ceiling row has a guard.

## Phase 5 — The document

Read [reference/writing.md](reference/writing.md) first; the deck contract, the twin contract, and the voice contract live there, and its Figures section sends you to `reference/gallery/` before you draft. Fill `meta` in registers.json (title, date, slug, banner for the starred assumption). Write `summary.html` before anything else: a deck of full-screen panels, thesis first, built from the diagnosis (what exists today, or what is required), the resolved decisions (what this proposes), and the numbers, with one figure drawn for this design on every panel. When the design is one idea with one figure, write a single poster panel instead and say so in NOTES.md. The panel kinds, the kit, and the word budgets are in [reference/schema.md](reference/schema.md); `check` enforces the budgets. Put the system diagram's Mermaid source in the `diagram` register key, its nodes named after the `arch` cards they belong to, so the doc can pan it, highlight it, and step the request paths through it. Write `diagram.overview` beside it: the same graph cut to ten nodes, ids drawn from the full source, which is what the Overview card draws. Where a number or a sequence earns it, declare one or two components in the `components` map and place them, in a deck figure or on an entry; the kinds and the escape hatch are in [reference/writing.md](reference/writing.md). Every label, in the diagrams, the deck, the chips, and the table heads, follows one capitalisation rule: acronyms upper-case (Tenant API, DPoP), product names as their owners write them (Postgres), identifiers in backticks, everything else sentence case. `check` lints labels against the acronym list in `design.py`, extended by `meta.acronyms`. Then set `key: true` on the 3–8 decisions and assumptions the doc should open on and file every one under a theme from Phase 0; write the tl;dr as three to five plain twins of at most 20 words each. Both files sit beside `registers.json` and the doc loads them at runtime, so the HTML itself is never edited. Then write the doc content in two passes: structure and de-jargoning first, then a separate tone pass whose test for every sentence is "does this solicit feedback, or make a claim?"; the doc exists to be corrected, not admired.

Five gates run over the summary, in this order, and each is a required step, not an available upgrade:

1. Load the `writing-docs` skill by path, `~/.claude/plugins/cache/skills/writing-docs/0.7.0/skills/writing-docs/SKILL.md`, and run its edit passes over the deck: completeness, then accuracy, then structure, then clarity, then brevity.
2. Run the voice gate: `wlm profile list` first, always; with a profile, read the style card before drafting, then run `wlm -p <profile> adversary critique` over the output of `$TOOL summary-text <dir>` and over the exported Markdown, and fold in or explicitly reject every flag. The critique needs machine negatives and exits without a verdict when it has none; then, or without a profile, the fallback contract applies, and NOTES.md records which one graded the doc.
3. Run `slop-cop check summary.html --lang=html --llm-effort=off`, then `$TOOL summary-text <dir> > summary.md` and `slop-cop check summary.md --lang=markdown --llm-effort=off`.
4. `$TOOL check --strict <dir>`: handles on every cited entry, no question titles, capitalisation, the word caps, component props, overview ids.
5. `$TOOL render-check <dir>`: every Mermaid block and every component draws in headless Chrome.

Triage every finding in NOTES.md: fixed, or rejected with a reason. The numbered checklist with exact invocations is in [reference/writing.md](reference/writing.md). Run `slop-cop check` over the doc's Markdown after each pass as well. Then `$TOOL pdf <dir>` and look at the pages with `pdftoppm`: a structural check tells you the PDF exists; only your eyes tell you it renders.

**Exit criteria:** the doc renders over `python3 -m http.server 8641` and opens on the deck with nothing else visible; the PDF is built and visually inspected. All five gates ran, in order, and every finding they raised is triaged in NOTES.md.

## Phase 6 — Publish and handoff

Stage a clean deploy folder holding only the files meant to ship:

```bash
$TOOL snapshot . --note "<headline>" --item "<one change, for the reader>"
mkdir -p dist
cp design-doc.html dist/index.html
cp registers.json qa-log.json NOTES.md summary.html dist/
cp -R history dist/
```

A doc with a `components/` directory runs `$TOOL build .` first and copies `components.js` too. AI in the page is switched on by an `ai.json` beside `index.html`, or one directory up for a collection, holding `{endpoint, model, key}` or `{"disabled": true}`; the file is written at deploy from a secret and never committed.

Nothing else ships. The diagram source is in the registers, the PDF button prints the page on the fly through the template's print stylesheet, and the diagram libraries load from a CDN at pinned versions. So `dist/` carries no `sysd.svg`, no PDF, and no vendored code. A doc that kept a hand-drawn diagram (`diagram.kind: "svg"`) copies `sysd.svg` too. The snapshot stamps this publish as a revision; it hashes `summary.html` with the registers, so an edit to the deck is a revision on its own. From the second one onward, a returning reviewer lands directly in the diff against the revision they last read, unchanged content tucked behind a toggle. The note and `--item` bullets are the first thing that reader sees: write them in plain language for someone coming back after days — what changed and what it means for them, never round numbers or register IDs (the diff panel already lists those). The contract with a worked example is in [reference/publish.md](reference/publish.md), and revision prose passes the same voice gate as the doc — `wlm profile list`, style card, adversary critique over the drafted note — before the snapshot is stamped.

Ask the user where it goes, then follow [reference/publish.md](reference/publish.md): local serving is `python3 -m http.server 8641`; public hosting is `wrangler deploy` when authenticated, or `wrangler deploy --temporary`, which returns a claim URL that expires in 60 minutes; hand that to the user immediately. After deploying, one lightweight check (the page loads with the right title) is enough; exhaustive per-asset probing after a confirmed deploy is noise. Add a changelog entry to NOTES.md naming the deploy name and live URL — later register edits redeploy through the same name to the same URL, with the exact sequence in [reference/publish.md](reference/publish.md).

**Exit criteria:** the user has the URL or serve command; the changelog records what shipped; the revision snapshot is recorded, its note and items having passed the voice gate first.

## Common issues

- **PDF step fails with "no Chrome found"** — install Chrome/Chromium or set `CHROME=/path/to/chrome`. `$TOOL pdf` and `$TOOL render-check` exit 2 with instructions.
- **Diagram host says "Diagrams need a network connection"** — the doc imports Mermaid from jsdelivr at a pinned version and the import failed. The rest of the doc works; `$TOOL pdf` and `$TOOL render-check` need the same network and say so when it is missing.
- **Doc shows a "Data not loaded" screen** — it was opened as `file://`; browsers block local-file fetch. Serve the folder over HTTP as the screen says.
- **Doc opens on the tl;dr with no Summary section** — `summary.html` is missing beside `registers.json`, or wasn't copied into `dist/`. `$TOOL check` warns about the first.
- **`check` warns "hand-drawn diagram"** — the project still carries a `sysd.svg` and no Mermaid source. Move the diagram into `diagram.source`, or set `diagram.kind: "svg"` to keep the file; the migration sequence is in `reference/publish.md`.
- **wlm voice profile absent** — the fallback voice contract in `reference/writing.md` applies. A missing profile excuses the style card, not the `wlm profile list` check that discovered it.
- **No Ask bar, no Cmd-K AI row** — the page found no `ai.json` beside `index.html` or one directory up, or it holds `{"disabled": true}`. Local work sets the `design-doc-ai` key in `localStorage`; a deploy writes the file from a secret, per `reference/publish.md`.
- **`check` errors on a `data-component`** — the host names an id the `components` map lacks, or the entry's props fail its kind's schema under `reference/components/`. Declare the entry, then place it.
