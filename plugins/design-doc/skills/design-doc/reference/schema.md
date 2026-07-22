# The register schemas

The contract between `registers.json`, `qa-log.json`, the HTML renderer, and the `design.py` driver (`check`, `pdf`). Markdown-bearing string fields support a mini dialect: `[text](url)` links, `` `code` ``, `**bold**`, `*italic*`, and `[^n]` footnote tokens. The tinyq example (`design.py scaffold <dir> --example`) is a filled instance of everything below.

## registers.json

### `meta` — document identity (the only per-project HTML config)

| Field | Required | Meaning |
|---|---|---|
| `title` | yes | h1, nav brand, browser title |
| `slug` | yes | download filenames: `<slug>-design-doc.md` |
| `date` | yes | shown in the date line |
| `subtitle` | no | defaults to "Design proposal" |
| `phase` | no | e.g. "draft"; shown in the date line |
| `banner` | no | `{assumption, text}` — the warning card for the starred assumption; `assumption` must be a real `A#`; omit the key to omit the card |
| `diagramCaption`, `timingsCaption` | no | captions under the diagram and timing strip |
| `footerNote` | no | appended to the footer and the date lines |
| `sections` | no | `{<sectionId>: {sub: "…"}}` one-line sub-copy under a section header; by default headers stand alone, so author one only when it carries design content the section body doesn't. Ids are `ground`, `architecture`, `paths`, `numbers`, `ceilings`, `decisions`, `assumptions`, `open`, `footnotes` |
| `canonical` | no | a sentence stating what lives in which file, for readers of the raw JSON |
| `rev` | no | current revision number; written by `design.py snapshot`, never by hand |
| `revisions` | no | `[{rev, date, note}]`, one entry per snapshot; written by `design.py snapshot`, never by hand |

Everything else about the HTML is fixed: the section skeleton, the status vocabularies, the artifact filenames (`registers.json`, `qa-log.json`, `NOTES.md`, `design-doc.pdf`). The system SVG is hand-edited in `design-doc.html` between the `<!--SYSD-->` markers; `design.py pdf` extracts exactly that block.

### Revision history (`history/`)

`design.py snapshot <dir> --note "<what changed>"` stamps the current registers as a revision: it bumps `meta.rev`, appends `{rev, date, note}` to `meta.revisions`, and archives the full registers.json as `history/rev-<N>.json`. The doc's changes-since view fetches those snapshots with relative paths, so `history/` ships in `dist/` alongside the JSON. When the registers haven't changed since the last snapshot the command records nothing and exits 0; `--force` records anyway (for updates where only the SVG or NOTES.md moved, which a registers diff can't see). A doc with no snapshots — or only one — renders without the revision picker.

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
| `decisions` | `{id, t, s, r, x?, round?, by?, date?}` | `s` ∈ resolved/superseded/open; `x` is rejected alternatives; `by` pairs with `s: "superseded"` both ways |
| `rounds` | dict keyed by round number (string) | condensed `{q, a, n?}` shown under the decision; the verbatim round lives in qa-log.json |
| `assumptions` | `{id, t, s, b, n?, star?}` | `s` ∈ working/validate; `n` carries revision history; one starred entry |
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
- Append-only; spelling cleanup is fine, substantive edits are not; explain-only exchanges are not logged.

## What `design.py check` enforces

Errors (non-zero exit): required `meta` keys; ID shapes and uniqueness (`A\d+`, `DQ\d+`, `c-[a-z0-9-]+`, `n-[a-z0-9-]+`, integer footnote `n`); status vocabularies; dangling references (`arch.dq`, `arch.a`, `constraints.a`, `pipe.card`, `findings` refs shaped like `DQ#` or `V#`, `open.g`, `meta.banner.assumption`, `decisions.by`); supersession integrity in both directions (`by` ⇔ `s: "superseded"`); `[^n]` tokens without a footnote entry; malformed `paths.segs`, `ceilings` rows, `scaleMarks`, or `numbers` tables (missing title, bad `cols`, row arity not matching `cols`); when `meta.rev`/`meta.revisions` are present, their shape (positive integer `rev`, non-empty list, integer `rev` and `date` per entry, strictly increasing revs, `meta.rev` equal to the last entry's).

Warnings (advisory): footnotes never referenced; registers `rounds` entries no decision points at, or missing from qa-log; a decision `round` with no registers `rounds` entry; p95 below p50; qa-log answers that match no offered label (legal — confirm they were intended); revision numbers not contiguous from 1; a listed revision whose `history/rev-<N>.json` is missing or unparsable (the changes-since picker can't diff against it).
