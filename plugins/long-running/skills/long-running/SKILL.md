---
name: long-running
description: How the root of a multi-lane drive works without becoming its bottleneck. The root decides what only it can decide, spawns a lane for everything with a body, and relays what the machinery produces; landing, grading, lane liveness, incident spawn, release tracking and owner updates run as processes that reach the root only with a decision to make. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
---

# Long-running orchestration

A drive's root is a serial decision point. Every read it makes and every decision
routed through it waits in a queue with no clock, and when the root saturates the
work does not slow down, it stops in silence until the owner notices. On Nov 2 2026
that queue held eleven approved PRs for hours, an alert for eight minutes, a ready
owner update for 25 minutes, a dead standing lane for ten hours, and a landed fix
unreleased for three. This skill exists so none of that reaches the root's queue.

The root is two sessions. The session the owner types in is the owner relay: it
answers him, queues his words for the coordinator over cci, and relays the
coordinator's reports and the dashboard back. The coordinator is a lane that posts
as `root`, reads every record addressed to `root`, and does the three things a
root does: it spawns a lane for every ask with a body the turn it arrives, it
decides what a process escalated, and it relays what the desks produce. The owner's
message never lands in the coordinator's thought mid-turn, and the coordinator's
work never waits behind an answer to him. Everything else is a verb a script
answers or a process that runs without either session. A rule here is not advice
for a careful root; it names the verb or the process that makes the failure
impossible, and a rule with neither is a defect to fix in the pack.

This skill sits on `~/.claude/CLAUDE.md`. Fan-out shape, model routing and
verification depth come from there.

## Contents

| Section | What it answers |
|---|---|
| [When this applies](#when-this-applies) | Is this a drive? |
| [Judgment first](#judgment-first) | How to read every rule below |
| [Two sessions: the owner relay and the coordinator](#two-sessions-the-owner-relay-and-the-coordinator) | Who talks to the owner, who runs the drive |
| [What the root decides, and nothing else](#what-the-root-decides-and-nothing-else) | The root's whole job |
| [Ask the verb, never read](#ask-the-verb-never-read) | Every question the root has, and the command that answers it |
| [What runs without the root](#what-runs-without-the-root) | The processes, what each owns, and what reaches the root |
| [The rules](#the-rules) | R1 to R24, each one paragraph, ids stable |
| [Who does what](#who-does-what) | Every desk and lane, and its brief |
| [The owner's surfaces](#the-owners-surfaces) | The board, Slack, updates |
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
| Releases from a drive | [reference/release-lanes.md](reference/release-lanes.md) |
| Slack | [reference/slack-lane-brief.md](reference/slack-lane-brief.md) |
| Owner rulings, the register, judges | [reference/owner-rulings.md](reference/owner-rulings.md) |
| Stall, prompt and label watches, API budgets, the lane supervisor | [reference/watchers.md](reference/watchers.md) |
| Compaction and progress docs | [reference/compaction.md](reference/compaction.md) |
| The owner update and its table | [reference/owner-report-table.md](reference/owner-report-table.md), [reference/drive-dashboard.md](reference/drive-dashboard.md) |
| What went wrong before, by date | [reference/lessons.md](reference/lessons.md) |

## When this applies

- Three or more lanes in flight at once.
- A drive expected to outlast one context window.
- Any wait on an external system: CI, a merge queue, a cloud apply, a soak.
- Any bring-up, migration, sweep or audit whose units are many and independent.

A single-lane investigation is not a drive. One question goes to one subagent in
direct mode.

## Judgment first

The rules protect the root's context and the deliverable. Apply each for that
purpose, never as a literal script.

- No rule forbids reading the one line a decision turns on. The root reads that
  line and decides.
- A finding in hand is a fix. Apply it; it does not become a lane, a re-audit or a
  workflow.
- Read each owner sentence as a careful colleague would, for what it means in
  context. A correction names a defect in the root's reading; it never licenses a
  redesign of something he ruled on earlier. Before acting on a correction, read
  the register's rows on that subject and act inside them.
- An owner repeat is a defect report about the first reading. Find every place
  the earlier reading survived and fix them all that turn.
- A tooling refusal of a legitimate action is fixed forward in a side lane the
  same turn, never an excuse and never a question for the owner.
- The pack's hooks warn. They block only on trust: a merge without the owner's
  approval, a change to who approves landings, a revert or rollback without his
  word, hook-file edits, and the session-protection guards.

When 30 minutes have passed since the last owner prompt, the pack re-injects these
bullets with two of the owner's sentences. `judgment_reminder_minutes` in the
drive's config changes the interval.

## Two sessions: the owner relay and the coordinator

The owner ruled on Nov 2 2026 that one thread cannot both receive his messages and
route every lane: each message sidetracked the coordination, and each coordination
turn delayed his answer. So a drive has two root sessions, and the word `root` in
every rule below names the coordinator unless the rule says the relay.

**The owner relay** is the session he types in. Its whole job is him.

- It answers every owner message that turn: the "why" with the mechanism, the
  pick on his board, the Pacific time from `date` (R19, R21).
- It appends his words verbatim to the drive's rulings file and posts them to the
  coordinator as one `cci post --lane owner-relay --kind ask --to <coordinator>`
  per message or batch, never as a `SendMessage`, so the coordinator reads them at
  its next tool call and its turn is never restarted.
- It relays what the coordinator addresses to it (`--kind report --to owner-relay`)
  and the progress desk's update, unchanged and the moment it arrives.
- It spawns the coordinator at drive start from
  [reference/coordinator-brief.md](reference/coordinator-brief.md), as a
  `long-running:lane` on opus with `run_in_background`, and spawns nothing else.
  It posts no `go`, `hold`, `decide` or `launch` of its own; a relay that does is
  coordinating, and the pack warns.

**The coordinator** is a lane that posts as `root` and reads reader `root`. It
runs in a foreground `cci watch` loop for the life of the drive (R3) and holds
everything below: lanes, gos, holds, incidents, the task list, progress docs and
the register. It never posts to Slack or the board and never speaks to the owner;
everything he must see is a `report` to `owner-relay`. When it dies, the lane
supervisor relaunches it from its brief and newest handoff like any lane, and the
relay keeps answering the owner meanwhile. The relay and the coordinator never
`SendMessage` each other: the bus is the only channel between them, and a record
survives a compaction where a message does not.

## What the root decides, and nothing else

1. **An owner message.** The relay records his words verbatim and queues them to
   the coordinator; the coordinator applies each ruling to every live lane and
   artifact the turn it reads them (R17, R18, R19b). The relay answers a "why" with
   the mechanism of the root's own failure (R21) and puts a genuine pick on his
   board (R19). Neither asks what the plan, the register or an earlier pick already
   settles.
2. **A new ask gets a lane.** Every owner ask with a body becomes its own lane the
   turn it arrives, with a ledger row and a task (R6, R8). A lane holding more
   than two unstarted items is split. The root writes the brief and launches;
   it does none of the work.
3. **A decision a process escalated.** The supervisor, the landing runner, the
   stall watch and the update generator reach the root only with a `decide`,
   `ask`, `blocker` or `incident` record. The root answers each with one record
   and names the lane that acts. A resolution that names no fixer, no `FIX-LIVE`
   and no stated reason is refused by the pack.
4. **A conflict between two owner rules.** The root tells him in one line which
   rule it applies and why, and proceeds.
5. **The relay.** When the progress desk posts an owner update, or the coordinator
   posts a `report` to `owner-relay`, the relay sends its path or its text
   unchanged, the moment it is ready. When a tap card is drawn, nobody says
   anything; the runner lands on the tap.

Anything that is not on this list is a lane or a verb. The root's own work on a
deliverable, its own reads past one bounded window, and its own waits are each a
defect the hooks name.

## Ask the verb, never read

Every question the root has already has an answer it can ask for. The
`root_context` and `root_reads` hooks warn when the root reads instead.

| The root wants to know | It runs |
|---|---|
| Is a lane alive, and what did it last say | `drive_supervisor.py lanes <lane> --config <lr.json>` |
| Every lane, its state, PRs and standing duty | `drive_supervisor.py lanes --config <lr.json>` |
| What the drive has done since it last looked | `cci digest --drive <drive>`, then `cci tail --drive <drive> --cursor root` |
| What waits on the owner right now | `cci owner-waits --drive <drive>` |
| The state of PRs it must name | `ccx vcs pr status <n1> <n2> ...`, batched, never one at a time |
| Every open ask, its state, what is landed and not live | `ledger.py summary --drive <drive>` |
| The owner update, complete and from sources | `owner_update.py --render --drive <drive>`, run by the progress desk; the root relays its output |
| What an incident is doing | `incident.py status <slug>` |
| Where a lane's mail goes | `drive_supervisor.py route <lane> --config <lr.json>` |
| Whether an install may run now | `drive.py digest --quiet-point` |

A read the table does not name belongs to a lane: a PR, a diff, a CI log, a Slack
thread, a plan artifact, a long file, a cloud listing, a build page. The root
spawns a reader and takes back at most five lines. R1 and R2 say what the hooks
allow for the one line a decision turns on.

## What runs without the root

| Mechanism | Process | What it owns | What reaches the root |
|---|---|---|---|
| Landing | `desk-runner.py run --desk landing`, and the v2 landing mover in `drive_supervisor.py` | Every open PR of the drive: gates at the current head each pass, enqueues a PR the owner tapped or a go-carrying exempt class, restacks, resubmits an eviction, routes a red to its lane. Holds come only from `--kind hold` records. | A PR with every gate and no tap or go, once per pass, as a `landing-state` line; nothing else |
| Grading | finder and fidelity desks from the orca runner | A finder grade and a fidelity grade of every head within ten minutes | A verdict the lane rebuts or accepts, never the root |
| Lane liveness | the lanes mover in `drive_supervisor.py`, under launchd | The registry: one row per lane, relaunch of a dead lane from its brief and newest handoff, rotation in place at the context line, a `Standing:` duty with no live owner for ten minutes | One `blocker` per standing duty with no owner; one `defect` per lane nothing proves dead |
| Incident spawn | the supervisor's Alerts check | A Datadog or Sentry alert with no `incident` record and no fix lane after three minutes: the supervisor launches the fix and evidence lanes itself and posts the spawn | The spawn record; a `blocker` only when the launch fails |
| Landed, not live | `ledger.py summary` and the supervisor | A landed PR whose release target has shipped nothing since, after an hour | One `blocker` per target with the exact `tools/ci release start --only <target> --manual` line |
| Stalls | `stall_watch.py` | Work that stopped moving: one `urgent` record per stall to its owner, read from the registry, never a tail | Only a stall whose owner is the root |
| The owner's taps | `board_watch.py` per board | Every tap, pick and write-in as an `owner` record; the runner lands on it | Nothing; a pick with no lane at 30 minutes comes back as an `ask` |
| The owner update | `owner_update.py --render`, run by the progress desk | Every plan step, owner-table workstream, live lane under its step, open ask, pick, incident and release today, PR with a go owed or a hold; every row with who is on it or **nobody**, what blocks it, what unblocks it and by when, what went wrong; unowned work first; exit 1 naming anything missing | The rendered update, to relay unchanged |
| The handoff | the compaction hook | The pre-compaction progress doc, with the registry dump and the rendered update | Nothing to write but the root's own narrative |

A mechanism that does not run is a defect of the drive, not a chore for the root.
When the root finds itself doing a row's work by hand, the turn's deliverable is
the fix to that process (R14).

## The rules

Each rule is stated once. Ids are stable; scripts, hooks and other files cite them.

**R1. Take ground truth from the owning lane or the verb.** Once a lane or a verb
can answer a question, the root never greps a log, lists cloud resources, curls an
API, opens a build page or parses JSON. It asks and takes back at most five lines.
Silence is not progress. Priority PR state is the exception: the root reads it in
one batched `ccx vcs pr status` call.

**R2. Delegate anything with a body.** A read past one bounded window, a log, a
multi-step investigation, PR or merge mechanics and a bulk enumeration each belong
to a lane. The `root_context` hook warns on every read of the kinds its message
names and allows one bounded read of one named file, a Read window, `head -n`,
`tail -n`, `sed -n 'A,Bp'` or `ccx code read --section` up to 150 lines. The
`root_reads` hook warns when the root opens a second source for one owner ask
instead of relaying the progress desk's update, and when it pipes a lane's records
through `tail` or `grep` instead of the `lanes` verb.

**R3. One lane per wait-then-do chain, and no lane parks.** "When X lands, do Y" is
one sequencer lane that polls X, does Y and reports once. Never a Monitor per
build or a root poll. A lane that ends its turn waiting is a bug: lanes poll in
foreground loops to a terminal state. Waiting mechanics are in
[reference/bus-contracts.md](reference/bus-contracts.md).

**R4. Never re-brief a lane, never answer a duplicate.** A `SendMessage` to a
finished agent re-runs its whole brief; to a running one, it is queued until the
turn ends. Reply to nothing already acted on. `desk-wait.sh` marks printed mail
read. Rotation flushes a lane in place
([reference/watchers.md](reference/watchers.md#supervise-and-rotate-lanes)).

**R5. Write it down, do not hold it.** Findings go to cc-notes from the lane that
found them. A lane writes with `ccn log append`, `ccn note add`, `ccn doc add` and
`ccn task add`; the root may use the cc-notes tools. An incident is an
investigation and a log through `incident.py`; a handoff is a doc; briefs are
attachments on one `briefs: <slug>` log. Nobody makes a markdown record under
`~/.claude/scratch` by hand; files stay files only where a process reads them.

**R6. Put every owner request in flight the turn it arrives.** A new lane per ask,
never an append to a busy lane's queue. Lanes whose files overlap stack, each
brief naming the files it owns. Before every milestone report, every unstarted
ask is dispatched and named.

**R7. Check a PR's state before reporting it.** Never from a message or memory.
`ccx vcs status` in the stack's worktree, or the squash commit on fetched trunk.

**R8. Record every owner ask in the ledger.** The turn it arrives:
`ledger.py ask --ledger <id> --text "<verbatim>" --lane <lane> --accept "<check>" --due <time>`.
An ask is done only at `LIVE`; before that the root says "in #N, not live yet:
`<blocker>`". `ledger.py summary --drive <drive>` is the only source for what is
assigned, in flight, landed or live; a landed PR with no release in an hour
reads `LANDED-NOT-LIVE` there and reaches the root as a blocker.

**R9. Orca worker traffic runs through `desk-runner.py`.** The root posts `launch`
and `relay` lines with `desk-launch.py` and never runs a check-and-ack loop. It
receives cci records over the cc-inbox channel, with no Monitor, after one
`cci subscribe --drive <drive> --reader root --kind incident,decide,ask,defect,blocker --follow oncall=incident`
at drive start. Records to `root` or `main` arrive whatever their kind. Skip a seq
already handled. The runner's config and passes are in
[reference/orca-desk-brief.md](reference/orca-desk-brief.md).

**R9a. An urgent hold gets a decision owner and a clock.** A lane holding an urgent
release posts `orca-desk: hold <slug> owner=<lane> :: <what, on what>` through
`desk-launch.py hold` and `unhold` when it moves. After `deadlines.hold_minutes`
the runner posts a `DECIDE hold:<slug>` line, and the root resolves it with that
lane ahead of any open owner card on another subject.

**R10. Landed is the only progress.** Status to the owner is landed, queued, live,
or the exact blocker naming the PR, its head and the gate. "Open" and "in CI" are
not status. A red gets a structural fix with a test, never a retry.

**R11. Hands off deploys, and an owner's order carries every approval.** The root
and its lanes never start, approve, watch or re-cut a release unless the owner
asked for it. When he orders a release, that order is the approval for every step
it needs: deletes, replaces and migrations its plan names are applied by the
release lane, counted in its done line, and never asked again; the
`owner_order_is_the_approval` hook refuses a root ask for a second word. An
incident release starts at once with no dry run, keeps the zero-delete and
zero-replace bound unless he ordered it, and is the smallest release that carries
the fix. No release is canceled once its apply has started. A cluster's code
removal lands and ships before the cluster is destroyed. Release lanes are in
[reference/release-lanes.md](reference/release-lanes.md).

**R12. Route every red at once.** Every `STALLED-RED` line goes to its owning lane
the same turn with a 15-minute deadline; a red with no live owner gets a lane from
[reference/red-sweep-brief.md](reference/red-sweep-brief.md).

**R13. Dispatch every owed item the same turn.** When several owed items have no
PR, the root dispatches all of them in one turn, one lane each.

**R14. A process fix is a PR the turn it is adopted.** Every tooling or process
change made mid-drive goes to a lane that PRs it into the pack that turn, drives
it through merge, release and install, and proves it on the record that reported
the defect (`replay.py capture`). A tooling defect, a refusal, a `# ccx:raw`
fallback or a repeated chore gets a tooling lane that turn. Only a tooling lane
installs a release under a live drive, once `drive.py digest --quiet-point` exits
0, and one lane installs per night so two installs never race. A lesson that
lives only in a status, a memory or a retro card is not landed.

**R15. CI is the verifier, not the machine.** No lane runs tests, builds,
typechecks or lint locally; the `local_builds` hook refuses them. A batch job
never runs on the orchestrator's machine. The one exception is a Go golden's
`go test -update` run.

**R16. An active production alert gets its executor the same turn, without the
root.** The supervisor launches the fix and evidence lanes for an alert nobody has
answered in three minutes. When an alert reaches the root first, through a link,
an `#outage` report, a page, a runner `PAGE` record, a failing release check or a
supervisor `ALERT-UNOPENED` blocker,
the root runs `incident.py open` and `incident.py run` that turn, before anything
else, with no real-or-not verdict, no `AskUserQuestion` and no mute treated as
resolution. Every fix lands forward; a rollback runs only on the owner's own word.
The rest is in [reference/active-alert-brief.md](reference/active-alert-brief.md).

**R17. Owner routing applies to the next spawn and to every live lane on that
problem.** Stand down every live lane on that problem and relaunch on the named
route the turn the instruction arrives.

**R18. A ruling reaches a lane the way it reads.** Orca lanes get
`desk-launch.py relay`; a desk gets a cci record addressed to it; a running Agent
lane gets a cci record it reads at its next tool call, and a `SendMessage` for the
turn boundary. The root confirms a stand-down on disk before treating it as done.

**R19. A settled question is applied, never asked.** Before any owner question
reaches the board, the root checks the plan, the register (`ccn doc show` of the
standing rules), `ccn answer search` and the drive's picks. An absolute owner
instruction stays absolute in every brief. **R19a.** A gate that wants an
owner-recorded answer for a settled decision is satisfied the same turn by
citing the plan line or pick. **R19b.** Every ruling binds what it implies: the
root records the words verbatim, writes the implications, confirms them with him
in chat for a few or on one card for many, and applies them to every live
artifact and lane at once. A ruling travels by its words, never by an id.
Mechanics are in [reference/owner-rulings.md](reference/owner-rulings.md).

**R20. A Slack link from the owner is an assignment.** That turn, in one batch:
the Slack lane from [reference/slack-lane-brief.md](reference/slack-lane-brief.md),
the doing lane from the link itself, one line to the owner naming both, and a task
for each. The root never reads Slack or composes Slack copy.

**R21. Talk to the owner in his terms, and own the root's delays.** Every time
shown to him is Pacific with no zone label and comes from `date` or a record,
never from the root's sense of elapsed time. A "why did you" gets the mechanism
of the root's own failure first, then the fix and the lane. Every line names the
thing, never a codename or an id. Every artifact he reads leads with the human
layer: what it changes for him, where it stands, what it needs from him.

**R22. Quote the owner's design verbatim. Ship that design or hold.** Every brief
on a subsystem quotes each owner design ruling verbatim with the entry point at
`file:line`. Before `READY-FOR-SHIP` the lane posts a `contract` record; the root
withdraws it only on a mismatch with a quoted ruling. Never a "static first" split
that changes the agreed semantics.

**R23. Stop every finished agent the turn its completion arrives.** `TaskStop`
after taking its report, and before any spawn. Live lanes and Orca terminals are
not finished agents. A testing lane under the owner's standing order is never
finished.

**R24. A production operation is logged the turn it runs.** One line in the
cc-notes log `production-operations`, label `prod-ops`: who, when, where, the
exact command, the authorizing answer, the effect. Every incident lane reads the
last 24 hours of it before naming a mechanism.

## Who does what

The root spawns a subagent for every task with a body, its own routine work
included. A lane never spawns with `Agent`; it writes the brief to a file and posts
`cci post --kind ask --to root --text 'spawn <name> brief=<path> model=<model>'`.
Implementation lanes run on Sprites through `desk-launch.py lane`; an `Agent`
spawn is for a coordination lane with no worktree. A lane that must hold the Mac
names why in its brief.

| Runner, desk or lane | What it does | Brief |
|---|---|---|
| Coordinator, posting as `root` | Everything the root decides: lanes, gos, holds, incidents, the task list, progress docs, the register; reports to the relay over cci | [coordinator-brief.md](reference/coordinator-brief.md) |
| Landing runner, `desk-runner.py run --desk landing`, and the v2 landing mover | The drive's merge queue: gates, enqueues on tap or exempt go, routes reds and conflicts, starts releases on landing | [landing.md](reference/landing.md) |
| `landing-desk` | Takes lane reports, reconciles landings, keeps the ledger | [landing-desk-brief.md](reference/landing-desk-brief.md) |
| Orca runner, `desk-runner.py run --desk orca` | Launches and relays to Orca workers, clocks incidents, starts grading jobs | [orca-desk-brief.md](reference/orca-desk-brief.md) |
| Lane supervisor, `drive_supervisor.py run` | Keeps every lane alive, relaunches, rotates, launches incident lanes for unanswered alerts, raises standing-duty and landed-not-live blockers | [watchers.md](reference/watchers.md) |
| Incident executor, `incident.py run` | Owns one incident from intake to final reply | [active-alert-brief.md](reference/active-alert-brief.md) |
| `alerts-desk` | Reports monitor transitions as `incident` records | [alerts-desk-brief.md](reference/alerts-desk-brief.md) |
| Progress desk | Runs `owner_update.py --render` and posts the update the root relays | [owner-report-table.md](reference/owner-report-table.md) |
| Priority desk | Drives one owner-named number-one outcome | [priority-desk-brief.md](reference/priority-desk-brief.md) |
| `drive-watchdog` | Watches the drive against the plan and keeps the retro log | [drive-watchdog-brief.md](reference/drive-watchdog-brief.md) |
| `red-sweep` | Owns every red with no live owner | [red-sweep-brief.md](reference/red-sweep-brief.md) |
| `finder-desk`, fidelity desks | Grade every head for defects and for fidelity to the plan | [finder-brief.md](reference/finder-brief.md), [fidelity-desk-brief.md](reference/fidelity-desk-brief.md) |
| Board refresher, `board_watch.py` | Draws the tap cards and records every tap as a cci record | [The owner's surfaces](#the-owners-surfaces) |
| `dashboard-keeper` | Keeps the drive dashboard running | [dashboard-keeper-brief.md](reference/dashboard-keeper-brief.md) |
| `user-test` | Plays the owner against every revision of a surface he will use | [user-test-brief.md](reference/user-test-brief.md) |
| Merge train, semantic collisions, sequencer | Land the hot set, catch cross-lane contradictions, run a wait-then-do chain | [merge-train-brief.md](reference/merge-train-brief.md), [semantic-collisions-brief.md](reference/semantic-collisions-brief.md), [orca-lane-brief.md](reference/orca-lane-brief.md) |
| Slack lane, `comms-desk` | Post what the owner asked to be posted | [slack-lane-brief.md](reference/slack-lane-brief.md), [comms-desk-brief.md](reference/comms-desk-brief.md) |
| Handoff subagent | Writes a rotating lane's handoff | [handoff-subagent-brief.md](reference/handoff-subagent-brief.md) |
| Stall watch, prompt watch, label watch | Find stalls and stuck prompts, land unlabelled stacks | [watchers.md](reference/watchers.md) |
| Judge pass, `judge_watch.py` | Checks contracts and relays against the owner's words | [owner-rulings.md](reference/owner-rulings.md) |

A lane that ships a PR or calls a skill is `long-running:lane-ship`; any other
lane or desk is `long-running:lane`; a lane that posts to Slack is
`general-purpose` on opus. A brief whose duty the drive keeps for good carries a
`Standing: <duty>` line, and the supervisor raises a blocker when that duty has no
live owner.

## The owner's surfaces

### The board

The root asks without parking. Every owner ask the drive blocks on is a question
card from `drive.py ask-owner`, with two to four answers and one recommended; a
per-PR merge ask is a merge row the board refresher draws from the ledger. The
`blocking_asks` hook refuses the root's `AskUserQuestion` while a lane runs.

- Every PR goes to the owner. A PR that is green, approved and graded at its head
  reaches his tap card within one runner pass, and the runner lands it on his tap.
  Only the classes he exempted land on a go, and nothing new joins them without
  his word; the `landing_approval` hook refuses a record or brief that changes it.
- A tapped PR never comes back. The tap carries to every later head; a tapped PR
  takes no behavior change, only what CI needs.
- A card he touched stays until he submits the round. Every card names the event
  that ends it (`board_cards.py until`), and the board lane sweeps ended cards.
- What waits on him is what `cci owner-waits --drive <drive>` prints; the root
  quotes those lines and never a runner note.
- A settled question never reaches the board (R19). A design doc reaches him as
  one choice card per decision, never one approve card.

`board_watch.py` posts one `owner` record per tap, pick, write-in and round close,
writes each answer as a cc-notes answer, and files a `needs-lane` ask for every
pick; a pick with no lane at 30 minutes comes back as an `ask`, at 60 as a
`blocker`.

### Slack

The drive writes one automated Slack message: the merge card in the owner's direct-message
status thread. Everything else is a post he named, through a Slack lane. The root
never reads Slack. The rest is in
[reference/slack-lane-brief.md](reference/slack-lane-brief.md).

### Updates

Every update the owner gets is `owner_update.py --render`, run by the progress
desk and relayed by the root unchanged: a path or a paste, the moment it is
ready, never rewritten and never assembled from memory. Its sections are derived,
never listed by hand; every row carries who is on it right now or **nobody**,
what blocks it, what unblocks it and by when, and what went wrong; unowned work
is listed first; the generator exits 1 naming any live lane, open ask or open PR
the text lacks. The pack nudges a long root reply that lacks a derived section or a
live lane. The columns are in
[reference/owner-report-table.md](reference/owner-report-table.md).

## Records

| Record | Where | Rule |
|---|---|---|
| Lane messages, decisions, heads, contracts, blockers, asks | cci, on the drive | [bus-contracts.md](reference/bus-contracts.md) |
| Lane liveness, PRs, standing duties | The supervisor's registry, `drive_supervisor.py lanes` | [watchers.md](reference/watchers.md) |
| Owner asks | The PR ledger, as `ask/<seq>` rows | R8 |
| PR rows, holds, landings, landed-not-live | The PR ledger | [landing.md](reference/landing.md) |
| Findings, notes, docs, investigations, logs | cc-notes | R5 |
| Standing owner rules | The register doc, cc-notes answers, one cci `go` each | [owner-rulings.md](reference/owner-rulings.md) |
| Production operations | cc-notes log `production-operations` | R24 |
| Progress and handoffs | cc-notes progress doc, generated | [compaction.md](reference/compaction.md) |
| Owner asks and Agent lanes in flight | The task list | Below |

### The task list

The task list holds owner asks and Agent lanes; work routed through
`desk-runner.py` lives in its records. The root runs `TaskCreate` the turn it
spawns a lane or an owner ask arrives, and only the root completes a task, after
consuming the deliverable. A maintenance lane (`Role: maintenance`) may complete
tasks and retire registry lanes, posting an `applied` record for each. At every
handoff and compaction the root reconciles the list against the registry, never
against memory. The `task_list` hooks nudge on a lane with no task and an ask with
no task; `task_archive` keeps the list small.

## Start and resume a drive

```sh
LEDGER=$(ledger.py init --title "desk: $DRIVE")
drive.py start --ledger "$LEDGER" [--orca-run <run>] [--state-dir ~/.claude/scratch/<slug>] [--program <plan slug>]
cci subscribe --drive <drive> --reader owner-relay --kind report,ask
drive_supervisor.py install --config <state dir>/lr.json
```

The relay then spawns the coordinator from
[reference/coordinator-brief.md](reference/coordinator-brief.md); the coordinator
runs its own
`cci subscribe --drive <drive> --reader root --kind incident,decide,ask,defect,blocker --follow oncall=incident`
and the steps below.

1. Start the orca runner before the first Orca worker
   ([reference/orca-desk-brief.md](reference/orca-desk-brief.md#configure-and-start-the-runners)).
2. Spawn `landing-desk` and start the landing runner before any lane opens a PR
   ([reference/landing.md](reference/landing.md)).
3. Start the standing desks from [Who does what](#who-does-what), each with a
   `Standing:` line in its brief.
4. Every lane registers its branch prefix at spawn with
   `ledger.py register --lane <lane> --branch-prefix <prefix>`.

A resumed relay joins with `drive.py start --drive <id> --ledger <id>` and checks
that the coordinator is live with `drive_supervisor.py lanes <coordinator>`. A
resumed or relaunched coordinator catches up with `cci digest --drive <drive>`, `cci tail --drive <drive> --cursor root`
and `drive_supervisor.py lanes --config <lr.json>`; after a compaction it answers
every unresolved record from `cci tail --since <seq> --reader root --kind decide,ask,blocker,hold,incident`
before any other work. Compaction is in
[reference/compaction.md](reference/compaction.md).

Every script in `scripts/` is on PATH by name through the plugin's `bin/`. A script
run by path from an older version re-runs the installed copy, unless a process of
its own version started it. The store config `lr.json` is read by every installed
version, so an older process survives a newer install.

## Scripts by verb

| Script | What it does |
|---|---|
| `drive.py` | Starts, joins and ends a drive, prints its digest and quiet point, puts a question card on the board (`ask-owner`) |
| `drive_supervisor.py` | Runs the lane supervisor (`run`, `install`), answers liveness (`lanes [NAME ...]`), routes mail (`route`), changes a registry row (`request`) |
| `desk-runner.py` | Runs the orca and landing runners (`run --desk orca\|landing`), edits `runner.json` (`config`), hands a live runner to a new version (`handoff`) |
| `desk-launch.py` | Posts every keyed orca-desk line (`incident`, `lane`, `relay`, `hold`, `withdraw`) |
| `ledger.py` | Keeps the PR ledger: asks, reports, registrations, holds, landed-not-live, `summary` |
| `owner_update.py` | Renders the owner update from sources (`--render`) and checks a written one |
| `owner_waits.py` (`cci owner-waits`) | Prints what waits on the owner |
| `incident.py` | Runs the incident executor: `open`, `run`, `brief`, `grant`, `note`, `status` |
| `mq.py`, `merge_queue.py` | Steer the merge queue and hold its adapters |
| `remote-restack.py`, `remote-vet.py`, `sprite-job.sh`, `sprite-cargo.sh` | Restack, vet and build on a Sprite |
| `finder.py`, `fidelity.py` | Grade one head |
| `rulings.py`, `standing.py`, `judge_watch.py`, `judge-eval.py` | Match, check and judge against owner rulings |
| `board_watch.py`, `board_cards.py`, `board_rows.py` | Record taps, end cards, draw merge rows |
| `stall_watch.py`, `prompt_watch.py`, `prompt_screen.py` | Find stalls and stuck prompts |
| `handoff.py`, `progress.py`, `compact_job.py` | Generate and lint the compaction handoff |
| `replay.py` | Captures a reported defect as a fixture and replays a drive's last day on install |
| `actions.py`, `bus.py`, `monitor-watch.py`, `findings.py`, `endstate.py`, `retro.py` | Record side effects, post on the bus, read monitors, keep findings, grade end states, keep retro rows |
| `orca-launch.sh`, `orca-check.sh`, `worker-launch.sh`, `orca-gc` | Launch, check and clean up Orca workers |
| `inboxes.py`, `desk-wait.sh`, `inbox-digest.py`, `inbox-rotate.py` | Wait on, digest and rotate desk mailboxes |
| `plan-table.py`, `plans.py`, `pr-prose.py`, `lane_brief.py` | Render plan tables, write PR prose, render briefs |

## Before every tool call

- Is there a verb for this? Run it ([Ask the verb](#ask-the-verb-never-read)).
- Does it have a body? Spawn the lane (R2, R6), with its task and ledger row.
- Is it a wait? Fold it into the lane that acts (R3).
- Is it status? Relay the progress desk's update, or send nothing (R10).
- Is it an owner message? Record the words, apply the ruling everywhere, answer
  a "why" with the mechanism, put only a genuine pick on the board (R17 to R21).
- Is it an alert the supervisor has not answered? `incident.py open` and `run`
  now (R16). A Slack link? Both lanes now (R20).
- Did a tool refuse, or did something go wrong twice? The fix PR is this turn's
  deliverable (R14).
- Am I about to approve, hold, start or cancel something? The owner's order
  carries it, or a lane owns it; the root does neither by hand (R11).
- Finished agents stopped (R23); a production operation logged (R24); nothing
  called done that is not live and checked.

A call that survives the list decides something no verb and no lane can decide.
Everything else is a lane.
