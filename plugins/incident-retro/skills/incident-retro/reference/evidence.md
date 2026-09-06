# Evidence snapshots

A retro cites evidence the reader can inspect without leaving the page, and
without the page holding a Datadog or Slack credential. Every piece of
external evidence is therefore snapshotted into a JSON file under
`evidence/` at authoring time, registered in `retro.json`, and rendered from
the file. `retro.py evidence fetch` writes the Datadog files; the authoring
agent writes the Slack files from its own Slack tooling; `retro.py check`
validates all of them on every run.

Three formats exist, each named by a `schema` string carrying a version:
`ir.notebook/1`, `ir.monitor/1`, `ir.slack/1`. A renderer refuses a file
whose `schema` it does not know.

## `evidence/datadog/notebook-<id>.json` (`ir.notebook/1`)

One file per Datadog notebook. Top level:

- `schema`: `"ir.notebook/1"`.
- `id`: the notebook id, an integer. Must equal the `id` of the
  `evidence.notebooks[]` entry that names the file.
- `url`: `https://app.<site>/notebook/<id>`, the "Open in Datadog" link.
- `site`: the Datadog site the snapshot came from, for example
  `datadoghq.com`.
- `fetchedAt`: when the snapshot was taken, UTC, `2026-09-05T18:00:00Z`.
- `name`, `author`, `modified`, `type`: the notebook's name, its author's
  display name (never the handle or email), its last-modified timestamp and
  its metadata type (`investigation`, `postmortem`, `runbook`, ...).
- `time`: `{start, end, live}`. `start` and `end` are ISO 8601 timestamps
  with offset. `live` is `false` for a notebook pinned to an absolute
  window. A notebook set to a live span such as "past 4 hours" is resolved
  against `fetchedAt` and recorded with `live: true`; `check` warns because
  such a window rarely covers the incident. Pin the notebook to an absolute
  window in Datadog before fetching.
- `cells[]`: one entry per notebook cell, in notebook order.
- `warnings[]`: strings the fetcher wants the author to see, currently only
  the live-span note.

Every cell carries `index` (its position) and `type` (the Datadog cell
type). The remaining fields depend on the type.

Markdown cell: `text`, the raw Markdown. The page renders it through the
same Markdown dialect as the rest of the retro.

Timeseries cell (`timeseries`):

- `title`, `graphSize`: the cell's title and Datadog graph size (`xs`
  through `xl`, or null).
- `time`: the cell's own window when it overrides the notebook's, else
  null. The data was queried over this window when present, the notebook's
  otherwise.
- `requests[]`: the queries the cell draws, normalised. Each request has
  `queries[]` (Datadog v2 query objects: `data_source`, `name`, `query`,
  and whatever else the notebook stored), `formulas[]` (`formula`, `alias`,
  and `limit` when the notebook set one) and `displayType` (`line`, `bars`,
  `area`). A legacy request written as a single `q` string becomes one
  query named `query1` and one formula `query1`.
- `data`: `{interval, t, series}`. `t` is the shared time axis in epoch
  seconds; `interval` is the rollup step in seconds; `series[]` holds one
  entry per (formula, tag group) with `request` and `formula` indexes back
  into `requests`, a `label` (the alias when the formula has one, the
  formula text when a request has several formulas and no alias, then the
  group tags), `tags`, `unit` (a short unit name or null) and `v`, values
  aligned with `t`, null where Datadog returned no point.
- `status`: `"rendered"`.

Toplist and query-table cells (`toplist`, `query_table`) carry the same
`title`, `graphSize`, `time` and `requests` fields, and `data.rows[]`: one
row per (formula, tag group) with `request`, `formula`, `label`, `tags`,
`unit` and a scalar `value`. Rows arrive in the order Datadog applied the
formula's `limit`.

Log-stream cell (`log_stream`):

- `query`, `indexes[]`, `columns[]`: the search, the indexes it targets
  (empty means all), and the columns the cell shows.
- `data`: `{logs, truncated, limit}`. Each log has `ts`, `status`,
  `service`, `host`, `message` cut to 500 characters, and `attrs`, the
  values of the cell's extra columns (anything beyond timestamp, status,
  service, host and message; a dotted column such as `@http.status_code`
  is looked up through the log's attribute tree, and a column that is not
  an attribute is read from the log's tags). `truncated` is true
  when more logs matched than `limit` kept.

Any other cell type (`heatmap`, `distribution`, `hostmap`, `note`, ...)
has no snapshot query. The fetcher stores the cell's raw Datadog
`definition`, sets `status: "unrendered"` and a `reason`, and the page
shows the definition's query text with the "Open in Datadog" link.

## `evidence/datadog/monitor-<id>.json` (`ir.monitor/1`)

One file per monitor.

- `schema`, `id`, `url` (`https://app.<site>/monitors/<id>`), `site`,
  `fetchedAt`: as for notebooks.
- `name`, `type` (`metric alert`, `query alert`, `log alert`, ...),
  `query`, `message` (the notification template, with its `{{...}}`
  variables intact), `tags[]`, `created`, `modified`, `overallState`.
- `thresholds`: `critical`, `warning`, `criticalRecovery`,
  `warningRecovery`, each a number or null.
- `options`: `evaluationDelay` (seconds), `notifyNoData`,
  `noDataTimeframe`, `renotifyInterval`, `requireFullWindow`.
- `events[]`: the monitor's state transitions inside the incident window,
  oldest first. Each has `ts`, `transition` (the destination state in
  lower case: `alert`, `warn`, `ok`, `no data`), `group` (the monitor
  group the transition applies to, for example `teamid:...`), `title` (the
  notification title, `[P2] [Triggered] ...`) and `url`, the event in
  Datadog.

The event window is `timestamps.onset` minus one hour to
`timestamps.allClear` (or `resolved` when there is no all-clear) plus one
hour. When `retro.json` has neither `onset` nor `resolved` yet, the fetcher
takes seven days either side of now and prints a warning; re-fetch once the
timestamps are in.

## `evidence/slack/<channel_name>-<ts>.json` (`ir.slack/1`)

One file per quoted message or thread.

- `schema`: `"ir.slack/1"`.
- `permalink`: the message permalink as copied from Slack,
  `https://<workspace>.slack.com/archives/<channel_id>/p<16 digits>`, with
  an optional `?thread_ts=<ts>` when the message is a reply.
- `channel_id`, `ts`: parsed from the permalink. `p1725555555123456`
  becomes `1725555555.123456`.
- `channel_name`: the channel's name without `#`. It names the file
  together with `ts`.
- `thread_ts`: the thread root's `ts` when the snapshot is a thread or a
  reply, else null.
- `fetchedAt`: ISO 8601 with offset.
- `messages[]`: `ts`, `user_id`, `user_name` (the display name, so the
  page can resolve `<@U...>` mentions), `datetime` (ISO 8601 with offset,
  the display time), `text` in Slack mrkdwn, `reactions[]` of
  `{name, count}`, and `files[]` of file names. For a message permalink the
  first entry is the quoted message; for a thread the root comes first and
  the replies follow, capped at 50.
- `truncated`: true when the thread had more than 50 replies.
- `redactions[]`: notes on anything the author removed from the transcript
  before saving it.

The page never calls Slack. It renders mrkdwn from the file: mentions
resolve to `@user_name` through the ids the file itself carries, links,
bold, italics, code and quotes are converted, and all text is escaped
first.

## How `evidence fetch` maps Datadog to the files

```
retro.py evidence fetch <dir> [--notebook ID|URL]… [--monitor ID|URL]…
    [--from-ssm --ssm-api-key-path P --ssm-app-key-path P [--aws-profile NAME] [--aws-region R]]
    [--site datadoghq.com] [--logs-limit 25] [--interval SECONDS]
    [--no-register] [--allow-terms] [--forbidden-terms REGEX] [--dry-run]
```

Notebooks and monitors are accepted as bare ids or as their
`https://app.<site>/notebook/<id>` and `https://app.<site>/monitors/<id>`
URLs.

A notebook is read with `GET /api/v1/notebooks/{id}`. Each cell is queried
over its own window when it has one, otherwise the notebook's:

- `timeseries` posts the cell's `queries[]` and `formulas[]` to
  `POST /api/v2/query/timeseries`. Datadog chooses the rollup unless
  `--interval` is given; when a response would carry more than about 1500
  points the fetcher re-queries with an interval that fits, and the step
  actually used lands in `data.interval`. A cell with several requests
  queries all of them at the first request's step so they share one time
  axis.
- `toplist` and `query_table` post to `POST /api/v2/query/scalar`; the
  column-oriented response becomes `data.rows`.
- `log_stream` posts to `POST /api/v2/logs/events/search` with the cell's
  query, indexes and window, sorted by timestamp, keeping `--logs-limit`
  lines.
- Every other type is stored unrendered with its raw definition.

A monitor is read with `GET /api/v1/monitor/{id}`; its transitions come from
`POST /api/v2/events/search` with the query
`source:alert @monitor_id:<id>` over the incident window described above.

The fetcher prints one line per cell (`cell 3 timeseries: 8 series, 330
points, interval 120s`, `cell 5 distribution: unrendered`) and one per
monitor. `--dry-run` reads the notebook definition and prints the plan for
each cell (type, queries, window, interval) without querying data or
writing anything.

Unless `--no-register` is passed, each snapshot is added to
`evidence.notebooks[]` or `evidence.monitors[]` in `retro.json` as
`{id, url, file}` (an existing entry with the same id is updated), and a
monitor absent from `detection.monitors[]` is appended there with `role`
(`caught` when it fired inside the window, `missed` when it existed and did
not, `added` when it was created after onset), `fired` (its first alert
transition) and `recovered` (the first recovery after that). Edit the role
if the heuristic is wrong.

Limits worth knowing: the v2 query endpoints return at most a few thousand
points per series, so a multi-day window at a one-minute rollup is where
`--interval` earns its keep; log searches are capped by `--logs-limit`, not
paginated; template variables in notebook queries are sent as written, so
resolve them in Datadog first; and `heatmap` and `distribution` cells have
no snapshot query at all.

## Credentials

Keys travel only in the `DD-API-KEY` and `DD-APPLICATION-KEY` request
headers and live in process memory; nothing in a snapshot identifies who
fetched it. By default the fetcher reads `DD_API_KEY` and `DD_APP_KEY` from
the environment. With `--from-ssm` it runs

```
aws ssm get-parameter --with-decryption --name <path> --query Parameter.Value --output text
```

once per key, adding `--region` and `--profile` when `--aws-region` and
`--aws-profile` are given. Both `--ssm-api-key-path` and
`--ssm-app-key-path` are required with `--from-ssm`; the plugin ships no
default parameter paths.

## The forbidden-terms grep

Log messages, monitor messages and notebook titles can carry customer
names, tenant identifiers or hostnames that must not reach a published
retro. Before writing any snapshot the fetcher serialises the payload and
greps it for a regex, resolved in this order: `--forbidden-terms`, then the
`FORBIDDEN_TERMS` environment variable, then the nearest `.customer-names`
file found walking up from the retro directory (one regex alternative per
line, `#` lines ignored, joined with `|`, matched case-insensitively). A
match aborts the write and names the matched terms; `--allow-terms` writes
anyway and prints the same terms as a warning. When no source is
configured the fetcher warns that the payload was not screened.
`retro.py check` runs the same grep over `retro.json`, `NOTES.md` and every
text file under `evidence/` on every run, so a snapshot written with
`--allow-terms` still fails the check until the terms are removed or the
list is updated.

## Authoring Slack snapshots

The agent writing the retro has Slack access through its own tooling; the
plugin does not. To cite a message or thread:

1. Run `retro.py evidence slack new --permalink URL --channel-name NAME`
   (add `--thread` to capture the whole thread, root first). It prints an
   `ir.slack/1` skeleton with `channel_id`, `ts` and `thread_ts` parsed
   from the permalink and one empty message, and names the file to write
   on stderr: `evidence/slack/<channel_name>-<ts>.json`.
2. Fill `messages[]` from the Slack thread or history call: every message
   needs `ts`, `user_id`, `user_name`, `datetime` with offset, `text`,
   `reactions` and `files`. Remove anything that must not be published and
   say so in `redactions[]`.
3. Register the file in `evidence.slack[]` as `{url, file}` where `url` is
   the permalink, and cite the permalink from the timeline, a cause's
   evidence or an action's links.
4. Run `retro.py evidence slack check <dir>`. It validates every file under
   `evidence/slack/` and every `evidence.slack[]` entry: the schema string,
   permalink shape, that `channel_id`, `ts` and `thread_ts` agree with the
   permalink, the file name, timestamps with offsets, the per-message
   fields, the 50-message cap and that the first message is the quoted one
   or the thread root.

`retro.py check` repeats the Slack validation and additionally warns about
every Slack permalink cited anywhere in `retro.json` that has no snapshot,
so a cited thread the reader cannot see is never silent.
