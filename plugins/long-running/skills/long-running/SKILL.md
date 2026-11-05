---
name: long-running
description: Runs a multi-lane drive so no coordinator becomes its bottleneck. The session the owner types in is the communicator; a sibling Orca session bound as lane root is the coordinator; a standing meta lane takes every skill, tooling and process change and turns each ruling into a skill update; every question has a verb of the drive tool lr; landing, grading, lane liveness, incident spawn, release tracking and owner updates run as processes that reach the coordinator only with a decision to make. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
---

# Long-running orchestration

A coordinator is a serial decision point: every read it makes and every decision routed
through it waits in a queue with no clock, and when it saturates the work stops in silence.
This skill keeps anything from reaching such a queue.

A drive's top level is two sessions. The **communicator** is the session the owner types in;
its whole job is him. The **coordinator** is a sibling Orca session bound as lane `root`: it
spawns a lane for every ask with a body the turn it arrives, decides what a process
escalated, and relays what the desks produce. Beside them stand the **meta lane**, which
takes every skill, tooling and process change and turns each ruling into a skill update, and
the **signal lane**, which tails
the coordinator so the communicator never has to. Everything else is a verb of the drive
tool `lr` or a process that runs without any of them. A rule here names the verb or process
that makes a failure impossible; a rule with neither is a defect to fix in the pack.

This skill sits on `~/.claude/CLAUDE.md`, which owns fan-out shape, model routing and
verification depth. The pack is one Go binary, `lr`, with the Python scripts it replaces kept
only until each verb goes live
([design](https://github.com/Forge-AI/design-docs/tree/main/design-docs/long-running-v2)).

Three or more lanes at once, a task that outlives one context window, a wait on CI, a queue,
an apply or a soak, or a sweep over many independent units is a drive. A single-lane
investigation is one subagent in direct mode, not a drive.

## Contents

- [Read the file for your job](#read-the-file-for-your-job)
- [Judgment first](#judgment-first)
- [Hooks and refusals](#hooks-and-refusals)
- [Meta changes land in the skill](#meta-changes-land-in-the-skill)
- [Before every tool call](#before-every-tool-call)
- [Ask the verb, never read](#ask-the-verb-never-read)
- [The rules](#the-rules)
- [Phase 0: the standing sessions](#phase-0-the-standing-sessions)
- [What runs without the coordinator](#what-runs-without-the-coordinator)
- [Lanes and desks](#lanes-and-desks)
- [The owner's surfaces](#the-owners-surfaces)
- [Records and the task list](#records-and-the-task-list)
- [Start and resume](#start-and-resume)
- [Add a rule to this skill](#add-a-rule-to-this-skill)

## Read the file for your job

Read only the row you are doing. Each file states its rules in full.

| You are | Read |
|---|---|
| Answering the owner: replies, picks, redesigns, his Slack and board | [communicator-brief.md](reference/communicator-brief.md) |
| Rendering the owner update or keeping the dashboard | [owner-report-table.md](reference/owner-report-table.md), [drive-dashboard.md](reference/drive-dashboard.md) |
| The coordinator | [coordinator-brief.md](reference/coordinator-brief.md) |
| The meta lane or its implementer | [meta-lane-brief.md](reference/meta-lane-brief.md) |
| The signal lane; the chores lane | [signal-lane-brief.md](reference/signal-lane-brief.md); [chores-lane-brief.md](reference/chores-lane-brief.md) |
| Writing a lane brief, or working as a lane or desk under one | [orca-lane-brief.md](reference/orca-lane-brief.md) |
| Rotating a lane's context; a handoff or a compaction | [handoff-subagent-brief.md](reference/handoff-subagent-brief.md), [compaction.md](reference/compaction.md) |
| Launching or relaying Orca workers, on the Mac or on a Sprite | [orca-desk-brief.md](reference/orca-desk-brief.md), [orca-workers.md](reference/orca-workers.md), [orca-sprite-workers.md](reference/orca-sprite-workers.md) |
| Landing a PR, the merge queue, D1 to D23 | [landing.md](reference/landing.md), [merge-train-brief.md](reference/merge-train-brief.md) |
| Grading a head, a landing or a plan | [grading-principles.md](reference/grading-principles.md), [finder-brief.md](reference/finder-brief.md), [finder-desk-brief.md](reference/finder-desk-brief.md), [finder-desk-rows.md](reference/finder-desk-rows.md), [fidelity-desk-brief.md](reference/fidelity-desk-brief.md), [fidelity-job-brief.md](reference/fidelity-job-brief.md), [semantic-collisions-brief.md](reference/semantic-collisions-brief.md) |
| Owning a red with no owner | [red-sweep-brief.md](reference/red-sweep-brief.md) |
| An alert or incident | [active-alert-brief.md](reference/active-alert-brief.md), [alert-fix-brief.md](reference/alert-fix-brief.md), [alerts-desk-brief.md](reference/alerts-desk-brief.md), [alert-evidence-brief.md](reference/alert-evidence-brief.md), [alert-comms-brief.md](reference/alert-comms-brief.md), [unfixed-case-brief.md](reference/unfixed-case-brief.md) |
| A release | [release-lanes.md](reference/release-lanes.md), [manual-release-brief.md](reference/manual-release-brief.md) |
| Posting to Slack | [slack-lane-brief.md](reference/slack-lane-brief.md), [comms-desk-brief.md](reference/comms-desk-brief.md) |
| Posting or reading cci records, the lane bus, B rules | [bus-contracts.md](reference/bus-contracts.md) |
| Recording or applying an owner ruling, the register, judges | [owner-rulings.md](reference/owner-rulings.md) |
| Lane liveness: why a lane cannot go quiet, the supervisor and the watches | [supervisor.md](reference/supervisor.md), [watchers.md](reference/watchers.md), [drive-watchdog-brief.md](reference/drive-watchdog-brief.md) |
| The one number-one outcome; playing the owner against a surface | [priority-desk-brief.md](reference/priority-desk-brief.md); [user-test-brief.md](reference/user-test-brief.md) |
| Starting a sub-drive | [sub-drives.md](reference/sub-drives.md) |
| Reading a large corpus into fix lanes | [the bulk-read skill](../bulk-read/SKILL.md) |
| Asking why a rule exists; no lane needs this to do its job | [lessons.md](reference/lessons.md) |

## Judgment first

- No rule forbids reading the one line a decision turns on.
- A finding in hand is a fix, never a lane, a re-audit or a workflow.
- Read each owner sentence as a careful colleague would. A correction names a defect in the
  reading, never a redesign of something he ruled on earlier; read the register's rows on
  that subject before acting. A repeat is a defect report about the first reading: fix every
  place it survived, that turn.

## Hooks and refusals

- Hooks warn. They block only on trust: a merge without the owner's approval, a change to who approves landings, a revert without his word, a runner or desk started from the communicator, a lane driving his signed-in browser on production, edits to the installed hook files and the session-protection guards. A failed read only warns; `tests/test_fail_open.py` enforces it and lists the exceptions, and the Slack permission hook fails open ([slack-lane-brief.md](reference/slack-lane-brief.md)).
- A hook that blocks your work gets a fix lane the turn it blocks you, and you keep working on what it does not block. The blocked session launches it, whether lane, desk, coordinator or communicator, with no ask to `root` and no meta lane: `lr launch lane hook-fix-<hook> opus high <brief> --drive <drive> --lane <you>`, unless `lr lanes hook-fix-<hook>` shows one live.
- Hooks live in `plugins/long-running/capt-hook/hooks/` in yasyf/cc-skills and in `captain_hook/builtin_packs/` in yasyf/captain-hook, and every change follows `captain-hook:authoring-hooks`, whose lint and test run in that repo's CI. The fix lane fixes the hook's real defect with a regression test, ships it, installs it where the blocked session runs, and its proof is the refused call going through ([orca-lane-brief.md](reference/orca-lane-brief.md#who-spawns-asks-and-reads)).
- Fixing is not routing around: while a refusal stands nobody runs the blocked thing through a wrapper, `cc-sudo` or a script. The owner's safety rules stay as written: merge, Slack, load tests and the process guard. A hook guarding one of them that misfires gets its misfire fixed with a regression test, and the rule itself changes only on his word. The one hook disabled before its fix is one that blocks the communicator's every stop: comment it out in the pack source with a TODO naming its fix lane, in a PR merged at once, since an edit to the installed cache lasts only until the next install, which this Mac runs within minutes.
- A hook that blocks nothing and could be better goes to the meta lane as one `cci post --kind note --to meta-desk`. Any other tool's refusal goes to the meta lane the same turn and the work proceeds on its hand path. No refusal is an excuse or a question for the owner, and neither is the platform: a record that blames a Sprite, a host or a shared tool for a wait or a failure names the lane fixing its root cause and that fix's clock, and with no such lane it asks the coordinator for one that turn. Replacing a machine is a stopgap and never the answer.

## Meta changes land in the skill

The lane that receives a globally applicable meta change, any ruling on how lanes, cards, waits, replies, hooks, routing
or answers work, sends it to the meta lane in the turn it arrives, as one cci record. The meta lane always makes it an
update of this skill, or of the brief template, hook or cc-notes doc that owns it, merged and installed, rewriting and
refactoring the section it touches so the skill reads as one design, never a bare appended line. A memory file or a
record is a pointer and never counts as done.

## Before every tool call

| If the call is | Do |
|---|---|
| Something a verb answers | Run the verb. |
| Work with a body | Spawn the lane with its task and ledger row. |
| A wait | Fold it into the lane that acts. |
| Status | The communicator sends the update desk's render; the coordinator sends nothing. |
| An owner message | The communicator spawns one subagent for it, which answers on the surface it came from; the coordinator applies the ruling everywhere; a ruling on how the drive works goes to the meta lane that turn, which makes it a skill update. |
| An alert the supervisor has not answered | `lr incident run <slug>` now. |
| A tool refusal, or something that went wrong twice | A hook blocking your work gets the fix lane you launch this turn ([Hooks and refusals](#hooks-and-refusals)); anything else goes to the meta lane this turn, where the fix is a Go verb. Proceed either way. |
| An approval, hold, start or cancel | The owner's order carries it or a lane owns it. |

Finished agents stopped, production operations logged, nothing called done that is not live and checked.
A call that survives the list decides something no verb and no lane can decide. Everything else is a lane.

## Ask the verb, never read

| To know | Run |
|---|---|
| Is a lane alive, and what did it last say | `lr lanes <lane> --config <lr.json>` |
| Every lane, its state, PRs and standing duty | `lr lanes` |
| What happened since it last looked | `cci digest --drive <drive>`, then `cci tail --drive <drive> --cursor root` |
| What waits on the owner | `cci owner-waits --drive <drive>` |
| Which sub-drives are open and what each still holds | `lr drive sub list` |
| The state of PRs it must name | `ccx vcs pr status <n1> <n2> ...`, batched |
| Which PRs the owner approved on the board, and which of them are still open | `lr approved [--since <seq>]`, backfilled verdicts excluded |
| Every open ask, and what is landed but not live | `lr asks` |
| The owner update, complete and from sources | `lr update render`, run by the communicator's `update-desk` |
| What an incident is doing; one sentence on what it is | `lr incident status <slug>`; `lr incident describe <slug>` |
| Where a lane's mail goes; every `lr` verb takes a stem, `meta-desk`, for its newest live lane | `lr route <lane>` |

A read the table lacks belongs to a lane: a PR, a CI log, a Slack thread, a long file, a cloud
listing, a build page. A diff, plan or verdict you are approving is yours to read. The coordinator spawns a reader and takes
back at most five lines; a search is one call, `lr ask-lane '<question>'`. The `root_context`
and `root_reads` hooks warn on every read of those kinds, on a second source opened for one
owner ask, and on a lane's records piped through `tail` or `grep`; in the communicator's
window `root_context` refuses a wide search and names `lr ask-lane`.

## The rules

Ids are stable; scripts, hooks and references cite them. `root` means the coordinator unless
the rule names the communicator.

**R1.** Ground truth comes from the owning lane or the verb, never a grep, a listing, a curl or
a build page; at most five lines back. Priority PR state is read in one batched
`ccx vcs pr status` call.

**R2.** Anything with a body is a lane: a read past one bounded window, a log, an
investigation, PR or merge mechanics, an enumeration. One bounded read of one named file, up to
150 lines, is allowed.

**R3.** One lane per wait-then-do chain, and no lane parks: a lane waits on a `Monitor`, an Agent
notification, `CronCreate` for a clock, or one `lr wait` call, to a terminal state, and reports once.
`shell_waits` blocks a shell loop or sleeper. A desk's `Monitor` re-arms at expiry.

**R4.** Never re-brief a lane, never answer a duplicate. A `SendMessage` to a finished agent
re-runs its brief. `lr wait` marks printed mail read.

**R5.** Write it down, do not hold it: findings to cc-notes from the lane that found them, briefs
as attachments on the `briefs: <slug>` log, nothing hand-made under `~/.claude/scratch`.

**R6.** Every owner request is in flight the turn it arrives, one lane per ask, never appended to
a busy lane's queue. A lane owns one PR per deliverable and lands its fixes as commits on it; [orca-lane-brief.md](reference/orca-lane-brief.md) says when it
stacks.

**R7.** A PR's state is read before it is reported: `ccx vcs status` or the squash commit on
fetched trunk, never a message or memory.

**R8.** Every owner ask is a ledger row the turn it arrives, and done only at `LIVE`; before that
the answer is "in #N, not live yet: `<blocker>`". A landed PR with no release in an hour reads
`LANDED-NOT-LIVE` and reaches the coordinator as a blocker.

**R9.** Orca worker traffic runs through the runner (`lr launch`), never a check-and-ack loop. Mac
load queues and refuses no launch: one that may leave the Mac starts on a box at once, and a
worker's question goes to the lane that launched it. The coordinator receives records as batches
from `lr coordinator next`, after one
`cci subscribe --drive <drive> --reader root --to-kind incident --follow oncall=incident` keeps
all but incidents off its tool calls; the communicator subscribes as `owner-relay` for `report`,
`ask` and `blocked`. A seq already handled is skipped.

**R9a.** An urgent hold gets a decision owner and a clock; the coordinator resolves the runner's
`DECIDE hold:` line ahead of any open owner card on another subject.

**R10.** Landed is the only progress: landed, queued, live, or the exact blocker naming the PR, its
head and the gate. A red gets a structural fix with a test, never a retry.

**R11.** Hands off deploys, and an owner's order carries every approval. Nobody starts, approves,
watches or re-cuts a release unless the owner asked. When he orders one, the release lane applies
it and never asks again (`owner_order_is_the_approval`). A regular release is the default; an
emergency runs only when the regular path is too slow for a live customer impact. No release is
canceled once its apply has started. The full rule is in
[release-lanes.md](reference/release-lanes.md).

**R12.** Every red goes to its owning lane the same turn with a 15-minute deadline; a red with no live owner gets a lane.

**R13.** Every owed item is dispatched the same turn, one lane each. A fix or review response owed on an open PR goes to
the lane that owns it, as a commit on that PR, never a new lane or a stacked child.

**R14.** A process fix is a PR the turn it is adopted, driven to merge, release and install by its
lane, proven on the record that reported the defect. A hook that blocks a session is fixed by the lane
that session launches, which installs its own fix at merge ([Hooks and refusals](#hooks-and-refusals)).
Every other tooling defect or refusal goes to the meta lane that turn, which gives it an implementer; the coordinator never spawns one.
A session or lane that hits a workaround, a repeated lookup or a tool gap twice sends the meta lane one
`cci post --kind note --to meta-desk` that turn, unasked, naming the verb it wants, and the meta lane builds it
without being told. A fix for drive tooling that is actively causing harm ships as its own PR off main, never stacked,
merged and installed before any other tooling change. No new Python enters the pack apart from capt-hook hooks; a fix to a Python script is a Go verb.

**R15.** CI is the verifier and runs every test. Before it pushes, a lane runs `go build` and `go vet` over the Go packages
it touched and the typecheck over the TypeScript it touched, which the `vet_before_push` hook enforces; the `local_builds`
hook refuses every other local test, build and lint. Batch jobs run on in-region compute. Machine load never holds a remote
build or an owner-directed apply. A wrapper that runs a build or check remotely fails with a clear error when no remote
builder answers, and never falls back to the local toolchain.

**R16.** An active alert gets its fix lane the same turn, without a coordinator: the supervisor
launches the fix and evidence lanes for an alert nobody answered in three minutes. An alert that
reaches the coordinator first gets `lr incident run <slug> --fix-brief <file>` before anything
else, with no real-or-not verdict and no mute treated as resolution. Fixes land forward; a rollback
runs only on the owner's own word, and only his explicit order mutes an alert. Each incident has
one actor: beside an open ai-oncall incident a lane hands findings to its commander, and takes it
over only when `lr owner away <incident id>` prints away. The full rule is in [active-alert-brief.md](reference/active-alert-brief.md).

**R17.** An owner routing instruction applies to the next spawn and to every live lane on that
problem, the turn it arrives.

**R18.** A ruling reaches a lane the way it reads: Orca lanes by `lr relay`, a desk by a record
addressed to it, an Agent lane by a record it reads at its next tool call. A stand-down is
confirmed on disk before it counts.

**R19.** A settled question is applied, never asked: the plan, the register and `ccn answer search`
are checked first, an absolute instruction stays absolute, a gate wanting an owner answer for a
settled decision is satisfied by citing the pick (R19a), and every ruling binds what it implies,
recorded verbatim, confirmed with him and applied to every live artifact at once (R19b).

**R20.** A Slack link from the owner is an assignment: the communicator queues it, the coordinator
spawns the Slack lane and the doing lane that turn in one batch, and one `report` to the
communicator names both. Nobody reads Slack or writes Slack copy outside the Slack lane.

**R21.** Whoever drafts for the owner gives, in the same message, the answer to the first question he will ask; a promise of a later answer is a defect. It uses no term he has not been shown and names every mechanism by what it does to him, and the communicator rewrites a lane's phrase rather than relaying it. A change
names its concrete things: which fields and where, which chats or users, before and after. A public API added or changed comes unprompted with its signature, the alternatives weighed, the choice and why. Anything named is a link.
From every lane on every surface, a line saying something waits on his yes or no carries that card with its buttons; no card, no such line. Nothing asks him what a lane can read for itself: whether a card drew is read from the posted message's blocks and attachments through the Slack API, never from his glance, and his yes on a render is about how it should look, never whether it drew. [Terms](reference/communicator-brief.md).

**R22.** A change does what its request names and nothing else: behavior the request does not name stays, and a lane that thinks it must go brings that as a pick and never removes it. Every brief quotes the owner's design verbatim with the entry point at `file:line`; the lane
posts a `contract` before `READY-FOR-SHIP`; the design ships whole or holds, never split. A ruling
that changes a data model, namespace layout or embedding model is a design order: first a one-pager
with its open choices as specific cards, and building only after he has read it or said build.

**R23.** Every finished agent is stopped the turn its completion arrives, and before any spawn;
`lr gc` reclaims a settled lane's Orca workspace and terminal the same pass, and a new lane starts
remote unless it drives the Mac's Orca app. A testing or perf lane under an owner standing order is not
stood down because a PR or a bar was met: it moves to the next scenario, and only the owner retires it.

**R24.** Every production operation is one line in the cc-notes log `production-operations` that
turn; incident lanes read its last day before naming a mechanism. No lane calls production as the owner or drives his signed-in browser, Orca tab or session, read or write, under any authority, incident apply authority included ([orca-lane-brief.md](reference/orca-lane-brief.md#who-spawns-asks-and-reads)).

**R25.** A deadline comes with a plan. A record, update line or card that states a time something must
happen by names the lane that meets it and the steps, and the pack refuses one without them.

## Phase 0: the standing sessions

**Binding.** Every session of a drive is bound to one lane name, and every hook, stop check,
injection and cci delivery keys on that binding and nothing else. An unbound session hears no pack
hook but those that bind it (`tests/test_unbound_silence.py`). The communicator's window is bound
`owner-relay`, the coordinator's `root`, a lane's to its lane name. Because hooks are window-scoped,
the coordinator is never an in-process subagent of the communicator: the two never `SendMessage`
each other, and the bus is their only channel, since a record survives a compaction where a message
does not. The three hook tiers are in [coordinator-brief.md](reference/coordinator-brief.md).

**The communicator** routes and spawns, like the coordinator, and does no work inline. Every owner message, whether
chat, his Slack DM, a Slack thread or a board tap, becomes one spawned subagent (`Agent`, `general-purpose`, opus),
which reads what the answer needs, does the work or routes it, and sends the reply itself. The communicator's own
calls for a message are `slack_react`, `owner_mark`, the `cci post` of its routing record and the spawn. Every answer
comes that turn, in his terms, on his Slack and his board, through the `reply` tool on the plugin's `lr` MCP server. A
message he types in the terminal window is also answered there as text, by the communicator, in the same turn: it runs
that subagent in the foreground and prints the markdown its `reply` returned, unchanged. Its standing Monitor on
`lr owner watch` puts `:eyes:` on his Slack message as it arrives, and a message of his in a thread gets its thread reply
within a minute. A standing desk, the meta desk included, routes and spawns the same way: one lane per item and no
implementation itself, a one-line text fix being the only exception. A pick is a `reply` with `picks`, never plain text and never `AskUserQuestion`. Each owner
ask reaches the coordinator as one `cci post --lane owner-relay --kind ask --to root --text 'owner:m47 <one-line order>'`,
and a message about the skill, its tooling, a prompt, a hook or the process, any ruling on how the drive works included,
goes to the meta lane that turn as `--kind ask --to meta` naming his row. A read-only question of his is
answered by the subagent spawned for it, never a drive lane. It talks only to the coordinator, the meta and signal lanes
and helpers it spawned for its own work, and beyond the launches at drive start it posts no `go`, `hold`, `decide` or
`launch`.

**The coordinator** runs in a foreground `lr coordinator next` loop for the life of the drive, reads
reader `root` and posts as `root`. It launches lanes through the supervisor and Orca (`lr launch`),
never with `Agent`, binds the Run and starts every desk (`lr desks start`). It never posts to Slack or
the board and never speaks to the owner; everything he must see is a `report` to `owner-relay`. It
owns drive work only and decides five things: a new ask gets a lane, an owner ruling is applied everywhere, an escalation
gets one answer, a conflict between two owner rules is settled in one `report`, and a desk's output is relayed unchanged. A
hook that blocks it gets a fix lane like any session's. Any other tooling defect it hits goes out as
`cci post --lane root --kind report --to owner-relay --text 'for meta: <the defect, any refusal verbatim>'` and the work proceeds on the hand path.

**The meta lane** is a standing session bound to lane `meta`. It owns every skill, tooling, prompt, hook
and process change but the fix of a hook blocking a session, and every meta order the owner sends. It reports only to the
communicator; the coordinator hears from it only one `decide` line, when it must act on a drive lane
(`meta_records`). Each item ends in a merged PR with tests, which the meta lane installs itself (R14),
and it is done only when installed where it runs and seen working.

**The signal lane** is a standing Sonnet session bound to lane `signal`. It tails the coordinator's whole
cci feed and posts the communicator pointers, seqs and transcript offsets, never summaries.

**A sub-drive** is an option the coordinator alone holds, for a big project that shares no PR, file, plan
step or release target with the drive and needs lanes of its own; `lr drive sub start` refuses without
each fact.

## What runs without the coordinator

Landing (`lr landing pass`), grading (`lr grade`), lane liveness (`lr lanes`, the supervisor under launchd), Sprite stops
(`lr stop-sprites`), incident spawn, Namespace box deadlines and Mac queue waits, landed-not-live (`lr asks`), stalls
(`lr watch`), the owner's taps (`board_watch.py`), the owner update and dashboard (`lr update render --sent`) and the handoff
(`lr handoff generate`, `lr lanes handoff`) each run as a process. Each reaches the coordinator only with a decision, as
the table in [coordinator-brief.md](reference/coordinator-brief.md) lays out.

A mechanism that does not run is a defect of the drive, never a chore for a coordinator. When either
session does a row's work by hand, it sends the defect to the meta lane that turn (R14).

## Lanes and desks

A lane that ships a PR or calls a skill is `long-running:lane-ship`; any other lane or desk is
`long-running:lane`; a lane that posts to Slack is `general-purpose` on opus. Implementation lanes and
every desk run on Sprites. A brief whose duty the drive keeps for good carries `Standing: <duty>`.

- Every standing desk is an `lr`-launched lane registered to the coordinator, on a Sprite unless its
  brief says `place=local` and names why it needs the Mac. An `Agent` subagent ends with the window that
  spawned it, so neither window spawns a desk with `Agent`, and the communicator spawns only its per-message
  subagents and short read lanes, under an hour and with no `Standing:` line.
- A lane never spawns with `Agent`; it writes the brief and posts
  `cci post --kind ask --to root --text 'spawn <name> brief=<path> model=<model>'`. The meta lane launches
  its own implementers with `lr launch lane <name> opus high <brief> --lane meta`.
- A question that needs a search goes to `lr ask-lane '<question>'`, with `--mac` when it needs Slack or a
  session transcript, which no Sprite has. A question that takes more than a few reads of transcripts,
  records or logs goes through `lr bulk read` (the `bulk-read` skill), so no window reads the corpus itself.
- A lane reads its records before it merges (B2 in [bus-contracts.md](reference/bus-contracts.md)): its
  merge is refused while one another lane addressed to it sits unread and unanswered.

The coordinator's brief lists every desk it starts and what each does.

## The owner's surfaces

- Every PR goes to the owner. A PR that is green, approved and graded at its head reaches his tap card
  within one runner pass and lands on his tap. Only the classes he exempted land on a go, and nothing new
  joins them without his word (`landing_approval`). The tap rules are in [landing.md](reference/landing.md), and so is the rule for forward ports: a port lands faithful to its old-tree PR, opens with `port of #n` wherever he reads it, and a fix past its source is its own follow-up.
- Each concept has one store ([sources-of-truth.md](reference/sources-of-truth.md)); drive PRs live in the PR
  ledger, `lr board rows --json`, which every PR card, list and gate reads. A PR waits on him only when its row
  says `ready_for_tap` and not tapped, else `Not ready for your tap` with `waits_on`. No lane keeps a PR list or
  card lines; a wrong field is fixed in the PR body or its record. `cci owner-waits` prints all else he owes.
- Every other owner ask is a question card its lane draws with `lr ask-owner`, with two to four answers and one
  recommended, whose recommended answer is built on a branch as soon as it is asked, with no PR until he
  answers. A settled question never reaches the board. The card rules are in
  [orca-lane-brief.md](reference/orca-lane-brief.md) ("A design lane's part").
- Every card names the event that ends it, such as a release going live or a PR landing, and the board
  refresher withdraws it once that happens.
- While the owner is away, each recommended option is built as a branch and deployed to staging one at a
  time, with the full test and perf tables run on each, without stopping when a bar is met.
- A line to him that names something breaking names the plan to fix it beside it, and no card option has a loss
  and no fix. An option acting on a set names each member, its PR link and what it does for a user; a judge warns.
- A change to anything he sees, a card, status line, board row, Slack post or dashboard, is designed for how he reads and
  taps it on his phone before it ships; the fastest edit is not the bar. A Slack message link is posted as its permalink and Slack draws its preview; nothing hand-draws one. A PR link shows the preview cc-slack draws from the PR ledger.
- Nobody calls `AskUserQuestion`; an urgent yes/no is the same card sent to his Slack with `cc-present ask`.
- Every `gh` write posts under his GitHub login, so an approval or a tap verdict cites `board verdict #<seq>`
  or the `owner_login_verdicts` hook refuses it.
- The drive writes one automated Slack message, the merge card, posted at the root of his DM with its own
  thread; everything else is a post he named. Every update he gets is `lr update render`, sent by the
  communicator unchanged with its gaps shown, and the dashboard shows the same rows.

## Records and the task list

Lane messages, decisions, heads, contracts, blockers and asks live on cci; the formats and where every
other kind of state lives are in [bus-contracts.md](reference/bus-contracts.md).

- An agent never writes a time in a record: cci stamps each one, and a record cites an earlier one by its
  seq. A deadline is the exception, carried as attrs: `--attr owner=<lane>` and one
  `--attr step:<n>=<clock> <what>` per step (`deadline_plans`).
- Every cci `--to`, record, reply card, status line and chat message names a lane by its alias, such as
  tap-rows, never tap-rows-1: a numbered `--to` warns, and `lr reply` rewrites one.
- Lane liveness and standing duties live in the supervisor's registry, which the coordinator changes
  only through `lr request`; PRs live in the PR ledger (`lr board rows`). A decide's prose changes no row.
- The task list is the coordinator's: it runs `TaskCreate` the turn it spawns a lane or an ask arrives,
  completes a task only after consuming the deliverable, and reconciles the list against the registry at
  every handoff.

## Start and resume

```sh
LEDGER=$(ledger.py init --title "desk: $DRIVE")
drive.py start --ledger "$LEDGER" --orca-run <run> [--state-dir ~/.claude/scratch/<slug>] [--program <plan slug>]
lr owner start --drive <drive>
lr supervisor install --config <state dir>/lr.json
lr dashboard install
lr launch coordinator <state dir>/root/brief.md
lr launch meta <state dir>/meta/brief.md
lr launch signal <state dir>/signal/brief.md
lr launch chores <state dir>/chores/brief.md
```

| Session | Lane | Model and effort | Brief |
|---|---|---|---|
| `coordinator` | `root` | `fable high` | [coordinator-brief.md](reference/coordinator-brief.md) |
| `meta` | `meta` | `opus medium` | [meta-lane-brief.md](reference/meta-lane-brief.md) |
| `signal` | `signal` | `sonnet low` | [signal-lane-brief.md](reference/signal-lane-brief.md) |
| `chores` | `chores` | `sonnet low` | [chores-lane-brief.md](reference/chores-lane-brief.md) |

The communicator fills each brief, then launches the four as sibling Orca sessions, each bound to its own
lane. `lr launch` posts to the orca runner when one is up and otherwise launches from the communicator's
terminal, which coordinates the Run, since the coordinator has not started a runner yet. The coordinator
runs its own subscribe line (R9), then `lr desks start` before any lane: the Run binding and every standing
desk its brief names, each desk with a `Standing:` line. The communicator's lines above start no runner and
no desk. Every lane registers its branch prefix at spawn with `ledger.py register`.

`lr owner start` subscribes the communicator: the owner's taps, asks, decides, blocks and incidents wake it
on arrival, and reports, notes, answers and other routine kinds reach it together, at most every five minutes
and at once ahead of any other record.

A resumed communicator joins with `drive.py start --drive <id> --ledger <id>` and checks the standing sessions
with `lr lanes root meta signal`; SessionStart brings back any lane's own subscription and watches, says so,
and lists its other live tasks. A resumed or relaunched coordinator follows its catch-up in
[compaction.md](reference/compaction.md) before any other work.

## Add a rule to this skill

State a new rule once, in the file that owns its topic, under the heading of what a reader is doing there, with
its actor, trigger and clock, rewriting and refactoring the section it touches, never a bare append. Leave out dates, quotes, record ids and the story of how it was learned; the
ledger and cc-notes hold those. Keep this file a table of contents plus what every lane needs:
`tests/test_skill_size.py` holds its ceiling.
