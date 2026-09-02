# The register schemas

The contract between `registers.json`, `qa-log.json`, the two hand-written files beside them (`summary.html`, `sysd.svg`), the HTML renderer, and the `design.py` driver (`check`, `pdf`, `snapshot`, `summary-text`). Markdown-bearing string fields support a mini dialect: `[text](url)` links, `` `code` ``, `**bold**`, `*italic*`, and `[^n]` footnote tokens. The tinyq example (`design.py scaffold <dir> --example`) is a filled instance of everything below.

## registers.json

### `meta` — document identity (the only per-project HTML config)

| Field | Required | Meaning |
|---|---|---|
| `title` | yes | h1, nav brand, browser title |
| `slug` | yes | download filenames: `<slug>-design-doc.md` |
| `date` | yes | shown in the date line |
| `subtitle` | no | defaults to "Design proposal" |
| `phase` | no | e.g. "draft"; shown in the date line |
| `draft` | no | boolean, default false. True pins a grey draft banner to the top of the doc, prefixes the browser title with `[DRAFT]`, and opens the Markdown export with a draft blockquote. Independent of `phase`, which is free text the date line prints verbatim |
| `draftNote` | no | replaces the banner's default sentence ("This document is in progress and will change. It is not ready for review.") |
| `banner` | no | `{assumption, text}` — the warning card for the starred assumption; `assumption` must be a real `A#`; omit the key to omit the card |
| `diagramCaption`, `timingsCaption` | no | captions under the diagram and timing strip |
| `footerNote` | no | appended to the footer and the date lines |
| `homeLink` | no | `{href, label}` — a back link the rail renders above the brand, for a doc that lives in a collection (`{"href": "../", "label": "← All docs"}`); `check` errors on any other shape |
| `sections` | no | `{<sectionId>: {sub: "…"}}` one-line sub-copy under a section header; by default headers stand alone, so author one only when it carries design content the section body doesn't. Ids are `ground`, `architecture`, `paths`, `numbers`, `ceilings`, `decisions`, `assumptions`, `open`, `footnotes` |
| `canonical` | no | a sentence stating what lives in which file, for readers of the raw JSON |
| `rev` | no | current revision number; written by `design.py snapshot`, never by hand |
| `revisions` | no | `[{rev, date, note, items?, changed?, files?}]`, one entry per snapshot; written by `design.py snapshot`, never by hand. `files` holds the digests of `summary.html` and `sysd.svg` at that snapshot, and `changed` names the ones that differ from the previous entry's (`summary`, `sysd`); the changes-since view flags the Summary section from it |

Everything else about the HTML is fixed: the section skeleton, the status vocabularies, the artifact filenames (`registers.json`, `qa-log.json`, `NOTES.md`, `summary.html`, `sysd.svg`, `design-doc.pdf`). The renderer leads every entry with its title and shows the ID as a small secondary chip; a cross-reference (`by`, `arch.dq`, a "(A2)" citation in prose) renders as the target's title with the ID trailing and a hover tooltip. The JSON values are what `check` verifies; the titles are what the reader sees.

### `sysd.svg` and `summary.html`

Two hand-written files sit beside `registers.json`. The doc fetches both at runtime and `design.py pdf` reads them from disk, so the HTML template is never edited; scaffold leaves a placeholder of each to replace.

`sysd.svg` is the system diagram: one `<svg>` with a `viewBox` and no fixed `width`/`height`, drawn with the `.sysd` classes the doc styles in both colour schemes (`grp`, `bx`, `bxo`, `dur`, `ln`, `tt`, `ac`, `sm`, `tag`). The renderer scales it to the column and offers a full-size view when it's much wider than the text; `check` warns when the `viewBox` is missing, since nothing can scale without it.

`summary.html` is the executive summary, for people only: it's never parsed as data, and no register field points at it. It's a body-level fragment, injected into the doc's first section: no `<html>`, `<head>`, `<body>`, or `<script>`, each an error in `check`. It inherits the doc's styles and themes through the CSS variables `--bg`, `--bg2`, `--ink`, `--ink2`, `--line`, `--accent`, `--accent-soft`, `--ok`, `--warn`, `--card`, `--mono`, `--sans`. Inline `<svg>` diagrams use the `.sysd` classes above, and there are no new colours. A missing file hides the section and its rail link, and `check` warns, since the doc then opens without an executive summary. A register ID in the text (`DQ4`, `A2`, `Q3`, `V1`, `c-log`) becomes a hoverable link to its entry when the entry exists and a `check` warning when it doesn't, so citations stay honest; the content contract is in [writing.md](writing.md).

The renderer and the PDF both style a small kit the fragment may use. Any valid fragment renders, with or without it:

| Class | Renders as |
|---|---|
| `.xs-lede` | the large opening paragraph |
| `.xs-tiles` > `.xs-tile` | headline tiles; inside a tile, `<b>` is the label, `<span class="xs-before">` and `<span class="xs-after">` a before/after pair or `<span class="xs-value">` a single value, `<small>` a note |
| `.xs-compare` | rows of `<div class="xs-row">` holding `.xs-topic`, `.xs-before`, `.xs-after`, and an optional muted `.xs-why`; a row stacks below 900px |
| `.xs-figure` | a figure with a `<figcaption>` |
| `.xs-cols` | two side-by-side columns, for a before/after diagram pair |

Ordinary `h3`, `p`, `ul`, and `table` inherit the doc styles. A fragment using most of the kit:

```html
<p class="xs-lede">Workers poll a jobs table today; this proposal replaces the table with a log and hands out leases, so dispatch stops costing one scan per worker per tick.</p>
<div class="xs-tiles">
  <div class="xs-tile"><b>Dispatch p95</b><span class="xs-before">480 ms</span><span class="xs-after">12 ms</span><small>estimated, pending V1</small></div>
  <div class="xs-tile"><b>Retries per job</b><span class="xs-value">at most 5</span><small>unbounded today</small></div>
</div>
<figure class="xs-figure">
  <div class="xs-cols">
    <svg viewBox="0 0 320 120"><rect class="bx" x="10" y="40" width="120" height="40"/><text class="tt" x="70" y="65">jobs table</text></svg>
    <svg viewBox="0 0 320 120"><rect class="bxo" x="10" y="40" width="120" height="40"/><text class="tt" x="70" y="65">job log</text></svg>
  </div>
  <figcaption>Left, today's polled table. Right, the log with per-job leases.</figcaption>
</figure>
<div class="xs-compare">
  <div class="xs-row"><div class="xs-topic">Dispatch</div><div class="xs-before">Every worker scans the table every 500 ms.</div><div class="xs-after">A worker holds a long-poll and receives a lease (DQ4).</div><div class="xs-why">A scan per worker per tick is the cost that grows with the fleet.</div></div>
</div>
<h3>What we are not changing</h3>
<p>Jobs stay one SQLite file per tenant (DQ1), and consumers keep deduplicating on their idempotency key (A1).</p>
```

### Revision history (`history/`)

`design.py snapshot <dir> --note "<headline>" --item "<one change>"` stamps the current registers as a revision: it bumps `meta.rev`, appends `{rev, date, note, items?, changed?, files?}` to `meta.revisions` (`items` only when `--item` flags were given), and archives the full registers.json as `history/rev-<N>.json`. The note is a short reader-facing headline — `check` warns past ~90 characters — and each repeatable `--item` states one change in plain language; the authoring contract, with a worked example, is in [publish.md](publish.md). The doc's changes-since view fetches those snapshots with relative paths, so `history/` ships in `dist/` alongside the JSON. The command hashes the registers, `summary.html`, and `sysd.svg`. When none of them changed since the last snapshot it records nothing and exits 0. When only the summary or the diagram moved it records a revision with that file in `changed`. `--force` records anyway, for updates where only NOTES.md moved, which no hash sees. A doc with no snapshots — or only one — renders without the revision picker; with a baseline, a returning reader (or a `?since=<rev>` link) opens straight into the diff.

### Rendered registers

| Key | Entry shape | Notes |
|---|---|---|
| `tldr` | markdown string | one bullet each |
| `constraints` | `{t, a, star?}` | `a` is one or more space-separated `A#` ids |
| `terms` | `{k, v}` | glossary, rendered before the architecture |
| `footnotes` | `{n, b}` | `n` integer; referenced as `[^n]` from any prose field |
| `arch` | `{id, t, dq[], a[], b[]}` | `id` is `c-<slug>`; `b` is paragraphs; `dq`/`a` must resolve |
| `pipe` | `{t, s, chip, card}` | the main pipeline; `card` is an `arch` id to jump to |
| `pipeBg` | `{t, chip}` | the background pipeline row |
| `paths` | `{id, name, budget?, segs[], note}` | `segs` rows are `[step, p50ms, p95ms, description]` |
| `scaleMarks` | `{ms, label, c}` | log-scale timing strip; `c` is a CSS var color token |
| `ceilings` | `[resource, ceiling, symptom, guard]` | plus `ceilingsNote`, a trailing caveat string |
| `numbers` | `{id, t, sub?, cols[], rows[][], note?}` | generic quantitative tables (throughput, capacity, cost, freshness); `id` is `n-<slug>`; each row has one cell per `cols` entry |
| `decisions` | `{id, t, s, r, x?, round?, by?, date?}` | `s` ∈ resolved/superseded/open, rendered Decided / Replaced / Still open; `x` is rejected alternatives; `by` pairs with `s: "superseded"` both ways |
| `rounds` | dict keyed by round number (string) | condensed `{q, a, n?}` shown under the decision; `q` is the plain question with no leading ID (`check` warns); the verbatim round lives in qa-log.json |
| `assumptions` | `{id, t, s, b, n?, star?}` | `s` ∈ working/validate, rendered Assumed / Needs someone to confirm; `n` carries revision history; one starred entry |
| `open` | `{g, id, t}` | `g` must be an `openGroups` key; ids may reference other registers (`DQ15` can sit in the open list) |
| `openGroups` | `{key: label}` | iteration order is display order |

An empty or absent rendered register hides its section and nav link, so a fresh scaffold renders without placeholder noise.

### Data-only registers (never rendered)

| Key | Shape | Purpose |
|---|---|---|
| `findings` | `[n, severity, title, ref]` | index of the adversarial review; the prose stays in `<reviewer>-review-<date>.md`. `ref` is the disposition target — usually a `DQ#`, sometimes a spike `V#` or another register's shorthand |
| `timingComponents` | free | backing derivations for the path numbers |
| `housekeeping` | strings | internal changelog notes |

## qa-log.json

```
{ description, rounds: [ { round, date, topic, note?, questions: [
    { header, question, options: [{label, description}], answer, note?, multiSelect? } ] } ] }
```

- By convention the last option of every question is "Add to open list" ("I don't know yet — record it as an open question").
- `answer` is free text, and has two legal modes: it equals one option's `label` exactly (the renderer marks that option chosen), or it is a custom answer (the renderer prints it separately). With `multiSelect`, it is the chosen labels comma-joined.
- `topic`, `header`, and `question` are plain words: no leading register ID and no "finding 27". The ID rides in the fork block's `decides` field, or trails the `header` ("Worker transport (DQ5)"), and the finding number stays in the `findings` register; `check` warns on either.
- Append-only; spelling cleanup is fine, substantive edits are not; explain-only exchanges are not logged.

## What `design.py check` enforces

Errors (non-zero exit): required `meta` keys; `meta.draft` a boolean and `meta.draftNote` a non-empty string when present; ID shapes and uniqueness (`A\d+`, `DQ\d+`, `c-[a-z0-9-]+`, `n-[a-z0-9-]+`, integer footnote `n`); status vocabularies; dangling references (`arch.dq`, `arch.a`, `constraints.a`, `pipe.card`, `findings` refs shaped like `DQ#` or `V#`, `open.g`, `meta.banner.assumption`, `decisions.by`); supersession integrity in both directions (`by` ⇔ `s: "superseded"`); `[^n]` tokens without a footnote entry; malformed `paths.segs`, `ceilings` rows, `scaleMarks`, or `numbers` tables (missing title, bad `cols`, row arity not matching `cols`); when `meta.rev`/`meta.revisions` are present, their shape (positive integer `rev`, non-empty list, integer `rev` and `date` per entry, strictly increasing revs, `meta.rev` equal to the last entry's, `items` and `changed` lists — when present — holding only non-empty strings); `meta.homeLink`, when present, an object with non-empty `href` and `label`; a `summary.html` containing `<html`, `<head`, `<body`, or `<script`.

Warnings (advisory): footnotes never referenced; registers `rounds` entries no decision points at, or missing from qa-log; a decision `round` with no registers `rounds` entry; p95 below p50; qa-log answers that match no offered label (legal — confirm they were intended); revision numbers not contiguous from 1; a listed revision whose `history/rev-<N>.json` is missing or unparsable (the changes-since picker can't diff against it); a revision note past ~90 characters (keep the note a headline; detail goes in `--item` bullets); no `summary.html`, or one still carrying the scaffold's TODO (either way the doc opens without an executive summary); a register ID cited in `summary.html` (`DQ#`, `A#`, `Q#`, `V#`, `c-<slug>`) that no register defines; a `sysd.svg` with no `viewBox`; a qa-log `question`, `topic`, or `header`, or a registers `rounds[].q`, that starts with a register ID or contains "finding N".

`check --strict` promotes the publish-blocking warnings to errors — a missing or skeleton `summary.html`, a listed revision whose `history/rev-<N>.json` is missing, a `sysd.svg` without a `viewBox`, and an `index.html` beside the registers that carries no `GENERATED` stamp — which is how a CI job runs it.

## What `design.py summary-text` prints

`design.py summary-text <dir>` prints `summary.html` as plain text: headings as `#` lines, list items as `-` bullets, table rows as pipe rows, everything else as paragraphs, `<svg>` skipped. It's the input for the voice gate (`wlm adversary critique`, `slop-cop`), and the "Executive summary" block of the doc's Markdown export is the same reduction done in the browser.
