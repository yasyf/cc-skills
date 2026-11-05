# Active production alert lanes

## Contents

- [Open the incident](#open-the-incident)
- [One actor per incident](#one-actor-per-incident)
- [Launch incident lanes directly](#launch-incident-lanes-directly)
- [Scope before and during incident mode](#scope-before-and-during-incident-mode)
- [Incident mode on PRs and lanes](#incident-mode-on-prs-and-lanes)
- [Incident clocks](#incident-clocks)
- [Case lanes for unfixed cases](#case-lanes-for-unfixed-cases)
- [Take over another drive's stalled fix](#take-over-another-drives-stalled-fix)
- [Incident comms](#incident-comms)
- [Incident doc lane and brief](#incident-doc-lane-and-brief)
- [Fix lane brief](#fix-lane-brief)
- [Evidence lane brief](#evidence-lane-brief)

## Read by job

| You are | Read |
| --- | --- |
| The coordinator | Everything above the fenced briefs: Open the incident, One actor, Launch, Scope, PRs and lanes, Clocks, Case lanes, Take over, Comms. |
| The fix lane | Only the brief the coordinator or the runner pasted: [Fix lane brief](#fix-lane-brief), or `reference/alert-fix-brief.md` when the runner launched it. |
| The evidence lane | Only [Evidence lane brief](#evidence-lane-brief). |
| The comms lane | [Incident comms](#incident-comms), and `reference/slack-lane-brief.md` for posting. |
| The incident-doc lane | [Incident doc lane and brief](#incident-doc-lane-and-brief). |

## Open the incident

**An active production alert is P0 from the moment it is seen (SKILL.md R16).** Open the incident and launch its fix lane in the same turn, before anything else.

### What counts as an alert

- An active alert or outage on any surface: a Datadog monitor, a Sentry alert, an `#outage` report, an on-call page, a broken CI or review pipeline.
- An alert or breakage link from the owner.
- An `incident` record from the `oncall` drive, which carries ai-oncall's page feed.
- A runner `PAGE` record relayed from an on-call channel during a drive release.
- A record saying customers are failing, or fail from a change already moving. The stall watch opens it to the coordinator in the pass that reads it, never as a card (`reference/watchers.md`); no finding waits on a card.

An `oncall` or `PAGE` record works like the owner's alert link: the coordinator opens the incident and launches the fix lane with no owner round-trip.

### Who launches

- An active alert gets its fix lane the same turn, without a coordinator: the supervisor launches the fix, evidence and comms lanes for an alert nobody answered in three minutes.
- A red dev build launches the same way: the landing pass posts its `orca-desk: incident dev-red-<commit>` line beside `DEV-RED` or `POST-MERGE-RED`, unless a record on topic `dev-red` already names the fix PR.
- When an alert reaches the coordinator first (a link the communicator relayed, an `#outage` report, a page, a runner `PAGE`, a failing release check, an `ALERT-UNOPENED` blocker), the coordinator runs `lr incident run <slug> --fix-brief <file>` before anything else.

### The incident and its records

An incident is its records on the drive's cci bus:

- The coordinator's `INCIDENT OPEN <slug>: <what fired>`.
- The fix lane's `SCOPE`, `MECHANISM`, `FIX-LIVE` or `NOT-OURS` records. `SCOPE`, `MECHANISM` and `FIX-LIVE` end with the alert's Slack message permalink and its Sentry or Datadog issue URL, as bare URLs, whichever exist; the owner's incident line reads them from there.
- The coordinator's `INCIDENT <slug> CLOSED` once a `FIX-LIVE` or `NOT-OURS` record cites the evidence.

`lr incident run <slug> --fix-brief <file>` launches the lanes. `lr incident status <slug>` prints what fired, the live lane on it, its newest scope, mechanism and fix-live records with their seqs, and what closes it. `lr incident list` prints every incident since midnight Pacific, unowned first, from the same reader as `lr update`.

### Never put anything between the alert and `lr incident run`

- No "real-or-not" or "ours-or-not" verdict, no mechanism-depth mandate, no `AskUserQuestion`, and no mute treated as resolution.
- Diagnosis redirects the fix lane; it never precedes it. The owner's "if it is real, fix it" means launching the fix and evidence lanes, not a verdict gate.
- The fix lane's route is `incident` on every surface, including CI or tooling breakages.
- While a drive incident is open, the pack's `owner_facing` hooks block plan mode, an off-incident `AskUserQuestion`, and a root-run probe.
- The pack's `owner_facing` hook blocks the coordinator's `Agent` spawn when the name starts with `incident-` or `outage-`, or the brief's first line says incident or outage. It allows a name carrying a support role (`evidence`, `export`, `ship`, `comms`, `intake`, `retro`, `watch`, `handoff`) or the Opus 5.5 `-backup` lane.
- Fix every incident forward (the no-rollback rule is in step 0 of the [Fix lane brief](#fix-lane-brief)). The never-cancel-a-running-apply rule holds.
- Only the owner's explicit order to mute an alert runs `lr alert mute <monitor> --why '<reason>'`, which mutes it, stands down its lanes, and keeps every launch off it.
- A standing wait or hold order of the owner's never holds an open incident's fix: the fix lane ships and merges it at once and the owner hears in one line.

### Intake: read the shared message, never the sharer

A report that arrives as a Slack share belongs to its original author. Read it with `slack_thread`, and take the reporter, team, machine, screenshot, and `reported=` time from the message under `shares`, so the fix and evidence lanes read that person's logs. The sharer may only be passing it along: never file it as theirs or ask them what they saw.

### Ratify desk launches and start support lanes in the intake turn

A monitor transition from `monitor-watch.py --alert-drive` posts `orca-desk: alert <slug> <link> :: <what fired>` to `orca-desk` on cci; the runner records it without launching. The alerts desk posts `orca-desk: incident <slug> <link> reported=<HH:MM> :: <what fired>` only when it judges a lane necessary (`reference/alerts-desk-brief.md`); `reported` is the first report's Pacific time (`reference/orca-desk-brief.md`). When the alert has a second link, such as the Slack message beside a Sentry issue, `<what fired>` carries it, so the record and every brief hold both.

For an `incident` line the runner fills `reference/alert-fix-brief.md` with drive facts from `alert.facts`, attaches it to the briefs log as `<slug>-fix.full.md`, launches `<slug>-fix` on `incident` `xhigh` at once, and relays a repeat to the live lane. When the runner's `INCIDENT` line arrives, the coordinator ratifies it in that turn:

1. Adopt the evidence lane the launch started, and spawn an incident-doc lane beside it as `long-running:lane` on opus named `incident-<id>-retro`.
2. Claim, fence, freeze, and hold nothing (`tools/ci release claim`, a fence, a freeze, or a hold) unless the owner asks for that exact one; other people's releases keep flowing while the fix lands. Never freeze a target because a PR waits on review, a person's or a bot's, and never freeze everyone over a dependency your own change created; a change that must ship with another target waits in its own stack.
3. Adopt the comms lane the launch started; the coordinator writes nothing to Slack. It posts where [Incident comms](#incident-comms) says, before the mechanism is known.

### Record the Slack grants

The coordinator records Slack grants for the affected account channels and threads, plus `#outage` for a platform-wide incident, one per channel:

`ccn answer add "<question>" --body "<owner's words>" --label slack-grant:<channel id>`

- The Slack permission hook reads that label as a standing grant for top-level posts and replies in that channel and mints each write's ticket, so the comms lane passes no ticket or grant flag (`reference/slack-lane-brief.md`).
- The coordinator supplies the grants at launch. Each grant covers only its named surface. Consecutive replies under a one-thread grant need no human message between them.

### The coordinator's part afterward

- Post the fix lane's `opened`, `ready`, `fix-live`, and close to `owner-relay` as one `report` each.
- Never relay owner words to a lane as authority, never re-brief the incident's lanes, never post or compose incident copy.
- After a fix PR merges, one named owner, the incident executor or the coordinator, carries every following step as one chain: merge, stored-config sync activation, canary check, and rebuilds. No step is left without an owner.

## One actor per incident

Each incident has one actor. When `tools/ci oncall incidents` shows an open ai-oncall incident covering the alert, ai-oncall's commander is that actor, and this applies to another drive's sessions too.

- The fix lane's rules and commands (`ci oncall connect`, `ci oncall say --kind finding|request`, `ROUTED`, when `ci oncall takeover` may run, what to do when ai-oncall seems wrong) are in the "One actor per incident" paragraph of step 0 in the [Fix lane brief](#fix-lane-brief). The same rules bind every lane that touches the incident.
- A fix lane takes an incident over only when `lr owner away <incident id>` prints away, and a hook refuses every other `ci oncall takeover`. The verb decides, never a lane's reading: nothing from him for 30 minutes across his ledger and owner records, no named person holding the incident, and `ci oncall connect` saying no commander runs. A failed read means present. The takeover records in the owner's name, and his standing yes (answer 7641eea) covers that in this case. On page eu7ayk two lanes took over on a refused connect alone while he was at work, which is why a refused or waiting connect is never enough.
- His explicit word wins either way. The communicator records it with `lr owner word away|here --row <mN> [--incident <id>]`: an `away` with no incident ends at his next message, and one naming an incident orders that takeover.
- The comms lane posts nothing in the page's thread beside the commander.
- Pushback on a mute or false-alarm close proposed by ai-oncall: see [Scope before and during incident mode](#scope-before-and-during-incident-mode).

## Launch incident lanes directly

**The supervisor and the coordinator launch incident lanes with `lr launch incident`.**

- The fix and evidence lanes run as Orca codex workers on `gpt-6.1-sol`, fast tier, `xhigh`.
- The comms lane runs as Claude on `incident-opus`, because a codex lane cannot post to Slack: no hook runs there to mint the ticket every `cc-slack` write spends. The fix lane still owns the incident and what is said, so an incident has two agents only for this reason.
- Only the `incident` alias runs fast; plain `sol` is the standard tier and never an incident route.
- Claude never takes the fix or evidence role. Fable and astra never run.

```sh
lr launch incident <name> <fix-brief> [<evidence-brief> [<comms-brief>]] [--alert '<link, what fails, owner words>']
```

### What `lr launch` does

- With `--alert`, it fills each brief template itself: it drops the template's `Root:` lines, writes the lane's name for every `<lane>` and the text for the `Alert:` line, and saves `<state dir>/<lane>/brief.md`. The coordinator never edits a template by hand. A brief still holding `<lane>`, `<state>`, `<...>`, or a placeholder on its `Alert:` line is refused.
- It posts `<name>-fix`, `<name>-evidence` and `<name>-comms` to `orca-desk` on cci as `NOW` launch lines at `xhigh`, on `incident` with `role=fix` and `role=evidence` and on `incident-opus` with `role=comms`, so the runner's incident clock holds only the fix lane. Each launch's key is `R` and the number cci gives its record.
- The comms lane posts under its grant with no draft round trip. Exposure nobody has reported never goes out from it: each finding reaches the owner as one card with the facts, and he picks who tells whom.
- It then waits for `LAUNCHED` or `LAUNCH-FAILED` and prints each lane's dispatch and terminal. It exits 0 when both launched, or when a lane has no verdict yet while the relay's `orca alive` record is fresh, since that verdict arrives as a record. It exits 2 only when the relay's `orca alive` record is over five minutes old.

### Answer, withdraw, and write

- A lane writes its own PR title, body, and commit message.
- Answer a lane's question with `cci post --kind answer --to <lane> --re <seq>` on the lane's ask or the runner's `DECIDE`; the runner relays it to the open Orca question (`reference/orca-desk-brief.md`).
- Withdraw a launch or lane with `lr launch withdraw <R<n> | unfixed:<msg>:<n> | lane>`.

### Name the monitor a hand-opened fix lane holds

A slug such as `journal-open-plat` does not tell the desk or the supervisor which monitor a lane is on, so they launch a duplicate `dd-<id>-fix` when that monitor alerts. Put `monitor=<id>` on the brief's `ccx:` line when the alert line already shows it. When the monitor is learned after launch, post one record:

`cci post --kind incident --lane <your lane> --lane-ref <fix lane> --attr monitor=<id> --to root --text 'monitor <id> is <slug>'`

Either way, while that lane's dispatch is live, an incident line for the monitor logs `LAUNCH-SKIPPED` and the supervisor launches nothing.

### Check runner liveness; fall back inline when the runner is down

Before launching, read the orca runner's newest alive record:

```sh
cci tail --drive <drive> --lane desk-runner --kind state --topic orca -n 1
```

- A record under five minutes old means the runner takes the line (its timer thread posts every minute).
- When none is under five minutes old, `lr launch` and the `desk_liveness` hook report `LAUNCH-FAILED` and refuse in one line naming `<name>-fix`. The coordinator then fills `reference/alert-fix-brief.md` and launches the fix lane inline, in the same turn, as a background `codex:codex-wrapper` running `codex-ask --incident -l incident-<id>-<role> <brief>`.
- Never keep both: if the runner comes back and launches the same line, keep the lane further along and stop the other.

### When `lr incident run` is refused

When the incident run verb is refused, for example on machine load, the coordinator still renders the fix and evidence briefs from the fixed templates with the pack's brief-rendering verb and launches them. A brief written by hand without the fixed standing orders is forbidden.

### Escalate to Opus 5.5 fast mode

- The orca desk's incident clock launches the Opus 5.5 backup `<lane>-opus` on the same brief as `incident-opus xhigh` and keeps the first lane running. Its limits are in the Escalate paragraph of the [Fix lane brief](#fix-lane-brief).
- `incident-opus` is the one `lr launch start` alias that starts `claude-opus-5-5` in Claude fast mode.
- When the owner asks for a smarter lane on the incident, that `incident-opus` lane is the escalation, launched at once and named in the coordinator's reply. Never use fable, astra, or an unnamed model.
- A fix lane gets one Opus fallback.

**The incident turn outside `lr incident run`.** A coordinator that launches the fix lane by hand posts the full cci record form on one line; the runner refuses a shorter line and logs `LAUNCH-FAILED`:

```
cci post --drive <drive> --lane root --kind go --to orca-desk --text 'R<n> (<h:mm AM|PM>) orca-desk: launch <name>-fix NOW incident xhigh role=fix brief=<absolute path>'
```

Outside a drive, the fix lane is an inline background `codex:codex-wrapper` running `codex-ask --incident`, with Claude Opus 5.5 in fast mode on a miss, and never fable or astra. Check each incident lane every 10 minutes; with no mechanism within 15 minutes of spawn, start a second lane on Opus fast in parallel. The fix lane's brief carries apply authority: a PR through the submit skill, merged with `gh pr merge <PR> --squash` the moment it is approved and green, skipping the merge queue (never `stack-enqueue` or `merge-fast`), and break-glass or hand apply pre-authorized at 0 delete and 0 replace with counts reported and runbook-logged. "Reproduce before fixing" and "Observe, don't infer" govern the fix lane, not whether it exists, and no owner round-trip comes before the spawn. An order to unfreeze everything lifts every freeze, listed first in the ack; a request for another lane, such as a retro, is a new named lane, never folded into one already running. A healthy release means the affected teams' customer runs succeed, never only zero OOMKills and CDP errors.

## Scope before and during incident mode

**Post the scope check at spawn and at every check-in.** Incident mode buys speed with authority: fast-tier lanes, releases without per-step owner approval, break-glass applies. The coordinator spends that authority only on a failure that is still happening on a path the plan keeps. The check never gates the spawn: the incident's lanes start in the alert's turn, and every fix brief asks the fix lane for the same two answers as step zero. In the spawn turn and at every check-in after it (each lane check, each milestone a lane posts, each owner prompt while open) the coordinator posts the `SCOPE` record:

```sh
cci post --lane root --drive <drive> --kind incident --topic <slug> --to <fix lane>,<evidence lane> \
  --text "INCIDENT <slug> SCOPE: active <yes|no: customer runs failing now, from telemetry>; plan keeps path <yes|no: the plan step that keeps or deletes it> <alert links>"
```

### The two answers

The definitions of release gates (always `active yes`) and of "no customer impact" are in step 0 of the [Fix lane brief](#fix-lane-brief); the coordinator's `SCOPE` uses the same ones.

- **Active** means customer runs are failing now in telemetry since the last check-in. An error earlier today, a fix still in flight, or a replay that still fails is not active.
- **The plan keeps the path** when no step of the drive's plan deletes or replaces it before its next milestone. A path deleted at cutover is `no`, however far off cutover is.

### Push back on false-alarm or mute closes while unfixed

- A mute, a false-alarm verdict, or a monitor change that stops counting failing teams, proposed by ai-oncall or anyone while the mechanism is unfixed, is never a close.
- The coordinator posts pushback where it was proposed, keeps the incident open, and names the fix owner that turn. A proposal from ai-oncall gets its pushback as a `ci oncall say --kind finding` to its commander and in one record to the coordinator.
- The relay refuses a fix lane's stop that scopes a release gate out (`REFUSED-STOP`, launching its Opus fallback at once). It flags every proposed mute on a record whose typed fields tie to an open incident or a monitor (`MUTE-PUSHBACK`): its kind, a `monitor` or `incident` attr, a monitor URL, or a `re` chain reaching one, never its words alone.
- A page the fix lane's mechanism proves false is muted as a window, never a close (step 0 of the [Fix lane brief](#fix-lane-brief)).

### Close incident mode on either `no`

A `no` to either question ends incident mode that turn. The coordinator:

- posts `INCIDENT <slug> CLOSED: <the answer that is no, and why>`;
- stands incident lanes down with `lr launch withdraw <lane>`, cancels any incident release not yet started, and stops any replay or pass box the incident started;
- reports to `owner-relay` which incident closed, which answer was no, and what happens to the open work.

Fixes already live stay live. Remaining work becomes an ordinary PR through the landing desk or a card for the owner's pick.

### Which tree a fix touches

The fix lane's first answer line says which tree the cause and the fix touch (step 0 of the [Fix lane brief](#fix-lane-brief)). When the owner asks whether a break is the drive's own doing, answer yes or no and name the bug first:

- The drive broke the old app: fix it now and run a retro.
- The bug was already there: apply the stopgap and add a regression test for the new tree.

### Redesigns and repeat outages

A fix that grows into a redesign of a path the plan deletes is a signal to stop, not to add a lane. Re-ask both questions before launching a second release on the same path, a fix touching code shared with the new path, or a proper-fix card for a deleted path. When two owner rules pull apart, such as "shipped code failing is an incident" and "this path is deleted at cutover," the coordinator pushes back and reports to `owner-relay` which rule it applies and why.

When a second outage hits the same area, stop fixing one at a time: halt, re-enter planning, and find the fundamental fix before shipping more. The fix already carrying the live alert still ships first.

## Incident mode on PRs and lanes

**An open `incident` record puts its named PRs and lanes in incident mode.** The record can name a PR by its refs or `#<n>` in its text, or name a lane as poster or recipient. That drops the drive's process gates for that PR and lane:

- The stall watch raises no tap, board, or carry stall for the PR (`reference/watchers.md`).
- `drive.py` run with `--drive` and no Claude session id reads the drive, so a codex lane passes identity checks (`reference/watchers.md`).
- Every incident fix brief orders the lane to build, ship by using submit-pr, and post `ready`. Codex cannot run commit mechanics, so `codex-wrapper` hands its branch to a Claude `lane-ship` lane named `incident-<slug>` that ships it; the pack's `lane_landing` hook warns on `stack-enqueue` from that lane and names the plain squash.
- Incident fixes land by plain squash (`gh pr merge <n> --squash`, never through the merge queue) in the pass they are approved and green (`reference/landing.md`). Green means only the checks required by branch rules; a red non-required check is read for its content and never waited on.
- The plan check still holds. The finder and fidelity desks still grade the PR, and `rulings.py check-pr --incident` refuses only uncited rulings, a reserved change with no answer, and removed tests (`reference/owner-rulings.md`).
- A reviewer-bot finding on an incident fix is accepted with `.agents/skills/submit-pr/scripts/pr-reviews accept <slug> --why "incident: <why the fix is still right>"`, never fixed by a push that restarts CI. The exception is a finding that says the fix itself is wrong: that push ends with `# fix-is-wrong: <slug>`. The remaining gates are an approving review, green required checks, a plain squash, and zero deletes and zero replaces on apply.
- Incident fixes ship by regular release, not hotfix; the fix lane still merges directly.
- Never stack a follow-up on an incident fix that awaits its merge: a child in the native stack refuses the fix's plain merge and cannot reopen. Open the follow-up on the default branch after the merge.
- A fix for a live user-facing break opens as a PR with the code change alone and no tests. The lane squashes it the moment CI is green and the PR is approved, then ships the regression and CI tests as a follow-up PR from the same lane right after the fix is live.
- Incident mode never buys the owner's identity: the rule in [orca-lane-brief.md](orca-lane-brief.md#who-spawns-asks-and-reads) holds for every incident lane, and a coordinator asking for a scratch proof names a test identity or a no-write probe.
- The record that resolves the incident ends incident mode.

## Incident clocks

**Every incident fix lane aims to be fix-live within 20 minutes of launch.** The coordinator treats a lane past 20 minutes as a miss to act on in that turn. The relay keeps a milestone clock on incident fix lanes: mechanism, opened PR, `ready`. Its thresholds, the lanes it covers, what stops it, and the `<lane>-opus` fallback are in [orca-desk-brief.md](orca-desk-brief.md#handle-alerts-incident-clocks-and-case-lanes). The coordinator's part:

- **On `INCIDENT-CLOCK <lane> <mechanism|pr|ready>`**, post the line as a `report` to `owner-relay`, ahead of any other work in that turn, and ratify the fallback the line names. Both lanes keep running; the first PR wins.
- **On `INCIDENT-CLOCK <lane> in-process`**, which the runner posts after the Orca launch of `<lane>-opus` failed twice, start `<lane>-opus` that turn in your own session as an `Agent` of type `long-running:lane-ship` on opus in fast mode, with the brief the line names. Never retry Orca a third time.
- **A coordinator `decide` steers a clocked lane and never stops its clock**, so a handoff record cannot switch off the backup. A coordinator `hold` naming the lane, or a `stand-down` or `stopped` to it, does stop it.
- **A lane cannot post a mechanism and go silent.** `mechanism` is not a turn end, so `cci hook stop` refuses the lane's stop until it posts `opened` or `blocker`.

### Read a lane's code state

`drive.py lane-status <lane>` finds every worktree the lane owns from launch receipts, `WORKTREE` records posted by the `announce_lane_worktree` hook, `git worktree list`, and PR head branches. `--held` keeps only the worktrees the lane made, which is what a successor's live-process check reads.

- Each line names the branch, dirty files, commits ahead of trunk, the last commit's age, whether the origin branch holds HEAD, and the PR with its CI state.
- A lane with no worktree on this Mac, such as an Orca worker on a paired remote server, reads from the drive's Orca Run instead, or `--run <run>` or a `ctx_` dispatch id. It prints one line per dispatch whose task title, display name, or spec names the lane: worktree, environment, branch, stage, outcome, liveness, last update and heartbeat, terminal, and last message to the Run.
- A lane that is an in-process teammate of the coordinator's session also gets a line with its agent type, model, whether it runs or sits idle, its last activity, and when it joined.
- With none, it prints `no worktree found for lane <lane>` and exits 1.

## Case lanes for unfixed cases

**The coordinator never tells a reporter "fixed" on a synthetic or neighboring case.** The fix lane reproduces the reporter's exact case before `ready` and again on the released build before `fix-live` (steps 6 and 9 of the [Fix lane brief](#fix-lane-brief)).

**Every unfixed case becomes an incident case lane, never backlog.**

- The fix lane's `UNFIXED <lane>: <case>` lines on its `ready:` and `fix-live:` statuses (format in the [Fix lane brief](#fix-lane-brief), step 7) are the only reports that launch case lanes; a `worker_done`, `NOT-OURS`, or `STOPPED` launches none.
- For each case, up to `incident.case_lanes` (default 2), the runner fills `reference/unfixed-case-brief.md` and launches `<first>-case<n>` on incident xhigh in the same pass (`reference/orca-desk-brief.md`). It logs `INCIDENT <lane> …: its fix does not cover: <case> | case lane <first>-case<n> launching …`. A case launches once, whether the fix lane or its `-opus` fallback reports it.

### What launches nothing

- A case lane echoing its own case.
- A `not-ours` or `stand-down` from the fix lane or its fallback, or the coordinator's `stand-down` or `stopped` to either: this ends the incident. A report that opens with `NOT-OURS`, `stood down`, or `Stopped` launches no case lane.
- An incident the coordinator resolved with a `done`, stopped by name, or another drive's coordinator answered for: the runner launches no fix lane and posts `INCIDENT-OWNED` as a `state` record instead.
- A later report: it reaches the coordinator as `UNFIXED <lane> …: <cases> | no case lane launched: …`, and a case launch not yet started fails. A report from a lane the coordinator withdrew does the same.

### What reaches the coordinator as a `DECIDE`

Excess cases, second-generation cases from a case lane, and cases from a report whose mechanism says `UNKNOWN` reach the coordinator as a `DECIDE` carrying a ready `orca-desk: launch <first>-case<n> NOW incident xhigh brief=<path>` line per case. The coordinator starts a lane by posting its line.

The coordinator ratifies each launched case lane, has the fix's one post to the reporter say the case is still open, and never parks a named case while the break is live.

## Take over another drive's stalled fix

**Watch another drive's incident fix and take it over the moment it stalls.** Another drive owns an incident's fix when the runner posts an `INCIDENT-OWNED` state record or another drive's lane opens a fix PR. In that turn, the coordinator spawns `incident-<slug>-watch` as `long-running:lane` on sonnet, effort low. It runs one foreground watch on the owning drive:

```sh
lr wait 540 mailbox:incident-<slug>-watch=<cursor file> -- cci watch --drive <owning drive> --lane <its fix lane> --lane desk-runner --kind stopped --kind blocker --kind decide
```

The trigger is the fix lane's `stopped` or `blocker` at once, or a `decide` from that drive's runner naming the fix lane once five minutes pass with no `answer` from that drive's coordinator. On the trigger, the watch lane posts the `TAKEOVER` blocker and finishes:

```sh
cci post --drive <drive> --lane incident-<slug>-watch --kind blocker --to root --pr <n> --text "TAKEOVER #<n>: <owning drive> <kind> #<seq> <first line>"
```

**Spawn `incident-<slug>-ship` the turn `TAKEOVER` arrives.**

- The coordinator spawns it as `long-running:lane-ship` on opus in fast mode.
- The ship lane checks out the PR's branch with `ccx vcs worktree add <branch>` and replays it onto current dev with `ccx vcs stack submit --restack`.
- It posts `opened --pr <n>` on this drive so the landing pass owns the PR, and `ready` when approved and green so the landing pass merges it by plain squash.
- On landing, it posts the `release` record required by D20 (`reference/landing.md`).
- If the owning drive pushes a newer head later, the ship lane continues from that head and never force-pushes over it.

The coordinator posts one `decision` addressed to the owning drive's coordinator and fix lane naming the takeover and its trigger. It never sets the owning drive a deadline, never asks whether it is mid-push, and never waits for its coordinator to rule.

## Incident comms

Slack grant mechanics, `gemini-4-argon` copy generation, and `cc-slack` posting rules belong to `reference/slack-lane-brief.md`. An incident comms lane:

- Posts on what the fix lane hands it and on the fix lane's `SCOPE`, `MECHANISM` and `FIX-LIVE` records, and answers people's questions in those threads from the same records.
- Replies in the page thread and in `#outage` under standing answer ea25bad. Any other channel needs a `slack-grant` the coordinator recorded for it, and the alerts channel has none: when an alert needs a reply there, report it to `owner-relay` so he can be asked.
- Post proactively in affected account channels with a recorded grant before the mechanism is known. Acknowledge the page and say investigation has started, with no per-post owner ask. Resolve the channel and forward-deployed engineers through the ai-oncall skill mapping; on a miss, search Slack channels by customer name, report which path found them, and never guess a forward-deployed engineer mention.
- Follow up at mechanism and fix-live and nothing between them, since a test or progress is never a post. Use plain words and Pacific times, and @-mention the account's forward-deployed engineers. Every account-channel post, including the first acknowledgement, states what we are doing to prevent recurrence without claiming an unverified fix.
- Open platform-wide incidents in `#outage`. When an incident affects more than one customer or a core service, post in `#outage` with impact, timeline, status, and prevention, and update at mechanism, fix-live, and resolution. Add the live incident-doc link in `#outage` and the account channels as soon as the doc lane hands it over; never hold the first acknowledgement for the doc link.
- Follow the fix and evidence lanes on the bus with `cci tail --drive <drive> --lane <slug>-fix` and `--lane <slug>-evidence`, and post at each `MECHANISM` and `FIX-LIVE` record.

## Incident doc lane and brief

**Maintain a live retro doc throughout the incident.** The `incident-<id>-retro` lane runs the incident-retro skill's live mode in the `Forge-AI/design-docs` checkout; the steps are in the brief below. The page at `https://docs.poetic.design/incident-retros/<date>-<slug>/` polls `live/<date>-<slug>`.

- Each retro scaffold names the drive's records as sources: the cc-notes investigation and log ids, the owner answers and notes it turned on, and one `--incident "cci:<tenant>|<customer>|<lane regex>"` query.
- All incident records are written through `ccn`, never as loose Markdown under `~/.claude/scratch`.

```text
ccx: role=retro
You are incident-<incident id>-retro, maintaining the live incident doc.
Agent long-running:lane; model opus.
Owner: <coordinator agent name>, for incident <incident id>.
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
     Hand only the URL from the final RENDERED: line to <coordinator agent name> and
     <comms lane name>. The incident's step 4 report and Slack retro-link reply
     take that URL from the skill output, never a PR URL. Every such draft
     passes `retro.py comms-check <draft-file|-> --url <rendered-url>` before posting.

Do NOT touch: production, fix or evidence worktrees, or Slack posts.
Worktree: <design-docs-checkout>, exclusive to this lane.
Finish: after resolution, final sync, merge, and handoff of the checked RENDERED: URL.

Register slice: lane=<lane> step=incident
```

## Fix lane brief

```text
ccx: role=fix
You are <fix lane name>, fixing the active production alert.
Model gpt-6.1-sol, effort xhigh, service tier fast; Orca codex worker.
Owner: the coordinator <coordinator agent name> for incident <incident id>.
Authority: implement the fix and open its PR through <submit skill>.
  While the alert is active, the fix rides the next regular release when one is
  cutting or can cut within the fix's urgency; otherwise release or apply it now,
  through the release skill or <break-glass skill>, never after a dry run, a
  `--plan` or a preview: the apply's own approve hold or prompt is the only gate,
  and 0 deletes and 0 replaces apply without asking.
  Use `--emergency` only when a live customer impact cannot wait for that regular
  release, and say why in the release record. One release per fix.
  Log the apply in <runbook>, and append one line to the production-operations
  log in the same turn, for this and every other state-changing production
  command you run:
  `ccn log append "$(ccn log list --label prod-ops | cut -f1)" --entry "who | when, UTC and Pacific | cluster, team | exact command | authority | effect"`
  This authority never covers the owner's identity: never call production as him
  or drive his signed-in browser, Orca tab or session, read or write. A proof that
  needs his identity is a pick to him through `lr ask-owner`; a scratch proof with
  no named identity means a test identity or no write. `lr codex-guard check`
  refuses the Orca browser shape on a production host; this line binds every
  other route.
  Any delete or replace stops for the owner, unless he ordered this release: start
  that one with `--order <answer id>`, and its gate passes itself.
  A break-glass profile refused with ForbiddenException on GetRoleCredentials
  holds no live grant. Grant yourself, never wait for the owner, then retry:
  `env -u AWS_PROFILE tools/ci break-glass --reason "INCIDENT <incident id>: WHY" --stack DOMAIN/ENV`
  The incident section of <break-glass skill> has the wait and verify steps.
  A CI pipeline whose stored settings a `ci sync` writes is activated by the
  coordinator after landing; never run that sync yourself.

Verified facts, do not re-derive:
  alert <alert link>; monitor <monitor id> / <monitor state>
  metric query <metric query>; runbook <runbook>
  named code path <named code path>; evidence lane <evidence lane name>
  comms lane <comms lane name>; cci drive <bus id>, topic <incident topic>
  record: investigation <investigation id>, log <incident log id>
    (cc-notes, on the drive checkout)

Standing orders, fixed in this template:
  - You own what is said. <comms lane name> posts it, because a codex lane cannot post
    to Slack; hand it what people need to hear with
    `cci post --drive <bus id> --lane <fix lane name> --kind report --to <comms lane name> --text "..."`.
  - Reported <report time> PT. The bar is fixed and live within 20 minutes of the
    report, by <due time> PT; the desk's limits below run from your launch, inside it.
  - Never ask a reporter or anyone else for evidence; read telemetry and Sentry
    yourself.
  - Verification: before each push, `go build` and `go vet` over the Go packages you touched and the typecheck over the TypeScript you touched; no local test runs or lint; CI grades; a red CI step is read from its log, fixed, and pushed.
  - Any repair pass or backfill runs on in-region EC2, never on
    this Mac.
  - A hook that blocks your work gets a fix lane you launch that turn,
    `lr launch lane hook-fix-<hook> opus xhigh <brief>`, and the fix goes on through what the
    hook does not block; nothing runs the refused thing through a wrapper, `cc-sudo` or a script.
  - Any other tooling failure goes to the meta lane the same turn, as one `defect` record to
    the coordinator opening `for meta:` with any refusal verbatim, plus the hand path; it never
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
     milestone> <alert links>`, where `<alert links>` is the alert's Slack message
     permalink and its Sentry or Datadog issue URL, bare, whichever exist, and it ends
     your `MECHANISM` and `FIX-LIVE` lines too. A failing Forge release smoke team, a red dev build, and a
     failing release check are `active yes` always: they are the release gate
     and the earliest canary, and a customer run in the same window fails the
     same way, with the release that preceded it as the first suspect. "No
     customer impact" goes on the SCOPE line as who is hit so far, never as a
     no. An on-call bot's false alarm, a mute, or a monitor change that stops
     counting the failing teams is neither the mechanism nor the fix: post it
     to the coordinator as `PUSHBACK <fix lane name>: <who proposed what, and the unfixed mechanism>`
     and keep fixing. A page your mechanism proves false is muted the minute it is proven, as
     the window until your fix is live, never the close: post
     `MUTE <fix lane name>: monitors <ids> until fix-live: <the proof, one line>`, kind
     `incident`, to the coordinator, then ask ai-oncall with its verb, never a prose Slack post:
     `ci oncall connect <page id> --state <your worktree>/oncall.json --label <fix lane name>`
     under a persistent Monitor (`tools/ci oncall incidents` names the incident id), and
     `ci oncall say <page id> --state <your worktree>/oncall.json --kind request --text "MUTE <ids> until <RFC3339, fix-live estimate plus an hour, at most 24 hours>: <the proof>. The fix is ours; page nobody on these monitors."`.
     The incident stays open until `FIX-LIVE`. A release smoke team, a release check, or
     dev red is never false.
     One actor per incident (SKILL.md R16): when `tools/ci oncall incidents` shows an
     open ai-oncall incident covering this alert, route every finding and requested
     action through its commander with `ci oncall say --kind finding|request`; never
     ship, roll back, freeze, mute, page or post beside it. Investigating beside it is
     yours to do. Take it over only when `lr owner away <incident id>` prints away: run
     it right before `ci oncall takeover <incident id>` and quote its whole output in
     your `TAKEOVER` record; a hook refuses any other takeover. The takeover records in
     the owner's name, which is what his standing yes (answer 7641eea) covers, along with
     only the Slack posts the takeover itself needs, in the incident channel and the
     page thread. When it prints present, you do not take over: when you judge
     ai-oncall's handling wrong or too slow, say so in a `request` and in one record to
     the coordinator. A refused or waiting `ci oncall connect` alone is never a reason.
     No credential is read from any store, file, SSM parameter or environment unless the
     owner's words in this brief name it; a mute, an ack or a monitor change goes through
     ai-oncall's verb or an ask in the page thread, never a key you fetched.
     A no to either ends incident mode: no release, no break-glass
     apply, no new lane. Post `STOPPED <fix lane name>: out of scope, <the answer that
     is no, and why>` and leave any candidate as an ordinary PR. A fix that
     grows into a redesign of a path the plan deletes is the signal to stop,
     never a reason to widen the fix.
     A failure on our own build is ours by default. An alert whose cause is an
     Akamai or customer-edge denial, such as a tenant-edge 403, gets no look, no
     fix work and no report: the owner has ruled the class is dropped at the first
     read, so stop there and post one `stopped` record naming the ruling, and tell
     nobody else. A cause beside that denial, such as a timeout, is still yours.
     Whether a recent PR caused the failure is evidence for the
     mechanism, never a stop: customer harm now and a path the plan keeps are
     the only gates.
     NO ROLLBACK; find the mechanism and fix forward; the fix ships as a new release
     through the walker while other releases of the target keep flowing.
     No brief, lane, or exception grants a rollback or a revert. One runs only when
     the owner's own word names that act, after your diagnosis has found the issue;
     never offer one before that.
     Your first answer line says which tree the cause and the fix touch. Old tree
     only: land the minimal fix under incident authority. New tree touched: a
     fundamental fix, with the options brought to the owner, never a band-aid.
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
     A monitor you add pages no one unless the owner approved that page: land it
     notify-only, and the coordinator puts "page on this?" to him later as a card.
  3. Before you name a mechanism, read the last 24 hours of the
     production-operations log:
     `ccn log show "$(ccn log list --label prod-ops | cut -f1)"`
     An entry on the alert's target inside the onset window is the first mechanism
     to test; quote it with its authorizing answer.
     Name what lies under the trigger: the order or dependency that let it break
     this path. A fix that stops the trigger and leaves that order says so and names
     who owes the rest.
     A fix that adds capacity, such as disk, memory, replicas or a limit, is
     headroom: before `FIX-LIVE`, name what stopped the thing that used to
     reclaim it and the PR or commit that stopped it, found in the subsystem's
     recent PRs and `ccn answer search <subsystem>`, and put both in the
     `FIX-LIVE` line, which a hook refuses without them.
     A hand apply's `applied` record names the file:line or row that next
     writes the same field and whether it agrees, and a capacity `FIX-LIVE`
     waits until that writer's PR carrying the applied value is live, or says
     the writer does not reset it.
     The moment you know the mechanism, record it on the bus:
     `cci post --drive <bus id> --lane <fix lane name> --kind mechanism --text "MECHANISM <fix lane name>: <mechanism, one line> <alert links>"`.
     The line cites what proves it: a record, PR, build, `file:line`, monitor or
     notebook. A cause you infer and have not proven opens with `Unproven:`.
     Also send a status Run message with subject `mechanism: <evidence>`.
  4. Design check, when the brief quotes design rulings. Before the PR opens,
     record the entry point your change calls and how it meets each ruling:
     `cci post --drive <bus id> --lane <fix lane name> --kind design --text "DESIGN <fix lane name>: <symbol at file:line>; <each ruling, met how>; leaves out: <none, or each piece>"`.
     Open the PR once the coordinator answers that record; a redirect arrives
     on the bus. A durable fix ships the
     agreed design whole. A first PR that leaves out part of a ruling (its
     footers, its judge, its entry point) changes what the code means, so it is
     a hold, never a "static first" split; mitigate through the apply instead.
  5. Open the PR through <submit skill>. When the broken surface is the
     reviewer, ask the coordinator for a human review in the same turn.
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
     `cci post --drive <bus id> --lane <fix lane name> --kind fix-live --text "FIX-LIVE <fix lane name>: <evidence, one line> <alert links>"`.
     If the evidence shows the alert is not ours, record that instead:
     `cci post --drive <bus id> --lane <fix lane name> --kind not-ours --text "NOT-OURS <fix lane name>: <evidence, one line>"`.
  9. Run <exact live-check command> every two minutes and post its output as a
     status Run message until it shows <expected live output>. Run the
     reporter's own case again on the released build. Once both pass, send a status
     Run message with subject `fix-live: <evidence>`. The brief names
     that exact command; the coordinator uses it too. For a static site, for example:
     `curl -fsS https://platform.poetic.com/ | grep -oE '/releases/[0-9a-f]{40}/' | head -1`.
  10. Record every artifact in cc-notes, never under ~/.claude/scratch:
     `ccn log append <incident log id> --entry "<kind: one line>" --attach <file, repeatable>`.
     A directory goes as one .tgz; `--replace` updates a same-named attachment.
     Investigation detail goes to
     `ccn investigation append <investigation id> "<evidence, one line>"`.
     Mechanism, live, and not-ours go on the bus as the records above, where
     `lr incident status` reads them. Never run `ccn sync`; the coordinator syncs.

Escalate: with no mechanism 15 minutes after launch, the orca desk launches an
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
  Each missed limit sends a blocker to the coordinator, which reports it to `owner-relay`.

Do NOT touch: unrelated targets, files, branches, or another lane's worktree.
Worktree: <absolute path, exclusive to this lane>.
Finish: when the PR is noted and any production apply is verified, report to
  <coordinator agent name> with the PR, head, apply counts, runbook entry, and the
  `UNFIXED` lines again. Use the bus when unsupervised, otherwise the
  preamble's Orca worker_done command. Your
  completion settles this assignment; the coordinator owns the incident until its
  final reply. Never report live from a mute.

Register slice: lane=<lane> step=incident
```

## Evidence lane brief

```text
ccx: role=evidence
You are <evidence lane name>, reading telemetry, logs, and the deploy timeline for the active production alert.
Model gpt-6.1-sol, effort xhigh, service tier fast; Orca codex worker.
Owner: the coordinator <coordinator agent name> for incident <incident id>.
Authority: read-only evidence. Feed <fix lane name> and the coordinator.
  You gate nothing. Never ask the fix lane to wait for diagnosis.
  Read-only never covers the owner's identity: never call production as him or
  drive his signed-in browser, Orca tab or session, reads included.

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
     `cci post --drive <bus id> --lane <evidence lane name> --kind mechanism --text "MECHANISM <evidence lane name>: <mechanism, one line> <alert links>"`.
     The line cites what proves it, or opens with `Unproven:`.
  5. If the alert is a monitor defect, send the evidence to redirect the fix lane
     to a monitor fix. A muted monitor is not resolved.
  6. Record every artifact in cc-notes, never under ~/.claude/scratch:
     `ccn log append <incident log id> --entry "<kind: one line>" --attach <file, repeatable>`.
     A directory goes as one .tgz; `--replace` updates a same-named attachment.
     Investigation detail goes to
     `ccn investigation append <investigation id> "<evidence, one line>"`.
     Mechanism, live, and not-ours go on the bus as the records above, where
     `lr incident status` reads them. Never run `ccn sync`; the coordinator syncs.

Escalate: with no mechanism 15 minutes after launch, the orca desk launches an
  Opus 5.5 backup fix lane in fast mode in parallel; keep working. Never fable or astra.

Do NOT touch: code, monitor configuration, production state, deploys, or applies.
Worktree: <absolute path, read-only>.
Finish: send <fix lane name> the diagnosis, onset, evidence pointers, and remaining
  gaps on the bus. Use the bus when unsupervised, otherwise the preamble's Orca send
  and worker_done commands. You gate nothing.

Register slice: lane=<lane> step=incident
```
