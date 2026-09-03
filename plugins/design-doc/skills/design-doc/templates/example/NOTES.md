# tinyq — Notes

The prose companion to the structured registers. Everything with a stable ID
lives in `registers.json` (canonical; rendered by `design-doc.html`); verbatim
question rounds live in `qa-log.json`. This file holds what doesn't fit
structure: method, derivations, longer arguments, and anything homeless.

## Where things live

- `registers.json` — assumptions, decisions, architecture sections, open items,
  timings, ceilings, the system diagram as Mermaid source under `diagram`,
  plus the doc-facing content: tl;dr, ground rules, terms, footnotes. Every
  rendered entry carries its exact wording and a plain twin `p`, which is what
  the doc shows first; decisions and assumptions carry `key` and `theme`.
  Edit this to change the design doc.
- `summary.html` — the executive summary, hand-written for people and never
  parsed: a deck of 3–5 `<section class="xs-panel" data-kind="…">` panels, or
  one `poster`, that the doc opens on full-screen and the PDF prints as headed
  sections. Use the doc's own CSS variables and the `.xs-*` kit classes so it
  themes in light and dark, and give every panel one figure: inline SVG or a
  `<pre class="mermaid">` block.
- `design-doc.html` — the interactive doc. Renders from registers.json, so it
  must be served over HTTP (`python3 -m http.server 8641` in this folder);
  opened as a bare file it shows instructions instead. The ↓ button downloads
  the synthesized Markdown; the PDF button prints the page; the footer links
  open the registers, question log, and this file as rendered Markdown in a
  modal.
- `design-doc.pdf` — the served doc printed through its own print stylesheet
  by the design-doc skill's `design.py pdf` via headless Chrome, the same
  document the PDF button prints. Generated, never checked in; rerun after
  editing.
- `qa-log.json` — the full log of every question round: options offered and
  answers as given (lightly copyedited). Explain-only exchanges are not logged.
- This file — prose.

## Method

The design ran assumption-first: no design until ground truths were recorded
with stable IDs and statuses (Assumed / Needs someone to confirm), then
design by question rounds — every decision traceable to a question, its
options, and the answer, always with an "add to open list" escape.
Supersessions are recorded, never erased: A4 was revised once, DQ3 retired
into DQ4.

## The diagnosis

The current "queue" is a database table polled by every worker on a 5-second
timer. Two root causes, everything else symptoms: dispatch latency is the poll
interval, not the work, and the poll query scans the whole table, so load
grows with backlog size rather than throughput. tinyq inverts both: dispatch
is a push to a parked long-poll, and the hot set is the queue head, not the
table.

## Derivations too long for a register field

**Why the lease deadline is the only failure story.** Every worker failure —
crash, hang, network partition — looks identical to the dispatcher: renewals
stop. Collapsing all failure modes into one (lease expiry → re-queue) is what
keeps the protocol small; the price is duplicate delivery on expiry, which A1
says consumers absorb via idempotency keys.

## Changelog

- 2026-07-21: Registers collected (A1–A4); rounds 1–2 produced DQ1–DQ4;
  adversarial review absorbed (2 findings, both dispositioned into DQ1/DQ2).

## Loose notes

- The platform-team conversation (Q1) is the load-bearing external dependency;
  bring the idempotency-key story and the 7-day completed-key window.
