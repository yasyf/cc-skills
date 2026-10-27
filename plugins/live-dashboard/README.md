# live-dashboard

**Watch where a task stands from your phone without reading logs.** `live-dashboard` assembles a live, per-task status page from typed components and serves it from one directory with a cadence scheduler, a cache, a secret scan, and an Ask-the-page chat.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install live-dashboard@skills
```

Prerequisites:

- `python3`, which runs the bundled script behind the `live-dashboard` CLI.
- Optional: Tailscale, so `start` prints a tailnet link you can open from a phone.

## Use it

### Watch a live test from your phone

Checking bot response times during a live test means tailing logs and refreshing Slack threads by hand. With `live-dashboard` installed, ask:

```text
Put up a dashboard for this bot test so I can watch the latency from my phone
```

The skill initializes a dashboard directory from the `live-test` preset with `context.json` and `layout.yaml`, wiring `slack-feed` alongside `latency-percentiles`. `slack-feed` shows who posted, when, and how fast, and links the message; the words stay in Slack. It runs `live-dashboard check` to parse, bind, and run every card once, then runs `live-dashboard start` to serve the directory on a port derived from the dashboard's id and print the link, using the tailnet URL when Tailscale runs. On a phone, the page renders as one column and table rows become stacked cards.

### See where a PR stack or release stands

Tracking which pull requests block a stack or where a release sits takes too many browser tabs. With `live-dashboard` installed, ask:

```text
Show me where this PR stack stands
```

The skill sets up the `pr-review` preset with `pr-review-queue` and `landing-preview` cards, leaving PR bodies and review comment text off the board. For other tasks, it starts from the `drive`, `incident`, `sweep`, `design-doc`, or `release` presets. `release.*` cards read a Platy release pipeline, and a pack named in `context.json` registers as `<name>.*`, so long-running's pack is `lr.*`. Payloads cache in `cache/<id>.json` so restarts serve the last good data at once, failing cards keep their last good payload and back off, and cards that miss two cadences show a stale-since marker.

### Answer a task-specific question with a custom card

Built-in cards cover common queues and feeds, but every incident or sweep has a question unique to the job. With `live-dashboard` installed, ask:

```text
Add a card that counts errors in the deploy log
```

When the built-in `log-matches` fits, the skill binds it in `layout.yaml` to count named patterns and date the newest match without putting raw log lines on the board. When no built-in answers the question, the skill runs `live-dashboard new NAME --payload KIND` to write a task-specific component into `components/`, registers it as `local.*`, and appends its card to the layout. A broken `layout.yaml` keeps the last good layout up and shows the defect with its line. Every payload is scanned before serving, and any value matching a key, token, secret, or password environment variable or token-shaped string drops the payload and names only the variable.

## What's inside

| Piece | Kind | What it does |
| --- | --- | --- |
| `live-dashboard:live-dashboard` | skill | Picks the cards for the question at hand, writes the layout and any local components, checks them, and serves the page |
| `live-dashboard` | CLI (bin/) | init, check, new, start, url, status, card, snapshot, stop, open, catalog |
| Presets | data | `drive`, `pr-review`, `live-test`, `incident`, `sweep`, `design-doc`, `release` |

Part of [cc-skills](../../README.md).
