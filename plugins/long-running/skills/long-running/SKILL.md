---
name: long-running
description: How a multi-lane drive runs without a root as its bottleneck. The session the owner types in is the owner bridge; a sibling Orca session bound as lane root is the coordinator that runs the drive; a standing meta lane takes every skill, tooling and process improvement, and a standing signal lane points the communicator at what matters in the coordinator's feed; every question has a verb of the drive tool lr; landing, grading, lane liveness, incident spawn, release tracking and owner updates run as processes that reach the coordinator only with a decision to make. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
---

# Long-running orchestration

A drive's root is a serial decision point: every read it makes and every decision
routed through it waits in a queue with no clock, and when it saturates the work
stops in silence until the owner notices. On Nov 2 2026 that queue held eleven
approved PRs for hours, an alert for eight minutes, a ready update for 25 minutes,
a dead standing lane for ten hours and a landed fix unreleased for three, and each
owner message interrupted the coordination that would have moved them. This skill
exists so none of that reaches a root's queue again.

The root is two sessions, and the whole skill is built around that split. The
**owner bridge** is the session the owner types in; its whole job is him. The
**coordinator** is a sibling Orca session the bridge launches at drive start,
bound as lane `root`, and it does the three things a root does: it spawns a lane
for every ask with a body the turn it arrives, it decides what a process
escalated, and it relays what the desks produce. Two standing lanes start beside
them: the **meta lane** takes every improvement to this skill and its tooling, so the
coordinator owns drive work only, and the **signal lane** tails the coordinator, so
the communicator (the owner bridge) never has to. Everything else is a verb of the drive tool `lr` or a
process that runs without any of them. A rule here names
the verb or the process that makes a failure impossible; a rule with neither is a
defect to fix in the pack.

This skill sits on `~/.claude/CLAUDE.md`, which owns fan-out shape, model routing
and verification depth. The pack is one Go binary, `lr`, with the Python scripts
it replaces kept only until each verb goes live
([design](https://github.com/Forge-AI/design-docs/tree/main/design-docs/long-running-v2)).

| Topic | File |
|---|---|
| Landing and the merge queue, D1 to D22 | [reference/landing.md](reference/landing.md) |
| Grading heads, landings and plans | [reference/grading-principles.md](reference/grading-principles.md) |
| The orca runner and Orca workers | [reference/orca-desk-brief.md](reference/orca-desk-brief.md), [reference/orca-workers.md](reference/orca-workers.md) |
| Writing a lane brief; the briefs of the coordinator, the meta lane and the signal lane | [reference/orca-lane-brief.md](reference/orca-lane-brief.md), [reference/coordinator-brief.md](reference/coordinator-brief.md), [reference/meta-lane-brief.md](reference/meta-lane-brief.md), [reference/signal-lane-brief.md](reference/signal-lane-brief.md) |
| cci records and the lane bus | [reference/bus-contracts.md](reference/bus-contracts.md) |
| Incidents, releases, Slack | [reference/active-alert-brief.md](reference/active-alert-brief.md), [reference/release-lanes.md](reference/release-lanes.md), [reference/slack-lane-brief.md](reference/slack-lane-brief.md) |
| Owner rulings, the register, judges | [reference/owner-rulings.md](reference/owner-rulings.md) |
| The lane supervisor and the watches | [reference/watchers.md](reference/watchers.md) |
| Compaction and the owner update | [reference/compaction.md](reference/compaction.md), [reference/owner-report-table.md](reference/owner-report-table.md) |
| What went wrong before, by date | [reference/lessons.md](reference/lessons.md) |

## When this applies

Three or more lanes at once, a task that outlives one context window, a wait on
CI, a queue, an apply or a soak, or a sweep over many independent units. A
single-lane investigation is one subagent in direct mode, not a drive.

## Judgment first

- No rule forbids reading the one line a decision turns on.
- A finding in hand is a fix, never a lane, a re-audit or a workflow.
- Read each owner sentence as a careful colleague would. A correction names a
  defect in the reading; it never licenses a redesign of something he ruled on
  earlier. Before acting on one, read the register's rows on that subject.
- An owner repeat is a defect report about the first reading: fix every place
  the earlier reading survived, that turn.
- A tooling refusal goes to the meta lane the same turn and the work proceeds on the
  hand path; it is never an excuse and never a question for the owner.
- Hooks warn. They block only on trust: a merge without the owner's approval, a
  change to who approves landings, a revert without his word, hook-file edits and
  the session-protection guards.

## Phase 0: the standing sessions

**Binding.** Every session of a drive is bound to one lane name, and every hook,
stop check, injection and cci delivery keys on that binding and nothing else. The
bridge's window is bound `owner-relay`. The coordinator's window is bound `root`,
so every root hook (`root_context`, `root_reads`, `owner_facing`, `task_list`, the
stop checks, the compaction handoff, the register injection) and every record
addressed to `root` lands on the coordinator and none of it on the bridge. The pack's hooks
come in three tiers on one switch, which `/long-running:coordinator` sets for the
session that runs it. Shared hooks (context discipline: `root_context`, `root_reads`)
act in both windows. Coordinator hooks (asks, retro rows, coverage, landing, launch,
incident checks, the task list, the compaction handoff) act only in the session that
ran `/long-running:coordinator`. Communicator hooks (plain words, owner words and picks,
claims to the owner, confirm-back, failure reports that name what stops a
recurrence) act only in the window that ran `/long-running`
alone. The coordinator skill also claims cci lane `root` with `cci announce --lane
root`, so cci's stop check and injections follow it; the communicator claims
`owner-relay` with its subscribe line. A lane's window, the meta and signal lanes' included, is bound to its lane name and gets the lane hooks. Hooks are window-scoped,
so the coordinator is never an in-process subagent of the bridge: it is a sibling
Orca session, launched by the bridge, that the bridge never `SendMessage`s and that
never `SendMessage`s the bridge. The bus is the only channel between them, because
a record survives a compaction where a message does not.

**The owner bridge** answers every owner message that turn, in his terms: the "why"
with the mechanism of the drive's own failure, the pick on his board, the Pacific
time from `date`. It appends his words verbatim to the drive's rulings file and
posts them to the coordinator as one
`cci post --lane owner-relay --kind ask --to root` per message or batch, so the
coordinator reads them at its next tool call and its thought is never interrupted.
A message about the skill, its tooling, a prompt, a hook or the process goes to the
meta lane instead, verbatim, as `--kind ask --to meta`, and so does a tooling defect the
coordinator reports.
It is the drive's presentation layer: it reads what the coordinator addresses to it
(`--kind report --to owner-relay`) the moment it arrives and synthesizes what he
would want from it, and it never forwards the coordinator's text as written. Only
the communicator owes him his own terms (R21): plain words, Pacific times, the thing's
name never a codename or id, the mechanism of the drive's failure before the fix,
the human layer before the agent layer
([owner-bridge-brief.md](reference/owner-bridge-brief.md)). It owns the dashboard and the owner updates:
one generator (`lr update render`) feeds both from the same rows, run by its one
standing helper `update-desk`, in the format the owner's words on updates, work
tracking and blockers set ([owner-report-table.md](reference/owner-report-table.md),
[owner-bridge-brief.md](reference/owner-bridge-brief.md)). The communicator talks only to
the coordinator, the meta and signal lanes and helpers it spawned itself for its own work; it never addresses the coordinator's children or the meta lane's, and beyond
the three launches at drive start it posts no `go`, `hold`, `decide` or `launch`. Every owner message lands in the bridge's message ledger as it
arrives, and the bridge's turn cannot end while a row is not marked responded or
addressed (`lr owner mark <id> responded|addressed`). The bridge also proposes: when what it relays shows
a structural fault, such as PRs queued behind one coordinator decision, it brings him
the split as an option, not only the symptom. It never calls `AskUserQuestion`: a pick
goes on his board, and an urgent yes/no goes to his Slack as an ask card with buttons
(`cc-present ask`).
Every message it sends is addressed to the owner in second person and carries only what
changed for him or what he must decide. Ledger rows, hook mechanics, lane acks and
"nothing new" never appear in chat, and a turn with nothing for him ends with no text;
its Stop hook refuses any other closing message. It is a root session for reads: each
fact it needs is one bounded read or one post, else a subagent. The shared read hooks act
on it at the coordinator's thresholds, and a second Bash read with no `Agent` spawn
since the first draws a nudge ([owner-bridge-brief.md](reference/owner-bridge-brief.md)).

**The coordinator** reads reader `root`, posts as `root`, runs in a foreground
`cci watch` loop for the life of the drive, and holds everything a root holds:
lanes, gos, holds, incidents, the task list, progress docs and the register. It
launches lanes through the supervisor and Orca (`lr launch`), never with `Agent`.
It never posts to Slack or the board and never speaks to the owner; everything he
must see is a `report` to `owner-relay`. Its records are for machines and the communicator:
terse, jargon-heavy, ids and lane names included, in no format it owes anyone. The
same holds for a lane's records to it. When it dies the supervisor relaunches it
from its brief and newest handoff like any lane, and the bridge keeps answering the
owner meanwhile. It owns drive work only: it never takes a skill, tooling, prompt, hook
or process improvement and never spawns a lane for one. Its one duty there is to report a
tooling defect that it or a lane hits, as
`cci post --lane root --kind report --to owner-relay --text 'for meta: <the defect, any refusal verbatim>'`,
which the communicator forwards to the meta lane, and to keep the work moving on the hand
path meanwhile. It decides five things and nothing else:

1. **A new ask gets a lane** the turn it arrives, with a ledger row and a task
   (R6, R8). A lane holding more than two unstarted items is split.
2. **An owner ruling is applied** to every live lane and artifact the turn it
   is read (R17, R18, R19b); a settled question is never re-asked (R19).
3. **An escalation gets one answer.** The supervisor, the landing runner, the
   stall watch and the update generator reach it only with a `decide`, `ask`,
   `blocker` or `incident` record; it answers each with one record naming the
   lane that acts. A resolution naming no fixer, no `FIX-LIVE` and no reason
   is refused by the pack.
4. **A conflict between two owner rules** is settled in one `report` to the communicator,
   naming the rule applied.
5. **A desk's output is relayed** unchanged: a milestone as a `report` to the
   bridge, which then sends the owner an update, a tap card by saying nothing while
   the runner lands on the tap. A gap the bridge's update names, a message of the
   owner's no record carries, comes back as a `blocker` and gets a record naming the
   lane that acts.

Its own work on a deliverable, its own reads past one bounded window and its own
waits are each a defect the hooks name, and so is a queue of PRs waiting on one of
its decisions: a decision the owner's picks already settle moves to a package that
applies them by rule, and one they do not settle is his, on the board. Every escalation target, brief, template,
handoff, task list, incident checklist and dashboard in the pack names the
coordinator where it once named the root, names `owner-relay` where the owner
must see something, and names the meta lane where a skill or tooling fix is owed.

**The meta lane** is a standing session the communicator launches at drive start from
[meta-lane-brief.md](reference/meta-lane-brief.md), bound to its own lane `meta`,
neither `root` nor `owner-relay`. Every skill, tooling, prompt, hook and process
improvement goes there, and so does every meta order the owner sends. It reports only to
the communicator and never addresses the coordinator; when the coordinator must act, it
says so and the communicator relays it. It keeps `<state dir>/meta/items.md`, one row per item: the
item, the owner's words with their time, the lane, the state, the PR, and what blocks it
or comes next. The communicator reads only that file for meta items in the owner's updates. The
meta lane launches one implementer per item, and each item ends in a merged PR with tests,
installed by the drive's one installer (R14).

**The signal lane** is a standing Sonnet session the communicator launches at drive start from
[signal-lane-brief.md](reference/signal-lane-brief.md), bound to its own lane `signal`.
It tails the coordinator's whole cci feed and posts the communicator pointers to it, seqs and
transcript offsets, never summaries. The communicator reads what they point at and does the
synthesis.

**A sub-drive** is an option the coordinator holds and never a default. The
coordinator alone decides, and only for a big project plainly unrelated to the rest of
the drive. The test is five facts it can state: the project shares no PR, no file, no
plan step and no release target with the parent's work, and it needs lanes of its
own. If any one is doubtful, the work is a lane here.

```sh
lr drive sub start <name> --objective <what it delivers> --reason <why it is separate> \
  --no-shared-prs <how you know> --no-shared-files <how you know> \
  --no-shared-steps <how you know> --no-shared-targets <how you know> \
  --own-lanes <the lanes it needs> [--repo <owner/name> --checkout <path>]
```

The verb refuses without the reason and every fact, and refuses in any window but a
root coordinator's. It waits on a launch, so run it with `run_in_background`. It starts
the project as a drive of its own: a small coordinator launched the way `lr launch`
starts one, its own lanes, ledger, Orca Run and supervisor, and its own cci drive,
`<parent>--<name>`. It is a drive and not a child topic because cci binds a lane per
drive: a second session cannot hold `root` on the parent's drive, and every root hook,
stop check and cursor keys on lane and drive. The launch sets
`CLAUDE_LONG_RUNNING_DRIVE` to the child, so the coordinator runs
`/long-running:coordinator` and gets the same hooks, each acting on the child's
registry entry and bus. It starts no sub-drive of its own.

It reports to the parent's coordinator only, as `--to root@<parent>`, and never to the
communicator or the owner; the pack blocks a `cci post` to either from any session of
the sub-drive, and its supervisor sends a dead coordinator's blocker to the parent too.
The parent folds each report into the drive's record: a ledger row for the project, one
line in its progress doc, and a `report` to `owner-relay` when the owner must see it.
It carries the owner's answers and rulings down with `--to root@<parent>--<name>` or
`--re`. A PR tap the sub-drive waits on goes up the same way, as an `ask`.

`lr drive sub list` names each open sub-drive with what it still holds.
`lr drive sub end <name>` hands every open lane, record and PR back to the parent as
one `ask` it must close, posts the child's sessions a stand-down, removes its
supervisor and registry entry, and names the `orca-gc` line to run once they stop.

## Ask the verb, never read

| To know | Run |
|---|---|
| Is a lane alive, and what did it last say | `lr lanes <lane>` (today `drive_supervisor.py lanes <lane> --config <lr.json>`) |
| Every lane, its state, PRs and standing duty | `lr lanes` |
| What happened since it last looked | `cci digest --drive <drive>`, then `cci tail --drive <drive> --cursor root` |
| What waits on the owner | `cci owner-waits --drive <drive>` |
| Which sub-drives are open and what each still holds | `lr drive sub list` |
| The state of PRs it must name | `ccx vcs pr status <n1> <n2> ...`, batched |
| Every open ask, and what is landed but not live | `lr asks` (today `ledger.py summary --drive <drive>`) |
| The owner update, complete and from sources | `lr update render`, run by the bridge's `update-desk`, sent by the bridge |
| What an incident is doing | `lr incident status <slug>` |
| Where a lane's mail goes | `lr route <lane>` |
| Whether an install may run now | `drive.py digest --quiet-point` |

A read the table lacks belongs to a lane: a PR, a diff, a CI log, a Slack thread,
a plan artifact, a long file, a cloud listing, a build page. The coordinator spawns
a reader and takes back at most five lines. The `root_context` and `root_reads`
hooks warn on every read of those kinds, on a second source opened for one owner
ask, and on a lane's records piped through `tail` or `grep`.

## What runs without the root

| Mechanism | Owns | Reaches the coordinator with |
|---|---|---|
| Landing (`lr landing pass`, today `desk-runner.py --desk landing`) | Every open PR: gates at the current head each pass, enqueues a PR the owner tapped or a go-carrying exempt class, restacks, resubmits an eviction, routes a red to its lane. Holds come only from `--kind hold` records. | One `landing-state` line per pass for a PR with every gate and no tap or go |
| Grading (`lr grade`, the finder and fidelity desks) | A finder and a fidelity grade of every head within ten minutes | Nothing; a verdict goes to the lane |
| Lane liveness (`lr lanes`, the supervisor under launchd) | One registry row per lane, relaunch from brief and newest handoff, rotation in place, a `Standing:` duty with no live owner for ten minutes | One `blocker` per unowned duty; one `defect` per lane nothing proves dead |
| Incident spawn (the supervisor's Alerts check) | An alert with no `incident` record and no fix lane after three minutes: the supervisor launches the fix and evidence lanes itself | The spawn record; a `blocker` only when the launch fails |
| Landed, not live (`lr asks`) | A landed PR whose release target has shipped nothing in an hour | One `blocker` per target with the exact release line |
| Stalls (item deadlines; today `stall_watch.py`) | Work that stopped moving, read from the registry | Only a stall whose owner is the coordinator |
| The owner's taps (`board_watch.py`) | Every tap, pick and write-in as an `owner` record; the runner lands on it | A pick with no lane at 30 minutes, as an `ask` |
| The owner update and the dashboard (`lr update render --sent`, run by the bridge's `update-desk`) | Every plan step, workstream, live lane under its step, open ask, pick, incident and release today, PR with a go owed or hold; every row with who is on it or **nobody**, what blocks it, what unblocks it and by when, what went wrong; unowned work first; exit 1 naming anything missing | The rendered update, which the bridge relays and the dashboard shows |
| The handoff (`lr handoff generate`) | The pre-compaction doc with the lane registry and the rendered update | Nothing but the coordinator's own narrative |

A mechanism that does not run is a defect of the drive, never a chore for a root.
When either session does a row's work by hand, it sends the defect to the meta lane
that turn (R14).

## The rules

Ids are stable; scripts, hooks and references cite them. `root` means the
coordinator unless the rule names the bridge.

**R1.** Ground truth comes from the owning lane or the verb, never a grep, a
listing, a curl or a build page; at most five lines back. Priority PR state is
read in one batched `ccx vcs pr status` call.

**R2.** Anything with a body is a lane: a read past one bounded window, a log, an
investigation, PR or merge mechanics, an enumeration. One bounded read of one named
file, up to 150 lines, is allowed.

**R3.** One lane per wait-then-do chain, and no lane parks: lanes poll in
foreground loops to a terminal state and report once. Never a Monitor per build.

**R4.** Never re-brief a lane, never answer a duplicate. A `SendMessage` to a
finished agent re-runs its brief. `desk-wait.sh` marks printed mail read.

**R5.** Write it down, do not hold it: findings to cc-notes from the lane that
found them, briefs as attachments on the `briefs: <slug>` log, nothing hand-made
under `~/.claude/scratch`.

**R6.** Every owner request is in flight the turn it arrives, one lane per ask,
stacked where files overlap, never appended to a busy lane's queue.

**R7.** A PR's state is read before it is reported: `ccx vcs status` or the squash
commit on fetched trunk, never a message or memory.

**R8.** Every owner ask is a ledger row the turn it arrives, and done only at
`LIVE`; before that the answer is "in #N, not live yet: `<blocker>`". A landed PR
with no release in an hour reads `LANDED-NOT-LIVE` and reaches the coordinator as
a blocker.

**R9.** Orca worker traffic runs through the runner (`lr launch`, `lr relay`),
never a check-and-ack loop. Mac load queues and refuses no launch: one that may leave
the Mac starts on a box at once, and a worker's question goes to the lane that launched it. The coordinator receives records over the cc-inbox
channel after one `cci subscribe --drive <drive> --reader root --kind incident,decide,ask,defect,blocker --follow oncall=incident`;
the bridge subscribes as `owner-relay`. A seq already handled is skipped.

**R9a.** An urgent hold gets a decision owner and a clock, posted with
`desk-launch.py hold`; the coordinator resolves the runner's `DECIDE hold:` line
ahead of any open owner card on another subject.

**R10.** Landed is the only progress: landed, queued, live, or the exact blocker
naming the PR, its head and the gate. A red gets a structural fix with a test,
never a retry.

**R11.** Hands off deploys, and an owner's order carries every approval. Nobody
starts, approves, watches or re-cuts a release unless the owner asked. When he
orders one, the release lane applies and counts the deletes, replaces and
migrations its plan names and never asks again; the `owner_order_is_the_approval`
hook refuses a second word, and `tools/ci release start --order <answer id>` passes
the gate itself. An incident fix rides the next regular release when one can cut
within its urgency, with no dry run, and keeps the zero-delete bound unless he
ordered it. No release is canceled once its
apply has started. A regular release is the default: an emergency release runs only
when the regular path is too slow for a live customer impact, and never two for one
fix. A release at its approval gate five minutes after an owner order is a blocker
to root, never a wait. A cluster leaves the code and ships before it is destroyed.

**R12.** Every red goes to its owning lane the same turn with a 15-minute
deadline; a red with no live owner gets a lane.

**R13.** Every owed item is dispatched the same turn, one lane each.

**R14.** A process fix is a PR the turn it is adopted, driven to merge, release
and install by its lane, proven on the record that reported the defect. A tooling
defect, refusal or repeated chore goes to the meta lane that turn, which gives it an
implementer; the coordinator never spawns one. One lane installs
per night, at `drive.py digest --quiet-point`. No new Python enters the pack; a fix
to a Python script is a Go verb.

**R15.** CI is the verifier. No lane runs tests, builds, typechecks or lint locally;
the `local_builds` hook refuses them. Batch jobs run on in-region compute.

**R16.** An active alert gets its executor the same turn, without a root: the
supervisor launches the fix and evidence lanes for an alert nobody answered in
three minutes. When an alert reaches the coordinator first (a link the bridge
relayed, an `#outage` report, a page, a runner `PAGE`, a failing release check, an
`ALERT-UNOPENED` blocker), it runs `lr incident open` and `run` before anything
else, with no real-or-not verdict and no mute treated as resolution. Fixes land
forward; a rollback runs only on the owner's own word.

**R17.** An owner routing instruction applies to the next spawn and to every live
lane on that problem, the turn it arrives.

**R18.** A ruling reaches a lane the way it reads: Orca lanes by `lr relay`, a desk
by a record addressed to it, an Agent lane by a record it reads at its next tool
call. A stand-down is confirmed on disk before it counts.

**R19.** A settled question is applied, never asked: the plan, the register and
`ccn answer search` are checked first, an absolute instruction stays absolute, a
gate wanting an owner answer for a settled decision is satisfied by citing the pick
(R19a), and every ruling binds what it implies, recorded verbatim, confirmed with
him and applied to every live artifact at once (R19b).

**R20.** A Slack link from the owner is an assignment: the bridge queues it, the
coordinator spawns the Slack lane and the doing lane that turn in one batch, and
one `report` to the communicator names both. Nobody reads Slack or writes Slack copy
outside the Slack lane.

**R22.** Every brief quotes the owner's design verbatim with the entry point at
`file:line`; the lane posts a `contract` before `READY-FOR-SHIP`; the design ships
whole or holds, never split.

**R23.** Every finished agent is stopped the turn its completion arrives, and before
any spawn.

**R24.** Every production operation is one line in the cc-notes log
`production-operations` that turn; incident lanes read its last day before naming a
mechanism.

## Lanes and desks

A lane that ships a PR or calls a skill is `long-running:lane-ship`; any other lane
or desk is `long-running:lane`; a lane that posts to Slack is `general-purpose` on
opus. Implementation lanes run on Sprites. A brief whose duty the drive keeps for
good carries `Standing: <duty>`. A lane never spawns with `Agent`; it writes the
brief and posts `cci post --kind ask --to root --text 'spawn <name> brief=<path> model=<model>'`.
The meta lane, which never addresses root, launches its own implementers with
`desk-launch.py lane <name> opus xhigh <brief> --lane meta`.

| Desk or lane | Does | Brief |
|---|---|---|
| Coordinator | Everything in [Phase 0](#phase-0-the-standing-sessions) | [coordinator-brief.md](reference/coordinator-brief.md) |
| Meta lane, the communicator's | Every skill, tooling, prompt, hook and process improvement, and every meta order the owner sends; `items.md`; one implementer per item | [meta-lane-brief.md](reference/meta-lane-brief.md) |
| Signal lane, the communicator's | Tails the coordinator's feed and posts the communicator pointers, never summaries | [signal-lane-brief.md](reference/signal-lane-brief.md) |
| Landing runner and `landing-desk` | The merge queue, lane reports, the ledger | [landing.md](reference/landing.md), [landing-desk-brief.md](reference/landing-desk-brief.md) |
| Orca runner | Launches and relays to Orca workers, starts grading jobs | [orca-desk-brief.md](reference/orca-desk-brief.md) |
| Lane supervisor | Liveness, relaunch, rotation, standing duties, alert launch, landed-not-live | [watchers.md](reference/watchers.md) |
| Incident executor, `alerts-desk` | One incident from intake to final reply; monitor transitions as records | [active-alert-brief.md](reference/active-alert-brief.md), [alerts-desk-brief.md](reference/alerts-desk-brief.md) |
| `update-desk`, the bridge's one helper | Renders the owner update the bridge sends, writes the dashboard's rows from it, keeps the dashboard app up | [owner-bridge-brief.md](reference/owner-bridge-brief.md), [owner-report-table.md](reference/owner-report-table.md) |
| `finder-desk`, fidelity desks, `red-sweep` | Grade every head; own every red with no owner | [finder-brief.md](reference/finder-brief.md), [fidelity-desk-brief.md](reference/fidelity-desk-brief.md), [red-sweep-brief.md](reference/red-sweep-brief.md) |
| Board refresher, `drive-watchdog`, `user-test` | Tap cards and records; the retro log; the owner played against each surface | [drive-watchdog-brief.md](reference/drive-watchdog-brief.md), [user-test-brief.md](reference/user-test-brief.md) |
| Priority desk, merge train, sequencer, Slack lane, handoff writer | One number-one outcome; the hot set; a wait-then-do chain; a named post; a rotating lane's handoff | [priority-desk-brief.md](reference/priority-desk-brief.md), [merge-train-brief.md](reference/merge-train-brief.md), [orca-lane-brief.md](reference/orca-lane-brief.md), [slack-lane-brief.md](reference/slack-lane-brief.md), [handoff-subagent-brief.md](reference/handoff-subagent-brief.md) |

## The owner's surfaces

Every PR goes to the owner. A PR that is green, approved and graded at its head
reaches his tap card within one runner pass and lands on his tap; only the classes
he exempted land on a go, nothing new joins them without his word, and the
`landing_approval` hook refuses a record or brief that changes it. A tapped PR
never comes back: the tap carries to every later head, and a tapped PR takes no
behavior change. Every other owner ask is a question card from `drive.py ask-owner`
with two to four answers and one recommended; a settled question never reaches the
board; a card he touched stays until he submits the round; nobody calls
`AskUserQuestion`, and an urgent yes/no is the same card sent to his Slack with
`cc-present ask`. `board_watch.py` records
every tap as an `owner` record and a cc-notes answer. What waits on him is what
`cci owner-waits` prints, never a runner note. The drive writes one automated Slack
message, the merge card in his direct-message status thread; everything else is a
post he named. Every update he gets is `lr update render`, sent by the bridge
unchanged with its gaps shown, and the dashboard shows the same rows.

## Records and the task list

Lane messages, decisions, heads, contracts, blockers and asks live on cci. Lane
liveness, PRs and standing duties live in the supervisor's registry. Owner asks,
PR rows, holds and landings live in the ledger. Findings, notes, docs and logs live
in cc-notes, standing owner rules in the register doc, production operations in
the `production-operations` log, progress and handoffs in generated progress docs,
and meta items in the meta lane's `items.md`.
The task list is the coordinator's: it runs `TaskCreate` the turn it spawns a lane
or an ask arrives, completes a task only after consuming the deliverable, and
reconciles the list against the registry at every handoff.

## Start and resume

```sh
LEDGER=$(ledger.py init --title "desk: $DRIVE")
drive.py start --ledger "$LEDGER" --orca-run <run> [--state-dir ~/.claude/scratch/<slug>] [--program <plan slug>]
cci subscribe --drive <drive> --reader owner-relay --kind report,ask
drive_supervisor.py install --config <state dir>/lr.json
export LR_CONFIG=<state dir>/lr.json
lr launch coordinator <state dir>/root/brief.md
lr launch meta <state dir>/meta/brief.md
lr launch signal <state dir>/signal/brief.md
```

| Session | Lane | Model and effort | Brief |
|---|---|---|---|
| `coordinator` | `root` | `opus xhigh` | [coordinator-brief.md](reference/coordinator-brief.md) |
| `meta` | `meta` | `opus xhigh` | [meta-lane-brief.md](reference/meta-lane-brief.md) |
| `signal` | `signal` | `sonnet low` | [signal-lane-brief.md](reference/signal-lane-brief.md) |

The communicator fills each brief, then launches the three as sibling Orca sessions, each
bound to its own lane. `lr launch` posts to the orca runner when one is up and otherwise
launches from the communicator's terminal, which coordinates the Run, since the coordinator
has not started a runner yet. The coordinator runs its own subscribe line (R9), starts the orca runner before the
first Orca worker, the landing desk and runner before any lane opens a PR, and the
standing desks each with a `Standing:` line. Every lane registers its branch prefix
at spawn with `ledger.py register`.

A resumed communicator joins with `drive.py start --drive <id> --ledger <id>` and checks
the three with `lr lanes root meta signal`. A resumed or relaunched coordinator catches
up with `cci digest`, `cci tail --cursor root` and `lr lanes`, and after a
compaction answers every unresolved record from
`cci tail --since <seq> --reader root --kind decide,ask,blocker,hold,incident`
before any other work. Every script is on PATH through the plugin's `bin/`; a
script run by path from an older version re-runs the installed copy, and `lr.json`
is read by every installed version.

## Before every tool call

Is there a verb for this? Run it. Does it have a body? Spawn the lane with its task
and ledger row. Is it a wait? Fold it into the lane that acts. Is it status? The communicator
sends the update desk's render; the coordinator sends nothing. Is it an owner message? The communicator
records the words and queues them; the coordinator applies the ruling everywhere, and
a meta order goes to the meta lane.
Is it an alert the supervisor has not answered? `lr incident open` and `run` now.
Did a tool refuse, or did something go wrong twice? It goes to the meta lane this
turn, where the fix is a Go verb, and the work proceeds. Am I about to approve, hold, start or cancel something? The
owner's order carries it or a lane owns it. Finished agents stopped, production
operations logged, nothing called done that is not live and checked.

A call that survives the list decides something no verb and no lane can decide.
Everything else is a lane.
