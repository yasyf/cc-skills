# PROJECT_TITLE — Notes

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

The design runs assumption-first: no design until ground truths are recorded
with stable IDs and statuses (Assumed / Needs someone to confirm), then
design by question rounds — every decision traceable to a question, its
options, and the answer, always with an "add to open list" escape.
Supersessions are recorded, never erased.

## The diagnosis

(What is wrong with the current system, stated as root causes rather than
symptoms. Write this, and get the user to agree with it, before designing.)

## Derivations too long for a register field

(Bold lead-in paragraphs, one per argument.)

## Figure briefs

(Five lines per figure, before it is drawn: the point it makes, the decision
it serves by ID, the smallest view that makes the point, what it leaves out,
and the real labels it uses. The first two lines become its caption.)

## The critique

(One row per panel, figure, table and stat: the decision it serves, the
claim it makes, the question a reader is left with, and fixed or cut. A row
with no decision is cut.)

## Changelog

- PROJECT_DATE: Registers scaffolded.

## Loose notes

- (Anything homeless.)
