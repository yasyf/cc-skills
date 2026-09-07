# Evidence snapshots

A retro keeps its evidence inspectable without placing a Datadog or Slack
credential in the page. Each external source gets a JSON snapshot under
`evidence/` at authoring time. `retro.json` registers the snapshot, and the
page renders it from disk. `retro.py evidence fetch` writes the Datadog files.
The authoring agent writes Slack files through its own Slack tooling, then
`retro.py check` validates the full set.

Three formats exist, each named by a `schema` string carrying a version:
`ir.notebook/1`, `ir.monitor/1`, `ir.slack/1`. A renderer refuses a file
whose `schema` it does not know.

## `evidence/datadog/notebook-<id>.json` (`ir.notebook/1`)

Each Datadog notebook gets one file with these top-level fields:

- `schema`: `"ir.notebook/1"`.
- `id`: the notebook id, an integer. Must equal the `id` of the
  `evidence.notebooks[]` entry that names the file.
- `url`: `https://app.<site>/notebook/<id>`, the "Open in Datadog" link.
- `site`: the Datadog site the snapshot came from, for example
  `datadoghq.com`.
- `fetchedAt`: when the snapshot was taken, UTC, `2026-09-05T18:00:00Z`.
- `name`, `author`, `modified`, `type`: the notebook's name, its author's
  display name, its last-modified timestamp, and its metadata type. The
  author's value is never a handle or email. Metadata types include
  `investigation`, `postmortem`, and `runbook`.
- `time`: `{start, end, live}`. `start` and `end` are ISO 8601 timestamps
  with offset. `live` is `false` for a notebook pinned to an absolute
  window. A notebook set to a live span such as "past 4 hours" is resolved
  against `fetchedAt` and recorded with `live: true`; `check` warns because
  such a window rarely covers the incident. Pin the notebook to an absolute
  window in Datadog before fetching.
- `cells[]`: one entry per notebook cell, in notebook order.
- `warnings[]`: strings the fetcher wants the author to see. The live-span
  note is the only warning defined here.

Every cell carries its position in `index` and the Datadog cell type in
`type`. The remaining fields depend on that type.

Markdown cell: `text`, the raw Markdown. The page renders it through the
same Markdown dialect as the rest of the retro.

A `timeseries` cell has these fields:

- `title`, `graphSize`: the cell's title and Datadog graph size. The size runs
  from `xs` through `xl`, or is null.
- `time`: the cell's own window when it overrides the notebook's, else
  null. The fetcher queries this window when present and the notebook's window
  otherwise.
- `requests[]`: the queries the cell draws, normalised. Each request has
  `queries[]` containing Datadog v2 query objects with `data_source`, `name`,
  `query`, and any stored notebook fields. It also has `formulas[]` with
  `formula`, `alias`, and an optional `limit`, plus a `displayType` of `line`,
  `bars`, or `area`. A legacy request written as a single `q` string becomes
  one query named `query1` and one formula `query1`.
- `data`: `{interval, t, series}`. `t` is the shared time axis in epoch
  seconds, and `interval` is the rollup step in seconds. `series[]` holds one
  entry for each formula and tag group. Its `request` and `formula` indexes
  point back into `requests`. The `label` uses the alias when present, then
  the formula text when a request has several formulas, then the group tags.
  `tags` holds those tags, `unit` is a short unit name or null, and `v` holds
  values aligned with `t`, using null where Datadog returned no point.
- `status`: `"rendered"`.

Cells of type `toplist` and `query_table` carry the same `title`, `graphSize`,
`time`, and `requests` fields. Their `data.rows[]` has one row for each
formula and tag group, with `request`, `formula`, `label`, `tags`, `unit`, and
a scalar `value`. Rows arrive in the order Datadog applied the formula's
`limit`.

A `log_stream` cell has these fields:

- `query`, `indexes[]`, `columns[]`: the search, its target indexes, and the
  columns shown. An empty index list means all indexes.
- `data`: `{logs, truncated, limit}`. Each log has `ts`, `status`,
  `service`, `host`, `message` cut to 500 characters, and `attrs`, the
  values of the cell's extra columns. Extra columns are anything beyond
  timestamp, status, service, host, and message. A dotted column such as
  `@http.status_code` makes the fetcher traverse the log's attribute tree. For
  any other column, the fetcher reads the value from the log's tags.
  `truncated` is true
  when more logs matched than `limit` kept.

Other cell types, including `heatmap`, `distribution`, `hostmap`, and `note`,
have no snapshot query. The fetcher stores the cell's raw Datadog
`definition`, sets `status: "unrendered"` and a `reason`, and the page
shows the definition's query text with the "Open in Datadog" link.

## `evidence/datadog/monitor-<id>.json` (`ir.monitor/1`)

Each monitor has one file.

- `schema`, `id`, `url`, `site`, `fetchedAt`: as for notebooks. The `url`
  reads `https://app.<site>/monitors/<id>`.
- `name`, `type`, `query`, `message`, `tags[]`, `created`, `modified`,
  `overallState`: the monitor fields. Types include `metric alert`,
  `query alert`, and `log alert`. `message` is the notification template and
  keeps its `{{variable}}` values intact.
- `thresholds`: `critical`, `warning`, `criticalRecovery`,
  `warningRecovery`, each a number, or null.
- `options`: `evaluationDelay` in seconds, `notifyNoData`,
  `noDataTimeframe`, `renotifyInterval`, `requireFullWindow`.
- `events[]`: the monitor's state transitions inside the incident window,
  oldest first. Each has `ts` and a lower-case destination state in
  `transition`: `alert`, `warn`, `ok`, or `no data`. `group` names the monitor
  group, for example `teamid:checkout`. `title` holds the notification title,
  such as `[P2] [Triggered] Checkout errors`, and `url` points to the event in
  Datadog.

The event window starts one hour before `timestamps.onset` and ends one hour
after `timestamps.allClear`. It uses `resolved` as the endpoint when there is
no all-clear. When both `onset` and `resolved` are unset, the fetcher takes
seven days either side of now and prints a warning. Re-fetch once the
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
- `messages[]`: `ts`, `user_id`, `user_name`, `datetime`, `text`,
  `reactions[]`, and `files[]`. `user_name` is the display name, so the page
  can resolve Slack mention ids. `datetime` is the display time in ISO 8601
  with offset. `text` uses Slack mrkdwn, `reactions[]` contains
  `{name, count}`, and `files[]` contains filenames. For a message permalink the
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

The fetcher reads a notebook with `GET /api/v1/notebooks/{id}`. It queries each
cell over its own window when present and uses the notebook's window otherwise.
The cell types map as follows:

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
- For every other cell type, the fetcher stores the raw definition and marks it
  unrendered.

The fetcher reads a monitor with `GET /api/v1/monitor/{id}`. Its transitions come from
`POST /api/v2/events/search` with the query
`source:alert @monitor_id:<id>` over the incident window described above.

The fetcher prints one line per cell and one per monitor. Example cell lines
are `cell 3 timeseries: 8 series, 330 points, interval 120s` and
`cell 5 distribution: unrendered`. `--dry-run` reads the notebook definition
and prints each cell's type, queries, window, and interval without querying
data or writing anything.

Unless the caller passes `--no-register`, the fetcher adds each snapshot to
`evidence.notebooks[]` or `evidence.monitors[]` in `retro.json` as
`{id, url, file}`. It updates an existing entry with the same id. The fetcher
also appends a monitor absent from `detection.monitors[]` with `role`, `fired`,
and `recovered`. The role is `caught` when it fired inside the window,
`missed` when it existed but did not fire, and `added` when it was created
after onset. `fired` is its first alert transition, and `recovered` is the
first recovery after that. Edit the role if the heuristic is wrong.

The v2 query endpoints return at most a few thousand points per series. Use
`--interval` for a multi-day window at a one-minute rollup. `--logs-limit`
caps log searches, and the fetcher does not paginate them. The fetcher sends
notebook template variables unchanged, so resolve them in Datadog first. Cells
of type `heatmap` and `distribution` have no snapshot query.

## Credentials

Keys travel only in the `DD-API-KEY` and `DD-APPLICATION-KEY` request
headers and live in process memory; nothing in a snapshot identifies who
fetched it. By default, the fetcher reads `DD_API_KEY` and `DD_APP_KEY` from
the environment. With `--from-ssm`, the fetcher runs this command once per
key:

```
aws ssm get-parameter --with-decryption --name <path> --query Parameter.Value --output text
```

It adds `--region` and `--profile` when `--aws-region` and `--aws-profile`
are given. Both `--ssm-api-key-path` and
`--ssm-app-key-path` are required with `--from-ssm`; the plugin ships no
default parameter paths.

## The forbidden-terms grep

Log and monitor messages can carry customer names or tenant identifiers.
Notebook titles can carry hostnames. These values must not reach a published
retro.

Before writing any snapshot, the fetcher serialises the payload and greps it
for a regex. It checks `--forbidden-terms` first, then the `FORBIDDEN_TERMS`
environment variable. If neither supplies a regex, it walks up from the retro
directory to find the nearest `.customer-names` file. The file carries one
regex alternative per line.

The fetcher ignores lines starting with `#`. It joins the remaining
alternatives with `|` and matches them case-insensitively. A match aborts the
write and names the matched terms. `--allow-terms` writes anyway and prints the
same terms as a warning. When no source is configured, the fetcher warns that
it did not screen the payload.

`retro.py check` repeats this screening across the authored record. It scans
`retro.json` and `NOTES.md`. It also scans every text file under `evidence/`,
so a snapshot written with `--allow-terms` still fails the check until the
terms are removed or the list is updated.

## Write Slack snapshots

The agent writing the retro has Slack access through its own tooling; the
plugin does not. Follow these steps to cite a message or thread. Add `--thread`
to the first command when the snapshot should capture the whole thread, root
first.

1. Run `retro.py evidence slack new --permalink URL --channel-name NAME`.
   The command prints an `ir.slack/1` skeleton with `channel_id`, `ts` and `thread_ts` parsed
   from the permalink and one empty message, and names the file to write
   on stderr: `evidence/slack/<channel_name>-<ts>.json`.
2. Fill `messages[]` from the Slack thread or history call: every message
   needs `ts`, `user_id`, `user_name`, `datetime` with offset, `text`,
   `reactions` and `files`. Remove anything that must not be published and
   say so in `redactions[]`.
3. Register the file in `evidence.slack[]` as `{url, file}` where `url` is
   the permalink, and cite the permalink from the timeline, a cause's
   evidence or an action's links.
4. Run `retro.py evidence slack check <dir>` to validate every file under
   `evidence/slack/` and every `evidence.slack[]` entry. The checks cover the
   schema string, permalink shape, filename, timestamp offsets, per-message
   fields, and the 50-message cap. They also confirm that `channel_id`, `ts`,
   and `thread_ts` agree with the permalink and that the first message is the
   quoted one or the thread root.

`retro.py check` repeats the Slack validation and also warns about every Slack
permalink cited in `retro.json` without a snapshot. A missing thread is always
visible in the check output.
