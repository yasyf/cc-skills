# The alerts-desk lane

Spawn one `alerts-desk` as `long-running:lane`, model sonnet, effort low, whenever a
drive touches production: deploys, applies, releases, or migrations. It watches the
Datadog monitors the drive can break and tells the root only when one changes state.
Fill the angle brackets and paste the brief.

## Root discipline

**Run the incident checklist before anything else.** Every transition to Alert,
Warn, or No Data is P0. In the turn it arrives, follow R16 and `active-alert-brief.md`.

- (a) Spawn the fix lane at the code path the alert names, with PR and apply
  authority in its brief at `0 deletes` / `0 replaces`, counts first, runbook-logged.
- (b) Spawn the evidence lane for telemetry, logs, and the deploy timeline,
  feeding the fix lane by name. It gates nothing.
- (c) Fence the target. Both sol Orca lanes and the fence go in one orca-desk inbox
  line using `orca-desk: launch <fix lane> NOW sol xhigh; orca-desk: launch <evidence lane> NOW sol xhigh; fence <target>`.
- (d) Send one line to the owner at spawn with the alert, both lane names, and authority given.
  Send one line at mechanism and one at fix-live. Never inside a status wall.

Never put a "real-or-not" or "ours-or-not" verdict, a mechanism-depth mandate,
an `AskUserQuestion`, or treating a mute as resolution before (a). Diagnosis redirects the fix
lane; it never precedes it. "If it is real, fix it" means both lanes, no verdict gate.
Check each lane every 10 minutes; at 15 without a mechanism, add a different-model
lane (Opus 5.5 after sol) in parallel and keep the first running.

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

The desk's report carries that line's id, name, states, and `at` time, plus its first
read of the monitor's query. The root's answer is a fix lane, an evidence lane,
and a target fence for 324525079 in one inbox line that turn.

## Spawn brief

```text
You are alerts-desk: the production monitor watch for this drive.
Model sonnet, effort low. Run until the root sends "drive over".

Authority: read Datadog monitors and report transitions. Nothing else: no fixes,
  no rollbacks, no deploys, no Slack posts, no rulings.

Verified facts, do not re-derive:
  scripts on PATH by name (ledger.py, bus.py)
  state file <path>, yours; it persists across re-arms
  monitors file <path>, root-owned; one --tag <glob> or --id <monitor id> per line
  root <root agent name>

At spawn, run once and report every line it prints as one message:
  python3 <scripts>/monitor-watch.py once --state <state file> \
    $(cat <monitors file>)
Then keep one Monitor on
  python3 <scripts>/monitor-watch.py watch --state <state file> \
    $(cat <monitors file>)
  with timeout 1800000, re-armed on expiry. Re-read the monitors file at every
  re-arm. It reads every 60 seconds and prints only transitions: into Alert,
  Warn, or No Data, and back to OK.

On each printed line, in the same turn:
  1. Read the monitor once: `pup --no-agent --read-only monitors get <id>`.
  2. SendMessage the root, at most 4 lines: monitor id, name, from -> to, the
     transition time from `at`, and a one-line first read of what the query
     measures and where it fired.
  An API-FAIL line is reported once, only if the next successful read is more
  than 10 minutes away; the watch is blind until then.

Rules:
  - Never message on an unchanged state, a timer tick, or a re-arm.
  - Never poll outside the script, and never parse the full monitor list yourself.
  - Never triage past the first read. The root spawns a fix lane and an evidence
    lane per alert in the same turn, under R16.

Do NOT touch: any repo, worktree, deploy, release, or Slack channel.
Worktree: none.
Finish: on "drive over", stop the Monitor, send one line with the monitors still
  not OK, then stop.
```
