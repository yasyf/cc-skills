# long-running

A drive that spans a night of orders loses track the moment one session tries to do the work, watch CI, and talk to you at once, and your messages sit for minutes while it does bookkeeping. `long-running` splits the drive into two sessions. The communicator only talks to you and acks each message inside 5 to 10 seconds; the coordinator turns each order into a background agent that ships it end to end while one watcher turns every outside wait into an event.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install long-running@skills
```

Prerequisites:

- `cc-inbox` (`>=0.54.10`), `cc-notes` (`>=0.76.0`) and `orca` (`>=0.1.12`) plugins, installed as dependencies
- `cc-slack` plugin, for the messages and PR cards in your Slack direct message (DM)
- The GitHub CLI `gh` and the `claude` CLI on `PATH`
- Google Chrome or Chromium, to draw a PR's `arch` Show me diagram as the picture on its card with cc-present's arch-map, the component the dashboard draws it with live; set `CHROME` to one outside the usual places
- Graphviz `dot` on `PATH`, to draw a PR's `dot` Show me diagram

## Start a drive

Open two sessions in the same checkout and run the skill in each:

```text
/long-running durable-agents-rebuild
```

One runs `drive start <name> --role coordinator --watch <alert channel>`, arms the `drive watch` monitor, and holds the alert channels. The other runs `drive start <name> --role communicator --channel <your DM>` and holds your Slack DM. The two talk through cci records on the drive. After a restart, the same command rebinds that role to the new session, and the role's cci cursor delivers what it missed.

Run `drive setup` once so cc-slack's daemon can call `drive card` to draw PR cards without the plugin on its `PATH`.

## What a drive is

| Concept | What it is |
| --- | --- |
| Communicator | The session you talk to. For each of your messages it reacts and acks inside 5 to 10 seconds, with nothing else in that message, then starts a Sonnet handler; the handler answers you itself, shares what you asked in the thread you named, and hands orders and rulings to the coordinator. Reports, cards, drafts and checks all run in handlers, never in the communicator's own turn, so your next message is picked up within seconds too. |
| Coordinator | The session that runs the drive. It turns your messages into orders and jobs, routes events, lands and installs, and sends the communicator what to post. It never posts to Slack. |
| Order | One thing you asked for, in your words, with a state, an optional due time, its jobs, and its proof. |
| Job | One background agent owning one deliverable: a `builder` ships one change from code through merge and install, a `reader` answers one question an order needs, a `design` lane on Fable draws the initial design of a tricky order and its questions for you before any builder starts, a `codex` lane reviews or diagnoses. A job that reproduces a bug saves one pick-up-and-run file that `drive status` lists. While your Mac is over its load limit, a builder on the monorepo starts as a remote lane on a Sprite instead, so its builds and checks run there. |
| Event | A line from `drive watch`: a PR's CI or merge state changed, you tapped a PR card, a remote lane reported, a promised time passed with no line in its thread (again every 10 minutes until one is posted), or open work owes a summary (every 30 minutes, never while you are away). |
| Message | An ack or a summary in your thread, a question card at your DM root, or one PR card per stack with Approve and Hold. |

State lives in one file per drive under `~/.local/state/long-running/<drive>/`, written only by the `drive` CLI. Your rulings live in cc-notes answers, and cross-session records go through cci.

## What's inside

| Component | Kind | What it does |
| --- | --- | --- |
| `long-running:long-running` | Skill | Makes this session the communicator or the coordinator of a named drive. |
| `long-running:builder` | Agent | Ships one change end to end: code, PR, CI, merge, install, check. |
| `long-running:handler` | Agent | Handles one of your messages on Sonnet and answers you in Slack itself: a read, a share in the thread you named, or a handoff to the coordinator. |
| `long-running:reader` | Agent | Answers one question read-only, with evidence. |
| `long-running:designer` | Agent | Draws the initial design of a tricky order read-only on Fable, with each fork as a short question for you, before any code is written. |
| `drive` | CLI | Drive state: orders, jobs and their reproduction files, PRs, taps, status, the event stream, PR cards, and plugin install after merge; `drive place` says whether a builder starts here or remote, `drive job launch`, `resume` and `end` run a remote lane through the `orca` plugin, `drive job ask` keeps a job's question only once it fits on one short card, `drive job report` keeps the rest of a job's one-line return, and `drive manual <page>` prints one page of the skill, whose body is only a short map of them so a compaction brings little of it back. |
| `drive-watch` | Monitor | Streams PR state changes, taps, due times, ticks, and your return into the coordinator, one line each: taps, due times, remote lanes and your return at once, the rest batched every 5 minutes. |
| `session_start`, `pre_compact`, `landing_guard`, `scratch_folder`, `message_limits`, `record_lines` | Hooks | Inject the session's role and rules into each session at start, resume, and compaction, and tell a compaction's summary to keep only what the disk does not hold; refuse merging a drive PR in a `Forge-AI` repo without your tap or a recorded exemption; create the `Scratch:` folder a drive agent's prompt names before the agent starts, so its first file write cannot fail; refuse a post in your DM longer than you read: 60 words a line, 120 a summary, 150 a question card, while an answer to a question or order of yours goes out whole and an edit that rewords a line keeps about that line's length; cut a record between the two sessions to one line of 120 characters, its whole text moved into its body, your words and incidents kept whole. |

Part of [cc-skills](../../README.md).
