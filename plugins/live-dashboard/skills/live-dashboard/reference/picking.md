# Pick and assemble the cards

Start from the owner's question, not from the catalog. Write down the two or three
things the owner checks this dashboard for, then pick the card that answers each one
directly and put it first. Everything else goes in a collapsed section or nowhere.

## Match the question to the cards

| The owner asks | Cards |
|---|---|
| What waits on me? | `lr.needs-owner` pinned, `asks` |
| Which PRs need my review, and what lands if I approve? | `pr-review-queue`, `stack-graph`, `landing-preview`, `held-prs` |
| Does the bot answer, and how fast? | `slack-feed` on the test threads, `latency-percentiles` over the bot's timelines |
| Which test cells pass, and where is each one's evidence? | `matrix-file` over the test lane's results file |
| Which test pages are open, who holds each, and how long is left? | `view` over the pages file with a `due` column |
| Which test channels are live, and when did each last move? | `slack-channels` with the channel ids or a name `prefix` |
| What must be true before we switch it on? | `gates`: `pr:`, `file:`, `ledger:` and `monitor:` sources, `status:open` for hand steps |
| What did the owner rule, and who acts on it? | `rulings` |
| Is the design decided? | `design-gates` on the doc's register ids, `gates` for the PRs and files that close them |
| Is production healthy right now? | `datadog-monitors`, `timeseries`, `incident-feed`, `log-matches` |
| How far along is the sweep? | `kv-file` on the progress file, `view` over the result table, `cci-lanes` |
| Which stacks can Platy deploy? | `release.tiles`, `release.stacks`, `release.builds` |
| Is anything stuck or stale? | `open-records`, `cci-lanes`, `dashboard-health` |

`reference/catalog.md` lists every parameter. Read a card's section there before
setting `with:`; most bind from `context.json` and need nothing.

## Lay it out

- The first section answers the owner's first question. Pin at most one card, the one
  whose new rows deserve a browser notification.
- Give tables with many columns `width: 3`, graphs and feeds `width: 2`, and tiles,
  key-value cards, and checklists `width: 1`.
- Collapse the drive's plumbing: lanes, quota, notes, health. The owner opens it when
  something looks wrong.
- Keep a dashboard under about twelve cards. A card nobody reads costs quota and
  attention every cadence.
- Set `every` only to slow a card down or to make a manual one. A card that calls GitHub
  GraphQL stays at `2m` or slower; the shared quota refuses calls under 500 remaining.

## Bind once, reuse everywhere

Put a fact every card shares in `context.json`, not in each `with:`. A long-running
drive's `context.json` already carries `id`, `title`, `program`, `repo`, `checkout`,
`ledger`, `sessions`, `cci_drive`, `orca_run`, `state_dir`, `started_at`, and `packs`.
Add a key for the task, such as `owner_login`, and every card with that parameter
binds it. `drive.py` rewrites only the keys it owns, so a task key survives the next
registry write.

Two cards with the same `use` and the same bound parameters share one run. `landing-preview`
reads the `stack-graph` and `pr-review-queue` cards by id, `stack` and `review` by
default, so keep those ids or pass `graph:` and `queue:`.

## Stacks Graphite rebased

A PR whose base is `graphite-base/<n>` names no parent PR, so `stack-graph` draws it
under that branch as its own trunk. Map it to its real parent with `parents`, keyed by
PR number:

```yaml
- {use: stack-graph, id: stack, width: 2, with: {parents: {'31055': '31129', '31079': '31071'}}}
- {use: pr-review-queue, id: review, width: 3, with: {parents: {'31055': '31129', '31079': '31071'}}}
```

## Live tests

Name the threads the test posts in. `slack-feed` reads each permalink through
`cc-slack thread`, keeps the bot's messages with `bot:`, and stamps each with its
latency from the thread's opening message. Until `threads` names one, the card says
it has not run.

`latency-percentiles` reads three sources. `files` with `value`, `group`, and
`failure` reads benchmark rows from JSON, JSONL, CSV, or TSV, and `failure: '!success'`
counts a false `success` field. `deltas_from` with `deltas_to` measures from one
timeline entry to each later stage across every timeline file. `query` reads a Datadog
metric through `pup`. List the groups the test must cover in `groups:` so a missing one
shows as not run instead of vanishing.

```yaml
- use: latency-percentiles
  id: bot-latency
  with:
    files: 'timelines/*.timeline.json'
    deltas_from: origin
    deltas_to: [thread, triage, incident-channel]
    groups: [thread, triage, incident-channel]
    budgets: {thread: '60000'}
```

Name the test matrix's results file. `matrix-file` puts each result row in the cell its
`row` and `col` fields name, links the cell to the row's `link`, such as the Slack
permalink or the test plan section, and hovers its `detail` fields, such as the defect's task id and
priority. Fix `rows` and `cols` so an untested cell still shows.

```yaml
- use: matrix-file
  id: test-matrix
  width: 3
  with: {file: 'results.jsonl', row: stage, col: cell, link: permalink, detail: [defect, task, priority], cols: [page, ack, triage, mute]}
```

Open test pages and channels read from a file the test lane keeps, one row per page
or channel. A `due` column counts down to its deadline and reads overdue past it; an
`age` column shows the last activity.

```yaml
- use: view
  id: live-pages
  with: {file: 'pages.tsv', where: {state: '!resolved'}, columns: ['page:link', holder:badge, deadline:due, last_activity:age]}
```

`latency-percentiles` in `deltas_from` mode plots one history point per timeline file,
so each run reads as one point, and every entry in `budgets` draws as a rule, such as a
120-second ack bar.

## Not live yet

A card whose source starts only after a deploy, such as a Datadog query for a metric
the release adds, can sit in a collapsed section titled for it, with a `title` that
names what makes it live. It reads "no points" until then; drop the words from the
title once it fills.

## When no built-in fits

Write a local component when the owner's question needs a join no built-in makes: a
PR-by-ruling matrix, a plan's steps as gates, a spike's recall table. `live-dashboard new
<name> --dir D --payload <Kind>` scaffolds it; `reference/authoring.md` covers the
rest. Prefer `view` when the answer is a filtered table from a file, another card, or a
cc-notes record; it needs no code.
