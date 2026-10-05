# Active production alert lanes

An active alert has one durable owner, the incident executor in
`scripts/incident.py`. The root opens the incident and starts the executor. From
then on the executor runs intake, the comms events, the incident fix and evidence
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
  --bus <cci drive> --comms-lane <comms lane name> --root-lane <root agent name> \
  --checkout <repo checkout> --orca-run <run id> --orca-repo <repo id> \
  --common "$(ccn -R <repo checkout> attachment path <briefs log> common.md)" \
  --alert <Sentry issue or monitor link> --runbook <runbook> [--adopt fix=<lane already running>] \
  --grant thread=<grant id> [--grant channel=<grant id>] --grant sync=<authority ref> --grant rebuild=<authority ref> \
  [--expect-config <text the live configuration carries>]
incident.py run --incident <id>
```

Start `run` as a background command. It exits only when the final reply posts or
another owner takes the incident.

In the intake turn, ratify any desk launch from its `INCIDENT` line and start
the support lanes:

1. Adopt the fix lane with `--adopt fix=<lane>` when the desk already launched it.
2. Start or adopt the evidence lane. Beside it, spawn an incident-doc lane as
   `long-running:lane` on opus, named `incident-<id>-retro`, using the brief below.
3. Fence the target from further deploys and applies outside the fix lane.
4. Start the comms lane under the contract below. Its first post goes to each
   affected account channel before the mechanism is known. A platform-wide
   incident also gets a `#outage` post. The incident-doc link follows when ready;
   it never holds the first acknowledgement.

The incident-doc lane runs the incident-retro skill's live mode in the
Forge-AI/design-docs checkout. It runs `live init`, merges the shell PR, then
runs `live sync` at every state change and at least every 10 minutes. It hands
the page link to comms for `#outage` and the account channels.

At resolution, it runs `live finalize`, completes Remediation and the owner's prevention picks
on `retro/<date>-<slug>`, and runs `retro.py publish <dir>` through merge and
the rendered-URL check. It hands comms the URL from the final `RENDERED:` line.
The incident's step 4 report and the Slack retro-link reply use that URL,
never a PR URL. Every such draft passes
`retro.py comms-check <draft-file|-> --url <rendered-url>` before posting.

- Each grant is the owner's authority for one kind of side effect. The root
  records Slack grants for the affected account channels and threads, plus
  `#outage` for a platform-wide incident, under cc-notes answers `5ad4507` and
  `52f4863`. These standing rulings need no per-post owner ask. The comms lane
  passes each id as `--grant <id>`. `thread` comes from `cc-slack grant --url <permalink>
  --quote "<owner's words>"` and covers replies in that thread. `channel` comes
  from `cc-slack grant --channel <channel id> --quote "<owner's words>"` and covers
  top-level posts in that one channel, never a reply, broadcast, or edit. `sync`
  and `rebuild` cite the owner's words or the standing rule that authorizes the
  pipeline sync and the re-kick. A missing grant pauses only the step that needs it.
  The executor asks the root once, and `incident.py grant` lets the next pass
  proceed.
- The executor launches the fix and evidence lanes with
  `scripts/orca-launch.sh <lane> incident xhigh <brief>`. Both run on
  `gpt-6.1-sol` with the fast tier at `xhigh`; only the `incident` alias runs
  fast, and plain `sol` is the standard tier. If 15 minutes pass without a
  mechanism or a PR, it launches an Opus 5.5 backup in Claude fast mode on the
  same brief, passing `--settings '{"fastMode":true}'` through
  `ORCA_LAUNCH_CLAUDE_ARGS`. It refuses any other model for the fix or evidence
  role and never uses fable or astra.
- It posts the fence for the target on the bus, and it reports `opened`, `pr`,
  `landed`, `activated`, `live`, `recovered`, and `closed` to the root lane as
  milestones. The root relays those to the owner as one line each, in Pacific
  time.
- After intake, the root owns the decisions the executor asks for on the
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
Its first step on a page or alert affecting a customer team is a proactive post
in that account's channel: acknowledge the page and say investigation has started.
Do this before the mechanism is known, with no per-post owner ask, under cc-notes
answer `5ad4507`. Resolve the channel and FDEs through the ai-oncall skill mapping
(`d12f767`). On a miss, search Slack channels by account name and report which
path found the channel and FDEs; never guess an FDE mention.

Post account updates at mechanism and fix-live. Use plain words, Pacific times,
and @-mention the account's FDEs. Every account-channel post states what we are
doing to prevent recurrence (`52f4863`), including the first acknowledgement.
State the prevention work underway without claiming an unverified fix.

After the account posts, open a platform-wide incident in `#outage` when it
affects more than one customer or a core service. Include impact, timeline,
status, and prevention. Update at mechanism, fix-live, and resolution. Add the
live incident-doc link as soon as the doc lane hands it over, in `#outage` and
the account channels.
The root supplies grants for those channels and threads at launch; each grant
still covers only its named surface.

*Prevents a customer page waiting for diagnosis or another owner approval before
any account update. cc-notes answers `5ad4507`, `d12f767`, and `52f4863`,
2026-10-03.*

`incident.py` posts through `bus.py` onto the same cci drive, opened with
`incident.py open --bus <cci drive>`. The comms lane reads asks directly:

```sh
cci tail --drive <drive> --reader <comms lane> --kind ask --since 0 --json
```

The executor posts each event as a cci `ask` from
`incident-<id>`. Each entry carries JSON with `event` (`ack`, `pr`,
`review-request`, `landed`, `live`, or `recovered`), the `grant` id, the `surface`,
the `thread`, and the event's facts, with times already in Pacific. The lane writes
the copy through an Opus writer subagent (`Agent` with `model: opus`) and posts it with
`cc-slack reply --url <thread> --grant <grant> --text <copy>`. For the `channel`
surface it posts with `cc-slack send --channel <channel id> --grant <grant> --text
<copy>`. Consecutive replies under one thread grant need no human message between
them. It then answers the entry:

```sh
cci post --drive <drive> --lane <comms lane> --kind answer --re <seq> --text "posted ts=<ts>"
```

An event left unanswered for two minutes becomes one decision for the root.

## Incident doc lane brief

```text
ccx: role=retro
You are incident-<incident id>-retro, maintaining the live incident doc.
Agent long-running:lane; model opus.
Owner: <root agent name>, for incident <incident id>.
Authority: maintain the incident doc through the incident-retro skill's live
  mode in the Forge-AI/design-docs checkout. You gate no fix or comms post.

Verified facts, do not re-derive:
  alert <alert link>; onset <onset>; target <target>
  fix lane <fix lane name>; evidence lane <evidence lane name>
  comms lane <comms lane name>; cci drive <drive>, topic <incident topic>
  incident inputs: <incident-dir>/state.json and <incident-dir>/slack-log.jsonl
  docs checkout: <design-docs-checkout>; slug: <date>-<slug>

Do:
  1. Load the incident-retro skill. Keep its incident inputs current from the
     fix, evidence, and comms lanes. Follow its codename and publishing checks.
  2. Run `retro.py live init <incident-dir> --docs <design-docs-checkout>`.
     Open and merge the shell PR with the retro and both index cards before
     handing out the live page. Init makes no commit or PR itself.
  3. Run `retro.py live sync <incident-dir> --docs <design-docs-checkout>` at
     every state change and at least every 10 minutes. The page at
     https://docs.poetic.design/incident-retros/<date>-<slug>/ polls the
     live/<date>-<slug> branch. Verify the shell is served, then hand the link
     to <comms lane name> for #outage and the affected account channels.
  4. Keep impact, timeline, status, mechanism, mitigation, and prevention current.
     Record unknowns as unknowns. Send the doc link and each state change on
     <incident topic>; the comms lane owns Slack posts.
  5. At resolution, sync the final state, stop your sync loop, then run
     `retro.py live finalize <incident-dir> --docs <design-docs-checkout> --tags <two to six topical tags, comma-separated>`.
     Complete the retro on retro/<date>-<slug>, at the same page URL. Run
     `retro.py board <dir> --out <board.json>` and present it to the owner.
     Record the picks with picked, owner, and PR links or a named lane. Fill
     Remediation with what stops the incident and the follow-up lanes before
     the prose pass. The first retro PR includes these records.
  6. Run the skill's prose pass, then `retro.py publish <dir>`. It runs the
     gates, opens a ready PR, enables squash auto-merge, and waits for merge
     and the rendered-URL check. On exit 75, resume the printed AWAIT: command.
     Hand only the URL from the final RENDERED: line to <root agent name> and
     <comms lane name>. The incident's step 4 report and Slack retro-link reply
     take that URL from the skill output, never a PR URL. Every such draft
     passes `retro.py comms-check <draft-file|-> --url <rendered-url>` before posting.

Do NOT touch: production, fix or evidence worktrees, or Slack posts.
Worktree: <design-docs-checkout>, exclusive to this lane.
Finish: after resolution, final sync, merge, and handoff of the checked RENDERED: URL.
```

*Prevents an incident running without a lane keeping its live doc current. The
owner's relayed 5:4x PM ruling on 2026-10-03 set that duty. The
[live doc for run stalls and database read failures](https://docs.poetic.design/incident-retros/2026-10-03-run-stalls-and-database-read-failures/)
followed shell PR Forge-AI/design-docs#54, merged at 5:48 PM Pacific; its link went
under `#outage`, and the incident resolved at 5:59 PM.*

## Fix lane brief

```text
ccx: role=fix
You are <fix lane name>, fixing the active production alert.
Model gpt-6.1-sol, effort xhigh, service tier fast; Orca codex worker.
Owner: the incident executor <root agent name> for incident <incident id>.
Authority: implement the fix and open its PR through <submit skill>.
  While the alert is active, apply the fix to production through
  <break-glass skill> without asking when the plan shows 0 deletes and 0 replaces.
  Log the apply in <runbook>. Any delete or replace stops for the owner.
  A break-glass profile refused with ForbiddenException on GetRoleCredentials
  holds no live grant. Grant yourself, never wait for the owner, then retry:
  `env -u AWS_PROFILE tools/ci break-glass --reason "INCIDENT <incident id>: WHY" --stack DOMAIN/ENV`
  The incident section of <break-glass skill> has the wait and verify steps.
  A CI pipeline whose stored settings a `ci sync` writes is activated by the
  executor after landing; never run that sync yourself.

Verified facts, do not re-derive:
  alert <alert link>; monitor <monitor id> / <monitor state>
  metric query <metric query>; runbook <runbook>
  named code path <named code path>; evidence lane <evidence lane name>
  comms lane <comms lane name>; cci drive <drive>, topic <incident topic>
  record: investigation <investigation id>, log <incident log id>
    (cc-notes, on the drive checkout)

Design rulings on this subsystem, verbatim; the fix calls their entry point:
  <design rulings>
  entry point: <entry point>

If you were launched unsupervised, report through the bus; Orca carries no worker_done for you.

Do:
  0. Rollback first. If a release, apply, or deploy in the 2 hours before onset
     touched the alert's target, first roll it back to that target's previous
     applied commit under the apply authority above, with 0 deletes and 0 replaces.
     Diagnose after the rollback is live. A forward fix is never the first move
     while a rollback is available. A rollback plan with a delete or replace
     stops for the owner.
  1. Start at the named code path now, while evidence is still arriving.
     Never wait for a diagnosis verdict before starting the fix.
  2. Read <evidence lane name>'s findings as they arrive by bus or Orca messages.
     Redirect with the evidence, including to a monitor fix for a monitor defect.
     A muted monitor still gets fixed; use the mute window.
  3. The moment you know the mechanism, record it for the executor:
     `incident.py note --incident <incident id> --mechanism "<mechanism, one line>"`.
     Also send a status Run message with subject `mechanism: <evidence>`.
  4. Design check, when the brief quotes design rulings. Before the PR opens,
     record the entry point your change calls and how it meets each ruling:
     `incident.py note --incident <incident id> --design-check "<symbol at file:line>; <each ruling, met how>; leaves out: <none, or each piece>"`.
     Open the PR once `incident.py status --incident <incident id>` shows the
     design confirmed; a redirect arrives on the bus. A durable fix ships the
     agreed design whole. A first PR that leaves out part of a ruling (its
     footers, its judge, its entry point) changes what the code means, so it is
     a hold, never a "static first" split; mitigate through the apply instead.
  5. Open the PR through <submit skill>, then record it:
     `incident.py note --incident <incident id> --pr <PR number>`.
     The executor reports it, asks for human review when the broken surface is
     the reviewer, watches the landing, activates, and re-runs the failed work.
  6. For a fix that needs a production apply, plan through <break-glass skill>,
     apply under the authority above, log the apply in <runbook>, and verify it
     against the alert's metric. Then record the evidence:
     `incident.py note --incident <incident id> --live "<evidence, one line>"`.
     If the evidence shows the alert is not ours, record that instead:
     `incident.py note --incident <incident id> --not-ours "<evidence, one line>"`.
  7. Run <exact live-check command> every two minutes and post its output as a
     status Run message until it shows <expected live output>. Then send a status
     Run message with subject `fix-live: <h:mm PM PT> <evidence>`. The brief names
     that exact command; the root uses it too. For a static site, for example:
     `curl -fsS https://platform.poetic.com/ | grep -oE '/releases/[0-9a-f]{40}/' | head -1`.
  8. Record every artifact in cc-notes, never under ~/.claude/scratch:
     `ccn log append <incident log id> --entry "<kind: one line>" --attach <file, repeatable>`.
     A directory goes as one .tgz; `--replace` updates a same-named attachment.
     Investigation detail goes to
     `ccn investigation append <investigation id> "<evidence, one line>"`.
     Mechanism, PR, live, and not-ours still go through `incident.py note`; the
     executor records the verdicts. Never run `ccn sync`; the root syncs.

Escalate: with no mechanism 15 minutes after launch, the executor launches an
  Opus 5.5 backup lane in fast mode on this brief in parallel; keep working.
  Never fable or astra.

Do NOT touch: unrelated targets, files, branches, or another lane's worktree.
Worktree: <absolute path, exclusive to this lane>.
Finish: when the PR is noted and any production apply is verified, report to
  <root agent name> with the PR, head, apply counts, and runbook entry. Use the bus
  when unsupervised, otherwise the preamble's Orca worker_done command. Your
  completion settles this assignment; the executor owns the incident until its
  final reply. Never report live from a mute.
```

*Prevents release-v3's starvation fix proposing a forward api apply with 9 deletes
after executor release #292 at 3:52 PM Pacific on 2026-10-03. Its first brief
steered away from a revert; the root had to order the executor rollback at 4:44 PM.*

## Evidence lane brief

```text
ccx: role=evidence
You are <evidence lane name>, reading telemetry, logs, and the deploy timeline for the active production alert.
Model gpt-6.1-sol, effort xhigh, service tier fast; Orca codex worker.
Owner: the incident executor <root agent name> for incident <incident id>.
Authority: read-only evidence. Feed <fix lane name> and the executor.
  You gate nothing. Never ask the fix lane to wait for diagnosis.

Verified facts, do not re-derive:
  alert <alert link>; monitor <monitor id> / <monitor state>
  metric query <metric query>; runbook <runbook>
  named code path <named code path>; fix lane <fix lane name>
  comms lane <comms lane name>; cci drive <drive>, topic <incident topic>
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
  Opus 5.5 backup fix lane in fast mode in parallel; keep working. Never fable or astra.

Do NOT touch: code, monitor configuration, production state, deploys, or applies.
Worktree: <absolute path, read-only>.
Finish: send <fix lane name> the diagnosis, onset, evidence pointers, and remaining
  gaps on the bus. Use the bus when unsupervised, otherwise the preamble's Orca send
  and worker_done commands. You gate nothing.
```
