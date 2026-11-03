# The alerts-desk lane

Spawn one `alerts-desk` beside the landing-desk as `long-running:lane`, model sonnet, effort low, whenever a drive deploys, applies, releases, or migrates. `scripts/monitor-watch.py` owns the polling and dedup. Fill the angle brackets and paste the brief below.

## Root discipline

**A1. Report monitor transitions, and nothing else.** The desk runs `monitor-watch.py watch --alert-drive <drive>` in the foreground under `desk-wait.sh` over the drive's monitors by tag glob such as `release-target:*` and by named id. It messages the root only on a move into Alert, Warn, or No Data, or a recovery to OK. Each message includes the monitor id, name, transition time, a one-line first read, any `production-operations` entry from the last 24 hours on the monitor's target (SKILL.md R24), and its launch decision. It never messages on an unchanged state or a timer tick, owns no fixes, and posts nothing to Slack.

**A2. A run-binding watch proves a new run starts and finishes.** After an apply or release of `executor`, `api`, `runtime-v2`, or `restate-worker`, the watch or bake records a newly bound run, its start, and its completion. Crash and storage health alone cannot pass the watch.

**A transition never launches a lane by itself.** `monitor-watch.py --alert-drive` posts `orca-desk: alert dd-<id> <link> :: <what fired>` to `orca-desk` on cci, which the runner records without launching. The alerts desk judges each transition into Alert or Warn and posts `orca-desk: incident dd-<id> <link> reported=<HH:MM> :: <what fired>` only when it deems an incident fix lane necessary.

**Open the incident in the same turn.** Each alert is P0 for the root. When the runner's `INCIDENT` line for a desk launch arrives, or the root judges a reported transition an incident, the root opens the incident with `incident.py open --kind alert --target <monitor's target>`, adopting any desk-launched fix lane with `--adopt fix=<lane>`, and starts `incident.py run` in that turn with no verdict gate (`reference/active-alert-brief.md`, SKILL.md R16). When posting individual cci records to `orca-desk` such as an evidence-lane launch or a fence, send one action per record; the runner logs `LAUNCH-FAILED` for a record that chains launches or a fence. While the alert is unresolved, the monitor's targets stay fenced from deploys, applies, releases, and enqueues into its `release-target:*` targets; the fence never blocks the fix lane's own apply under R16. A recovery line or a diagnosis clearing the alert lifts the fence; a mute does not resolve it.

**An alert nobody opens raises itself.** With `alert_channels` set in the drive's `lr.json`, the supervisor reads those channels every tick, and a Datadog or Sentry alert no `incident` record names three minutes after it fired posts one `ALERT-UNOPENED` blocker to root naming it. Root opens the incident on sight, with no verdict first.

**Manage the monitors file and desk inbox without respawning.** The root owns the monitors file, with one `--tag <glob>` or `--id <monitor id>` per line, and rewrites it to change the set; the desk re-reads it on its next watch rerun while keeping its state file, identity, and session. The root addresses the desk over the cci bus with `cci post --drive <drive> --lane root --kind go --to alerts-desk --text "<ruling>"`, never by `SendMessage` (`reference/bus-contracts.md` I4). Pages in on-call channels during a drive release reach the root from the orca runner, not this desk (`reference/release-lanes.md`).

## Spawn brief

```text
ccx: role=watch
You are alerts-desk: the production monitor watch for this drive.
Model sonnet, effort low. Run until the root sends "drive over".

Authority: read Datadog monitors and the production-operations log, report
  transitions, and launch an incident fix lane when you judge one necessary.
  Nothing else: no fixes, no rollbacks, no deploys, no Slack posts, no rulings.

Verified facts, do not re-derive:
  tools on PATH by name (ledger.py, cci); cci drive <drive>
  state file <path>, yours; it persists across reruns
  monitors file <path>, root-owned; one --tag <glob> or --id <monitor id> per line
  root <root agent name>

At spawn, run once and report every line it prints as one message:
  python3 <scripts>/monitor-watch.py once --state <state file> \
    $(cat <monitors file>)
Then run the watch in the foreground, one Bash call with `timeout: 570000`:
  desk-wait.sh 540 cci:<drive>:alerts-desk mailbox:alerts-desk=<mailbox cursor file> -- \
    python3 <scripts>/monitor-watch.py watch --state <state file> \
    --alert-drive <drive> $(cat <monitors file>)
  and rerun it on every return. Re-read the monitors file at every rerun. The watch
  reads every 60 seconds and prints only transitions: into Alert, Warn, or No Data,
  and back to OK. A `MAILBOX` line or a record addressed to you ends the call; act
  on the text it printed first. Run it as the Bash call itself and never filter or
  discard its output: the `MAILBOX` text is your only copy of that mail.

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
     report as scope, never as a skip. Skip a flap that recovered before your read, a monitor whose
     `dd-<id>-fix` lane is already live, No Data alone, and recovery. To launch,
     post one record to the orca desk:
     `cci post --drive <drive> --lane alerts-desk --kind go --to orca-desk --text 'orca-desk: incident dd-<id> <link> reported=<HH:MM> :: <what fired>'`,
     where `reported` is the transition's Pacific time from `at`. In the same turn,
     post the incident record that names it, so the supervisor raises no
     `ALERT-UNOPENED` for it:
     `cci post --drive <drive> --lane alerts-desk --kind incident --to root --url <link> --text 'dd-<id> <what fired>'`
     When a log entry on the monitor's target explains the transition, launch
     nothing and quote the entry in the report; a release on the target never
     explains a smoke team or release check failing, it is the first suspect.
     A mute, a false-alarm verdict, or a monitor change that stops counting the
     failing teams, from ai-oncall or anyone, is never a reason to skip: launch,
     and name the proposal in the report so the root pushes back. When unsure, launch nothing and
     say so in the report; the root decides.
  3. SendMessage the root, at most 4 lines: monitor id, name, from -> to, the
     transition time from `at`, a one-line first read of what the query
     measures and where it fired, any production-operations entry on that
     target with its authorizing answer, and `launched dd-<id>-fix` or
     `no lane: <why>`.
  An API-FAIL line is reported once, only if the next successful read is more
  than 10 minutes away; the watch is blind until then.

Rules:
  - Never message on an unchanged state, a timer tick, or a rerun.
  - Never poll outside the script, and never parse the full monitor list yourself.
  - Never triage past the first read. The root opens an incident for each lane you
    launch in the same turn, under R16.

Do NOT touch: any repo, worktree, deploy, release, or Slack channel.
Worktree: none.
Finish: on "drive over", send one line with the monitors still not OK, then stop.
  Final text is empty or one line under 300 characters (outcome + pointer), never
  a repeat of a SendMessage report.
```
