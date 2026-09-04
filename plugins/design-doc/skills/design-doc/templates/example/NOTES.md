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

## Figure briefs

Five lines before any figure is drawn: the point it makes, the decision it
serves, the smallest view that makes the point, what it leaves out, and the
real labels it uses. The first two lines become the figure's caption.

**The system diagram.** Point: a job is safe once the log accepts it, and
everything downstream is rebuilt from the log. Decision: DQ1, DQ2. View: the
four parts on the enqueue-to-deliver path, plus the archive. Leaves out: the
idempotency-key lookup and the lease timer wheel, which are steps inside a
part. Labels: Producers, Job log, Dispatcher, Workers, Object storage.

**Thesis, the path.** Point: one durable thing, and a worker already waiting.
Decision: DQ1. View: the same path, one line per flow. Leaves out: the
restore path from the archive. Labels: enqueue, lease, push, after a day.

**Compare, the two lanes.** Point: the poll becomes a push and the table
becomes a log. Decision: DQ1, DQ2. View: today's table and workers beside
the proposed log, dispatcher, and workers, one tenant per lane. Leaves out:
a second tenant; the isolation argument is DQ1's text. Labels: Jobs table,
Three workers, Finished rows; Job log, Dispatcher, Two workers, Object
storage.

**Numbers, the latency scale.** Point: dispatch waits on a push, not a
five-second timer. Decision: DQ4, measured by V1. View: two points on one
log scale. Leaves out: enqueue latency, which the timings section carries.
Labels: 5s today, 8ms proposed.

**Cost, the fork.** Point: the design stays small only while every consumer
absorbs a duplicate. Decision: DQ2, gated on A1. View: one fork with two
outcomes. Leaves out: what a dedup ledger would cost; that is a different
design. Labels: every consumer tolerates a duplicate, log plus leases, dedup
ledger in the hot path.

## The critique

One row per panel, figure, table and stat: the decision it serves, the claim
it makes, the question a reader is left with, and whether it was fixed or
cut. A row with no decision is cut.

| Part | Decision | Claim | Open question | Fixed or cut |
|---|---|---|---|---|
| Thesis panel | DQ1, DQ4 | One log per tenant, workers already waiting | When is a job safe? | Fixed: the lede names the ack. |
| Thesis figure | DQ1 | One durable thing, everything else rebuilt from it | Where do old jobs go? | Fixed: the archive edge. |
| Compare panel | DQ1, DQ2, DQ4 | The poll becomes a push, the table a log | Who wins when two workers race? | Fixed: the lease row. |
| Compare figure | DQ1, DQ2 | Three pollers on one table, against one log and its leases | Why two workers on the right? | Fixed: the caption says parked. |
| Numbers panel | DQ4 | Dispatch drops from seconds to milliseconds | Measured or estimated? | Fixed: every stat is tagged estimated. |
| p95 stat | DQ4 | 8ms from enqueued to running | Against what today? | Fixed: the delta shows 5s. |
| p50 stat | DQ1 | 2ms enqueue ack | What bounds it? | Fixed: the throughput table names the flush loop. |
| Sustained stat | DQ1 | 3k jobs a second per tenant | Per tenant or total? | Fixed: the label says per tenant. |
| Backlog stat | DQ1 | A backlog costs nothing extra | Why flat? | Fixed: the what-if shows the scan today. |
| Numbers figure | DQ4, V1 | Two points on one log scale | Is 8ms measured? | Fixed: the caption names the spike. |
| Throughput table | DQ1, V1 | One file per tenant sustains 3k jobs a second | What happens above it? | Fixed: the note points at ceilings. |
| Cost panel | DQ2, A1 | Every producer sets a key; one file per tenant to back up | What if a consumer cannot dedup? | Fixed: the fork figure. |
| Cost figure | DQ2, A1 | The design stays small only while duplicates are safe | What would the ledger cost? | Cut: a different design. |
| Ops box in the thesis draft | none | The dispatcher's timer wheel | | Cut: no decision. |

## Changelog

- 2026-07-21: Registers collected (A1–A4); rounds 1–2 produced DQ1–DQ4;
  adversarial review absorbed (2 findings, both dispositioned into DQ1/DQ2).

## Loose notes

- The platform-team conversation (Q1) is the load-bearing external dependency;
  bring the idempotency-key story and the 7-day completed-key window.
