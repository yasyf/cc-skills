# Active production alert lanes

An active alert has one durable owner, the incident executor in
`scripts/incident.py`. The root opens the incident and starts the executor. From
then on the executor runs intake, the comms events, the sol fix and evidence
launches, the landing check, activation, the canary, failed-work recovery, and the
final reply. It reaches the root only with an unresolved decision. Its state lives
in the `scripts/actions.py` store, one file per incident under
`~/.claude/long-running/incidents/`, so a restarted executor resumes where the last
one stopped and never repeats a side effect.

At intake, the executor opens an investigation and an `incident <id>` log in
cc-notes on the drive checkout, both labeled `incident` and `incident:<id>`.
Milestones and lane-brief attachments go to the log; evidence and verdicts go to
the investigation. It records mechanism with `root-cause`, landing with
`fix --commit`, and not-ours with `exonerate`. Read the record with
`incident.py status --incident <id>`, `ccn investigation show <id>`, and
`ccn log show <id>`. A refused write asks the root once and never blocks a launch.

## Root discipline

**Open the incident and start its executor before anything else.** The alert is P0
under R16.

```sh
incident.py open --kind pr-review --incident <id> --thread <permalink> --onset <ISO time> \
  --bus <bus id> --comms-lane <comms lane name> --root-lane <root agent name> \
  --checkout <repo checkout> --orca-run <run id> --orca-repo <repo id> \
  --common "$(ccn -R <repo checkout> attachment path <briefs log> common.md)" \
  --alert <Sentry issue or monitor link> --runbook <runbook> [--adopt fix=<lane already running>] \
  --grant thread=<grant id> [--grant channel=<grant id>] --grant sync=<authority ref> --grant rebuild=<authority ref> \
  [--expect-config <text the live configuration carries>]
incident.py run --incident <id>
```

Start `run` as a background command. It exits only when the final reply posts or
another owner takes the incident.

- Each grant is the owner's authority for one kind of side effect. The root
  records the two Slack grants from the owner's words, and the comms lane passes
  each id as `--grant <id>`. `thread` comes from `cc-slack grant --url <permalink>
  --quote "<owner's words>"` and covers replies in that thread. `channel` comes
  from `cc-slack grant --channel <channel id> --quote "<owner's words>"` and covers
  top-level posts in that one channel, never a reply, broadcast, or edit. `sync`
  and `rebuild` cite the owner's words or the standing rule that authorizes the
  pipeline sync and the re-kick. A missing grant pauses only the step that needs it.
  The executor asks the root once, and `incident.py grant` lets the next pass
  proceed.
- The executor launches the fix and evidence lanes with
  `scripts/orca-launch.sh <lane> sol xhigh <brief>`. Both run on `gpt-6.1-sol`
  with the fast tier at `xhigh`. If 15 minutes pass without a mechanism or a PR, it
  launches an Opus 5.5 backup on the same brief. It refuses any other model for
  the fix or evidence role and never uses fable or astra.
- It posts the fence for the target on the bus, and it reports `opened`, `pr`,
  `landed`, `activated`, `live`, `recovered`, and `closed` to the root lane as
  milestones. The root relays those to the owner as one line each, in Pacific
  time.
- The root's whole job after `open` is the decisions the executor asks for on the
  bus: a missing grant, a silent comms lane, an overdue action, a failed canary,
  or a read-back that still drifts after an apply. `incident.py status --incident
  <id>` prints the milestones and open decisions in Pacific time.

`--kind pr-review` is a broken review pipeline: it carries activation, canary, and
re-kick. `--kind alert` is any other alert, opened with `--target <service or stack>`
in place of the pipeline. It has no activation step, and it is live when the fix or
evidence lane runs `incident.py note --incident <id> --live "<evidence>"`. Either kind
closes without a fix when a lane runs `incident.py note --incident <id> --not-ours
"<evidence>"`. When the desk already launched a fix or evidence lane, `--adopt
fix=<lane>` records it instead of starting a second worker; that lane's brief
must carry the `incident.py note` lines below.

**Never between the alert and `incident.py run`.**

- A "real-or-not" or "ours-or-not" verdict
- A mechanism-depth mandate
- An `AskUserQuestion`
- Treating a mute as resolution

Diagnosis redirects the fix lane; it never precedes it. An owner's "if it is real,
fix it" means the executor's fix lane plus its evidence lane, not a verdict gate.

## What the executor guarantees

- A merge never closes the incident. After landing, the incident stays
  `activation_pending` until `ci sync <pipeline>`, run from a worktree at the landed
  commit, reports that the stored configuration matches. If `--expect-config` was
  given, the stored configuration must also carry that text.
- No rebuild fires on the old configuration. The first rebuild is the canary, and
  the others wait until its `Prepare PR evidence` job passes.
- The affected work is re-derived on every pass. It covers every build that failed
  or was canceled between the onset and activation, grouped by PR. The latest failed
  build at an open PR's current head is rebuilt exactly once. A closed PR and a
  moved head are accounted as skipped, not rebuilt. A head that fails after the
  first pass is still owned.
- Each side effect is recorded as `started` before it runs. A response lost in a
  crash or a timeout leaves the action `unverifiable`, and only a read of external
  state settles it: the rebuild's `rebuilt_from`, the comms post on the bus, the
  launch receipt, or the sync dry run. An action is retried only after that read
  proves the effect absent, at most three attempts in all. A lost launch with no
  receipt goes to the root, because a second launch could start a second worker.
- `recovered` waits for every build in the outage window to finish, then needs the
  activation receipt and accounting for every rebuild: re-run,
  green, red with PR links, and skipped. `closed` needs the posted ts of the final
  reply.
- A worker's completion settles its assignment, not the incident.
- Reassignment is `actions.py transfer --expect-generation <n> --to <owner>`,
  followed by the new owner's `actions.py ack`. The former owner's next write fails
  on the generation and its runner exits. A runner started as the former owner
  refuses to start. Nothing stops or signals a session.

## Comms lane contract

The comms lane comes from [slack-lane-brief.md](slack-lane-brief.md#incident-comms-lane).
The executor posts each event to it as a bus `ask` from
`incident-<id>`. Each entry carries JSON with `event` (`ack`, `pr`,
`review-request`, `landed`, `live`, or `recovered`), the `grant` id, the `surface`,
the `thread`, and the event's facts, with times already in Pacific. The lane writes
the copy through astra and posts it with
`cc-slack reply --url <thread> --grant <grant> --text <copy>`. For the `channel`
surface it posts with `cc-slack send --channel <channel id> --grant <grant> --text
<copy>`. Consecutive replies under one thread grant need no human message between
them. It then answers the entry:

```sh
bus.py post --bus <bus id> --from <comms lane name> --kind answer --re <seq> --text "posted ts=<ts>"
```

An event left unanswered for two minutes becomes one decision for the root.

## Fix lane brief

```text
You are <fix lane name>, fixing the active production alert.
Model gpt-6.1-sol, effort xhigh, service tier fast; Orca codex worker.
Owner: the incident executor <root agent name> for incident <incident id>.
Authority: implement the fix and open its PR through <submit skill>.
  While the alert is active, apply the fix to production through
  <break-glass skill> without asking when the plan shows 0 deletes and 0 replaces.
  Log the apply in <runbook>. Any delete or replace stops for the owner.
  A CI pipeline whose stored settings a `ci sync` writes is activated by the
  executor after landing; never run that sync yourself.

Verified facts, do not re-derive:
  alert <alert link>; monitor <monitor id> / <monitor state>
  metric query <metric query>; runbook <runbook>
  named code path <named code path>; evidence lane <evidence lane name>
  comms lane <comms lane name>; bus <bus id>, topic <incident topic>
  record: investigation <investigation id>, log <incident log id>
    (cc-notes, on the drive checkout)

If you were launched unsupervised, report through the bus; Orca carries no worker_done for you.

Do:
  1. Start at the named code path now, while evidence is still arriving.
     Never wait for a diagnosis verdict before starting the fix.
  2. Read <evidence lane name>'s findings as they arrive by bus or Orca messages.
     Redirect with the evidence, including to a monitor fix for a monitor defect.
     A muted monitor still gets fixed; use the mute window.
  3. The moment you know the mechanism, record it for the executor:
     `incident.py note --incident <incident id> --mechanism "<mechanism, one line>"`.
  4. Open the PR through <submit skill>, then record it:
     `incident.py note --incident <incident id> --pr <PR number>`.
     The executor reports it, asks for human review when the broken surface is
     the reviewer, watches the landing, activates, and re-runs the failed work.
  5. For a fix that needs a production apply, plan through <break-glass skill>,
     apply under the authority above, log the apply in <runbook>, and verify it
     against the alert's metric. Then record the evidence:
     `incident.py note --incident <incident id> --live "<evidence, one line>"`.
     If the evidence shows the alert is not ours, record that instead:
     `incident.py note --incident <incident id> --not-ours "<evidence, one line>"`.
  6. Record every artifact in cc-notes, never under ~/.claude/scratch:
     `ccn log append <incident log id> --entry "<kind: one line>" --attach <file, repeatable>`.
     A directory goes as one .tgz; `--replace` updates a same-named attachment.
     Investigation detail goes to
     `ccn investigation append <investigation id> "<evidence, one line>"`.
     Mechanism, PR, live, and not-ours still go through `incident.py note`; the
     executor records the verdicts. Never run `ccn sync`; the root syncs.

Escalate: with no mechanism 15 minutes after launch, the executor launches an
  Opus 5.5 backup lane on this brief in parallel; keep working.
  Never fable or astra.

Do NOT touch: unrelated targets, files, branches, or another lane's worktree.
Worktree: <absolute path, exclusive to this lane>.
Finish: when the PR is noted and any production apply is verified, report to
  <root agent name> with the PR, head, apply counts, and runbook entry. Use the bus
  when unsupervised, otherwise the preamble's Orca worker_done command. Your
  completion settles this assignment; the executor owns the incident until its
  final reply. Never report live from a mute.
```

## Evidence lane brief

```text
You are <evidence lane name>, reading telemetry, logs, and the deploy timeline for the active production alert.
Model gpt-6.1-sol, effort xhigh, service tier fast; Orca codex worker.
Owner: the incident executor <root agent name> for incident <incident id>.
Authority: read-only evidence. Feed <fix lane name> and the executor.
  You gate nothing. Never ask the fix lane to wait for diagnosis.

Verified facts, do not re-derive:
  alert <alert link>; monitor <monitor id> / <monitor state>
  metric query <metric query>; runbook <runbook>
  named code path <named code path>; fix lane <fix lane name>
  comms lane <comms lane name>; bus <bus id>, topic <incident topic>
  record: investigation <investigation id>, log <incident log id>
    (cc-notes, on the drive checkout)

If you were launched unsupervised, report through the bus; Orca carries no worker_done for you.

Do:
  1. Send the first finding within about 10 minutes, with evidence and gaps.
  2. Date onset on an independent counter; do not infer it from alert time alone.
  3. Read the named metric and runbook; separate affected and healthy targets.
     Correlate onset with the drive's landings, deploys, and applies.
  4. Send findings as they land to <fix lane name> on the bus topic
     <incident topic>, with query, time window, and evidence pointers. Once the
     mechanism is clear, record it:
     `incident.py note --incident <incident id> --mechanism "<mechanism, one line>"`.
  5. If the alert is a monitor defect, send the evidence to redirect the fix lane
     to a monitor fix. A muted monitor is not resolved.
  6. Record every artifact in cc-notes, never under ~/.claude/scratch:
     `ccn log append <incident log id> --entry "<kind: one line>" --attach <file, repeatable>`.
     A directory goes as one .tgz; `--replace` updates a same-named attachment.
     Investigation detail goes to
     `ccn investigation append <investigation id> "<evidence, one line>"`.
     Mechanism, PR, live, and not-ours still go through `incident.py note`; the
     executor records the verdicts. Never run `ccn sync`; the root syncs.

Escalate: with no mechanism 15 minutes after launch, the executor launches an
  Opus 5.5 backup fix lane in parallel; keep working. Never fable or astra.

Do NOT touch: code, monitor configuration, production state, deploys, or applies.
Worktree: <absolute path, read-only>.
Finish: send <fix lane name> the diagnosis, onset, evidence pointers, and remaining
  gaps on the bus. Use the bus when unsupervised, otherwise the preamble's Orca send
  and worker_done commands. You gate nothing.
```
