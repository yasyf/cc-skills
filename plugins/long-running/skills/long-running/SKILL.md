---
name: long-running
description: How a multi-lane drive runs without a coordinator as its bottleneck. The session the owner types in is the communicator; a sibling Orca session bound as lane root is the coordinator; a standing meta lane takes tooling builds and defects, while a ruling on how the drive works lands in the skill the turn it is made; every question has a verb of the drive tool lr; landing, grading, lane liveness, incident spawn, release tracking and owner updates run as processes that reach the coordinator only with a decision to make. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
---

# Long-running orchestration

A drive's coordinator is a serial decision point: every read it makes and every decision
routed through it waits in a queue with no clock, and when it saturates the work
stops in silence until the owner notices ([lessons.md](reference/lessons.md), Nov 2
2026). This skill exists so nothing reaches such a queue again.

A drive's top level is two sessions, and the whole skill is built around that split. The
**communicator** is the session the owner types in; its whole job is him. The
**coordinator** is a sibling Orca session the communicator launches at drive start,
bound as lane `root`: it spawns a lane for every ask with a body the turn it arrives,
decides what a process escalated, and relays what the desks produce. Beside them
stand the **meta lane**, which takes every improvement to this skill and its tooling,
and the **signal lane**, which tails the coordinator so the communicator never has
to. Everything else is a verb of the drive tool `lr` or a process that runs without
any of them. A rule here names the verb or the process that makes a failure
impossible; a rule with neither is a defect to fix in the pack.

This skill sits on `~/.claude/CLAUDE.md`, which owns fan-out shape, model routing
and verification depth. The pack is one Go binary, `lr`, with the Python scripts
it replaces kept only until each verb goes live
([design](https://github.com/Forge-AI/design-docs/tree/main/design-docs/long-running-v2)).

| Topic | File |
|---|---|
| Landing and the merge queue, D1 to D23 | [reference/landing.md](reference/landing.md) |
| Grading heads, landings and plans | [reference/grading-principles.md](reference/grading-principles.md) |
| The orca runner and Orca workers | [reference/orca-desk-brief.md](reference/orca-desk-brief.md), [reference/orca-workers.md](reference/orca-workers.md) |
| Writing a lane brief; the briefs of the coordinator, the meta lane and the signal lane | [reference/orca-lane-brief.md](reference/orca-lane-brief.md), [reference/coordinator-brief.md](reference/coordinator-brief.md), [reference/meta-lane-brief.md](reference/meta-lane-brief.md), [reference/signal-lane-brief.md](reference/signal-lane-brief.md) |
| Sub-drives | [reference/sub-drives.md](reference/sub-drives.md) |
| cci records and the lane bus | [reference/bus-contracts.md](reference/bus-contracts.md) |
| Incidents, releases, Slack | [reference/active-alert-brief.md](reference/active-alert-brief.md), [reference/release-lanes.md](reference/release-lanes.md), [reference/slack-lane-brief.md](reference/slack-lane-brief.md) |
| Owner rulings, the register, judges | [reference/owner-rulings.md](reference/owner-rulings.md) |
| The lane supervisor and the watches; why a lane cannot go quiet | [reference/watchers.md](reference/watchers.md), [reference/supervisor.md](reference/supervisor.md) |
| Reading a large corpus into fix lanes | [the bulk-read skill](../bulk-read/SKILL.md) |
| Compaction and the owner update | [reference/compaction.md](reference/compaction.md), [reference/owner-report-table.md](reference/owner-report-table.md) |
| What went wrong before, by date | [reference/lessons.md](reference/lessons.md) |

## When this applies

Three or more lanes at once, a task that outlives one context window, a wait on
CI, a queue, an apply or a soak, or a sweep over many independent units. A
single-lane investigation is one subagent in direct mode, not a drive.

## Judgment first

- No rule forbids reading the one line a decision turns on.
- A finding in hand is a fix, never a lane, a re-audit or a workflow.
- Read each owner sentence as a careful colleague would. A correction names a defect in the reading, never a redesign of something he ruled on earlier; read the register's rows on that subject before acting. A repeat is a defect report about the first reading: fix every place it survived, that turn.
- A tooling refusal goes to the meta lane the same turn and the work proceeds on the hand path; it is never an excuse and never a question for the owner.
- A change to how the drive works, such as a ruling on routing, waits, cards,
  reactions, guards or who answers what, goes into this skill, a brief template, a
  capt-hook hook or a cc-notes doc the turn it is made. A session memory is a pointer at most.
- Hooks warn. They block a shell wait, which he ordered, and otherwise only on trust: a merge without the owner's approval, a
  change to who approves landings, a revert without his word, a runner or desk
  started from the communicator, hook-file edits and the session-protection guards. A failed read only warns; `tests/test_fail_open.py` enforces it and lists the exceptions.

## Phase 0: the standing sessions

**Binding.** Every session of a drive is bound to one lane name, and every hook,
stop check, injection and cci delivery keys on that binding and nothing else. An
unbound session hears no pack hook but those that bind it (`tests/test_unbound_silence.py`). The
communicator's window is bound `owner-relay`; the coordinator's is bound `root`, so
every coordinator hook and every record addressed to `root` lands on the coordinator and
none of it on the communicator. The hooks come in three tiers on one switch:
shared hooks (context discipline) act in both windows; coordinator hooks (asks,
coverage, landing, launch, incident checks, the task list, the
compaction handoff) act only in the session that ran `/long-running:coordinator`,
which also claims the cci lane with `cci announce --lane root`; communicator hooks
(plain words, owner words and picks, claims to the owner, confirm-back, failure
reports naming what stops a recurrence) act only in the window that ran
`/long-running` alone. A lane's window is bound to its lane name. Because hooks are
window-scoped, the coordinator is never an in-process subagent of the communicator:
the two never `SendMessage` each other, and the bus is their only channel, since a
record survives a compaction where a message does not.

**The communicator** routes and spawns, like the coordinator, and does no work
inline. Every owner message, whether chat, his Slack DM, a Slack thread or a board
tap, becomes one spawned subagent (`Agent`, `general-purpose`, opus), which reads
what the answer needs, does the work or routes it, and sends the reply itself with
the `lr` reply, cc-slack and cci tools. The communicator's own calls for a message
are `slack_react`, `owner_mark`, the `cci post` of its routing record and the
spawn; it writes no file, reads no Slack thread and runs no multi-step lookup
([communicator-brief.md](reference/communicator-brief.md#each-message-is-a-subagent)).
The reply lands where the message came from: a chat message is answered in chat, a
Slack thread message in that thread, a board tap on that card, and no answer moves
to another surface. Each answer comes that turn, in his terms: the "why" with the
mechanism of the drive's own failure, the pick on his board. The
ledger hook records his words verbatim as they arrive (`lr owner`); nobody appends
them to a rulings file by hand or pastes them into a forward (owner, Nov 3).
The communicator names each row to the coordinator as one `cci post --lane owner-relay
--kind ask --to root --text 'owner:m47 <one-line order>'`, so the coordinator reads it
at its next tool call. A
ruling of his on how the drive works is written into this skill by the subagent
that takes his message, in that turn ([Meta changes land in the skill](#meta-changes-land-in-the-skill)).
Tooling he asks to have built goes to the meta lane, as `--kind ask --to meta`, and
so does a tooling defect the coordinator reports. Meta records go to the meta desk only, and the coordinator
hears one line only when it must act on a drive lane. It is the drive's presentation
layer: it reads what the
coordinator addresses to it (`--kind report --to owner-relay`) and synthesizes what
he would want from it, never forwarding the coordinator's text as written. Only the
communicator owes him his own terms (R21): plain words, Pacific times, the thing's
name never a codename or id, the fix and when it is live rather than how it broke, the human
layer before the agent layer ([communicator-brief.md](reference/communicator-brief.md)).
Every cci `--to`, record, reply card, status line and chat message names a lane by its alias, such as tap-rows, never tap-rows-1: a numbered `--to` warns, and `lr reply` rewrites one.
It owns the dashboard and the owner updates: one generator, `lr update render`,
feeds both from the same rows, run by its one standing helper `update-desk`
([owner-report-table.md](reference/owner-report-table.md)). A dashboard restart is
`lr dashboard restart`, which a subagent of the communicator runs and never hands the
owner; a changed dashboard goes live only with `--proof` ([drive-dashboard.md](reference/drive-dashboard.md#going-live)).
His ruling on a dashboard surface ships its text change within 15 minutes, with one screenshot of that page, even while the full fix is still in a PR.
An order of his gets no acknowledgement and no promised time: he hears back
once, when it is live or decided, in one sentence, and a question gets one sentence.
`owner_replies_stay_short.py` warns and never blocks, in the window, on the `reply` tool and his Slack
DM, a reply that only acknowledges or promises a clock, one over two sentences that is
not an update, a return update or a pick card, and a mechanism, diagram or explainer
link he did not ask for. Only a turn where he asks how a system works gets a hosted
one-pager: `lr explain <topic> --from <markdown or record>` hosts it and sends him its
link, and the communicator's hooks prompt for it and refuse that turn's reply without
one, unless the verb ran and failed, in which case it has filed a defect to the meta desk.
When he says a design is wrong at its root, the drive runs the whole redesign
sequence that turn, with no further word from him: `lr explain redesign` withdraws the
cards, holds the PRs, hosts the page and opens a design lane that builds each
recommended option as a branch, never a PR
([communicator-brief.md](reference/communicator-brief.md#redesigns)).
When the owner writes after an hour or more away, its reply opens with the
dashboard link, then what changed in a few lines; the full table lives on the
dashboard. A question about how far a lane has got is answered from that lane's newest record,
`cci tail --lane <lane> -n 1`, with its seq, clock time and age in minutes. Every
message to him goes out through one call, the `reply` tool on the plugin's
`lr` MCP server, which lands it in this window, a cci record and the dashboard's
store, adds a board card that cc-present posts to his Slack with one button per pick
when it carries `picks`, and a card at his Slack DM root when it carries links, PRs
or a status; it hand-posts no Slack or board card
([communicator-brief.md](reference/communicator-brief.md#replies)). A pick is never
plain text asking him for a letter or number. A PR the text or `prs` names is
linked by cc-slack, which posts its merge-row card from `lr board card` in the
same thread when the PR waits on his tap. `owner_returns.py` refuses a plain pick
and a stop that leaves the pick inline. Its session
holds the owner's DM: `owner_returns.py` arms a channel watch on the DM at session start
and on each prompt, so his reply in a DM thread reaches it as well as the lane that
posted there, and refuses a stop while `cc-slack dm-owed` lists a reply nobody has
answered in its thread. A reply in the thread of a card another live session posted
reaches only that session, and this one neither sees nor owes it. One session holds the DM at a time: while another live session
holds it, this one takes no watch and owes no replies. A reply unacked after 30 seconds adds one line to its next tool result. `register_capture.py` posts each such reply once as an `owner`
record with id `slack:<ts>` and attrs `ts` and `thread`, so a forward names the id instead of pasting his words. A reply under another session's card goes to that session, never to a lane of this drive ([communicator-brief.md](reference/communicator-brief.md#his-replies-under-another-sessions-card)). It talks only to the
coordinator, the meta and signal lanes and helpers it spawned for its own work,
never to the coordinator's children, and beyond the launches at drive start it posts
no `go`, `hold`, `decide` or `launch`. Every owner message lands in its message
ledger as it arrives, one row per ask, and its turn cannot end while a row is not marked responded
or addressed (the `owner_mark` tool of the `lr` MCP server; a row for what he asked to read takes `reply`, and a promise takes `due` with `by`). The turn's final text in its window lands on each row its owner message opened, as `owner-relay/answers/<row>.md`, never replacing a longer answer, and marks an open row responded. A question he types on the dashboard's review page or his board gets its row with no turn of the communicator's: `lr owner ask` routes a review-page question to the coordinator and the communicator, `lr owner received` takes one the board watch routed, and each writes the row due 15 minutes out and wakes this window with "Received, with <who has it>, answer by <clock>", the same line the page shows on his question. The communicator marks that row like any other, and past its clock it blocks the turn the same way. A coordinator `ask` or
`blocked` record that waits on him is a ledger row the moment it posts, delivered by
the cci channel the communicator subscribed to, never found by a later read: on Nov 3
the coordinator waited from 10:20 PM to 1:40 AM and he noticed before the
communicator did (owner, 1:40 AM). A board answer lands there
too, sorted by `lr board classify`: a tap or verdict goes straight to the asking
lane with a copy here and a closed row; a write-in or note comes here alone with an
open row, and the communicator routes it to the lane that asked. It also proposes:
when what it relays shows a structural fault, such as PRs queued behind one
coordinator decision, it brings him the split as an option, not only the symptom.
It never calls `AskUserQuestion`: a pick or an urgent yes/no is a `reply` with
`picks`, which reaches his board and his Slack with buttons in the same turn, or its
Stop refuses the reply. Every message it
sends is addressed to him in second person and carries only what changed for him
or what he must decide; ledger rows, hook mechanics, lane acks and "nothing new"
never appear in chat, a turn with nothing for him ends with no text, and its Stop
hook refuses any other closing message. It reads nothing to answer him: the
subagent for his message does every read.

**The coordinator** reads reader `root`, posts as `root`, runs in a foreground
`lr coordinator next` loop for the life of the drive, one turn per batch of
records, and holds lanes, gos, holds, incidents, the task
list, progress docs and the register. It launches lanes through the supervisor and
Orca (`lr launch`), never with `Agent`. It binds the Run and starts every desk
(`lr desks start`), each remote as an Orca session; the relay runs under launchd
on the Mac because it drives the Mac's Orca app and cuts Mac worktrees; the verbs that start a runner or
desk refuse the communicator. It never posts to Slack or the board and never
speaks to the owner; everything he must see is a `report` to `owner-relay`. Its
records are for machines and the communicator: terse, ids and lane names included,
in no format it owes anyone, and so are a lane's records to it. When it dies the
supervisor relaunches it from its brief and newest handoff like any lane, and the
communicator keeps answering the owner meanwhile. It owns drive work only: it never
takes a skill, tooling, prompt, hook or process improvement and never spawns a lane
for one; a tooling defect it or a lane hits goes out as
`cci post --lane root --kind report --to owner-relay --text 'for meta: <the defect, any refusal verbatim>'`
and the work proceeds on the hand path. It decides five things and nothing else:

1. **A new ask gets a lane** the turn it arrives, with a ledger row and a task
   (R6, R8). A lane holding more than two unstarted items is split.
2. **An owner ruling is applied** to every live lane and artifact the turn it
   is read (R17, R18, R19b); a settled question is never re-asked (R19).
3. **An escalation gets one answer.** The supervisor, the landing pass, the
   stall watch and the update generator reach it only with a `decide`, `ask`,
   `blocker` or `incident` record; it answers each with one record naming the
   lane that acts. A resolution naming no fixer, no `FIX-LIVE` and no reason is
   refused by the pack.
4. **A conflict between two owner rules** is settled in one `report` to the
   communicator, naming the rule applied.
5. **A desk's output is relayed** unchanged: a milestone as a `report` to the
   communicator, a tap card by saying nothing while the runner lands on the tap.
   A gap the communicator's update names comes back as a `blocker` and gets a
   record naming the lane that acts.

Its own work on a deliverable, its own reads past one bounded window and its own
waits are each a defect the hooks name, and so is a queue of PRs waiting on one of
its decisions: a decision the owner's picks already settle moves to a package that
applies them by rule, and one they do not settle is his, on the board. Every
escalation target, brief, template, handoff, task list, incident checklist and
dashboard in the pack names the coordinator for a decision, names
`owner-relay` where the owner must see something, and names the meta lane where a
skill or tooling fix is owed.

**Meta changes land in the skill.** Every globally applicable meta change, any
ruling on how lanes, cards, waits, replies, hooks, routing or answers work, is
written into this skill, or into the brief template, hook or cc-notes doc that owns
it, in the turn it is made, as a PR merged and installed that turn. A memory file or
a cci record to another lane is at most a pointer and never counts as done. The
session that received the ruling owns the write, the communicator through the
subagent it spawns for that message, and never hands it to the meta lane.

**The meta lane** is a standing session the communicator launches at drive start
from [meta-lane-brief.md](reference/meta-lane-brief.md), bound to lane `meta`.
Every tooling build, defect and improvement that no ruling settles goes there, and
so does every owner order to build tooling. It reports only to the communicator. The
coordinator hears from it only one `decide` line with no item number, when it must
act on a drive lane such as a stand-down or a relaunch. The `meta_records` hook
refuses any other record a meta lane sends `root`. `lr launch meta` records its role
as `meta` in the lane registry, and a successor keeps it whoever launches it. The orca runner and the lane supervisor send each
notice about a meta lane to its launcher, and each `HOOK-DRIFT` and `BASE-DRIFT` blocker to the meta
desk. It keeps `<state dir>/meta/items.md`, one row per item with the owner's words,
the lane, the state, the PR, what blocks it and its Due, and the communicator reads
only that file for meta items in the owner's updates. Every open row carries a Due:
the Pacific time by which its next step must have happened, `3:04 PM`, with `Jan 2`
after it when that is not the day the file is written. The stall watch pages
`owner-relay` and the meta lane once when a Due passes or an open row has none, pages
again only after the row changes, and never pages a row under `## Done`. Beside it,
`meta/programs.md` holds one row per meta program with its state and blocker, and
every owner update carries it as its Drive tooling section. Each item ends in a merged PR with
tests, which the meta lane installs itself (R14).

The meta lane also finds waste itself; a person noticing it first is the failure.
`lr waste` prints a ranked list under 4 KB for a window, last hour by default, with no
LLM call, and the supervisor runs it hourly and sends the meta lane one note of what is new
or has grown; a quiet hour sends nothing. The meta lane reads it each time it posts and
opens an item for what it finds, naming the signal; a lane warning about clobbering or
racing opens a fix, never a report. A meta task is done only when its change is installed
where it runs and has been seen working: its `done` record carries `--attr installed=` and
`--attr proof=`, a lane that only merged posts `landed`, and `lr waste` flags a merged meta
PR older than 30 minutes with no such record. A standing lane's `done` closes that item, never the lane. `lr waste` also flags an owner Slack reply older
than two minutes with no in-thread answer, from what `cc-slack dm-owed` has stored, and ranks Codex compactions per lane hour, counted from the Codex session files bound to the drive's lanes, with the lane that lost the most turns named (floor 6 an hour; a 200,000-token limit measured 1.8, a 64,000-token one 17.9). The
`waste` section of `runner.json`
holds `lane` (the meta lane's name, default `meta`), `ignore` (recipients that never post
by design), `growth` (the factor a repeated finding must reach to post again, default 2),
`fan_out_recipients`, `transcripts` (the most it reads an hour, largest first), `hook_logs`,
`merge_lookback_hours`, `codex_sessions` (the Codex sessions directory, default `$CODEX_HOME/sessions` or `~/.codex/sessions`),
and `floors` per signal.

**The signal lane** is a standing Sonnet session the communicator launches at drive
start from [signal-lane-brief.md](reference/signal-lane-brief.md), bound to lane
`signal`. It tails the coordinator's whole cci feed and posts the communicator
pointers, seqs and transcript offsets, never summaries.

**A sub-drive** is an option the coordinator alone holds, for a big project that
shares no PR, file, plan step or release target with the drive and needs lanes of
its own; `lr drive sub start` refuses without each fact
([sub-drives.md](reference/sub-drives.md)).

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
| What an incident is doing | `lr incident status <slug>` |
| Where a lane's mail goes; every `lr` verb takes a stem, `meta-desk`, for its newest live lane | `lr route <lane>` |

A read the table lacks belongs to a lane: a PR, a diff, a CI log, a Slack thread,
a plan artifact, a long file, a cloud listing, a build page. The coordinator spawns
a reader and takes back at most five lines; a search is one call, `lr ask-lane '<question>'`. The `root_context` and `root_reads`
hooks warn on every read of those kinds, on a second source opened for one owner
ask, and on a lane's records piped through `tail` or `grep`; in the communicator's window `root_context` refuses a wide search and names `lr ask-lane`.

## What runs without the coordinator

| Mechanism | Owns | Reaches the coordinator with |
|---|---|---|
| Landing (`lr landing pass`) | Every open PR: gates at the current head each pass, enqueues a PR the owner tapped or a go-carrying exempt class, restacks, resubmits an eviction, routes a red to its lane. Holds come only from `--kind hold` records. | One `landing-state` line per pass for a PR with every gate and no tap or go |
| Grading (`lr grade`, the finder and fidelity desks) | A finder and a fidelity grade, within ten minutes, of every head a lane owns or the owner tapped; never a draft | Nothing; a verdict goes to the lane |
| Lane liveness (`lr lanes`, the supervisor under launchd) | One registry row per lane, relaunch from brief and newest handoff, rotation in place, a `Standing:` duty with no live owner for ten minutes; a lane that posted `done` or `stopped` is retired for good | One `RELAUNCHED` record to the meta desk per relaunched duty; one `blocker` per duty held after three deaths in an hour or ended on an order; one `defect` per lane nothing proves dead |
| Sprite stops (`lr stop-sprites`, run by the supervisor) | The Sprite of every lane that ended, stopped once with `cc-remote orca stop`; a live, held or unsettled lane keeps its Sprite | One `blocker` per stop that fails |
| Incident spawn (the supervisor's Alerts check) | An alert with no `incident` record and no fix lane after three minutes: the supervisor launches the fix and evidence lanes itself; a fix lane that misses gets its Opus fallback on `incident-opus`, the only alias that runs Claude fast mode | The spawn record; a `blocker` only when the launch fails |
| Namespace box deadlines and Mac queue waits (the supervisor's nsc and MacWaits checks) | Every box `nsc list` shows with a deadline that `lr nsc create` or `lr nsc extend` recorded on the bus. An hour before the deadline the supervisor extends the box by its create duration while its lane is live and tells the lane; a refused extend warns the lane to recreate the box. When the drive's `runner.json` names `landing.mac_queue` (`org`, `queue`, `limit_vcpu`) and a CI job on that queue has waited ten minutes, the supervisor notes the lane holding each of the longest-held macOS boxes, one box per waiting job, naming the job, how many macOS boxes lanes hold against the limit and the hand remedy (cancel the job, retry it; never inside a trunk-merge build without asking); `lr nsc create` of a macOS shape prints the same count and limit. It touches no box and no job | One `urgent` for a box whose lane is retired or that has no lane label, which is then left to expire; one `report` per holder per Mac wait |
| Landed, not live (`lr asks`) | A landed PR whose release target has shipped nothing in an hour | One `blocker` per target with the exact release line |
| Stalls (item deadlines, `lr watch`) | Work that stopped moving, read from the registry; a record naming a deadline with no plan behind it | Only a stall whose owner is the coordinator; a planless deadline pages `owner-relay` |
| The owner's taps (`board_watch.py`) | Every tap, pick and write-in as an `owner` record; the runner lands on it | A pick with no lane at 30 minutes, as an `ask` |
| The owner update and the dashboard (`lr update render --sent`, run by `update-desk`) | Every plan step, workstream, live lane under its step, open ask, pick, incident and release today, PR with a go owed or hold; every row with who is on it or **nobody**, what blocks it, what unblocks it and by when, what went wrong; unowned work first; exit 1 naming anything missing | The rendered update, which the communicator relays and the dashboard shows |
| The handoff (`lr handoff generate`, `lr lanes handoff` before a compaction) | The pre-compaction doc with the lane registry and the rendered update | Nothing but the coordinator's own narrative |

A mechanism that does not run is a defect of the drive, never a chore for a
coordinator. When either session does a row's work by hand, it sends the defect to
the meta lane that turn (R14).

## The rules

Ids are stable; scripts, hooks and references cite them. `root` means the
coordinator unless the rule names the communicator.

**R1.** Ground truth comes from the owning lane or the verb, never a grep, a
listing, a curl or a build page; at most five lines back. Priority PR state is
read in one batched `ccx vcs pr status` call.

**R2.** Anything with a body is a lane: a read past one bounded window, a log, an
investigation, PR or merge mechanics, an enumeration. One bounded read of one named
file, up to 150 lines, is allowed.

**R3.** One lane per wait-then-do chain, and no lane parks: a lane waits on a `Monitor`, an Agent notification, `CronCreate` for a clock,
or one `lr wait` call, to a terminal state, and reports once. `shell_waits` blocks a shell loop or sleeper.

**R4.** Never re-brief a lane, never answer a duplicate. A `SendMessage` to a
finished agent re-runs its brief. `lr wait` marks printed mail read.

**R5.** Write it down, do not hold it: findings to cc-notes from the lane that
found them, briefs as attachments on the `briefs: <slug>` log, nothing hand-made
under `~/.claude/scratch`.

**R6.** Every owner request is in flight the turn it arrives, one lane per ask,
never appended to a busy lane's queue. A lane owns one PR per deliverable and
lands its fixes as commits on it; `reference/orca-lane-brief.md` says when it stacks.

**R7.** A PR's state is read before it is reported: `ccx vcs status` or the squash
commit on fetched trunk, never a message or memory.

**R8.** Every owner ask is a ledger row the turn it arrives, and done only at
`LIVE`; before that the answer is "in #N, not live yet: `<blocker>`". A landed PR
with no release in an hour reads `LANDED-NOT-LIVE` and reaches the coordinator as
a blocker.

**R9.** Orca worker traffic runs through the runner (`lr launch`), never a
check-and-ack loop. Mac load queues and refuses no launch: one that may leave the
Mac starts on a box at once, and a worker's question goes to the lane that launched
it. The coordinator receives records as batches from `lr coordinator next`, after
one `cci subscribe --drive <drive> --reader root --to-kind incident --follow oncall=incident`
keeps all but incidents off its tool calls; the communicator subscribes as
`owner-relay` for `report`, `ask` and `blocked`. A seq already handled is skipped.

**R9a.** An urgent hold gets a decision owner and a clock; the coordinator
resolves the runner's `DECIDE hold:` line ahead of any open owner card on another subject.

**R10.** Landed is the only progress: landed, queued, live, or the exact blocker
naming the PR, its head and the gate. A red gets a structural fix with a test,
never a retry.

**R11.** Hands off deploys, and an owner's order carries every approval. Nobody
starts, approves, watches or re-cuts a release unless the owner asked. When he
orders one, the release lane applies and counts the deletes, replaces and
migrations its plan names and never asks again; the `owner_order_is_the_approval`
hook refuses a second word. An incident fix rides the next regular release when one
can cut within its urgency and keeps the zero-delete bound unless he ordered it. A
regular release is the default; an emergency release runs only when the regular
path is too slow for a live customer impact, never twice for one fix. No release is
canceled once its apply has started. A release at its approval gate five minutes
after an owner order is a blocker to the coordinator, never a wait, and so is an order no
release record carries five minutes on: the coordinator posts the order with `--attr
order=<answer id> --attr lane=<release lane>`. A cluster leaves the code
and ships before it is destroyed ([release-lanes.md](reference/release-lanes.md)).

**R12.** Every red goes to its owning lane the same turn with a 15-minute
deadline; a red with no live owner gets a lane.

**R13.** Every owed item is dispatched the same turn, one lane each. A fix or
review response owed on an open PR goes to the lane that owns it, as a commit on
that PR, never a new lane or a stacked child.

**R14.** A process fix is a PR the turn it is adopted, driven to merge, release
and install by its lane, proven on the record that reported the defect. A tooling
defect, refusal or repeated chore goes to the meta lane that turn, which gives it
an implementer; the coordinator never spawns one. One lane installs per night. An install
interrupts nothing: the supervisor takes a release between ticks and the relay between passes. No new Python enters the pack; a fix to a Python
script is a Go verb.

**R15.** CI is the verifier and runs every test. Before it pushes, a lane runs `go build` and `go vet` over the Go packages it touched and the typecheck over the TypeScript it touched, which the `vet_before_push` hook enforces;
the `local_builds` hook refuses every other local test, build and lint. Batch jobs run on in-region compute.

**R16.** An active alert gets its fix lane the same turn, without a coordinator:
the supervisor launches the fix and evidence lanes for an alert nobody answered in three minutes.
When an alert reaches the coordinator first (a link the communicator relayed, an `#outage` report, a page, a runner `PAGE`, a failing release check, an `ALERT-UNOPENED` blocker), it runs `lr incident run <slug> --fix-brief <file>`
before anything else, with no real-or-not verdict and no mute treated as resolution. Fixes land forward; a rollback runs only on the owner's own word. Only his explicit order to mute an alert runs `lr alert mute <monitor> --why '<reason>'`, which mutes it, stands down its lanes, and keeps every launch off it.
Each incident has one actor: when `tools/ci oncall incidents` shows an open ai-oncall incident covering the alert, the fix lane hands its findings and requests to that incident's commander with `ci oncall connect` and `ci oncall say`,
and never takes it over: `ci oncall takeover` runs only on the owner's own words, since it records the takeover in the owner's name. It investigates freely and never ships, rolls back, freezes, mutes, pages or posts beside the commander,
and another drive's sessions follow the same rule (retro AI12, page 4oov3m; page eu7ayk, two lanes took over in the owner's name when connect was refused during commander startup).

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

**R20.** A Slack link from the owner is an assignment: the communicator queues it,
the coordinator spawns the Slack lane and the doing lane that turn in one batch,
and one `report` to the communicator names both. Nobody reads Slack or writes
Slack copy outside the Slack lane.

**R22.** Every brief quotes the owner's design verbatim with the entry point at
`file:line`; the lane posts a `contract` before `READY-FOR-SHIP`; the design ships
whole or holds, never split. A ruling that changes a data model, namespace layout or
embedding model is a design order: first a one-pager with its open choices as specific
cards, and building only after he has read it or said build.

**R23.** Every finished agent is stopped the turn its completion arrives, and
before any spawn; `lr gc` reclaims a settled lane's Orca workspace and terminal the
same pass, and a new lane starts remote unless it drives the Mac's Orca app.

**R24.** Every production operation is one line in the cc-notes log
`production-operations` that turn; incident lanes read its last day before naming
a mechanism.

**R25.** A deadline comes with a plan. A record, update line or card that states a
time something must happen by names the lane that meets it and the steps, and the
pack refuses one without them.

## Lanes and desks

A lane that ships a PR or calls a skill is `long-running:lane-ship`; any other lane
or desk is `long-running:lane`; a lane that posts to Slack is `general-purpose` on
opus. Implementation lanes and every desk run on Sprites. A brief whose duty the
drive keeps for good carries `Standing: <duty>`. Every standing desk is an
`lr`-launched lane registered to the coordinator, on a Sprite unless its brief says
`place=local` and names why it needs the Mac; an `Agent` subagent ends with the window
that spawned it, so neither window spawns a desk with `Agent`, and the communicator
spawns only its per-message subagents and short read lanes, under an hour and with no
`Standing:` line. A lane never spawns with `Agent`;
it writes the brief and posts `cci post --kind ask --to root --text 'spawn <name>
brief=<path> model=<model>'`. The meta lane launches its own implementers with
`lr launch lane <name> opus xhigh <brief> --lane meta`. A question that needs a search goes to `lr ask-lane '<question>'`, with `--mac` when it needs Slack or a session transcript, which no Sprite has: a Sonnet lane answers the asker in one `answer` record naming the seqs and paths it read, then ends, and the asker's turn never waits on it. A question that takes more than a
few reads of transcripts, records or logs goes through `lr bulk read` (the `bulk-read`
skill), so no window reads the corpus itself. A lane reads its records
before it merges (B2 in [bus-contracts.md](reference/bus-contracts.md)): its merge is
refused while one another lane addressed to it sits unread and unanswered, and the
one refusal prints all of them.

| Desk or lane | Does | Brief |
|---|---|---|
| Coordinator | Everything in [Phase 0](#phase-0-the-standing-sessions), every former coordinator role, and every runner and desk in this table but `update-desk`, which it starts with `lr desks start` | [coordinator-brief.md](reference/coordinator-brief.md) |
| Meta lane, the communicator's | Every tooling build, defect and improvement no ruling settles, and every owner order to build tooling; `items.md`; one implementer per item | [meta-lane-brief.md](reference/meta-lane-brief.md) |
| Signal lane, the communicator's | Tails the coordinator's feed and posts the communicator pointers, never summaries | [signal-lane-brief.md](reference/signal-lane-brief.md) |
| Landing pass and `landing-desk` | The merge queue, lane reports, the ledger; both remote | [landing.md](reference/landing.md), [landing-desk-brief.md](reference/landing-desk-brief.md) |
| Orca runner, the relay | Launches and relays to Orca workers, starts grading jobs; on the Mac, the one desk that is not remote | [orca-desk-brief.md](reference/orca-desk-brief.md) |
| Lane supervisor | Liveness, relaunch, rotation, standing duties, alert launch, landed-not-live, Sprite stops | [watchers.md](reference/watchers.md) |
| Incident lanes, `alerts-desk` | One incident from intake to final reply; monitor transitions as records | [active-alert-brief.md](reference/active-alert-brief.md), [alerts-desk-brief.md](reference/alerts-desk-brief.md) |
| `update-desk`, the communicator's one helper | Renders the owner update the communicator sends, writes the dashboard's rows from it, keeps the dashboard app up | [communicator-brief.md](reference/communicator-brief.md), [owner-report-table.md](reference/owner-report-table.md) |
| `finder-desk`, fidelity desks, `red-sweep` | Grade every head; own every red with no owner | [finder-brief.md](reference/finder-brief.md), [fidelity-desk-brief.md](reference/fidelity-desk-brief.md), [red-sweep-brief.md](reference/red-sweep-brief.md) |
| Board refresher, `drive-watchdog`, `user-test` | Tap cards and records; the retro log; the owner played against each surface | [drive-watchdog-brief.md](reference/drive-watchdog-brief.md), [user-test-brief.md](reference/user-test-brief.md) |
| Priority desk, merge train, sequencer, Slack lane, handoff writer | One number-one outcome; the hot set; a wait-then-do chain; a named post; a rotating lane's handoff | [priority-desk-brief.md](reference/priority-desk-brief.md), [merge-train-brief.md](reference/merge-train-brief.md), [orca-lane-brief.md](reference/orca-lane-brief.md), [slack-lane-brief.md](reference/slack-lane-brief.md), [handoff-subagent-brief.md](reference/handoff-subagent-brief.md) |

## The owner's surfaces

A line to him that names something breaking names the plan to fix it beside it, and a card option is never drawn with a loss and no fix.

Every PR goes to the owner. A PR that is green, approved and graded at its head
reaches his tap card within one runner pass and lands on his tap; only the classes
he exempted land on a go, nothing new joins them without his word, and the
`landing_approval` hook refuses a record or brief that changes it. A tap carries
to a later head only when its own diff is the same, or fidelity judges its behavior
and scope unchanged since the head he tapped; any other head returns to his board saying what changed. A stale PR or a draft never reaches his board. Every other owner
ask is a question card from `drive.py ask-owner` with two to four answers and one
recommended. The recommended answer is built on a branch as soon as the question is
asked, with no PR until he answers, so he tries the thing itself. Its option's
`build` names the branch (`--branch OWNER/NAME:BRANCH`), or `build.none` says why a
pick such as a process choice has nothing to build. `board_text_gate` refuses a card
naming neither an hour after its first draw, and a branch he rejects is deleted. A settled question never reaches the board; a card he touched stays
until he submits the round; nobody calls `AskUserQuestion`, and an urgent yes/no is
the same card sent to his Slack with `cc-present ask`. A card proposing a change to
an existing artifact sets its `design`, carrying a screenshot of the live artifact,
the change rendered inside it, and a cite of its design record; cc-present refuses
the card without them ("A design lane's part" in
[orca-lane-brief.md](reference/orca-lane-brief.md)). `board_watch.py` records every
tap as an `owner` record and a cc-notes answer. Every `gh` write posts under his
GitHub login, so an approval or a tap verdict a drive posts cites
`board verdict #<seq>`, the `owner` record of his verdict on that PR's row at its
current head, or the `owner_login_verdicts` hook refuses it. A PR waits on him only when its row in `lr board rows --json` says `ready_for_tap`, and a line saying otherwise is refused. That row is the only list of drive PRs: no lane keeps its own, `lr board waits --pr <n>` reads what one waits on, and a PR's state changes by posting its record, never by asking for a hand entry;
anything else waiting on him is what `cci owner-waits` prints, never a runner note. The drive writes one automated Slack
message, the merge card, posted at the root of his DM with its own thread; everything else is a
post he named. Every update he gets is `lr update render`, sent by the communicator
unchanged with its gaps shown, and the dashboard shows the same rows.

## Records and the task list

Lane messages, decisions, heads, contracts, blockers and asks live on cci. An agent
never writes a time in a record: cci stamps each one, and a record cites an earlier
one by its seq. Lane
liveness, PRs and standing duties live in the supervisor's registry, which the
coordinator changes only through `lr request`: `done <lane>` retires a lane,
`owner <lane> --pr <n>` moves a PR, and `stand-down <old> --successor <new>` replaces a
lane. A decide's prose changes no row. Owner asks,
PR rows, holds and landings live in the ledger. Findings, notes, docs and logs live
in cc-notes, standing owner rules in the register doc, production operations in
the `production-operations` log, progress and handoffs in generated progress docs,
and meta items in the meta lane's `items.md`. The task list is the coordinator's:
it runs `TaskCreate` the turn it spawns a lane or an ask arrives, completes a task
only after consuming the deliverable, and reconciles the list against the registry
at every handoff.

A record that names a deadline, a clock time after by, before, until or due, or a
`deadline` attr, carries its plan as attrs: `--attr owner=<lane>` and one
`--attr step:<n>=<clock> <what>` per step, each step leading with its Pacific time
and holding no comma. A reply to it carrying those attrs, or a record that resolves
it, settles it too. The `deadline_plans` hook refuses a Bash `cci post` or a cci post
tool call that names a deadline without them, and the board text gate flags a card
that names a deadline beside no lane. The stall watch pages `owner-relay` once for
a deadline record that slips through, naming the record, its deadline and the lane
that posted it, and closes that page when the plan arrives.

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
| `coordinator` | `root` | `fable xhigh` | [coordinator-brief.md](reference/coordinator-brief.md) |
| `meta` | `meta` | `opus xhigh` | [meta-lane-brief.md](reference/meta-lane-brief.md) |
| `signal` | `signal` | `sonnet low` | [signal-lane-brief.md](reference/signal-lane-brief.md) |
| `chores` | `chores` | `sonnet low` | [chores-lane-brief.md](reference/chores-lane-brief.md) |

The communicator fills each brief, then launches the four as sibling Orca sessions,
each bound to its own lane. `lr launch` posts to the orca runner when one is up and
otherwise launches from the communicator's terminal, which coordinates the Run,
since the coordinator has not started a runner yet. The coordinator runs its own
subscribe line (R9), then `lr desks start` before any lane: the Run binding and every standing desk its [brief](reference/coordinator-brief.md)
names, each desk with a `Standing:` line. The communicator's lines above start no
runner and no desk. Every lane registers its branch prefix at spawn with
`ledger.py register`.

`lr owner start` subscribes the communicator: the owner's taps, asks, decides, blocks
and incidents wake it on arrival, and reports, notes, answers and other routine kinds
reach it together, at most every five minutes and at once ahead of any other record.

A resumed communicator joins with `drive.py start --drive <id> --ledger <id>` and checks the standing sessions with `lr lanes root meta signal`; SessionStart brings back any lane's
own subscription and watches, says so, and lists its other live tasks ([compaction.md](reference/compaction.md)). A resumed or
relaunched coordinator follows its catch-up there before any other work.

## Before every tool call

Is there a verb for this? Run it. Does it have a body? Spawn the lane with its task
and ledger row. Is it a wait? Fold it into the lane that acts. Is it status? The
communicator sends the update desk's render; the coordinator sends nothing. Is it
an owner message? The communicator spawns one subagent for it, which answers on
the surface it came from; the coordinator applies the ruling everywhere, and a
ruling on how the drive works lands in this skill that turn.
Is it an alert the supervisor has not answered? `lr incident run <slug>` now.
Did a tool refuse, or did something go wrong twice? It goes to the meta lane this
turn, where the fix is a Go verb, and the work proceeds. Am I about to approve,
hold, start or cancel something? The owner's order carries it or a lane owns it.
Finished agents stopped, production operations logged, nothing called done that is
not live and checked.

A call that survives the list decides something no verb and no lane can decide.
Everything else is a lane.
