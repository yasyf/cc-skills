# long-running

A drive that spans a night of orders loses track the moment one session tries to do the work, watch CI, and talk to you at once. `long-running` keeps one session, the driver, on talking and routing: every order goes to a background agent that ships it end to end, and one watcher turns every outside wait into an event.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install long-running@skills
```

Prerequisites:

- `cc-inbox` (`>=0.54.10`), `cc-notes` (`>=0.76.0`) and `orca` (`>=0.1.12`) plugins, installed as dependencies
- `cc-slack` plugin, for the messages and PR cards in your Slack direct message (DM)
- The GitHub CLI `gh` and the `claude` CLI on `PATH`
- Graphviz `dot` on `PATH`, to draw a PR's `arch` or `dot` Show me diagram as the picture on its card

## Start a drive

```text
/long-running durable-agents-rebuild
```

The session you type in becomes the driver. It runs `drive start <name>`, arms the `drive watch` monitor, and takes orders from the terminal and your Slack DM. After a restart, the same command rebinds the drive to the new session.

Run `drive setup` once so cc-slack's daemon can call `drive card` to draw PR cards without the plugin on its `PATH`.

## What a drive is

| Concept | What it is |
| --- | --- |
| Driver | The one session you talk to. It plans, spawns jobs, routes events, and writes every message you get. It hands off all the work and never blocks. |
| Order | One thing you asked for, in your words, with a state, an optional due time, its jobs, and its proof. |
| Job | One background agent owning one deliverable: a `builder` ships one change from code through merge and install, a `reader` answers one question, a `codex` lane reviews or diagnoses. While your Mac is over its load limit, a builder on the monorepo starts as a remote lane on a Sprite instead, so its builds and checks run there. |
| Event | A line from `drive watch`: a PR's CI or merge state changed, you tapped a PR card, a remote lane reported, a promised time passed, or open work owes a summary (every 30 minutes, never while you are away). |
| Message | An ack or a summary in your thread, a question card at your DM root, or one PR card per stack with Approve and Hold. |

State lives in one file per drive under `~/.local/state/long-running/<drive>/`, written only by the `drive` CLI. Your rulings live in cc-notes answers, and cross-session records go through cci.

## What's inside

| Component | Kind | What it does |
| --- | --- | --- |
| `long-running:long-running` | Skill | Makes this session the driver of a named drive. |
| `long-running:builder` | Agent | Ships one change end to end: code, PR, CI, merge, install, check. |
| `long-running:reader` | Agent | Answers one question read-only, with evidence. |
| `drive` | CLI | Drive state: orders, jobs, PRs, taps, status, the event stream, PR cards, and plugin install after merge; `drive place` says whether a builder starts here or remote, and `drive job launch`, `resume` and `end` run a remote lane through the `orca` plugin. |
| `drive-watch` | Monitor | Streams PR state changes, taps, due times, and ticks into the driver. |
| `session_start`, `landing_guard` | Hooks | Inject the drive's state into the driver at start, resume, and compaction; refuse merging a drive PR in a `Forge-AI` repo without your tap or a recorded exemption. |

Part of [cc-skills](../../README.md).
