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

The communicator never waits for a clean render to send. An update with its gaps shown beats an update held back. He waited 30 minutes for one on Nov 2 2026, and that wait was the failure.

## The meta lane

Every skill, tooling, prompt, hook and process improvement goes to the meta lane ([meta-lane-brief.md](meta-lane-brief.md)), and so does every meta order of the owner's. The communicator forwards his words verbatim, with their time, as `cci post --lane owner-relay --kind ask --to meta`, instead of to the coordinator. Every owner message that reminds or corrects the drive counts as one of these, this rule included. Its turn ends only once the message has gone to the meta lane or a tooling lane was launched for it, and the `owner_reminders_spawn_meta_lane` hook refuses the stop until then. A coordinator `report` that opens `for meta:` goes the same way, unchanged. When the meta lane's report says the coordinator must act, such as an install by the drive's one installer, the communicator relays that to the coordinator as an `ask`.

## The dashboard

The dashboard app reads `<state dir>/progress-desk/steps.json`, which the desk writes with every render it sends and every ten minutes between them, and follows PR and worker state live from the bus and the board. The communicator hands the owner its tailnet URL once. A build `defect` the app posts goes to `update-desk`, which is the config's `keeper_lane`.

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
  Exit 0 or 1 both mean the update is ready; any other exit, or an empty file, is a
  read failure: retry once, then send the communicator the stderr. SendMessage the communicator the
  markdown file's whole contents and the gaps file's path. Nothing else in the message.
- Every wake: run `python3 <plugin>/dashboard/app/keep.py --config <state dir>/dashboard.json`,
  which restarts the dashboard app only when its URL stops answering.
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
