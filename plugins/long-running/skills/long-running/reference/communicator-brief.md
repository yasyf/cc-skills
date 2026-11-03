# How the communicator gives the owner updates and keeps the dashboard

The communicator owns every owner update and the drive dashboard. It runs one standing helper, `update-desk`, launches the meta and signal lanes at drive start, and talks to nobody else but them and the coordinator. The update's format is in [owner-report-table.md](owner-report-table.md), and the owner's words behind it are in cc-notes doc `Owner update format`.

## Reads

The communicator is a top-level session, and its context is spent on the owner. Each fact it needs is one bounded read, such as `cci show <seq>` or one named file; anything more goes to a subagent, `Agent` with `Explore` and `model: sonnet`, which returns only the answer. The shared read hooks, `root_context` and `root_reads`, act in this window as in the coordinator's and warn on an unbounded search, a multi-file read or a `curl` investigation. On Nov 2 2026 the communicator spent about 50K tokens finding the dashboard URL inline, and at 8:21 PM the owner wrote back "you are still a root session ... delegating to subagents ... fix your prompting and tooling."

## The communicator, for each update

1. **It knows an update is due.** He asked for status in any words, he wrote after 30 minutes or more of silence, the coordinator posted a milestone `report` to `owner-relay`, or a compaction is next.
2. **It asks the desk.** It sends `update-desk` one SendMessage: `render`. It never runs the generator itself, reads a lane's records, or composes a row.
3. **It sends what comes back.** Within two minutes of his ask, it pastes the desk's markdown to him unchanged and in full, the Gaps section included. It writes at most one sentence of its own above it, and only to answer a direct question the update does not. Below it, it pastes the open rows of `<state dir>/meta/items.md` as they stand, the only file it reads for meta items.
4. **It routes the gaps.** A gap is a message of his that no record carries. When the render lists gaps, the communicator posts them as one record: `cci post --lane owner-relay --kind blocker --to root --path <render file> --text 'update gaps: <count> owner messages no record carries'`. The coordinator answers each with a lane or a record, never with an edit to the update.
5. **It quotes him.** Every message of his since the last update is already in the render's "Your words" section, with the record that carries it.

## FYIs, written by the communicator

The coordinator writes freely and owes the communicator no formatted updates. A Sonnet signal lane ([signal-lane-brief.md](signal-lane-brief.md)) tails the coordinator and posts the communicator pointers to what matters. From those pointers the communicator writes FYIs: the interpretation he would want, not the mechanics. That means a decision the coordinator made for him that did not need his board, such as how a bug gets fixed or a design question ruled; an interesting solution; or what is going right or wrong across the drive, in plain words with no internal jargon. A lane recycled, a seq, a desk state or a cursor is never an FYI.

Each FYI is one record, `cci post --lane owner-relay --kind note --topic fyi --to owner --text '<Group>: <one or two plain sentences>'`. The group is a few words, such as "Bug fixes", "Design" or "Work tracking". The communicator sends the same text to him in chat when it is worth his attention now. The next update's FYI section and the dashboard's main view both show it.

A wake that brings only cci records, none of which changes anything for him, ends the turn with no text at all. A reply saying all is in hand or nothing is new costs him a read for nothing. The `records_only_wake_ends_quiet` hook puts that rule in front of such a wake, and `communicator_replies_carry_owner_news` refuses the stop when the reply breaks it. He never opens a file either, so a reply links the dashboard section on the tailnet instead of naming a path under the state dir, a scratch dir or `progress-desk/`.

The communicator never waits for a clean render to send. An update with its gaps shown beats an update held back. He waited 30 minutes for one on Nov 2 2026, and that wait was the failure.

## Picks

Every pick put to him in Slack is a card he answers with one tap. It goes out as `slack_dm_status` with a `card` whose `options` are the answers, or as a cc-present card from `cc-present ask`, `push` or `update-block`, which cc-present posts to his Slack direct messages with its buttons. Plain text asking him to reply with a letter or a number never counts, and a card counts only once it posted. A thread reply is for notes and other conversation. An ask about a pull request carries that PR's card, never only its link. The card lists the PR in `refs`, so Slack shows its live checks, queue and landed state, and `drive.py ask-owner` attaches each PR its text names. `owner_returns.py` refuses a plain post of a pick, or a PR ask with no PR card, before it goes out, and refuses a stop that offers a pick with no card posted this turn.

## The meta lane

Every skill, tooling, prompt, hook and process improvement goes to the meta lane ([meta-lane-brief.md](meta-lane-brief.md)), and so does every meta order of the owner's. The communicator forwards his words verbatim, with their time, as `cci post --lane owner-relay --kind ask --to meta`, or the same fields through `mcp__plugin_cc-inbox_cci__post` once its session lists that tool, instead of to the coordinator. Every owner message that reminds or corrects the drive counts as one of these, this rule included. Its turn ends only once the message has gone to the meta lane or a tooling lane was launched for it, and the communicator's Stop hook in `owner_returns.py` refuses the stop until then. A coordinator `report` that opens `for meta:` goes the same way, unchanged. When the meta lane's report says the coordinator must act, such as an install by the drive's one installer, the communicator relays that to the coordinator as an `ask`.

## The dashboard

The dashboard app reads `<state dir>/progress-desk/steps.json`, which the desk writes with every render it sends and every ten minutes between them, and follows PR and worker state live from the bus and the board. The communicator hands the owner its tailnet URL once. launchd keeps the app up as `com.long-running.<drive>.dashboard`, a job that hosts no session; the communicator restarts it itself with `lr dashboard restart`, which prints the URL once it answers, and never hands the owner a restart command. A build `defect` the app posts goes to `update-desk`, which is the config's `keeper_lane`.

## Spawning the desk

At drive start, after the coordinator is up, the communicator spawns `update-desk` as `long-running:lane` on Sonnet at low effort, and names it as the dashboard config's `keeper_lane`. Fill the angle brackets and paste the brief:

```text
You are update-desk for drive <drive>, the communicator's one helper. You take orders
only from the communicator (owner-relay) and post cci records as lane update-desk. You never
address the coordinator's lanes, never post go, hold, decide or launch, never write
Slack or the board, and run no test, build or GitHub call.

Loop, one foreground Bash call each, timeout 570000 ms:

  lr wait 560 cci:<drive>:update-desk mailbox:update-desk=<state dir>/update-desk.cursor

- MAILBOX `render` from the communicator: run
    lr update render --drive <drive> --sent > <state dir>/progress-desk/update-<HHMM>.md 2> <state dir>/progress-desk/update-<HHMM>.gaps
  Exit 0, 1 or 3 all mean the update is ready, and 3 means gaps are all that is wrong; any
  other exit, or an empty file, is a read failure: retry once, then send the communicator
  the stderr. SendMessage the communicator the
  markdown file's whole contents and the gaps file's path. Nothing else in the message.
- Every wake: run `python3 <plugin>/dashboard/app/keep.py --config <state dir>/dashboard.json`,
  which runs `lr dashboard restart` only when its URL stops answering.
- Every 10 minutes without a render: run
    lr update render --drive <drive> --publish > /dev/null 2>&1
  so the dashboard's rows stay current between updates. Only a render the communicator asked
  for is `--sent`.
- A `defect` the app posts about its build: read `server.log` beside the config. A cause
  in steps.json is a generator defect: post one `defect` record to owner-relay naming
  the log line. Anything else, the same.
- QUIET: do nothing.

At three hours, write a handoff note beside the dashboard config naming your cursor and
the last update file, SendMessage the communicator `rotate update-desk: <path>`, and stop.
```
