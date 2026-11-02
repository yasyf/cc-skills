---
name: long-running
description: Hard rules for orchestrating multi-lane work without burning the orchestrator's context. The root holds decisions and the task list; every task with a body is a lane; every wait folds into the lane that acts; no lane parks and no lane is re-briefed; state lives in cci records, cc-notes, the PR ledger and the task list; only the landing desk and its runner enqueue lanes' stacks; the orca runner carries worker traffic; an active alert gets its incident executor the same turn; nothing is done until it is live and checked. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
---

# Long-running orchestration

The root of a drive protects its own context and the deliverable. Lanes do
the work, records hold the state, and the root reads verdicts and makes decisions. A
root that delegated everything and still ran out of context spent it on logs it read,
JSON it parsed, duplicate notifications it answered, and status it restated.

This skill sits on `~/.claude/CLAUDE.md`. Fan-out shape, delegation, model routing, and
verification depth come from there and are not repeated here.

## Contents

| Section | What it answers |
|---|---|
| [When this applies](#when-this-applies) | Is this a drive? |
| [Judgment first](#judgment-first) | How to read every rule below |
| [Improve, check, finish](#improve-check-finish) | Self-improvement, proactivity, checking your own work, driving to live |
| [The root's rules](#the-roots-rules) | R1 to R24, one paragraph each |
| [Who does what](#who-does-what) | Every runner, desk and lane, and the file that briefs it |
| [The owner's surfaces](#the-owners-surfaces) | The board, Slack, reports, and how taps travel |
| [Records](#records) | cci, the ledger, cc-notes, the task list |
| [Start and resume a drive](#start-and-resume-a-drive) | The commands, in order |
| [Scripts by verb](#scripts-by-verb) | What each script does |
| [Before every tool call](#before-every-tool-call) | The per-turn checklist |

Reference files, by topic:

| Topic | File |
|---|---|
| Landing, the merge queue, D1 to D22, `ledger.py` | [reference/landing.md](reference/landing.md) |
| Grading heads, landings, plans and instruments | [reference/grading-principles.md](reference/grading-principles.md) |
| The orca runner, O1 to O16 | [reference/orca-desk-brief.md](reference/orca-desk-brief.md) |
| Launching Orca workers | [reference/orca-workers.md](reference/orca-workers.md) |
| Writing a lane brief | [reference/orca-lane-brief.md](reference/orca-lane-brief.md) |
| cci records, desk inboxes, the lane bus | [reference/bus-contracts.md](reference/bus-contracts.md) |
| Incidents end to end | [reference/active-alert-brief.md](reference/active-alert-brief.md) |
| Slack | [reference/slack-lane-brief.md](reference/slack-lane-brief.md) |
| Owner rulings, the register, judges | [reference/owner-rulings.md](reference/owner-rulings.md) |
| Stall, prompt and label watches, API budgets, the lane supervisor | [reference/watchers.md](reference/watchers.md) |
| Compaction and progress docs | [reference/compaction.md](reference/compaction.md) |
| The dashboard and the owner report table | [reference/drive-dashboard.md](reference/drive-dashboard.md), [reference/owner-report-table.md](reference/owner-report-table.md) |
| What went wrong before, by date | [reference/lessons.md](reference/lessons.md) |

## When this applies

- Three or more lanes in flight at once.
- A drive expected to outlast one context window.
- Any wait on an external system: CI, a merge queue, a cloud apply, a soak.
- Any bring-up, migration, sweep, or audit whose units are many and independent.

A single-lane investigation is not a drive. One question goes to one subagent in direct
mode.

## Judgment first

The rules protect the root's context and the deliverable. Apply each for that purpose,
never as a literal script.

- No rule forbids reading the one line a decision turns on. The root reads that line
  and decides.
- A finding in hand is a fix. Apply it; it does not become a lane, a re-audit or a
  workflow.
- A workflow is for many independent units the owner asked to be thorough about. Small
  or known work runs direct.
- Read each owner sentence as a careful colleague would, for what it means in context.
  A rule about hot paths, such as no polling on a chat's turn path, is not a rule about
  every loop: a rare manual operator action keeps a plain poll.
- An owner repeat is a defect report about the first reading. Find every place the
  earlier reading survived and fix them all that turn.
- A tooling refusal of a legitimate action is fixed forward in a side lane the same turn.
  Never use it as an excuse or turn it into a question for the owner.
- The pack's hooks warn. They block only on trust: a merge without the owner's approval,
  a revert or rollback without his word, hook-file edits, and the session-protection
  guards. A warning is advice to weigh, not a gate to satisfy.

When 30 minutes have passed since the last owner prompt, the pack re-injects these
bullets with two of the owner's sentences. `judgment_reminder_minutes` in the drive's
config changes the interval.

## Improve, check, finish

These rules run every turn. The owner should never be the one who notices a failure,
a stall, a duplicate, or a half-finished fix.

### Improve the machinery every time something goes wrong

After any owner correction, owner repeat, stand-down or tooling defect, the root changes
its own machinery before the turn ends. It posts the mechanism of its failure, names the
file, hook or script whose change makes the failure impossible, opens that PR, and writes
the retro row. Naming a lane is not a fix. A fix PR for a live loop carries auto-merge and
an install order in the same turn.

- A reported failure is fixed in place, never answered. When the owner says to look
  into a failure, the deliverable is the fix, live. The account is a one-line note beside it,
  never a document, a report, or a worker of its own.
- A retro is the root's own. The root says in chat, in plain words, what failed in
  its own conduct. Then it ships the tooling fixes, and it puts options for a deeper
  shift on the owner's board only when there is a real design pick. A retro document is
  not the deliverable.
- A tooling defect is fixed in the tool. It is never written down as a memory, a
  note or a workaround. A memory that records a tooling defect is deleted when the fix
  lands.
- One supervisor, never band-aids. Liveness, restarts, successors, and stall
  detection are functions of the lane supervisor's single loop
  ([reference/watchers.md](reference/watchers.md)). No one-off resilience fix is started;
  the hack it would replace is deleted once the supervisor covers it.
- A known defect is fixed the hour it is known. A defect that blocks landings or
  recurs is never acknowledged twice. The second sighting is a root defect.

### Ship every tooling fix cleanly and responsibly

The drive improves its own tooling as it runs. A tooling bug, refusal, hook misfire, stale
alarm, or missing verb that any lane or the root hits is a deliverable of the drive in the
turn it is hit. It gets a named owner lane and a record. It is never a workaround, a mute, a
retry, or a "known issue" label.

- Cleanly means one fix for the whole class, tests that fail on the old code, no hacks,
  no duplicate paths, and zero comments. The old path is deleted when the new one lands.
- Responsibly means the owner lane tracks the fix to live: a PR that merges on green,
  the release installed, and the lane that hit the defect confirming it live. The lanes
  that depend on the tool hear of the change at handoff. A release is pinned so a
  half-shipped fix never runs under a live desk.
- A root that acknowledges the same tooling defect twice has a defect of its own.

### Fix the class, not the instance

- A second bug in an area means a redesign of that area. The root names the class
  and the fix removes the whole class, so the third bug cannot be written. A point fix on
  a second bug in one area is a defect. A redesign that changes a product surface or the
  plan goes to the owner as a card; the rest ships.
- One responsibility, one code path. When a responsibility has more than one code
  path, the drive collapses it to one owner. The root sweeps for these; it does not wait
  for a third bug.
- Every fix names its pattern. A fix lane names the pattern its bug is an instance
  of and the structural change that removes the pattern. Cards that are instances of one
  pattern merge into one pattern card, with the class fix as an option.
- No band-aid with the durable fix carded for later. The durable fix is built now. A
  card is only for a genuine design pick between two durable fixes.
- Flakes and reds are fixed at the root, all at once. Retries, reruns, `--flake`,
  skips, widened timeouts, and bypasses are not fixes. Every test is fixed at its root
  and made fast, and every new flake classification gets a cause-fix lane the same hour.
- A regression test always ships, but never blocks a fix that has waited long. The
  fix lands, and the test becomes a follow-up PR with its own task.

### Check your own work the way the owner will

- Read what the owner reads, before every report. The root reads its dashboard, its
  ask ledger, its retro rows and its board as the owner would, and fixes what it finds,
  before it sends any status. These are root defects, caught before he sees them:
  - a duplicate row or ask;
  - a stall older than its deadline;
  - a row that renders `UNDEFINED`, blank, or with no owner;
  - an owner message with no answer or action;
  - an owner ruling he had to repeat;
  - a "not started" for work a lane posted on;
  - a card for a question a ruling, plan line or earlier pick already settled.
- A settled ruling is never re-held or re-asked. Before any question or hold, the
  root searches prior answers and picks on that topic. A re-ask of something he answered
  is a defect, and the root fixes every place the narrowing survived.
- Updates come from the ledger, never memory. Before any status or update reply, the
  root runs `owner_update.py --drive <drive>`. It prints every open owner ask with its
  state and its lane's last record, every in-progress task with its lane's last record
  time, every delivery since the last update (a `report`, `done`, `fix-live` or
  `handoff` from a lane owning an open task), every PR row on his board, and every open
  incident. The reply covers each delivery and calls nothing in flight "not started."
  The pack's Stop hook refuses a status reply that drops a delivery, or says "not
  started" of a lane that posted in the last 30 minutes, and hands back the lines to add.
- A lane's report is a record. A lane posts its report as a cci record first (a
  `report`, `head`, `handoff`, `fix-live` or `done`), then sends one `SendMessage`. A
  report sent only by `SendMessage` is a defect: a message is lost across a compaction,
  a record is in the next handoff. The pack's stop hook refuses a lane's stop while its
  last 10 minutes hold a `SendMessage` to root and none of those records.

### Drive every item to live

- Done means live and checked. A thing is done only when it runs in production, or
  in the installed tool, and the root has read it there. "The lane said done," a merged
  PR, and a green build are steps on the way. See R8 for asks and R10 for status.
- Every retro row, ask and defect have a live owner and reach live. `retro.py` is
  the only writer of retro rows (`add`, `set`, `live`, `duplicate`). A row names the
  failure, the mechanism that makes it impossible, a live worker, a due time, and a state
  of building, open (with its PR) or merged, and only `retro.py live` with the `FIX-LIVE`
  record id closes it. An ask is one ledger row per text, with a live owner and a due
  time. Any row unchanged for a day, or past due, reaches the root as a blocker each day.
  A row with no live owner is the root's to drive or hand off that turn.
- Anything on the drive's own path is the drive's. A failure in a release pipeline,
  build or tool the drive runs gets a fix lane with apply authority the turn it is seen.
  Never say "if it is ours," and never wait on a classification.
- Prepare the next step before the current one finishes. Plans and boards are
  curated on the assumption that every board PR lands. The test build, the test machines
  and the runs that grade it are ready before the merges finish.
- A board pick starts its build the moment it lands. The one exception is a pick
  whose build a later card on the same board could change; that lane names the card it
  waits on.

### Build ahead while the owner is away

A card that waits on the owner never waits idle. While any of his cards is open, and
always when he says he is away, the drive builds toward his likely answer and tests
it, so his tap lands on proven work.

- Build the recommended option. For every open card with a recommended option, an
  implementation lane builds it off fresh trunk as a pushed branch named
  `options/<card>-<option>`. Never a PR, never enqueued, never released to production.
  The card's line carries the branch and its head.
- His pick decides the branch. When he picks the recommended option, the branch
  opens as its PR through the submit skill. When he picks another, the branch is
  abandoned and his pick is built from scratch, never by editing the recommended one.
- Guess the rest. One lane guesses the cleanup and remaining work he is likely to ask
  for and builds each guess the same way: a pushed branch, no PR until he approves.
- Test every option branch on staging, one at a time. The release lane deploys one
  option branch to staging, and the test Mac and perf lanes point at it and run their
  full scenario and regression tables. Each lane posts its results to the card's line:
  every number against the current trunk build and the best earlier result, and every bug found.
  Only then does the next branch deploy.
- A met bar is a waypoint. Perf and tester lanes never stop at a met bar. They move
  to the next realistic scenario and re-run the whole table on every new build as taps
  land and structural changes merge. A regression against the previous build or the
  best earlier result weighs as much as a dev red: the root names the PRs that landed between the
  two builds, spawns the fix lane, and the number is restored, never accepted.
- Push back when a fix targets a path the plan deletes. Before building more on a
  failing path, the root checks whether the plan keeps it. When two owner rules pull
  apart, it tells him in one line which rule it applies and why.

## The root's rules

Each rule is stated once. Ids are stable; scripts, hooks, and other files cite them.

**R1. Take ground truth from the owning lane.** Once a lane can answer a question, the
root never greps a log, lists cloud resources, curls an API, opens a build page, or
parses JSON. It asks the owning lane with a scoped resume and takes back at most five
lines. Verifying means asking a lane for the one number; checking liveness means asking
for a three-line status with a deadline. Silence is not progress. D3 is the exception:
the root reads priority PR state itself, checks its gates and labels it, and never relays
an ETA for a green priority PR.

**R2. Delegate anything with a body.** A read past one file, a log, a multi-step
investigation, PR or merge mechanics, and a bulk enumeration each belong to a lane. The
root never reads a PR, diff, CI log, Slack thread, plan artifact, or long file to answer a
question. `gh pr view` and `ccx vcs diff` become a sonnet or opus reader lane or
`cc-context:pr-review-triage`; a CI log becomes `cc-context:ci-triage`; a Slack thread
becomes a reader lane. The lane returns the conclusion with pointers such as `file:line`, PR
number, and Slack ts. A lane report longer than a screen goes back by scoped resume, to be
rewritten in 25 lines with a disk pointer.

The `root_context` hook warns, and never blocks, when a drive's root:

- reads a window past 150 lines, or a lane artifact (anything under `audits/`, `briefs/`,
  `handoffs/`, `tool-results/`, `subagents/` or `transcripts/`, a `matrix.md`, a `.jsonl`
  transcript);
- runs `rg`, `ag`, or `cat`, `head`, `tail`, `sed`, `awk`, `grep` or `jq` over a file
  outside `inbox/`, or any read except `grep -c` over `inbox/*.md`;
- reads history or a PR through `git log`, `show`, `diff`, `blame`, `grep`, `gh pr view`,
  `gh pr diff`, `gh run view`, `gh issue view`, `ccx code`, `repo`, `web`, or `ccx vcs diff`,
  `show`, `history`, and `reviews`, or uses the Grep tool or the ccx Model Context Protocol (MCP) read tools;
- reads Slack, or fetches a Claude Doc, Datadog notebook, Linear document or Capacities
  page, or calls any MCP tool after one of its responses passed 8000 characters.

The hook allows the plan and its progress folder; filters over piped output such as
`ccx vcs status | grep`; heredoc appends, `ls`, `date`, `ccn`, `ccx vcs status`,
`ccx vcs pr status`, `cc-present`, `Agent`, `SendMessage`, the task tools and `Monitor`;
and one bounded read of one named file outside `inbox/`. Allowed bounded reads are a Read window, `head -n`,
`tail -n`, `sed -n 'A,Bp'`, `ccx code read --section` or the MCP `ccx_code_read` with a
section, each up to 150 lines; `head -c` up to 4000 bytes; `grep -c`, `grep -m` up to 20,
or `wc`; or a read piped into such a `head`, `tail` or `sed`. A `# ccx:raw` comment or
`CAPT_HOOK_CCX_RAW=1` runs a command as written. `LONG_RUNNING_ROOT_READ_LINES`,
`LONG_RUNNING_ROOT_MCP_CHARS` and `LONG_RUNNING_ROOT_ARTIFACT_DIRS` change the thresholds.

**R3. One lane per wait-then-do chain, and no lane parks.** "When X lands, do Y" is one
sequencer lane that polls X, does Y and sends one message. Never a per-step Bash watcher,
a Monitor per build, or a root-context poll. A lane that ends its turn waiting is a bug:
lanes poll in foreground loops to a terminal state and report once. A lane never
backgrounds a subagent or codex run and ends its turn; it runs them in the foreground or
polls the reply file in a foreground loop. Waiting mechanics are in
[reference/bus-contracts.md](reference/bus-contracts.md).

**R4. Never re-brief a lane, never answer a duplicate.** A `SendMessage` to a finished
agent resumes it with its whole brief, and it re-runs that brief. A `SendMessage` to a
running agent spawns a second copy that restarts the lane while the original never sees
the message.

Reply to nothing already acted on. An idle notification answers only the
messages the lane had when that turn began; wait for the lane's report on a newer order.
`desk-wait.sh` marks the mail it prints as read, so the runtime never delivers it twice.
Rotation flushes a lane in place, as described in
[reference/watchers.md](reference/watchers.md#supervise-and-rotate-lanes).

**R5. Write it down, do not hold it.** Findings go to cc-notes from the lane that found
them, never into the root's window. Lanes carry no MCP tools beyond cc-slack's, so they
write with `ccn log append`,
`ccn investigation open` and `append`, `ccn note add`, `ccn doc add` and `ccn task add`;
the root may use the cc-notes MCP tools. Pass record ids to lanes:

- An incident is an investigation and a log, through `incident.py`.
- A handoff is a doc, per [reference/handoff-subagent-brief.md](reference/handoff-subagent-brief.md),
  carrying the outgoing session's `cci me` line.
- A report or audit is a note for a verdict, or a doc with a read-before trigger.
- Lane briefs are attachments on one `briefs: <slug>` log, replaced with `--replace` on
  edit. A tool that needs a file reads `ccn attachment path <id> <name>`.

Nobody runs `mkdir` or `cat >` to make a markdown record under `~/.claude/scratch`.
Files stay files only where a reader needs them, including desk inboxes for `inboxes.py`, the
orca-waiter and Monitor streams, launch receipts, desk state, and the executor's actions
JSON, which needs `flock` and compare-and-swap. Lanes never run `ccn sync`; the root
does. The task list rules are under [Records](#records).

**R6. Put every owner request in flight the turn it arrives.** Start a new lane, or an
explicitly named parallel sub-lane; never append the request to a busy lane's queue.
Split a lane holding more than two unstarted items into parallel lanes. Where their
files overlap, stack them, each lane branching off the previous lane's branch, and each
brief names the files its lane owns. Before every milestone report, dispatch every owner
ask still unstarted and name it in the report. More lanes with one deliverable each beat
fewer lanes with queues, because a lane's queue is wall-clock the owner pays for.

**R7. Check a PR's state before reporting it.** Never state from a message or from memory
that a PR is merged, queued or blocked. Run `ccx vcs status` in the stack's worktree, or
look for the squash commit `(#N)` on a freshly fetched trunk. This binds the root and
every desk.

**R8. Record every owner ask in the ledger.** In the turn an ask arrives, before or with
its lane dispatch, the root runs:

```sh
ledger.py ask --ledger <id> --text "<verbatim>" --lane <lane> --accept "<acceptance check>"
```

- Each new ask gets its own lane (R6). A PR carrying an `IN-PR` ask takes no second ask,
  and `report --ask` refuses one; the new ask goes on a stacked follow-up PR.
- Every lane records each sub-dispatch as an ask row before dispatching it, with the
  reply it must return as `--accept`, and runs `ledger.py answer` when the reply lands. An
  orphaned sub-dispatch shows as `LOST`, and the root dispatches every `LOST` ask that
  turn.
- A lane whose progress is not a PR records each step with
  `ledger.py progress --ledger <id> --ask <id> --note "<what moved>" [--artifact <url or id>]...`.
  The ask reads `IN-PROGRESS` until 30 minutes pass with no entry.
- Before claiming anything is assigned, in flight or done, the root reads
  `ledger.py summary --drive <drive>` or `ledger.py show --asks`, never its own plan table.
- An ask is done only at `LIVE`. Once every linked PR has landed, the root records the
  shipping pipeline's next run with `ledger.py live --ledger <id> --at <ISO>`, moving the
  ask from `LANDED-NOT-LIVE` to `LIVE`. Before that the root says "in #N, not live yet:
  `<blocker>`".
- `ledger.py drop` records an ask the owner withdrew, and `ledger.py answer` closes an ask
  that was a question instead of shipped work.

**R9. Orca worker traffic runs through `desk-runner.py`.** The root posts `relay` and
`launch` lines with `desk-launch.py` and never runs a check-and-ack loop, a relaunch
sweep or a helper script inline. No model desk sits between the root and Orca. The root
receives cci records over the cc-inbox plugin's channel, with no Monitor: it launches
with `--channels plugin:cc-inbox@cc-inbox` beside every other channel plugin the drive
uses, and at drive start runs once:

```bash
cci subscribe --drive <drive> --reader root --kind incident,decide,ask,defect,blocker --follow oncall=incident
```

Records addressed to `root` or `main` arrive whatever their kind, with other lanes'
broadcasts of each `--kind`. `--follow oncall=incident` adds ai-oncall's page feed: one
`incident` record per page open, state change and close, handled under R16. Each record
arrives as a `<channel source="plugin:cc-inbox:cci">` tag; skip a seq already handled.
The subscription survives compaction and `/clear`, and the cc-inbox `SessionStart` hook
moves it into a resumed session; re-run `cci subscribe` to change kinds.

A root launched without the flag does not receive tags, and managed settings must list every plugin the flag
names in `allowedChannelPlugins`. Never arm a Monitor on `cci watch` for the root; a root
switching over stops its Monitor after subscribing, since records in the overlap arrive
twice. The
runner's config, startup and passes are in
[reference/orca-desk-brief.md](reference/orca-desk-brief.md).

**R9a. An urgent hold gets a decision owner and a clock.** A lane holding a release the
plan marks urgent, or a blocked catch-up of a coupled control-plane set, posts
`orca-desk: hold <slug> owner=<lane> :: <what is held, and on what>` with
`desk-launch.py hold`, naming itself, or a dedicated decision-owner lane. It appends
`orca-desk: unhold <slug>` when the release moves. After `deadlines.hold_minutes`
(default 15) the runner posts:

```text
DECIDE hold:<slug> <owner>: held <n> min: <what> | the root decides it now with <owner>, ahead of any open owner question on another subject
```

The root resolves it with that lane ahead of any open owner card on another subject.

**R10. Landed is the only progress.** Status to the owner is landed, queued, or the exact
blocker naming the PR, its head and the gate it waits on. "Open" and "in CI" are not status.
PRs on merge conflicts for more than 24 hours combine into trains (D7, D8). A red gets a
structural fix with a test, never a retry; a timing red gets one rebuild, then a report.

**R11. Hands off deploys.** The root and its lanes never start, approve, override, watch,
or re-cut a release or deploy unless the owner asked for it in that turn. An option the
root described earlier is not permission, and no script re-cuts on its own. No cut goes
out while a known fix for the last failure is still open.

Incidents are the exception: a release whose purpose is landing an incident fix is pre-authorized end to end across its
start, every approval step and its migrations, starts real at once with no dry run, and
is the smallest release that carries the fix. Its apply stays bounded by zero deletes and zero
replaces with counts reported; a delete or replace goes to the owner. Release lanes are
in [reference/release-lanes.md](reference/release-lanes.md).

**R12. Route every red at once.** Every `STALLED-RED` line goes to its owning lane in the
same turn with a 15-minute deadline. A red with no live owner gets a new lane at once,
following [reference/red-sweep-brief.md](reference/red-sweep-brief.md). Idle lanes never see CI.

**R13. Dispatch every owed item the same turn, and read priority PRs directly.** When
several owed items have no PR, the root dispatches all of them in one turn, one lane
each, instead of one per turn or behind a desk. It reads priority PR state itself in
one batched `ccx vcs pr status <n1> <n2> ...` call.

**R14. A process fix is a PR the turn it is adopted.** Every tooling or process change
the root makes mid-drive goes to a lane that PRs it into the skill or tool that turn. The lane
drives it through merge, release and install. That includes a new desk, a new landing
model, and a new rule relayed to lanes; a change living only in inbox files or a desk's
brief is lost at the next drive.

Tool improvements are automatic. A tooling defect, a refusal, a `# ccx:raw` fallback, or a
repeated chore that could be a verb gets a tooling lane that turn, and the work keeps
going. A lesson is a process fix too. When the root, a lane or a retro names a lesson, a
rule or a fix for next time, the PR that encodes it opens that turn from a lane spawned
for it, unprompted. A lesson that lives only in a status, a memory or a retro card is not
landed. A lane that cannot spawn records a `ccn papercut` and names the defect in its
report, and the root dispatches the tooling lane on reading it. The capt-hook
`tooling_nudge` hook flags these signals; the rule holds without it.

- Only a tooling lane installs a cci or long-running release under a live drive, and only
  once `drive.py digest --quiet-point` exits 0, so no runner pass or merge runs through
  the swap.
- A tooling fix is proven on the record that reported it. A PR that fixes a reported
  defect in the lanes mover or the landing runner's class check carries a fixture that
  `replay.py capture` writes from that record into `tests/fixtures/replay/`, and CI
  replays every fixture. `desk-runner.py handoff --version` and the supervisor's move to a
  newer install replay the drive's last 24 hours and refuse when the new pack relaunches a
  lane or refuses a PR the old one did not; a gate that crashes only warns. The orca desk
  posts, per fixture class, how often the defect recurred in the 24 hours after install.
- A patch made under incident pressure is recorded as a reconcile item the turn it ships,
  and its root-cause fix runs as its own lane, never as backlog.

**R15. CI is the verifier, not the machine.** No lane runs tests, builds, typechecks or
lint locally; CI grades, and a red step is read from its log, fixed and pushed. That holds
for one failing test as much as for a whole package. The pack's `local_builds` hook warns
on each such command in every session of a drive, the root's included; a Go golden's
single `go test -update` run is the one exception. Machine load never holds a
Buildkite build, a hand apply of a saved plan, an incident step or an owner-directed
step; those run at once.

- A wrapper that sends builds or checks to remote compute refuses when no remote target
  answers; it never falls through to the local toolchain. A lane that sees work it sent
  remote run on the Mac stops it and posts a `defect` to root naming the wrapper and the
  command; the root spawns the fix that turn.
- A batch job never runs on the orchestrator's machine. A data pass, a backfill, a dry
  run of one and a bulk rewrite run on in-region compute, one process per bucket or team.
  Every pass brief names its compute (the box recipe, the region, the bucket or team
  each process owns), and the lane refuses to start on the Mac. A one-shot read, such as
  one run's dry run or a metadata listing, is not a batch job.

**R16. An active production alert gets its executor the same turn.** Active alerts
include a Datadog monitor, a Sentry alert, an `#outage` report, an on-call page, a broken
CI or review pipeline, an alert or breakage link from the owner, and an `incident` record
from ai-oncall's feed. They also include a runner `PAGE` record, a record saying customers are failing or
about to fail from a change already moving, a failing release smoke team, a red dev build, and
a failing release check. Each is P0 from the moment it is seen, with no owner round-trip
and no wait on ai-oncall. That turn, before anything else, the root runs
`incident.py open` with the owner's grants and starts `incident.py run` in the
background. The executor then owns the incident through its final reply, covering the fix and
evidence lanes, comms, clocks, release, and recovery.

Between the alert and `incident.py run`, never insert a real-versus-false or ours-versus-theirs verdict, a
mechanism-depth mandate, an `AskUserQuestion`, or a mute treated as resolution. At the
spawn and every check-in the root posts the two scope answers on whether it is failing customers
now and whether the plan keeps this path; a no to either ends incident mode that turn. A
mute is the window until fix-live, never the close. During an incident the drive's
process gates stand aside for the incident's PRs and lanes; the plan check never does.
Every fix lands forward; a rollback or revert runs only on the owner's own word naming
it. Everything else, including the root's part on each executor line, clock line and
case lane, is in [reference/active-alert-brief.md](reference/active-alert-brief.md).

**R17. Owner routing applies to the next spawn and to every live lane on that problem.**
An owner's routing or process instruction, such as "use Orca for incidents" or "pass the
fast tier flag," applies the turn it arrives. Stand down every live lane on that problem
and relaunch on the named route. Never record it for new lanes only, weigh it against the
routing table, keep a lane because it is "already deep in the code", or defer it to the
lane editing the skill.

**R18. A ruling reaches a lane the way it reads.** Orca lanes get
`desk-launch.py relay <lane>[,<lane>...] <text>`; launches use `desk-launch.py lane` under
O1. A desk gets rulings as cci records addressed to it (I1). A running Agent lane gets a
`SendMessage` and the handoff line in the file it hands off through; the root confirms the
handoff on disk before treating the lane as stood down, since a running copy never sees
the `SendMessage`.

**R19. A settled question is applied, never asked.** Before any owner question reaches
the board, the root checks it against the owner's plan and every plan it names,
starting with the Decisions sections, `ccn answer list --label scope:durable` and the drive's
answer labels, and the project's feedback memories. When any of them answers a question, the root
writes the ruling to the lanes (R18) and logs it. A lane's question
list is input to filter, never output to forward.

An absolute owner instruction such as "nothing
is dropped" stays absolute in every brief and ruling, with no "unless the owner drops
it" exit. A prior decision that contradicts an owner ruling is a defect to fix forward.
The authorities, the judge that checks every asking call, and how standing rules are
recorded are in [reference/owner-rulings.md](reference/owner-rulings.md).

**R19a. A gate that wants an owner-recorded answer for a settled decision is satisfied
the same turn.** When the plan or a board pick carries the decision, the root records it
as the ruling and accepts on the owner's behalf, citing the plan line or pick. When the gate needs his fresh answer, the root puts the
question card on the board that turn. It never ends
a turn waiting for him to say "ask" and never leaves PRs parked behind the gate.

**R19b. Extrapolate every ruling.** A ruling binds everything it implies: sibling
mechanisms, both directions of a relation, and every artifact, lane, ledger row, budget,
test, card, and brief that carries the old assumption. The turn it arrives, the root
records the owner's words verbatim, writes the implications, confirms them with him in
chat for a few or on one card for many, and applies the ruling to every live artifact and
lane at once. A ruling travels by its words, never by an id or a gist.

Before a board,
brief, handoff or plan goes out, the root sweeps it against every ruling's broadest
reading and drafts from the rulings instead of the code on trunk. It also checks each
lane's reported work against the plan before relaying it. A plan gap is a new column,
table, route, field, state, timer, or metric missing from both the plan and every owner row; a `DECIDE` record
never settles it, so it goes on the board and the lane stops on it. A contract,
read-back or `DECIDE` record is never narrower than the owner row it cites.

**R20. A Slack link from the owner is an assignment.** A Slack permalink the owner pastes,
bare or with words, is the root's to own, whoever wrote the message. His in-thread
"Looking" or "on it" means he handed it to the drive. That turn, after only R16's
`incident.py open` and `run`, in one batch of tool calls, the root:

1. spawns the Slack lane, `general-purpose` on opus, from
   [reference/slack-lane-brief.md](reference/slack-lane-brief.md);
2. spawns the doing lane (fix, investigation or answer) from the link itself, in the
   same batch, never after the Slack lane's read; a production break is an
   `orca-desk: incident <slug> <link> reported=<HH:MM> :: <what fails>` line;
3. sends the owner one line naming both lanes;
4. runs `TaskCreate` for both.

The root never composes Slack copy and never reads Slack itself. Every Slack rule is in
the Slack lane brief.

**R21. Talk to the owner in his terms, and own the root's delays.**

- Every time shown to the owner is Pacific with no zone label (`10:35am`,
  `Oct 1 at 9:52am`), in chat, boards, Slack, and the owner-facing lines of handoffs and
  progress docs. Inbox, runbook and ledger stamps may stay UTC.
- An owner "why did you..." gets the root's own answer that turn, stating the mechanism of its
  failure in plain words, then the fix and the lane that ships it.
- A delay the root's dispatching caused is the root's. Never attribute it to the owner.
- Every owner-facing line and card names the thing, never its codename, inbox id, answer
  id, or `ctx_` id. Before sending, reread each line as someone who has not read the
  drive's records, and rename every noun that reader does not know.
- Every artifact the owner reads has two layers. First comes the human layer: a picture,
  a timeline, a table with toned cells, or at most 40 words on what it changes for him,
  where it stands and what it needs from him. The agent layer (metric names, commands,
  ids) sits behind a click. A markdown table renders as a table. A merge row is its six
  facts, one line each, words before any id. A picture goes on a card with no decision
  in it, since focus mode hides a deciding card's other children. Detail sits in a
  closed `<details>`. The writer screenshots a board at desktop width and at 390 wide
  before it reports.

At `Stop` in a drive, the `owner_facing` hook queues `Next reply to the owner: give times
in Pacific with no zone label, never UTC` when the root's final reply carries a UTC clock
time (`17:35Z`, `17:3xZ`, `16:52 UTC`). A judge warns when a reply, card, board, plan or
root question needs the drive's vocabulary, reads as fields instead of sentences, or
carries a proposed Slack reply. Ids a pattern picks out reach the judge as candidates,
never as the verdict. The time nudge and the plain-words nudge on each term fire at most
once an hour each. The `authorizing_lines_wait_for_the_owner_question` hook blocks a
`GO`, `LIFT` or `UNBLOCK` line appended to an `inbox/*.md` file, and a
`cci post --kind go|lift|unblock` record, when it names the PR or routing change an
unanswered question or board posted that turn asks about. Write it in a later turn, once
the owner answers.

**R22. Quote the owner's design verbatim. Ship that design or hold.** Every brief or
ruling on a subsystem quotes each owner design ruling verbatim, with the required entry
point as a symbol at `file:line` that a reader lane found before dispatch. A package list
is not an entry point. Before `READY-FOR-SHIP`, the lane posts a `contract` record naming
the entry point the diff calls, each ruling it meets and anything it leaves out. It
settles the check itself from its brief, the register and trunk, and keeps building; it
asks the root first only when its choice changes owner-visible behavior.

The root reads
the contract and withdraws it only on a mismatch with a quoted ruling. Never ship a
"static first" split that changes the durable fix's agreed semantics: ship the design
whole or hold the PR, and re-route a lane that cannot build it instead of narrowing the
design. Incident fix lanes record the check with `incident.py note --design-check`.

**R23. Stop every finished agent the turn its completion arrives.** On each subagent
completion, after taking its report, the root calls `TaskStop` on it. Before any spawn, it
stops every finished agent of its own. A full concurrent-subagent limit means finished
agents still hold slots, never a blocker to report. Live lanes, flushed lanes and Orca
terminals are not finished agents.

**R24. A production operation is logged the turn it runs, and read before a mechanism is
named.** Every destructive or state-changing production operation an agent runs on the
owner's authorization includes a `kubectl exec`, a reset-table, a data delete, a hand apply, a
break-glass write, or the R16 apply. Each gets one line in the cc-notes log
`production-operations`, label `prod-ops`, in the same turn:

```sh
ops=$(ccn log list --label prod-ops | cut -f1)
ccn log append "$ops" --entry "who: <session or lane> | when: <UTC> (<Pacific>) | where: <cluster>, <team> | action: <exact command> | authorized by: <answer id> | effect: <what changed>"
```

Every incident lane and the alerts desk read the last 24 hours of it before naming a
mechanism (`ccn log show "$(ccn log list --label prod-ops | cut -f1)"`). An entry on the
alert's target inside the onset window is the first mechanism to test. The read never
stands between an alert and `incident.py run`.

## Who does what

The root spawns a subagent for every task with a body, its own routine work included. A
lane never spawns with `Agent`, since a teammate cannot background the call; instead, it writes the
brief to a file, posts
`cci post --kind ask --to root --text 'spawn <name> brief=<absolute path> model=<opus or sonnet>'`
and keeps working. The `lanes_never_spawn` hook refuses a lane's `Agent` call.

**Implementation lanes run on Sprites.** Every implementation lane launches through the orca
desk onto a Sprite with `desk-launch.py lane`. An `Agent` spawn is only for a coordination
lane that holds no worktree, such as a board, progress, watchdog or comms lane, or a
read-only search. Incident fix lanes keep the incident route. A lane that must hold the Mac,
for Mac-only desktop or signing work, names why in its brief.

| Runner, desk or lane | What it does | When it runs | Brief |
|---|---|---|---|
| Landing runner, `desk-runner.py run --desk landing` | The drive's merge queue: gates and enqueues ready bottom prefixes, routes reds and conflicts, starts releases on landing | Before the first PR when three or more lanes ship through one queue | [landing.md](reference/landing.md) |
| `landing-desk` | Takes lane reports, reconciles landings, keeps the ledger | With the landing runner | [landing-desk-brief.md](reference/landing-desk-brief.md) |
| Orca runner, `desk-runner.py run --desk orca` | Launches and relays to Orca workers, clocks incidents, starts finder and fidelity jobs and the judge pass | Before the first Orca worker | [orca-desk-brief.md](reference/orca-desk-brief.md) |
| Incident executor, `incident.py run` | Owns one incident from intake to final reply | The turn an alert is seen | [active-alert-brief.md](reference/active-alert-brief.md) |
| `alerts-desk` | Reports monitor transitions and judges when one needs a lane | When the drive touches production | [alerts-desk-brief.md](reference/alerts-desk-brief.md) |
| Priority desk | Drives one owner-named number-one outcome | While that outcome is open | [priority-desk-brief.md](reference/priority-desk-brief.md) |
| `drive-watchdog` | Watches the drive against the plan and keeps the retro log | Once the drive has a plan | [drive-watchdog-brief.md](reference/drive-watchdog-brief.md) |
| `red-sweep` | Owns every red with no live owner | Once the drive has open PRs | [red-sweep-brief.md](reference/red-sweep-brief.md) |
| `finder-desk`, fidelity desks | Grade every head for defects and for fidelity to the plan | With the landing runner | [finder-brief.md](reference/finder-brief.md), [fidelity-desk-brief.md](reference/fidelity-desk-brief.md) |
| `dashboard-keeper` | Keeps the drive dashboard app running | From the drive's first plan | [dashboard-keeper-brief.md](reference/dashboard-keeper-brief.md) |
| `user-test` | Plays the owner against every revision of a surface he will use | Per surface revision | [user-test-brief.md](reference/user-test-brief.md) |
| Merge train, semantic collisions, sequencer | Land the hot set, catch cross-lane contradictions, run a wait-then-do chain | As needed | [merge-train-brief.md](reference/merge-train-brief.md), [semantic-collisions-brief.md](reference/semantic-collisions-brief.md), [orca-lane-brief.md](reference/orca-lane-brief.md) |
| Slack lane, `comms-desk` | Post what the owner asked to be posted | Per owner link or post | [slack-lane-brief.md](reference/slack-lane-brief.md), [comms-desk-brief.md](reference/comms-desk-brief.md) |
| Handoff subagent | Writes a rotating lane's handoff | On rotation | [handoff-subagent-brief.md](reference/handoff-subagent-brief.md) |
| Lane supervisor, stall watch, prompt watch, label watch | Keep every lane alive, find stalls and stuck prompts, land unlabelled stacks | Always, as processes | [watchers.md](reference/watchers.md) |
| Board recorder, `board_watch.py` | Turns every owner tap into a cci record | Per board, started by a hook | [The owner's surfaces](#the-owners-surfaces) |
| Judge pass, `judge_watch.py` | Checks contracts and relays against the owner's words | Every minute, from the orca runner | [owner-rulings.md](reference/owner-rulings.md) |

For agent types, a lane that ships a PR or calls a skill is `long-running:lane-ship`; any
other lane or desk is `long-running:lane`. Every lane that posts to Slack spawns as
`general-purpose` on opus, because a plugin agent type's tool list is fixed when the
root's session starts. No lane or root watches Slack, and no comms lane stands.

## The owner's surfaces

### The board

The root asks without parking. While any lane runs, it posts the question with
`cc-present ask` or as a chat line that names a default, proceeds on that default, and
applies the answer when it arrives. The pack's `blocking_asks` hook refuses the root's
`AskUserQuestion` while a lane runs, unless the question carries a `ccx: ask=blocking`
line because a lane is blocked on the answer.

Every owner ask the drive blocks on goes on the root's cc-present board as a question
card, except a per-PR merge ask, which is a merge row. The root adds each card with one
command:

```bash
drive.py ask-owner --title "<question>" --lede "<what he needs to decide>" \
  --option "<answer>" --option "<answer>" --recommend <n> [--for <waiting lane>...]
```

It pushes the card to the root session's board with one single-select choice of two to four
answers, exactly one recommended, and opens one `ask` record naming the card to each
`--for` lane. It refuses a card whose text names a change or record number. Every card
already offers Other and `Explain more`; never list either as an answer. On an
`Explain more` tap, which resolves nothing, the root posts
`cci post --drive <drive> --lane root --kind ask --to root --text "explain more: <card id>"`,
then answers in plain words with `cc-present reply --block <card id> --body <answer>`. The
asking lane stops on that item until the tap; the root never parks waiting for it.

- A design doc is a set of choice cards. Every decision a design doc makes is its own
  card with options, facts and a recommended pick, and the rendered doc is on the same
  board. A doc never reaches him as one approve card.
- A tapped PR never comes back. A PR the owner tapped at any head never returns to
  his board, at any later head, for any reason; the tap carries to every later head. A
  tapped PR takes no behavior change: only what CI needs to go green (rebases, conflict
  fixes, compile, and lint fixes). A reviewer-bot, finder or fidelity finding on it is
  rebutted or accepted as a risk in the PR's Rulings comment; a behavior change becomes a
  follow-up PR. Before drawing any merge row, the drawer reads the board's verdict store
  for an approval at any head of that PR.
- A card he touched stays. A card the owner touched in the last 30 minutes, or on a
  board he is using, is never removed or replaced. Answered cards stay until he submits
  the round; removals happen only on a submit or his word.
- Every card names the event that ends it before it is pushed.
  `board_cards.py until <card> --until <at:TIME|pr:N|release:STACK@ENV:COMMIT|record:SEQ>`
  refuses a missing or past condition, and the `board_text_gate` hook warns on a card
  pushed with none. The board lane runs `board_cards.py sweep --lane <lane>` every cycle.
  The sweep removes each card whose event happened and posts a withdraw record naming the
  outcome.
- Curate by feature. Group rows and cards by feature with one line on how the
  pieces relate, merge duplicates, and retire landed or superseded items.
- What waits on him is what `cci owner-waits --drive <drive>` prints: each board row
  awaiting his tap at the head it names and each unanswered pick. The root quotes those
  lines, never a runner note. `cci owner-waits --drive <drive> --pr <n>` says what a PR
  not on the board waits on. The root's Stop hook checks every reply that says something
  waits on his tap against it, and sends the reply back with the real state.

The board recorder, `board_watch.py`, started detached per board by the
`start_board_watch` hook, posts one `owner` record to all from lane `board-watch` for each
decision, pick, written answer, note, merge-row verdict, and round close. Each carries the
card title, block id, his words verbatim, and a tapped merge row's head. It resolves
every open ask, blocker or hold that names the block, its card or the merge row
(`merge-row-<pr>`, `merge:<pr>`). A lane parked on a card names the block or card id in
its ask and unparks on the `owner` record or its own closed ask, never on a root relay.

Each pick and written answer on the root's board also gets a lane at once. The recorder
files a `needs-lane` task on the drive's cc-notes backlog and posts an `ask` to root on
topic `needs-lane:<block>`, unless the pick answers an open ask a lane posted. The root
binds a lane by spawning one whose brief names the block or the ask, or by a lane's
`claim` naming it; the stall watch raises any such ask unbound at 15 minutes. The
recorder also posts the board's rows as a `board-rows` record and applies the landing
runner's `board-write` records, so the runner and the stall watch read the board only
from cci. `board_watch.py backfill --session <id>` records every verdict the board already
held and prints `verified`. A recorder runs the version it started with. After a release
that changes `board_watch.py`, the root ends the old recorder's `cc-present watch` child
and relaunches it from the installed version.

### Slack

The drive writes one kind of automated Slack message: the merge card in the owner's direct-message (DM)
status thread. Everything else is a post the owner named, through a Slack lane or
`comms-desk`. The root may run a write the owner approved, by a tap or an explicit
instruction that turn, with the cc-slack tools; `cc-slack dm-status` to his own DM stays
the root's. Every other Slack rule is in
[reference/slack-lane-brief.md](reference/slack-lane-brief.md).

### Reports

The root reports to the owner on milestones or when he must act, never per event. Every
milestone report and status answer is the owner report table in
[reference/owner-report-table.md](reference/owner-report-table.md). It has one row per plan
step in plain words, with what it delivers, its state, what blocks it, what we do to
unblock it, and what went wrong or slowed it. That last column is never blank. Every
`READY-HELD` row names its gate and why. The live rows sit in one cc-notes doc labeled
`owner-table`. The dashboard is in
[reference/drive-dashboard.md](reference/drive-dashboard.md).

## Records

| Record | Where | Rule |
|---|---|---|
| Lane messages, decisions, heads, contracts, blockers, asks | cci, on the drive | [bus-contracts.md](reference/bus-contracts.md) |
| Owner asks | The PR ledger, as `ask/<seq>` rows | R8 |
| PR rows, holds, landings | The PR ledger | [landing.md](reference/landing.md) |
| Findings, notes, docs, investigations, logs | cc-notes | R5 |
| Standing owner rules | cc-notes answers plus a cci `go` record each | [owner-rulings.md](reference/owner-rulings.md) |
| Production operations | cc-notes log `production-operations` | R24 |
| Progress and handoffs | cc-notes progress doc | [compaction.md](reference/compaction.md) |
| Owner asks and Agent lanes in flight | The task list | Below |

### The task list

The task list holds owner asks and Agent lanes. Work routed through `desk-runner.py` lives
in its action records and is never mirrored into tasks.

- The root runs `TaskCreate` in the same turn it spawns a lane through `Agent`,
  `orca-launch.sh` or `orca worker-start`, with `owner` set to the lane, and in the same
  turn every owner ask arrives, a message queued mid-turn included.
- Only the root completes a task, and only after consuming the deliverable: the PR routed
  to the ledger, the ruling recorded, the answer relayed. A lane saying done, `READY`, `GREEN`
  or landed completes nothing, and a hook warns a lane that sets a lane-owned task to
  `completed`.
- A maintenance lane is the exception. Its brief or spawn prompt carries `Role: maintenance`
  or `ccx: role=maintenance`. It may complete or delete tasks, close ledger rows
  (`ledger.py drop|answer|unregister|unadmit|gone`), and retire registry lanes
  (`drive_supervisor.py request done <lane> --config <lr.json> --by <its own lane>`). The
  hook admits its `TaskUpdate` and posts a cci `applied` record `MAINTENANCE <lane> <action>`
  naming each task, row or lane it closed.
- At every handoff, milestone report and compaction, the root reconciles the list: every
  `in_progress` task has a working lane, and every running lane has an open task. It
  completes what was consumed and re-owns or deletes the rest. Every lane receives the
  live task list on every wake.
- The `task_list` hooks nudge when a spawned lane has no task by turn end, when a lane's
  done report names a task still `in_progress`, and when an owner ask has no task after
  three tool calls or by turn end. The owner-ask nudge skips when a `TaskCreate` happened in
  the last 10 minutes and fires at most once an hour. Every 20 turns they check for drift
  and busy lanes with no task. Lane nags share a limit of one per lane per hour, and a
  lane that reported done twice is never nagged again. Done reports share one line listing
  the lanes, and the drift line names the lanes. Treat each nudge as a task write due now.
- In a live drive, `task_archive` moves completed tasks older than two hours to the list's
  `.archive.ndjson` on `SessionStart`, `PreCompact`, and at most every 30 minutes on
  `Stop`. That keeps Claude Code's task reminder small; it reprints the live list every 10
  assistant messages without a task write, to the root and each in-process teammate. The
  owner's switch for that reminder is `CLAUDE_CODE_TODO_REMINDER_MODE=off` in the `env` block of
  `~/.claude/settings.json`.

## Start and resume a drive

```sh
LEDGER=$(ledger.py init --title "desk: $DRIVE")
drive.py start --ledger "$LEDGER" [--orca-run <run>] [--state-dir ~/.claude/scratch/<slug>] [--program <plan slug>] [--register-lane <lane>]
cci subscribe --drive <drive> --reader root --kind incident,decide,ask,defect,blocker --follow oncall=incident
```

1. Start the orca runner before the first Orca worker
   ([reference/orca-desk-brief.md](reference/orca-desk-brief.md#configure-and-start-the-runners)).
2. Spawn `landing-desk` and start the landing runner before any lane that opens a PR,
   whenever three or more lanes ship through one queue or the drive outlives one
   context window ([reference/landing.md](reference/landing.md)).
3. Start the standing desks as the table in [Who does what](#who-does-what) says.
4. Every lane registers its branch prefix at spawn with
   `ledger.py register --lane <lane> --branch-prefix <prefix>`.

A resumed root in a new session runs `drive.py start --drive <id> --ledger <id>` to join
the drive; `drive.py end` runs only when the drive is over. After a compaction, resume or
relaunch, the root catches up with `cci digest --drive <drive>`, then
`cci tail --drive <drive> --cursor root`, then
`cci tail --drive oncall --kind incident --cursor root-oncall`. A root whose subscription
predates `--follow` re-runs the subscribe line. Nobody tails, seds or greps whole inbox
files; instead, a new lane orients with `cci digest --drive <drive>`, and compaction itself is in
[reference/compaction.md](reference/compaction.md).

Every script in `scripts/` is on PATH by name through the plugin's `bin/`, and `orca-launch.sh` puts the same `bin/` on PATH for an incident worker's codex terminal, so briefs call
`ledger.py` or `orca-launch.sh`, never a path. A script run by path from an older version
re-runs the installed copy with the same arguments, unless a process of its own
version started it.

## Scripts by verb

| Script | What it does |
|---|---|
| `drive.py` | Starts, joins and ends a drive (`start`, `end`), registers desks (`desk`), prints the drive's digest (`digest`), shows the code a lane holds (`lane-status`), and puts a question card on the board (`ask-owner`) |
| `desk-runner.py` | Runs the orca runner and the landing runner via `run --desk orca\|landing`, edits `runner.json` safely (`config set\|unset`), and hands a live runner to a new version (`handoff`) |
| `desk-launch.py` | Posts every keyed orca-desk line (`incident`, `lane`, `relay`, `hold`, `withdraw`), waiting for the runner's verdict on a launch |
| `ledger.py` | Keeps the PR ledger: asks, reports, registrations, refreshes, reconciles, labels, holds, summaries ([landing.md](reference/landing.md)) |
| `mq.py` | Steers the merge queue: `why`, `status`, `gate`, `enqueue`, `hold`, `lift`, `priority`, `dequeue`, `requeue`, `recover` (root only) |
| `merge_queue.py` | Holds the queue's repository adapters and ejection kinds |
| `remote-restack.py` | Restacks a PR and every open PR below it on a Sprite |
| `remote-vet.py` | Builds and vets a lane's change on a Sprite |
| `sprite-job.sh`, `sprite-cargo.sh` | Run a desk job or a cargo build on a Sprite |
| `incident.py` | Runs the incident executor with `open`, `run`, `brief`, `grant`, and `note` |
| `actions.py` | Records every side effect before it runs, so a restart never repeats one |
| `bus.py` | Posts and reads the incident executor's lane bus over cci |
| `monitor-watch.py` | Reads Datadog monitors and posts alert lines for the ones that transition |
| `finder.py`, `fidelity.py` | Run one finder or fidelity grade of one head |
| `rulings.py` | Matches, classifies and checks owner rulings with `match`, `classify`, `check-pr`, `shapes`, and `shape` |
| `standing.py` | Reads live standing rules from cci and lints their handoff |
| `judge_watch.py`, `judge-eval.py` | Judge posted work against the owner's words; score judge backends by hand |
| `board_watch.py` | Records every owner tap on a board as a cci record |
| `board_cards.py` | Records the event that ends a card (`until`) and withdraws every card whose event happened (`sweep`) |
| `owner_waits.py` (`cci owner-waits`) | Prints what waits on the owner, and what a PR off the board waits on |
| `owner_update.py` | Prints every ask, task, delivery, board row and incident an owner update must cover |
| `findings.py` | Keeps every sweep finding and owner pick open until a head or landing names its fix |
| `stall_watch.py` | Finds work that stopped moving and posts one `urgent` record per stall to its owner |
| `prompt_watch.py`, `prompt_screen.py` | Find every terminal stopped at a prompt and tell whoever can act |
| `drive_supervisor.py` | Runs the lane supervisor that keeps every lane alive |
| `label-watch.sh`, `red-sweep.sh` | Enqueue ready stacks without a desk; report heads stalled red |
| `inboxes.py`, `desk-wait.sh`, `inbox-digest.py`, `inbox-rotate.py` | Wait on, digest and rotate desk inboxes and mailboxes |
| `handoff.py`, `progress.py` | Generate and lint a drive's progress record, and cap its generated sections |
| `plan-table.py`, `plans.py` | Render the owner report table, and roll registered tasks up into plan steps |
| `replay.py` | Captures a reported defect as a fixture, replays a drive's last day on install, and counts recurrences |
| `endstate.py` | Grades a release's end state before the walker cuts again |
| `orca-launch.sh`, `orca-check.sh` | Launch an Orca worker; wait on and acknowledge Orca messages |
| `pr-prose.py` | Writes a PR title, body and commit message for a lane on a model that writes no prose |

## Before every tool call

- When a lane could return the answer in ten lines, or when it is raw data, delegate it (R1, R2).
  Never open a lane's transcript, launch receipts or runtime listings; the supervisor
  relaunches dead lanes, and the root moves a lane's PRs only with
  `drive_supervisor.py request owner <lane>`.
- When a lane already owns the question, use a scoped resume and do not look.
- When about to wait, fold the wait into the lane that acts (R3).
- When about to restate status, send nothing.
- On a new owner ask, create a ledger row, its own lane (R6, R8), and a task this turn.
- Before reporting, run `owner_update.py`, dispatch every unstarted ask, read the
  dashboard and board as he will, fix what is wrong, then send the owner table.
- Before stating a PR's state or relaying an ETA, read it first, and label a green priority
  PR now (R7, R13).
- When a tool refuses, falls back to `# ccx:raw`, or needs a hand step, spawn a tooling lane this
  turn (R14).
- When something goes wrong, or the owner corrects or repeats himself, post the
  mechanism and the PR that prevents it before the turn ends.
- When a production alert is active, run `incident.py open` and `run` this turn, before anything
  else (R16).
- When the owner pastes a Slack link, spawn the Slack lane and doing lane this turn (R20).
- Before asking the owner something, check the authorities first (R19); ask only the rest,
  on the board.
- When the owner rules or repeats a ruling, record the words verbatim, confirm the implications, and apply them
  to every live artifact and lane (R19b).
- Before replying, use Pacific times, plain words, and own my own delays (R21).
- When a subagent finishes, or before spawning, run `TaskStop` on finished agents (R23).
- When anyone runs a production operation, write its log line this turn (R24).
- Anything not yet live and checked is not done.

A call that survives the list decides something no lane can decide. Everything else is a
lane.
