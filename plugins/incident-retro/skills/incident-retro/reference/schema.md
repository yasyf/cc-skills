# The retro.json contract

`retro.json` is the contract shared by the evidence snapshots and
`incident-retro.html`. The `retro.py` driver reads the same contract through
`check`, `text`, `snapshot`, `links`, `render-check`, and `pdf`.
Markdown-bearing string fields accept `[text](url)` links and `` `code` ``.
They also accept `**bold**` and `*italic*`. The same mini dialect as the
design-doc renderer handles `[^n]` footnote tokens. Run
`retro.py scaffold <dir> --example` for the filled Acme instance.

## Timestamps

Every timestamp in the file is ISO 8601 with a UTC offset: `2026-08-14T17:42:00-07:00`. A bare `Z` is accepted as `+00:00`. `check` errors on a naive timestamp. `meta.timezone` is the zone the page and `text` display times in; it never changes what a timestamp means.

The Internet Assigned Numbers Authority maintains the timezone database used
here.

## `meta`: identity

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

The page reads four `localStorage` keys: `design-doc-ai`, `design-doc-github`, `design-doc-theme`, and `design-doc-wording`. They keep their design-doc names so one browser override works on both kinds of page.

## `summary`, `impact`, `resolution`, `detection`: twinned prose

Each block contains `text` and `p` plus section-specific fields. `text` is the wording the team stands behind. `p` is the plain twin a reader outside the team sees first: 30 words or fewer, or a third of the original, excluding ids and file paths. `check --strict` errors on a missing twin or one that breaks those rules. It also errors when `text` changes after a snapshot but `p` does not. Every cause carries the same pair.

- `impact.teams`: `[{codename, text}]`, one row per team and what it saw.
- `impact.metrics`: `[{label, value, unit?, delta?, measured, cites?}]`. `value` is a display string (`"3 180"`); `measured` is a boolean, so every number is measured or tagged estimated; `cites` names windows or causes.
- `resolution.links`: links as below, `closes` refused.
- `detection.monitors`: `[{id, file?, role, fired?, recovered?}]` with `role` in `caught`, `missed`, `added`. `fired` and `recovered` are timestamps; the page derives "fired N after onset" from `fired` and `timestamps.onset`.
- `impact`, `resolution` and `detection` may carry `component`, the id of a declared component the page renders under that section's text.

## `timestamps`: the numbers the tiles derive

```json
"timestamps": {"onset": "…", "detected": "…", "engaged": "…", "mitigated": "…", "resolved": "…", "allClear": "…"}
```

Every key is present; a value is a timestamp or `null`. `check` enforces `onset ≤ detected ≤ engaged ≤ mitigated ≤ resolved ≤ allClear` over the values present, and that `onset` and `resolved` are set unless `status` is `draft`.

The file never stores derived values. The time-to-detect tile uses
`TTD = detected − onset`, while the time-to-engage tile uses
`TTE = engaged − detected`. The time-to-mitigate tile uses
`TTM = mitigated − onset`, while the time-to-resolve tile uses
`TTR = resolved − onset`.

A window's duration is `end − start`, and a monitor's latency is
`fired − onset`. Action progress is done over total. The `derived_numbers`
helper in `retro.py` computes the four tiles for `text` and `check`. The page
computes the same values from the same keys. `check` warns when prose states
minutes or hours `after`, `to`, or `before` near detect, mitigate, resolve,
engage, onset, or fired. The `TTD`, `TTE`, `TTM`, and `TTR` tiles stay the only
source.

## `windows`: outage windows

`[{id, h, kind, start, end, text?, teams?, incident?}]`. Ids `W\d+`; `h` is the handle; `kind` is `outage`, `degraded` or `partial`; `end` is after `start`. Two windows that overlap on the same team draw a warning.

## `timeline`: what happened, in order

The shape is `[{id?, ts, kind, text, actor?, window?, phase?, refs?}]`, sorted by `ts`. `check` errors on an entry out of order.

- `kind`: `deploy`, `alert`, `report`, `hypothesis`, `action`, `mitigation`, `resolution`, `allclear`.
- `id` is optional and `T\d+`: an entry gets one only when a cause's `evidence` or prose cites it. `check` warns on an id nothing cites and errors on a cited `T#` no entry carries. A `(T7)` citation renders as the entry's local time.
- `phase`: `before` or `after`, required on an entry whose `ts` falls outside `[onset, allClear or resolved]`; an entry inside the incident carries none.
- `window`: a window id the entry belongs to.
- `refs`: links as below, either strings or `{url, kind?, label?}` objects, with `closes` refused. A Slack ref must be a message permalink, `https://<workspace>.slack.com/archives/C…/p…`; one with no snapshot under `evidence/slack/` draws a warning because the page cannot quote it. A `deploy` entry with no pull request or build ref draws a warning.

## `causes`

Each cause entry follows `[{id, kind, t, h, text, p, evidence?, code?, links?, incident?, component?}]` and the rules below.

- Ids `C\d+`; `kind` is `root`, `contributing` or `trigger`; `t` is a noun phrase of twelve words or fewer; `h` is the handle; `text` and `p` are the twinned pair.
- `evidence`: a list of https URLs and citeable ids such as a `T#`, `W#`, or another `C#`; every id must resolve.
- `code`: `{lang, source, caption?}`, a snippet the page shows behind a toggle.
- `links`: links as below, with `closes` refused. A change closes an action, never a cause.
- `component`: the id of a declared component the page renders inside the cause's card, typically an `ir.notebook` with a `cells` subset.

## `actions`

Each action entry follows `[{id, t, h, owner, source, state, links?, due?, note?}]` and the rules below.

- Ids `AI\d+`; `t` is one line of sixteen words or fewer; `h` is the handle.
- `owner`: a person's or team's name. `check --strict` errors on a missing owner.
- `source`: the cause id the action answers, or `lessons` or `review`.
- `state`: `todo`, `in-progress`, `done`, `dropped`; rendered To do, In progress, Done, Dropped.
- `links`: links as below; `closes: true` marks the pull request or issue whose landing completes the action. `retro.py links --fetch` reports an action `done` whose closing change is still open, and one `todo` or `in-progress` whose closing change merged.
- `due`: `YYYY-MM-DD`. `note`: one sentence, for a dropped action the reason.

## `lessons`

`{well: [{text}], wrong: [{text}], lucky: [{text}]}`. Each lesson is one line of forty words or fewer; an empty column is hidden.

## `evidence`: the register of what the page renders and links

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

The snapshot formats and the output from `retro.py evidence fetch` and `evidence slack new` are in `reference/evidence.md`. `check` reads each snapshot's `schema` and `id`. It warns when a notebook's `time` does not span onset to resolved or the notebook was fetched live. It also warns when a monitor in `detection.monitors[]` has no file or a cited Slack permalink has no snapshot.

## `notes` and `footnotes`

`notes`: `[{t, md, component?}]`, prose blocks the Notes section renders in order; `component` places a declared component under the block. `footnotes`: `[{n, b}]`, referenced from prose as `[^n]`; `check` errors on a token with no footnote and warns on a footnote nothing references.

## `components`: declared interactive blocks

`components` maps a lower-case, hyphenated id to one block. The same schema walker as design-doc validates it against `reference/components/<kind>.json`. The retro kit is `ir.tiles`, `ir.windows`, `ir.timeline`, `ir.notebook`, `ir.monitor`, `ir.slack-thread`, `ir.actions`, and `ir.causes`; `reference/components.md` describes them. The reusable design-doc kinds `dd.tabs`, `dd.before-after`, `dd.whatif`, `dd.steps`, `dd.timeline`, `dd.matrix`, `dd.flow`, and `dd.lanes` also work.

Tiles, the windows gantt, the swimlane, notebooks, monitors, Slack threads, the actions table, and the causes tree render without a declaration. Declare a component to place it through a cause's `component`, a note's `component`, `impact.component`, `resolution.component`, or `detection.component`. A declaration can also override the component's options.

`check` errors when a declaration has no placement. It also rejects an `ir.notebook` with an unregistered notebook or missing cell index, an `ir.monitor` with an unregistered monitor, and an `ir.slack-thread` with no snapshot. For the remaining kinds, it rejects `ir.tiles` keys outside `timestamps`, an unknown `ir.windows` window, a `dd.timeline` gate with no register id, and a `dd.matrix` whose cells do not match its rows and columns.

## Ids, handles and citations

Citeable ids are `W\d+`, `T\d+`, `C\d+`, `AI\d+`, and `I\d+`, unique across the file. In prose, `(C1)` or `(C1, T7)` renders as the handles in parentheses, the way design-doc renders `(DQ12)`; `retro.py text` does the same. A handle `h` is required on every window, cause, action, and sub-incident. It is a two-to-five-word phrase a reader says aloud, with no trailing period or id. `check` errors on a citation no register defines and on a title or handle that names an id.

## Links

A link is either an https URL string or `{url, kind?, label?, closes?}`. In the
object form, `url` is https and `kind` is `pr`, `issue`, `commit`, or `doc`. A
`github.com` URL determines the kind, which must agree with that URL. A Linear
or other issue tracker URL is a `doc` with a `label`. `closes` belongs only on
an action's link.

## Snapshots and history

`retro.py snapshot --note "…" [--item "…"]…` writes `meta.rev` and appends to `meta.revisions`. It archives the whole file as `history/rev-<N>.json`. The revision entry records `files.evidence`, a digest over every file under `evidence/`, so a re-fetched notebook is a revision; `changed: ["evidence"]` marks one where only the evidence moved. `check` verifies that `rev` equals the last revision, revisions increase from 1, and every `history/rev-N.json` exists and parses. It warns when the evidence digest has moved since the current revision.

## Libraries

The page loads its libraries from jsdelivr at exact versions, pinned in one `LIBS` block at the top of the template's script and repeated here so `check` can compare the two:

| Library | Version | Files |
|---|---|---|
| `lucide-static` | `lucide-static@1.39.0` | `icons/<name>.svg`, as design-doc |
| `uplot` | `uplot@1.6.32` | `https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.iife.min.js` and `https://cdn.jsdelivr.net/npm/uplot@1.6.32/dist/uPlot.min.css` |

Mermaid is not used. The template draws the retro's tiles and windows as SVG. It also draws the swimlane as SVG, while `uPlot` draws the notebook series on canvas. `check` warns when the template pins a version this table does not state.

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

1. `meta`: title, slug, date present; status, severity, authors without `@`, repo, ref, timezone, `homeLink`, `subIncidents`, sections, ai.
2. Timestamps parse with an offset and stay in order; onset and resolved set unless draft, enforced in strict mode.
3. Windows: ids, kind, start before end, incident resolves; overlaps on one team warn.
4. Timeline: order, kinds, unique ids, phase outside the incident, refs are https, Slack refs are permalinks with snapshots, and deploys carry a change ref. Missing snapshots and change refs warn.
5. Impact metrics: `measured` boolean, cites resolve.
6. Causes: ids, kinds, evidence resolves, code shape, links without `closes`; strict mode requires a root cause once reviewed.
7. Actions: ids, states, owner, source resolves, `closes` only on pull requests or issues, due dates; strict mode requires owners and rejects open actions on a resolved retro.
8. Evidence: files exist, snapshot schemas and ids match, images carry alt, prs carry a role; notebook windows, liveness, and monitors without files warn.
9. Citations and footnotes resolve.
10. Handles present and well-formed; twins present and within the rules; a twin left stale across a snapshot.
11. Prose stating a derived duration warns.
12. Capitalisation over titles, handles and labels.
13. Component schemas and their hosts.
14. Forbidden terms: the check scans `retro.json`, `NOTES.md` and every text file under `evidence/`. It loads terms from `--forbidden-terms`, `FORBIDDEN_TERMS`, or the nearest `.customer-names`, and warns when none is configured.
15. Library pins against this file warn.
16. Template freshness against `plugins/_shared` when that source tree is present.
17. `ai.json` beside the retro or in its parent directory.
18. Revision history integrity.
