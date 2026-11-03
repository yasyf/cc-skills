# Active production alert lanes

## Contents

- [Open and run the incident executor](#open-and-run-the-incident-executor)
- [Launch incident lanes directly](#launch-incident-lanes-directly)
- [Scope before and during incident mode](#scope-before-and-during-incident-mode)
- [Incident mode on PRs and lanes](#incident-mode-on-prs-and-lanes)
- [Incident clocks and PR-SILENT](#incident-clocks-and-pr-silent)
- [Case lanes for unfixed cases](#case-lanes-for-unfixed-cases)
- [Take over another drive's stalled fix](#take-over-another-drives-stalled-fix)
- [What the executor guarantees](#what-the-executor-guarantees)
- [Incident comms](#incident-comms)
- [Incident doc lane and brief](#incident-doc-lane-and-brief)
- [Fix lane brief](#fix-lane-brief)
- [Evidence lane brief](#evidence-lane-brief)

## Open and run the incident executor

**An active production alert is P0 from the moment it is seen.** An active alert or outage on any surface counts. That includes a Datadog monitor, a Sentry alert, an `#outage` report, an on-call page, or a broken CI or review pipeline. It also includes an alert or breakage link from the owner, an `incident` record from the `oncall` drive that carries ai-oncall's page feed, or a runner `PAGE` record relayed from an on-call channel during a drive release.

A record that says customers are failing, or fail from a change already moving, counts too. The stall watch opens it to the root in the pass that reads it, never as a card (`reference/watchers.md`), and no finding waits on a card. An `oncall` or `PAGE` record works like the owner's alert link. The root opens the incident, the executor launches the fix lane, and the comms lane replies in the page's thread with no owner round-trip and no wait on ai-oncall.

**Open the incident and start `incident.py run` in the same turn, before anything else (SKILL.md R16).** One durable owner, `scripts/incident.py`, runs intake, comms events, fix and evidence launches, the landing check, activation, the canary, failed-work recovery, and the final reply. It reaches the root only with an unresolved decision. Its state lives in the `scripts/actions.py` store under `~/.claude/long-running/incidents/` as one file per incident, so a restarted executor resumes where the last one stopped and never repeats a side effect.

```sh
incident.py open --kind pr-review --incident <id> --thread <permalink> --onset <ISO time> \
  --reported <ISO time of the first report> \
  --bus <cci drive> --comms-lane <comms lane name> --root-lane <root agent name> \
  --checkout <repo checkout> --orca-run <run id> --orca-repo <repo id> \
  --common "$(ccn -R <repo checkout> attachment path <briefs log> common.md)" \
  --alert <Sentry issue or monitor link> --runbook <runbook> [--adopt fix=<lane already running>] \
  --grant thread=<grant id> [--grant channel=<grant id>] --grant sync=<authority ref> --grant rebuild=<authority ref> \
  [--expect-config <text the live configuration carries>]
incident.py run --incident <id>
```

Start `incident.py run` as a background command. It exits only when the final reply posts or another owner takes the incident.

| Flag or verb | Behavior |
|---|---|
| `--kind pr-review` | Broken review pipeline; carries activation, canary, and re-kick. |
| `--kind alert --target <service or stack>` | Any other alert; no activation step; goes live when the fix or evidence lane runs `incident.py note --incident <id> --live "<evidence>"`. |
| `incident.py note --incident <id> --not-ours "<evidence>"` | Closes either kind without a fix. |
| `--adopt fix=<lane>` / `--adopt evidence=<lane>` | Adopts a lane already launched by the desk instead of starting a second worker; that lane's brief must carry the `incident.py note` commands. |
| `incident.py status --incident <id>` | Prints milestones and open decisions in Pacific time. |
| `incident.py grant` | Supplies a missing grant so the next executor pass proceeds. |
| `incident.py brief --incident <id> [--role fix]` | Writes `incident-<id>-fix.full.md` and `incident-<id>-evidence.full.md` beside the incident record and prints their paths. |

At intake, `incident.py` opens an investigation and an `incident <id>` log in cc-notes on the drive checkout, both labeled `incident` and `incident:<id>`. Milestones and lane-brief attachments go to the log; evidence and verdicts go to the investigation. It records the mechanism with `root-cause`, landing with `fix --commit`, and not-ours with `exonerate`. Read the record with `incident.py status --incident <id>`, `ccn investigation show <id>`, and `ccn log show <id>`. A refused write asks the root once and never blocks a launch.

**Never put anything between the alert and `incident.py run`.** Never wait for a "real-or-not" or "ours-or-not" verdict, a mechanism-depth mandate, or an `AskUserQuestion`, and never treat a mute as resolution. Diagnosis redirects the fix lane; it never precedes it. An owner's "if it is real, fix it" means launching the executor's fix and evidence lanes, not a verdict gate. The fix lane's route is the executor's on every surface, including CI or tooling breakages. The pack's `owner_facing` hook blocks the root's `Agent` spawn when the name starts with `incident-` or `outage-`, or the brief's first line says incident or outage. It allows the spawn when the name carries a support role such as `evidence`, `export`, `ship`, `comms`, `intake`, `retro`, `watch`, or `handoff`, or is the Opus 5.5 `-backup` lane.

**Ratify desk launches and start support lanes in the intake turn.** A monitor transition from `monitor-watch.py --alert-drive` posts `orca-desk: alert <slug> <link> :: <what fired>` to `orca-desk` on cci, which the runner records without launching. The alerts desk posts `orca-desk: incident <slug> <link> reported=<HH:MM> :: <what fired>` only when it judges a lane necessary (`reference/alerts-desk-brief.md`), where `reported` is the first report's Pacific time (`reference/orca-desk-brief.md`). For an `incident` line, the runner fills `reference/alert-fix-brief.md` with drive facts from `alert.facts` and attaches it to the briefs log as `<slug>-fix.full.md`. It launches `<slug>-fix` on `incident` `xhigh` at once, and relays a repeat to the live lane. When the runner's `INCIDENT` line arrives, the root ratifies it in that turn:

1. Adopt the launched fix lane into `incident.py` with `--adopt fix=<lane>`.
2. Start or adopt the evidence lane, and spawn an incident-doc lane beside it as `long-running:lane` on opus named `incident-<id>-retro`.
3. Fence the broken target from further deploys and applies outside the fix lane. Claim, freeze, and hold nothing (`tools/ci release claim`, freeze, or hold) unless the owner asks for that exact one; other releases keep flowing while the fix lands. Never freeze a target because a PR waits on review, a person's or a bot's, and never freeze everyone over a dependency your own change created; a change that must ship with another target waits in its own stack.
4. Start the comms lane (`#incident-comms`). Post first in the affected account channels before the mechanism is known under cc-notes answer `5ad4507`, and open in `#outage` for a platform-wide incident under `52f4863`.

**Fix every incident forward.** No lane chooses a rollback or a revert. A rollback or revert runs only when the owner's own word names that act after diagnosis has found the issue. The never-cancel-a-running-apply rule holds.

**Supply grants and answer executor asks.** Each `--grant` gives the owner's authority for one kind of side effect. `sync` and `rebuild` cite the owner's words or the standing rule authorizing pipeline sync and re-kick. The root records Slack grants for the affected account channels and threads, plus `#outage` for a platform-wide incident, under cc-notes answers `5ad4507` and `52f4863`.

It records each grant with `ccn answer add "<question>" --body "<owner's words>" --label slack-grant:<channel id>`. The Slack permission hook reads that label as a standing grant for top-level posts and replies in that channel and mints each write's ticket, so the comms lane passes no ticket or grant flag; see `reference/slack-lane-brief.md`. The root supplies grants for those channels and threads at launch, and each grant covers only its named surface. Consecutive replies under one-thread grant need no human message between them.

A missing grant pauses only the step that needs it. After intake, the root answers the executor's bus asks about a missing grant, a silent comms lane, an overdue action, a failed canary, or a read-back that still drifts. The executor posts the target fence on the bus and reports `opened`, `pr`, `landed`, `activated`, `live`, `recovered`, and `closed` to the root lane. The root relays `opened`, `pr`, `landed`, `activated`, `live`, `recovered`, and `closed` to the owner as one line each in Pacific time and plain words (SKILL.md R21). The root never relays owner words to a lane as authority, never re-briefs the executor's lanes, and never posts or composes incident copy.

## Launch incident lanes directly

**The executor and the root launch incident lanes with `desk-launch.py incident`.** Both fix and evidence lanes run as Orca codex workers on `gpt-6.1-sol`, fast tier, `xhigh`. Only the `incident` alias runs fast, while plain `sol` is the standard tier and never an incident route. The executor refuses Claude for the fix and evidence roles and never uses fable or astra.

```sh
desk-launch.py incident <name> <fix-brief> [<evidence-brief>] [--alert '<link, times PT, what fails, owner words>']
```

With `--alert`, `desk-launch.py` fills each brief template itself. The script drops the template's `Root:` lines, writes the lane's name for every `<lane>` and the text for the `Alert:` line, and saves `<state dir>/<lane>/brief.md`, so the root never edits a template by hand. Any brief still holding `<lane>`, `<state>`, `<...>`, or a placeholder on its `Alert:` line is refused. `desk-launch.py` takes the next `R<n>` at one past every key on cci, and posts `<name>-fix` and `<name>-evidence` to `orca-desk` on cci as `NOW incident xhigh` launch lines with `role=fix` and `role=evidence` so the runner's incident clock holds only the fix lane.

After posting, `desk-launch.py` waits for `LAUNCHED` or `LAUNCH-FAILED`, prints each lane's dispatch and terminal, and exits 0 only when both launched. A codex lane writes its PR title, body, and commit message with `pr-prose.py`, a headless Opus 5.5 writer named by `orca-launch.sh` (`reference/orca-workers.md`). The root answers a lane's question with `cci post --kind answer --to <lane> --re <seq>` on the lane's ask or the runner's `DECIDE`, which the runner relays to the open Orca question (`reference/orca-desk-brief.md`). The root withdraws a launch or lane with `desk-launch.py withdraw <R<n> | unfixed:<msg>:<n> | lane>`.

**Check runner liveness and fall back inline when the runner is down.** Before launching, the root reads the orca runner's newest alive record:

```sh
cci tail --drive <drive> --lane desk-runner --kind state --topic orca -n 1
```

A record under five minutes old means the runner takes the line, since its timer thread posts every minute. When no `orca alive` record is under five minutes old, `desk-launch.py` and the `desk_liveness` hook report `LAUNCH-FAILED` and refuse in one line naming `<name>-fix`. In that case, or when `incident.py run` is refused, the root renders the briefs with `incident.py brief --incident <id>`. It then launches the fix lane inline in the same turn as a background `codex:codex-wrapper` running `codex-ask --incident -l incident-<id>-<role> <brief>`. Never keep both; if the runner comes back and launches the same line, the root keeps the lane further along and stops the other.

**Escalate to Opus 5.5 fast mode after 15 minutes without a mechanism or PR.** If 15 minutes pass without a mechanism or a PR, the executor launches an Opus 5.5 backup (`claude-opus-5-5`) in Claude fast mode on the same brief. It passes `--settings '{"fastMode":true}'` through `ORCA_LAUNCH_CLAUDE_ARGS` and keeps the first lane running. When the owner asks for a smarter lane on the incident, that Opus 5.5 fast lane is the escalation, launched at once and named in the root's reply; never use fable, astra, or an unnamed model. A fix lane gets one Opus fallback; the executor launches none while the desk clock's `<lane>-opus` is launching or live, and the runner launches none while the executor's backup is launching or live.

## Scope before and during incident mode

**Post the scope check at spawn and at every check-in.** Incident mode buys speed with authority through fast-tier lanes, releases without per-step owner approval, and break-glass applies. The root spends that authority only on a failure that is still happening on a path the plan keeps. The check never gates the spawn; the executor and its lanes start in the alert's turn, and every fix brief asks the fix lane for the same two answers as step zero. In the spawn turn and at every check-in after it, including each lane check, each milestone the executor relays, and each owner prompt while open, the root posts the `SCOPE` record.

```sh
cci post --lane root --drive <drive> --kind incident --topic <slug> --to <fix lane>,<evidence lane> \
  --text "INCIDENT <slug> SCOPE: active <yes|no: customer runs failing now, from telemetry>; plan keeps path <yes|no: the plan step that keeps or deletes it>"
```

Active means customer runs are failing now in telemetry since the last check-in. An error earlier today, a fix still in flight, or a replay that still fails is not active. Release gates are always active. A failing Forge release smoke team, a red dev build, and a failing release check are `active yes` always, with the release that preceded the failure as the first suspect. "No customer impact" is recorded as scope, naming who is hit so far, and never downgrades the incident.

**Own our failures and leave Akamai's.** A failure on our own build, such as a sand-next release, is ours by default and gets a fix lane at once. An alert whose cause is Akamai, such as a tenant-edge 403, gets a look and no fix work: the fix lane records it with `incident.py note --not-ours` and stops. Whether a recent PR caused a failure is evidence for the mechanism, never a stop. Customer harm now and a path the plan keeps are the only gates.

Push back on false-alarm or mute closes while unfixed. A mute, a false-alarm verdict, or a monitor change that stops counting failing teams, proposed by ai-oncall or anyone while the mechanism is unfixed, is never a close. The root posts pushback where it was proposed, keeps the incident open, and names the fix owner that turn. The desk runner refuses a fix lane's stop that scopes a release gate out (`REFUSED-STOP`, launching its Opus fallback at once) and flags every proposed mute (`MUTE-PUSHBACK`).

Mute a proven false page as a window, never a close. When the fix lane's mechanism proves a page false because every team the monitor names is completing runs and the defect is in the signal, the lane mutes it the minute it is proven. It posts `MUTE <lane>: monitors <ids> until fix-live: <proof>` and requests the mute from ai-oncall through `ci oncall connect` and `ci oncall say --kind request`, never a prose Slack post. The incident stays open until `FIX-LIVE`. A release smoke team, a release check, or dev red is never false. The plan keeps the path when no step of the drive's plan deletes or replaces it before its next milestone. A path deleted at cutover is `no`, however far off cutover is.

**Close incident mode on either `no`.** A `no` to either question ends incident mode that turn. The root posts `INCIDENT <slug> CLOSED: <the answer that is no, and why>`, stands incident lanes down with `desk-launch.py withdraw <lane>`, cancels any incident release not yet started, and stops any replay or pass box the incident started. Fixes already live stay live. Remaining work becomes an ordinary PR through the landing desk or a card for the owner's pick. The root tells the owner in one line which incident closed, which answer was no, and what happens to the open work.

A fix that grows into a redesign of a path the plan deletes is a signal to stop, not to add a lane. Re-ask both questions before launching a second release on the same path, a fix touching code shared with the new path, or a proper-fix card for a deleted path. When two owner rules pull apart, such as "shipped code failing is an incident" and "this path is deleted at cutover," the root pushes back, and tells the owner in one line which rule it applies and why.

## Incident mode on PRs and lanes

**An open `incident` record puts its named PRs and lanes in incident mode.** An open `incident` record can name a PR by its refs or `#<n>` in its text, or name a lane as poster or recipient. Doing so drops the drive's process gates for that PR and lane:

- The stall watch raises no tap, board, or carry stall for the PR (`reference/watchers.md`).
- Running `drive.py` reads a drive named by `--drive` with no Claude session id, so a codex lane passes identity checks (`reference/watchers.md`).
- Every incident fix brief orders the lane to build, ship by using submit-pr, and post `ready`. Codex cannot run commit mechanics, so `codex-wrapper` hands its branch to a Claude `lane-ship` lane named `incident-<slug>` that ships it; the pack's `lane_landing` hook warns on `stack-enqueue` from that lane and names the plain squash.
- Incident fixes land by plain squash (`gh pr merge <n> --squash`, never through the merge queue) in the pass they are approved and green (`reference/landing.md`). Green means only the checks required by branch rules; a red non-required check is read for its content and never waited on.
- The plan check still holds. The finder and fidelity desks still grade the PR, and `rulings.py check-pr` refuses as it does for any PR (`reference/owner-rulings.md`). The remaining gates are an approving review, green required checks, a plain squash, and zero deletes and zero replaces on apply.
- The record that resolves the incident ends incident mode.

## Incident clocks and PR-SILENT

**Every incident fix lane aims to be fix-live within 20 minutes of launch.** The root treats a lane past 20 minutes as a miss to act on in that turn. The orca desk runner keeps two clocks on incident fix lanes: the milestone clock (mechanism, opened PR, `ready`) and the `PR-SILENT` clock from mechanism to PR. Their thresholds, the lanes they cover, what stops them, and the `<lane>-opus` fallback are in [orca-desk-brief.md](orca-desk-brief.md#handle-alerts-incident-clocks-and-case-lanes). The root's part:

- **On `INCIDENT-CLOCK <lane> <mechanism|pr|ready>`**, the root reports the line to the owner in one plain line of its next reply, ahead of any other work in that turn and never as a Slack post, and ratifies the fallback the line names. Both lanes keep running, and the first PR wins.
- **On `INCIDENT-CLOCK <lane> in-process`**, which the runner posts after the Orca launch of `<lane>-opus` failed twice, the root starts `<lane>-opus` that turn in its own session as an `Agent` of type `long-running:lane-ship` on opus in fast mode with the brief the line names, and never retries Orca a third time.
- **A root `decide` steers a clocked lane and never stops its clock**, so a handoff record cannot switch off the backup. A root `hold` naming the lane, or a `stand-down` or `stopped` to it, does stop it.
- **On `PR-SILENT <lane> #<seq>`**, the root acts on the code state the line carries, below.

**Act on `PR-SILENT` using `drive.py lane-status <lane>`.** The `PR-SILENT` line embeds the output of:

```bash
drive.py lane-status <lane>
```

`drive.py lane-status <lane>` finds every worktree the lane owns from launch receipts, `WORKTREE` records posted by the `announce_lane_worktree` hook, `git worktree list`, and PR head branches. Each line names the branch, dirty files, commits ahead of trunk, the last commit's age, whether the origin branch holds HEAD, and the PR with its CI state. A lane with no worktree on this Mac, such as an Orca worker on a paired remote server, reads from the drive's Orca Run instead, or `--run <run>` or a `ctx_` dispatch id. It prints one line per dispatch whose task title, display name, or spec names the lane, with its worktree, environment, branch, stage, outcome, liveness, last update and heartbeat, terminal, and last message to the Run. A lane that is an in-process teammate of the drive's root session also gets a line naming its agent type, model, whether it runs or sits idle, its last activity, and when it joined. With none, it prints `no worktree found for lane <lane>` and exits 1.

`desk-runner.py show` lists each waiting lane as `pr-silent <lane>: ...` with the same command. The root reads the code state in the `PR-SILENT` line instead of checking by hand; it orders the lane to push and open the PR, or starts `<lane>-opus` when the line shows no code. A root `--resolves` on the line, or `stand-down` or `stopped` to the lane, ends the repeats. On the lane side, the `incident_lane_ships_its_mechanism` gate refuses an in-process incident lane's stop while its newest mechanism has no `opened` or `blocker` after it and `drive.py lane-status` shows no pushed branch. It prints that status and the commit, push, and submit commands to run, and passes a lane that has neither a worktree nor a name that owes a PR.

## Case lanes for unfixed cases

**Reproduce the reporter's exact case before reporting ready.** The fix lane must reproduce the exact thread, run, request, or monitor query the report named. It shows the case failing before the fix, passing on the build carrying the fix, and passing again on the released build before `fix-live`. A fix proven only on a synthetic or neighboring case is not ready, and the root never tells a reporter "fixed" on one.

**Every unfixed case becomes an incident case lane, never backlog.** The fix lane closes its `ready:` and `fix-live:` statuses with one `UNFIXED <lane>: <case>` line per known case the fix does not cover, or `UNFIXED <lane>: none`. Only those partial-fix reports launch case lanes; a `worker_done`, `NOT-OURS`, or `STOPPED` launches none. For each case up to `incident.case_lanes` (default 2), the runner fills `reference/unfixed-case-brief.md` and launches `<first>-case<n>` on incident xhigh in the same pass (`reference/orca-desk-brief.md`). It logs the launch as `INCIDENT <lane> …: its fix does not cover: <case> | case lane <first>-case<n> launching …`, and a case launches once whether the fix lane or its `-opus` fallback reports it.

A case lane echoing its own case launches nothing. A `not-ours` or `stand-down` from the fix lane or its fallback, or the root's `stand-down` or `stopped` to either, ends the incident. A report that opens with `NOT-OURS`, `stood down`, or `Stopped` launches no case lane. The runner launches no fix lane for an incident the root resolved with a `done`, stopped by name, or another drive's root answered for, and posts `INCIDENT-OWNED` as a `state` record instead.

A later report launches nothing and reaches the root as `UNFIXED <lane> …: <cases> | no case lane launched: …`, and a case launch not yet started fails; a report from a lane the root withdrew does the same. Excess cases, second-generation cases from a case lane, and cases from a report whose mechanism says `UNKNOWN` reach the root as a `DECIDE` carrying a ready `orca-desk: launch <first>-case<n> NOW incident xhigh brief=<path>` line per case; the root starts a lane by posting its line. The root ratifies each launched case lane, keeps the reporter told that the case is still open, and never parks a named case while the break is live.

## Take over another drive's stalled fix

**Watch another drive's incident fix and take it over the moment it stalls.** Another drive owns an incident's fix when the runner posts an `INCIDENT-OWNED` state record or another drive's lane opens a fix PR. In that turn, the root spawns `incident-<slug>-watch` as `long-running:lane` on sonnet, effort low. It runs one foreground watch on the owning drive:

```sh
cci watch --drive <owning drive> --lane <its fix lane> --lane desk-runner --kind stopped --kind blocker --kind decide
```

The trigger is the fix lane's `stopped` or `blocker` at once, or a `decide` from that drive's runner naming the fix lane once five minutes pass with no `answer` from that drive's root. On the trigger, the watch lane posts the `TAKEOVER` blocker and finishes:

```sh
cci post --drive <drive> --lane incident-<slug>-watch --kind blocker --to root --pr <n> --text "TAKEOVER #<n>: <owning drive> <kind> #<seq> <first line>"
```

**Spawn `incident-<slug>-ship` the turn `TAKEOVER` arrives.** The root spawns `incident-<slug>-ship` as `long-running:lane-ship` on opus in fast mode. The ship lane checks out the PR's branch with `ccx vcs worktree add <branch>` and replays it onto current dev with `ccx vcs stack submit --restack`. It posts `opened --pr <n>` on this drive so the landing runner owns the PR, and posts `ready` when approved and green so the landing runner merges it by plain squash.

On landing, the ship lane posts the `release` record required by D20 (`reference/landing.md`). The root posts one `decision` addressed to the owning drive's root and fix lane naming the takeover and its trigger. The root never sets the owning drive a deadline, never asks whether it is mid-push, and never waits for its root to rule. If the owning drive pushes a newer head later, the ship lane continues from that head and never force-pushes over it.

## What the executor guarantees

- Activation read-back before closing. A merge never closes a `pr-review` incident. After landing, the incident stays `activation_pending` until `ci sync <pipeline>`, run from a worktree at the landed commit, reports that the stored configuration matches and contains `--expect-config` text if given.
- Canary before bulk rebuilds. No rebuild fires on the old configuration. The first rebuild is the canary, and remaining rebuilds wait until its `Prepare PR evidence` job passes.
- Idempotent failed-work recovery. Affected work is re-derived on every pass from every build that failed or was canceled between onset and activation, grouped by PR. The latest failed build at an open PR's current head is rebuilt once; closed PRs and moved heads are accounted as skipped; a head that fails after the first pass is still owned.
- Action-store deduplication. Every side effect is recorded in `scripts/actions.py` as `started` before it runs. A lost response leaves the action `unverifiable` until a read of external state settles it through the rebuild's `rebuilt_from`, the comms bus post, the launch receipt, or the sync dry run. An action is retried only after that read proves the effect absent, up to three attempts total. A lost launch with no receipt goes to the root so a retry cannot start a second worker.
- Strict completion gates. A worker's completion settles its assignment, not the incident. `recovered` waits for every build in the outage window to finish, the activation receipt, and accounting for every rebuild across re-run, green, red with PR links, and skipped states. `closed` requires the posted `ts` of the final reply.
- Generation-checked ownership transfer. Reassignment runs `actions.py transfer --expect-generation <n> --to <owner>`, followed by the new owner's `actions.py ack`. The former owner's next write fails on the generation and its runner exits; a runner started as the former owner refuses to start. Nothing stops or signals a session.

## Incident comms

Slack grant mechanics, `gemini-4-argon` copy generation, and `cc-slack` posting rules belong to `reference/slack-lane-brief.md`. For an incident comms lane:

- Post proactively in affected account channels before the mechanism is known. Under cc-notes answer `5ad4507`, acknowledge the page and say investigation has started with no per-post owner ask. Resolve the channel and forward-deployed engineers through the ai-oncall skill mapping (`d12f767`); on a miss, search Slack channels by customer name, report which path found them, and never guess a forward-deployed engineer mention.
- Follow up at mechanism and fix-live. Use plain words, Pacific times, and @-mention the account's forward-deployed engineers. Every account-channel post, including the first acknowledgement, states what we are doing to prevent recurrence (`52f4863`) without claiming an unverified fix.
- Open platform-wide incidents in `#outage`. When an incident affects more than one customer or a core service, post in `#outage` with impact, timeline, status, and prevention (`52f4863`), and update at mechanism, fix-live, and resolution. Add the live incident-doc link in `#outage` and the account channels as soon as the doc lane hands it over; never hold the first acknowledgement for the doc link.
- Consume executor comms events from cci. `incident.py` posts each event, whether `ack`, `pr`, `review-request`, `landed`, `live`, or `recovered`, as a cci `ask` from `incident-<id>` carrying JSON with `event`, `grant`, `surface`, `thread`, and Pacific-time facts. The comms lane reads asks with `cci tail`.

```sh
cci tail --drive <drive> --reader <comms lane> --kind ask --since 0 --json
```

  After posting by using `cc-slack reply` or `cc-slack send`, the comms lane answers the ask with `cci post`.

```sh
cci post --drive <drive> --lane <comms lane> --kind answer --re <seq> --text "posted ts=<ts>"
```

  An event left unanswered for two minutes becomes a decision for the root.

## Incident doc lane and brief

**Maintain a live retro doc throughout the incident.** The `incident-<id>-retro` lane runs the incident-retro skill's live mode in the `Forge-AI/design-docs` checkout, running `live init`, merge of the shell PR, and `live sync` at every state change and at least every 10 minutes. The page at `https://docs.poetic.design/incident-retros/<date>-<slug>/` polls `live/<date>-<slug>`. Each retro scaffold names the drive's records as sources, including the cc-notes investigation and log ids, the owner answers and notes it turned on, and one `--incident "cci:<tenant>|<customer>|<lane regex>"` query. All incident records are written through `ccn`, never as loose Markdown under `~/.claude/scratch`. At resolution, the lane runs `live finalize`, completes Remediation and the owner's prevention picks on `retro/<date>-<slug>`, runs `retro.py publish <dir>` through merge and the rendered-URL check, and hands comms the URL from the final `RENDERED:` line instead of a PR URL. Every draft carrying that link must pass `retro.py comms-check <draft-file|-> --url <rendered-url>` before posting.

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
     writing prose. The first retro PR includes these records.
  6. Run `retro.py prose <dir>`, which writes every prose field through
     gemini-4-argon, then `retro.py publish <dir>`. It runs the
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

## Fix lane brief

```text
ccx: role=fix
You are <fix lane name>, fixing the active production alert.
Model gpt-6.1-sol, effort xhigh, service tier fast; Orca codex worker.
Owner: the incident executor <root agent name> for incident <incident id>.
Authority: implement the fix and open its PR through <submit skill>.
  While the alert is active, release or apply the fix to production at once,
  through the release skill or <break-glass skill>, never after a dry run, a
  `--plan` or a preview: the apply's own approve hold or prompt is the only gate,
  and 0 deletes and 0 replaces apply without asking.
  Log the apply in <runbook>, and append one line to the production-operations
  log in the same turn, for this and every other state-changing production
  command you run:
  `ccn log append "$(ccn log list --label prod-ops | cut -f1)" --entry "who | when, UTC and Pacific | cluster, team | exact command | authority | effect"`
  Any delete or replace stops for the owner, unless he ordered this release.
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
  comms lane <comms lane name>; cci drive <bus id>, topic <incident topic>
  record: investigation <investigation id>, log <incident log id>
    (cc-notes, on the drive checkout)

Standing orders, fixed in this template:
  - Reported <report time> PT. The bar is fixed and live within 20 minutes of the
    report, by <due time> PT; the desk's limits below run from your launch, inside it.
  - "stop asking people for things you can collect yourself": never ask a reporter
    or anyone else for evidence; read telemetry and Sentry yourself.
  - Verification: no local test runs, builds, typechecks or lint; CI grades; a red CI step is read from its log, fixed, and pushed.
  - Any repair pass or backfill runs on in-region EC2, never on
    this Mac.
  - A tooling failure gets a same-turn tooling lane plus the hand path; it never
    stops the fix.

Design rulings on this subsystem, verbatim; the fix calls their entry point:
  <design rulings>
  entry point: <entry point>

If you were launched unsupervised, report through the bus; Orca carries no worker_done for you.

Do:
  0. SCOPE FIRST. Your first record, before the mechanism, answers two
     questions, kind `incident`: `SCOPE <fix lane name>: active <yes|no: customer runs
     failing now, from telemetry>; plan keeps path <yes|no: the step of the
     drive's plan in cc-notes that keeps or deletes this path past its next
     milestone>`. A failing Forge release smoke team, a red dev build, and a
     failing release check are `active yes` always: they are the release gate
     and the earliest canary, and a customer run in the same window fails the
     same way, with the release that preceded it as the first suspect. "No
     customer impact" goes on the SCOPE line as who is hit so far, never as a
     no. An on-call bot's false alarm, a mute, or a monitor change that stops
     counting the failing teams is neither the mechanism nor the fix: post it
     to root as `PUSHBACK <fix lane name>: <who proposed what, and the unfixed mechanism>`
     and keep fixing. A page your mechanism proves false is muted the minute it is proven, as
     the window until your fix is live, never the close: post
     `MUTE <fix lane name>: monitors <ids> until fix-live: <the proof, one line>`, kind
     `incident`, to root, then ask ai-oncall with its verb, never a prose Slack post:
     `ci oncall connect <page id> --state <your worktree>/oncall.json --label <fix lane name>`
     under a persistent Monitor (`ci aioncall open-incidents` names the page id), and
     `ci oncall say <page id> --state <your worktree>/oncall.json --kind request --text "MUTE <ids> until <RFC3339, fix-live estimate plus an hour, at most 24 hours>: <the proof>. The fix is ours; page nobody on these monitors."`.
     The incident stays open until `FIX-LIVE`. A release smoke team, a release check, or
     dev red is never false.
     No credential is read from any store, file, SSM parameter or environment unless the
     owner's words in this brief name it; a mute, an ack or a monitor change goes through
     ai-oncall's verb or an ask in the page thread, never a key you fetched.
     A no to either ends incident mode: no release, no break-glass
     apply, no new lane. Post `STOPPED <fix lane name>: out of scope, <the answer that
     is no, and why>` and leave any candidate as an ordinary PR. A fix that
     grows into a redesign of a path the plan deletes is the signal to stop,
     never a reason to widen the fix.
     A failure on our own build is ours by default. An alert whose cause is
     Akamai, such as a tenant-edge 403, gets a look and no fix work: record it with
     `incident.py note --incident <incident id> --not-ours "<evidence, one line>"`
     and stop. Whether a recent PR caused the failure is evidence for the
     mechanism, never a stop: customer harm now and a path the plan keeps are
     the only gates.
     NO ROLLBACK; find the mechanism and fix forward; the fix ships as a new release
     through the walker. The fence holds only other releases of the target, never
     the fix.
     No brief, lane, or exception grants a rollback or a revert. One runs only when
     the owner's own word names that act, after your diagnosis has found the issue;
     never offer one before that.
  1. Start at the named code path now, while evidence is still arriving.
     Never wait for a diagnosis verdict before starting the fix. Your first
     deliverable, before any fix, is the whole failing path: every stage from the
     user's action to the error that can fail, one line each with the telemetry
     that shows whether it failed, attached with `--path` to your `MECHANISM`
     record. A fix to one stage of a path you have not mapped is not the fix. Read the
     reporter's case from telemetry: the client versions of their team
     from the metrics, the run, session, or thread record and which component
     served the failing request, server logs and Sentry by user and team id,
     and the release timeline. Never ask the reporter for evidence.
  2. Read <evidence lane name>'s findings as they arrive by bus or Orca messages.
     Redirect with the evidence, including to a monitor fix for a monitor defect.
     A muted monitor still gets fixed; use the mute window.
  3. Before you name a mechanism, read the last 24 hours of the
     production-operations log:
     `ccn log show "$(ccn log list --label prod-ops | cut -f1)"`
     An entry on the alert's target inside the onset window is the first mechanism
     to test; quote it with its authorizing answer.
     The moment you know the mechanism, record it for the executor:
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
  6. Before you call the fix ready, reproduce the reporter's own case, the exact
     thread, run, request, or monitor query the report named. Show it failing
     before the fix and passing on the build that carries the fix. A fix proven
     only on a synthetic or neighbouring case is not ready, and no release ships
     before that case passes at the candidate head.
  7. When the PR is green and that case passes, say so on cci:
     `cci post --drive <bus id> --lane <fix lane name> --kind ready --pr <PR number> --text "READY <fix lane name>: <PR number, head, the case failing before and passing after>"`.
     Then send a status Run message with subject `ready: <PR number>`. Its body
     lists every known case the fix does not cover, one line each, exactly
     `UNFIXED <fix lane name>: <the case, one line>`, or
     `UNFIXED <fix lane name>: none`. The orca desk launches an incident lane
     for each case at once, so no case waits as backlog.
  8. For a fix that needs a production apply, plan through <break-glass skill>,
     apply under the authority above, log the apply in <runbook> and on the
     production-operations log, and verify it against the alert's metric. Then
     record the evidence:
     `incident.py note --incident <incident id> --live "<evidence, one line>"`.
     If the evidence shows the alert is not ours, record that instead:
     `incident.py note --incident <incident id> --not-ours "<evidence, one line>"`.
  9. Run <exact live-check command> every two minutes and post its output as a
     status Run message until it shows <expected live output>. Run the
     reporter's own case again on the released build. Once both pass, send a status
     Run message with subject `fix-live: <h:mm PM PT> <evidence>`. The brief names
     that exact command; the root uses it too. For a static site, for example:
     `curl -fsS https://platform.poetic.com/ | grep -oE '/releases/[0-9a-f]{40}/' | head -1`.
  10. Record every artifact in cc-notes, never under ~/.claude/scratch:
     `ccn log append <incident log id> --entry "<kind: one line>" --attach <file, repeatable>`.
     A directory goes as one .tgz; `--replace` updates a same-named attachment.
     Investigation detail goes to
     `ccn investigation append <investigation id> "<evidence, one line>"`.
     Mechanism, PR, live, and not-ours still go through `incident.py note`; the
     executor records the verdicts. Never run `ccn sync`; the root syncs.

Escalate: with no mechanism 15 minutes after launch, the executor launches an
  Opus 5.5 backup lane in fast mode on this brief in parallel; keep working.
  Never fable or astra.
  When the orca desk launched you, its clock also runs from your launch: a
  mechanism within 10 minutes, an opened PR within 15, ready within 20. It
  reads your `mechanism:`, `ready:`, and `fix-live:` status messages and the cci records
  posted as <fix lane name>: `mechanism`, the PR's `opened` record, which
  `ccx vcs ship` posts in a Claude session and `drive.py record --opened` in a
  Codex one, `ready`, `fix-live`, `not-ours`, and `stopped`. Past
  10 or 15 minutes it launches <fix lane name>-opus in parallel, and the first
  PR wins. You get one Opus fallback, from whichever of the two fires first.
  Each missed limit sends a blocker to the root, who tells the owner.

Do NOT touch: unrelated targets, files, branches, or another lane's worktree.
Worktree: <absolute path, exclusive to this lane>.
Finish: when the PR is noted and any production apply is verified, report to
  <root agent name> with the PR, head, apply counts, runbook entry, and the
  `UNFIXED` lines again. Use the bus when unsupervised, otherwise the
  preamble's Orca worker_done command. Your
  completion settles this assignment; the executor owns the incident until its
  final reply. Never report live from a mute.
```

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
  comms lane <comms lane name>; cci drive <bus id>, topic <incident topic>
  record: investigation <investigation id>, log <incident log id>
    (cc-notes, on the drive checkout)

If you were launched unsupervised, report through the bus; Orca carries no worker_done for you.

Do:
  1. Send the first finding within about 10 minutes, with evidence and gaps.
  2. Date onset on an independent counter; do not infer it from alert time alone.
  3. Read the named metric and runbook; separate affected and healthy targets.
     Correlate onset with the drive's landings, deploys, and applies, and with
     the last 24 hours of the production-operations log, before you name a
     mechanism:
     `ccn log show "$(ccn log list --label prod-ops | cut -f1)"`
     Quote an entry on the alert's target with its authorizing answer.
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
