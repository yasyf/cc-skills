# The component kit

`incident-retro.html` draws eight `ir.*` blocks of its own and reuses eight
`dd.*` blocks from the design-doc plugin. Every block is described by a JSON
schema under `reference/components/`, in the same shape as
`dd.timeline.json`: `kind`, `title`, `summary`, `belongs`, the schema body,
and an `example`. `retro.py check` validates every entry of `components` in
`retro.json` against its schema.

## How blocks reach the page

Most `ir.*` blocks render without a declaration. The page derives them from
the registers and places them where a reader expects them:

| Kind | Renders without a declaration when | Where |
|---|---|---|
| `ir.tiles` | always | top of Overview |
| `ir.windows` | `windows` has an entry | Overview, under the summary |
| `ir.timeline` | `timeline` has eight or more entries | head of Timeline |
| `ir.causes` | `causes` has an entry | head of Causes |
| `ir.monitor` | per `detection.monitors[]` entry | Resolution and detection |
| `ir.actions` | `actions` has an entry | Action items |
| `ir.notebook` | per `evidence.notebooks[]` entry, first one open | Evidence |
| `ir.slack-thread` | per `evidence.slack[]` entry | Evidence |

Declare a block in `components` to place one elsewhere or to change its
options. A declared block is placed in one of three ways:

- `meta.sections.<section>.components: ["<id>", …]` appends it to the end of
  that section. When a declared block of the same kind lands in the section
  where the implicit one would render, the implicit one is skipped, so a
  declared `ir.timeline` under `meta.sections.timeline` replaces the default
  swimlane rather than adding a second.
- `causes[].component: "<id>"` renders it inside that cause's card, under the
  evidence and links.
- `notes[].component: "<id>"` renders it under that note.

Component ids are `[a-z][a-z0-9-]*`. Every block gets a Markdown flattening
for the export and the assistant's context: charts become per-series min,
max and last values, transcripts become one line per message, the tracker
becomes a table.

## `ir.tiles`

The derived durations as a band of large numbers. Each tile is the gap
between two of the `timestamps`; a tile whose endpoint is null is hidden.
The built-in tiles are `ttd` (onset to detected), `tte` (detected to
engaged), `ttm` (onset to mitigated) and `ttr` (onset to resolved). `tiles`
picks and orders them, or adds a custom one with `from`, `to` and `label`.
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
opens the entry. `windows` restricts the bars to a subset of ids. `lanes`
puts one lane per `team`, per sub-`incident`, or one per window (`none`);
the default is `incident` when the retro has sub-incidents, `team` when any
window names a team, else `none`. `marks: false` drops the timestamp rules.

```json
{"kind": "ir.windows", "title": "When customers were affected", "lanes": "team"}
```

## `ir.timeline`

The timeline as a swimlane: time runs left to right, one lane per kind of
entry (or per actor with `lanes: "actor"`), a glyph per entry with its time
under it, the outage windows as tinted bands with their handles, deploys and
alerts extended as vertical ticks, the timestamps as dashed rules. Each kind
has its own glyph, so the marks read without colour: a triangle for deploys,
a warning triangle for alerts, a circle for reports, a diamond with a question
mark for hypotheses, a square for actions, a filled diamond for mitigations,
a ticked circle for resolution and all clear.

Hovering a mark shows the entry and, when one of its refs points at a Slack
snapshot, the quoted message. Clicking scrolls to the row in the list. The
wheel zooms around the pointer, dragging pans, and a Reset control returns
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
block is a collapsible card: the header carries the notebook's name, the cell
count and author, and an Open in Datadog link; the first line inside states
when the snapshot was taken and the window it covers, with a warning pill
when the notebook used a live window at fetch time.

Cells render by type. Markdown cells go through the same Markdown dialect as
the rest of the retro. Timeseries cells draw with uPlot, series coloured
`--viz-1` to `--viz-8` in request order, a ninth and later series folded into
"Other"; the legend is always present and follows the cursor, and with
`bands` (the default) the outage windows and the timestamps are painted over
the plot. Toplists and query tables render as horizontal bars with the value
on the right. Log streams render as a table of time, service, status and
message, with a note when the snapshot was truncated. A cell with no snapshot
data (heatmaps, distributions) renders as a card naming why and carrying the
query, so the reader can open it in Datadog. Every cell host ends up with
`data-rendered` or `data-unrendered`, which `retro.py render-check` asserts.

`notebook` names the `evidence.notebooks[]` id. `cells` picks cell indexes,
`height` sets the chart height (180 to 480), `title` replaces the notebook's
name, and `open: false` starts the card collapsed. uPlot loads from jsdelivr
the first time a notebook mounts; charts are recreated at three times the
resolution for print and re-inked when the colour scheme changes.

```json
{"kind": "ir.notebook", "title": "Connection waits during the outage",
 "notebook": 777001, "cells": [1], "height": 200}
```

## `ir.monitor`

A monitor card from its snapshot under `evidence/datadog/`: the name, a type
pill, a role pill when the monitor is listed in `detection.monitors[]`
(caught it, missed it, added after), the current state, the query, the
thresholds, and the evaluation delay and no-data settings. Below them a strip
draws the monitor's transitions (alert, warning, recovery, no data) against
the timestamps, and a line reads how long after onset it fired and how much
later it recovered, computed from `detection.monitors[].fired` and
`recovered`, or from the first alert event when those are absent.
`marks: false` drops the strip.

```json
{"kind": "ir.monitor", "monitor": 12345}
```

## `ir.slack-thread`

A Slack thread from its snapshot under `evidence/slack/`. The header names
the channel, counts the messages and states when the snapshot was captured,
with an Open in Slack link. Each message shows an initials avatar, the
author, the local time (UTC on hover), the text, reactions and file names.
Slack's mrkdwn is rendered after escaping: `<@U…>` mentions become the user
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
state, owner or source. Each row shows the id, the handle over the full
title, the owner, the source as a citation chip (or "Lessons" / "Review"),
the state pill with the due date, and the linked pull requests and issues
with their live GitHub state when the site carries a token. `group` sets the
starting grouping and `showDone: false` hides done and dropped items.

```json
{"kind": "ir.actions", "group": "owner", "showDone": false}
```

## `ir.causes`

The causes as a tree read top to bottom: the trigger, the root cause, then
the contributing causes, each node a card with the handle and the plain
twin. Hovering a node shows its card; clicking opens the entry below. A retro
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
- `dd.steps` for a walk-through; the retro has no system diagram, so
  `target` is ignored.
- `dd.whatif` for a rate that multiplies out.

Their schemas are copied into `reference/components/` as `dd.*.json` by the
shared build.
