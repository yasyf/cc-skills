# The alerts-desk lane

## Contents

- [What the desk does](#what-the-desk-does)
- [What the coordinator does with an alert](#what-the-coordinator-does-with-an-alert)
- [Alerts in watched channels](#alerts-in-watched-channels)
- [The monitors file and the desk inbox](#the-monitors-file-and-the-desk-inbox)
- [Spawn brief](#spawn-brief)

## What the desk does

The coordinator starts one `alerts-desk` remote beside the landing-desk with `lr desks start`, model sonnet, effort low. It watches whatever the drive deploys, applies, releases, or migrates. `scripts/monitor-watch.py` owns the polling and dedup. Fill the angle brackets and paste the brief below.

- **A1. Report monitor transitions, and nothing else.** The desk runs `monitor-watch.py watch --alert-drive <drive>` in the foreground under `lr wait` over the drive's monitors, by tag glob such as `release-target:*` and by named id. It messages the coordinator only on a move into Alert, Warn, or No Data, or a recovery to OK. Each message carries the monitor id, name, transition time, a one-line first read, any `production-operations` entry from the last 24 hours on the monitor's target (SKILL.md R24), and its launch decision. It never messages on an unchanged state or a timer tick, owns no fixes, and posts nothing to Slack.
- **A2. A run-binding watch proves a new run starts and finishes.** After an apply or release of `executor`, `api`, `runtime-v2`, or `restate-worker`, the watch or bake records a newly bound run, its start, and its completion. Crash and storage health alone cannot pass the watch.
- **A desk transition launches nothing by itself; an alert in a watched channel does.** `monitor-watch.py --alert-drive` posts `orca-desk: alert dd-<id> <link> :: <what fired>` to `orca-desk` on cci, which the runner records without launching. The alerts desk judges each transition into Alert or Warn and posts `orca-desk: incident dd-<id> <link> reported=<HH:MM> :: <what fired>` only when it deems an incident fix lane necessary.

## What the coordinator does with an alert

Each alert is P0 for the coordinator. Open the incident in the same turn:

- When the runner's `INCIDENT` line for a desk launch arrives, or the coordinator judges a reported transition an incident, post `INCIDENT OPEN <slug>: <what fired>`. Unless the desk already launched `<slug>-fix`, run `lr incident run <slug> --fix-brief <file>` in that turn with no verdict gate (`reference/active-alert-brief.md`, SKILL.md R16).
- Posting individual cci records to `orca-desk`, such as an evidence-lane launch: send one action per record. The runner logs `LAUNCH-FAILED` for a record that chains launches.
- An open alert claims, fences, freezes, and holds no release target unless the owner asks for that exact one; other releases keep flowing while the fix lands.
- A mute does not resolve the alert.

## Alerts in watched channels

An alert in a watched channel opens its own incident.

- With `alert_channels` set in the drive's `lr.json`, the supervisor reads each channel's 20 newest messages every 30-second tick, plus the thread of any card whose `reply_count` or `latest_reply` moved since the last tick within the hour.
- A Datadog or Sentry alert, a red Watch line (`The Watch failed for <component> on <env>: "<monitor>"`) or a red tenant smoke line (`<release> is live, but <test> on <env> went red`), in a card or a reply, that no `incident` record names launches `<slug>-fix` and `<slug>-evidence` through `lr incident run` on that tick and posts `ALERT-LAUNCHED` to the coordinator, with no verdict first.
- The slug is stable per monitor, component and env. A later red on the same key within a day posts one `ALERT-AGAIN` note to the open fix lane and launches nothing.
- Only a failed launch posts the `ALERT-UNOPENED` blocker; the coordinator then opens the incident by hand.
- An alert or page matching `orca.refused_briefs` in the drive's `runner.json` is logged at debug and opens nothing.

## The monitors file and the desk inbox

- The coordinator owns the monitors file, one `--tag <glob>` or `--id <monitor id>` per line, and rewrites it to change the set. The desk re-reads it on its next watch rerun, keeping its state file, identity, and session; no respawn.
- Address the desk over the cci bus with `cci post --drive <drive> --lane root --kind go --to alerts-desk --text "<ruling>"`, never by `SendMessage` (`reference/bus-contracts.md` I4).
- Pages in on-call channels during a drive release reach the coordinator from the orca runner, not this desk (`reference/release-lanes.md`).

## Spawn brief

```text
ccx: role=watch
You are alerts-desk: the production monitor watch for this drive.
Model sonnet, effort low. Run until the coordinator sends "drive over".

Authority: read Datadog monitors and the production-operations log, report
  transitions, and launch an incident fix lane when you judge one necessary.
  Nothing else: no fixes, no rollbacks, no deploys, no Slack posts, no rulings.

Verified facts, do not re-derive:
  tools on PATH by name (ledger.py, cci); cci drive <drive>
  state file <path>, yours; it persists across reruns
  monitors file <path>, coordinator-owned; one --tag <glob> or --id <monitor id> per line
  coordinator <coordinator agent name>

At spawn, run once and report every line it prints as one message:
  python3 <scripts>/monitor-watch.py once --state <state file> \
    $(cat <monitors file>)
Then run the watch in the foreground, one Bash call with `timeout: 570000`:
  lr wait 540 cci:<drive>:alerts-desk -- \
    python3 <scripts>/monitor-watch.py watch --state <state file> \
    --alert-drive <drive> $(cat <monitors file>)
  and rerun it on every return. Re-read the monitors file at every rerun. The watch
  reads every 60 seconds and prints only transitions: into Alert, Warn, or No Data,
  and back to OK. A record addressed to you ends the call; act on the records it
  printed first. Run it as the Bash call itself and never filter or discard its
  output.

Each known monitor's move into Alert or Warn also posts
  `orca-desk: alert dd-<id> <link> :: <what fired>` to `orca-desk` on cci.
  The runner records that record and launches nothing. A first read, No Data, and
  recovery never post an alert directive. You still report each transition.

On each printed line, in the same turn:
  1. Read the monitor once: `pup --no-agent --read-only monitors get <id>`.
     Then read the last 24 hours of the production-operations log (R24), before
     you name any mechanism:
     `ccn log show "$(ccn log list --label prod-ops | cut -f1)"`
     A monitor whose `created` or `modified` time is in the last hour is the
     first suspect on its own page: the release that applied it, finished or
     failed, may have shipped a query that misreads a healthy path. Name that
     release, quote its `applied before it failed` line when it failed
     (`tools/ci release watch <release>`), and put both in the report and the
     incident line's `what fired`, so the fix lane tests the monitor before the
     product.
  2. On a move into Alert or Warn, decide whether an incident lane is necessary.
     Launch one when the first read shows production impact the drive's work
     can cause or fix: a customer-facing error rate, failing runs, a stalled
     pipeline. A monitor that fires on a Forge release smoke team, a release
     check, or dev red always launches one, with "no customer impact" in the
     report as scope, never as a skip. Skip a flap that recovered before your read, No Data alone, and recovery.
     The orca desk decides whether a live lane already holds the monitor: it
     relays to `dd-<id>-fix`, or answers `LAUNCH-SKIPPED` naming the lane whose
     brief or incident record names the monitor, so post the line. To launch,
     post one record to the orca desk:
     `cci post --drive <drive> --lane alerts-desk --kind go --to orca-desk --text 'orca-desk: incident dd-<id> <link> :: <what fired>'`.
     In the same turn, post the incident record that names it, so the supervisor raises no
     `ALERT-UNOPENED` for it:
     `cci post --drive <drive> --lane alerts-desk --kind incident --to root --url <link> --text 'dd-<id> <what fired>'`
     When a log entry on the monitor's target explains the transition, launch
     nothing and quote the entry in the report; a release on the target never
     explains a smoke team or release check failing, it is the first suspect.
     A mute, a false-alarm verdict, or a monitor change that stops counting the
     failing teams, from ai-oncall or anyone, is never a reason to skip: launch,
     and name the proposal in the report so the coordinator pushes back. When unsure, launch nothing and
     say so in the report; the coordinator decides.
  3. Post one cci record to the coordinator, at most 4 lines: monitor id, name, from -> to, the
     transition time from `at`, a one-line first read of what the query
     measures and where it fired, any production-operations entry on that
     target with its authorizing answer, and `launched dd-<id>-fix` or
     `no lane: <why>`.
  An API-FAIL line is reported once, only if the next successful read is more
  than 10 minutes away; the watch is blind until then.

Rules:
  - Never message on an unchanged state, a timer tick, or a rerun.
  - Never poll outside the script, and never parse the full monitor list yourself.
  - Never triage past the first read. The coordinator opens an incident for each lane you
    launch in the same turn, under R16.

Do NOT touch: any repo, worktree, deploy, release, or Slack channel.
Worktree: none.
Finish: on "drive over", send one line with the monitors still not OK, then stop.
  Final text is empty or one line under 300 characters (outcome + pointer), never
  a repeat of the report.
```
