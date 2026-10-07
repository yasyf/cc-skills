---
name: live-dashboard
description: Assemble, serve, and keep live a dashboard built for the task at hand from typed components - PRs waiting for review, stack landing previews, Slack test threads with the bot's response times, latency percentiles, design-doc gates, Datadog monitors, cci lanes - plus local components written for this one job. Use when a drive, release, live test, incident, sweep, or review needs a page the owner can watch; when the owner asks for a dashboard, a status page, or "show me where this stands"; or when long-running starts a drive dashboard.
---

# Live dashboard

A dashboard is one directory. `context.json` holds the facts every card binds from,
`layout.yaml` says which cards show and where, and `components/` holds the components
written for this job alone. The server reads all three, runs each card on its own
cadence, and serves the page on a port derived from the dashboard's id, so the link
survives restarts.

Pick the cards for the task. A preset is a starting point, and the owner's question is
the spec. A drive that waits on review leads with the review queue and what an
approval lands. A live bot test leads with the test threads and the bot's latency
percentiles. When no built-in answers the owner's question, write a local component.

## Start one

`live-dashboard` is the plugin's `bin/` entry point; inside a long-running drive the
hook prints its full path.

```bash
live-dashboard init --dir ~/.claude/scratch/bot-test/dashboard --preset live-test --title "Bot latency"
live-dashboard check --dir ~/.claude/scratch/bot-test/dashboard
live-dashboard start --dir ~/.claude/scratch/bot-test/dashboard
```

`init` copies a preset into `layout.yaml` and writes a minimal `context.json` when none
exists. `start` prints the link to give the owner; it is the tailnet URL when Tailscale
runs. A long-running drive needs none of this: `drive.py` writes `context.json`, and
the drive's hooks seed `layout.yaml` from the `drive` preset and start the server.

## Commands

| Command | Does |
|---|---|
| `init --dir D --preset NAME [--title T]` | Copies a preset into `D/layout.yaml`; refuses when one exists |
| `check --dir D [--only ID] [--static]` | Parses, binds, and runs every card once in a child process; prints one line per defect, exit 1 on any |
| `new NAME --dir D --payload KIND` | Writes `components/<name>.py` returning `KIND.example()` and appends a `local.<name>` card |
| `start --dir D` | Serves the dir, retiring a server another plugin version or pack set started; prints the link |
| `url --dir D` | Prints the running server's link; exit 1 when none runs |
| `status --dir D [--json \| --changes]` | Each card's status and error; `--changes` prints only transitions, forever, for a Monitor |
| `card --dir D ID [--json]` | One card from the running server, as markdown with its cites, or its JSON envelope |
| `snapshot --dir D [--card ID] [--md]` | Runs cards inline without a server and prints them |
| `stop --dir D`, `open --dir D` | Stops the server; opens the page in a browser |
| `catalog [--check]` | Regenerates `reference/catalog.md` from the code |

Presets: `drive`, `pr-review`, `live-test`, `incident`, `sweep`, `design-doc`,
`release`.

## The layout

```yaml
title: Chat memory
banner: Stack lands bottom-up once the owner approves.
sections:
  - title: Review
    components:
      - {use: pr-review-queue, id: review, width: 3, with: {parents: {'31055': '31129'}}}
      - {use: landing-preview, id: land-now, pinned: true}
  - title: Live test
    collapsed: true
    components:
      - use: latency-percentiles
        id: bot-latency
        every: 1m
        with: {files: 'timelines/*.timeline.json', deltas_from: origin, deltas_to: [thread, triage]}
```

Every card is one `use:` plus these optional keys:

| Key | Takes |
|---|---|
| `id` | A unique card id; defaults to the `use` |
| `title` | The card's heading; defaults to the component's title |
| `question` | The one question the card answers, shown under its title; defaults to the component's |
| `every` | `15s`, `30s`, `1m`, `2m`, `5m`, `15m`, or `manual`; defaults to the component's cadence |
| `width` | 1, 2, or 3 columns on a wide screen; a phone shows one column |
| `pinned` | `true` lifts the card above every section and notifies on new rows |
| `with` | The component's parameters |

Each parameter binds from `with`, else the `context.json` key of the same name,
else the component's default; a parameter with none of the three is a `check` error
naming the line. Cards with the same `use` and the same bound parameters share one
run.

Built-ins are bare names. `release.*` reads a Platy release pipeline. A pack named in
`context.json` under `packs` registers as `<name>.*`, so long-running's pack is `lr.*`.
This dir's own components register as `local.*`.

## Never on the board

The owner reads the page on a phone, through a tailnet link, often beside other people.
These never reach a payload, a card title, a banner, or a note:

- Secrets: keys, tokens, passwords, cookies, signed URLs. The secret scan below drops
  most, but a component must never read one into a row in the first place.
- PR bodies and review comment text. Link the PR and show its state instead.
- Raw log lines. `log-matches` counts named patterns and dates the newest match.
- Slack message text. `slack-feed` shows who posted, when and how fast, and links the
  message, and the words stay in Slack.
- Customer data and anything copied out of a production database.

A card that needs one of these links to where it lives.

## What the engine guarantees

- A failing card keeps its last good payload, shows its error, and backs off from twice
  its cadence up to four times it. A rate-limit error waits five minutes.
- A card past twice its cadence without a good run reads "stale since" the second missed
  run; one running past its timeout reads hung. The `dashboard-health` card lists every
  card's state.
- Every card shows the one question it answers under its title.
- On a phone the page is one column of cards, and every table row becomes a stacked
  card with labeled fields, so no table scrolls sideways.
- Payloads cache in `cache/<id>.json`, so a restart serves the last good data at once.
- Every payload is scanned before it is served. A value of any env var named like a
  key, token, secret, or password, or a token-shaped string, drops the payload and
  names the variable, never the value.
- A broken `layout.yaml` keeps the last good layout up and shows the defect with its
  line in a strip above the cards.

## Read next

| Page | Open it when |
|---|---|
| `reference/picking.md` | Choosing the cards for the owner's question and laying them out |
| `reference/catalog.md` | Looking up a built-in's payload, cadence, and parameters |
| `reference/authoring.md` | Writing a local component or a pack |
| `reference/keeping-live.md` | Keeping the layout honest over the task's life, or briefing the curator desk |
| `reference/release.md` | Reading what the `release.*` cards compute |
