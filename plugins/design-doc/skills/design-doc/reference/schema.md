# The register schemas

The contract between `registers.json`, `qa-log.json`, the hand-written `summary.html` beside them, the HTML renderer, and the `design.py` driver (`check`, `pdf`, `snapshot`, `summary-text`, `plainify`, `render-check`). Markdown-bearing string fields support a mini dialect: `[text](url)` links, `` `code` ``, `**bold**`, `*italic*`, and `[^n]` footnote tokens. The tinyq example (`design.py scaffold <dir> --example`) is a filled instance of everything below.

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
| `timingsCaption` | no | caption under the timing strip; the diagram's caption is `diagram.caption` |
| `footerNote` | no | appended to the footer and the date lines |
| `homeLink` | no | `{href, label}` — a back link the rail renders above the brand, for a doc that lives in a collection (`{"href": "../", "label": "← All docs"}`); `check` errors on any other shape |
| `sections` | no | `{<sectionId>: {sub: "…"}}` one-line sub-copy under a section header; by default headers stand alone, so author one only when it carries design content the section body doesn't. Ids are `ground`, `architecture`, `paths`, `numbers`, `ceilings`, `decisions`, `assumptions`, `open`, `footnotes` |
| `canonical` | no | a sentence stating what lives in which file, for readers of the raw JSON |
| `rev` | no | current revision number; written by `design.py snapshot`, never by hand |
| `revisions` | no | `[{rev, date, note, items?, changed?, files?}]`, one entry per snapshot; written by `design.py snapshot`, never by hand. `files` holds the digest of `summary.html` at that snapshot, and of `sysd.svg` when `diagram.kind` is `svg`; `changed` names the ones that differ from the previous entry's (`summary`, `sysd`); the changes-since view flags the Summary section from it |

Everything else about the HTML is fixed: the section skeleton, the status vocabularies, the artifact filenames (`registers.json`, `qa-log.json`, `NOTES.md`, `summary.html`, `design-doc.pdf`, and `sysd.svg` for a hand-drawn diagram). The renderer leads every entry with its title and shows the ID as a small secondary chip; a cross-reference (`by`, `arch.dq`, a "(A2)" citation in prose) renders as the target's title with the ID trailing and a hover tooltip. The JSON values are what `check` verifies; the titles are what the reader sees.

### `diagram` — the system diagram

The system diagram is a register key, not a file:

```json
"diagram": {
  "kind": "mermaid",
  "source": "flowchart LR\n  api[API] --> log[(Job log)]\n  log --> worker[Worker]",
  "caption": "One log per tenant; workers lease from it."
}
```

`source` is one Mermaid block with a `graph` or `flowchart` header. The node ids in it are the doc's join points. `arch[].node` names the node a card belongs to, so a click on the node opens the card. The optional fifth element of a `paths[].segs[]` row names the node or edge the step traverses, so "Play the request" can walk the diagram. The renderer initialises Mermaid on the `base` theme with its colours read from the doc's CSS variables and re-inks it when the colour scheme changes. The interactions are the renderer's own: hover highlights a node's edges and neighbours, click opens the card in a side sheet, and the system diagram pans and zooms with a reset control. Mermaid runs with `securityLevel: "strict"` and `htmlLabels: false`, so the source carries no `click` directives and no HTML labels. Summary figures use the same engine as `<pre class="mermaid">` blocks inside a panel's `<figure>`.

A hand-drawn diagram stays valid as `{ "kind": "svg", "file": "sysd.svg", "caption": "…" }`: one `<svg>` with a `viewBox` and no fixed `width`/`height`, drawn with the `.sysd` classes the doc styles in both colour schemes (`grp`, `bx`, `bxo`, `dur`, `ln`, `tt`, `ac`, `sm`, `tag`). `check` warns "hand-drawn diagram" on it and `--strict` accepts it; nothing steps through it.

The libraries load from jsdelivr at exact versions, pinned in one `LIBS` block at the top of the template's script and repeated here so `check` can compare the two: `mermaid@11.17.2`, `@mermaid-js/layout-elk@0.2.3`, `svg-pan-zoom@3.6.2`, `lucide-static@1.39.0`. Nothing is vendored. A reader with no network sees "Diagrams need a network connection" in the diagram host and a doc that otherwise works.

### `summary.html`

`summary.html` is the executive summary, for people only: it's never parsed as data, and no register field points at it. The doc fetches it at runtime and `design.py pdf` prints it from the served page, so the HTML template is never edited; scaffold leaves a placeholder to replace. It's a body-level fragment, injected into the hero before the document: no `<html>`, `<head>`, `<body>`, or `<script>`, each an error in `check`. It inherits the doc's styles and themes through the CSS variables `--bg`, `--bg2`, `--ink`, `--ink2`, `--line`, `--accent`, `--accent-soft`, `--ok`, `--warn`, `--card`, `--mono`, `--sans`, plus the two lane tones `--before` and `--after`. Inline `<svg>` uses the `.sysd` classes above, and there are no new colours. A missing file hides the hero and its rail link, and `check` warns, since the doc then opens without an executive summary. A register ID in the text (`DQ4`, `A2`, `Q3`, `V1`, `c-log`) becomes a hoverable link to its entry when the entry exists and a `check` warning when it doesn't, so citations stay honest; the content contract is in [writing.md](writing.md).

The fragment is a deck of `<section class="xs-panel" data-kind="…">`, three to five panels with `thesis` first, or exactly one `poster`. Each panel fills the viewport, and the rail and the document fade in only once the reader scrolls past the last one. Every panel carries one `<figure>`, inline SVG, a `<pre class="mermaid">` block, or an `<img>`, which the panel lays out as its dominant element at 1100px and above and stacks above the text below that. The kit:

| Class | Renders as |
|---|---|
| `.xs-panel[data-kind=thesis]` | the display panel: an `h1`, one-sentence lede, and the hero figure |
| `.xs-panel[data-kind=compare]` | an `.xs-compare` grid: an `.xs-heads` row of column titles, then one `.xs-row` per topic holding an `.xs-topic` and two clauses in `.xs-before` and `.xs-after`, or a requirement and its answer |
| `.xs-panel[data-kind=numbers]` | an `.xs-stats` band of two to four `.xs-stat`, each an `.xs-value`, an optional `.xs-delta`, and an `.xs-label`; `data-measured="no"` adds an "estimated" tag |
| `.xs-panel[data-kind=cost]` | two short `.xs-cols` columns under the `h3` headings "What this costs" and "Where to push back", two to four items each |
| `.xs-panel[data-kind=poster]` | the one-screen alternative: a single composed figure built from the poster primitives below |
| `.xs-lane[data-tone=before\|after]` | a poster column with a coloured header and eyebrow |
| `.xs-node` | an icon, a label, and a mono sublabel; `data-dashed` marks what is not built yet |
| `.xs-group` | a dashed enclosure with a caption (`PACKAGE`, `DATA`) |
| `.xs-connect` | a `<ul class="xs-connect">` of `<li data-from="…" data-to="…">` naming two node ids; the kit draws them as an SVG overlay at render, font load, and resize, and `data-dashed`, `data-arrow`, `data-label`, `data-route` (`tree`, `up`, `h`, `side`, `gutter`, `curve`), and `data-tone` (`before`, `after`, `ink`) shape each line |
| `.xs-legend` > `.xs-cell` | the legend band, two to four cells |
| `.xs-icon[data-icon=server]` | a lucide icon inlined at runtime from the pinned CDN; `check` warns on a name outside the pinned set |

Ordinary `h2`, `p`, `ul`, and `table` inherit the panel's type scale. A four-panel deck:

```html
<section class="xs-panel" data-kind="thesis">
  <h1>Workers stop polling</h1>
  <p>A log and a lease replace the jobs table, so dispatch stops costing one scan per worker per tick.</p>
  <figure aria-label="Today's polled table beside the proposed log">
    <pre class="mermaid">flowchart LR
  w1[Worker] -- scan --> t[(Jobs table)]
  w2[Worker] -- long-poll --> l[(Job log)]</pre>
  </figure>
</section>
<section class="xs-panel" data-kind="compare">
  <h2>What changes</h2>
  <div class="xs-compare">
    <div class="xs-heads"><span></span><span>Today</span><span>Proposed</span></div>
    <div class="xs-row"><div class="xs-topic">Dispatch</div><div class="xs-before">Every worker scans the table every 500 ms.</div><div class="xs-after">A worker holds a long-poll and receives a lease (DQ4).</div></div>
  </div>
  <figure aria-label="Scan cost against fleet size, today and proposed"><svg viewBox="0 0 320 120">…</svg></figure>
</section>
<section class="xs-panel" data-kind="numbers">
  <h2>The numbers</h2>
  <div class="xs-stats">
    <div class="xs-stat" data-measured="no"><span class="xs-value">12 ms</span><span class="xs-delta">from 480 ms</span><span class="xs-label">Dispatch p95, pending V1</span></div>
    <div class="xs-stat" data-measured="yes"><span class="xs-value">5</span><span class="xs-delta">unbounded today</span><span class="xs-label">Retries per job</span></div>
  </div>
  <figure aria-label="Dispatch latency by fleet size"><svg viewBox="0 0 320 120">…</svg></figure>
</section>
<section class="xs-panel" data-kind="cost">
  <h2>What this costs, and where to push back</h2>
  <div class="xs-cols">
    <div><h3>What this costs</h3><ul><li>One more process per tenant, the log writer.</li></ul></div>
    <div><h3>Where to push back</h3><ul><li>The one-file-per-tenant bound (A1) is what makes the log cheap.</li></ul></div>
  </div>
  <figure aria-label="Where the new process sits"><svg viewBox="0 0 320 120">…</svg></figure>
</section>
```

`reference/gallery/deck.html` and `reference/gallery/poster.html` are complete fragments the template renders with no doc-specific data, each with a screenshot beside it.

### Revision history (`history/`)

`design.py snapshot <dir> --note "<headline>" --item "<one change>"` stamps the current registers as a revision: it bumps `meta.rev`, appends `{rev, date, note, items?, changed?, files?}` to `meta.revisions` (`items` only when `--item` flags were given), and archives the full registers.json as `history/rev-<N>.json`. The note is a short reader-facing headline — `check` warns past ~90 characters — and each repeatable `--item` states one change in plain language; the authoring contract, with a worked example, is in [publish.md](publish.md). The doc's changes-since view fetches those snapshots with relative paths, so `history/` ships in `dist/` alongside the JSON. The command hashes the registers, the diagram source with them, and `summary.html`, plus `sysd.svg` for a hand-drawn diagram. When none of them changed since the last snapshot it records nothing and exits 0. When only the summary or a hand-drawn diagram moved it records a revision with that file in `changed`. `--force` records anyway, for updates where only NOTES.md moved, which no hash sees. A doc with no snapshots — or only one — renders without the revision picker; with a baseline, a returning reader (or a `?since=<rev>` link) opens straight into the diff.

### Rendered registers

| Key | Entry shape | Notes |
|---|---|---|
| `tldr` | `{md, p}` | three to five items; `md` is the precise bullet, `p` its plain twin of at most 20 words, which is what the doc shows |
| `constraints` | `{t, a, p, star?}` | `a` is one or more space-separated `A#` ids |
| `terms` | `{k, v}` | glossary, rendered before the architecture |
| `footnotes` | `{n, b}` | `n` integer; referenced as `[^n]` from any prose field |
| `arch` | `{id, t, dq[], a[], b[], node?}` | `id` is `c-<slug>`; `b` is paragraphs; `dq`/`a` must resolve; `node` is the Mermaid node id in `diagram.source` the card belongs to |
| `diagram` | `{kind, source?, file?, caption}` | the system diagram, above |
| `paths` | `{id, name, budget?, segs[], note}` | `segs` rows are `[step, p50ms, p95ms, description, node?]`; the fifth element names the `diagram` node or edge the step traverses, which "Play the request" highlights |
| `scaleMarks` | `{ms, label, c}` | log-scale timing strip; `c` is a CSS var color token |
| `ceilings` | `[resource, ceiling, symptom, guard]` | plus `ceilingsNote`, a trailing caveat string |
| `numbers` | `{id, t, sub?, cols[], rows[][], note?}` | generic quantitative tables (throughput, capacity, cost, freshness); `id` is `n-<slug>`; each row has one cell per `cols` entry |
| `decisions` | `{id, t, s, r, p, key, theme, x?, round?, by?, date?}` | `s` ∈ resolved/superseded/open, rendered Decided / Replaced / Still open; `x` is rejected alternatives; `by` pairs with `s: "superseded"` both ways; `key: true` opens the section as a spotlight card; `theme` is a `themes` key |
| `rounds` | dict keyed by round number (string) | condensed `{q, a, n?}` shown under the decision; `q` is the plain question with no leading ID (`check` warns); the verbatim round lives in qa-log.json |
| `assumptions` | `{id, t, s, b, p, key, theme, n?, star?}` | `s` ∈ working/validate, rendered Assumed / Needs someone to confirm; `n` carries revision history; one starred entry; `key` and `theme` as on decisions |
| `themes` | `{key: label}` | the design's three to five themes; every decision and assumption files under one, and the section groups by them in this order |
| `open` | `{g, id, t, p, blocks?}` | `g` must be an `openGroups` key; ids may reference other registers (`DQ15` can sit in the open list); `blocks` lists the decisions this item holds up (`["DQ4"]`), rendered as a chip |
| `openGroups` | `{key: label}` | iteration order is display order; doubles as the open list's theme |

`p` is the plain twin: one or two short sentences, at most 30 words or a third of the original, no register IDs, no paths, no finding numbers; the contract is in [writing.md](writing.md). Between three and eight entries per register carry `key: true`.

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

Errors (non-zero exit): required `meta` keys; `meta.draft` a boolean and `meta.draftNote` a non-empty string when present; ID shapes and uniqueness (`A\d+`, `DQ\d+`, `c-[a-z0-9-]+`, `n-[a-z0-9-]+`, integer footnote `n`); status vocabularies; dangling references (`arch.dq`, `arch.a`, `constraints.a`, `findings` refs shaped like `DQ#` or `V#`, `open.g`, `meta.banner.assumption`, `decisions.by`); supersession integrity in both directions (`by` ⇔ `s: "superseded"`); `[^n]` tokens without a footnote entry; malformed `paths.segs`, `ceilings` rows, `scaleMarks`, or `numbers` tables (missing title, bad `cols`, row arity not matching `cols`); when `meta.rev`/`meta.revisions` are present, their shape (positive integer `rev`, non-empty list, integer `rev` and `date` per entry, strictly increasing revs, `meta.rev` equal to the last entry's, `items` and `changed` lists — when present — holding only non-empty strings); `meta.homeLink`, when present, an object with non-empty `href` and `label`; a `summary.html` containing `<html`, `<head`, `<body`, or `<script`, an `<iframe>`, `<object>`, or `<embed>`, an inline `on*=` handler, or an `href`/`src` whose scheme is anything but `http(s)` (checked after entity decoding, so `java&#x0A;script:` is caught); a `diagram` whose `kind` is neither `mermaid` nor `svg`, a `mermaid` one with an empty `source` or no `graph`/`flowchart` header, an `arch[].node` or a fifth `segs[]` element that names no id in the source; a `theme` that is not a `themes` key; a `tldr` entry that is not `{md, p}`; an `open[].blocks` entry that resolves to no decision.

Warnings (advisory): footnotes never referenced; registers `rounds` entries no decision points at, or missing from qa-log; a decision `round` with no registers `rounds` entry; p95 below p50; qa-log answers that match no offered label (legal — confirm they were intended); revision numbers not contiguous from 1; a listed revision whose `history/rev-<N>.json` is missing or unparsable (the changes-since picker can't diff against it); a revision note past ~90 characters (keep the note a headline; detail goes in `--item` bullets); no `summary.html`, or one still carrying the scaffold's TODO (either way the doc opens without an executive summary); a register ID cited in `summary.html` (`DQ#`, `A#`, `Q#`, `V#`, `c-<slug>`) that no register defines; a `sysd.svg` with no `viewBox`, and a `diagram` of kind `svg` at all ("hand-drawn diagram"); a `LIBS` pin in the template that differs from the versions stated above; a qa-log `question`, `topic`, or `header`, or a registers `rounds[].q`, that starts with a register ID or contains "finding N". When the entry at `meta.rev` carries `files`, `check` rehashes `summary.html` (and `sysd.svg`, when used) against those digests and, on a mismatch, warns that the file changed since that revision; it compares no earlier entry.

The deck rules, warnings that `--strict` promotes: a `summary.html` with fewer than three or more than five `.xs-panel`, an unknown `data-kind`, a first panel that is not `thesis`, a second `thesis`, or a `poster` beside any other panel; a panel over its budget (`thesis` 40 words, `compare` 120, `numbers` 60, `cost` 90, `poster` 180, the deck 350, counted outside `<svg>`, `<pre class="mermaid">`, and `<code>`); a panel with no `<figure>`; a sentence that starts with a register ID, or the tokens `finding N` and `pass-N`; a `<figure>` with neither `aria-label` nor `<figcaption>`; an `.xs-stat` without `data-measured="yes"` or `"no"`.

The twin rules, likewise: a rendered entry with no `p`; a `p` over 30 words and over a third of its original; a `p` carrying a register ID (the `DQ#`, `A#`, `Q#`, `V#`, `c-` shapes plus every id the project's registers define, except a path id that reads as a plain word), a path, or a finding number; a `tldr[].p` over 20 words; fewer than three or more than eight `key: true` entries in `decisions` or `assumptions`; an `.xs-icon` name outside the pinned lucide set (fetched once and cached under `~/.cache/design-doc/`); and, under `--strict` only, an entry whose wording changed since `history/rev-<N>.json` at `meta.rev` while its `p` did not.

`check --strict` promotes the publish-blocking warnings to errors, which is how a CI job runs it. Those are a missing or skeleton `summary.html`, the deck rules, the twin rules, a listed revision whose `history/rev-<N>.json` is missing, a `summary.html` or `sysd.svg` that no longer matches the digest recorded at `meta.rev`, a `sysd.svg` without a `viewBox`, and an `index.html` beside the registers that carries no `GENERATED` stamp. A hand-drawn diagram stays a warning under `--strict`.

## What `design.py summary-text` prints

`design.py summary-text <dir>` prints `summary.html` as Markdown, one `## <kind>` section per panel in deck order, so the gates see the hierarchy: the panel's own headings as `###` lines and deeper under it, list items as `-` bullets, table rows as pipe rows, everything else as paragraphs, `<svg>` and `<pre class="mermaid">` skipped. It's the input for the gates (`wlm adversary critique`, `slop-cop --lang=markdown`), and the "Executive summary" block of the doc's Markdown export is the same reduction done in the browser.

## What `design.py plainify` writes

`design.py plainify <dir> [--only DQ3,A2] [--dry-run]` drafts a `p` for every rendered entry that has none and writes it in place. It sends the whole batch to `slop-cop plainify`, under a 30-word budget, with the register ids banned and a glossary naming every one of them, so a twin that would have cited `DQ4` names that decision's title instead. `SLOP_COP` points at another binary; without one on `PATH` the command says to `brew install yasyf/tap/slop-cop`. `--provider claude` and `--provider codex` are the fallbacks, one call per entry through that CLI with the prompt in [plain.md](plain.md); `--provider none` writes nothing and lists what is missing. `--only` limits the run to the named ids; `--dry-run` prints the review table and writes nothing.

The table is the review surface: id, the original's first line, the twin, and its issues. The issues column carries what `check` says about the twin next to what slop-cop graded against its own contract. A twin it could not fit in the budget, or could not keep an id out of, arrives flagged, and a trailing line counts the flagged twins. Read every twin against its original and edit it before the snapshot, since neither grader has a view of what the entry means beyond its words.

## What `design.py render-check` renders

`design.py render-check <dir>` serves the project directory, opens the doc in headless Chrome (the finder `design.py pdf` uses), renders every Mermaid block, and fails on the first parse error with the block's source and Mermaid's message. `check` sees only structure, so this is the only command that proves a diagram draws; `publish.sh` and CI run it. It needs network access to jsdelivr and says so when the import fails.

## What `design.py pdf` prints

The template carries an `@media print` stylesheet: light palette; rail, veil, toggles, and search hidden; the deck flattened to headed sections in panel order, a poster on its own landscape page; every `<details>` open; every request path laid out under its name; the register filters, search, and changes-since view cleared for the print and restored after it; Mermaid rendered before print; page breaks kept out of cards and tables; the running title and revision in the footer. The rail's PDF button calls `window.print()` against it. `design.py pdf <dir>` serves the directory, opens the doc in headless Chrome over its debugging pipe, waits for the page to report every diagram and connector rendered, fails on a diagram that did not, and prints `<dir>/design-doc.pdf` through the same stylesheet, so the file matches what the button gives a reader. It needs the same network access as `render-check`.
