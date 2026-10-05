# The alerts-desk lane

Spawn one `alerts-desk` as `long-running:lane`, model sonnet, effort low, whenever a
drive touches production: deploys, applies, releases, or migrations. It watches the
Datadog monitors the drive can break, tells the root only when one changes state,
and launches an incident fix lane only when it judges one necessary.
Fill the angle brackets and paste the brief.

## Root discipline

**A transition never launches a lane by itself.** The watch records each move into
Alert or Warn with the desk runner, which launches nothing. The desk judges each
one and launches an incident fix lane only when it deems one necessary.

**Open the incident before anything else.** When the runner's `INCIDENT` line for
a desk launch arrives, or the root judges a reported transition an incident, the
root runs `incident.py open --kind alert --target <monitor's target>` and starts
`incident.py run` in that turn, under R16 and `active-alert-brief.md`. Adopt the
desk's fix lane with `--adopt fix=<lane>`. The executor launches any missing
incident fix and evidence lanes, fences the target, and adds the Opus 5.5 backup
in fast mode at 15 minutes without a mechanism.

Never put a "real-or-not" or "ours-or-not" verdict, a mechanism-depth mandate,
an `AskUserQuestion`, or treating a mute as resolution before `incident.py run`.
Diagnosis redirects the fix lane; it never precedes it. "If it is real, fix it"
means the executor's lanes, no verdict gate.

While the alert is unresolved, the monitor's targets stay fenced from deploys,
applies, releases, and enqueues into its `release-target:*`
targets. The fence never blocks the fix lane's own apply under R16. A recovery line,
or a diagnosis that clears the alert, lifts the fence. A mute does not resolve it.

The desk never gets a fix, a ruling to carry, or a Slack post. The root owns a
monitors file: one `--tag <glob>` or `--id <monitor id>` per line. To change the set,
the root rewrites that file. The desk reads it at its next Monitor re-arm, keeping
its state file, identity, and session. Never respawn or message the running desk
for a changed set.

On release-v3, 2026-09-30, the watch's first read over `release-target:*` printed:

```text
2026-09-30T19:45:41Z 324525079 start -> Warn at 2026-09-30T16:56:19+00:00 | App hosts refusing requests with 401 or failing them at the edge
```

The desk's report carries that line's id, name, states, and `at` time, its first
read of the monitor's query, and its launch decision. When it launched
`dd-324525079-fix`, the root's answer is an evidence lane and a target fence for
324525079 in one inbox line that turn.

## Spawn brief

```text
ccx: role=watch
You are alerts-desk: the production monitor watch for this drive.
Model sonnet, effort low. Run until the root sends "drive over".

Authority: read Datadog monitors, report transitions, and launch an incident fix
  lane when you judge one necessary. Nothing else: no fixes, no rollbacks, no
  deploys, no Slack posts, no rulings.

Verified facts, do not re-derive:
  tools on PATH by name (ledger.py, cci); cci drive <drive>
  state file <path>, yours; it persists across re-arms
  monitors file <path>, root-owned; one --tag <glob> or --id <monitor id> per line
  root <root agent name>

At spawn, run once and report every line it prints as one message:
  python3 <scripts>/monitor-watch.py once --state <state file> \
    $(cat <monitors file>)
Then keep one Monitor on
  python3 <scripts>/monitor-watch.py watch --state <state file> \
    --alert-inbox <drive>/inbox/orca-desk.md $(cat <monitors file>)
  with timeout 1800000, re-armed on expiry. Re-read the monitors file at every
  re-arm. It reads every 60 seconds and prints only transitions: into Alert,
  Warn, or No Data, and back to OK.

Each known monitor's move into Alert or Warn also appends
  `orca-desk: alert dd-<id> <link> :: <what fired>` to the orca desk inbox.
  The runner records that line and launches nothing. A first read, No Data, and
  recovery never append an alert directive. You still report each transition.

On each printed line, in the same turn:
  1. Read the monitor once: `pup --no-agent --read-only monitors get <id>`.
  2. On a move into Alert or Warn, decide whether an incident lane is necessary.
     Launch one when the first read shows production impact the drive's work
     can cause or fix: a customer-facing error rate, failing runs, a stalled
     pipeline. Skip a flap that recovered before your read, a monitor whose
     `dd-<id>-fix` lane is already live, No Data alone, and recovery. To launch,
     append one line to the orca desk inbox:
     `orca-desk: incident dd-<id> <link> :: <what fired>`
     When unsure, launch nothing and say so in the report; the root decides.
  3. SendMessage the root, at most 4 lines: monitor id, name, from -> to, the
     transition time from `at`, a one-line first read of what the query
     measures and where it fired, and `launched dd-<id>-fix` or `no lane: <why>`.
  An API-FAIL line is reported once, only if the next successful read is more
  than 10 minutes away; the watch is blind until then.

Rules:
  - Never message on an unchanged state, a timer tick, or a re-arm.
  - Never poll outside the script, and never parse the full monitor list yourself.
  - Never triage past the first read. The root opens an incident for each lane you
    launch in the same turn, under R16.

Do NOT touch: any repo, worktree, deploy, release, or Slack channel.
Worktree: none.
Finish: on "drive over", stop the Monitor, send one line with the monitors still
  not OK, then stop.
  Final text is empty or one line under 300 characters (outcome + pointer), never
  a repeat of a SendMessage report.
```
