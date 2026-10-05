---
name: long-running
description: Hard rules for orchestrating multi-lane work without burning the orchestrator's context - routine ground truth arrives as a lane's verdict, anything with a body is a lane, every wait folds into the lane that acts, no lane parks and no lane is re-briefed, state lives in cc-notes and the task list, an open-PR ledger records every PR and owner ask, lanes self-enqueue their stacks against a root-owned holds list, a landing-desk reconciles landings, desk-runner.py owns worker traffic and ready-prefix landing, each owner-named number-one outcome gets a priority desk, and a lane bus carries decisions, heads, contracts, blockers, and asks from each lane's cursor. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs at once, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
---

# Long-running orchestration

Delegating every unit of execution does not protect the orchestrator's window. Every
tool result and every inbound lane message lands in it anyway. What protects it is
refusing to look: the orchestrator reads verdicts and holds decisions. D3 gives the root
one direct check for priority PRs. A drive that delegated all of its work and still ran
out of context spent it on logs it read itself, JSON it parsed itself, duplicate
notifications it answered, and status it restated per event.

## When this applies

- Three or more lanes in flight at once.
- A drive expected to outlast one context window.
- Any wait on an external system: CI, a merge queue, a cloud apply, a soak.
- Any bring-up, migration, sweep, or audit whose units are many and independent.

A single-lane investigation is not this. One question goes to one subagent in direct mode.

The standing subagents are `landing-desk`; `alerts-desk` when the drive touches
production; and one priority desk per owner-named #1-priority outcome while that
outcome is open. Once the drive has posted to Slack, the Slack watch lane is standing
too, briefed from `reference/slack-watch-brief.md`. The orca desk is the `desk-runner.py run --desk orca` process,
not a subagent. Its landing process shares the store and runs D3, D14, and D16
where the checkout carries `stack-enqueue`.
The root spawns a subagent for every task with a body, including its own routine work.

This skill is the context-discipline layer over `~/.claude/CLAUDE.md`. Fan-out shape comes
from §Parallelize Independent Work, lane behavior from §Delegation, per-lane model and
effort from §Model Routing, and depth of checking from §Verification Budget. None of
that is repeated here.

## The twenty-two hard rules

**R1. Take ground truth from the owning lane.** Once a lane can answer a question, never grep a log, list cloud resources, curl an API, open a build page, or parse JSON in the root context. Ask the owning lane with a scoped resume and take back at most five lines. The root checks priority PRs itself under D3.

*Prevents reading CloudWatch and Buildkite output while an assigned triage lane already held the answer.*

"Verify ground truth yourself, never trust silence" and this rule do not conflict.
Outside D3, verifying means asking a lane for the one number, and checking liveness
means asking for a 3-line status. Neither means reading the source. Silence is not progress.
Ask, and set a deadline for the answer.

**R2. Delegate anything with a body.** Any read past one file, any log, any multi-step investigation, any PR or merge mechanics, any bulk enumeration belongs to a lane. The root holds decisions and the task list, and checks and labels priority PRs under D3.

*Prevents repeated small reads and merge calls filling the root's window.*

The root never reads a PR, diff, CI log, Slack thread, plan artifact, or long file to
answer a question. `gh pr view` and `ccx vcs diff` become a sonnet or opus reader lane,
or `cc-context:pr-review-triage`; a CI log becomes `cc-context:ci-triage`; a Slack
thread becomes a reader lane. The lane returns the conclusion and pointers: file:line,
PR number, Slack ts. A lane report longer than a screen goes back to its lane through a
scoped resume, to be rewritten under 25 lines with a disk pointer. The root stays at
rulings, dispatch, and the handoff record.

*Prevents the root filling its window with PR bodies and diffs it read to answer one
question: "stop polluting your main context by reading PRs etc" (release-v3, 2026-09-30).*

The pack's `root_context` hook enforces R1-R2 in a drive's root, and lanes and subagents
pass it. It blocks:
- A Read whose window passes 150 lines.
- A Read of a lane artifact: anything under `audits/`, `briefs/`, `handoffs/`,
  `tool-results/`, `subagents/`, or `transcripts/`, a `matrix.md`, or a `.jsonl` transcript.
- A search with `rg` or `ag`, and `cat`, `head`, `tail`, `sed`, `awk`, `grep`, or `jq`
  over a file outside an `inbox/` directory.
- A Read, or `cat`, `head`, `tail`, `sed`, `awk`, or `grep` except `grep -c`, over
  `inbox/*.md`, even with a bounded window; use
  `inbox-digest.py --state <drive>/inbox/.inbox-digest.json <files>`.
- A history or PR read: `git log`, `show`, `diff`, `blame`, or `grep`; `gh pr view`,
  `gh pr diff`, `gh run view`, or `gh issue view`; `ccx code`, `repo`, or `web`;
  `ccx vcs diff`, `show`, `history`, or `reviews`.
- The Grep tool and the ccx MCP read tools.
- A Slack thread, history, or search read, through the Slack MCP or `cc-slack thread`.
- A Claude Docs, Datadog notebook, Linear document, or Capacities fetch, and any MCP
  tool after one of its responses passed 8000 characters.

The plan and its progress folder pass, as do filters over piped output
such as `ccx vcs status | grep`, heredoc appends, `ls`, `date`, `ccn`, `ccx vcs status`,
`ccx vcs pr status`, `cc-present`, `Agent`, `SendMessage`, the task tools, and `Monitor`.
One bounded read of one file outside `inbox/` passes too, including `head -n`/`tail -n`
up to 40 lines, `head -c` up to 4000 bytes, `grep -c`, `grep -m` up to 20, `wc`, or a read piped into such a
`head` or `tail`. Any other file read, search, history read, or Slack read is shown to a
small model first, and blocks only when the model is confident it is an investigation
rather than a one-shot control-plane read.
A `# ccx:raw` comment on a Bash command, or `CAPT_HOOK_CCX_RAW=1`, runs it as written. Set
`LONG_RUNNING_ROOT_READ_LINES`, `LONG_RUNNING_ROOT_MCP_CHARS`, or
`LONG_RUNNING_ROOT_ARTIFACT_DIRS`, a comma list, in the session's environment to change
the thresholds.

*Prevents the root of 2026-10-01 reading audits, briefs, and PR state inline: "you again
spammed your main context with stuff you didn't need to."*

**R3. One lane per wait→do chain, and no lane ever parks.** "When X lands, do Y" is one
sequencer lane that polls X, does Y, and sends one message. Never a per-step Bash
watcher, never a Monitor per build, never a root-context poll. A lane that ends its turn
waiting is a bug; lanes poll in foreground loops to a terminal state and report once.
*Prevents both the per-landing watcher tax and the lane that parked at a confirm gate
while its silence read as progress.*

A lane never backgrounds a subagent or codex run and ends its turn. Run subagents and
codex in the foreground (blocking), or poll the reply file in a foreground loop to a
terminal state.

*Prevents the `00:48Z` loss of the "Use ccx for" ask for `AGENTS.md` in monorepo audit
note `ab649fa1`. The lane backgrounded a codex subagent and ended its turn;
completion went to the root session and the lane never woke.*

**R4. Never re-brief a lane, never answer a duplicate.** `SendMessage` to a finished
agent resumes it carrying its whole original brief and it re-runs that brief.
`SendMessage` to a running agent spawns a second copy that restarts the lane while the
original never sees the message. Reply to nothing you have already acted on, and expect
2-3 duplicate idle-notifications per real report. Rotation flushes the lane in place;
it keeps working and resumes from its ledger and cursor after its own compaction
(see Lane rotation). *Prevents lane collisions in a shared worktree
and the reply tax on notifications that carry no news.*

**R5. Write it down, do not hold it.** Findings go to cc-notes from the lane that found
them, never into the orchestrator's window. Lanes carry no `mcp__*` tools, so they
write with `ccn log append`, `ccn investigation open` and `append`, `ccn note add`,
`ccn doc add`, and `ccn task add`. The root may use the `mcp__plugin_cc-notes_*` tools.

Drive records live in cc-notes on the drive checkout. Pass record ids to lanes:

- Incidents use an investigation and log through `incident.py`.
- Handoffs use a doc; follow
  [reference/handoff-subagent-brief.md](reference/handoff-subagent-brief.md).
- Reports and audits use a note for a verdict or finding, or a doc with a
  read-before trigger.
- Lane briefs are attachments on one `briefs: <slug>` log; use `--replace` on edit.
  A tool that needs a file reads `ccn attachment path <id> <name>`.

Never run `mkdir` or `cat >` to create a markdown record under `~/.claude/scratch`.
Desk inboxes (`inbox/*.md`) stay files because readers use `inboxes.py`'s stream:
sorted `<file>.archive/YYYY-MM-DD.md` files, oldest first, then the live file.
Byte offsets and line counts survive rotation; `standing.py` reads the same stream,
and `desk-runner.py` appends escalations. The
orca-waiter/Monitor streams, orca-launch receipts, and desk state stay files for
their stream and state readers. The executor's actions JSON stays a file because
it needs `flock` and generation compare-and-swap, which cc-notes lacks. Lanes never run
`ccn sync`; the root syncs.

The action record tracks work routed through `desk-runner.py`; never mirror it
into `TaskCreate`/`TaskUpdate`. The root does not complete shadow tasks
for runner actions. Its task list holds owner asks and Agent lanes; the task rules
below apply to those. Lanes' `started`/`done` replies, not the root, move runner
actions. A send proves delivery only.

Keep the task list current with every lane and ruling. The root creates a task with
`TaskCreate` in the same turn it spawns a lane through `Agent`, `orca-launch.sh`, or
`orca worker-start`; set `owner` to the lane's name. The root creates a task in the same
turn every owner ask arrives, including a queued message delivered mid-turn.

Only the root completes a task, and only after consuming the deliverable: route the PR
to the desk/ledger, record the ruling, relay the answer to the owner. A lane saying
done, READY, GREEN, or landed completes nothing. Lanes report through `SendMessage`
and leave the root's task open; a hook denies a lane's `TaskUpdate` to `completed` on a
lane-owned task.

A named lane's final text reaches the root as an idle notification on every stop.
Ending on `SendMessage` with no trailing text omits that text. During a drive,
`lane_reports` refuses lane final text over 300 characters. Read the `SendMessage`
report, not the idle notification.

At every handoff, milestone report, and compaction, the root reconciles the list:
every `in_progress` task has a working lane, and every running lane has an open task.
Complete what was consumed; re-own or delete the rest. Every lane receives the live
task list on every wake.

In a live drive, `task_archive` moves completed tasks whose files are older than two
hours from `~/.claude/tasks/<list>/` to its `.archive.ndjson` on
`SessionStart`, `PreCompact`, and at most once per 30 minutes on `Stop`. This keeps
Claude Code's built-in reminder small. It reprints the live list every 10 assistant
messages without `TaskCreate` or `TaskUpdate`, to the root and each in-process teammate.
The owner's switch is `CLAUDE_CODE_TODO_REMINDER_MODE=off` in the `env` block of
`~/.claude/settings.json`; it disables that reminder for the whole Claude Code process.

Report to the user on milestones or when they must act, never per event. Once
`long-running` is invoked, the compaction hook nudges the root to write a new progress
doc, then handles superseding, the plan pointer, and `/compact`, as Compaction handoff
describes. The plan stays the drive's mandate and decisions. *Prevents the forced
mid-drive handoff with nothing written down to hand over.*

The pack's `task_list` hooks enforce this. They nudge when a spawned lane has no task by
turn end, when a lane's done report names a task still `in_progress`, and when an owner
ask has no task after three tool calls or by turn end. The owner-ask nudge is dropped
when a `TaskCreate` happened in the last 10 minutes. Every 20 turns they check for drift
and busy lanes with no open task.

All lane nags share a limit of at most one per lane per hour. A lane that has reported
done twice is never nagged again. Done reports share one line listing the lanes, and
the drift line names the lanes. Treat each nudge as a `TaskCreate` or `TaskUpdate` due now.

*Prevents lanes running with no task, completed lanes left `in_progress` for hours,
mid-turn owner asks with no task, and lanes completing tasks the root still owed
follow-ups on.*

**R6. Put every owner request in flight the turn it arrives.** Start a new lane or an
explicitly named parallel sub-lane; never append the request behind a busy lane's queue.
Split a lane holding more than two unstarted items into parallel lanes. Where their
files overlap, stack them with each lane branching off the previous lane's branch;
each brief names the files that lane owns.

Before every milestone report, check the task list for owner asks still unstarted,
dispatch them before the report goes out, and name them in it. Prefer more concurrent
lanes with one deliverable each over fewer lanes carrying queues. Speed is what the
owner measures, and a lane's queue is wall-clock the owner pays for. R1-R5 still hold;
a new lane costs the root one brief and one report, never a read. *Prevents ten owner
asks sitting unstarted for hours behind one lane's four open PRs, found only when the
owner asked.*

**R7. Check a PR's state before reporting it.** Never state that a PR is merged, queued,
or blocked from a message or from memory. Before reporting it, run `ccx vcs status` in
the stack's worktree, or check for the squash commit `(#N)` on a freshly fetched
trunk. This applies to the root and the desk.

*Prevents the root telling the owner "#25121 queued to merge" when it had already
been on dev for 15 minutes.*

**R8. Record every owner ask in the desk ledger.** The root runs
`ledger.py ask --ledger <id> --text "<verbatim>" --lane <lane> --accept "<acceptance check>"`
in the turn the ask arrives, before or with the lane dispatch. Each new ask gets its
own lane; never append it to a busy lane's brief, under R6. A PR carrying an
`IN-PR` ask takes no second ask: `report --ask` refuses it. The new ask goes on a
stacked follow-up PR.

Every lane records each sub-dispatch as an ask row before dispatch with
`ledger.py ask --ledger <id> --text "<sub-task>" --lane <lane> --accept "<the reply it must return>"`.
When the reply lands, the lane runs `ledger.py answer`. An orphaned sub-dispatch
surfaces as `LOST` in the summary.

Before claiming anything is assigned, in flight, or done, the root reads
`ledger.py summary` or `ledger.py show --asks`, never its own plan table. R7 still
applies to PR state; the root never reports it from the plan file. An ask is done only
at `LIVE`. Once every linked PR has landed, the root records the shipping pipeline's
next run with `ledger.py live --ledger <id> --at <ISO>`. A delivered ask then moves
from `LANDED-NOT-LIVE` to `LIVE`. Before `LIVE`, the root says
"in #N, not live yet: `<blocker>`", never that the ask is done. The root dispatches
each `LOST` ask in that turn. It runs `ledger.py drop` when the owner withdraws an ask
and `ledger.py answer` when the ask is a question rather than shipped work.

*Prevents "one post + one approval per release" slipping for hours in the busy
release-fast lane's brief, invisible to every summary, while the root tracked PR
state in plan tables and 148 of the drive's 405 PRs had no ledger row.*

**R9. Orca worker traffic runs through `desk-runner.py`.** The root issues `relay`
and `launch` commands and watches its inbox files through one Monitor on
`inbox-watch.py --state <drive>/inbox/.inbox-watch.json [--match <extra regex>] [--heartbeat <lane>=<file>:<seconds>] --session <root session id> <inbox files...>`
at timeout 1800000, re-armed on every exit and after compaction. Its byte-offset
cursor loses and replays nothing on re-arm; `ESCALATION`, `INCIDENT`, `URGENT`,
`DECIDE`, `ALERT`, and `ASK root` always match, and pure Python leaves no grep
stage for a reaper to kill. After five minutes without a root turn since an urgent
line arrived, it pushes to the owner's DM and names any open question holding
delivery.

After compaction or a re-arm gap, catch up with
`inbox-digest.py --state <drive>/inbox/.inbox-digest.json <files>`; never tail, sed,
or grep whole inbox files. The digest keeps the newest appended lines within a
6144-byte budget, clips each to 200 characters, counts omissions, and advances its
state beside `.inbox-watch.json`. It starts at the live file's beginning on first
use, skips archives, and reports unread archived bytes. A new lane orients with
`inbox-digest.py --all <files>`, which reads archives and live files with the same
caps and touches no state.

Use `--match '.*'` for all inbox traffic or a regex for extra lines. The root never
runs the check/ack loop, relaunch sweeps, or helper scripts inline. No model desk
sits between the root and Orca.

Start the orca runner before the first worker; keep its escalations
under the drive's `inbox/`. Use `policy` for a landing rule and `show` for action
state. [The runner brief](reference/orca-desk-brief.md) gives the config and cutover.

*Prevents G130's `AmiBake` launch being lost behind a desk handoff, R620 being sent
to a superseded desk, and GO carrying no start deadline (2026-10-01 audit, Brief 3
and ranked fix 3).*

*Prevents release-v3's eight Monitor deaths between 2:44 and 5:04 PM Pacific on
2026-10-03: Claude Code's shell `grep` function ran embedded ugrep, which the
machine reaper killed after 15 minutes. The 2:50 PM watch died at 3:06 PM; the
merge walker's 3:13 PM DECIDE/ESCALATION about a network delete cutting a live
customer box fell in the gap. The 3:33 PM `tail -n 0` re-arm skipped it, leaving
the root to find it at 4:10 PM, 57 minutes late. Open `AskUserQuestion` calls at
3:07-3:32 PM and 4:11-4:31 PM held every Monitor event and teammate message;
"Run assignment starved" sent at 4:26 PM reached the root at 4:32 PM.*

**R9a. An urgent hold gets a decision owner and a clock.** A lane holding a release
the plan marks urgent, or a blocked catch-up of a coupled control-plane set,
appends `orca-desk: hold <slug> owner=<lane> :: <what is held, and on what>` to
the orca desk inbox. It names itself or a dedicated decision-owner lane as owner.
A held or blocked coupled release gets its own decision-owner lane. When the
release moves, the holding lane appends `orca-desk: unhold <slug>`.

After `deadlines.hold_minutes` (default 15), the runner writes one decision:

```text
DECIDE hold:<slug> <owner>: held <n> min: <what> | the root decides it now with <owner>, ahead of any open owner question on another subject
```

The root resolves it with that lane before or instead of an open
`AskUserQuestion` on another subject. The unrelated owner question goes to a
non-blocking board or waits. With no root turn for five minutes after the line
arrives, `inbox-watch.py` pushes it to the owner's DM under R9.

*Prevents the 2026-10-03 api/runtime-v2/restate-worker catch-up staying held while
executor shipped alone at 3:51 PM Pacific and the starvation mechanism arrived.
Demeter questions occupied 3:07-3:32 PM and 4:11-4:31 PM. cc-notes answer `5548bb2`,
option 3; retro `849e294`.*

**R10. Landed is the only progress.** Status to the owner is landed, queued, or the
exact blocker: the PR, its head, and the gate it waits on. "Open" and "in CI" are not
status. PRs sitting more than 24 hours on merge conflicts are combined into trains
under D7 and D8 and land as one batch. A red gets a structural fix with a test, never
a retry; a timing red gets one rebuild, then a report.

*Prevents the owner hearing "open" on 09-29 while stacks of real work sat on merge
conflicts for more than a day: "just saying something is open is not acceptable".*

**R11. Hands off deploys.** The root and its lanes never start, approve, override,
watch, or re-cut a release or deploy unless the owner asked for it in that turn. An
option the root described earlier is not permission, and no script re-cuts on its
own. No cut goes out while a known fix for the last failure is still open: land the
fix first.
R16's pre-authorized active-alert fix apply is exempt from this rule.

*Prevents cuts 377, 378, 388, 390, 396, and 413 each failing on the next open bug on
09-29, and the root override-approving 413 unasked: "no one asked you to do anything
with the deploy just focus on your work".*

**R12. Route every red at once.** Every `STALLED-RED` line goes to its owning lane in
the same turn with a 15-minute deadline, and a red with no live owner gets a new lane
at once; see Stalled-red sweep. Idle lanes never see CI.

*Prevents seven PRs sitting red for 30 to 100 minutes on 09-29 while their owners
sat idle.*

**R13. Dispatch every owed item in the same turn, and read priority PRs directly.**
When several owed items have no PR, the root dispatches all of them in one turn,
one lane each. Never dispatch one per turn or wait behind a desk. The root reads
priority PR state itself in one batched `ccx vcs pr status <n1> <n2> ...` call.

*Prevents the owed Phase 0 items sitting as pushed branches with no PR while the
root waited on desk relays during release-v3 on 2026-09-30.*

**R14. A process fix is a PR the turn it is adopted.** Every tooling or process
change the root makes mid-drive goes to a lane that PRs it into the skill or tool
in that same turn. That lane drives it through merge, release, and installation.
This includes a new desk, a new landing model, and a new rule relayed to lanes.
A change that lives only in scratch inbox files or a desk's brief is lost at the
next drive.

Tool improvements are automatic. When a lane or the root hits a tooling defect, a
refusal, a `# ccx:raw` fallback, or a repeated chore that could be a verb, it
spawns a tooling lane in the same turn to fix or build the tool, and keeps going.
That lane carries the fix through PR, merge, release, and install. Nobody waits for
the owner to ask. A lane that cannot spawn records a `ccn papercut` and names the
defect in its report, and the root dispatches the tooling lane on reading it. The
capt-hook `tooling_nudge` hook flags these signals; the rule holds without it.

*Prevents the release-v3 R137 and R138 rulings of 2026-09-30 living only in the
drive's inbox files until this skill change.* *Also prevents the owner asking three
times on 2026-10-01 for tool fixes the root had worked around: the ccx submodule
refusal, Orca message delivery, and the ledger gap.*

**R15. CI is the verifier, never the box.** A lane never runs a whole-package build or
suite locally: no `buck2 build`, `cargo build`, `yarn tsc:*`, `bun test`, jest, or
`go test ./...`. It pushes and reads CI. The only local runs are the one failing test
that reproduces a red CI step, or a single artifact the brief names. Those run one at a
time with parallelism capped at `-j 8` or the tool's equivalent. Machine load governs only those local runs: before
starting one, a lane reads the 1-minute load, and above the core count it posts one
`HELD on load <load>/<cores>: <command>` status line to the Run mailbox
(`orca orchestration send --type status`) and waits at most 5 minutes before running it
anyway. Load never holds a Buildkite build, a hand apply of a saved plan, an incident
step, or an owner-directed step; those run at once.

*Prevents release-v3 lanes pushing the box to load 103 with local rust builds on
2026-09-30, until the 12:35Z mass kill, and apply-restate-1255 holding an
owner-ordered Buildkite apply on load 428 until the root answered (2026-10-02).*

**R16. An active production alert gets its executor in the same turn.** An active
alert or outage on any surface counts: a Datadog monitor, a Sentry alert, an
`#outage` report, an on-call page, a broken CI or review pipeline, or an alert or
breakage link from the owner. It is P0 from the moment it is seen. That turn,
before anything else, the root runs `incident.py open` and starts `incident.py run`
as a background command (`reference/active-alert-brief.md`). From then on the
executor owns the incident, through the final reply:

- It posts the fence and launches the fix and evidence lanes through
  `scripts/orca-launch.sh` as Orca codex workers on `gpt-6.1-sol`, fast tier, `xhigh`.
  With no mechanism after 15 minutes, it adds an Opus 5.5 backup lane
  (`claude-opus-5-5`) and keeps the first running. It refuses Claude for the fix and
  evidence roles and never uses fable or astra.
- It posts each comms event (ack, PR, review request, landing, live, final reply)
  to the comms lane on the bus with that event's cc-slack grant id. It confirms each
  posted ts from the lane's bus answer.
- It holds a landed fix at `activation_pending` until the live configuration reads
  back as the landed tree. It runs a canary, rebuilds each failed head of the outage
  window once, and accounts for every rebuild before it reports `recovered`.
- It records every side effect in the `scripts/actions.py` store before running it,
  so a restart or a duplicate delivery never repeats one. A lost response stays
  `unverifiable` until a read of external state settles it.

Alert intake runs before the root wakes. `monitor-watch.py --alert-inbox` and the
Slack watch lane append `orca-desk: alert <slug> <link> :: <what fired>` to the
orca desk inbox. The runner fills `reference/alert-fix-brief.md` with drive facts
from `alert.facts`, attaches it to the briefs log as `<slug>-fix.full.md`, and
launches `<slug>-fix` on sol xhigh at once; a repeat relays to the live lane. The root ratifies from `INCIDENT` in that turn:

1. Adopt the launched fix lane into `incident.py` with `--adopt fix=<lane>`.
2. Start or adopt the evidence lane. Beside it, spawn an incident-doc lane as
   `long-running:lane` on opus, named `incident-<id>-retro`.
3. Fence the target from further deploys and applies outside the fix lane.
4. Start comms in the affected account channels first, before the mechanism is
   known, under cc-notes answer `5ad4507`. A platform-wide incident also opens
   in `#outage` under `52f4863`.

The incident-doc lane runs the incident-retro skill's live mode in the
Forge-AI/design-docs checkout: `live init`, then `live sync` at every state change
and at least every 10 minutes. The shell PR merges first; the page at
`https://docs.poetic.design/incident-retros/<date>-<slug>/` polls
`live/<date>-<slug>`. The lane hands its link to comms for `#outage` and the
account channels. At resolution, it runs `live finalize` and prepares the draft
retro on `retro/<date>-<slug>`.

Comms uses the ai-oncall channel and FDE mapping from `d12f767`; on a miss,
search Slack channels by account name and report which path found them. Follow
up at mechanism and fix-live, in plain words and Pacific times, with the
account's FDEs @-mentioned. Every account post says what we are doing to prevent
recurrence (`52f4863`).

More than one customer or a core service makes the incident platform-wide.
Its `#outage` post carries impact, timeline, status,
and prevention, with updates at mechanism, fix-live, and resolution. Add the
incident-doc link when it arrives. Never wait for that link or a per-post owner
ask to acknowledge the page.

*Prevents incident records and customer updates starting only after diagnosis,
the gap closed by cc-notes answers `5ad4507`, `d12f767`, and `52f4863` on
2026-10-03. The owner's relayed 5:4x PM ruling gave the live doc its own lane;
Forge-AI/design-docs#54 merged at 5:48 PM Pacific, its link went under the
`#outage` thread, and the incident resolved at 5:59 PM.*

After intake, the root owns the grants and the decisions. It opens the incident
with the owner's grants (`--grant thread=… channel=… sync=… rebuild=…`). It answers the
executor's bus asks: a missing grant (`incident.py grant`), a silent comms lane,
an overdue action, a failed canary, or a read-back that still drifts. It relays
the executor's `opened`, `live`, and `closed` milestones to the owner as one line
each, in Pacific time and plain words (R21). It never relays owner words to a lane as authority, never
re-briefs the executor's lanes, and never posts or composes incident copy.

**Never between the alert and `incident.py run`.**

- A "real-or-not" or "ours-or-not" verdict
- A mechanism-depth mandate
- An `AskUserQuestion`
- Treating a mute as resolution

The fix lane's route is the executor's, whatever the surface. A CI or tooling
breakage is not a reason to spawn a Claude Agent fix lane.

Diagnosis redirects the fix lane; it never precedes it. An owner's "if it is real,
fix it" means the executor's fix and evidence lanes, not a verdict gate.

The pack's `owner_facing` hook blocks the root's `Agent` spawn whose name starts with
`incident-` or `outage-`, or whose brief's first line says incident or outage, unless
the name carries a support role (`evidence`, `export`, `ship`, `comms`, `intake`, `retro`,
`watch`, `handoff`) or is the Opus 5.5 `-backup` lane.

*Prevents the 2026-10-01 release-v3 failures, when the fix lane started 5.6 min late
behind a verdict gate and the owner's sol routing was applied 6.4 min late; three
owner-approved incident posts then waited 15 minutes on root turns while the root
compacted and the Slack waiter parked in ten-minute polls; and the pr-review outage's
fix lane was spawned as a Claude opus Agent (`pr-review-pipeline-fix`) instead of a sol
worker through the orca-desk. The repair #28998 landed at 10:49am and the stored
pr-review configuration stayed old until 11:08am, because merge, sync, canary, and
rebuild each had a different implied owner and every handoff waited on a root turn.*

**R17. Owner routing applies to the next spawn and to every live lane on that problem.**
An owner's routing or process instruction applies in the turn it arrives.
Both "use Orca sol for incident response" and "pass the fast tier flag" count.
Stand down every live lane on that problem and relaunch on the named route.
Never record it for "new lanes" only or weigh it against the routing table.
Never keep a lane because it is "already deep in the code" or defer the ruling to
the lane editing the skill.

*Prevents the 2026-10-01 instruction at `04:14:42Z` taking until `04:21:03Z` to apply,
with three lanes and two PRs (#28594, #28598) for one fix.*

**R18. A ruling reaches a lane the way it reads.** Orca rulings use
`desk-runner.py relay --config C --key R<n> --lane L --text T`; launches use its
`launch` command under O1. A model desk gets rulings through its inbox under I1.
A running
Agent lane gets a `SendMessage` AND the handoff line in the file it hands off
through. The root confirms the handoff on disk before treating the lane as stood
down; a running copy never sees the `SendMessage`.

*Prevents R365 at `04:16:03Z` launching nothing until R370, and sandsql-handoff-fix
opening #28594 29 s after its stand-down (2026-10-01).*

**R19. A settled question is applied, never asked.**
Before an owner question reaches `AskUserQuestion` or a cc-present board (`cc-present start --doc`, `push`, `update-block`), the root checks each question against the authorities.
First read the owner's plan and every plan it names as source of truth, including each plan's Decisions section and the section the question touches. Then check `ccn answer list --label scope:durable` plus the drive's answer labels. Last check the project's feedback memories.
A question any of them answers is applied: the root writes the ruling to the lanes (R18) and logs it. The question never reaches the owner. "Confirm or override" cards for items settled by the plan count as asking.

A lane's question list is input, not output. The root never forwards a lane's owner-question file verbatim; it filters every item first. Choose board or `AskUserQuestion` only after the filter.

An absolute owner instruction ("nothing is dropped", "every side-feature carries") stays absolute in every brief, ruling, and memory the root writes.
Never add "unless the owner explicitly drops it" or "or an explicit owner drop": an exit turns the ruling into a decision surface, and audit lanes manufacture questions from it.

An audit or parity lane's brief says the same: grade a row the owner's rulings cover against those rulings (carry, or the plan mechanism that carries it); never queue it as an owner question.
A prior root or PR decision that contradicts an owner ruling is a defect to revert, not a question.
Where a landed PR or a refining plan line conflicts with the owner's plan, the owner's plan wins and the conflict becomes a revert lane.

Only a question nothing settles goes to the owner. Each card names which authorities were checked.

The pack's `settled_questions` hook matches each question in an asking call (`AskUserQuestion`, `cc-present start --doc`, `push`, `update-block`) against the tracked plan's Decisions lines, the `scope:durable` answers, and the feedback memories while a drive is active.
It blocks only on a match and quotes the matching plan line, answer id, or memory; drop those questions and re-issue the rest. A wrong match passes when re-issued, since asking calls then pass for ten minutes.

An owner message that states a standing rule, with "from now on", "always", "never", "I
told you", or "the plan is", is recorded the turn it arrives, as an `answer_add` with
`scope:durable` and a line in the plan's Decisions section. When a turn ends without an `answer_add` or
`answer_edit`, the `root_context` hook queues `owner standing rule not recorded:
answer_add it (scope:durable) + a plan Decisions line` for the next turn.
An `AskUserQuestion` answer, a picked `Send` preview of a Slack lane's draft included,
is recorded by the cc-notes pack itself with `from:owner` and `source:askuserquestion`;
the root never records one by hand.

*Prevents the release-v3 parity board of 2026-10-01 (05:51Z), which asked the owner seven keep-or-drop questions (ack gate, finish, on-call swap, dev-check card, divider rows, Start button, start refusals) and a DAG question the plan's Decisions and §TM-dag already answered, because the root encoded "nothing is dropped" with an "or an explicit owner drop" exit and forwarded the audit lane's question list unfiltered: "the board showed those cards because you asked those questions in the first place instead of following the plan."*

**R20. A Slack link from the owner is an assignment, and every Slack write is a lane.**
A Slack permalink the owner pastes, bare or with words, is the root's to own, whoever
wrote the message. The owner's in-thread "Looking", "on it", or "checking" means they
handed it to the drive, never "the owner has it". It is never informational.

That turn, before anything except R16's `incident.py open` and `run`:

- (a) Spawn `long-running:lane-ship` on sonnet from `reference/slack-lane-brief.md`.
  It adds `eyes` within one minute on the message that asks, through the cc-slack CLI,
  reads the thread, and returns the ask in ≤5 lines.
- (b) Spawn the doing lane (fix, investigation, or answer) from the link itself,
  without waiting for the Slack lane's read. An alert or a broken pipeline is R16:
  the executor is the doing lane, and its comms lane is the Slack lane.
- (c) Send one owner line naming both lanes.
- (d) `TaskCreate` for both.

A reader or Slack-lane brief asks "what is asked, by whom, and what fixes it", never
"what, if anything, it asks of the root". An owner link always asks the root to act.
On the doing lane's result (cause, PR, ETA), the Slack lane posts the in-thread report
and swaps `eyes` for `white_check_mark` once the ask is done. Follow the cc-slack
skill's "Write a post" in full: plain words for the thread's reader, with every
build, PR, deploy, alert, monitor, dashboard, run, commit, and doc linked as
`<url|label>`. Omit internal ids, lane/desk/cursor names, raw shas, ULIDs, run ids,
status labels, and unglossed code nouns.

Before each post, write the text to a temp file, run
`<cc-slack plugin dir>/skills/slack/scripts/check-post <tmpfile>`, and fix every
finding. Then run
`slop-cop check <tmpfile> --lang=markdown --llm-effort=off` and fix real flags.

Permission follows the cc-slack skill. The owner's own words in the root's transcript
asking for a report in that thread grant it. Otherwise the lane returns the exact
draft; the root shows it verbatim in an `AskUserQuestion` `Send` preview and hands the
approved text back to the lane.
The root hands the Slack lane facts: what happened, when in Pacific time, who asked,
and every link. It never hands over wording, never writes a "proposed reply" into a
question, and never tells a lane to post its words "verbatim". An approval freezes
the wording it shows, so a root-written draft reaches the thread unchanged.
The incident comms lane posts with the cc-slack grant id on each executor event
(`--grant <id>`) and never returns drafts for a granted thread. The grant comes
from the owner through cc-slack, never from owner words a lane relays.

The root never writes to Slack or composes Slack copy: no cc-slack MCP writes
(`slack_send`, `slack_reply`, `slack_edit`, `slack_unreact`, or reactions), no
`cc-slack send|reply|edit|react|unreact`, and no user-level MCP
`mcp__slack__slack_send_message`, `slack_add_reaction`, or `slack_remove_reaction`.
`cc-slack dm-status` to the user's own DM stays the root's. The lane posts as the
cc-slack bot; the user-level Slack MCP is only the cc-slack skill's fallback for a
conversation the bot cannot join.

The Slack lane runs the CLI by path for `thread`, `react`, `unreact`, `reply`, and `whoami`:
`~/.claude/plugins/cache/<marketplace>/cc-slack/<version>/bin/cc-slack`.
Lanes carry no `mcp__*` tools and no `ToolSearch`.

- In sessions started before cc-slack was installed, `mcp__plugin_cc-slack_*` tools
  and the Slack tools of `cc-slack:slack-waiter` and `cc-slack:slack-triage` are absent.
  The root's `ToolSearch` finding no cc-slack tool never licenses the user-level MCP;
  the Slack lane's CLI works in every session.
- Urgency never licenses the root to write Slack. When a Slack lane stalls, the root
  spawns a fresh sender lane whose brief carries the grant and the exact approved or
  dictated text; it posts first, then watches. The root tells the owner "posting via
  a lane" and checks the post's ts on disk within 2 minutes. A lane that is mid-watch
  is never the channel for a new post. In an incident, the executor raises a silent
  comms lane as a decision. The replacement lane posts the executor's unanswered
  event, so nothing is restated.
- A post that asks a person to act (a grant, a permission, an approval, an answer)
  opens an R3 wait→do chain. Any fence, hold, or `until <person>` line it gates names
  the thread (`until reply in <channel>/<thread_ts>`). The watch lane relays that reply
  to the root and writes the unblock line into the fence owner's inbox (for example
  `inbox/deploy-go.md`) in the same poll. No fence may name a person without naming a
  watched thread.
- An answer the owner dictated goes out as one message with the grant in the dispatch,
  never staged across several instructions.
- The owner's surface word is literal: "channel" means a top-level channel post,
  "thread" a thread reply, "DM" a direct message.
- A post about a PR re-reads the PR's state (approvals, CI, landed) immediately before
  posting; the writer lane does that read itself, never the root from memory.
- An incident's comms lane takes its authority from the grant id on each executor
  event and posts without a root turn; the root is told, not asked
  (`reference/active-alert-brief.md`).

The pack's `root_context` hook blocks every Slack write in a drive's root with
`delegate to a lane: long-running:lane-ship (model: sonnet) briefed from
reference/slack-lane-brief.md — ... (R20: the drive root never writes to Slack)`;
`# ccx:raw` does not bypass it; lanes pass.

The pack's `slack_threads` hook appends every thread a drive session posts in to
`<state dir>/slack/watched-threads.jsonl`, whether the post went through the cc-slack
CLI, its MCP tools, or the user-level `mcp__slack__slack_send_message`, and whether or
not it passed `--no-watch`. That file is the watch lane's thread list; handoffs and
spawn prompts never carry one (`reference/slack-watch-brief.md`).

*Prevents the release-v3 failure of 2026-10-01: the owner pasted Anubhav Jain's
"PR reviewer broken" thread at `17:25:46Z` after replying "Looking". The root briefed
a reader to say "what, if anything, it asks of the release-v3 root", took back "nothing
directly … yasyf is investigating", and left the ack and report undone until the owner
wrote "i said im looking at PR reviewer bc i sent it to you". The root then reacted and
replied itself through the user-level Slack MCP (`17:29:25Z`, `17:29:59Z`), with UTC
times, bare `#28934` and build numbers, a pending-PR ETA, and no cc-slack skill loaded,
because the session predated the install and its permission gate skipped on transcript
size: "you shouldn't be doing that slack response inline, you should be delegating
response and triage to a lane". The same day the root posted inline a second time at
10:44am Pacific (through the user-level MCP, before the hook shipped), saying #28998
still needed approval 3 minutes after Anubhav had approved it; it staged the dictated
answer across three instructions to a waiter parked in a 590-second watch, so Anubhav
waited about 10 minutes; and it posted a thread reply where the owner said 'send it in
the channel'.*

**R21. Talk to the owner in the owner's terms, and own the root's delays.**

- Every time shown to the owner — chat replies, boards, Slack, and the owner-facing
  lines of handoffs and progress docs — is Pacific with no zone label (`10:35am`,
  `Oct 1 at 9:52am`). Inbox, runbook, and ledger stamps may stay UTC; any owner-facing
  summary of them converts.
- An owner "why did you…" gets a direct answer from the root in that same turn: the
  cause in the root's own words, then the lane that fixes it. Delegating the answer to
  a lane is not answering.
- A delay the root's own dispatching caused is reported as the root's. Never attribute
  it to the owner ("mixed signals", "conflicting instructions") when the owner said it
  once.
- Every owner-facing line, `AskUserQuestion`, and board card names the thing, never
  its codename: "the release-pipeline cutover stack", not "D"; "the deploy that runs
  after each merge", not "the walker"; "the freeze on api/plat", not "G304". Inbox,
  ruling, answer, and `ctx_` ids belong in the ledger and the briefs, never in a
  question to the owner. Before sending, reread each line as someone who has not read
  the drive's inbox, and rename every noun they would not recognize.

The pack's `owner_facing` hook reads the root's final reply at `Stop` in a drive and
queues `owner-facing times are Pacific with no zone label (R21): restate <times> from
your last reply in Pacific` when it carries a UTC clock time (`17:35Z`, `17:3xZ`,
`16:52 UTC`). Its `plain_words_in_owner_questions` hook blocks a drive root's
`AskUserQuestion` that carries a codename, an inbox or answer id, or a proposed Slack
reply.

*Prevents release-v3, 2026-10-01: the root reported every time to the owner in UTC
(`17:35Z`) although the owner had asked for Pacific, because the rule lived only as a
Slack-copy rule; it delegated the owner's 'why did you…' to a lane instead of
answering; and it described a 10-minute delay its own staged dispatches caused as
'mixed signals': 'no one gave you mixed signals about responding to him, don't lie.'*
*Also prevents release-v3, 2026-10-02 at 1:38pm: the root wrote its own reply to
Andrew into an `AskUserQuestion` ("Heads-up: D landed today …"), offering "Post as
written" for the comms lane, and the owner answered "he has no idea wtf D is, you are
violating your no jargon rules and keep it simple rules, debug why that is". That day 30 of the root's 33
questions to the owner carried drive vocabulary (31 bare "D", 5 inbox ids), and the
plain-language check covered only the posting lane's copy.*

**R22. Quote the owner's design verbatim. Ship that design or hold.**

- In every brief or ruling on a subsystem, quote the relevant register rules verbatim.
  Cite their linked answer ids without pasting full answers. Name the required
  entry point as a symbol at `file:line`. Have a reader lane find it before dispatch.
  A package list is not an entry point:
  importing every package still permits a second implementation.
- Before READY-FOR-SHIP, name the entry point the diff calls, each ruling it meets,
  and anything it leaves out. The root confirms this design check against the
  rulings before any ship lane launches. Incident fix lanes record it with
  `incident.py note --design-check`; the executor asks the root to confirm.
- Never ship a "static first" split that changes the durable fix's agreed semantics,
  such as dropping footers, the judge, or the entry point. Ship the design whole or
  hold the PR; mitigate through the apply. If a lane says the full fix exceeds its
  role, re-route it to a lane that can build it. Never narrow the design to fit the lane.

*Prevents the C09G failure of 2026-10-01: R693 named packages instead of
`releaseDAG`; R699 deferred footers and baseline; R704 sent READY-FOR-SHIP straight
to a ship lane. #29109 added a second closure without footers, and the owner caught
the mismatch in the diff.*

## The landing desk and its ledger

On a drive where many lanes open PRs, the root is the wrong place for their reports.
Each report is a message in the root's window, and each landing is a wait. The desk is
one long-lived lane, `landing-desk`, that takes those reports and reconciles landings.

Lanes enqueue their own stacks under D1. Where the checkout carries `stack-enqueue`,
`desk-runner.py run --desk landing` owns D3, D14, and D16, including for shards.
The landing desk never enqueues beside it. Without that script, the desk keeps
the `ledger.py label` path. The root owns the holds file and priority PRs under D3.
It receives P0 lines immediately and a summary every 30 minutes.

`scripts/ledger.py` is its one tool. It uses `ccx vcs pr state` for refreshes and
`ccx vcs pr watch` for transitions. Both read ccx's machine-wide pull request cache,
which polls each repository at most once every 30 seconds. Its one store is a cc-notes
ledger with a row per PR our lanes shipped. The holds, the routing, the label history,
and the landing are fields on that row. Lane messages are `msg/<seq>` rows and owner
asks are `ask/<seq>` rows beside the PR rows. Rules reviews are `review/<pr>@<head>`
rows that hold landing only for unwaived findings on the PR's current head. cc-notes
finds the ledger through the working directory's repository, so a lane outside that
checkout passes `ledger.py -C <checkout> <verb>`.

Every script in `scripts/` is on PATH by name through the plugin's `bin/`, which Claude
Code adds for the installed version, so briefs call `ledger.py`, `bus.py`, or
`orca-launch.sh` and never a path. `orca-launch.sh` puts the same `bin/` on PATH for a
sol worker's codex terminal.

The desk grades, lands, and tracks only through `ledger.py`, never scripts of its own;
a gap in `ledger.py` is a `RULING NEEDED`, not a workaround.
`reference/landing-desk-brief.md` is the desk's brief, ready to paste;
`reference/desk-contracts.md` holds the message shapes. It is R5 applied to PR state and
R3 applied to the watching.

Spawn it first, before any lane that will open a PR, whenever three or more lanes
will ship through one merge queue or the drive will outlive one context window. Below
that the lane that opened the PR lands it, or the root lists it for the label watch
under Mechanics, and there is no desk.

**D1. Lanes enqueue their own green bottom prefixes; the root owns holds and priority PRs.** The moment its stack has a green, approved bottom prefix, the owning lane re-reads the root's holds file. Before every enqueue it writes the held PR numbers to a fresh file, digits only, with `grep -o '#[0-9]\+' <holds file> | tr -d '#' > <held file>`; never cache the held set. Extend it with the open PRs of held lanes under D3.

Where the checkout carries an enqueue script, a lane calls it as
`stack-enqueue <prefix top> --hold $(cat <held file>)`, then reports with
`ledger.py report`. Drop `--hold` when the numeric file is empty: it requires at
least one PR number, never a filename. Argparse exit 2 otherwise reads as
`unsettled`.

A ready stack is enqueued as one batch from any of its PRs: `stack-enqueue` lands the largest green bottom prefix in one call. A lane never enqueues the first PR alone and the rest later, and never through the `merge` label or `gt merge` on one PR of a stack. A partial enqueue needs `--partial`. On a stalled queue, where a head's draft closed unmerged, run `stack-enqueue --check <head>`, then `--recover`.

The landing runner runs D3 for tracked stacks; the landing desk
never adds a competing enqueue. The desk records outside enqueues on refresh as
`in the queue, labelled outside the desk`. `ledger.py label` cannot pass `--hold`
yet. Where the repo has no enqueue script, run `ledger.py label --repo <repo> --ledger <id> --pr <prefix top> --expect-head <sha> --checkout <its worktree>`; the holds the desk has mirrored into the ledger are the guard.

The lane keeps `ccx vcs pr watch` on the stack. On ejection or conflict it rebases and re-enqueues at once. It is not finished until its squash `(#N)` is on the base branch, and never ends a turn with a green, approved, unheld bottom prefix unenqueued. After the prefix lands, the lane restacks the PRs above it with `ccx vcs stack submit`. No ruling is needed.

Only the root appends or edits the holds file. Each line names held PRs as `#<n>` and whole held lanes as `lane:<name>`, then the reason. Every `#<n>` in the file is held, so a reason names another PR without the `#`. A lane never self-enqueues a prefix containing or sitting above a held PR, including any PR of a held lane. It reports `held` on its tip, names the held PR, and leaves that PR and those above it to the root to release. The desk mirrors each entry as a `ledger.py hold` with that reason so `label` refuses it, and lifts it when the root removes the line.

Lanes record each report themselves with `ledger.py report`: PR, full head sha, verdict, and one line of text. The desk reads them with `ledger.py inbox --take` every iteration, and its watch prints a `REPORT` line the moment one lands. The root receives `P0` and `RULING NEEDED` lines immediately and the 30-minute summary. If the owner flags a PR as priority, or it blocks a release or a user, the root checks its gates and enqueues it itself in the same turn under D3. That approval covers only the head the owner named; if the PR gains commits or scope, the root gets fresh approval naming the new head. Never relay an ETA for a green priority PR.

*Prevents green approved stacks waiting on one serial desk, which prompted the owner's 2026-09-30 ruling to enqueue more than one thing at once. Also prevents a tip enqueueing held parents. Tip #28102 sat above held #28081 and #28082 on 2026-09-30, kept out of the queue only by red CI.*

**D2. Track and grade our lanes' PRs.** The pack's PR hook, `ledger.py report`, `ledger.py register`, and an explicit `refresh --pr` are the only paths that open a PR row. Owner ask rows open only through `ledger.py ask`. The desk grades every tracked PR without waiting for a lane report at its current head. The desk never lists the repository's pull requests; a PR it cannot trace to one of our lanes stays outside the ledger and its counts.

*Prevents routing comments and rebase orders landing on other engineers' PRs, which one repo-wide sweep did twenty times in an hour.*

**D3. Enqueue every ready bottom prefix in parallel.** `<tip>` is the top of the largest contiguous bottom prefix whose PRs pass. `ledger.py label --pr <tip> --expect-head <tip-sha> --checkout <path>` walks base refs to the repo's default branch and re-reads every PR in that prefix. Each must be open, with approval in force, successful commit status, no failed checks, and a completed, successful latest `ai-review`. Approval on any commit counts; a dismissed or withdrawn approval does not. Each head must have no desk hold or lane `held` verdict on that head, no prior label or pull, and no conflict with its base.

Allowed `mergeable_state` values are `clean`, `behind`, and `has_hooks`; a PR above the bottom of a stack may also read `unstable` while Graphite's `mergeability_check` is its only unfinished check. `--expect-head` takes a lowercase hex prefix of the tip's sha, 7 to 40 characters long. An untracked downstack PR or an orphaned base refuses the whole attempt.

An open child outside the prefix is allowed only when a lane tracks its ledger row and its head carries Graphite's `Graphite / mergeability_check` check run. Restack any other child through Graphite or retarget it to the trunk before adding the label. `--expect-head` pins the tip you graded; each call re-reads its tip immediately before grading it. A report is not required; the forge decides whether a head is red or conflicting.

Where the checkout carries `.agents/skills/submit-pr/scripts/stack-enqueue`,
`desk-runner.py run --desk landing` runs D3. Each pass runs `ledger.py refresh`
and `reconcile`, reads rows, and gates every tracked open stack tip in parallel:
`stack-enqueue <tip> --check [--whole] [--hold <n>...]`. The gate selects the largest
ready bottom prefix. The runner accepts an enqueue for its exact prefix heads
and runs independent enqueues in parallel. It permits another attempt for the
same heads only when earlier attempts are proven to have enqueued nothing.

Every enqueue re-reads the root's holds file, including every open ledger row
whose lane is named as `lane:<name>`. The command is
`stack-enqueue <prefix top> --hold $(cat <held file>)`; omit `--hold` for an empty
numeric file. A `held` refusal waits for the root and is never routed as a red.
The runner verifies an enqueue only when `reconcile` records every prefix row
as `landed` by squash on the base. The landing desk never enqueues beside it.
Never hold a ready prefix behind another stack's landing under D19.
The runner enqueues a ready stack as one batch, never its first PR alone, and never through `merge` or `gt merge` on one PR; a stalled queue head gets `stack-enqueue --check <head>`, then `--recover`.

The accepted policy is a record seeded from config as `prefix` at #28601's
revision. The root changes it with `desk-runner.py policy --config C --key L<n>
--landing prefix|whole --revision REV --source TEXT --supersedes <current revision>`.
A command naming another predecessor is rejected with `STALE-POLICY`; a
stale-checkout ruling such as L260 cannot replace the accepted rule that way.

Where the repo has no enqueue script, the landing desk uses
`ledger.py label --pr <tip> --expect-head <tip-sha> --checkout <path>`. One label
on the prefix top enqueues the prefix as a batch. `label --all-clean` grades
stacks sequentially and is the fallback sweep only for these repos. Re-read
holds before each call and mirror PR holds and held lanes' open PRs under D1.

Lanes retain D1. For priority PRs, the root reads their gates in one batched
`ccx vcs pr status <n1> <n2> ...` call and enqueues in the same turn. Priority
approval covers only the named head under D1. The desk records outside enqueues
on refresh as `in the queue, labelled outside the desk`.

*Prevents a green tip enqueueing a red parent, an ejected head entering the queue
again unchanged, and a green priority PR waiting for the owner to queue it by hand.*

**D4. Landed means the squash is on the base branch.** `ledger.py reconcile` fetches the
trunk (the repo's default branch) once, lands every row a squash subject ending `(#n)`
names, and settles each closed row left by tree equality. The queue deletes a stacked PR's base when
the stack lands, so the tool never fetches that base. It never settles a row by
the PR's `merged` field, which a squash-merging queue leaves false on every PR it lands.
A closed row with neither match becomes `closed-without-squash`, a name that cannot be read
as success, because a child auto-closed by its base's deletion looks exactly like a
landing until the trunk is checked. *Prevents lanes waiting hours on a PR that landed
minutes after they started, and a lost stacked child counted as merged.*

**D5. Route every red or conflicting row in the pass that finds it, once per head.**
`refresh` then `route`, never `refresh` now and `route` when there is time. `route`
records the head, job, and lane it sent, so the same head and job are never routed twice
and a moved head is routed again. Nothing is written to the pull request: a lane is
addressed where it listens, and a comment on a PR reaches whoever happens to read it.
Route to a lane only while it is live. A finished lane's name resumes its whole brief
under R4 and collides with the lane now holding the worktree, so record it with
`ledger.py gone` and route its reds under D8.
*Prevents the red PR that sat for hours because the pass that found it only recorded
it, and the routing comment on someone else's PR. Also prevents a red routed to a
finished infra lane on #25188, which pushed from the worktree a fresh rebase lane held.*

**D6. Every hold has a reason and an expiry, and every message is recorded once.**
`ledger.py hold` takes both; a row parked without them is an untracked row wearing a
ledger's clothes, and the summary does not count it as held. `enqueue` drops a second
message with the same kind, PR, and head, so a duplicate idle notice is neither stored
twice nor answered. *Prevents the hold nobody can lift and the reply tax on notifications
carrying no news.*

**D7. The hot set lands by train.** A `merge-train` lane, spawned with the desk,
owns every reported PR whose files hit the hot set. Every two hours it fetches
trunk, orders the ready cars oldest first, rebases them into one stack of at
most six with `ccx vcs stack rebase --linearize`, resolves each conflict once in
the conflict workspace, and enqueues the whole train once every car is green; a
red car is fixed forward or ejected with `--parent`, never waited on. A hot-set row is routed to
the train, not to its lane. *Prevents the shelf rot where fourteen of fifteen
conflicting PRs never reached the queue.*

**D8. Route ejections, conflicts, and reds in the pass that sees them.** The owning
lane handles its own ejection from its watch. The desk routes an ejection or conflict
to the lane at once when the watch or a pass shows it. A row whose lane is gone goes
to the train (hot set) or the standing `red-desk` in that same pass. A step red on
trunk's latest build is held as `dev-red:<step>` with a six-hour expiry instead of
routed. `ledger.py hold` refuses a PR that has green children unless `--stack` holds
them all.

*Prevents the 38 of 80 open PRs untouched for twelve
hours and the foreign reds lanes diagnose as their own.*

**D9. A stale plan is information, never a refusal.** A pull request's plan is computed
at its head. When the base moves under a stack that plan reached, the desk prints the
stacks and the movers in the grade and labels anyway. The landing plans the tree it
actually applies and refuses its own op classes there, so the gate that matters sits
where the tree is real. Any class the desk would refuse on a plan belongs in the
landing's admission rule, not in a pre-merge staleness check. A rebase is owed for a
merge conflict or after a bottom prefix lands under D16.

*Prevents the bounce where a green PR is refused
because an unrelated stack moved and then spends half an hour in a rebase and a CI
re-run that change nothing about what the landing does.*

**D10. Record reports immediately; reconcile every three minutes.** Where
`stack-enqueue` exists, the landing runner runs D3 every 180 seconds by
default, without waiting for a report. The landing desk types reports, runs
refresh/watch/P0 and red routing, mirrors holds, and keeps stale, summary,
shards, and train duties. It never enqueues beside the runner. Where the repo
has no script, a `clean` report still triggers `ledger.py label` on its largest
ready, unheld, unqueued bottom prefix in the same turn.

Each desk pass reads all PR numbers in one `ccx vcs pr status <n1> <n2> ...` call
and the Buildkite build list, never one REST call per PR. Stagger desks and shards
by a minute at `:00`, `:01`, and `:02`. One refused prefix never stops another;
the D3 executor routes gate blockers under D14. A lane's `red` or `conflicting`
verdict does not refuse a head the forge passes.

*Prevents clean PRs waiting for a 20-minute pass that labels one report at a time,
until the owner enqueues one in Graphite by hand.*

**D11. Name every clean row older than 30 minutes with its blocker.** `ledger.py stale`
lists every open row whose latest report is `clean` and at least 30 minutes old, with
one blocker each. The blocker is held, in the queue, routed, label refused, head moved
since the report, or never graded. The summary carries the same lines and the median
minutes from a row's last report to its landing over the window.

Clear each stale row's blocker in the pass that sees it with a label, route, hold,
lift, or `RULING NEEDED`. *Prevents a clean row aging silently while the counts line
reads healthy.*

**D12. Shard at 15 lanes or 25 active rows, whichever comes first.** Split early.
Spawn parallel sub-lanes named
`landing-desk-<shard>`, each owning a named set of lanes' rows in the same ledger with
`--shard lane-a,lane-b`. A stack's rows belong to the shard of its tip's lane. Each
shard runs `refresh`, `landed`, `route`, and `stale` on its own rows every three
minutes. It runs D3's `ledger.py label` path only where no `stack-enqueue` exists;
the landing runner owns those enqueues otherwise.

Stagger desks and shards by a minute at `:00`, `:01`, and `:02`.
Read all PR numbers in the pass with one `ccx vcs pr status` call and the Buildkite
build list. The refresh lock is keyed on the ledger, so shards never race a sync.

Lanes keep reporting to `landing-desk`; the main desk types every message in, uses
D10's report path, and alone sends the root the summary. *Prevents one desk's pass
growing with the board until its pass takes 20 minutes.*

**D13. Register each lane's stack when it starts and whenever it opens a PR.** The root registers the drive with `drive.py start --ledger <id>`. The pack's PR hook then registers every PR a drive session or Orca worker opens under the lane's name. It records the head when the command prints one. This covers in-process subagents and teammates at any depth; `orca-launch.sh` passes the drive to Orca workers, resolving it from the Orca run when the desk runner, which holds no session, launches them. A session no drive claims prints one stderr line from `drive.py record`.

Lanes still register a unique branch prefix ending in `/` at spawn with `ledger.py register --ledger <id> --lane <name> --branch-prefix <prefix>` and still `report` verdicts. Hand-register a PR only when the hook's context line says it was not recorded, using the command it gives. Read recorded PRs with `ledger.py list --ledger <id> [--lane <name>] [--open] [--json]`.

Each refresh makes one `ccx vcs pr state --repo <repo> <PR numbers> --lane-prefix <prefix>` call with every row's PR number and any explicit `--pr` numbers, repeating `--lane-prefix` for each registered prefix. The same read returns the lanes' open PRs from ccx's machine-wide pull request cache. Every discovered PR enters the same batch and takes the same gates as a reported PR; the desk never lists the repository's pull requests.

*Prevents three PRs a lane never reported sitting unmerged for hours.*

**D14. Grade a moved or unreported head like any other.** Every tracked current
head takes D3's gates without a report. Where `stack-enqueue` exists, the landing
runner routes each `BLOCKED` head except `held` once per head and blocker. It
re-runs the gate immediately before creating the route and suppresses a blocker
that already cleared.

Orca delivery can follow later; this is not a recheck at
that later send. Orca lanes receive a relay; other lanes receive
`bus.py post --kind blocker`. The landing desk never repeats these per-head gate
messages and retains `ledger.py route` for red CI and conflicts.

Without `stack-enqueue`, the desk sends `new head <sha9>: <blocker>` once per head
and blocker. A head that moved since refresh is graded on the next pass without
a route; red CI and conflicts go through `route` without a duplicate batch message.

*Prevents two heads that moved after their reports being ignored for hours.*

**D15. Name what the desk is waiting on and ping the lane in the same pass.** The summary's `waiting:` line groups tracked open PRs as `ungraded`, `refused`, `red`, and `held`. An `ungraded` row lacks a label and a grade at its current head; a `refused` row has a label refusal at that head, with the reason in `stale` or `show`. A `red` row has a CI failure or a `dirty` or `blocked` mergeable state; `held` covers desk holds and lane `held` verdicts on the current head.

In the same pass, run `route` and send its messages. D3's executor handles enqueues; the landing desk runs that step only without `stack-enqueue`. `summary` requires `--repo` and `--checkout` and settles landings first, so a landed row never appears as pending.

*Prevents the desk waiting silently while five ready PRs sat unmerged for hours.*

**D16. The green bottom of a stack lands now.** Enqueue the largest contiguous bottom prefix whose PRs are green, approved, and unheld as one Graphite batch. Never wait for the top of a stack to go green before landing a green bottom. PRs above the prefix wait on their CI, review, or hold. After the prefix lands, route a restack of the first PR above it to its owning lane; the lane restacks the remaining PRs with `ccx vcs stack submit`.

Where `stack-enqueue` exists, the landing runner sends this route after verifying ledger `landed` rows, by relay for Orca lanes or `bus.py post --kind blocker` for others. The landing desk never duplicates it. Without that script, the desk keeps the route; for Orca lanes use `desk-runner.py relay --config C --key R<n> --lane L --text T`.

*Prevents the wait for unfinished PRs above a green bottom that the owner ruled out on 2026-10-01, and made the repo rule in the monorepo's #28601: AGENTS.md "Stacked diffs" lands the largest green bottom prefix with `stack-enqueue <any PR of the stack>` and restacks everything above it right after. Restacking children and rerunning their CI after the prefix lands is an accepted cost.*

**D17. An owner-visible change needs its render approved before the label.** A PR that changes something the owner sees rendered, such as a UI, message, or generated document, holds under D6. Use the reason "render not approved" until the owner has approved that exact render at the head being labelled, unless the owner has granted ship-then-fix for that lane. A re-render at a new head needs a fresh approval; the old one covered a different head.

*Prevents a redesigned modal or a rewritten Slack card landing and reaching users before the owner had seen how it actually rendered.*

**D18. A queued PR is watched until its squash is on the base; an ejection is a P0 the pass it happens.**
The desk arms `ledger.py watch` under Monitor when it spawns and re-arms it on every
expiry. Forward every `P0` line to the root the moment it prints; the root treats it
like a `RULING NEEDED` line and acts in the same turn. The 3-minute refresh is the
reconciliation pass; the watch detects transitions. Stagger desks and shards by a
minute at `:00`, `:01`, and `:02`. Each pass reads every PR number in one batched
`ccx vcs pr status` call and the Buildkite build list.

Every lane that enqueues or reports a PR runs
`ccx vcs pr watch --lane-prefix <prefix> --until landed` under
Monitor, re-armed on expiry, or in a foreground loop instead of ad-hoc polling.
`ejected` or `conflicting` means rebase now. The lane watches until its PRs land.

*Prevents the #27949 incident in Forge-AI/monorepo on 2026-09-30: priority #1 was
green, approved, and queued at 05:29Z, then ejected on a merge conflict at 05:34Z
after a nine-PR stack landed on `dev`. The desk's pass was minutes away and its summary
half an hour away; the lane had stopped watching, and the owner found it first.*

**D19. Land as many independent stacks at once as possible; shape them so they do not conflict.**
Every green, approved, unheld stack enqueues in the pass it reaches that state. The
desk never holds a ready stack for another stack, never waits for another stack's
squash, and never reads an ejection as a reason to slow down. It routes a `conflicting`
or ejected PR to its lane in the same pass, and the lane re-enqueues the moment its
restack is green.

Stack shaping prevents conflicts; serial landing does not. The lane brief states the
shape. A shared-file edit goes in the smallest additive first PR of its stack, in the
file's declared order. A lane whose change touches a file another open PR touches
stacks on that PR instead of racing it. A generated output is never committed, since
the build generates it, which is a repo rule. A stack that still carries a regenerated
artifact rebases and regenerates it on ejection, never resolving the file by hand.

*Prevents the serial landing the owner revoked on 2026-10-01: "the fix is to have as
many independent PR stacks merging as possible, and shaping them carefully to avoid
conflicts".*

`refresh` regrades the rows the ledger holds and merges the forge's fields into them, so
the fields the desk writes are never overwritten: `lane`, `declared_intent`, the holds,
the routing, the label history, and the landing. A refresh that cannot reach the forge
exits non-zero and writes nothing. The records live on `refs/cc-notes/*` and survive
compaction, a session restart, and a handoff. After compaction, the same `landing-desk`
reads its inbox, holds, routes, label history, and landings from the ledger and resumes
from its cursor. None of that goes into session memory or the plan file.
See Lane rotation.

<a id="the-orca-desk"></a>

## The orca desk runner

`desk-runner.py` replaces the model-operated orca-desk. Start two detached
processes with one config and store: `run --desk orca` and `run --desk landing`.
Restarting either is idempotent. The orca process alone consumes the Run mailbox;
let an existing desk finish its pass and end its loop before starting it. Keep
the old session open. The landing process runs D3, D14, and D16 where the
checkout carries `stack-enqueue`.

The orca runner rotates every `*.md` file beside its escalations file once an hour.
`inbox-rotate.py` records time and stream-end marks and archives through the newest
mark at least six hours old into `<file>.archive/YYYY-MM-DD.md`, so the first
archival happens about six hours after the first mark.

The root issues `relay`, `launch`, and `policy` commands with the drive's R/L
keys. A repeated key in the same lane is one action. A relay can also be one
`R<n> orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>` line appended to
`inbox/orca-desk.md`; the runner delivers it once per lane and logs `RELAYED` or
`RELAY-FAILED`. A launch can be one
`R<n> orca-desk: launch <lane> [NOW] <model> <effort> brief=<absolute path>` line in
the same file; the runner runs `launch` for it once, `NOW` meaning `--owner-directed`,
and logs `LAUNCHED` or `LAUNCH-FAILED`. The root never `SendMessage`s a desk. It
includes the escalations file in one Monitor on
`inbox-watch.py --state <drive>/inbox/.inbox-watch.json --match '.*' --session <root session id> <inbox files...>`
at timeout 1800000, re-armed on every exit. Add `--heartbeat <lane>=<file>:<seconds>`
for each watch lane. R9 defines cursor, urgent-line, and owner-DM behavior.
`show` and the config's `view` file render state; these inbox files are views,
never authority.

Lane results reach the root as `FIX-LIVE`,
`MECHANISM`, or `OUTCOME` escalation lines, or by Run message id. Never reconstruct
the latest lane state with `grep ... | tail` across several files: file order can
hide an earlier file's newest lines. Never use `orca orchestration inbox | grep -A`
for this: the listing puts newer messages above the match.

[reference/orca-desk-brief.md](reference/orca-desk-brief.md) owns the config,
commands, passes, acknowledgement contract, escalation kinds, and cutover.
[reference/orca-workers.md](reference/orca-workers.md) describes the adapters.

*Prevents G130's `AmiBake` launch being lost behind a desk handoff, R620 going to a
superseded desk, and GO carrying no start deadline (2026-10-01 audit, Brief 3 and
ranked fix 3).*

**O1. Launch through the runner.** The root submits
`desk-runner.py launch --config C --key R<n> --lane L --model M --effort E --brief PATH`.
The runner starts `orca-launch.sh` detached. No model desk runs lifecycle helpers.

**O2. Settle a launch from its line or receipt.** A `ready` or `unsupervised` line
counts as launched, as does a new dispatch receipt when the log is empty. Other
output produces `LAUNCH-FAILED`; neither a line nor a new receipt produces
`UNVERIFIABLE`, never an automatic relaunch. `unsupervised` escalates separately.

**O3. Keep one Run reader.** Only desk-runner consumes the Run. It reads
`orca orchestration inbox --terminal run:<run> --limit N --json` without marking
messages read, processes new sequences oldest first, and persists each cursor
advance. It expands full pages until the cursor is covered. Its first read starts
at the newest sequence without replaying history, and nothing launches or
relays until that read succeeds. No other loop may run
`orca orchestration check --run <run>` with `--wait` or `--ack`.

**O4. Answer from the brief, escalate the rest.** A Sonnet-low `claude -p` judge
with no tools reads the lane's brief. It answers what the brief settles or emits
`DECIDE` with the question and options. Missing briefs and judge failures can
escalate without options. The root answers with `relay --reply-to <msg id>`.

**O5. Relay to the current dispatch.** Submit
`desk-runner.py relay --config C --key R<n> --lane L --text T [--reply-to <msg id>]`.
The runner checks the current receipt and dispatch. A guidance relay sends the action's
thread id and requests `started <key>` before acting and `done <key>: <result>`
after. Its send proves delivery only; the lane's status replies move the action.
Question-reply actions currently complete on send without those worker acks.

**O6. Never end a session.** The runner never stops, signals, releases, or closes
Claude, Codex, Orca, terminals, PTY daemons, or their supervisors. A settled
worker keeps its session open. No launch receipt or missing heartbeat changes this.
The sweep names each newly settled dispatch once in a `RECLAIM` line with the
config's `orca.gc` command, and the root runs that command under R195.

*Prevents the 12:35Z kill that ended every session of a drive (release v3, 2026-09-30).*

**O7. Keep acceptance, start, result, and verification distinct.** Actions move
through `accepted`, `started`, `completed`, and `verified`, with `failed` and
`unverifiable` recording failure or missing proof. An `unverifiable` send is
settled from Orca `request-show`/`--retry-request` or the recipient's mailbox;
without proof it escalates. Never resend blindly. An unparseable CLI response
currently enters failed-send retries; that response shape lacks this guarantee.
`show` renders the record.

**O8. A stale heartbeat never authorizes a relaunch.** Every five minutes the
runner sweeps receipt dispatches. A non-live active dispatch emits `LIVENESS`;
resume its session in place. A requested relaunch offers the lane container to
the new dispatch; its first ack takes ownership at the next generation. The old
owner retains authority until that ack. After transfer, another dispatch's ack
gets a stand-down reply and cannot move the original action.

**O9. Never re-brief.** Update the attachment with
`ccn log append <briefs log> --entry "<what changed>" --attach <lane>.full.md --replace`,
then relay its pointer from `ccn attachment path <briefs log> <lane>.full.md`.
Never start another lane to carry a follow-up.

**O10. Never answer stale questions to a replacement.** Keep the original
question id as the reply address. The current judge path lacks a stale-sender
fence; this rule must not be reported as an enforced runner guarantee.

**O11. Treat a capacity fallback like an ask.** Workers whose `ask` returns
`capacity reached` send `question` or `escalation`. Both use the same judge path.

**O12. Pause ten seconds between Run reads.** The orca runner sleeps ten seconds
between passes. The landing process uses its configured 180-second interval.
Model desks retain the 60-second inbox-wait rule.

**O13. A prompt is a desk bug.** The five-minute sweep reads `observation.agentWait`
and emits `PROMPT` in the pass that sees it. Stale unread mail gets one terminal
wake per message. Completed or failed dispatch mail emits `STALE-MAIL`; no sweep
relaunches a worker or guesses an answer to its prompt.

*Prevents lanes sitting for hours on a prompt only their own terminal showed
(release v3, 2026-09-30).*

**O14. Hold launches while load exceeds the core count, for a bounded time.** The
runner reads the 1-minute load before each launch. A sol launch or one submitted with
`--owner-directed` starts whatever the load. Above the core count any other launch
stays accepted, `show` marks it `HELD` with the load and its age, and after
`deadlines.load_hold_minutes` (default 5) it fails with a `LAUNCH-HELD` escalation and
a Run mailbox message. It never kills a worker to lower load.

*Prevents the load of 103 behind the 12:35Z mass kill (release-v3, 2026-09-30).*

**O15. Preserve the codex and sol launch routes.** `codex` and `gpt-*` use
`orca-launch.sh` on Orca's codex agent. Incident launches name `--model sol
--effort xhigh`: `gpt-6.1-sol`, a `--no-parent` worktree, and an explicit terminal
command with `--dangerously-bypass-approvals-and-sandbox` and
`-c service_tier=fast`. Keep Orca's runtime defaults unchanged. An unsupervised
launch needs a reporting route, never another launch. Inline lanes still use
`Skill(codex)` or `codex:codex-wrapper`; one-off questions use `codex-ask`.

## The alerts desk

Production monitor traffic belongs to one long-lived `long-running:lane`, `alerts-desk`,
model sonnet, effort low. Spawn it beside the landing-desk whenever the drive deploys,
applies, releases, or migrates. `scripts/monitor-watch.py` owns the polling and the
dedup; `reference/alerts-desk-brief.md` is the desk's brief, ready to paste.
`reference/active-alert-brief.md` holds the executor's runbook and its lane briefs.

**A1. Report monitor transitions, and nothing else.** The desk keeps one Monitor on
`monitor-watch.py watch --alert-inbox <drive>/inbox/orca-desk.md` over the drive's
monitors, by tag glob such as `release-target:*` and by named id. It messages the root only on a move into Alert,
Warn, or No Data, or a recovery to OK: monitor id, name, transition time, and a
one-line first read. Never on an unchanged state or a timer tick. It owns no fixes
and posts nothing to Slack.

Each alert is P0 for the root. Open its incident and start the executor that turn
under R16, with no verdict gate. The monitor's
targets stay fenced from deploys, applies, and enqueues until it recovers or diagnosis
clears the alert; the fence never blocks the fix lane's own apply under R16.

**A2. A run-binding watch proves a new run starts and finishes.** After an apply
or release of executor, api, runtime-v2, or restate-worker, the watch or bake
records a newly bound run, its start, and its completion. Crash and storage
health alone cannot pass the watch.

*Prevents the 2026-10-03 executor watch passing after 30 minutes while run starts
fell to 1/min. Retro `849e294`; monorepo #29861 adds a run-starts line to
`deploy-watch.sh`.*

## The priority desk

`reference/priority-desk-brief.md` is the brief, ready to paste.

**P1. Give each owner-named #1 outcome its own desk.** While the outcome is open,
the root spawns `<outcome>-desk` as `long-running:lane`, model opus, with its own
append-only inbox file. Every other desk forwards traffic from those lanes to
that inbox and stops handling them.

*Prevents the shared desk queue that release-v3 ruling R138 split on 2026-09-30.*

**P2. Open with the owner's owed list and the start state.** List one numbered item per
PR or proof in the owner's definition of done. State what is on the base branch,
queued, open as a PR, and pushed with no PR. Name the lanes that owe each item.

*Prevents the owed Phase 0 items sitting as pushed branches with no PR during
release-v3 on 2026-09-30.*

**P3. Drive every owned lane in the same iteration.** Act on all owed items in
parallel, one dispatch per lane per iteration. Lanes self-enqueue under D1.
The priority desk enqueues each stack's largest green, approved, unheld bottom prefix
as soon as it sees it, using D3's parallel calls. It routes ejections, conflicts,
and reds to the owning lane at once.

**P4. Dispatch unclaimed work at the PR deadline.** For pushed heads with no PR after
10 minutes, check the owning lane's active work before dispatching `<lane>-submit`,
sonnet xhigh, in its own worktree. It does PR mechanics only and submits those exact
heads as one linear stack. For an unbuilt owed item with no PR after 15 minutes,
dispatch a separate implementation lane only when no live lane is working on it.
Keep the original session; never duplicate its active work.

**P5. Report the owed list every 15 minutes and idle when it is done.** Send the
root each item as landed, queued, PR + blocker, or no PR + the lane launched for it.
Include `cursor R<n>`. When every item is landed or proven, report once and keep the
session open and idle.

## Desk inboxes

These I-rules apply to the landing desk, priority desks, and shards. The owner ruled:

> take all ephemeral stuff out of manual files and ccn, and move it into cci (cc-inbox)

Durable owner rulings, decisions, runbooks, and design docs stay in cc-notes.
The orca desk keeps its launch, relay, and hold lines in `inbox/orca-desk.md` in
the `R<n>` form because the owner's CLAUDE.md Incident Turn names that file and format.
The root never `SendMessage`s a desk.

**I1. A desk's inbox is the set of cci records addressed to it.** The root posts each ruling
with `cci post --drive <drive> --lane root --kind go --to <desk> --text "<ruling>"`.
Keep text under 400 characters; put a longer body in a file and attach it with
`--path <file>`. Link durable records with `--ccn <id>`.

**I2. Read from the saved cursor at the top of every iteration.** Before any other
work, act on the records printed by `desk-wait.sh` or the cci Monitor. Both readers
advance the cci cursor named for the desk. Then read with
`cci tail --drive <drive> --cursor <desk> --to <desk>` and act on each new record
and any delivered message. Repeat a capped read with the same cursor and filters.
Every report names the last sequence read as `cursor #<seq>`.

**I3. Read the inbox before reporting a wait on the root.** Check for the answer
before saying an item is waiting on the root.

**I4. Never `SendMessage` a running desk.** When its reported cursor stays behind
the root's last addressed record for more than one iteration, the root posts one
`cci post --drive <drive> --lane root --kind go --to <desk> --text "Read unread range #<first>-#<last>."`
and records the stall in its progress record. The desk reads that record at the top
of its next iteration (I2). Resume only a desk that
has already reported and ended its loop, through Scoped resume, in place with the
same identity.

*Prevents the 26 rulings left unread in a looping desk on 2026-09-30.*

**I5. Keep about 15 lanes per desk.** Split early into a second desk or a priority
desk.

**I6. A standing rule has its own durable answer, and it is never done.** An owner rule
that holds until replaced ("from now on", "for the remainder", "every landing")
gets its own `scope:durable` cc-notes answer first, then one
`cci post --drive <drive> --lane root --kind go --to <desk> --ccn <answer id> --topic standing --text "<rule>"`.
It never shares a record or an answer id with a one-off. "Deploy everything now"
and "deploy every landing from now on" are two records. No record marks the standing rule done, complete, or closed.

Supersede its answer with a later answer, then post
`cci post --drive <drive> --lane root --kind correction --to <desk> --re <seq> --ccn <later answer id> --topic standing --text "<replacement rule>"`.
Here `<seq>` names the standing record being superseded. A desk brief and each
summary list live standing rules by answer id, plus the plan's Decisions, never
as a sequence range.

A standing rule handed to a lane as a deliverable still gets its own durable
answer and addressed cci record first. The lane's task cites that answer id, and
completing the task never retires the rule.

*Prevents the release-v3 "release everything as it merges" rule being lost three
times on 2026-09-30 and 10-01: R312 shared a line with the one-off R311, the
landing-desk-2 brief declared only "L65–L107 standing" and dropped L40, and R348
marked "R312 done" once deploy-experience's `tools/deploy --since` task built
toward it.*

**I7. In-process teammate desks wait in the foreground.** Every Agent-spawned
desk is in-process. Every in-process desk and lane names its own team mailbox,
`~/.claude/teams/<team>/inboxes/<lane name>.json`, as a source. Run one foreground
Bash call with `timeout: 60000`:
`desk-wait.sh 50 cci:<drive>:<desk> <team mailbox>=<cursor file> [<other file>=<cursor file>...]`.

It waits at most 50 seconds and returns on new addressed records, a new line in
another watched file, or `MAILBOX <n> unread`, where `n` counts all unread mailbox
entries. It clips displayed records and file lines to 400 characters, ending
clipped lines with an ellipsis. On `MAILBOX`, end the Bash call: Claude Code
delivers the message at that tool-call boundary. Run step 0 on the output and
delivered message, then rerun the call in a loop.

The `cci:<drive>:<desk>` source runs `cci tail` for records addressed to the desk
past the cci cursor named for it, advancing that cursor. The script also advances
each file's cursor. Act on the printed records and file lines before reading
beyond those cursors (I2). Run the 3-minute reconciliation pass, the 30-minute
summary, and periodic sources such as `ledger.py watch ... --once` and
`ccx vcs pr watch ... --once` as foreground steps in that same loop between
waits, never as background Bash or Monitor.

Only top-level session desks arm one Monitor on
`cci watch --drive <drive> --cursor <desk> --to <desk>`
at timeout 1800000. The watch exits by itself after 29 minutes; re-arm it on every
exit and after compaction. It advances the desk's cci cursor as it prints records;
run step 0 on those records at every wake.

*Prevents the 34-minute miss of R956-R969 in a backgrounded desk pass on
2026-10-02. The first fix armed a Monitor an in-process desk is never woken by.*

*Prevents root `SendMessage` picks sitting unread for 20-60 minutes while lanes wait, as happened to sweepers-delete, inference-delete, lr-dashboard, applied-from-pulumi, and platy-ux-promises-3 on 2026-10-04.*

## The lane bus

The bus is `cci`. Each post is one record, and each lane reads deliveries from a
cci cursor named for that lane. `SendMessage` can arrive while its reader is idle
or mid-poll. The reader checks cci before acting on a message that may be stale.
`reference/bus-contracts.md` holds the record kinds and the delivery rule.

The root puts the cci drive name in every brief beside the ledger id. Durable
decisions stay in cc-notes; their bus records link them with `--ccn <id>`.
`incident.py` comms still use `scripts/bus.py` until that integration moves to cci.

**B1. Post the state, message the pointer.** Use
`cci post --drive <drive> --lane <lane> --kind <kind> --text "<text>"` for
`decision`, `head`, `contract`, `blocker`, `ask`, `answer`, and `withdraw` records.
Post decisions other lanes build on, heads after every push, and contracts for
interfaces other lanes consume.

Address records with `--to` when a named lane must act; link replies with `--re <seq>`.
The `SendMessage` that wakes that lane carries the entry number and nothing else. *Prevents a lane acting on the body of a stale message
when the log already holds the newer entry.*

**B2. Read at every wake and before every decision or report.** A lane's first tool
call on any wake is `cci tail --drive <drive> --cursor <lane> --reader <lane>`.
Add `--topic` or `--kind` filters to subscribe to broadcasts; addressed records
arrive regardless of those filters. Read again before deciding, reporting, or
asking.

A read that shows `[ANSWERED #n]` or `[WITHDRAWN #n]` on an entry ends any wait on it. A message is never acted on before
the read. *Prevents the wait on an answer already given and the act on a verdict
already retracted.*

**B3. Watch while running.** Landing desks, priority desks, and shards follow I7.
Other top-level lanes arm one Monitor on
`cci watch --drive <drive> --cursor <lane>-watch --reader <lane>` with the same
subscription, at the maximum timeout, and re-arm it when it exits after 29 minutes.

Other in-process lanes run the watch in the foreground with `--for 50s`, then read
their lane cursor under B2. The separate watch cursor keeps wake events from
advancing the lane's read cursor.

The watch prints an entry only when one is delivered, so a running lane hears a blocker or an
answer within the interval instead of at its next wake. This is the one Monitor a
lane keeps; R3 still forbids one per build. *Prevents the idle lane that never saw its
CI red until the owner did.*

**B4. Withdraw, never overwrite.** A retracted verdict, a moved head, or a changed
interface is a `cci post --drive <drive> --lane <lane> --kind withdraw --re <seq> --text "<reason>"`
from its poster, then a new record. Address the withdrawal to the original recipients.
Every text read of the old entry shows the withdrawal, so a lane that already acted learns it and a lane
that has not yet acted never does. *Prevents two lanes carrying two versions of one
verdict.*

**B5. Read `state` before asking, and `digest` for the root's view.**
`cci state --drive <drive>` gives the latest non-withdrawn head and contract per
lane and topic; a question it answers is never sent to a lane.

The root reads `cci digest --drive <drive>` beside the desk's summary for open
asks, blockers, holds, incidents, and the latest record per lane. The digest covers 24 hours by default
and counts older open items; use a longer `--since` window to inspect them. An open ask
past its lane's cadence is the root's to dispatch under R6. Two contracts on one topic
from two lanes are a collision to rule on before either ships. *Prevents the root
relaying by hand what any lane could read, and the contradiction found after both
sides landed.*

The desk posts each `route` line with
`cci post --drive <drive> --lane <desk> --kind blocker --topic <pr> --to <lane> --text "<route line>"` in the pass
that prints it, so a red reaches an idle lane at its next wake whatever became of the
message.

## Mechanics

### Lane brief

The `ccx:` line names the lane's role for the hooks: `fix` with incident wording routes
to the orca-desk, and `helper`, `reader`, `watch`, `export`, `evidence`, `handoff`,
`comms`, and `triage` lanes need no root task.

```
ccx: role=<role> tooling-lane=<key, for a tooling lane only>
Authority: <what you do without asking; what stops for the owner>.
Verified facts, do not re-derive: <ids, shas, URLs, state already confirmed>.
Design rulings, verbatim: <relevant register rules, their linked answer ids,
  and the entry point (symbol at file:line) each requires; or
  "none">. Before READY, state which entry point your diff calls, each ruling it meets,
  and anything it leaves out; the root confirms before a ship lane launches (R22).
Do:
  1. <step>
  2. <step>
Escalate early, do not improvise: scope surprise, an assumption the code refutes,
  an auth or approval gate, or two failed approaches. Return findings + 2-4 options.
AskUserQuestion is unavailable; on a decision, take the brief's default, log it with
  `ccn log append <drive log id>`, and report it.
Do NOT touch: <files, branches, worktrees another lane owns>.
Worktree: <absolute path, exclusive to this lane>.
Keep inbox lines under 400 characters; put evidence in a file or cc-notes and leave a pointer in the line. Orient with `inbox-digest.py --all <files>` when starting a new lane.
Standing rules register: <id of the newest `standing-rules:<slug>` doc, or "none">.
  The owner-approved register has at most 30 rules. Carry its wording, never full answers.
  Claude subagents, including planning agents, receive it once at SubagentStart.
  Orca Claude workers carrying CLAUDE_LONG_RUNNING_DRIVE receive it once at SessionStart.
  Above 9,000 characters, the hook names it. Read it with `ccn doc show <id7>` before acting.
  For Codex, paste the body from `ccn doc show <register id>` here verbatim.
  Codex workers have no hooks. In Claude drive sessions, the key-moment judge can
  inject one relevant durable answer, verbatim, once per lane per answer.
Every rule binds this lane. Your READY names the rulings your diff touches.
Standing rules served: <`R<n>` ids with their answer ids, or "none">. Your task cites
  them; finishing it never retires them, and no report calls them done.
Stack shape, under D19: put each shared-file edit in the smallest additive first PR of
  your stack, in the file's declared order. When a file you change is in another open
  PR, stack on that PR instead of racing it. Never commit a generated output; the build
  generates it. A stack that still carries a regenerated artifact rebases and
  regenerates it on ejection.
Holds file: <path>, root-owned; rebuild a fresh numeric held file before every
  enqueue from its #<n> entries and held lanes' open PRs under D3.
Self-enqueue: take the largest green, approved, unheld bottom prefix and run
  `stack-enqueue <prefix top> --hold $(cat <held file>)` at once, omitting `--hold`
  when the numeric file is empty, then `ledger.py report` the enqueue. Where the
  repo has no enqueue script, use
  `ledger.py label --repo <repo> --ledger <id> --pr <prefix top> --expect-head <sha> --checkout <worktree>`.
  Never self-enqueue above a held PR or any PR of a held lane. Report `held` on
  your tip, name the held PR, and leave release to the root.
  Keep `ccx vcs pr watch` on the stack; on ejection or conflict, rebase and
  re-enqueue the moment the restack is green. Never wait for another stack to land
  before enqueueing yours. After the prefix lands, restack the PRs above it with
  `ccx vcs stack submit`. Never end a turn with a ready, unheld prefix unenqueued.
Ledger: <id>. Register your branch prefix when spawned:
  `ledger.py register --ledger <id> --lane <name> --branch-prefix <prefix>`.
Report every PR open, push, enqueue, and READY yourself, in this one shape:
  `ledger.py report --ledger <id> --pr <n> --head <full sha> --lane <name> --verdict <clean|red|conflicting|held> --text "<one line>"`,
  plus `--ask <id>` for an owner ask. READY is `--verdict clean --text "READY ..."`.
  The desk's inbox reads that row; a SendMessage to a looping desk is never read.
Bus: <id>; script bus.py, on PATH by name; --repo <drive checkout>.
  Subscribe: --topic <each PR, branch prefix, and contract you own or consume> --kind decision.
  First call on every wake, and before every decision, report, or ask:
    `bus.py read --bus <id> --lane <name> <subscription>`; a message is acted on only after it.
  While running: one Monitor on `bus.py watch --bus <id> --lane <name> <subscription>`,
    timeout 1800000, re-armed on expiry.
  Post a `head` after every push, a `contract` for anything another lane consumes, a
    `decision` another lane could build on, a `blocker` or `ask` addressed `--to` the lane
    that acts; `withdraw --re` before you change or retract any of them. A SendMessage
    carries the entry number, never the body.
Codex: call `Skill(codex)` or `codex:codex-wrapper`; a one-off question goes to `codex-ask`.
`# ccx:raw` at the end of a Bash command runs it past the hooks as written. Use it only
  where a hook misreads the command, and name that hook in your report.
Run subagents and codex in the foreground (blocking), or poll the reply file in a foreground loop to a terminal state; never background-and-end-turn.
Record each sub-dispatch with `ledger.py ask` before dispatch and `ledger.py answer` when its reply lands.
Verify through CI: never run a whole-package build or suite locally (buck2/cargo build,
  `yarn tsc:*`, `bun test`, jest, `go test ./...`). Run locally only the one failing test
  that reproduces a red CI step, or a single artifact this brief names, one at a time with
  `-j 8` or the tool's equivalent. Load governs only those local runs: above the core
  count, post `HELD on load <load>/<cores>: <command>` to the Run mailbox and wait at most
  5 minutes. Never hold a Buildkite build, a saved-plan apply, an incident step, or an
  owner-directed step on load.
GitHub budget: one 5000-point hourly GraphQL budget serves the whole account, at 1 point
  per call. Watch a PR with `ccx vcs pr watch --state <file>` or REST
  `gh api repos/<owner>/<repo>/commits/<sha>/check-runs`, at most once a minute; never
  loop `gh pr view --json statusCheckRollup` or `gh pr checks`. Probe `rateLimit` at most
  every 5 minutes. Ship only while `rate.remaining` in
  `~/Library/Caches/cc-context/prstate/<owner>/<repo>/state.json` exceeds 1500; REST
  `gh api rate_limit` reads stale, so never gate on it.
Report short deltas with pointers (file:line, PR number, sha, Slack ts, disk path); never
  paste a diff, log, PR body, or thread into a message. Write it to disk and send the path.
Finish: a lane with a PR finishes only once its squash `(#N)` is on the base branch.
  Drive to a terminal state, then SendMessage <orchestrator> exactly one report,
  ≤10 lines: verdict | ids | what changed | what is next. That message is your last
  action. Final text after that SendMessage is empty or one line under 300
  characters (outcome + pointer), never the report again.
  Do not end a turn waiting. Every push to a reported PR re-reports the new
  head with `ledger.py report` in the same turn; the desk grades without waiting for it.
```

For implementation and test lanes, prefer the repository's canonical remote
entrypoint when it provides one. In Forge-AI/monorepo, follow
[the Orca skill](https://github.com/Forge-AI/monorepo/blob/dev/.agents/skills/orca/SKILL.md).
It defaults to Sprite prepare+attach and the already-owned native Run with its
sole inbox consumer. It does not route through desk-runner or common
`orca-launch.sh`. Preserve explicit model, effort, and Codex service tier.

Give a canonical remote worker the skill's complete VM brief and source-return
contract. It returns an uncommitted patch and strict report; the root collects,
reviews, and ships the source. Do not copy the Mac lane-ship template's paths or
PR obligations into that brief. Retain every session, including explicit local
root/Fable and extremely sensitive workers; completion or a cleanup recommendation
never authorizes closing one. Dedicated remote API keys belong only to the
worker process; local Fable keeps existing Mac interactive authentication.

For in-process subagents, use one of this plugin's two lane types with the
routing table's `model`. Both definitions default to Opus; set a different
model explicitly when the assignment calls for it. Fable is local only, for
top-level root orchestrators or extremely sensitive implementation. Ordinary
workers and subdesks do not inherit Fable from the root.

The standing subagents, the desk's shards, sequencers, and pollers are
`long-running:lane`. An implementation lane that ships a PR or calls a skill such as submit-pr, open-pr, or
codex is `long-running:lane-ship`. Both leave out `ToolSearch`, the `mcp__*` tools, and
the deferred-tool list, and both carry the 1h prompt cache a nine-minute poll needs.
`lane` also leaves out the Skill tool and the skill listing, so it starts 16k tokens
lighter than `general-purpose`; `lane-ship` starts 5k lighter.

If a `lane` needs a skill, give that operation to a separately named `lane-ship`
with a scoped brief.
Keep the original lane running; the new lane must not duplicate its active work.

A lane using the separate desktop Orca adapter is a session the Agent tool cannot
message. Submit `desk-runner.py launch --config C --key R<n> --lane L --model M --effort E
--brief PATH`; the runner invokes `scripts/orca-launch.sh` using
`reference/orca-workers.md`. `reference/orca-lane-brief.md` is its brief, ready to paste:
a shared contract and lane section concatenated into one file, with a ≤300-character
pointer as the `--spec`. A codex Orca lane launches on Orca's codex agent under O15,
never as a claude worker calling the codex skill; the codex skill is for inline lanes,
and `codex-ask` for one-off questions.

That adapter's launch, relay, and receipt rules apply only to its own lanes.
Canonical remote workers use home Dispatch messages and the repository skill's
source-return workflow. Namespace remains opt-in pending regular Compute runtime
acceptance; native recipe-picker enablement is a separate surface.

One isolated checkout per editing lane. Two agents in one checkout race HEAD, the index, and
untracked files; a restack under a running ship lands its staged diff on whatever branch
is checked out at commit time.

### Scoped resume

Only for a lane that has already reported. Never message a running lane; put anything it
may need in a file its brief tells it to read, or wait. A `codex:codex-wrapper` gets
nothing after it reports at all: spawn a fresh one with a scoped brief.

```
SCOPED FOLLOW-UP. Your original brief is CLOSED. Do not redo any of it.
Already done, do not repeat: <one line>.
Answer only: <the one question>.
Reply ≤5 lines, then stop. No re-investigation, no new edits, no new files.
```

### Sequencer lane

One lane owns a whole wait→do chain and sends one message at the end of it. Worked
example, landing a PR through a merge queue:

```
Do:
  1. gh pr edit <n> --repo <repo> --add-label merge
  2. Poll origin/dev for the squash commit "(#<n>)" until it appears or 40 min pass.
     A queue-merged PR reads state=CLOSED, mergedAt=null; the commit is the truth.
  3. On landing, <the dependent step this chain exists to trigger>.
Finish: one report - landed sha, dependent step result, or the blocking state.
```

The orchestrator learns the merge landed and the next step ran from that one message.
It never polls the queue itself.

### Merge train lane

The lane D7 names. It owns the rows whose files hit the hot set, the handful of files
that nearly every open PR touches. It turns them into one linear stack at a time
instead of leaving each PR to rebase against the others alone.
`reference/merge-train-brief.md` is its brief, ready to paste; it is a
`long-running:lane-ship` lane with its own worktree and a log per train.

```sh
ledger.py train --repo "$REPO" --ledger "$LEDGER" --paths 'infra/ci/src/pipelines/release/**' 'infra/engine.ts'
```

`train` prints at most six cars. Green cars come before conflicting but approved
ones, then fewest file overlaps with the other cars, then oldest. It also prints the
rows past the cap as the next train, the hot rows that are not ready, and the
`ccx vcs stack rebase --linearize` line for the cars. It leaves out held rows and
rows the queue already holds, and writes nothing. The globs are `fnmatch` globs, so `*` also crosses `/`.

Run one train per hot set, never one for the whole repo: a train is serial, and one
red car evicts every car above it.

### Semantic-collisions lane

Parallel lanes collide in meaning long before they collide in text: one lane builds on
a mechanism another lane is deleting, and both PRs go green. A standing
`semantic-collisions` lane, a `long-running:lane` on opus at `xhigh`, holds the bird's-eye view
of every open PR and worktree and resolves those collisions before either side lands.
`reference/semantic-collisions-brief.md` is its brief, ready to paste.

- It keeps a map file, one row per collision: the lanes, the PRs, the contradiction,
  the verdict, and who was told. Head snapshots beside it let each sweep re-read only
  the heads that moved.
- Per collision it messages the owning lanes directly with a 20-minute deadline. Only
  a collision no settled ruling decides goes to the root, as `RULING NEEDED`.
- The root pings it on every 30-minute tick, since an in-process teammate cannot
  schedule itself.
- An owner ruling that retires a mechanism triggers a full sweep of every open PR and
  worktree, with one verdict per PR: builds on it, mentions it, or carries the same
  guard under another name.

*Prevents lanes building on the release ledger and the skew check on 09-29 while
other lanes were deleting both.*

### Polling loop

Lanes poll in the foreground. The Bash tool caps `timeout` at 600000 ms, so a call
budgets about nine minutes and the lane re-runs it until a terminal state.
A lane loop waits through `desk-wait.sh` on its own team mailbox instead of `sleep`,
so a `SendMessage` ends the call.

```sh
deadline=$(( SECONDS + 540 ))
while (( SECONDS < deadline )); do
  state=$(<authoritative query>)
  case "$state" in
    passed|merged|CREATE_COMPLETE|UPDATE_COMPLETE) echo "TERMINAL ok $state"; exit 0 ;;
    failed|broken|canceled|timed_out|ROLLBACK_*|*_FAILED) echo "TERMINAL bad $state"; exit 1 ;;
  esac
  wake=$(desk-wait.sh 30 "<team mailbox>=<cursor file>")
  case "$wake" in
    MAILBOX*) echo "$wake"; exit 0 ;;
  esac
done
echo "STILL_RUNNING $state"
```

Run it with `timeout: 570000`. Cover failure states in the `case`, not success alone, or
a broken build polls until the deadline. Anything needing an env token runs in the lane's
Bash; a Monitor shell does not inherit the environment, so `BUILDKITE_API_TOKEN` and its
kin are empty there. Prefer a CLI with a stored credential over an exported token.

A nine-minute call outlasts the 5-minute prompt cache a subagent gets by default, so the
lane's next request rewrites its whole context at 1.25× the input price instead of
reading it at 0.1×. Lanes need the 1h cache. The `subagentPromptCacheTtl: "1h"` setting
or `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h` gives it to every subagent. Without either,
the lane types' `experimental.cacheTtl: "1h"` gives it to lanes alone, except while a
subscription is in overage.

### Shared API budgets

The GitHub REST and GraphQL limits and the Buildkite REST limit are each shared
by every lane, the desk, and every watcher at once. A lane that
reads right up to its own rate-limit header can still starve the desk and every
release watch running beside it.

Read a job's log once and keep it on disk; never loop a log read over a build's plan
shards, and never scan more than the handful of shards a failure actually names. A
scan that reads more than a few hundred logs is a decision, not a background job:
size it and send the count to the root before running it. Prefer an artifact or an
annotation over scraping a log at all.

One poller owns each PR or build, under R3. Resuming it after a wake is not the same
as starting a second one, and restarting a poller from scratch resets whatever
backoff it was holding. A 429 or 403 mid-poll gets a fixed wait and a retry, never
an immediate one and never a fresh loop.

GitHub GraphQL costs 1 point per call, 5000/hr shared by the whole account. Watch a
PR with `ccx vcs pr watch --state` or REST through
`gh api repos/<owner>/<repo>/commits/<sha>/check-runs`, never more often than every
60 s. No `gh pr view --json statusCheckRollup` or `gh pr checks` loops; no `rateLimit`
probes more often than every five minutes. The shared GitHub budget ran dry at 04:39Z
on 2026-10-01.

**GitHub budget.** GraphQL is one 5000-point hourly budget for the whole account, and
every call costs 1 point whatever its shape. Steady polling by Orca cards, PR
watchers, and lanes measured 40 to 75 points a minute, and a ship storm on top
emptied it. A lane watches a PR with `ccx vcs pr watch --state <file>` or the REST
check-runs endpoint, at most once a minute, and never loops `gh pr view --json
statusCheckRollup` or `gh pr checks`. It probes `rateLimit` at most every 5 minutes,
and ships only while `rate.remaining` in
`~/Library/Caches/cc-context/prstate/<owner>/<repo>/state.json` exceeds 1500. REST
`gh api rate_limit` lags the real GraphQL counter, so nothing gates on it. Both lane
brief templates carry this rule.

*Prevents a per-shard log loop emptying the Buildkite budget mid-release, and a bulk
scan across thousands of logs failing two releases' own pipeline syncs on the same
shared limit; also the account's GraphQL budget running dry at 04:39Z during release v3
(2026-10-01), which refused every lane's ship until the hourly reset.*

### Desk cadence

Lanes self-enqueue under D1. The desk runs the reconciliation loop below; the root
records asks and verifies their acceptance checks. Create the ledger once at spawn
and put its id in every brief. Each pass reads all PR numbers in one
`ccx vcs pr status <n1> <n2> ...` call and the Buildkite build list, never one REST
call per PR. Run every three minutes, with desks and shards staggered by a minute at
`:00`, `:01`, and `:02`.

Where the checkout carries `stack-enqueue`, start the landing runner from
[its config](reference/orca-desk-brief.md#config-and-startup). It owns the enqueue
pass and re-reads holds for every call. The model desk runs the ledger duties
below and never adds an enqueue loop beside it. Use `ledger.py label` only where
the repo has no enqueue script.

A resumed root in a new session runs `drive.py start --drive <id> --ledger <id>`
to join the existing drive. Run `drive.py end` only when the drive is over.

```sh
LEDGER=$(ledger.py init --title "desk: $DRIVE")
drive.py start --ledger "$LEDGER" [--orca-run <run>] [--state-dir ~/.claude/scratch/<slug>]
ledger.py ask     --ledger "$LEDGER" --text "<verbatim>" --lane lightning-eh --accept "<acceptance check>"

# on each report: record it and grade the current head; reports are not a gate
ledger.py report  --ledger "$LEDGER" --pr 21221 --head <sha> --lane lightning-eh --verdict clean --ask ask/000001
ledger.py ruling  --ledger "$LEDGER" --lane p2-edge-rows --pr 20284 --text "land without the document form" --options "A land|B hold|C close"
ledger.py enqueue --ledger "$LEDGER" --kind idle --pr 21221 --head <sha> --lane lightning-eh --text "done"
ledger.py inbox   --ledger "$LEDGER" --take
ledger.py register --ledger "$LEDGER" --lane lightning-eh --branch-prefix lightning/ --pr 21221

# every 3 minutes, staggered :00/:01/:02: reconcile every tracked current head
ledger.py refresh --repo "$REPO" --ledger "$LEDGER"
ledger.py reconcile --repo "$REPO" --ledger "$LEDGER" --checkout "$CHECKOUT"
ledger.py route   --repo "$REPO" --ledger "$LEDGER"
ledger.py stale   --ledger "$LEDGER"

ledger.py route --repo "$REPO" --ledger "$LEDGER" --pr 21221 --job "plan comment missing for this head"
ledger.py hold  --ledger "$LEDGER" --pr 20284 --reason "waits on #20314" --hours 4
ledger.py hold  --ledger "$LEDGER" --pr 20290 --reason "argo cutover waits on the owner" --hours 12 --stack
ledger.py hold  --ledger "$LEDGER" --pr 21052 --reason "dev-red:infra-plan-observability" --hours 6

# hot-set conflicts and a gone lane's hot-set rows go to the train, its other rows to the red desk
ledger.py gone  --ledger "$LEDGER" --lane lightning-eh
ledger.py route --repo "$REPO" --ledger "$LEDGER" --train merge-train --paths 'infra/ci/src/pipelines/release/**' 'infra/engine.ts' --fallback red-desk

ledger.py summary --repo "$REPO" --ledger "$LEDGER" --checkout "$CHECKOUT"
ledger.py show    --ledger "$LEDGER" --asks
ledger.py live    --ledger "$LEDGER" --at "$(date -u +%FT%TZ)" --text "release 37 deployed"
ledger.py drop    --ledger "$LEDGER" --ask ask/000002 --reason "owner withdrew it"
ledger.py answer  --ledger "$LEDGER" --ask ask/000003 --text "<the reply>"

if [ ! -f "$CHECKOUT/.agents/skills/submit-pr/scripts/stack-enqueue" ]; then
  ledger.py label --repo "$REPO" --ledger "$LEDGER" --all-clean --shard lane-a,lane-b --checkout "$CHECKOUT"
fi

drive.py end
```

At spawn, arm the watch under Monitor at its maximum timeout and re-arm on every
expiry. Pass each priority PR the root names with `--priority`; forward every `P0`
line immediately. The three-minute batch reconciles the ledger.

```sh
ledger.py watch --repo "$REPO" --ledger "$LEDGER" --checkout "$CHECKOUT" [--priority <n>]... [--shard <lane>,<lane>]
```

`--shard` watches only those lanes' rows and keeps its own snapshot,
`~/.cache/ccn-ledger/<ledger>.<lanes>.watch.json`, so watchers on different shards never
share one. A PR that had no entry in the snapshot when a pass first saw it and already
landed or closed emits nothing and settles nothing; `landed` settles it. A standing
`conflicting`, `red`, or `ejected` still emits once.

`label --dry-run` runs every guard, prints the stack it would enqueue, and writes
nothing; run it once on a repo before the first live label.

The parallel example uses each ready prefix's top as `TIP` and collects each output
after `wait`; include every ready prefix in the pass, or run it immediately for ready
reports. Each shard does the same for its lanes, using held rows from the whole
ledger. Report each successful enqueue with `ledger.py report`; refresh records it
as `in the queue, labelled outside the desk`. A `held` refusal waits for the root and is not routed.

Where the repo has no enqueue script, use `ledger.py label --pr <tip> --expect-head <sha>`
for each ready prefix's top; mirrored ledger holds are the guard. `label --all-clean` is
the fallback sweep there because it grades every stack in one sequential call.
It supports `--dry-run` and prints each stack to enqueue. It considers every tracked open row whose
current head has never carried the label and is not held, and re-reads each tip before
grading it. `--expect-head` pins the graded tip for a single-stack call.

Reports open rows, carry the lane's text, and feed `stale` and p50 report-to-landing
minutes; the desk grades without waiting for them.

`stale --minutes N` sets the report-age threshold, which defaults to 30 minutes.
`stale` also names every open PR opened at least `--hours` ago, 60 by default, as
`aged #N <h>h <lane>: <state>`; land it or close it before the reviewer's three-day
full re-review applies. `route` without `--pr`
sweeps every red or conflicting row, reads the first failing line from the Buildkite
log, prints the message to send each lane, and records it; `route --dry-run` prints
and records nothing. With `--train <lane> --paths <globs>`, a conflicting row whose
files match goes to the train, and so does any matching row whose lane is gone; a
red row whose lane is live stays with it for the code fix. No route addresses a lane
`gone` names: `--fallback` sends its other rows to a live lane, `--pr` needs `--lane`,
and a route recorded to it is sent again. A row held `dev-red:<step>` is skipped
until its hold expires. `hold`
refuses a PR with green rows stacked on it in the ledger unless `--stack` holds them
too. `unlabel --reason` records why a label came off and blocks a
re-label of that head; it does not stop a queue that already took the PR. A lane
asking what it owns gets `ledger.py show --red`, never the raw table.

### Drive dashboard

`drive.py start` and every root `SessionStart` while the drive is active start the
dashboard server automatically, only when the session belongs to a registered
drive. No agent step is needed. The hooks start it detached so they never block a
session and print no URL. Get the URL with `lr-dashboard.py url`.
Open it to read the drive's state.

Open the URL printed by `lr-dashboard.py url` and click **Ask** in the bottom-right
corner to ask about owner asks, quiet lanes, recent landings, or the latest census.
Chat opens in a floating window over the dashboard and preserves its open state and
history across refreshes in the same tab. When Tailscale is running, the URL is
`http://<MagicDNS name>:<port>/`, and anyone on the tailnet can open the dashboard and
use chat. Each question starts with a digest of the drive; the chat can search inbox
history, tasks, ledger rows, boards, and cc-notes, read source records, and inspect
state sections. Answers link back to the refs they cite. The server uses Cerebras's
`gpt-oss-120b` with `CEREBRAS_API_KEY` set in its environment; the browser receives a
per-server chat token, never that key. If the server started without the key, restart
it with the variable set and reload the page.

Run `lr-dashboard.py start --drive <id>` to start it by hand. Use `lr-dashboard.py url`
to print the running URL, `lr-dashboard.py snapshot` for JSON, and `lr-dashboard.py serve`
to serve in the foreground.

The server binds `127.0.0.1`, prefers a port derived from the drive id, and records
its address in `<state dir>/dashboard/server.json`. On the next start, a newer
plugin version replaces the old server through `/shutdown`.
`start` holds a per-drive lock at `<state dir>/dashboard/start.lock` so concurrent
starts do not spawn two servers. `/shutdown` requires a token recorded in
`server.json`, so a browser page from another origin cannot stop the server.

It reads inbox files and rotated archives under `<inbox>/<file>.md.archive/*.md`,
the ledger, the root's task list and archive, and cc-notes
plans, progress docs, handoff docs, program docs, logs, investigations, and answers. It
also reads cc-present boards, compactions from the root transcript, Orca run tasks,
inbox watch state, and beat files.
Collector failures, including registry reads racing `drive.py end`, appear as
source errors without stopping the poll. Orca workers are read across every page.
An explicit `CLAUDE_CODE_TASK_LIST_ID` wins when resolving the task list.
Transcripts are found under any project directory, preserving earlier compactions
after a drive moves checkouts.

The only hand-edited dashboard input is `<state dir>/dashboard.yaml`. Use top-level
section keys holding `- text` items, or `- text:` items with an indented `url:` line.

The server records each inbox file's line count and mtime in
`<state dir>/dashboard/seen.json` each time the file grows. Walking back from the
end, every recorded mark bounds the lines it covers. This places lines appended
while the server runs; older history still relies on the clocks in each line.
A file that shrinks, such as one rotated, resets its marks.

The walk follows a line's clock back at most 16 hours, so a quoted or misordered
stamp cannot drag earlier lines back by days. An undated clock requiring a larger
step is marked approximate, as is a line without a clock. When a line has no
parenthesized stamp, clocks are searched in its first 80 characters.

ISO stamps keep their zone. Month-day stamps after the cursor fall in the previous
year.

### Lane bus

The root creates the bus once and puts its id in every brief; everything after that is
`bus.py`, run with `--repo <drive checkout>` from any worktree.

```sh
BUS=$(bus.py init --title "bus: $DRIVE")

# a lane, on every wake and before every decision, report, or ask
bus.py read  --bus "$BUS" --lane pr-plans --topic 27510 --topic iam-contract --kind decision

# a lane, while running: one Monitor, timeout 1800000, re-armed on expiry; prints only deliveries
bus.py watch --bus "$BUS" --lane pr-plans --topic 27510 --topic iam-contract --kind decision

# publish state instead of answering questions about it
bus.py post --bus "$BUS" --from pr-plans --kind head     --topic 27510        --text <full sha>
bus.py post --bus "$BUS" --from iam-structural --kind contract --topic iam-contract --text "grants derive from row kinds; no hand IAM"
bus.py post --bus "$BUS" --from iam-structural --kind decision --topic iam-contract --text "IAM wave lands before the pre-hold" --to pr-plans

# ask, answer, block, withdraw; the printed #seq is what the SendMessage carries
bus.py post --bus "$BUS" --from pr-plans --kind ask      --topic 27510 --text "clear to label before the IAM wave?" --to iam-structural
bus.py post --bus "$BUS" --from iam-structural --kind answer --re 7 --text "yes, land it"
bus.py post --bus "$BUS" --from landing-desk --kind blocker  --topic 27510 --text "DESK #27510 3f3acff97: plan job red" --to pr-plans
bus.py post --bus "$BUS" --from artifact-contract --kind withdraw --re 4 --text "verdict retracted; contract changed"

# read state, never ask for it; the root reads summary, never the log
bus.py state   --bus "$BUS" [--lane iam-structural] [--topic 27510]
bus.py summary --bus "$BUS"
bus.py read    --bus "$BUS" --lane root --all --peek   # the whole log, when a thread must be read
```

A `head` is the full 40-hex sha. A reply inherits its target's topic; an `answer` goes
to the asker and a `withdraw` to the target's addressees unless `--to` says otherwise.
Only the poster withdraws an entry, once. The cursor is
`~/.cache/ccn-bus/<bus>/<lane>.cursor`, so a compacted lane resumes where it left off;
`--peek` leaves it, `--since` and `--all` re-read. `read` prints
`nothing new since #n` when nothing reached the lane; `watch` prints nothing then, and
`bus unreachable: ...` once when cc-notes stops answering. Every post holds a lock keyed
on the bus and retries a contended ref, because `ccn log append` refuses rather than
queues a concurrent write.

### Label watch

`scripts/label-watch.sh` enqueues the stacks the root holds outside a ledger, which are
priority PRs under D1 and every PR on a drive too small for a desk. The desk keeps to `ledger.py`.
Each PR passes this gate before its stack goes into the queue:

1. `ccx vcs pr status` reads it `not queued`. A queued or landed PR prints `SKIP`.
2. It has no `hold` label.
3. Its head merges cleanly into the freshly fetched trunk under `git merge-tree`.
   Otherwise, it prints `CONFLICT` with the files.
4. Its head also merges cleanly into the trunk merged with each queued PR's admitted
   commit, apart from PRs in its own downstack. A conflict prints
   `NOT-READY <sha> conflicts-with #N <files>`, and the PR waits on the list until #N
   lands, when the trunk check takes over.
5. It is mergeable, its base is not `graphite-base/*`, and its state is `clean` when
   its base is the trunk.
6. Every commit status and check run on its head sha passed or was skipped, apart from
   Graphite's own `mergeability_check`. A red one prints `NOT-READY <sha> red <names>`,
   an unfinished one `NOT-READY <sha> pending <names>`. GitHub reads a red optional
   check as `unstable`, not blocked, so mergeability alone lets a red PR through.
7. Every required approver approved its current head sha.

No label bypasses a gate. An override or skip-checks label put on a PR to clear a red
the diff did not cause is banned. When an untouched shard reds falsely, trigger one
rebuild so it re-grades on a fresh build. If it reds again, report the shard, its
time, and its p99 to the root instead of labelling around it.

A listed PR brings in its stack, which is every open PR below it down to the trunk,
walked through `pulls?head=`, and every open PR stacked above it, walked through
`pulls?base=`. The gate runs bottom-up over each stack. A PR already carrying the label
prints `SKIP labelled` and passes the gate for the PRs above it. A PR the queue already
holds prints `SKIP queued`, and every PR above it waits as `NOT-READY <sha> downstack #N`
until it lands. The first PR to fail stops the walk, and every PR above it prints
`NOT-READY <sha> downstack #N` without reading its checks.

A stack's largest passing bottom prefix goes into the queue now. One
`POST /v1/graphite/merge`, the call `gt merge` makes, sends the prefix's PR numbers
bottom first. Graphite queues them as one batch.

When open PRs remain above it, the prefix top prints `<top> ENQUEUED <sha> prefix`
and the PRs below it `SKIP covered-by #<top>`. Each PR above keeps its `NOT-READY` reason with
`restack-after #<top>` appended. If the prefix reaches the stack's top, that PR
prints `ENQUEUED <sha>` without `prefix`.

After the prefix lands, route the first PR above it to its owning lane to restack
the rest with `ccx vcs stack submit`; Orca routes use
`desk-runner.py relay --config C --key R<n> --lane L --text T`. Where the landing
runner owns D16, it sends this route; the desk never duplicates it.
A stack that forks never goes in. Every passing PR prints
`NOT-READY <sha> fork at #N` until the stack is linearized.
*Prevents #27520 sitting on a stale `graphite-base` branch after #27616 and #27617
land; the desk routes that PR to its lane for a restack.*

The call reads gt's token from `LABEL_WATCH_GRAPHITE_AUTH`, default
`~/.config/graphite/auth`, and hands it to curl on stdin, never on the command line.
A REST label can go unseen by Graphite; the API call is answered. A failed call prints
`API-FAIL enqueue`.

`LABEL_WATCH_DRY_RUN=1` appends `dry-run` to the `ENQUEUED` line and enqueues nothing.
`LABEL_WATCH_HOLD=<file>` names PRs, one per line, that fail the gate as
`NOT-READY <sha> held`. The watch re-reads the file every sweep, never enqueues or
appends a held PR, and holds that PR and every PR above it. A passing prefix below it lands.

```sh
export LABEL_WATCH_APPROVERS='forge-pr-reviewer[bot],poetic-svc' LABEL_WATCH_CHECKOUT=~/Code/monorepo
label-watch.sh once 25742
printf "%s\n" 25763 25780 >> "$LIST"
label-watch.sh watch "$LIST"
```

`LABEL_WATCH_REPO`, `LABEL_WATCH_TRUNK`, `LABEL_WATCH_LABEL`, and `LABEL_WATCH_INTERVAL`
default to the checkout's origin, its `origin/HEAD`, `merge`, and 300 seconds. Run
`watch` in the background.

Add a PR by appending its number to the list file; a number listed twice is read
once. Each sweep reads Graphite once for the whole list and GitHub only for PRs that
are not queued. A PR whose squash, a subject ending `(#N)`, is on the fetched trunk
prints `SKIP landed` without any read, and a closed PR prints `SKIP closed` and leaves
the list. The checks and reviews reads run only after the earlier gates pass. `watch`
deletes `ENQUEUED` and `SKIP` entries, keeps `CONFLICT`, `HELD`, `NOT-READY`, and `API-FAIL` ones, appends the
stack PRs it found still waiting on the gate, and prints a line only when a PR's result
changes. It exits once the list is empty and every PR it saw queued or enqueued has
closed.

`ccx vcs pr status` reads an eviction from the queue's Merge activity comment. The watch
prints `EVICTED <sha> <reason> <time>` once per eviction for a listed PR or one it saw
queued or enqueued. The PR then goes through the gate that sweep, so a conflicting head
prints `CONFLICT` with its files, and it returns to the list.
*Prevents #26918 printing only its stale `NOT-READY conflicts-with` line while the queue
had already dropped it for merge conflicts.*

The trunk is fetched into `refs/label-watch/<trunk>`, never `refs/remotes/origin/<trunk>`,
so a shared clone's other fetches cannot hold its ref lock. A fetch that still fails after
three tries prints `API-FAIL trunk-fetch` for every PR that sweep and enqueues nothing.

The queued PRs are the ones `ccx vcs pr status` reads `queued` on the list, and in
`watch` every PR the watch saw queued or enqueued, until it closes. A stack enqueued
earlier in a sweep is a conflict base for the rest of that sweep.
*Prevents #25907 being evicted for conflicting with #25890, which the queue already held
ahead of it.*

A `CONFLICT` or `red` line goes to the lane that owns the PR, to rebase or fix. Never
answer it with a label, and never re-queue an evicted PR before its lane pushes a fixed head. The PR stays on the list, and the watch enqueues the rebased stack once it passes.

A PR sent back for rework gets the `hold` label and leaves the list in the same turn.
Taking the label off does not dequeue it, and neither does converting it to a draft.
*Prevents a reworked PR landing anyway, as one did after Graphite had enqueued it,
through a removed label and a conversion to draft.*

### Stalled-red sweep

`watch` only reprints a line when a PR's result changes. A head stuck `CONFLICT`,
`NOT-READY`, or `EVICTED` for hours goes silent again the moment it was first seen;
a drive too small for a desk most needs that state surfaced.
`scripts/red-sweep.sh` reads the same list file `label-watch.sh` watches and re-alerts,
once per head, on any entry still stuck past `RED_SWEEP_AGE_MINUTES` (default 20):

```
STALLED-RED #<n> <sha> <status>/<mergeable_state> head <age>m old: <title>
```

A red head whose every failing status is a Buildkite build, with each failed step
also failed on the trunk's latest finished build of that pipeline, prints
`DEV-RED #<n> <sha> <step>,<step>` instead, once
per head while the trunk stays red. That red is the trunk's: hold it as
`dev-red:<step>` rather than route it. Once the trunk's step passes, a head still red
prints `STALLED-RED`. `RED_SWEEP_TRUNK` names the trunk, by default the checkout's
`origin/HEAD`.

Run it beside `label-watch.sh watch`, pointed at the same list file, as its own
background sweep; it never edits the list, so `label-watch.sh` still owns removing
an entry once it lands or gets skipped.

Classify first: a `DEV-RED` line is held, never routed. Every `STALLED-RED` line then
goes to the lane that owns the PR by SendMessage in the same turn, with a 15-minute
deadline, because an idle lane never sees CI. A red whose owner is gone gets a new
lane at once, or goes to the train or red desk under D8.

*Prevents a PR read `CONFLICT` sitting unmentioned again for hours until an owner
asked whether anything else was stuck the same way. Also prevents seven reds sitting
30 to 100 minutes with idle owners.*

### Compaction handoff

Once the Skill call or a `/long-running` prompt invokes `long-running`,
this plugin's capt-hook pack runs the session's compaction handoff for the rest of the
session, compactions included. Nothing inside the session clears it early. The hook
writes the handoff record and sends the root one nudge for the narrative.

**Threshold.** The hook reads the live model at every check, from the transcript's
newest non-synthetic assistant turn, never from the model the session started on. The
window is `CLAUDE_CODE_AUTO_COMPACT_WINDOW` env, else the `autoCompactWindow` setting,
else the model default, and never more than the model's own window. A `[1m]` suffix
uses 1M, as do the native-1M models sonnet-5, opus-5 and 5-5, fable-5 and 5-1, and
mythos-5 and 5-1. Every other model is 200k, including haiku-4-5, sonnet-4-x,
opus-4-0 through 4-6, and every claude-3 model.

`threshold = window − 33k`, and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` can only lower it
further. The check runs after each main-session tool call, never a subagent's, and fires
once used tokens cross 80% of that threshold.

**Nudge.** At 80% of the threshold, the hook sends the root one nudge, as context
on its next tool call or prompt. It gives used tokens against the threshold and asks
for a new progress doc with the root's narrative when convenient. The hook fills the
handoff from the owner-approved register, live standing inbox rules, open asks, tasks,
lanes, monitors, inbox heads and cursors, and lint findings. The root writes only what
those sources lack. The slug comes from `progress:<slug>` in the plan's existing
progress pointer line, or from the plan's file stem if that line has no slug. The nudge is not repeated,
and the nudge never holds the turn.

**Root narrative.** At the nudge, create a new cc-notes progress doc with only the
narrative:

```sh
ccn doc add "<drive>: progress <UTC>" --label progress:<slug> --when "Resuming or compacting the <drive> drive: read before anything else, after the plan" --body -
```

Use the nudge's UTC timestamp (`YYYY-MM-DDTHHMMZ`) and pass the body on stdin.
The progress doc is living guidance with a `when` trigger and supersede edges.
Never rewrite the plan; it remains the stable mandate and decisions document.
Use these sections:

```md
# <drive>: progress <UTC>

## How the drive runs
<root role, lane contracts, desks, ledger and rulings-log ids>

## Owner asks and state
<every ask, its current state, evidence, and next gate>

## Landed
<completed work and evidence>

## Waiting on the owner
<unresolved decisions and the options already presented>

## Root's next actions
<ordered actions, dependencies, and owed follow-ups>
```

Only when the repo lacks cc-notes or the `ccn` binary is unavailable, write the
narrative as a new file beside the plan at `<plan-stem>-progress/<UTC>.md`. Otherwise,
use a progress doc; a note, a log, or a loose file does not replace it.

**Generated handoff.** The hook writes the handoff with `scripts/handoff.py`, through
the `bin/handoff.py` wrapper. The script uses only the standard library and has two
verbs:

```text
handoff.py generate --program <slug> --plan <path> [--inbox-dir DIR] [--ledger ID] [--session FILE|-] [--narrative-doc ID | --narrative-file PATH] [--generated-doc ID] [--fresh-since ISO] [--strict] [--folder] [--repo PATH]
handoff.py lint (--doc ID | --file PATH) --program <slug> [--plan PATH] [--previous-doc ID | --previous-file PATH] [--repo PATH]
```

The standing rules register is the owner-approved cc-notes doc labeled
`standing-rules:<program>`, as owner answers `4d381e0` and `8cb6da7` require.
It has at most 30 rules. A consolidation lane proposes changes for owner approval.
Full answers stay in cc-notes, linked by id from the register.
The release-v3 register, doc `0cf17c9`, is about 8 KB.
Its body is mirrored as the plan file's last section.

`generate` reads the newest register doc by `updated_at`.
It never builds, edits, or supersedes a register doc or writes a register file.

The first progress section is `## Standing owner rules`.
A pointer names the register doc before its body, quoted verbatim with `  >` on each
line. With no register doc, the section says so. Every live `(standing)` inbox rule
follows, with its inbox filename. Each inbox rule missing since the previous handoff
appears last as `- <id> superseded by <successor id>` or
`- <id> superseded by nothing: the sources dropped it ...`.
The handoff carries no separate list of durable answer titles.

The lint requires the whole register quoted in order; quoting only its first line
fails.

The plugin ships a `bin/rulings.py` launcher.

`rulings.py register --program <slug>` prints the newest register as `{id, body}` or
`null`.

`rulings.py match` feeds the key-moment judge.
It mirrors every `scope:durable` answer into `<state dir>/rulings/<id7>.md`.
Atomic per-file replacements keep concurrent readers from seeing partial files.
This full set is the retrieval corpus only. Nothing injects it in bulk.

`match` reads the proposed action from stdin. It uses the first 2,000 characters
as the query for `ccx code search --semantic`.
It excludes answers the register already cites.

`--drive <id>` replaces `--program` for workers carrying `CLAUDE_LONG_RUNNING_DRIVE`.
It resolves the program and state directory from the drive registry.

**Sub-lane injection.** Every sub-lane receives the register once at start.
Claude subagents, including planning agents, receive it at `SubagentStart`.
Orca Claude workers carrying `CLAUDE_LONG_RUNNING_DRIVE` receive it at `SessionStart`.

The body arrives whole and verbatim when it is at most 9,000 characters.
Above that limit, the hook names the doc and says to read it with
`ccn doc show <id7>` before acting.

Codex workers have no hooks, so their briefs paste the register body verbatim.
A brief carries the register's at most 30 rules, never full-answer quotes.
These injections are advisory and fail open.

**Key-moment judge.** `ruling_judge.py` runs before these calls in root, subagent,
and Orca Claude drive sessions:

- A `GO`, `HOLD`, or `DECIDE` line appended to an inbox.
- A pull request opened through `ccx vcs ship`, `ccx vcs stack submit`, or `gh pr create`.
- A plan-file write or edit.
- A Slack write through `cc-slack send`, `cc-slack reply`, or a Slack MCP send.
- A lane spawn through an Agent prompt or an `orca-launch.sh` brief file.

The judge calls `rulings.py match` with `-k 5` and a 6,000-byte candidate budget.
The five nearest durable answers come from ccx semantic search.
Answers the register already cites are excluded.
A small model returns the one answer the action clearly bears on or would violate,
or none. The hook injects only that answer, verbatim, once per lane per answer.
It is advisory, never blocks, and fails open.

*Prevents the release-v3 rule "release everything as it merges" (answer 4ffc9a5)
vanishing from progress doc b0ebc9a and later compactions while stale "(owner's
word)" plan lines re-imposed the gate it withdrew (2026-10-01).*

The record carries open owner asks from the drive's ledger as `ledger.py` ask rows
that are not dropped, answered, or `LIVE`, with their state. It also carries the
root's open tasks, lanes and monitors from the background tasks at the root's last
`Stop`, and the drive registry line with the drive, ledger, Orca run, checkout, and
root sessions.

For each `~/.claude/scratch/<slug>/inbox/*.md` file, the record carries its head id,
its `<file>.cursor` value, and its last five ruling lines. Sections run in this order:
`Standing owner rules`, `Read first`, `Open owner asks`, `Open tasks`, `Lanes and
monitors`, `Inboxes`, `Lint findings`, `Root narrative`.
The `Inboxes` section starts with
`inbox-digest.py --state <drive>/inbox/.inbox-digest.json <drive>/inbox/*.md`
and the instruction never to tail, sed, or grep a whole inbox.

The script writes the same markdown to `<plan-stem>-progress/<UTC>-generated.md`.
Without cc-notes, `--folder` writes only that progress file. The hook caps generation
at 120 seconds.

Generation prints JSON `{id, file, register, digest}` with the active progress doc id
in `id` and the register doc id in `register`. `register` is null when no register
exists. Both ids are null in folder mode.

At the next main-session `Stop`, the hook runs `generate --strict --narrative-doc
<the root's new doc>`, or `--narrative-file` for the file fallback. The root's doc
becomes the last section, `## Root narrative`, under `_From doc <id>._`. A generated
doc is never taken as the root's narrative. With no fresh narrative, the newest
progress record's narrative carries forward with one provenance line.

The `root_context` Stop check `nudge_unrecorded_standing_rule`, described above,
prompts the root to record standing rules. Durable answers feed the key-moment judge.
The handoff reads the approved register and live `(standing)` inbox lines.

**Lint.** Generation checks that `## Standing owner rules` exists and quotes the
current register doc. It checks that inbox ids such as `R123` from the previous
handoff are carried or superseded. Durable answer titles are not required.
The owner-gate check skips register lines prefixed with `  >`, inbox quotes, tasks,
and asks. A narrative line or live `(standing)` inbox rule that gates on `owner's word`,
`owner approval`, `owner sign-off`, `owner GO`, or `reserved for the owner` needs a live
answer id; a missing citation is a finding. Each finding names its source: the inbox file and
line, or the narrative doc and line.

The Stop path runs with `--strict`. On a narrative or inbox finding it writes nothing and
blocks the `Stop` with each finding and its edit until fixed: end the line with
`(answer <id>)`, or for an inbox rule append a standing line that supersedes it and cites
the answer. The plan's own
owner-gate lines without live answer ids appear under `## Lint findings` and count in the
restore, but never block generation. `handoff.py lint` runs the same checks on any doc
or file, plus the plan when `--plan` is supplied, and exits 3 on any finding.

**Supersede and point.** Exactly one `progress:<slug>` doc is active. When the root
wrote a hand-written progress doc for the coming compaction, `generate` augments that
doc in place: it is the `--narrative-doc`, or the newest hand-written doc this session
created in the last 30 minutes or since the previous compaction (`--fresh-since`). Its
body becomes the generated sections with its own narrative under `_From doc <id>._`.
Otherwise `generate` edits the session's generated doc, passed as `--generated-doc`,
in place while it is active, or adds one. The doc it wrote supersedes every other
active `progress:<slug>` doc. If the label still lists another, `generate` exits 4
naming each id; the `Stop` path blocks on it, and `PreCompact` puts the failure first
in the compaction instructions.

*Prevents the release-v3 handoff of 2026-10-02 10:07 PM PT leaving hand-written dcef5bb
active beside generated 91b9194, which carried dcef5bb's body under fresher generated
sections, while the plan pointer and compaction instructions named dcef5bb. Owner
ruling: "the hook should augment the existing one if one was recently made for the
upcming compaxt".*

The hook adds one pointer line to the plan the first time. It starts with
`- **Progress (read first after any compaction):**` and names the label and generated
doc id, or the generated filename for the file fallback. Later handoffs change only
that id or name, always to the active doc. The handoff never restructures the plan. Let the hook do the
superseding and pointer edit.

**`/compact`.** Once the progress record and pointer are ready, the hook starts a
detached background job at that main-session `Stop` and lets the stop through. The
job waits through orca for the terminal to go idle, reads the screen, and types
`/compact` only when the draft is empty and the input line holds no typed text. It
rechecks every 30 seconds and gives up silently after 30 minutes. With
`ORCA_TERMINAL_HANDLE` unset, the hook sends the owner one message to run `/compact`
by hand and blocks nothing.

**`PreCompact`.** In the main session only, never a subagent's, `PreCompact` runs
`generate` again unless the hook generated a handoff in the last five minutes. It
reads the approved register, quotes it in the progress doc, and carries the narrative forward.
Generation never changes the register.
This covers Claude Code's auto-compaction before the root writes a narrative.

The instructions point to the plan and active progress doc. They ask the summary to
keep only in-flight details those records lack. They carry no register text or
title list. The copy bar caps hook messages at two sentences and 300 characters.
The instructions end with
`Quote: active progress doc: <id8>; the id in this summary wins over any id captured
earlier in the conversation.` so the summary carries the id the hook just wrote.

**Resume.** On `SessionStart` with source `compact`, the hook injects the digest
`generate` printed. When a register exists, the digest names it first with
`ccn doc show <register id>`. The register binds every lane brief and outranks the summary.

Read the progress doc next, then the plan.
Without a register, the digest starts with the progress record.
Reload Skill `long-running` if its rules are gone.

The second line reads `Register: N owner-approved rules, M live standing inbox rules.`
The `Open:` line counts owner asks, tasks, lanes, monitors, and lint findings.
The digest carries no clipped title list.

Claude Code's `PostCompact` hook cannot return context. Delivery after compaction
uses `SessionStart` with source `compact`. Source `resume` follows the same path.
The hook reads the newest register doc and queues its body for the next main-session
tool event. `PostToolUse` delivers it once, or `UserPromptSubmit` does if a prompt
comes first. Either event clears the pending delivery.

For bodies of at most 9,000 characters, the register arrives whole and verbatim after a short header.
The body sits inside identical 12-tilde fences spelled `~~~~~~~~~~~~`.
The copy bar exempts the fenced body.
Above 9,000 characters, the hook names the doc with `ccn doc show <id7>`
and tells the root to read it in full before acting. Delivery never splits the register
into parts or injects a batch of matched answers.

*Prevents the release-v3 loss of October 4, 2026: titles-only carry dropped the
ec2881e ruling "No: Pulumi state is the only truth" and left later lane briefs
without it (cc-notes note d07074b).*

Claude Code moves `SessionStart` context over 10,000 characters to a file and injects
a 2 KB preview. capt-hook merges every pack's `SessionStart` context into one output;
cc-notes' compact restores take 7,500 of it, split between 4,500 for answers and 3,000
for touched records. At `2026-10-01 13:55Z`, the root's merged restore was 20,596 bytes.
The long-running pointer, printed last, never reached context.

If generation failed, `SessionStart` gives the reason and asks the root to write the
progress record now. If the root wrote no narrative before compaction, it adds that
the root should write one when convenient. The skill stays active across compaction.

On `SessionStart` with source `resume`, an active drive's newest progress record, by
`ccn doc list --label progress:<program>` or the newest file in the progress folder,
is injected under the same 2,000-byte budget: a line naming `ccn doc show <id7>` and
the plan, then the head of the record's body.

### Lane rotation

Every turn a lane takes re-reads its whole history. A desk at 400k tokens pays about
that many tokens again per wake to type in a three-line report, while everything it
needs to continue already sits on its ledger. Rotation flushes a long-lived lane in
place. Claude Code's own compaction shrinks its context; the same lane resumes from
its ledger and cursor with its identity intact.

**Threshold.** A lane's context is its last assistant turn's input plus cache tokens.
A lane is due at 0.7 of its own compaction threshold, computed from its live model the
way Compaction handoff describes. On a 600k window, that lands around 400k tokens.
`LONG_RUNNING_LANE_ROTATE_TOKENS` sets the line outright. Once `long-running` is
invoked, the same capt-hook pack checks the lanes on every main-session `Stop`, and
never blocks it. The check reads only the root transcript's newest 256 events. Without that
window a hook reads the newest 4 MiB, and on a long drive that projection exceeds
capt-hook's snapshot output limit, so capt-hook skips the hook.

**Liveness.** A lane is live only while it appears as a running teammate or subagent in
the `Stop` payload's `background_tasks`. A subagent matches by id. Claude Code labels
an in-process teammate by its prompt's first 50 characters plus `...`, not by the
`description` in its meta, so a teammate matches on either one. Many lanes share one
label, so a label match only counts a running task and never names one.

A lane is finished when no `pending` or `in_progress` task in the root's task list
covers it. Coverage matches the lane name as task owner, `lane <name>` in the
subject or a name mention in the subject. A finished lane is never asked or
escalated. If it finishes after an ask, the hook drops the ask and records
`finished` in the rotation timeline.

A teammate must also still be on its team's roster,
`~/.claude/teams/<team>/config.json`; membership alone never counts. A live teammate
always reads its inbox, so a lane whose transcript has not moved in the 10 minutes
after an inbox ask is gone until it moves again. A lane whose newest turn is more than
an hour behind the root's is dormant. Its cache is cold, it costs nothing until it
wakes, and the hook skips it.

**Delivery.** The hook asks the lane itself, appending the request to the lane's
teammate inbox, `~/.claude/teams/<team>/inboxes/<name>.json`, in Claude Code's own
message format and under its lock:

`ROTATE: record anything not yet in the ledger or cc-notes, reply "flushed <ids>" to team-lead, then keep working.`

It asks at most three new lanes per 15 minutes, highest token count first. A lane
already asked is asked again every 30 minutes until it replies with a first line
starting `flushed`, with no cap. Re-asks do not count toward the limit for new lanes.

A lane with no teammate inbox is asked through the root. The hook queues a
``ROOT-ACTION `<lane>`: SendMessage it now ...`` line carrying the same `ROTATE` text
into the root's context on its next tool call or prompt. The root sends the request
with `SendMessage` to the lane's name in that turn.

**Handoff.** A reply starting `flushed` confirms the lane recorded its state and
keeps working; it ends the rotation cycle. The hook records the lane's token count
at the flush and asks again only after its context grows by at least 10% of its
rotation line from that count. The hook nudges the root once: the lane keeps
running in place, nothing to do. After its own compaction, the lane reads its ledger
and saved cursor and continues. Never `TaskStop` or respawn a flushed lane, and
never spawn a second agent under a live lane's name. An `open-pr:pr-watcher` resumes
from its state file after its own compaction.

A lane that drops below its line through its own compaction or leaves
`background_tasks` because it stopped or rotated also ends the cycle. Dropping below
the line clears its flush record, so a later crossing starts a fresh cycle.

A live, awake, unfinished lane over its line gets a `ROOT-ACTION` after 10 minutes
without a reply starting `flushed` to its first ask, subject to the cap below. A later
main-session `Stop` queues ``ROOT-ACTION `<lane>`: Rotate it by hand: ...``. This line
and the no-inbox `ROOT-ACTION` ask share a cap of one line per lane per two hours;
a new ask does not reset it. The escalation has three steps:

0. Spawn `<lane>-handoff` as a subagent from
   [reference/handoff-subagent-brief.md](reference/handoff-subagent-brief.md) to write
   a cc-notes doc. Pass the previous handoff's doc id, if any. Its reply is at most
   two lines: `handoff <lane>: doc <id>; successor <lane>-<N+1>` and
   `unverified: <n> items (in the doc)`.
1. Spawn `<lane>-N+1` from the old lane's brief plus that doc id, read with
   `ccn doc show <id>`, and the cursor it names. `alerts-watch` becomes
   `alerts-watch-2`; `desk-3` becomes `desk-4`.
2. Once the successor reports, send the old lane a stand-down with `SendMessage`
   to its name. The old lane stops working and sends nothing further.

Each firing replaces that lane's previous queued `ROOT-ACTION` line instead of
adding another. The root acts on it in the turn it arrives. The root sends the old
lane a stand-down only after the successor has reported. A reply starting `flushed`
cancels the rotation.

The same swap applies to a lane that died or outgrew its line before any ask. The
root never reconstructs a handoff inline. It never opens a lane's transcript, its
receipts, its cursor files, or a runtime listing such as `orca orchestration
task-list` for a rotation. The handoff subagent reads all of them in its own
context; the root holds only the doc id it returns.

The session's hook state directory holds `rotation_state.json` with a `timeline`
list. It records one entry per `ask`, `flushed`, `compacted`, `finished` or `gone`
event. An `ask` entry holds `via` as `inbox` or `root`, `tokens`, and `line`. A
`flushed` entry holds `ids`, the first line's tokens containing a digit. A
`compacted` entry holds `tokens`. An `escalate` entry holds its first `at`, latest
`last`, `count`, and `tokens`; consecutive escalations for the same lane update
that entry.

*Prevents stopping and respawning lanes from ending live sessions during the
release-v3 drive (2026-09-30).*

*Prevents alerts-watch sitting over its line from 07:02Z until the owner ordered its
rotation by hand (release-v3, 2026-10-01).*

*Prevents landing-desk-7 passing 500k tokens with no `ROOT-ACTION`: every rotation
check was skipped for exceeding the snapshot output limit on a 174 MB root transcript
(release-v3, 2026-10-02).*

*Prevents a `ROOT-ACTION` for ccx-guard-eperm naming four different task ids
across four firings, three of which stopped other lanes sharing its label
(release-v3, 2026-10-01).*

*Prevents the root rebuilding orca-desk's handoff inline, in about 15 tool calls
over its 44 MB transcript, spawn brief, cursor files, receipts, and Orca task list,
until the owner said it was polluting its context (release-v3, 2026-10-01).*

## Anti-patterns seen

- A desk enqueueing one stack per pass while other green, approved stacks waited,
  until the owner said the desk was too slow (2026-09-30).
- The root waiting on desk relays for priority PRs and owed items with no PR instead
  of dispatching them all and reading their state itself (2026-09-30).
- A desk-model change that lived only in the drive's scratch inbox files during
  release-v3, R137/R138, on 2026-09-30.
- The root relaunching 33 Orca lanes inline and answering scope questions their
  briefs already settled (release v3, 2026-09-30).
- Sending rulings by `SendMessage` to a looping desk: 26 sat undelivered for over an
  hour instead of reaching an inbox file read every minute (2026-09-30).
- Lanes running local rust builds and full suites in parallel, pushing the box to load 103
  until the 12:35Z mass kill (release-v3, 2026-09-30).
- Reading build and cloud logs in the root window while an assigned lane owned the question.
- The root reading PRs and diffs itself to answer questions until the owner said "stop
  polluting your main context by reading PRs etc" (release-v3, 2026-09-30).
- One Bash watcher per build or PR landing, each returning JSON parsed in the root window.
- Replying to every lane idle-notification, duplicates included.
- Restating status to the user after each event instead of at milestones.
- Waiting on a completion push from a lane parked at a confirm gate.
- Messaging a finished agent, which resumed its full brief and collided with a fresh lane.
- Messaging a running agent, which spawned a second copy that restarted the lane.
- Several ship lanes in one worktree: HEAD moved mid-run and a staged diff landed on the
  wrong branch, and a build scratch file was swept into a PR as a 63 MB blob.
- Merge loops run from a worktree another lane was editing, which raced an amend and
  dropped a fix.
- Red PRs nobody was tracking: eight of ninety open PRs were red or conflicting, and the
  owner found them before the desk did.
- A desk built from a repo-wide PR list: twenty routing comments landed on six other
  engineers' PRs before anyone asked whose they were.
- A head relabelled after every queue ejection: the same conflicting head was queued
  and dropped twelve times while its rebase sat unstarted.
- Stacks landed one PR at a time bottom-up, each waiting on a retarget and a fresh CI run.
- Hand-rolling the supersede chain or plan pointer instead of letting the
  compaction-handoff hook do both.
- Rewriting the plan at each handoff and losing items that still need action.
- A handoff that blocked the turn with every over-line lane: the root stopped about
  thirty lanes in nine seconds, none of them flushed, and sent ROTATE to seventeen
  more; the block returned seconds later, before any lane could flush.
- Every new owner ask appended to one busy lane's queue; ten asks sat unstarted for
  hours behind its four open PRs until the owner asked.
- An ask for "one post + one approval per release" appended to the busy release-fast lane's
  brief, then dropped for hours; without a PR, the ask had no ledger row.
- The root reporting PR state from its plan table while 148 of 405 PRs had no ledger
  row, including every PR opened in the last three hours.
- The desk grading with hand-rolled scripts and calling only `report`, `hold`, and
  `lift`; its last `refresh` was 28 hours old and its last report row three hours old.
- A desk running `label` only on its 20-minute pass, one report at a time; clean PRs
  waited and the owner enqueued one by hand.
- Five ready PRs from one lane sat unmerged for hours because two heads moved after
  the report and three were never reported, while the desk waited silently.
- The root told the owner a PR was queued when it had been on the base branch for
  15 minutes.
- The root relayed a lane's 45-60 minute ETA for green priority PR #25145 and the owner
  queued it by hand.
- A desk kept for the whole drive, re-reading hundreds of thousands of tokens of history
  on every report while its state already sat on the ledger.
- A claude Orca worker wrapping codex through the codex skill, which doubles the
  sessions and the context for one lane's work (release-v3, 2026-09-30).
- A lane backgrounded a codex subagent and ended its turn; completion went to the
  root session and the lane never woke. The "Use ccx for" ask for `AGENTS.md` died
  at `00:48Z`.
- A lane waiting on an OK that had already reached its inbox mid-poll, and another
  waiting on a verdict its author had withdrawn an hour earlier; both acted on the
  message, and neither read a record.
- Idle lanes that never saw their CI reds, and a root relaying every lane's heads and
  contracts by hand while a fable lane hunted two lanes' contradicting models.
- Status reported as "open" or "in CI" while stacks of real work sat on merge
  conflicts for more than a day.
- Six release cuts in one evening, each failing on the next bug whose fix was still
  open, and a root that override-approved one nobody asked it to touch.
- `STALLED-RED` lines printed and never sent: seven reds sat 30 to 100 minutes while
  their owning lanes were idle.
- Lanes building on the release ledger and the skew check while other lanes deleted
  both, with no lane holding the map of who depended on what.

## Checklist before every tool call

1. Could a lane return this as ≤10 lines? → delegate.
2. Is this raw data - log, JSON, resource list, webpage, diff, PR, Slack thread, plan artifact? → delegate to a reader lane.
3. Does a lane already own this question? → scoped resume, do not look.
4. Am I about to wait? → fold the wait into the lane that acts.
5. Am I about to restate status? → send nothing.
6. On each new owner ask, record it with `ledger.py ask` and dispatch its lane this turn; never queue it behind a busy lane.
7. Am I about to report a milestone? → first dispatch every owner ask still unstarted.
8. Before stating a PR's state, check `ccx vcs status` or the `(#N)` squash on a fetched trunk; never report it from the plan file.
9. Am I about to relay an ETA for a green PR? → check its gates and label it now if it is a priority PR.
10. Before saying something is assigned, read the summary's `LOST` lines first. Never call an ask done before `LIVE`.
11. Am I about to relay one lane's head, contract, or decision to another? → it goes on the bus, and the other lane reads it.
12. Am I about to create, relaunch, or answer an Orca lane's routine traffic myself? → issue `desk-runner.py relay` or `launch`; read its escalation file, never run a model desk or a second mailbox loop.
13. Before waiting on a desk relay for a priority PR or an owed item, read the PRs in one batched `ccx vcs pr status` call and dispatch every owed item with no PR now.
14. Did a tool just refuse, fall back to `# ccx:raw`, or need a step done by hand, or am I running the same command a third time? → spawn its tooling lane this turn (fix, PR, merge, release, install) and keep going.
15. Is a production alert active? This turn, `incident.py open` with the owner's grants and `incident.py run` in the background, before any verdict, depth mandate, or question. After that, answer only the executor's decisions and relay its `opened`, `live`, and `closed` milestones in Pacific time.
16. Did I just spawn an Agent lane, take an owner ask, or consume its deliverable? → `TaskCreate`/`TaskUpdate` this turn; a lane's word alone completes nothing. Runner actions use their action records under R5, never shadow tasks.
17. Am I about to ask the owner anything (AskUserQuestion, a board, a lane's question list)? → check each question against the plan's decisions, `ccn answer list --label scope:durable`, and memory first; apply what is settled and ask only the rest.
18. Am I about to swap a lane because it missed `ROTATE`, outgrew its line, or died? Spawn `<lane>-handoff` from `reference/handoff-subagent-brief.md`, take back only the doc id, then spawn the successor with that id and send the old lane a stand-down with `SendMessage` by name after the successor's first report; never open the lane's transcript, receipts, or runtime listings myself.
19. Before writing an inbox line, desk brief, or handoff with an owner rule, give each
    standing rule its own `R<n> (standing)` line under I6. Never mark it done.
    The approved `standing-rules:<slug>` register has at most 30 rules.
    Handoff generation only reads it and quotes it in the progress doc.
    Claude hooks inject it once after compaction or resume and once at sub-lane start.
    Above 9,000 characters, they name it with `ccn doc show` instead.
    Codex briefs paste the register body verbatim. Briefs never paste full answers.
    At key moments, the judge searches five durable answers outside the register's
    citations. It injects one relevant answer or none, once per lane per answer.
    The judge is advisory, never blocks, and fails open.
    List each standing inbox id, never a range.
    READY names the rulings the diff touches.
20. Did the owner just paste a Slack link, or am I about to react, reply, or write Slack copy? → spawn the Slack lane (`reference/slack-lane-brief.md`) and the doing lane this turn; the root never writes to Slack.
21. Am I about to reply to the owner or ask a question? → times in Pacific with no zone
    label; plain words, with no codename, inbox id, or answer id; a 'why did you…'
    answered in this turn in my own words; a delay I caused named as mine.
22. Before briefing, ruling on, or shipping a subsystem change, quote the relevant
    register rules verbatim. Cite their answer ids and the entry point as a symbol at `file:line`.
    Confirm the lane's design check against those rulings before launching any ship lane.
    Ship the whole design or hold.

Apply D3 to priority PRs before delegating. A call that survives all twenty-two decides
something no lane can decide for you; everything else is a lane.
