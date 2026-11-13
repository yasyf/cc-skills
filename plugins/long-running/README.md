# long-running

A drive that spans a night of orders loses track the moment one session tries to do the work, watch CI, and talk to you at once, and your messages sit for minutes while it does bookkeeping. `long-running` splits the drive into three sessions. The communicator only talks to you and acks each message inside 5 to 10 seconds; the coordinator turns each order into a background agent that ships it end to end while one watcher turns every outside wait into an event; the meta session fixes the drive's own tools and skill.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install long-running@skills
```

Prerequisites:

- `cc-inbox` (`>=0.54.10`), `cc-notes` (`>=0.76.0`) and `orca` (`>=0.1.12`) plugins, installed as dependencies
- `cc-slack` plugin, for the messages and PR cards in your Slack direct message (DM)
- The GitHub CLI `gh` and the `claude` CLI on `PATH`
- Google Chrome or Chromium, to draw each Show me visual (code, an arch map, a Mermaid or Graphviz diagram, a chart, a compare) as the picture on its Slack card, with the same module the dashboard draws it with live; set `CHROME` to one outside the usual places

## Start a drive

Open three sessions in the same checkout and run the skill in each:

```text
/long-running durable-agents-rebuild
```

One runs `drive start <name> --role coordinator --watch <alert channel>`, which starts the drive's watch as its own process, and subscribes to the alert channels, which reach it as one line per alert or release. Another runs `drive start <name> --role communicator --channel <your DM>` and holds your Slack DM. The third runs `drive start <name> --role meta` and holds drive tooling; a drive with none keeps tooling with the communicator. The sessions talk through cci records on the drive. After a restart, the same command rebinds that role to the new session, and the role's cci cursor delivers what it missed.

Run `drive setup` once so cc-slack's daemon can call `drive card` to draw PR cards without the plugin on its `PATH`.

## What a drive is

| Concept | What it is |
| --- | --- |
| Communicator | The session you talk to. For each of your messages it reacts and acks inside 5 to 10 seconds, with nothing else in that message, then starts a handler, and writes nothing in its own terminal, on Opus unless the ask is very simple; the handler answers you itself, shares what you asked in the thread you named, and hands orders and rulings to the coordinator. It starts no tool fix or skill rewrite: those are the meta session's. Reports, cards, drafts and checks all run in handlers, never in the communicator's own turn, so your next message is picked up within seconds too. |
| Coordinator | The session that runs the drive. It turns your messages into orders and jobs, routes events, lands and installs, and sends the communicator what to post. It never posts to Slack. |
| Meta session | The session that holds drive tooling: every fix to a tool the drive runs on, every skill rewrite a ruling of yours calls for, and every install of a released tool, each a background job. It never posts to Slack and never touches your product orders: what you should see goes to the communicator as a record. |
| Order | One thing you asked for, in your words, with a state, an optional due time, its jobs, and its proof. |
| Job | One background agent owning one deliverable: a `builder` ships one change from code through merge and install, a `reader` answers one question an order needs, a `design` lane on Fable draws the initial design of a tricky order and its questions for you before any builder starts, a `codex` lane reviews or diagnoses. A job that reproduces a bug saves one pick-up-and-run file that `drive status` lists. While your Mac is over its load limit, a builder on the monorepo starts as a remote lane on a Sprite instead, so its builds and checks run there. |
| Event | A line from the drive's watch, a process of its own: a PR's CI or merge state changed, you tapped a PR card, a remote lane reported, a promised time passed with no line in its thread (again every 10 minutes until one is posted), or open work owes a summary (every 30 minutes, never while you are away). |
| Message | An ack or a summary in your thread, a question card at your DM root, or one PR card per stack with Approve and Hold. |

State lives in one file per drive under `~/.local/state/long-running/<drive>/`, written only by the `drive` CLI. Your rulings live in cc-notes answers, and cross-session records go through cci.

## What's inside

| Component | Kind | What it does |
| --- | --- | --- |
| `long-running:long-running` | Skill | Makes this session the communicator, the coordinator or the meta session of a named drive. |
| `long-running:builder` | Agent | Ships one change end to end: code, PR, CI, merge, install, check. |
| `long-running:handler` | Agent, started by `drive handler` as its own `claude --bg` session | Handles one of your messages on Opus and answers you in Slack itself: a read, a share in the thread you named, a handoff to the coordinator, or a report from the meta session. |
| `long-running:reader` | Agent | Answers one question read-only, with evidence. |
| `long-running:designer` | Agent | Draws the initial design of a tricky order read-only on Fable, with each fork as a short question for you, before any code is written. |
| `drive` | CLI | Drive state: orders, jobs and their reproduction files, PRs, taps, status, the event stream, PR cards, and plugin install after merge, which reloads every drive session as `drive reload` does on its own; `drive place` says whether a builder starts here or remote, `drive job launch`, `resume` and `end` run a remote lane through the `orca` plugin, `drive job ask` keeps each question a job asks, beside its others until he answers each, only once it fits on one short card, `drive job report` keeps the rest of a job's one-line return, and `drive manual <page>` prints one page of the skill, whose body is only a short map of them so a compaction brings little of it back, and `drive manual <page> <row>` only that page's table rows naming the word, such as `TAP`; `drive manual verbs "<verb>"` is a verb's every flag, held equal to the parser by a test, so no session reads `--help`. |
| `drive` | MCP server | `say`: one call posts a message to your Slack DM and shows it on the drive's dashboard, its `{card:…}` and `{pr:…}` references linked the way each place shows them and its visuals drawn as pictures for Slack, a chart as Slack's own chart, and live on the dashboard, and a code or diagram fence in its text drawn as a picture for Slack too. A session runs one drive server from its start: the server's launch line names no plugin version, so `/reload-plugins` after a release keeps it running. Each call runs the newest installed drive and the first call after an install relists the tools, so a fix reaches a running session at once, while the server's own code changes at the session's next start. |
| `session_start`, `pre_compact`, `landing_guard`, `scratch_folder`, `message_limits`, `record_lines`, `one_line_result`, `quiet_turn`, `no_file_tools`, `no_slack_reads` | Hooks | Inject the session's role and rules into each session at start, resume, and compaction, and after a compaction the newest installed skill page, which the compaction would bring back as the session loaded it, and tell a compaction's summary to keep only what the disk does not hold; refuse merging a drive PR in a `Forge-AI` repo without your tap or a recorded exemption; create the `Scratch:` folder a drive agent's prompt names before the agent starts, so its first file write cannot fail; refuse a post in your DM longer than you read: 60 words a line, 120 a summary, 150 a question card, while an answer to a question or order of yours goes out whole and an edit that rewords a line keeps about that line's length; cut a record between the sessions to one line of 120 characters, its whole text moved into its body, your words and incidents kept whole; send a drive session's background agent back when its final message is more than one line of 500 characters, since that message lands whole in the session's context, then set its job's state and said line from that line, and for a handler's own `claude --bg` session, bound to the drive as it starts, post that line to the communicator as one cci `done` record and remove the session, or start that handler again at once on its brief when an API error ended its turn, which Claude Code hands the StopFailure hook in place of Stop; record on a job the agent whose brief's `Drive:` line names it at that agent's first Bash, Read, Write or Edit call or its stop, and run the job again at its first such call after `SendMessage` wakes it; send the communicator's turn back when it ends on more than the one word `Sent.` in its terminal, which you never read; refuse Read, Write and Edit in the coordinator's, communicator's and meta session's own turns, since Claude Code puts each file opened that way back into the session after every compaction; refuse a Slack thread or channel read in those sessions' own turns, since the whole read stays in its context, leaving it to a handler or job. |

Part of [cc-skills](../../README.md).
