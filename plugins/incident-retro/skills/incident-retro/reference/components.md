# The component kit

`incident-retro.html` draws eight `ir.*` blocks of its own and reuses eight
`dd.*` blocks from the design-doc plugin. A JSON schema under
`reference/components/` describes each block in the same shape as
`dd.timeline.json`: `kind`, `title`, `summary`, `belongs`, the schema body,
and an `example`. `retro.py check` validates every entry of `components` in
`retro.json` against its schema.

## How blocks reach the page

Most `ir.*` blocks render without a declaration. The page derives them from
the registers and places them where a reader expects them:

| Kind | Renders without a declaration when | Where |
|---|---|---|
| `ir.tiles` | always | top of Overview |
| `ir.windows` | `windows` has an entry | Overview, under the tiles |
| `ir.timeline` | `timeline` has eight or more entries | head of Timeline, above the key moments |
| `ir.causes` | `causes` has an entry | head of Causes, as the causal chain |
| `ir.monitor` | per `detection.monitors[]` entry | Detection and response |
| `ir.actions` | `actions` has an entry | Action items |
| `ir.notebook` | per `evidence.notebooks[]` entry, collapsed | Evidence |
| `ir.slack-thread` | per snapshot, inside a closed channel and outage-window group | Evidence |

Declare a block in `components` to place one elsewhere or with other
options. A declared block is placed by naming its id in one of five fields:

- `causes[].component` renders it inside that cause's card, under the
  evidence and links.
- `notes[].component` renders it under that note.
- `impact.component` renders it at the end of Impact.
- `resolution.component` renders it under the resolution text.
- `detection.component` renders it under the monitor cards.

`retro.py check` errors on a field that names an undeclared id and warns on
a declared block no field places. Component ids are `[a-z][a-z0-9-]*`. Every
block gets a Markdown flattening for the export and the assistant's context:
charts become per-series min, max and last values, transcripts become one
line per message, the tracker becomes a table.

## Shared styles and disclosures

The kit uses one spacing scale, `--s-1` through `--s-7`, and one type scale,
`--t-xs` through `--t-3xl`, from the template's custom properties. Cards,
chips, controls, tables, and tiles share their base rules across the page.
Extend those rules for a component's layout instead of adding a second
visual system.

`details.disc` is the disclosure component. Its direct `summary` contains
the `.dis` chevron, `.h` short heading, optional `.meta` context, and optional
`.when` timestamp. The body uses `.body`, `.x`, `.tbody`, or `.nbbody`; shared
padding keeps it aligned beneath the summary. The `--disc-in` property sets
the left inset.

Never set `display` on a `details` element, including through a shared card
class. It can break Chrome's hiding of closed content and paint the body.
Put flex or grid layout on the `summary` or an inner body wrapper. Keep
transcript text out of the summary.

Timeline entries, causes, decisions, and unknowns start closed and show `h`
in place of their full wording. Notes also start closed, using `t` as their
heading. Lesson columns, hypothesis and recognition tables, the glossary,
and revision history use the same disclosure. The action table shows `h`;
the Full wording toggle reveals its full titles and notes.

The Overview link row starts behind a disclosure showing its link count.
In Evidence, Sentry issues, Linear issues, builds, pull requests, runs, and
documents each have one closed disclosure showing the kind and its count.
The monitor chip list remains visible. The impact per-team table starts
closed under "What each of N teams saw," with the team codenames beside the
heading. These disclosures keep their tables and chip lists out of the
initial view.

Narrative sections keep their takeaway. Evidence, Glossary, and Notes open
on their heading and collapsed structure, with no takeaway; conclusions
belong in the sections that argue them.

`render-check` measures the initial page against a 1500-word default budget
and rejects visible Slack messages and the body selectors listed in
[reference/schema.md](schema.md#render-check-the-initial-page). It does not
force open disclosures closed. It tests painted visibility with
`Element.checkVisibility()` and `getClientRects()`, excluding descendants of
`[aria-hidden=true]` as decorative. A body painted by a CSS `display` rule on
a closed disclosure counts toward the gates; the closed state alone does
not hide it from the check.

## `ir.tiles`

The derived durations as a band of large numbers. Each tile is the gap
between two of the `timestamps`; a tile whose endpoint is null is hidden.
The built-in tiles are `ttd` (onset to detected), `tte` (detected to
engaged), `ttm` (onset to mitigated) and `ttr` (onset to resolved). `tiles`
picks and orders them or adds a custom one with `from`, `to` and `label`.

`extra` adds figures such as counts, each marked `measured` true or false
(false renders an "estimated" tag) with optional `unit`, `delta` and
`cites`. `cites` on the block adds a row of citation chips. Values are
rendered as `2h 14m`, `29m` or `5d 3h`.

```json
{"kind": "ir.tiles", "title": "How long it took",
 "tiles": [{"id": "ttd"}, {"id": "ttr"}, {"id": "ttq", "label": "Time to all clear", "from": "resolved", "to": "allClear", "tone": "ok"}],
 "extra": [{"label": "Failed checkouts", "value": "12 480", "measured": true, "cites": ["W1"]}]}
```

## `ir.windows`

A gantt of the outage windows drawn as SVG in the doc's own style. Each
window is a bar on its lane, filled by kind (outage, degraded, partial), with
its handle and duration on the bar when they fit and beside it otherwise.
Dashed rules mark the timestamps. Hovering a bar shows its card; clicking
opens the entry.

`windows` restricts the bars to a subset of ids. `lanes`
puts one lane per team (`team`), per sub-incident (`incident`), or per window (`none`);
the default is `incident` when the retro has sub-incidents, `team` when any
window names a team, else `none`. `marks: false` drops the timestamp rules.

```json
{"kind": "ir.windows", "title": "When customers were affected", "lanes": "team"}
```

## `ir.timeline`

The timeline as a swimlane: time runs left to right, with one lane per kind
of entry or per actor with `lanes: "actor"`. Each entry has a glyph with its
time beneath it. Tinted bands show outage windows and their handles;
vertical ticks extend deploy and alert marks. Dashed rules mark timestamps.

Each kind has its own glyph so the marks read without color. Deploys use a
triangle, alerts a warning triangle, and reports a circle. Hypotheses use a
diamond with a question mark, actions a square, and mitigations a filled
diamond. Resolution and all clear use a ticked circle.

Every timeline entry carries `h`, which names its closed row and its page
citations. `retro.py text` still substitutes local time in timeline citations.
Expanding a row shows the event text and refs. Hovering a mark shows the entry and,
when one of its refs points at a Slack snapshot, the quoted message.
Clicking scrolls to the row in the list.

The wheel zooms around the pointer, dragging pans, and a Reset control returns
to the resting view, which spans the in-incident entries and the timestamps
(entries carrying `phase: before` or `after` sit outside it). `from` and `to`
fix the resting view, `kinds` restricts the entries, `windows: false` hides
the bands and `zoom: false` disables the wheel and drag. Print draws the
resting view.

```json
{"kind": "ir.timeline", "title": "Who did what", "lanes": "actor",
 "from": "2026-08-14T09:30:00-07:00", "to": "2026-08-14T11:00:00-07:00"}
```

## `ir.notebook`

A Datadog notebook drawn from its snapshot under `evidence/datadog/`. The
card starts collapsed: its closed row shows the notebook's name, cell count,
author, and an Open in Datadog link. `open: true` starts it expanded. The
expanded card opens with the snapshot time and the window it covers. A
warning pill marks a notebook that used a live window at fetch time.

Cells render by type. Markdown cells go through the same Markdown dialect as
the rest of the retro. Timeseries cells draw with `uPlot`, using `--viz-1`
to `--viz-8` in request order and folding later series into "Other". The legend stays visible and
follows the cursor. With `bands` enabled by default, outage windows and
timestamps appear over the plot.

Toplists and query tables render as horizontal bars with the value
on the right. Log streams render as a table of time, service, status and
message, with a note when the snapshot was truncated. A cell with no snapshot
data (heatmaps, distributions) renders as a card naming why and carrying the
query, so the reader can open it in Datadog. Every cell host has either
`data-rendered` or `data-unrendered`, which `retro.py render-check` asserts.

`notebook` names the `evidence.notebooks[]` id. `cells` picks cell indexes,
`height` sets the chart height (180 to 480), `title` replaces the notebook's
name, and `open: true` starts the card expanded. `uPlot` loads from jsdelivr
the first time a notebook mounts; charts are recreated at three times the
resolution for print and re-inked when the color scheme changes.

```json
{"kind": "ir.notebook", "title": "Connection waits during the outage",
 "notebook": 777001, "cells": [1], "height": 200}
```

## `ir.monitor`

A monitor card reads its snapshot from `evidence/datadog/`. It shows the
name, type pill, current state, and role pill when the monitor is listed in
`detection.monitors[]` (caught it, missed it, added after). The query,
thresholds, evaluation delay, and no-data settings sit behind the closed
How it is configured disclosure.

Below it a strip draws the monitor's transitions (alert, warning, recovery,
no data) against the timestamps. `marks: false` drops the strip. A line shows firing latency
from onset, using `detection.monitors[].fired` or the first alert event.
When `recovered` is present, it also shows the time from firing to recovery.

```json
{"kind": "ir.monitor", "monitor": 12345}
```

## `ir.slack-thread`

A Slack thread from its snapshot under `evidence/slack/`. A snapshot is
verbatim evidence, not authored prose; never pass it through a language
model. Slack entries have no authored `h`.

The block starts closed. `irSlack` derives its row name from the
participants. For a single message, the secondary line uses `openingWords`
to take the first eight words (`OPENING_WORDS = 8`) after `plainSlack` strips
formatting and converts Slack links to their labels. It appends an ellipsis
when more words remain.

A snapshot with several messages shows their count instead. The row also
shows the first message's local time. `open: true` starts the block expanded.

In Evidence, `slackLogs` groups registered snapshots by channel and the first
outage window containing each snapshot's first message, including the
window's endpoints. A snapshot stays in one group even if later messages
cross a window boundary. Snapshots outside every window share the channel's
unassigned group.

Each outer disclosure starts closed and shows the channel,
participants, total message count, and earliest-to-latest time span. Opening
it reveals the individual closed snapshot disclosures, including their
mechanical row labels and opening words. Those labels are inside the closed
group and are never visible by default. Participants are listed up to twelve
names, followed by the remaining count; message counts sum the snapshots
without deduplicating messages.

Open, the block states when the snapshot was captured, links to Slack, and
shows each message with an initials avatar, the
author, the local time (UTC on hover), the text, reactions and file names.
The card escapes Slack's mrkdwn before rendering it: `<@U…>` mentions become the user
name the snapshot recorded (or `@unknown`), `<url|label>` becomes a link,
`*bold*`, `_italic_`, `~strike~`, inline code, fenced code and `>` quotes
render as such. `collapse` sets how many messages show before a Show more
control (default 6). Timeline rows and cause evidence that cite a message
permalink open the same single-message card inline.

```json
{"kind": "ir.slack-thread",
 "permalink": "https://acme-corp.slack.com/archives/C0ACME01/p1755187260000100", "collapse": 6}
```

## `ir.actions`

The action tracker. A progress bar reads "N of M done"; an action is done
when its `state` is `done` or when a link marked `closes` has merged on
GitHub. Filters narrow the table by state and a toggle groups the rows by
state, owner or source. `group` sets the starting grouping and
`showDone: false` hides done and dropped items.

Each row shows the id, handle, owner, and source; the source is a citation
chip or "Lessons" / "Review". It also shows the state pill, due date, and
linked pull requests and issues with their live GitHub state when the site
carries a token. The Full wording toggle reveals the title and any action
note beneath the handle; the due date remains visible in either mode.

```json
{"kind": "ir.actions", "group": "owner", "showDone": false}
```

## `ir.causes`

The causes as a tree read top to bottom: the trigger, the root cause, then
the contributing causes, each node showing only its short name `h`.
Hovering a node shows its card; clicking opens the entry below. A retro
with `meta.subIncidents` draws one tree per incident, with causes that name
no incident grouped under "Across incidents". `layout: "list"` renders the
same tiers as rows of citation chips.

```json
{"kind": "ir.causes", "title": "What broke, and what let it", "layout": "tree"}
```

## Reused `dd.*` blocks

The design-doc kit is available under the same declaration rules. The kinds
that fit a retro:

- `dd.timeline` for the order the fixes land in (hotfix, monitor, long-term
  fix), each phase `done`, `next` or `blocked`, with `gate` naming an action
  item id.
- `dd.tabs` for alternatives side by side, such as the queries used.
- `dd.before-after` for a diagram pair, and `dd.matrix` for an options table.
- `dd.flow` and `dd.lanes` for self-laying-out figures.
- `dd.steps` for a walkthrough; the retro has no system diagram, so
  `target` is ignored.
- `dd.whatif` for a rate that multiplies out.

Their schemas are copied into `reference/components/` as `dd.*.json` by the
shared build.
