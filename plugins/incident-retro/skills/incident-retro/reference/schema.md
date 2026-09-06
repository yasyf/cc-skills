# The retro.json contract

The contract between `retro.json`, the snapshot files under `evidence/`, the HTML renderer `incident-retro.html`, and the `retro.py` driver (`check`, `text`, `snapshot`, `links`, `render-check`, `pdf`). Markdown-bearing string fields use the same mini dialect the design-doc renderer reads: `[text](url)` links, `` `code` ``, `**bold**`, `*italic*`, and `[^n]` footnote tokens. The Acme example (`retro.py scaffold <dir> --example`) is a filled instance of everything below.

## Timestamps

Every timestamp in the file is ISO 8601 with a UTC offset: `2026-08-14T17:42:00-07:00`. A bare `Z` is accepted as `+00:00`. `check` errors on a naive timestamp. `meta.timezone` is the zone the page and `text` display times in; it never changes what a timestamp means.

## `meta` — identity

| Field | Required | Meaning |
|---|---|---|
| `title` | yes | h1, rail brand, browser title |
| `slug` | yes | download filenames: `<slug>-incident-retro.md` |
| `date` | yes | date of the writeup, `YYYY-MM-DD` |
| `status` | yes | `draft`, `in-review`, `reviewed`, `resolved`; rendered Draft, Under review, Reviewed, Closed out. `draft` is the only status that may leave `timestamps.onset` or `resolved` null; `reviewed` and `resolved` expect at least one cause with kind `root`; `resolved` expects every action `done` or `dropped` |
| `subtitle` | no | defaults to "Incident retrospective" |
| `incident` | no | `{number?, severity?, severityLink?}`; `number` a positive integer, `severity` matches `sev-N`, `severityLink` an https URL |
| `authors`, `attendees` | no | lists of people's names; never an email or `mailto:` (`check` errors on `@`). A retro past `draft` names its authors |
| `commander` | no | the incident commander's name |
| `teams` | no | team codenames the retro concerns, as strings |
| `repo`, `ref` | no | as design-doc: `owner/repo` and a Git ref; the page renders a link into `repo` as `#1234` and others as `owner/repo#1234` |
| `timezone` | no | an IANA zone name, the display zone (default `UTC`) |
| `subIncidents` | no | `[{id, t, h}]` with ids `I\d+`, for a retro that covers several incidents; windows and causes may carry `incident: "I1"` |
| `homeLink` | no | `{href, label}`, a back link the rail renders above the brand |
| `sections` | no | `{<sectionId>: {sub}}` one-line sub-copy under a section header; ids are `overview`, `timeline`, `impact`, `causes`, `resolution`, `actions`, `lessons`, `evidence`, `notes` |
| `ai` | no | `{suggest?: {<sectionId>: ["…"]}}` the questions the assistant offers while a section is on screen; the endpoint and keys live in `ai.json`, never here |
| `acronyms` | no | words the capitalisation lint holds to their own spelling, on top of the built-in list plus `TTD`, `TTE`, `TTM`, `TTR`, `SEV` |
| `draft` | no | boolean; pins a draft banner as in design-doc |
| `canonical` | no | a sentence stating what lives in which file |
| `rev`, `revisions` | no | written by `retro.py snapshot`, never by hand; `revisions[].files.evidence` is the digest of every file under `evidence/` at that snapshot |

The `localStorage` keys the page reads (`design-doc-ai`, `design-doc-github`, `design-doc-theme`, `design-doc-wording`) keep their design-doc names on purpose, so one browser override works on both kinds of page.

## `summary`, `impact`, `resolution`, `detection` — twinned prose

Each is `{text, p, …}`. `text` is the wording the team stands behind; `p` is the plain twin a reader outside the team sees first: 30 words or fewer, or a third of the original, naming no ids and no file paths. `check --strict` errors on a missing twin, on a twin that breaks those rules, and on a twin left unchanged after its text changed since the last snapshot. Every cause carries the same pair.

- `impact.teams`: `[{codename, text}]`, one row per team and what it saw.
- `impact.metrics`: `[{label, value, unit?, delta?, measured, cites?}]`. `value` is a string as the reader should see it (`"3 180"`); `measured` is a boolean, so every number is measured or tagged estimated; `cites` names windows or causes.
- `resolution.links`: links as below, `closes` refused.
- `detection.monitors`: `[{id, file?, role, fired?, recovered?}]` with `role` in `caught`, `missed`, `added`. `fired` and `recovered` are timestamps; the page derives "fired N after onset" from `fired` and `timestamps.onset`.
- `impact`, `resolution` and `detection` may carry `component`, the id of a declared component the page renders under that section's text.

## `timestamps` — the numbers the tiles derive

```json
"timestamps": {"onset": "…", "detected": "…", "engaged": "…", "mitigated": "…", "resolved": "…", "allClear": "…"}
```

Every key is present; a value is a timestamp or `null`. `check` enforces `onset ≤ detected ≤ engaged ≤ mitigated ≤ resolved ≤ allClear` over the values present, and that `onset` and `resolved` are set unless `status` is `draft`.

Derived, never typed: TTD is `detected − onset`, TTE is `engaged − detected`, TTM is `mitigated − onset`, TTR is `resolved − onset`; a window's duration is `end − start`; a monitor's latency is `fired − onset`; the action bar is done over total. One helper in `retro.py` (`derived_numbers`) computes the four tiles for `text` and for `check`, and the page computes the same from the same keys. `check` warns on prose that states one of these durations in words (a number of minutes or hours "after", "to" or "before" something near detect, mitigate, resolve, engage, onset or fired), so the tiles stay the only source.

## `windows` — outage windows

`[{id, h, kind, start, end, text?, teams?, incident?}]`. Ids `W\d+`; `h` is the handle; `kind` is `outage`, `degraded` or `partial`; `end` is after `start`. Two windows that overlap on the same team draw a warning.

## `timeline` — what happened, in order

`[{id?, ts, kind, text, actor?, window?, phase?, refs?}]`, sorted by `ts` (`check` errors on an entry out of order).

- `kind`: `deploy`, `alert`, `report`, `hypothesis`, `action`, `mitigation`, `resolution`, `allclear`.
- `id` is optional and `T\d+`: an entry gets one only when something cites it (a cause's `evidence`, a citation in prose). `check` warns on an id nothing cites and errors on a cited `T#` no entry carries. A `(T7)` citation renders as the entry's local time.
- `phase`: `before` or `after`, required on an entry whose `ts` falls outside `[onset, allClear or resolved]`; an entry inside the incident carries none.
- `window`: a window id the entry belongs to.
- `refs`: links as below (strings or `{url, kind?, label?}`), `closes` refused. A Slack ref must be a message permalink, `https://<workspace>.slack.com/archives/C…/p…`; one with no snapshot under `evidence/slack/` draws a warning, because the page cannot quote it. A `deploy` entry with no pull request or build ref draws a warning.

## `causes`

`[{id, kind, t, h, text, p, evidence?, code?, links?, incident?, component?}]`.

- Ids `C\d+`; `kind` is `root`, `contributing` or `trigger`; `t` is a noun phrase of twelve words or fewer; `h` is the handle; `text` and `p` are the twinned pair.
- `evidence`: a list of https URLs and citeable ids (a `T#`, `W#`, another `C#`); every id must resolve.
- `code`: `{lang, source, caption?}`, a snippet the page shows behind a toggle.
- `links`: links as below, `closes` refused (a change closes an action, never a cause).
- `component`: the id of a declared component the page renders inside the cause's card, typically an `ir.notebook` with a `cells` subset.

## `actions`

`[{id, t, h, owner, source, state, links?, due?, note?}]`.

- Ids `AI\d+`; `t` is one line of sixteen words or fewer; `h` is the handle.
- `owner`: a person's or team's name. `check --strict` errors on a missing owner.
- `source`: the cause id the action answers, or `lessons` or `review`.
- `state`: `todo`, `in-progress`, `done`, `dropped`; rendered To do, In progress, Done, Dropped.
- `links`: links as below; `closes: true` marks the pull request or issue whose landing completes the action. `retro.py links --fetch` reports an action `done` whose closing change is still open, and one `todo` or `in-progress` whose closing change merged.
- `due`: `YYYY-MM-DD`. `note`: one sentence, for a dropped action the reason.

## `lessons`

`{well: [{text}], wrong: [{text}], lucky: [{text}]}`. Each lesson is one line of forty words or fewer; an empty column is hidden.

## `evidence` — the register of what the page renders and links

Every key is present as a list, empty when unused. A `file` is a path under `evidence/`, relative to the retro directory; `check` errors on a missing file and on a snapshot whose `id` or `permalink` disagrees with its register entry.

| Key | Entry | Snapshot |
|---|---|---|
| `notebooks` | `{id, url, file}` | `evidence/datadog/notebook-<id>.json`, schema `ir.notebook/1` |
| `monitors` | `{id, url, file}` | `evidence/datadog/monitor-<id>.json`, schema `ir.monitor/1` |
| `slack` | `{url, file}` | `evidence/slack/<channel_name>-<ts>.json`, schema `ir.slack/1` |
| `sentry` | `{url, label}` | none |
| `linear` | `{url, key, label?}` with `key` like `ENG-123` | none |
| `builds` | `{url, label}` | none |
| `prs` | `{url, role}` with `role` in `cause`, `fix`, `monitor`, `followup` | none |
| `runs` | `{id, label, url?}` | none |
| `images` | `{file, alt, caption?, cites?}` | the image itself under `evidence/images/` |
| `docs` | `{url, label}` | none |

The snapshot formats, field by field, and what `retro.py evidence fetch` and `evidence slack new` write are in `reference/evidence.md`. `check` reads each snapshot's `schema` and `id`, warns on a notebook whose `time` does not span onset to resolved or that was fetched live, warns on a monitor in `detection.monitors[]` with no file, and warns on any Slack permalink cited anywhere in the retro that has no snapshot.

## `notes` and `footnotes`

`notes`: `[{t, md, component?}]`, prose blocks the Notes section renders in order; `component` places a declared component under the block. `footnotes`: `[{n, b}]`, referenced from prose as `[^n]`; `check` errors on a token with no footnote and warns on a footnote nothing references.

## `components` — declared interactive blocks

`components` maps an id (lower-case words joined by hyphens) to one block, validated against `reference/components/<kind>.json` by the same schema walker design-doc uses. The retro kit is `ir.tiles`, `ir.windows`, `ir.timeline`, `ir.notebook`, `ir.monitor`, `ir.slack-thread`, `ir.actions`, `ir.causes`, described in `reference/components.md`; the reusable design-doc kinds `dd.tabs`, `dd.before-after`, `dd.whatif`, `dd.steps`, `dd.timeline`, `dd.matrix`, `dd.flow`, `dd.lanes` also work. The tiles, windows gantt, swimlane, notebooks, monitors, Slack threads, actions table and causes tree all render without a declaration; declare one to place a component elsewhere (a cause's `component`, a note's, `impact`, `resolution`, `detection`) or to override its options. `check` errors on a declared component nothing places, on an `ir.notebook` whose `notebook` is not a registered notebook or whose `cells` name an index its snapshot lacks, on an `ir.monitor` whose monitor is registered nowhere, on an `ir.slack-thread` whose permalink has no snapshot, on `ir.tiles` `from`/`to` that are not `timestamps` keys, on `ir.windows` naming an unknown window, on a `dd.timeline` gate that is no register id, and on a `dd.matrix` whose cells do not match its rows and columns.

## Ids, handles and citations

Citeable ids are `W\d+`, `T\d+`, `C\d+`, `AI\d+`, `I\d+`, unique across the file. In prose, `(C1)` or `(C1, T7)` renders as the handles in parentheses, the way design-doc renders `(DQ12)`; `retro.py text` does the same. A handle `h` is required on every window, cause, action and sub-incident: two to five words a reader would say out loud, no trailing period, naming no ids. `check` errors on a citation no register defines, and on a title or handle that names an id.

## Links

A link is a URL string or `{url, kind?, label?, closes?}`. `url` is https; `kind` is `pr`, `issue`, `commit` or `doc`, inferred from a `github.com` URL and required to agree with it; a Linear or other issue tracker URL is a `doc` with a `label`. `closes` belongs only on an action's link.

## Snapshots and history

`retro.py snapshot --note "…" [--item "…"]…` writes `meta.rev`, appends to `meta.revisions`, and archives the whole file as `history/rev-<N>.json`. The revision entry records `files.evidence`, a digest over every file under `evidence/`, so a re-fetched notebook is a revision; `changed: ["evidence"]` marks one where only the evidence moved. `check` verifies the chain (`rev` equals the last revision's, revisions increase from 1, every `history/rev-N.json` exists and parses) and warns when the evidence digest has moved since the current revision.

## Libraries

The page loads its libraries from jsdelivr at exact versions, pinned in one `LIBS` block at the top of the template's script and repeated here so `check` can compare the two:

| Library | Version | Files |
|---|---|---|
| `lucide-static` | `lucide-static@1.39.0` | `icons/<name>.svg`, as design-doc |
| `uplot` | `uplot@1.6.32` | `https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.iife.min.js` and `https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.min.css` |

Mermaid is not used: the retro's own figures (tiles, windows, swimlane) are hand-drawn SVG in the template, and uPlot draws the notebook series on canvas. `check` warns when the template pins a version this table does not state.

## Artifacts

| File | Role |
|---|---|
| `retro.json` | canonical structured retro |
| `NOTES.md` | prose that does not fit structure |
| `evidence/datadog/`, `evidence/slack/`, `evidence/images/` | snapshot files the page renders |
| `history/rev-<N>.json` | archived revisions, written by `snapshot` |
| `incident-retro.html` | the renderer, copied in by `scaffold`; must be served over HTTP |
| `incident-retro.pdf` | printed by `retro.py pdf`; generated, never checked in |
| `<slug>-incident-retro.md` | the Markdown export the page downloads; `retro.py text` prints the same document |

## What `retro.py check` enforces

Errors unless noted; `--strict` promotes the strict warnings.

1. `meta`: title, slug, date present; status, severity, authors (no `@`), repo, ref, timezone, homeLink, subIncidents, sections, ai.
2. Timestamps parse with an offset and stay in order; onset and resolved set unless draft (strict).
3. Windows: ids, kind, start before end, incident resolves; overlaps on one team warn.
4. Timeline: order, kinds, unique ids, phase outside the incident, refs are https, Slack refs are permalinks with snapshots (warn), deploys carry a change ref (warn).
5. Impact metrics: `measured` boolean, cites resolve.
6. Causes: ids, kinds, evidence resolves, code shape, links without `closes`; a root cause once reviewed (strict).
7. Actions: ids, states, owner (strict), source resolves, `closes` only on pr/issue, due dates; open actions on a resolved retro (strict).
8. Evidence: files exist, snapshot schemas and ids match, images carry alt, prs carry a role, notebook windows and liveness (warn), monitors without files (warn).
9. Citations and footnotes resolve.
10. Handles present and well-formed; twins present and within the rules; a twin left stale across a snapshot.
11. Prose stating a derived duration (warn).
12. Capitalisation over titles, handles and labels.
13. Component schemas and their hosts.
14. Forbidden terms across `retro.json`, `NOTES.md` and every text file under `evidence/`, from `--forbidden-terms`, `FORBIDDEN_TERMS`, or the nearest `.customer-names` (warns when none is configured).
15. Library pins against this file (warn).
16. Template freshness against `plugins/_shared` when that source tree is present.
17. `ai.json` beside the retro or one level up.
18. Revision history integrity.
