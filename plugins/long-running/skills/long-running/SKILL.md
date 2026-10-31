---
name: long-running
description: Hard rules for orchestrating multi-lane work without burning the orchestrator's context - routine ground truth arrives as a lane's verdict, anything with a body is a lane, every wait folds into the lane that acts, no lane parks and no lane is re-briefed, state lives in cc-notes and the task list, an open-PR ledger records every PR and owner ask, only the landing desk enqueues lanes' stacks, against a root-owned holds list, a landing-desk reconciles landings, desk-runner.py owns worker traffic and ready-prefix landing, each owner-named number-one outcome gets a priority desk, and a lane bus carries decisions, heads, contracts, blockers, and asks from each lane's cursor. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs at once, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
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
production; `drive-watchdog`, briefed from `reference/drive-watchdog-brief.md`,
once the drive has a plan; `red-sweep`, briefed from `reference/red-sweep-brief.md`,
once the drive has open PRs; and one priority desk per owner-named #1-priority outcome
while that outcome is open. The drive dashboard app runs from the drive's first plan,
kept by `dashboard-keeper`, and a `user-test` lane plays the owner against every
revision of a surface he will use (see Drive dashboard). No lane or root watches Slack
(owner answer d94db5b). No comms lane
stands: the root spawns `comms-desk` from `reference/comms-desk-brief.md` once per post
the owner gave word for, and it ends after that post. The orca desk is the
`desk-runner.py run --desk orca` process, not a subagent. Its landing process, the drive's merge queue, shares the store and runs D3, D14, D16, and D20.
The root spawns a subagent for every task with a body, including its own routine work.
A lane never spawns one with `Agent`, since a teammate cannot background the call: it writes the brief to a file, posts `cci post --kind ask --to root --text 'spawn <name> brief=<absolute path> model=<opus or sonnet>'`, and keeps working, and the `lanes_never_spawn` hook refuses the call.

This skill is the context-discipline layer over `~/.claude/CLAUDE.md`. Fan-out shape comes
from §Parallelize Independent Work, lane behavior from §Delegation, per-lane model and
effort from §Model Routing, and depth of checking from §Verification Budget. None of
that is repeated here.

## Judgment first

Read this before the rules, and read the rules through it.

- The rules exist to protect two things: the root's context and the deliverable. Apply
  each one for that purpose, never as a literal script.
- No rule forbids reading the one line a decision turns on. When a decision rests on one
  line of one file, the root reads that line and decides.
- A finding in hand is a fix. Apply it directly; it does not become a lane, a re-audit,
  or a workflow.
- A workflow is for many independent units the owner asked to be thorough about. Small
  or known work runs direct.
- Read each owner sentence as a careful colleague would, in the context of what it is
  about, and act on what it means rather than on the narrowest parse of its words.
- When the owner repeats something, treat the repeat as a defect report about how the
  root read it the first time. Find every place the earlier reading survived and fix them.
- When tooling refuses a legitimate action, fix the tool forward in a side lane in the
  same turn and keep going. A refusal is never an excuse, and never a question for the
  owner.
- The pack's hooks warn; they block only on trust: a merge without the owner's approval,
  a revert or rollback without his word, hook-file edits, and the session-protection
  guards. A warning is advice to weigh, not a gate to satisfy.

On an owner prompt, when 30 minutes have passed since the last one, the pack re-injects
these rules with two of the owner's sentences. Set `judgment_reminder_minutes` in the
drive's config to change the interval.

## The twenty-four hard rules

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
pass it. It warns, and never blocks, on:
- A Read whose window passes 150 lines.
- A Read of a lane artifact: anything under `audits/`, `briefs/`, `handoffs/`,
  `tool-results/`, `subagents/`, or `transcripts/`, a `matrix.md`, or a `.jsonl` transcript.
- A search with `rg` or `ag`, and `cat`, `head`, `tail`, `sed`, `awk`, `grep`, or `jq`
  over a file outside an `inbox/` directory.
- A Read, or `cat`, `head`, `tail`, `sed`, `awk`, or `grep` except `grep -c`, over
  `inbox/*.md`, even with a bounded window; use
  `cci digest --drive <drive>` and `cci tail --drive <drive> --cursor root`.
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
One bounded read of one named file outside `inbox/` passes too: a Read window, `head -n`,
`tail -n`, `sed -n 'A,Bp'`, `ccx code read <file> --section A-B`, or the MCP `ccx_code_read`
with such a section, each up to 150 lines; `head -c` up to 4000 bytes; `grep -c`, `grep -m`
up to 20, or `wc`; or a read piped into such a `head`, `tail`, or `sed`. Reading the one
source or test file a decision turns on is the root's own work.
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
original never sees the message. Reply to nothing you have already acted on. An idle
notification answers only the messages the lane had when that turn began, so one
that lands seconds after a new order says nothing about it; wait for the lane's
report on that order. desk-wait marks the mail it prints as read, so the harness
never hands it to the lane a second time. Rotation flushes the lane in place;
it keeps working and resumes from its ledger and cursor after its own compaction
(see Lane rotation). *Prevents lane collisions in a shared worktree
and the reply tax on notifications that carry no news.*

**R5. Write it down, do not hold it.** Findings go to cc-notes from the lane that found
them, never into the orchestrator's window. Lanes carry no `mcp__*` tools beyond
cc-slack's, so they write with `ccn log append`, `ccn investigation open` and `append`, `ccn note add`,
`ccn doc add`, and `ccn task add`. The root may use the `mcp__plugin_cc-notes_*` tools.

Drive records live in cc-notes on the drive checkout. Pass record ids to lanes:

- Incidents use an investigation and log through `incident.py`.
- Handoffs use a doc; follow
  [reference/handoff-subagent-brief.md](reference/handoff-subagent-brief.md).
  Every handoff carries the outgoing session's `cci me` line.
- Reports and audits use a note for a verdict or finding, or a doc with a
  read-before trigger.
- Lane briefs are attachments on one `briefs: <slug>` log; use `--replace` on edit.
  A tool that needs a file reads `ccn attachment path <id> <name>`.

Never run `mkdir` or `cat >` to create a markdown record under `~/.claude/scratch`.
Desk inboxes (`inbox/*.md`) stay files because readers use `inboxes.py`'s stream:
sorted `<file>.archive/YYYY-MM-DD.md` files, oldest first, then the live file.
Byte offsets and line counts survive rotation. `standing.py` reads rules from cci,
and `desk-runner.py` posts root escalations there.

The orca-waiter/Monitor streams, orca-launch receipts, and desk state stay files for
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

Report to the user on milestones or when they must act, never per event.

Every milestone report and every status answer is the owner report table,
`reference/owner-report-table.md`: one row for each plan step, named as the work in
plain words, with the columns Step, What it delivers, State, What blocks it, What we
are doing to unblock it, and What went wrong or slowed it so far. The last column is
a mini retro on every row of every update, fed by the drive-watchdog's retro log, and
is never blank. Every READY-HELD row names its gate and why that order, readers
first or a data gate, in one sentence. Ids follow the plain words, at most one per
cell. The plan carries the format and one filled example; the live rows sit in one
cc-notes doc labelled `owner-table`, which the root edits at each update. *Prevents
status answers that list PR numbers and record ids the owner cannot map to the plan,
and holds nobody can justify. The owner ruled on 2026-10-16: "make it in terms of
the original plan and actual work to be done not just a bunch of random
references".*

Once
`long-running` is invoked, the compaction hook nudges the root to write a new progress
doc, then handles superseding, the plan pointer, and `/compact`, as Compaction handoff
describes. The plan stays the drive's mandate and decisions. *Prevents the forced
mid-drive handoff with nothing written down to hand over.*

The pack's `task_list` hooks enforce this. They nudge when a spawned lane has no task by
turn end, when a lane's done report names a task still `in_progress`, and when an owner
ask has no task after three tool calls or by turn end. The owner-ask nudge is dropped
when a `TaskCreate` happened in the last 10 minutes, and fires at most once per hour. Every 20 turns they check for drift
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

A lane whose progress is not a PR, such as Slack posts or register entries, records
each step with `ledger.py progress --ledger <id> --ask <id> --note "<what moved>" [--artifact <url or id>]...`.
The ask then reads `IN-PROGRESS` until 30 minutes pass with no new entry, and the
summary and `show --asks` print its latest entry.

Before claiming anything is assigned, in flight, or done, the root reads
`ledger.py summary --drive <drive>` or `ledger.py show --asks`, never its own plan table. R7 still
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
and `launch` commands. It receives cci records over the cc-inbox plugin's `cci`
channel, with no Monitor. Launch the root with
`--channels plugin:cc-inbox@cc-inbox` alongside every other channel plugin the drive
uses, and at drive start run once:

```bash
cci subscribe --drive <drive> --reader root --kind incident,decide,ask,defect,blocker
```

Records addressed to `root` or `main` arrive regardless of kind, along with other
lanes' broadcasts of each `--kind`. Each record arrives as a
`<channel source="plugin:cc-inbox:cci">` tag whose body is
`#<seq> <time> <KIND> <lane> -> <to>: <text> [path]`. Skip a seq you have already
handled. The subscription belongs to the Claude Code window. Compaction and `/clear`
keep it, and the cc-inbox `SessionStart` hook moves it into a resumed session.
Re-run `cci subscribe` to change kinds. Never arm a Monitor on `cci watch` for
the root; a root switching over stops its Monitor after subscribing, and records
in the overlap arrive twice. The plugin ships no `inbox-watch.py`.
A desk watches its own records with
`cci watch --drive <drive> --cursor <desk> --to <desk>`; `--reader <desk>` also
delivers every lane's broadcasts, so a desk that acts only on its own records
passes `--to`. A root launched without the flag receives no tags,
and managed settings must list every plugin the flag names in
`allowedChannelPlugins`.

After compaction, a resume, or a relaunch, catch up with
`cci digest --drive <drive>`, then `cci tail --drive <drive> --cursor root`.
The tail resumes from the root's cursor and ends with a resume trailer when capped.
Never tail, sed, or grep whole inbox files. A new lane orients with
`cci digest --drive <drive>`.

The root never runs the check/ack loop, relaunch sweeps, or helper scripts inline.
No model desk sits between the root and Orca.

Start the orca runner before the first worker; set `drive` in its config for cci
escalations to root and `orca.desk_inbox` to the drive's `inbox/orca-desk.md`, which
anchors the runner's logs and state. The runner reads its orders only from cci records
addressed to `orca-desk`, which the root posts with `desk-launch.py`; it never reads that file. Use `policy` for a landing
rule and `show` for action state. Upgrade a live runner with `handoff`: it waits for
the old runner to finish its pass and exit, then starts the new one itself.
[The runner brief](reference/orca-desk-brief.md) gives the config and cutover.

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
posts `orca-desk: hold <slug> owner=<lane> :: <what is held, and on what>` with
`desk-launch.py hold`. It names itself or a dedicated decision-owner lane as owner.
A held or blocked coupled release gets its own decision-owner lane. When the
release moves, the holding lane appends `orca-desk: unhold <slug>`.

After `deadlines.hold_minutes` (default 15), the runner writes one decision:

```text
DECIDE hold:<slug> <owner>: held <n> min: <what> | the root decides it now with <owner>, ahead of any open owner question on another subject
```

The root resolves it with that lane ahead of any open owner card on another
subject; that card keeps waiting for its tap. The line reaches the root over the `cci` channel as
soon as the runner writes it.

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

Only a tooling lane installs a cci or long-running release under a live drive, and only
once `drive.py digest --quiet-point` exits 0, so no runner pass or merge runs through
the swap.

A lesson is a process fix. When the root, a lane, or a retro names a lesson, a rule,
or a "fix for next time", the skill or tool PR that encodes it opens in that same turn,
from a lane spawned for it, unprompted. A lesson that lives in a status, a memory file,
or a retro card alone is not landed. A patch made under incident pressure is recorded
as a reconcile item the turn it ships, and the drive replaces it with the fundamental
fix as its own lane, never as backlog.

*Prevents the release-v3 R137 and R138 rulings of 2026-09-30 living only in the
drive's inbox files until this skill change. Also prevents 2026-10-12 and 2026-10-13,
when lessons named in reports waited for the owner to ask: "you keep saying concrete
fixes for next time but not once have you opened a skill PR without me prompting", and
"clean up all the shitty bandaids you did today into a proper set of fundamental
fixes".* *Also prevents the owner asking three
times on 2026-10-01 for tool fixes the root had worked around: the ccx submodule
refusal, Orca message delivery, and the ledger gap.*

**R15. CI is the verifier, never the box.**
Verification: no local test runs, builds, typechecks or lint; CI grades; a red CI step is read from its log, fixed, and pushed.
That holds for one failing test and a single artifact as much as for a whole package.
Machine load never holds a Buildkite build, a hand apply of a saved plan, an
incident step, or an owner-directed step; those run at once.

*Prevents release-v3 lanes pushing the box to load 103 with local rust builds on
2026-09-30, until the 12:35Z mass kill, and apply-restate-1255 holding an
owner-ordered Buildkite apply on load 428 until the root answered (2026-10-02).*
*Also prevents lanes reproducing red CI with one local test after the owner withdrew
that allowance on 2026-10-13 (answer 7e82747, register F341) and ordered at 2:42 PM
"no local compiles anywhere; CI grades" (F348).*

A wrapper that sends builds or checks to remote compute refuses when no remote target
answers; it never falls through to the local toolchain. A lane that sees work it sent
remote run on the Mac stops it and posts a `defect` to root naming the wrapper and the
command, and root spawns the fix that turn. CI grades the work meanwhile.

*Prevents Oct 27 12:27 AM: a remote-check wrapper handed a check to the local compiler
when no builder answered, and a lane compiled on the Mac while believing the work ran
remotely.*

A batch job never runs on the orchestrator's machine. A data pass, a backfill, a dry
run of one, and a bulk rewrite run on in-region compute, one process per bucket or
team, and the Mac runs only the root, its desks, and its lanes. That holds for a
container on the Mac, a "small" pass, and a brief that says "run it from this Mac";
the lane names the box it will use before it starts and refuses to start on the Mac.
A one-shot read, such as one run's dry run or a metadata listing, is not a batch job.
Every pass brief names its compute: the box recipe, the region, and the bucket or
team each process owns.

*Prevents the L15 journal-rewrite write passes of 2026-10-13. HSBC, AIG, and a Demo
Team container ran on the Mac, drove its load to 85-105 on 32 CPUs, and timed out
every Orca worktree checkout ("index still differs from HEAD after 180s"), so four
incident lanes failed to launch. The Mac's link caused most of the pass's own
failures too. Owner: "never fucking run these locally, wtf is wrong with you, always
run it in ec2, make sure to never fuck that up again" and "get rid of all local batch
jobs ... run those on ec2".*

**R16. An active production alert gets its executor in the same turn.** An active
alert or outage on any surface counts: a Datadog monitor, a Sentry alert, an
`#outage` report, an on-call page, a broken CI or review pipeline, or an alert or
breakage link from the owner. It is P0 from the moment it is seen. That turn,
before anything else, the root runs `incident.py open` and starts `incident.py run`
as a background command (`reference/active-alert-brief.md`). From then on the
executor owns the incident, through the final reply:

- It posts the fence and launches the fix and evidence lanes through
  `desk-launch.py incident <name> <fix-brief> <evidence-brief>` as Orca codex workers on
  `gpt-6.1-sol`, fast tier, `xhigh`; plain `sol` is the standard tier and never
  an incident route. With no mechanism after 15 minutes, it adds an Opus 5.5
  backup lane (`claude-opus-5-5`) in Claude fast mode and keeps the first running.
  It adds none while the desk clock's `<lane>-opus` for the same fix lane is
  launching or live. It refuses Claude for the fix and
  evidence roles and never uses fable or astra.
- It posts each comms event (ack, PR, review request, landing, live, final reply)
  to the comms lane on the bus with that event's grant id. It confirms each
  posted ts from the lane's bus answer.
- It holds a landed fix at `activation_pending` until the live configuration reads
  back as the landed tree. It runs a canary, rebuilds each failed head of the outage
  window once, and accounts for every rebuild before it reports `recovered`.
- It records every side effect in the `scripts/actions.py` store before running it,
  so a restart or a duplicate delivery never repeats one. A lost response stays
  `unverifiable` until a read of external state settles it.
- It fixes every incident forward. No lane chooses a rollback or a revert: one runs
  only when the owner's own word names that act, after the diagnosis has found the
  issue (owner answers 36a298d and 91150e7). The never-cancel-a-running-apply rule holds.

When the root launches an incident pair itself, the launch is one command:

```sh
desk-launch.py incident <name> <fix-brief> [<evidence-brief>]
```

It takes the next `R<n>`, one past every key on cci, posts
`<name>-fix` and `<name>-evidence` to `orca-desk`
on cci as `NOW incident xhigh` launch lines with `role=fix` and `role=evidence`, so
the runner's incident clock holds only the fix lane, waits for the runner's `LAUNCHED` or
`LAUNCH-FAILED`, and prints each lane's dispatch and terminal. It exits 0 only when
both launched. On a drive whose orca runner posted no `orca alive` record in the last five minutes it posts nothing and refuses
in one line naming the Agent to spawn instead, `<name>-fix`. `incident.py open ... --adopt fix=<name>-fix --adopt
evidence=<name>-evidence` hands both lanes to the executor. A codex lane writes its
PR title, body, and commit message with `pr-prose.py`, a headless Opus 5.5 writer
that `orca-launch.sh` names in its brief, so it never asks the root for prose. The
root answers a lane's question with `cci post --kind answer --to <lane> --re <seq>`
on the lane's ask or on the runner's `DECIDE`. The runner relays that answer as the
reply to the lane's open Orca question, so no question is left open. It withdraws
a launch or a lane with `desk-launch.py withdraw <R<n> | unfixed:<msg>:<n> | lane>`.

*Prevents the 11:49 PM Oct 11 incident launch: two unaddressed launch lines, a
withdrawal the runner rejected, a codex lane that asked the root for PR prose, and
a stale Orca question answered by hand.*

Before that launch, the root reads the orca runner's newest alive record,
`cci tail --drive <drive> --lane desk-runner --kind state --topic orca -n 1`. One under
five minutes old means the runner takes the line, however long its current pass runs,
since a timer thread posts it every minute. None that recent, which `desk-launch.py` and the
`desk_liveness` hook report as `LAUNCH-FAILED`, means the root launches the fix lane inline in the same
turn instead of waiting for the runner: a background `codex:codex-wrapper` running
`codex-ask --incident` on the fix brief. Every incident fix brief says build, ship
via submit-pr, and post `ready`. Incident fixes land by plain squash the pass they
are approved and green (ef905a6): the landing runner merges the PR with
`gh pr merge <n> --squash`, never through the queue. Codex cannot run commit
mechanics, so the wrapper hands its branch to a Claude `lane-ship` lane named
`incident-<slug>` that ships it; the pack's `lane_landing` hook refuses that lane's
`stack-enqueue` and names the plain squash. Never both: when the runner comes back
and launches the same line, the root keeps the lane further along and stops the
other.

*Prevents the 4:45 AM Oct 12 incident launch: R2030 hit `LAUNCH-FAILED: orca desk
runner not running`, the inline codex fallback stopped for an owner choice instead
of building, the restarted runner launched the same lane at 4:48, and two lanes ran
on one incident while the fix lost about 5 minutes.*

The inline launch needs the rendered briefs, and only the executor writes them.
When the desk refuses `incident.py run`, on load or otherwise, the root runs
`incident.py brief --incident <id>`. It writes `incident-<id>-fix.full.md` and
`incident-<id>-evidence.full.md` beside the incident record, the same files `run`
would launch, and prints their paths; `--role fix` writes one. Each goes to
`codex-ask --incident -l incident-<id>-<role> <brief>`, so the lane's records and
reply file carry its name.

*Prevents the Oct 14 plat-sandsql-6644 and aig-below-floor launches: `run` was
refused on load above 32 cores, `open` had rendered no brief, and both briefs were
written by hand without the fixed lines.*

Alert intake runs before the root wakes. A monitor transition never launches a
lane by itself: `monitor-watch.py --alert-drive` posts `orca-desk: alert <slug>
<link> :: <what fired>` to `orca-desk` on cci, which the runner records and launches nothing on. The
alerts desk posts `orca-desk: incident <slug> <link> reported=<HH:MM> :: <what
fired>` the same way only when it judges a lane necessary. `reported` is the first report's
Pacific time; the brief names it and the 20-minute bar runs from it. For an `incident` line the runner
fills `reference/alert-fix-brief.md` with drive facts from `alert.facts`,
attaches it to the briefs log as
`<slug>-fix.full.md`, and launches `<slug>-fix` on incident xhigh at once; a repeat
relays to the live lane. The root ratifies from `INCIDENT` in that turn:

1. Adopt the launched fix lane into `incident.py` with `--adopt fix=<lane>`.
2. Start or adopt the evidence lane. Beside it, spawn an incident-doc lane as
   `long-running:lane` on opus, named `incident-<id>-retro`.
3. Claim the target for the fix lane:
   `tools/ci release claim <component>/<env>... --purpose incident --reason <alert> --until <time>`.
   Every other release then stops before its next apply there, and no
   `--emergency` or hotfix goes through it. Whoever ships the fix holds the
   claim; hand it to them with `tools/ci release transfer <id> --to <email>`,
   and give it back with `tools/ci release lift <id>` once the alert clears. A
   claim covers only the stacks that are breaking. Never claim a target because a
   PR waits on review, a person's or a bot's, and never claim everyone over a
   dependency your own change created: a change that must ship with another
   target waits in its own stack. On Oct 14 a `schema-add` freeze set while a PR
   waited on review refused every release that pulled `schema-add` in.
4. Start comms in the affected account channels first, before the mechanism is
   known, under cc-notes answer `5ad4507`. A platform-wide incident also opens
   in `#outage` under `52f4863`.

Every incident fix lane the runner launched is on a 20-minute clock from its
verified launch, with 10 minutes to a mechanism, 15 to an opened PR, and 20 to
`ready`. The runner reads the lane's own cci records and its `mechanism:`, `ready:`, and
`fix-live:` statuses. Past the mechanism or the PR limit it launches `<lane>-opus` itself, on
opus xhigh in Claude fast mode, on the lane's own brief under a fallback header.
Each missed limit posts one blocker to the root:

```text
INCIDENT-CLOCK <lane> <mechanism|pr|ready>: <what is missing, the minutes each phase took, and what the runner launched>
```

The root reports each `INCIDENT-CLOCK` line to the owner in one plain line of its next
reply, ahead of any other work in that turn, never as a Slack post, and ratifies the
fallback the line names. Both lanes keep running, and the first PR wins. A fix lane gets one Opus fallback. When the incident executor
adopted the lane, its 15-minute backup and the clock's `<lane>-opus` each stand
down while the other is launching or live.
When the Orca launch of `<lane>-opus` fails, on load or otherwise, the runner launches
it once more. When that fails too, it posts `INCIDENT-CLOCK <lane> in-process` naming
both failures and the fallback brief. The root starts `<lane>-opus` that turn in its
own session, an `Agent` of type `long-running:lane-ship` on opus in fast mode with that
brief, and never retries Orca a third time.
`ready`, `fix-live`, `not-ours`, `stopped`, `stand-down`, or a settled dispatch
stops the clock. Three rules keep the clock to the incident, not the lane:

- Milestones count across the incident. A clocked fix lane sharing the lane's
  incident name or `incident=` slug, such as `inc-disk-cred` beside `inc-disk-fix`,
  a `-case<n>` sibling, or the `-opus` fallback, and the incident executor's Opus
  backup for one of them are siblings. The incident name is the lane's name less
  `-opus`, `-case<n>`, and its last `-<part>`; a two-part name such as `api-fix`
  is its own.
  One mechanism, PR, or ready anywhere among them counts as the lane's mechanism,
  so no mechanism fallback launches; the PR threshold still clocks the lane that
  has no PR.
- A clock whose lane the root withdrew, or sent `stand-down` or `stopped`, ends as
  `withdrawn by <who>` and posts no `INCIDENT-CLOCK` line.
- While any incident clock runs, `DEADLINE`, `UNACKED`, `LIVENESS`,
  `STALE-MAIL`, and `RECLAIM` escalations about anything outside the incident go to
  the runner's view only. When the last clock ends they reach the root as one
  `DEFERRED <n> escalations held during <incident>` record naming each.

A second clock runs from each incident fix lane's mechanism, for in-process lanes
too: any lane named `inc-`, `incident-`, or `outage-`, other than an `-evidence`
lane, and every fix lane the runner launched. It fires only for a lane that owns a
worktree or owes a PR, by its launch or by a `-fix`, `-fix-<n>`, `-opus`, or
`-case<n>` name, so an evidence or closeout lane never fires it. A lane whose name or
brief names an evidence, proof, closeout, desk, or audit role never fires it either.
A PR from any lane for the same incident answers the mechanism. That PR names the
mechanism's seq with `--re`, shares its topic, or carries one of its incident tags,
such as `SAND-5A`. A root `go` that does the same answers it too, and so do a root
`decide` or `decision` addressed to the lane and the lane's own `done`.

Ten minutes after a `MECHANISM` or
`CANDIDATE` record with no `OPENED` or `BLOCKER` from the lane or its `-opus` pair,
the runner posts a blocker the incident never defers, and posts it again every five
minutes:

```text
PR-SILENT <lane> #<seq>: MECHANISM at 21:11, 10 min with no OPENED or BLOCKER record; <path> (git worktree list): HEAD (no branch), 0 dirty files, 0 ahead of trunk, last commit 130 min ago <sha> <subject>, not pushed, no PR | <lane>: ...
```

The code state in the line is `drive.py lane-status <lane>`, the root's one verb for
what code a lane holds:

```bash
drive.py lane-status <lane>
```

It finds every worktree the lane owns, by its launch receipt, its `WORKTREE` and other
cci records, the checkout's `git worktree list`, and its PRs' head branches. Each line
names the branch, dirty files, commits ahead of trunk, the last commit's age, whether
the origin branch holds HEAD, and the PR with its CI state. A lane with no worktree on
this Mac, such as an Orca worker on a paired remote server, reads from the drive's Orca
Run instead, or `--run <run>`: one line per dispatch whose task title, display name, or
spec names the lane, with its worktree, environment, branch, stage, outcome, liveness,
last update and heartbeat, terminal, and last message to the Run. A `ctx_` dispatch id
works as the lane. A lane that is an in-process teammate of the drive's root session
also gets a line naming its agent type, model, whether it runs or sits idle, its last
activity, and when it joined. With none it prints `no worktree found for lane <lane>`
and exits 1.
A worktree named after its branch
still resolves, because the pack's `announce_lane_worktree` hook posts
`WORKTREE <lane> <path> <branch>` the first time a lane runs a command inside a
worktree of the drive's repository. `desk-runner.py show` lists each waiting lane as
`pr-silent <lane>: ...` with the same command.

The root reads the code state in the line instead of checking by hand. It
orders the lane to push and open the PR, or starts `<lane>-opus` when the line
shows no code. A root `--resolves` on the line, or a `stand-down` or `stopped` to
the lane, ends the repeats. The lane side holds the same line: the pack's
`incident_lane_ships_its_mechanism` gate refuses an in-process incident lane's
stop while its newest mechanism has no `opened` or `blocker` after it and
`drive.py lane-status` shows no pushed branch. It prints that status and the
commit, push, and submit commands to run, and passes a lane with no worktree
that owes no PR by name.

`deadlines.incident_mechanism_minutes`,
`deadlines.incident_pr_minutes`, and `deadlines.incident_ready_minutes` set the
limits; [the runner brief](reference/orca-desk-brief.md) has the rules.

Every incident fix lane aims to be fix-live within 20 minutes of its launch, and the
root treats a lane past that as a miss to act on in that turn, never as normal.

*Owner, 2026-10-13: "you need to not fuck around during incidents and get them fixed in
20 mins max, always".*

*Prevents the seven incidents of 2026-10-13, which ran from 57 minutes to over four
hours. Lanes went more than 15 minutes with no mechanism and nobody was told, and
the Opus fallback was started by hand an hour late.*

A case the fix does not cover is an incident lane, never backlog. Both fix briefs
make the lane reproduce the reporter's own case before it reports ready. That case
is the exact thread, run, request, or monitor query the report named. It has to
fail before the fix, pass on the build that carries the fix, and pass again on the
released build before `fix-live`. A fix proven only on a synthetic or neighbouring
case is not ready, and the root never tells a reporter "fixed" on one.

The lane closes its `ready:` and `fix-live:` statuses with one
`UNFIXED <lane>: <case>` line per known case the fix does not cover, or
`UNFIXED <lane>: none`. Only those partial-fix reports launch case lanes; a
`worker_done`, `NOT-OURS`, or `STOPPED` launches none. For each case the runner
launches `<first>-case<n>` on incident xhigh in the same pass, where `<first>` is the
first fix lane of the incident, on a brief filled from
`reference/unfixed-case-brief.md`. It logs the launch as `INCIDENT <lane> …: its fix
does not cover: <case> | case lane <first>-case<n> launching …`. A case launches once,
whether the fix lane or its `-opus` fallback reports it.

An incident launches at most `incident.case_lanes` case lanes, two by default. The
rest of a report's new cases reach the root as one `DECIDE` that carries a ready
`orca-desk: launch <first>-case<n> NOW incident xhigh brief=<path>` line per case, so
the root starts a lane by posting its line. The same `DECIDE` carries a case lane's
new cases, since case lanes are one generation, and every case of a report whose
lane's report or newest mechanism says `UNKNOWN`. A case lane echoing its own case
launches nothing. A `not-ours` or `stand-down` from the fix lane or its fallback, or
the root's `stand-down` or `stopped` to either, ends the incident. A report that opens
with `NOT-OURS`, `stood down`, or `Stopped` launches no case lane. The runner launches
no fix lane for an incident the root resolved with a `done`, stopped by name, or another
drive's root answered for, and posts `INCIDENT-OWNED` as a `state` record instead. A later report launches nothing
and reaches the root as `UNFIXED <lane> …: <cases> | no case lane launched: …`, and a
case launch not yet started fails. A report from a lane the root withdrew does the
same. The root ratifies the case lane and keeps the reporter told that the case is
still open. It never parks a named case for later while the break is live.

*Prevents the resume fix of 2026-10-13. It was reported fixed after a test on a
neighbouring case while the reporter's own thread still failed, and two edge cases
the fix lane had named sat as backlog for two hours until a customer hit them.*

*Case lanes stay one generation because of the 10:37 AM Oct 14 loop. The runner
refused the root's withdrawals of five case lanes, which carried no `R<n>`. The
withdrawn case5 then echoed its own `UNFIXED` line, and the runner launched case6
from it. A new case still reaches the root with its launch line ready, so the cap
drops none (fidelity F340).*

*The per-incident cap, the end, and the `UNKNOWN` gate come from 3:43 AM Oct 16. A
fix lane posted `NOT-OURS` after the root's stand-down, and its `worker_done` listed
ten original 524s as `UNFIXED`; the runner launched ten case lanes from it.*

**Another drive's stalled incident fix is taken at once.** When another drive owns an
incident's fix, through the runner's `INCIDENT-OWNED` or a fix PR another drive's lane
opened, the root spawns `incident-<slug>-watch` as `long-running:lane` on sonnet,
effort low, in that turn. It runs one foreground watch on the owning drive:

```sh
cci watch --drive <owning drive> --lane <its fix lane> --lane desk-runner --kind stopped --kind blocker --kind decide
```

The fix lane's `stopped` or `blocker` is the trigger at once. A `decide` from that
drive's runner naming the fix lane is the trigger once five minutes pass with no `answer`
from that drive's root replying to it. On the trigger the watch lane posts
`cci post --drive <drive> --lane incident-<slug>-watch --kind blocker --to root --pr <n> --text "TAKEOVER #<n>: <owning drive> <kind> #<seq> <first line>"`
and finishes.

The turn the `TAKEOVER` arrives, the root spawns `incident-<slug>-ship` as
`long-running:lane-ship` on opus in fast mode. It checks out the PR's branch with
`ccx vcs worktree add <branch>` and replays it onto current dev with
`ccx vcs stack submit --restack`. It posts `opened --pr <n>` on this drive so the
landing runner owns the PR. Incident fixes land by plain squash the pass they are
approved and green (ef905a6); the ship lane posts `ready` and the landing runner
merges it. On landing it posts the `release` record D20 needs for the cut. The root posts one
`decision` addressed to the owning drive's root and fix lane, naming the takeover and
its trigger. It never sets the owning drive a deadline, never asks whether it is
mid-push, and never waits for its root to rule. A head the owning drive pushes later
is the PR's newest head; the ship lane continues from it and never force-pushes over it.

*Prevents 14 minutes of Oct 16. The owning drive's fix lane for #39264 posted
`stopped` at 2:02 PM and its runner's `DECIDE` sat unruled until 2:22. release-v3
asked that drive to push at 2:07 and set it a 2:20 deadline, and restacked the PR
itself only at 2:21, while kate and erik reported Iris disconnects.*

The incident-doc lane runs the incident-retro skill's live mode in the
Forge-AI/design-docs checkout: `live init`, then `live sync` at every state change
and at least every 10 minutes. The shell PR merges first; the page at
`https://docs.poetic.design/incident-retros/<date>-<slug>/` polls
`live/<date>-<slug>`. The lane hands its link to comms for `#outage` and the
account channels.

Each retro scaffold names the drive's records as sources: the incident's cc-notes
investigation and log ids, the owner answers and notes it turned on, and one
`--incident "cci:<tenant>|<customer>|<lane regex>"` query naming the incident's
tenant, customer, and lanes. The lane writes incident records as cc-notes
investigations, logs, docs, or notes through `ccn`, never as loose Markdown under
`~/.claude/scratch`.

At resolution, it runs `live finalize`, completes the retro
with Remediation and the owner's prevention picks on `retro/<date>-<slug>`,
and runs `retro.py publish <dir>`. It waits through merge and the rendered-URL
check, resuming the printed `AWAIT:` command when needed. It hands comms the
URL from the skill's final `RENDERED:` line. The incident's step 4 report and
the retro-link reply in Slack use that URL, never a PR URL. Every such draft
passes `retro.py comms-check <draft-file|-> --url <rendered-url>` before posting.

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
Both "use Orca incident for incident response" and "pass the fast tier flag" count.
Stand down every live lane on that problem and relaunch on the named route.
Never record it for "new lanes" only or weigh it against the routing table.
Never keep a lane because it is "already deep in the code" or defer the ruling to
the lane editing the skill.

*Prevents the 2026-10-01 instruction at `04:14:42Z` taking until `04:21:03Z` to apply,
with three lanes and two PRs (#28594, #28598) for one fix.*

**R18. A ruling reaches a lane the way it reads.** Orca rulings use
`desk-launch.py relay <lane>[,<lane>...] <text>`; launches use `desk-launch.py lane`
under O1. A model desk gets rulings through its inbox under I1.
A running
Agent lane gets a `SendMessage` AND the handoff line in the file it hands off
through. The root confirms the handoff on disk before treating the lane as stood
down; a running copy never sees the `SendMessage`.

*Prevents R365 at `04:16:03Z` launching nothing until R370, and sandsql-handoff-fix
opening #28594 29 s after its stand-down (2026-10-01).*

**R19. A settled question is applied, never asked.**
Before an owner question reaches the cc-present board (`cc-present start --doc`, `push`, `update-block`), the root checks each question against the authorities.
First read the owner's plan and every plan it names as source of truth, including each plan's Decisions section and the section the question touches. Then check `ccn answer list --label scope:durable` plus the drive's answer labels. Last check the project's feedback memories.
A question any of them answers is applied: the root writes the ruling to the lanes (R18) and logs it. The question never reaches the owner. "Confirm or override" cards for items settled by the plan count as asking.

A lane's question list is input, not output. The root never forwards a lane's owner-question file verbatim; it filters every item first. Only after the filter does a question go out, on the board (owner answers 01736af, b4dfb16).

An absolute owner instruction ("nothing is dropped", "every side-feature carries") stays absolute in every brief, ruling, and memory the root writes.
Never add "unless the owner explicitly drops it" or "or an explicit owner drop": an exit turns the ruling into a decision surface, and audit lanes manufacture questions from it.

An audit or parity lane's brief says the same: grade a row the owner's rulings cover against those rulings (carry, or the plan mechanism that carries it); never queue it as an owner question.
A prior root or PR decision that contradicts an owner ruling is a defect to fix forward, not a question.
Where a landed PR or a refining plan line conflicts with the owner's plan, the owner's plan wins and the conflict becomes a fix lane that changes the code to match the plan.
A revert runs only when the owner's own word names it (owner answer 36a298d).

Only a question nothing settles goes to the owner. Each card names which authorities were checked.

When the fork is about a check, gate or grader, the question names every existing
mechanism that grades the same signal and includes "delete it and let <that
mechanism> own the signal" as an option. Each option lists its added lines and
graders as a cost. Recommended goes to the option that adds the least new machinery,
unless the question says what that option fails to cover. Write no options while a
lane asked for them has not replied, and cite the research verdict the options rest on.

*Prevents the release-v3 owner question of 2026-10-10 (12:51Z). It said the release
"grades replicas in two places" yet offered no option that grades in one, and it marked
the largest option Recommended before the options lane replied. Four PRs then built a
second grader beside the Watch, and release 5445 failed its Switch on every env.*

A found violation of an existing owner ruling is applied, never put back to him as a card
or pick. The pack's `settled_questions` hook matches each question in an asking call
(`AskUserQuestion`, `cc-present start --doc`, `push`, `update-block`, a Slack status card
with options, and a root DECIDE sentence that orders a card) against the register's
in-force rows, the tracked plan's Decisions lines, the `scope:durable` answers, and the
feedback memories. It runs in lanes too, so the board refresher's pushes are checked. It
warns on a match and quotes the ruling or answer that settles it; apply that and drop the
question. A card on something the owner picked before, superseded picks and picks that
point the other way included, shows him each one as a line `You picked "<pick>" on
<date>`; the hook warns a card that leaves one out and lists the lines to add. The same
question re-issued passes, so a wrong match costs one retry.

An owner message that states a standing rule, with "from now on", "always", "never", "I
told you", or "the plan is", is recorded the turn it arrives, as an `answer_add` with
`scope:durable` and a line in the plan's Decisions section. When a turn ends without an `answer_add` or
`answer_edit`, the `root_context` hook queues `owner standing rule not recorded:
answer_add it (scope:durable) + a plan Decisions line` for the next turn.
A tap on an ask card, on Slack or the board, a tap approving a Slack lane's draft included, is the
owner's answer and the authorization for that question (owner answer 01736af). The root
records it as one `from:owner` answer whose body names the card's permalink and the
tapped label.

*Prevents the release-v3 parity board of 2026-10-01 (05:51Z), which asked the owner seven keep-or-drop questions (ack gate, finish, on-call swap, dev-check card, divider rows, Start button, start refusals) and a DAG question the plan's Decisions and §TM-dag already answered, because the root encoded "nothing is dropped" with an "or an explicit owner drop" exit and forwarded the audit lane's question list unfiltered: "the board showed those cards because you asked those questions in the first place instead of following the plan."*

**R19a. A gate that wants an owner-recorded answer for a settled decision is satisfied the same turn.**
Some gates accept only an answer the owner recorded, such as `pr-reviews accept --owner-ruling`
taking a cc-notes answer id. When the plan or a board pick already carries the
decision, the root asks it that turn as a question card on the board (owner answers 01736af, b4dfb16). Otherwise
it records the plan's decision as the ruling and accepts on the owner's behalf, citing the plan line
or board pick id in the accept reason. The root never ends a turn waiting for the owner to say "ask",
and never leaves the PRs behind the gate parked.

*Prevents the 2026-10-16 hold: #38419 and #38427 sat about two hours for a confirmation plan fc8714e and
board pick 7174aab had already made. The owner said "accept the rulings on my behalf, we already said
that's what we want."*

**R19b. Extrapolate every ruling.**
A ruling binds everything it implies, not only the mechanism the owner named.
The turn it arrives, the root records the owner's words verbatim and writes its implications.
They cover sibling mechanisms, both directions of a relation, and every artifact, lane,
ledger row, budget line, test row, board card, and brief that carries the old assumption.
It confirms those implications with the owner that turn, in chat for a few or on one card
for many, then applies the ruling to every live artifact and lane at once, never to new
work only (R17).
A ruling travels by its words and its implications, never by an id or a gist. A brief,
handoff, progress doc, or compaction narrative that carries only the id has dropped it.
Before a board, brief, handoff, or plan is presented, the root sweeps the whole artifact
against every ruling's broadest reading. A sentence that keeps, budgets, tests, or asks
about a thing a ruling removed is a defect even when it cites nothing.
A board, card, or reply is drafted from the owner's rulings, never from the code as it
stands on the base branch. A card that explains a term from that code where a ruling
covers it fails the sweep.
When the owner repeats a ruling, the repeat is a defect report: the root finds every
place the earlier narrowing survived, fixes them all that turn, and tells the owner what
was missed and why.
The root never settles a plan gap by DECIDE. A new column, table, route, field, state,
timer, or metric that neither the plan nor an owner row names goes on the owner's board
as a card, and the lane that met the gap stops on it. The root also reads what
each lane reports it did against the plan before relaying it; "a worker did X" is the
root's to check, not to pass on.
A contract, read-back, or DECIDE is never narrower than the owner row it cites, and
never applies his words past their purpose. `judge_watch.py`, a pass the orca runner
starts every minute, asks the drive's judge after the post, so no writer waits, whether
each lane's contract and each root relay misreads his verbatim words on the rows it
cites, or for a contract the two `rulings.py match` finds; a relay is also asked whether
it adds a mechanism neither his words nor the plan lines it shares words with name. A yes
posts one `review` record, `JUDGE <kind> #<seq> <key>: <sentence>`, in reply to the
artifact, to its writer and root. The writer fixes the artifact or answers on the flag
with the owner words that carry it; an answer puts one owner card on the board,
`judge-dispute-<flag>`, with his words and both readings, and his pick becomes an eval
case under `judge/owner/`. When `rulings.py classify` extends a row with new words, the
pass judges the ten newest contracts and relays of the last day citing that row again
under all his words; a yes there that the judge had read as carried before is a miss,
said so in the flag and written under `judge/misses/` as an eval case. The
`ruling_misread` hook judges the tests a pull request ships the same way at ship.
Every small-model question in the pack goes through one library,
`scripts/long_running/judge.py`; a caller with many questions of its own shape in one pass,
the stall watch, asks them in one `batch` call on the same command line. A judge reads the owner's words verbatim and what they
answered, never the register's summaries. A yes is asked a second time, and a split is
unsure, which never warns. A failed call returns nothing, so the caller keeps the
behavior it had without a judge. `LONG_RUNNING_JUDGE` picks the model: `sonnet+jev`, the
default, asks Sonnet at low effort with Claude Code's own system prompt replaced and lets
TypeSafe Jev settle a first answer of unsure; `sonnet`, `jev` and `jev+sonnet` are the
others. Each call appends one line to `judge/calls.jsonl` in the drive's state folder,
and `drive.py digest` prints each judge kind's verdicts, errors and latency over the last
day. `judge-eval.py <fixtures>` scores every backend on the same answers over fixture
files kept in the drive's state folder, because they carry the owner's words; it calls
models, so it runs by hand before a judge ships or a prompt changes, never in CI.
A ruling about system shape (one process, one primary and one replica, everything a
client, N things sharing one X) gets a CI test that fails on today's code in the same
turn as the build. `rulings.py shapes` lists the in-force shape rows; `check-pr` warns
of a PR citing one as `SHAPE-UNTESTED` until the PR adds a test naming the row or
`rulings.py shape <key> --test <repo>#<pr> <path>::<test>` records one. A test that names
the row is not enough: the drive's judge reads it beside his words, and a test it reads as
asserting a different invariant, one that passes while his fails, is `SHAPE-UNTESTED` too,
with the judge's sentence. The stall watch posts every shape row still untested 30 minutes
after it was written to root.

*Prevents the release-v3 repeats of 2026-10-22. The owner said for the fifth time that
starting a session in the cloud and then writing on the Mac is out of scope, and again
that journal-specific code lives in the journal crate, not the server. Each ruling had
been recorded as an id and a gist and applied to the one mechanism named. Sibling
mechanisms, the reverse direction, budget lines, test rows, board cards, and briefs kept
the old assumption. Board writers drafted from code on dev, and compactions carried the
plan text as the authority.*

*Prevents the 146 replicators for 144 chats of 2026-10-30. The owner picked one upload
process per Mac at 9:16 PM on Oct 29; a lane contract read it as one per destination and
key, the root accepted it, and no check compared the two.*

**R20. A Slack link from the owner is an assignment, and Slack drafting is a lane.**
A Slack permalink the owner pastes, bare or with words, is the root's to own, whoever
wrote the message. The owner's in-thread "Looking", "on it", or "checking" means they
handed it to the drive, never "the owner has it". It is never informational.

That turn, before anything except R16's `incident.py open` and `run`:

- (a) Spawn `general-purpose` on opus from `reference/slack-lane-brief.md`.
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

Permission follows the cc-slack skill. An owner instruction that says what to post or
who gets it ("explain to X …", "tell them …", "reply there", "manage the comms") is the
approval: the root quotes the owner's words verbatim to the Slack lane that turn, and
the lane posts directly with no draft round trip. The Slack permission hook counts the
root's prompts as the owner's words for a lane's write and mints the ticket for that
place. Only a post the owner did not ask for goes back to the root as an exact draft.
The root runs that write, asks the question the cc-slack permission hook prints for it
with `cc-present ask -` and the hook's board card, and stops on it until the owner's tap approves it; then
it retries the same write (owner answer 01736af). On a hook refusal for a judge
timeout, the lane retries once, then reports the verdict to the hook lane and keeps going.
The root hands the Slack lane facts: what happened, when in Pacific time, who asked,
and every link. It never hands over wording, never writes a "proposed reply" into a
question, and never tells a lane to post its words "verbatim". An approval freezes
the wording it shows, so a root-written draft reaches the thread unchanged.
The incident comms lane posts under the grant id on each executor event, a
`slack-grant:<channel id>` answer the permission hook reads, and never returns drafts
for a granted thread. The grant comes from the owner's own words, never from owner
words a lane relays.

The root does not compose Slack copy; the Slack lane drafts and posts as the cc-slack
bot. A write the owner approved, through a tap on that ask card or an explicit
instruction in the turn, the root may run itself with the cc-slack tools. The
permission hook owns that approval check, and no pack hook reroutes an approved root
write to a lane. `cc-slack dm-status` to the user's own DM stays the root's.

**Slack carries merge cards and nothing else.** The drive writes one kind of Slack
message: the merge card with Approve and Hold buttons in the owner's DM status thread.
With `landing.merge_ask_surface` set to `board`, the board's `pr-groups` block mirrors
each merge group to one card, posted once and edited in place on every head change and
on resolution, never re-posted. A tap on that card is a board tap: it records the same
verdict at the same head as the board row, and a verdict given on the board edits the
card at once. No lane and not the root writes anything else there or in any channel: no
status update, per-landing line, ready-to-tap list, batch post, lane report, correction,
or prose reply in the thread beyond the answer to his own `Explain more` tap. Status
lives on the drive dashboard. Prompt watch's
`needs_you` card is the one exception, and only in a drive that opts in with `--slack-cards`. The landing desk's
redraw of the thread's root card below is an edit, not a post, and stays. A new
automated poster needs the owner's word first, and a post the owner names goes out
through `comms-desk` or the R20 Slack lane.

Every owner ask the drive blocks on goes on the root's cc-present board, except a
per-PR merge ask under D3, which is the merge card (abddb9a). The root puts each
question card on the board with one command, never a hand-built block or a cci ask to
the board refresher:

```bash
drive.py ask-owner --title "<question>" --lede "<what he needs to decide>" \
  --option "<answer>" --option "<answer>" --recommend <n> [--for <waiting lane>...]
```

It pushes `card-<id>` with `cc-present update-block --round current` to the root
session's board: the title is the question, the lede its summary, and one single-select
`choice` holds the 2 to 4 answers with exactly one `recommended`. It then opens one
`ask` record naming the card to each `--for` lane, which the board recorder resolves
on his tap, and refuses a card whose text names a change or record number. The dashboard's Needs you lists every open
card, so nothing about it goes to Slack. Never an `AskUserQuestion`. The lane that
asked stops on that item until the tap, and the root never parks waiting for it. Each card offers Other and `Explain more`
(93037c3), so no card lists either among its answers. An `Explain more` tap resolves
nothing. The root posts
`cci post --drive <drive> --lane root --kind ask --to root --text "explain more: <card id>"`,
then answers in plain words with `cc-present reply --block <card id> --body <answer>`,
and the lane stays stopped until an answer is tapped.

What waits on the owner is what `cci owner-waits --drive <drive>` prints: each board row
awaiting his tap at the head it names and each unanswered pick, with when it went up. The
root quotes those lines, never a runner note or a lane's state line, when it tells him what
waits on him. `cci owner-waits --drive <drive> --pr <n>` says what a PR not on the board
really waits on: no row, a red build, a `fidelity: missing` grade unanswered since a time,
or an open parent below it.
The root's Stop hook checks every reply that says a PR or anything else waits on his tap,
approval or pick against `cci owner-waits`, and sends the reply back with the real state when
no board row at that PR's head awaits him (owner, 4:40 AM Oct 30: "there are no PRs waiting for
taps dont lie").

Every tap on the board reaches the bus the moment it happens, without the root. The
board recorder (`board_watch.py`, launched detached per board by the
`start_board_watch` hook) posts one `owner` record to all from lane `board-watch` for
each decision, pick, written answer, note, merge-row verdict, and round close, carrying
the card title, block id, his words verbatim, and a tapped merge row's head. It resolves
every open ask, blocker, or hold whose text names the block, its card, or the merge row
(`merge-row-<pr>`, `merge:<pr>`). A lane parked on a card names the card's block or card
id in its ask, then unparks on the `owner` record or its own closed ask, never on a root
relay. Each record carries its block, row, verdict and head as attributes, one record per
merge row. The root board's recorder also posts what the board shows as a `board-rows`
record and applies the landing runner's `board-write` records with cc-present, so the
landing runner and the stall watch read the board only from cci and run on any machine;
`board_watch.py backfill --session <id>` records every verdict the board already held and
prints `verified` once each stands in records. A recorder keeps running the version it started with, so after a release that
changes `board_watch.py` the root ends the old recorder's `cc-present watch` child and
relaunches it from the installed version.

*Prevents the Oct 26 spam: status cards, ready-to-tap lists and lane prose filled the
owner's DM thread and the team channel until he called all of it spam; only the merge
buttons were wanted.*

With `landing.status_card` set to `true` in `runner.json`, the landing desk keeps the
drive's root card current, so the root never writes rollup JSON itself. Each pass that
changes the ledger's picture, it runs `cc-slack dm-status --root-only` with the
drive's lanes and their open PRs as the `rollup`, the PRs the desk holds in the queue
as `refs`, PRs landed out of all tracked as `progress`, and open, queued, held, and
landed counts as `facts`. cc-slack polls those refs and redraws the card as CI and the
queue move. The desk inherits the root's `CLAUDE_CODE_SESSION_ID` from the shell that
started it, and redraws the root of that session's status thread, so the root sends
one status card before it starts the landing desk. A redraw cc-slack refuses logs
`CARD-FAILED` once per error, and the next pass tries again. This needs a cc-slack
release with `dm-status --root-only`.

Every lane that posts to Slack, the R20 Slack lane, the incident comms lane, and
`comms-desk`, spawns as `general-purpose` on opus, never as `long-running:lane` or
`lane-ship`. A plugin agent type's tool list is read once, when the root's session
starts, so a root that started on an older plugin spawns lanes without the tools
added since. `general-purpose` carries every MCP tool the session has, plus
`ToolSearch` to load them. The lane uses the cc-slack tools first (`slack_thread`,
`slack_react`, `slack_unreact`, `slack_reply`, `slack_send`), and the
`cc-slack` CLI on PATH when a tool call errors. When `cc-slack whoami` shows no
identity, the lane runs `cc-slack login` from the project checkout and retries. Both can
refuse: a Slack hook that crashes on import, no Slack identity on the machine, or a
conversation the bot cannot join. Then the lane posts the same checked text through
the user-level Slack MCP, `mcp__slack__slack_send_message` and
`mcp__slack__slack_add_reaction`, led by "_(<owner name>'s Claude)_" and under the same
grant. It reports the refusal verbatim to the root. A comms lane never leaves a
reporter unanswered because one Slack surface refused.

- In sessions started before cc-slack was installed, `mcp__plugin_cc-slack_*` tools
  and the Slack tools of `cc-slack:slack-waiter` and `cc-slack:slack-triage` are absent.
  The root writes no Slack even when its `ToolSearch` finds no cc-slack tool; the
  Slack lane reaches Slack through the CLI or the user-level MCP.

*Prevents the Oct 16 silence from 2:18 to 2:30 PM. kate and erik reported Iris
disconnects, and the two comms lanes posted nothing: the root checkout's stale Slack
hook crashed on import, then cc-slack had no Slack identity on the machine, and the
lanes, `long-running` types from a root that started on plugin 0.7.121, carried no
other Slack tool. The replies went out at 2:30 through the user-level MCP.*
- Urgency never licenses the root to write Slack. When a Slack lane stalls, the root
  spawns a fresh sender lane whose brief carries the grant and the exact approved or
  dictated text; it posts and reports the post's ts. The root tells the owner "posting via
  a lane" and checks the post's ts on disk within 2 minutes. In an incident, the executor raises a silent
  comms lane as a decision. The replacement lane posts the executor's unanswered
  event, so nothing is restated.
- A post that asks a person to act (a grant, a permission, an approval, an answer)
  opens an R3 wait→do chain. Any fence, hold, or `until <person>` line it gates names
  the thread (`until reply in <channel>/<thread_ts>`). No lane watches or polls that
  thread (owner answer d94db5b).
- An answer the owner dictated goes out as one message with the grant in the dispatch,
  never staged across several instructions.
- The owner's surface word is literal: "channel" means a top-level channel post,
  "thread" a thread reply, "DM" a direct message.
- A post about a PR re-reads the PR's state (approvals, CI, landed) immediately before
  posting; the writer lane does that read itself, never the root from memory.
- An incident's comms lane takes its authority from the grant id on each executor
  event and posts without a root turn; the root is told, not asked
  (`reference/active-alert-brief.md`).

**Reporter threads.** A thread where a person reported a break gets these rules from
every lane that writes in it, and the incident and Slack lane briefs carry them:

- Telemetry, never asks. No lane asks a reporter for evidence: a client version, an id,
  a log line, a reproduction step, or what they saw (owner answer 8ff2abf). Metrics,
  logs, Sentry, and our own run and session records answer it, and the incident fix
  brief lists those reads first. A fact none of them holds becomes a tooling lane that
  puts it in telemetry, such as the client version on every request.
- Architecture before cause. Before any message names a cause on a reporter's machine,
  the lane states which component served the failing request and shows that the request
  reached the reporter's client. A cloud run is served by the cloud. Every cause the
  lanes have not proven is called suspected, to the owner and in the thread.
- One message per reporter turn. Between two messages from the reporter the drive posts
  at most one. A fix going live, a lane's new ask, or a correction waits for the
  reporter's next message or is edited into the standing line. A root-directed line
  counts against the thread too.
- One writer per thread. One lane writes in a thread; every other lane routes through
  it. A Claude post there that is not one of this drive's own posts comes from another
  session. The writer stops posting, and the root tells the owner in that turn which
  thread and which post.

*Prevents the reporter threads of 2026-10-13. The comms desk asked a reporter for
`sand --version` while the alerts desk could read his team's client versions from
metrics, and posted 21 lines in one thread, three of them since the reporter last spoke.
The root also named a reporter's local sand as the cause of a failure in a cloud run,
and blamed another reporter's old client for a blob error the lane had called unproven.
Owner: "stop asking people for things you can collect yourself", "why don't you fucking
report that sand version in the header or something so you stop asking people", "its a
cloud run so what does it have to do with his local sand?", and in the thread itself,
"lol sorry for the insane spam here".*

**The drive's updates channel.** The turn the owner names a channel for the drive's
updates, the root records the owner's words as the channel's grant. The grant is one
cc-notes answer quoting those words with the label `slack-grant:<id>`. The Slack
permission hook reads it as a standing grant for top-level posts. It never waits on a
board or a second owner ask. At each milestone the root spawns `comms-desk` from
`reference/comms-desk-brief.md` with that milestone's facts and the owner's word for
the post. The desk posts it and ends. No lane stands on the channel, reads it for
messages, or answers anybody in it (owner answer d94db5b).

**Status posts mark milestones, never a clock.** The drive posts to its updates
channel at each milestone: a fix live on a named build, a release passed or failed, a
pass or migration finished, an incident opened or closed, a decision the owner must
see. It never posts per event and never on a timer, hourly or at any other cadence.
Silence between milestones is correct, and a long quiet stretch is reported only when
the owner asks. A drive that stops posting while milestones pass is a defect, and so is
one that makes up for it with timed posts. Before each milestone report to the owner,
the root checks that the channel carries its post.

The exemplar is the drive's kickoff post of 2026-10-12, at
https://poetic-slack.slack.com/archives/C0C9H4KPS7J/p1791841367899079. It leads with
the milestone in one bold line, then says in plain words what changes and for whom:
"We're collapsing them into one shape, so a laptop run looks just like a cloud run and
a teammate can open either one." It names the next PR, linked, and closes on the
contract: "Every piece that ships gets its own post here, picture included." Each post
carries the build or PR with its author, Pacific times, and no lane, desk, or register
names, and each milestone gets exactly one post.

*Prevents the 2026-10-13 sprint channel's silence from 10:43 AM into the evening, and
the hourly posts that replaced it. Owner, 2026-10-14: "your original posts were
perfect the only problem is you stopped doing them, then overcompensated by switching
to hourly".*

The pack's `root_context` hook blocks the root's Slack reads and never its approved Slack writes.

No hook enrols a posted thread or a pasted Slack link for watching, and no Slack watch
lane runs (owner answer d94db5b).

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
- Every owner-facing line, `needs_you` card, and board card names the thing, never
  its codename: "the release-pipeline cutover stack", not "D"; "the deploy that runs
  after each merge", not "the walker"; "the freeze on api/plat", not "G304". Inbox,
  ruling, answer, and `ctx_` ids belong in the ledger and the briefs, never in a
  question to the owner. Before sending, reread each line as someone who has not read
  the drive's inbox, and rename every noun they would not recognize.
- Every artifact the owner reads has two layers. What renders first is the human layer:
  a picture, a timeline, a table with toned cells, or at most 40 words, saying what it
  changes for him, where it stands, and what it needs from him. The agent layer (metric
  names, commands, record ids, every clause) sits behind a click: a disclosure, a
  Background section, a linked doc. A markdown table renders as a table, never as pipe
  text. This binds every board card, merge row, plan and plan preview, release plan, and
  dashboard screen, and the root reads each one as he will before it goes out:
  - a merge row is its six facts, one line each, words before any id;
  - a picture goes on a card with no decision in it, because the board's focus mode
    hides every non-decision child of a card that asks one, and an answered decision
    leaves the card;
  - detail sits in a closed `<details>` inside the card's page;
  - the writer screenshots the board at desktop width and at 390 wide before it reports.

The pack's `owner_facing` hook reads the root's final reply at `Stop` in a drive and
queues `Next reply to the owner: give times in Pacific with no zone label, never UTC`
when it carries a UTC clock time (`17:35Z`, `17:3xZ`, `16:52 UTC`). The time nudge, and
the plain-words nudge on each term in a reply or an owner question, fire at most once
per hour each, so a term already flagged warns again only an hour later. Its `plain_words_in_owner_questions` hook blocks a drive root's
`AskUserQuestion` that carries a codename, an inbox or answer id, or a proposed Slack
reply. Its `authorizing_lines_wait_for_the_owner_question` hook blocks a `GO`, `LIFT`, or
`UNBLOCK` line appended to an `inbox/*.md` file, and a `cci post --kind go|lift|unblock`
record, that names the PR or routing change an unanswered `AskUserQuestion` or a board
posted that turn asks about; write it in a later turn, once the owner answers.

*Prevents Oct 27 12:24 AM: a release plan card shipped as a list of metric names, failure
modes and a raw pipe table, a dump fit for an agent, and he had to ask for a version a
person can read.*
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

- In every brief or ruling on a subsystem, quote each owner design ruling verbatim
  with its id and the required entry point as a symbol at `file:line`. Have a reader
  lane find the symbol before dispatch. A package list is not an entry point:
  importing every package still permits a second implementation.
- Before READY-FOR-SHIP, the lane names the entry point the diff calls, each ruling it
  meets, and anything it leaves out, and posts that design check as a `contract` record
  on the bus. It settles the check itself from its brief, the standing rules register,
  and the base branch, and keeps building; it never waits for a confirm. The root reads
  the contract on the bus and withdraws it only on a mismatch with a quoted ruling.
  The lane asks the root first only when its choice changes owner-visible behaviour.
  Incident fix lanes record it with `incident.py note --design-check`; the executor
  asks the root to confirm.
- Never ship a "static first" split that changes the durable fix's agreed semantics,
  such as dropping footers, the judge, or the entry point. Ship the design whole or
  hold the PR; mitigate through the apply. If a lane says the full fix exceeds its
  role, re-route it to a lane that can build it. Never narrow the design to fit the lane.

*Prevents the C09G failure of 2026-10-01: R693 named packages instead of
`releaseDAG`; R699 deferred footers and baseline; R704 sent READY-FOR-SHIP straight
to a ship lane. #29109 added a second closure without footers, and the owner caught
the mismatch in the diff. Root confirms of each design check then parked lanes for 20
to 40 minutes on 2026-10-12 and dispatched them one at a time; the contract record keeps
the check visible without the wait.*

**R23. Stop every finished agent the turn its completion arrives.** On each subagent
completion notification, the root calls `TaskStop` on that agent in the same turn, after
taking its report. Before any spawn, the root stops every agent of its own that has
finished. The concurrent-subagent limit is never a blocker to report: a full limit means
finished agents still hold slots, so stop them and spawn. Live lanes, flushed lanes,
and Orca terminals are not finished agents; R4 and Lane rotation still govern them.

*Prevents the release-v3 root of 2026-10-07 reporting the subagent limit as a blocker
while many finished agents sat idle; the owner had to kill them by hand and ruled "fix
your skill and never use this as an excuse".*

**R24. A production operation is logged the turn it runs, and read before a mechanism
is named.** The cc-notes log `production-operations`, label `prod-ops`, holds one line
for every destructive or state-changing production operation an agent runs with the
owner's authorization. That covers a `kubectl exec`, a reset-table, a data delete, a
hand apply, and a break-glass write. The agent that runs one appends its line in the
same turn, and R16's pre-authorized apply is no exception.

```sh
ops=$(ccn log list --label prod-ops | cut -f1)
ccn log append "$ops" --entry "who: <session or lane> | when: <UTC> (<Pacific>) | where: <cluster>, <team> | action: <exact command> | authorized by: <answer id> | effect: <what changed>"
```

Every incident lane and the alerts desk read the last 24 hours of that log before
naming a mechanism.

```sh
ccn log show "$(ccn log list --label prod-ops | cut -f1)"
```

An entry on the alert's target inside the onset window is the first mechanism to
test, and the lane's report quotes it with its authorizing answer. The read belongs
to the lanes and the desk. It never stands between an alert and R16's `incident.py
run`.

*Prevents the 2026-10-13 unknown-table incident: a drive spent two hours diagnosing
tables missing from a customer's shared environments, which the owner's own session
had reset at 3:57 PM the day before on the owner's authorization. No shared record
said so, and the cause surfaced only from a Kubernetes audit read.*

## The landing desk and its ledger

On a drive where many lanes open PRs, the root is the wrong place for their reports.
Each report is a message in the root's window, and each landing is a wait. The desk is
one long-lived lane, `landing-desk`, that takes those reports and reconciles landings.

Lanes never enqueue under D1. `desk-runner.py run --desk landing` is the drive's
merge queue and owns D3, D14, D16, and D20, including for shards. It reaches the
repository's queue through the adapter `landing.adapter` names in `runner.json`. The
repository's queue is the one ccx selects: git config `ccx.queue`, else `[vcs] queue`
in the checkout's `.claude/ccx.toml`. `trunk-stack-enqueue` runs the repository's
stack-enqueue helper on a Trunk queue, over native GitHub stacks.
`graphite-stack-enqueue` runs the same script on a legacy Graphite queue only; on a
Trunk repository the runner refuses it and names `trunk-stack-enqueue` instead.
`label` runs `ledger.py label` for a queue that takes the `merge` label, which is also
Trunk's enqueueing label. The landing desk never enqueues beside it.
The root owns holds and priority PRs under D3, and steers the queue with `mq.py`.
It receives P0 lines immediately and a summary every 30 minutes.

`scripts/ledger.py` is its one tool. It uses `ccx vcs pr state` for refreshes and
`ccx vcs pr watch` for transitions. Both read ccx's machine-wide pull request cache,
which polls each repository at most once every 30 seconds. Its PR store is a cc-notes
ledger with a row per PR our lanes shipped. The holds, the routing, the label history,
and the landing are fields on that row. Lane messages are cci records keyed `#<seq>` on the program's cci drive,
the same drive named by desk-runner's config `drive`; owner asks are `ask/<seq>`
rows beside the PR rows. Rules reviews are `review/<pr>@<head>`
rows that hold landing while the PR's current head has a pending review or an
unwaived finding. cc-notes
finds the ledger through the working directory's repository, so a lane outside that
checkout passes `ledger.py -C <checkout> <verb>`.

Every script in `scripts/` is on PATH by name through the plugin's `bin/`, which Claude
Code adds for the installed version, so briefs call `ledger.py` or
`orca-launch.sh` and never a path. `orca-launch.sh` puts the same `bin/` on PATH for an
incident worker's codex terminal. Lane messages use `cci` directly. A script run by
path from an older version than the installed one re-executes the installed copy with
the same arguments, unless a process of its own version started it, such as a desk
runner that has not re-executed yet.

The desk grades, lands, and tracks only through `ledger.py`, never scripts of its own;
a gap in `ledger.py` is a `RULING NEEDED`, not a workaround.
`reference/landing-desk-brief.md` is the desk's brief, ready to paste;
`reference/desk-contracts.md` holds the message shapes. It is R5 applied to PR state and
R3 applied to the watching.

Spawn it first, before any lane that will open a PR, whenever three or more lanes
will ship through one merge queue or the drive will outlive one context window. Below
that the lane that opened the PR lands it, or the root lists it for the label watch
under Mechanics, and there is no desk.

**D1. The landing desk is the only enqueuer; lanes report; the root owns holds and priority PRs.** A lane never runs `stack-enqueue` (outside `--check` and `--status`), `gt merge`, `gh pr merge`, or `ledger.py label`, and never adds a `merge` or `merge-fast` label. The pack's `lane_landing` hook refuses those commands against the drive's repo, read from the drive registry, from every lane except the desks (`landing-desk`, its shards, priority desks), `desk-runner`, `merge-train` lanes, and incident lanes, named `incident-` or `outage-` or briefed with a `ccx: incident=<id>` line other than `none`, or named `<x>-fix` and briefed with its own `Register slice: lane=<x>-fix step=incident` line, which it refuses only `stack-enqueue` and `gh pr merge --admin` and lets run `gh pr merge <n> --squash`: incident fixes land by plain squash the pass they are approved and green (ef905a6), and the landing runner merges them. The same pack refuses `mq.py dequeue`, `requeue`, `priority`, `recover`, `hold`, and `lift` from every lane, desks included: only the root steers the queue. A tooling lane merges its own PRs in other repos. The merge queue enqueues every bottom prefix its check registry passes under D3, through its adapter in every repository. A hold is an open cci `hold` record, which `ledger.py held --drive <drive>` prints, or GitHub's `hold` label. Only the queue's `hold` check reads holds: no adapter takes a hold flag or reads one. A lane's `cci post --kind hold --pr <n>` holds that PR until a `lift --resolves <seq>` closes the record. The queue re-reads every hold just before each enqueue call, never a cached set.

The lane's part is the report. The moment a PR is green, it reports `READY` with `ledger.py report --drive <drive>`, then keeps `ccx vcs pr watch` on the stack. On ejection or conflict it restacks at once with `remote-restack.py --config <runner config> --lane <lane> <top PR>`, which replays the stack on a Sprite, pushes, and posts each new head, and it resolves by hand only a conflict that tool reports; the runner re-enqueues it. It is not finished until its squash `(#N)` is on the base branch. After the prefix lands, it restacks the PRs above it the same way. No ruling is needed.

A ready stack is enqueued as one batch from any of its PRs: the adapter lands the largest green bottom prefix in one call, never the first PR alone and the rest later, and never through the `merge` label or `gt merge` on one PR of a stack. On a stalled queue, where a head's draft closed unmerged, the root reads `mq.py --config <runner config> why <head>`, then runs `mq.py recover <head>`.

Holds travel only as cci records, so the landing runner reads no hold from a file and can run on any machine. The root holds a PR with `mq.py --config <runner config> hold <n> --reason "<why>"`, which posts a root `hold` record leading `hold #<n> <why>`, or with `cci post --kind hold` naming the PRs by `--pr`; a record holds the one PR its text leads with as `hold #<n>`, else every PR its refs name, and a later `#<n>` in the text is prose. `mq.py lift <n>` posts one `lift` record resolving every open hold record that holds the PR, and any record that resolves a hold lifts it. Just before each enqueue call the runner reads the `hold` label and the PR's cci `hold` and `ready` records fresh, refuses with one `REFUSED` line when any holds it or a `hold` record is newer than its newest `ready` record, and repeats the read once the adapter's enqueue returns, taking a prefix a hold appeared on back out of the queue through the adapter's dequeue, with a `DEQUEUED` line and a cci `stopped` record to every drive that names each PR. The runner never enqueues a prefix containing or sitting above a held PR; the lane reports `held` on its tip, names the held PR, and leaves that PR and those above it to the root to release.

Lanes record each report themselves with `ledger.py report --drive <drive>`: PR, full head sha, verdict, and one line of text. The desk reads them with `ledger.py inbox --drive <drive> --take` every iteration, and its watch prints a `REPORT` line the moment one lands. The root receives `P0` and `RULING NEEDED` lines immediately and the 30-minute summary. If the owner flags a PR as priority, or it blocks a release or a user, the root runs `mq.py --config <runner config> priority <n> 0` and `mq.py why <n>` in the same turn under D3, and fixes or routes whatever check `why` names. An open owner ask that links the PR gives it priority 0 already. That approval covers only the head the owner named; if the PR gains commits or scope, the root gets fresh approval naming the new head. Never relay an ETA for a green priority PR.

*Prevents green approved stacks waiting on one serial desk, which prompted the owner's 2026-09-30 ruling to enqueue more than one thing at once; the runner's parallel enqueues keep that. Also prevents a tip enqueueing held parents. Tip #28102 sat above held #28081 and #28082 on 2026-09-30, kept out of the queue only by red CI. On 2026-10-12 lanes L28 and L23 ran `stack-enqueue` themselves under a lane hold, landing #36023 and #36039; the owner ruled that the tooling, not a memory, stops it. The same night a hand-written `#30082 ours to land` line held the very PR it meant to free.*

**D2. Track and grade our lanes' PRs.** The pack's PR hook, `ledger.py report`, `ledger.py register`, and an explicit `refresh --pr` are the only paths that open a PR row. A PR is the drive's by what GitHub shows of the PR itself, never by record text: `refresh` admits every open PR on a branch prefix or stack a lane registered, and the landing runner registers a PR carrying the `drive:<drive>` label under its head branch. Every lane registers its branch prefix at spawn with `ledger.py register --lane <lane> --branch-prefix <prefix>`. `ledger.py unadmit --pr <n>` withdraws a PR the drive admitted by mistake: its row stays unregistered, so neither `refresh` nor the runner admits it again until a lane runs `register --pr` for it, and a merge label the desk set comes off. Owner ask rows open only through `ledger.py ask`. The desk grades every tracked PR without waiting for a lane report at its current head. The desk never lists the repository's pull requests; a PR it cannot trace to one of our lanes stays outside the ledger and its counts. The desk never enqueues, labels, or lands a PR the drive did not admit, however green and approved: the landing runner and `ledger.py label` refuse any stack holding one, and neither an `opened` or `head` record nor the author login makes a PR ours.

*Prevents routing comments and rebase orders landing on other engineers' PRs, which one repo-wide sweep did twenty times in an hour.*

**D3. Enqueue every ready bottom prefix in priority order.** `<tip>` is the top of the largest contiguous bottom prefix whose PRs pass. Under the `label` adapter, `ledger.py label --pr <tip> --checkout <path>` walks base refs to the repo's default branch and re-reads every PR in that prefix. Each must be open, with approval in force, successful commit status, no failed checks, and a completed, successful latest `ai-review`. Approval on any commit counts; a dismissed or withdrawn approval does not. Each head must have no desk hold or lane `held` verdict on that head, no prior label or pull, and no conflict with its base.

Allowed `mergeable_state` values are `clean`, `behind`, and `has_hooks`; on a legacy Graphite stack, a PR above the bottom may also read `unstable` while Graphite's `mergeability_check` is its only unfinished check. `--expect-head` takes a lowercase hex prefix of the tip's sha, 7 to 40 characters long. An untracked downstack PR or an orphaned base refuses the whole attempt.

An open child outside the prefix is allowed only when a lane tracks its ledger row and its stack records it: GitHub lists it in a native stack, the pull request's `stack` field, or on a legacy Graphite stack its head carries the `Graphite / mergeability_check` check run. Link any other child into its stack with `ccx vcs stack submit`, or retarget it to the trunk, before adding the label. `--expect-head` pins the tip you graded; each call re-reads its tip immediately before grading it. A report is not required; the forge decides whether a head is red or conflicting.

`desk-runner.py run --desk landing` runs D3 as the drive's merge queue. `runner.json`
names its adapter as `landing.adapter`: `{"kind": "trunk-stack-enqueue", "script":
"<path to stack-enqueue under the checkout>"}` when the repository's ccx queue is `trunk`,
`{"kind": "graphite-stack-enqueue", "script": "<path to stack-enqueue under the checkout>"}`
for a legacy Graphite queue only, or `{"kind": "label"}`. The native `trunk-stack-enqueue`
adapter consumes the helper's `GREEN`, `WAITING`, and `BLOCKED` prefix statuses plus the
structured `tools/ci trunk status` `active` boolean and never guesses queue membership.
The runner refuses to start with `MQ-CONFIG-INVALID` on an adapter, a check, or a high-risk
setting it cannot run, including `graphite-stack-enqueue` on a repository whose ccx queue
is `trunk`; the refusal names the `trunk-stack-enqueue` adapter that replaces it. Each pass fast-forwards the checkout
to the remote trunk, so `landing.checkout` names a worktree only the runner uses;
one its repository no longer lists, or one on a branch with no commit, is rebuilt
at the trunk head in that pass and logs `MIRROR-REBUILT`; one that still cannot
fast-forward logs `CHECKOUT-STALE` and gates nothing that pass.
It then runs `ledger.py refresh` and `reconcile`, reads rows, groups every tracked open stack tip by the root of
its stack, and gates each stack in parallel, naming every tip of a fork in one
adapter check. The check selects the largest ready bottom prefix. Trees gate in
`(priority, root PR)` order, where a tree's priority is the lowest ledger `priority`
among its PRs: the value `mq.py priority <n> <value>` writes, else 0 while an open
owner ask links the PR, else 100. Accepted enqueues run one parallel wave per
priority, lowest first.

Every prefix passes the check registry before it enqueues. `landing.checks` lists the
checks in order, and every built-in runs when it lists none:

- `hold`: the `hold` label, the PR's cci `hold` and `ready` records, and any open cci `incident` record whose refs or text name the PR, read fresh just before the enqueue call. An incident's fix never enters the queue: its lane squash-merges it directly, and the refusal names the incident's seq. It runs first on every enqueue path, the runner's and `mq.py gate` and `enqueue`, whether `landing.checks` lists it or not; `mq.py enqueue` refuses a held PR with a `HELD` line after the hold's own line, and an open `hold` record of any age holds until the root's `--resolves` closes it.
- `precondition`: a sentence in the PR body or the PR's cci records that holds its landing on something outside the PR in so many words (`lands only after`, `merge only after`, `before it lands`, `do not land until`, `not for landing`), outside quotes, code and block quotes, so a body that cites the phrases holds nothing. It never refuses a head: the root gets one `note` warning per head quoting the sentence, on the incident squash too, and posts a `hold` record when the sentence is a real dependency.
- `rules-review`: the ledger's `review/<pr>@<head>` row, which `ledger.py list` reports as `rules_blocked`.
- `rulings`: `rulings.py check-pr` at the head, below.
- `fidelity`: the fidelity lane's cci `review` grade at the head, below.
- `finder`: the sol finder's cci `review` record at the head, below.
- `merge-mode`: the owner's merge mode, below, read at accept and again just before the enqueue call.
- `approvals` and `required-ci`: the adapter's own verdict on the PR.

The first check that refuses a prefix posts its blocker or ask and stops it there.
`mq.py --config <runner config> why <n>` prints the adapter's verdict and one
`<check> pass|pending|fail <reason>` line per check, and posts nothing; `mq.py checks`
prints the registry. The runner accepts an enqueue for its exact prefix heads.
It permits another attempt for the
same heads only when earlier attempts are proven to have enqueued nothing, or
when `ccx vcs pr status` reads a believed-queued PR `not queued` or `evicted` at
the head it admitted. Then the adapter's verdict on the dropped PR names the
ejection, never the PR's comment text:

- `conflict` stays out at that head, and the gate routes its ejection to the lane.
- `own-red` is gated again, and the adapter blocks it until a new head.
- `batch-mate-red` is gated again and goes back at the same head. One blaming a PR
  outside the PR's own stack logs `AWAITS-GO` and waits for a root `go` naming the PR.
- `flaky-check` is a red check that read green on the PR's own head and failed only
  in a queue draft holding nothing but its stack. It stays out at that head after its
  first ejection, and one blocker naming the failing job goes to the restack lanes
  `landing.restack_lane` names, else the root. The PR enters the queue again only at
  a new head.

Two lines to root name what a batch leaves out, per root #131054 and the root's
Oct 26 runner brief:

- `HELD-OUT`: a hold covers a PR the check would sweep into the batch. The PR and
  every PR above it stay out, and the enqueue names only the tops below it (`--partial`
  on `graphite-stack-enqueue`; `trunk-stack-enqueue` never passes `--partial`). No check
  or enqueue call ever names a held PR.
- `BATCH-WAITS`: a root `go #<n> at <sha> with #<a> #<b>` binds its PRs and the
  `with` PRs into one batch, and the prefix holds only part of it, because one is
  held, red, or not yet tracked. Every PR of the batch and every PR above them stay
  out until the whole batch is enqueueable in one call.
- `FORK-DEFERRED`: the batch sits on an open fork outside it, so it waits until
  that fork lands, and the line names the PRs that wait and the fork. One linear chain
  goes per batch, so a batch that forks within itself goes in as one stack-enqueue call
  per chain in the same pass, bottom chain first.

A failing required check posts one `blocker` per PR, refusal, red job and cause. The
cause is the job's status description with run numbers and durations folded. A later
head that fails the same way gets one `note` under that blocker. A changed cause or a
new red job opens a new blocker.

`mq.py dequeue <n>` takes a PR out of the queue only when this drive's queue
enqueued it or one of its lanes opened it. For another drive's PR it refuses and
names that drive's root. A dequeue posts a cci `stopped` record to every drive
whose records name the PR, and the queue leaves that head out until `mq.py requeue <n>`,
which first has the adapter release its own latch on the head: under `label`, `ledger.py requeue`
clears the pulled or labelled mark that otherwise refuses the same head.
The root can order the same dequeue on the bus: a root record that opens with
`dequeue #<n>`, or says `landing-desk: dequeue #<n>` outside quotes and code, makes
the landing runner take the PR out before any enqueue of its next pass. A root
`withdraw` record that resolves or answers a root `go` orders out the same way each
PR of that go the runner already enqueued at the head the go names. It says
`DEQUEUED` only once a status read shows the queue let the PR go, and
`DEQUEUE-FAILED` when the read still finds it queued or the dequeue is refused.
`mq.py hold <n> --reason R` and `mq.py lift <n>` post and resolve the PR's cci
`hold` record, and `mq.py status` prints what the queue holds and the order it gates.
A PR's lane, for every blocker and restack the runner routes, is the poster
of the newest cci `head`, `opened`, `ready`, or `claim` record naming it, never root's
or a desk's, since a fidelity desk claims the heads it grades without owning them, else
the lane that registered its ledger row; a cci blocker also goes to the root
when `cci who` lists no live session for that lane. A lane with no live session
or dispatch and no cci record on the drive in the last 60 minutes is dead: its
blocker goes to the lanes `landing.restack_lane` names, or to the root while none is
named or every one is dead, opening with `owner lane dead`, and its conflict
is never parked (root #131190). To hand a PR to a new lane, post a `claim`
record naming it as that lane.

The runner also requires a fidelity ok at every prefix head. It reads the cci
`review` records the fidelity lanes posted about each PR with `--pr <owner/repo>#<n>`,
which cci also matches to a post that named the bare number, and keeps those whose
text starts `fidelity: ok` or `fidelity: missing` and whose first short sha is the
PR's current head. Each lane's newest grade of the head stands for that lane, so a
`missing` blocks until the same lane grades the head ok; a later ok from another
lane never clears it. An ok counts as posted: rows the register gains after it
never void it or hold the head (4236611).
A head graded `missing`, graded ok only at an older head, or never graded gets
one cci `blocker` per head and grade, sent to the PR's lane and the fidelity
lane, and the prefix is cut below it: the PRs under it that pass every check
enqueue now with `--partial`, leaving it and every PR above it out, and its
blocker names it as the cut. The check runs again just before the enqueue call. The lane is `landing.fidelity_lane` in `runner.json`, one lane
name or a list of lane names when grading is sharded. The runner re-reads the lane from
`runner.json` on every use, so adding a desk needs no restart. The landing desk refuses
to start without `landing.fidelity_lane` and posts `FIDELITY-LANE-UNSET` to the root.

The `finder` check needs the newest `finder:` review record naming the head, from
`finder-desk` or a numbered successor, to read `finder: ok`; a lane's own record or a
reply naming a finder record never grades a head. A head with none, or whose newest is `finder: major`,
gets one `FINDER-PENDING #<n>` blocker per head to its lane and `finder-desk`. A finder pass answers a
major with a new head's record or a later `finder: ok <head7> answered: <how>`.

The orca desk runner posts that record itself once `runner.json` carries a `finder`
object: `{"model": "sol", "concurrent": 2, "checkout": "<repo checkout>", "to": ["<lane>"],
"timeout_minutes": 60}`, every member optional. On every `head` record it starts one
codex finder for that PR and head at once, one per PR a multi-head record's
`head:<pr>` attributes name, so a restack posts one record with every head, never one per PR; for a
single head it takes the PR from the record's PR refs or,
when it has none, from the first `<owner/repo>#<n>` or `#<n>` of its text, beside the fidelity desks, never after them:
over the head's own commits, those no commit on the base branch's current tip matches by
patch, from `reference/finder-desk-brief.md`, which forbids any local build or test. A head
after one the PR's last finder record graded reads only what changed since, with no row
pass, and when its own change is patch-identical to the graded head's it posts that grade
as `finder: ok|major <head7> carried from #<seq>`, so the head never reads ungraded. A
major root settled carries as `finder: ok <head7> carried from #<seq>, settled by root
#<decide>`: a root `decide` settles a finder record it names by `--re` or `--resolves`,
every finder record on the PR before it when it is about the PR and says settled, and every
carry of a settled record. Only a root `ask` to `finder-desk` saying `finder: whole` queues a
whole-PR pass; a `decide` never does. A
head the PR no longer carries when the pass starts or finishes posts nothing. It posts
`finder: ok <head>` or `finder: major <head> <n> open: …` under lane `finder-desk` to
the head's lane. A head reaches the finder once, a head a `finder-desk`
record already names gets no second pass, and the runner holds at most
`concurrent` passes at once, queueing the rest. A failed pass, or one whose process
exited with no result, frees its slot and runs again, up to three attempts; the third
failure is one `FINDER-FAILED` to root, and root asks `finder-desk` for `finder: whole` on the PR. A
`FINDER-PENDING` head with no pass waiting or running queues one again, and passes left
waiting 15 minutes with none settling are one `FINDER-DESK-STALLED` to root. `finder-desk` posts once per pass, so `LANE-GONE` never counts it
as a lane.

Fidelity grading of heads runs the same way once `runner.json` carries a `fidelity`
object: `{"model": "sol", "concurrent": 6, "baseline": "<live old-app baseline>", "to":
["<lane>"], "timeout_minutes": 60}`, every member optional, with `fidelity-pipeline` added to
`landing.fidelity_lane` so its grades count. On every `head` or `opened` record it starts one
`fidelity.py` pass for the PR's newest head that no fidelity lane has graded. A head whose own
commits have the same `git patch-id` as an earlier head every fidelity lane graded ok takes
that grade with no model call, as `fidelity: ok <head> carried from <earlier>` with its
`old-app:` line. Its `pure port, no interaction` or `test-only or CI-only, no non-test code`
class comes from a root `decide` record that answers the carried grade, or a grade it was
carried from, with `class wrong`, which drops the class; else from the newest fidelity-desk ok
grade on the PR; else from the carried grade. A `missing` grade a root `decide` settled, by
`--re` or `--resolves` or by saying settled about the PR, counts as ok for a carry, which cites that
decide. A new head's codex read lists the settled points, a `missing` entry that repeats one word
for word is dropped, and a reply left with none posts ok citing the decide. Any other head gets one codex read from `reference/fidelity-job-brief.md`
over its own diff, its required register rows, the plan, the lane's newest contract and the
PR body, graded by `reference/fidelity-desk-brief.md`, and posts its fidelity line, one
`touches <key>` per reserved row and its `old-app:` line. A head codex cannot settle goes to
the other fidelity lanes as `fidelity: reader <head> <why>`; those desks keep read-backs,
text grades and the sweep. A failed pass is one `FIDELITY-FAILED` to them, and the next
`head` record for the PR queues it again.

The `rulings` check runs `rulings.py check-pr --repo <checkout> --drive <drive>
--pr <owner/repo>#<n> --head <sha> --base <base>` once per head a pass and holds no
head (4236611). Each cause it prints but `RESERVED-NO-ANSWER` is a warning: one cci
`note` per head and set of causes to the PR's lane. A head warns again only for a cause
none of its earlier warnings named, and never once an `answer` replies to or resolves
one of them. A reserved touch is merge mode's
to settle, because merge mode sends the card whose tap authorizes it.
A `NAME-HIT` clears once a line of the body's `## Rulings` cites the hit's row by key or
answer id, quotes at least four of the owner's words from it verbatim, and follows the quote
with a plain sentence of at least three words on how the PR keeps the ruling. The citation
is the acknowledgment, even for the row's own names (926a89d).
A row whose `protects` field names the harm it prevents, with `not_when` naming where it
does not apply, prints both under its words in every slice. A cited row that has one needs
a `## Rulings` line naming it that says what it protects and how the change serves that,
`<rule> protects <harm>; this change serves it by <how>`; otherwise check-pr prints
`RULINGS-PLAIN <rule> cited by id alone` (root #204765).
A `MECHANISM-BANNED` line names an added line, in code or a test, that starts a timer
(rule/949e6be), waits inside a loop (rule/f60345a), writes a credential, token, grant or
pass to a file (prompt/1026-105052), or runs a build or test on this machine
(rule/c9b7c87). Its lane fixes it before the owner's tap, or cites his answer naming the
shape as a `# owner: <answer id>` line under `## Rulings`. Lanes see the same lines
before a push from `rulings.py sweep --diff <file>` over their own diff.
A run that exits with a code other than 0 or 1, or prints a line naming no cause, passes the head with nothing
posted, and so does each `BLOCKER` line it prints: the check fails open (0afd2f6, 4236611).
`reference/fidelity-desk-brief.md` is the fidelity desks' brief, ready to paste, and
`reference/finder-brief.md` is the sol finder's.

*Prevents an enqueue past a fidelity refusal. On 2026-10-13 the runner enqueued
monorepo #36596 at ceb14d32, which the fidelity desk had graded missing F307;
it landed as 2b06f105 without its test, because the gate read only
`stack-enqueue --check` and the holds.*

A restack carries its grade. When no grade names the current head, the runner
takes each head an older `fidelity: ok` names whose own newest grades are ok, one of them counted, and
compares `git patch-id --stable` of the PR's own commits at both heads, read from
GitHub's compare of `<head>~<n>...<head>` with n the PR's commit count, so a restack
whose parent also moved still matches. Equal patch ids carry the ok to the new head in that pass,
cited as `fidelity: ok at <new head>, patch-identical to <graded head>` with the
graded record's cci seq. A grade naming the new head always wins, and a changed
patch waits for its own grade. The newest `finder: ok` carries the same way (root
#131054). A fidelity lane asked to grade such a head answers
the minute it is asked with the same line, without a re-read.

*Prevents the Oct 16 restack of monorepo #39264 to e41b99cc needing a fresh fidelity
grade for a diff already graded ok at c9fce65; landing-gates held the incident fix
for that grade at 2:31 PM.*

**The owner's merge mode.** At execution start, before the landing runner starts,
the root asks the owner once which merge mode the drive runs in:

- `every`: every PR waits for the owner's answer before it enqueues.
- `high-risk`: only a high-risk PR waits, one that changes data or migrations,
  credentials, the release pipeline, delete fences, storage formats, or infra rows;
  every other PR enqueues once it is green and approved.
- `auto`: every PR enqueues once it is green and approved.

The root records the pick as a cc-notes answer, `ccn answer add` with the question
verbatim and the label `program:<drive>`, and writes it to `runner.json` as
`landing.merge_mode`, with the repository's high-risk globs as `landing.high_risk_paths`,
and names the adapter as `landing.adapter`: `trunk-stack-enqueue` when the checkout carries
`.agents/skills/submit-pr/scripts/stack-enqueue` and the repository's ccx queue is `trunk`,
`graphite-stack-enqueue` when the checkout carries that script on a legacy Graphite queue,
else `label`. The landing runner refuses to start without one of the three
values and posts `MERGE-MODE-UNSET`. It re-reads the value on every use, so a later
change is one edit plus an `answer supersede`, with no restart.

`landing.tap_rules` maps a register row key to path globs, such as
`{"rule/82317b0": ["go/sand/next/*", "infra/ci/src/pipelines/test/sand.ts"]}`. In every
merge mode, a head whose changed files a row's globs name waits, while `rulings.py rows`
lists that row in force, until an approval on the root's board names that head. A root
`go` never stands in for that tap. A pure port (06288326), a test-only or CI-only PR
(owner, Oct 28 8:45 PM), and a head the runner noted `TAP-KEPT` at are exempt: a head
whose newest fidelity grade says one of `landing.exempt_grades` (default
`pure port, no interaction` and `test-only or CI-only, no non-test code`; the legacy
`landing.port_phrase` replaces the first) needs no tap and no go in any merge mode and
goes straight to the merge queue once the hold, fidelity, finder, build and CI checks
pass (owner 7:00 PM Oct 29, root #194387), with one `EXEMPT-ADMITTED` note to the root and
each `landing.board_lane` naming the grade when a tap rule names its files. A grade saying the port touches rebuild
code keeps the tap. Either class is also checked against every file the PR changes
against its base, never the grade's words alone (root #182391). A test-only or CI-only
claim keeps the tap on a file that is not a test file by `testfiles.py`, under
`__tests__/`, `testdata/`, `fixtures/`, `docs/`, `.buildkite/`, `.github/`, `tools/ci/`,
`infra/ci/`, `.agents/skills/*/scripts/` or `.agents/skills/*/tests/`, or buildkite yaml,
and the `TAP-REQUIRED` blocker opens with `TEST-ONLY-MISMATCH` naming the first such file.
A port claim keeps the tap on a file outside `go/sand/next/`, with `PORT-MISMATCH` naming
it, unless a PR comment under a `## Rulings` heading has a line that names the interaction
and cites the owner's answer as `# owner: <answer id>`. The runner posts one `TAP-REQUIRED` blocker per head,
naming the row, to the PR's lane, the root, and each `landing.board_lane`. When the changed
files, the register, or the board do not read, it never guesses a merge: the head stays
unqueued, and one `TAP-UNREAD` warning goes to the PR's lane, the root, and each
`landing.desk_lane`.

Every enqueue path reads the same tap: the runner's pass, `mq.py enqueue`, and a hand
`stack-enqueue`, which the pack's `lane_landing` hook refuses with the runner's own
`TAP-REQUIRED` line from `mq.py gate`. A root `go` that says the owner tapped or approved
a PR must rest on a board verdict: with `landing.merge_ask_surface` set to `board`, a go
naming an open PR at its head that claims his tap, with no port, test-only or CI-only
exemption, while no approval on the root's board names that head or a head the go text
names, draws one `GO-TAP-MISSING` blocker to the root per go and PR, saying what the board
holds for the PR instead (root #172682).

Under `every`, and under `high-risk` for a PR whose changed files match a glob in
`landing.high_risk_paths`, a prefix that passes every other gate is not enqueued.
The runner asks the merge card itself, once per waiting PR and head, and logs one
`MERGE-ASK` naming it. The card goes through the same path as every other card,
`cc-present ask` with `--session` set to the drive root's newest session in the drive
registry, so it lands on the root's board and in the root's Slack status thread, posted
as the root (owner answer 8ed9899): the question in plain words, the PR's title,
author, size, and review state, and the PR as a hyperlink with its live checks. It
waits on `merge:<n>:<short sha>` with `Merge (Recommended)`, `Hold`, `Other`, and
`Explain more` (93037c3), never an `AskUserQuestion`, so the drive keeps working while
the card waits. A card already open for the head is edited in place, never re-posted.
A missing registry entry or a failed post logs `MERGE-CARD-FAILED`, and the next pass
tries again. The root never hand-writes a merge card. An `Explain more` tap resolves
nothing: the root posts the `explain more` ask the line names and answers in plain
words with `cc-present reply --block <the card's waits id> --body <answer>`, which
replies on the board and under the Slack card with the link back to it.

A tap reaches the root as a `slack.action` directive. The root answers `Merge` with
`cci post --drive <drive> --lane root --kind go --pr <n> --text "merge #<n> at <short sha>"`,
and the runner enqueues the prefix on its next pass once a root `go` names every
waiting PR at its current head. Only the clause a `go` opens with names a PR: `go` or
`merge`, then `#<n> at <short sha>` pairs joined by commas or `and`. A PR and sha the
rest of the text mentions, a routing note or ruling posted as `go`, and a `go` a root
`withdraw` answers by `--re` let nothing merge. The root answers `Hold` with `mq.py hold <n> --reason "<why>"`. `Other` opens a Slack modal, and
the text the owner types there reaches the root as the card's capture. It merges nothing,
and the root classifies it as a capture.
An answer covers only the head it names, so a new head asks again. A changed-files
read GitHub refuses logs `FILES-READ-FAILED`, and nothing in that prefix enqueues
until it reads.

**Graphite down, merge directly (legacy `graphite-stack-enqueue` only; never Trunk).** On
`trunk-stack-enqueue`, Trunk failures or unreadable queue states stay `BLOCKED` or
`UNKNOWN` and never trigger direct merges or automated orphan/own-red/flake recovery;
native retry is a manual `stack-enqueue --flake` after inspecting verification. On a
legacy Graphite queue, Graphite reads down when no batch has started in 15
minutes while PRs are queued, or its two newest batches both timed out. The runner then
posts `GRAPHITE-DOWN since <time>` to root and the landing desks. It squash-merges each
stack it would have enqueued, once it is green, approved, and unheld (owner b530f5d). It
is the only merger, and one merge is in flight at a time (owner, 9:00 PM Oct 29). A stack
behind the latest `dev` is rebased by the runner itself (root #197237): each PR replayed
onto the tip with `git replay`, kept only when its own patch is unchanged, pushed in one
atomic push leased on the admitted heads. The rebase push dismisses the approval, so the
runner waits up to ten minutes for the review bot's approval again. A conflict, a changed
patch, or no approval gets one blocker per head to the PR's lane and the restack lanes.
A lone PR merges by `gh pr merge --squash --match-head-commit`, a longer chain by `gh
stack link` and one all-or-nothing `gh stack merge --squash`. The runner waits for each
squash, and each PR posts `MERGED #N at <head> direct as <squash>, Graphite down,
b530f5d`. An incident fix's plain squash takes the same turn and the same rebase. The
overflow stops meanwhile, and `GRAPHITE-UP` follows the first batch Graphite starts after
that time.

**Five minutes from go to queue.** A green PR a root `go` names at its head is in the
merge queue within five minutes of that go. A stack the passes leave out that long, unheld and
unrefused, goes straight into the queue through the runner's overflow, which posts a QUEUED
report naming itself (owner 9524276). The overflow sends a stack in whole once any PR of it
is due, and a blocked enqueue five minutes old no longer keeps the stack out. The runner logs one `GO-UNQUEUED` per head still
out only when its own newest enqueue of that head since the go ended blocked, failed,
stranded or unsettled, or went in and the queue then evicted it, never for backlog, naming that
outcome or the eviction's time and reason. A head the queue evicted before any enqueue gets one
blocker to the root and its lane naming the eviction, and while Graphite is down it merges
directly like a head the dead queue still holds. Every head a go or an exemption admits
carries, from the pass that admits it, exactly one `QUEUED`, `REFUSED` (naming the gate line or
the check) or `BELOW-BLOCKED` record per change of its outcome, and none while it stays the
same; one no branch of the pass settled posts `REFUSED unsettled:` to the root naming the last
branch that saw it, and a pass that gates nothing refuses each admitted head with
`gating off:` and why (owner, 5:16 AM Oct 30). A go'd head whose queue draft closed unmerged is
dequeued once and enqueued afresh (`ORPHAN-RELEASED`), never `--recover`ed. The root clears that one
thing in the same turn or spawns the lane that does. When the review bot's newest
`ai-review` run on that head reads Held, the line says the bot holds it on N blocking
findings, and the owning lane gets one record per head to fix or accept them whether or not
an enqueue failed; a Waiting for restack run reads as waiting for a restack push. Neither is
a missing verdict. Every landing pass posts one `state` record to root on topic
`landing-state` with the queued, waiting, held and refused counts, so root never asks. A missing
gate record is never the reason: a fidelity or finder carry at the new head (a restack
carries its grade, below) is that gate's record there, and nobody re-grades it.

**The landing desk hears the landing runner's routine records.** When
`landing.desk_lane` in `runner.json` names a lane, one or a list, the runner's
`QUEUED`, `LANDED`, `TAP-CARRIED`, `MERGE-ASK`, `BELOW-BLOCKED` and `FORK-DEFERRED` records go to that
lane, each `landing.board_lane`, and the PR's owning lane, each read as its live
successor, and not to root. A ledger row that settles landed with no enqueue or squash of the
runner's own, queued by hand or landed in a batch it did not enqueue, gets its own `LANDED`
record the pass the ledger reads its squash. A `GO-UNQUEUED`, `GO-TAP-MISSING` or `REFUSED` for a PR goes to each
`landing.go_lanes` lane, the desk lanes and the owning lane, and to root only when the owning lane is dead. A `LANE-GONE` for a lane goes to root every time, and to those lanes and
the gone lane too after the first, since only root relaunches a lane. Without
`landing.desk_lane` they go to root.

*Prevents Oct 26 11:33 PM: a go'd, approved, all-green PR sat out of the queue for 45
minutes because no desk checked how long a go had waited. Oct 27 7:07 PM: the bot held
a green PR on 2 blocking findings five minutes after its push, and the runner and root read
it as no verdict for 19 minutes. Oct 28 12:31 AM: repeat `LANE-GONE` records for two
lanes went to the desks only, and root, the one lane that relaunches, learned 25 minutes late twice.*

**A PR left on a legacy Graphite base is resubmitted.** Each pass, the runner takes the lowest open
drive PR whose ledger base starts with `graphite-base/`, where a landed parent on a legacy
Graphite stack leaves it reading conflicted or stuck, and ships its branch as it stands with
`ccx vcs ship --branch <branch> --tip-only --no-commit` from the landing checkout. It runs on
a stack worker beside the landing work, one at a time and once per head, and a `note` naming
the outcome goes to `landing.desk_lane`, else root.

With `landing.merge_ask_surface` set to `board`, the root asks every merge question
on its own board, and under `every` the runner checks only heads a root `go` names.
It bars every other open PR like a held one: no `stack-enqueue` call, `--check`
included, names it as a top or sweeps it in, and a go'd PR stacked on it waits
(root #131119, #131287), with one note to the root per stack naming each parent
that still needs its go, posted again only when that set of parents changes. After the go'd trees enqueue, the pass's one
`ccx vcs pr status` read routes those PRs instead: a conflict, red CI, or requested
changes goes to the lane as that head's blocker, and a landable head runs the
accept checks, so its `MERGE-ASK` still reaches the root's board. Each tree the pass gates
enqueues right after its accept, while the trees after it are still being checked.

Every push of a merge-triage block goes through
`board_rows.py --config <runner config> <block.json> -- cc-present update-block <args>`,
and `--check` runs the gate alone. A row, a triage item `merge-row-<pr>[-<sha7>]`,
fails when its hint's `commit <sha>` is not the PR's GitHub head, when its `textAt` (the
time its words were written, which the writer sets on every edit) is not after the
head's commit, or when the PR is closed, merged, or ruled superseded or closed by a root
record. It also fails when it lacks a field the owner decides by: a plain-words label and
the facts `For users`, `Differs from plan`, `Risk`, `Finder`, `Build (CI)` and `Gate`,
the Slack merge card's fields (root #131642). A row whose recorded verdict the push
would hide fails too: its id decided under another triage block, or its PR decided
under an id the push drops with no root record that re-keys it (root #131681). A head
with no finder review is a `WARN` line; one with no fidelity grade fails as `ungraded`. A finder review counts in any form lanes post,
`finder: ok|major|issues <sha>`, `finder [<pr>] issues <sha>` without a PR ref, or a
codex reply saved as `finders/<pr>-<sha7>.md` in the drive's state directory. Any failing row stops the push; `FLAG` lines
name rows whose first sentences match, for the root to merge or reword. The push hands
cc-present a copy without `textAt`. Owner, 1:55 PM Oct 26: "if theres stale PRs etc on
herr why have you kusr been sititg here waiting for me insyea dif oroactively
sddresijg them, orgsnzijg the bosrd better etc? ficnit sns your toolijg".

The gate is the default on every push of a block the owner taps, and nothing reaches his
board around it. It also refuses a row that asks a tap he already gave or refused
(`approved`, `re-ask`, `rejected`), a row no fidelity grade settles (`ungraded`,
`unsettled`), two rows he would read as one (`alike`), a blocker or one-tap claim over a
landed or rejected PR, and a landed PR left in any list; the script's docstring lists
each refusal. A refusal goes to root and the row is fixed before the push, never pushed
as it stands.

His tap carries. With `landing.merge_ask_surface` set to `board`, the landing runner
carries it across a restack itself on every pass, and no lane carries a tap by hand.
Take an approval on a `pr-groups` row at an older head of an open PR, with no verdict at
the PR's current head. The runner moves it to the current head with `cc-present verdict
carry` when records chain the two heads and call each move patch-identical. A lane's
`head` record reading `<new> (was <old>): restack, range-diff patch-identical` is one
such move, and so is each entry of a multi-head record (`cci post --head <pr>=<old>..<new>
<note>`) whose note, or else whose text, says patch-identical. A fidelity grade of `<new>` that names `<old>` anywhere and says patch-identical,
tap carries, no new behavior, test-only, or pure replay in its first sentence, with no added
behavior, is another. One
`TAP-CARRIED` note naming both heads goes to root and each lane `landing.board_lane`
names, one lane or a list, and a patch-identical carry onto a head no go names also draws the
runner's own `go` record for that head, so root posts none. A new head that a record names but no such chain reaches is a
fixed version, and its tap stays at the old head. One `TAP-NOT-CARRIED` note to the same
lanes says the board needs a new row at that head, and gives his tap's Pacific time, the
heads since it, and the sentence of the record that names the new behavior. For a review fix, the
root posts `go #<n> at <new head>` naming the fix, and the runner then carries the tap to
that head itself, citing the fidelity grade and the go, so the row lists his approved head
in `carried` and he is never asked again. A head that
adds behavior asks again. The re-asked row's `since` holds `changed` and `because`, and
`board_rows.py` refuses it (`since`) without both, then opens its `change` with "You
approved this at <time>. Since then: <what changed>. Shown again because: <the behavior
change>." Owner, 6:59 PM Oct 29: "if ur asking for a PR tap that Ive alrady apporiove dyou
ned to tell me whats changed / why ur showing it gain". A stack whose every row asks
again this way, each moved only by a fix with no new behavior, pushes as one row at its tip:
the row lists the others in cc-present's `stack`, and his one tap decides each at its own head
(root #194387, his 7:00 PM Oct 29 "all 3" on land-faster). Every verdict is read live
from the cc-present store (`cc-present outcomes`) and the root's `go` records at the
moment it is used; no writer, row, card, or dashboard keeps a copy. Owner, 12:28 PM Oct
27: "for each Biggest blocker, how are you fixing it structurally for future e.g. the
taps never showing up on board for approval".

*Prevents Oct 26 11:26 PM to 12:15 AM: the board asked three times for taps he had
already given and once for a head he had rejected, because rows were pushed from a
cached approved list and a root go at a moved head never reached the row. Prevents Oct
27 to 12:20 PM: seven PRs he had tapped sat 3 to 4 hours after patch-identical restacks
moved their heads, because the runner saw no tap at the new head until a lane carried
each one by hand.*

A repository the runner's own GitHub account owns, the login `gh api user` prints,
lands once green under every merge mode, with no card (de6c88c). A reserved touch
there still asks.

A head whose own diff matches a reserved row's `ops` waits for the owner in every
mode; a grader's `touches <key>` line alone never holds a head. Its `MERGE-ASK` names
the row's key and the first sentence of its words. The card quotes both, and the
owner's tap on it is the authorization. After a Merge tap the root runs
`rulings.py authorize --drive <drive> --row <key> --action <the tap's ts>`, whose
`authorizes:<key>` answer covers the row because `check-pr` then stops naming it.
The ts is a Slack card's `action_ts`, or the `at` of an approved merge row on the
root's cc-present board, which a carried tap keeps; a board row must quote the key
and its words like a card, and `authorize` records the owner's answer for a board tap.
An owner's pick on a board card whose text quotes the row's words authorizes too:
`rulings.py authorize --drive <drive> --row <key> --block <choice block> --option <picked option>`,
or `--block <approval block>` alone for an approval, reads the card through
`cc-present block show` and records the owner's answer when the newest answer on the
block is that affirmative pick.
A root `go` never covers a reserved row, whatever it names or whenever it was
posted (C224).

A red trunk is an incident from its first red build, never a label (owner, 12:06 AM Oct
27: "this si the kiond of thing that should ahve been caight way earlier but fix it now").
With `"trunk_check": "buildkite/test"` in `landing`, every landing pass reads that status
over the trunk's last 50 commits. A red run whose failed jobs include one a PR build also
runs posts a `DEV-RED since <time>` incident to root and drive-watchdog on the `dev-red`
topic, naming the jobs, the first red commit and the PR that landed it, saying no PR can
land until it is fixed, again every 30 minutes while red. The runner holds no row and
the queue keeps running: its required checks hold what cannot land. A run whose failed jobs no PR build runs posts `POST-MERGE-RED`: landing continues
and a fix is owed. `DEV-GREEN` posts once when it passes. The fix lane names its PR with
`cci post --topic dev-red --pr <n>`. With `landing.ci_build` set too, each newer finished
trunk build is read once and compared with the one before it: a job name that fails or
soft-fails more often in the newer build posts one `POST-MERGE-RED` to root, each
`landing.desk_lane` and the lanes of the PRs that landed between the two builds, naming the
build, the jobs and those PRs, so a build that passes only because its failing jobs soft-fail
still reads red. While DEV-RED stands, `board_rows.py` keeps every
tap row on the board and opens the merge card's prompt with `dev's test build is red since
<time>; nothing lands until #<fix> fixes it.`, and the pack refuses a READY report or a
root ruling that calls a head "green except" a job, a job "known red", or a trunk "red by
design".

A green head whose only passing build is Graphite's "Redundant within its stack" skip
is not ready. With `landing.ci_build` set, as in
`"ci_build": "tools/ci bk api /pipelines/{pipeline}/builds/{number}"`, the runner reads
each passing Buildkite build at the head once. It grades such a head `unproven:
redundant skip, child #<n> <state>`, sends that blocker to the lane, and leaves the head
and every PR above it out of the merge card and the enqueue. Graphite skips every
non-tip branch of a stack, so the head is ready again once a passed full build of the
skip's own pipeline runs on a commit that contains it: a build at the stack's tip, or one
the PR's lane names in a `full run green <build>: #<pr> #<pr>` note or report. A failed
build at the head itself always wins: the head reads `unproven: its own build <url>
failed` until it passes.

With `landing.ci_grade` also set, no lane hand-rolls that full build. When no build at the
tip covers the head and none is still running there, the runner points a `ci-grade/<pr>`
branch at the head, which has no open PR for the optimizer to skip, and runs the command
once per head with `{commit}`, `{branch}`, `{pipeline}` and `{pr}` filled in. The command
starts one build and prints its JSON, as in
`"ci_grade": "tools/ci bk api -X POST -d '{\"commit\":\"{commit}\",\"branch\":\"{branch}\",\"ignore_pipeline_branch_filters\":true}' /pipelines/{pipeline}/builds"`.
The head reads `unproven: redundant skip, ..., so full build <url> grades <sha>` until
that build passes. A start that fails logs one `CI-GRADE-FAILED` per head and is
tried again next pass.

Incident fixes still land by plain squash under every mode, and a `checks_wait_for_go`
pause never stops them. An approved, green fix merges once no open cci `hold` record, read again
just before `gh pr merge`, names it and `check-pr --incident` names none of
`RULINGS-UNCITED`, `RESERVED-NO-ANSWER`, and `TEST-REMOVED`. A head it names one of them
for is refused: one cci `RULINGS-REFUSED` blocker to the PR's lane and the root, and the
fix merges in the first pass they clear (45200f2). Neither grade gates it: a fix
whose head has no counted fidelity ok or no finder ok 30 minutes after it lands
logs `UNGRADED-30` once (d968e1d).

*Prevents a fixed landing policy the owner never chose. On 2026-10-21 the owner ruled
(1fe20ee) that the drive asks at the start which of the three modes it runs in, and
that each per-PR ask is a card with buttons, never a blocking `AskUserQuestion`.*

The runner posts no commit status and adds or removes no label. Its check registry
alone enforces holds, rules-review, the rulings, fidelity, and the finder: no required
check carries them, so a hand `gt merge`, a web-UI enqueue, or a hand adapter call
bypasses all of them. The pack's `rulings_at_merge` hook runs `check-pr` on a
`gh pr merge` in a drive session and advises with each cause (0afd2f6). An incident
lane's merge is the exception: the hook refuses it with `RULINGS-REFUSED` while
`check-pr --incident` names `RULINGS-UNCITED`, `RESERVED-NO-ANSWER` or `TEST-REMOVED` (45200f2).
The root reads `mq.py why <n>` before any of those.

A `hold` label on a PR holds it too,
with every PR stacked above it. The enqueue names every top of the prefix, since
the stack-enqueue adapter refuses a green PR the batch would carry unnamed. A
`held` refusal waits for the root and is never routed as a red.
The runner verifies an enqueue only when `reconcile` records every prefix row
as `landed` by squash on the base. The landing desk never enqueues beside it.
Never hold a ready prefix behind another stack's landing under D19.
The runner enqueues a ready stack as one batch, never its first PR alone, and never through `merge` or `gt merge` on one PR; a stalled queue head gets `mq.py why <head>`, then `mq.py recover <head>`.

The accepted policy is a record seeded from config as `prefix` at #28601's
revision. The root changes it with `desk-runner.py policy --config C --key L<n>
--landing prefix|whole --revision REV --source TEXT --supersedes <current revision>`.
A command naming another predecessor is rejected with `STALE-POLICY`; a
stale-checkout ruling such as L260 cannot replace the accepted rule that way.

Under the `label` adapter, one label on the prefix top enqueues the prefix as a
batch, and the desk mirrors held PRs as `ledger.py hold`
rows under D1 so `label` refuses them too.

Lanes never enqueue under D1. For priority PRs, the root reads `mq.py why` for each
and sets `mq.py priority <n> 0` in the same turn. Priority approval covers only
the named head under D1. The desk records outside enqueues on refresh as
`in the queue, labelled outside the desk`.

*Prevents a green tip enqueueing a red parent, an ejected head entering the queue
again unchanged, and a green priority PR waiting for the owner to queue it by hand.
On 2026-10-21 the owner moved drive machinery out of the monorepo: #41528 removed
stack-enqueue's rules-review gate, its drive-scoped dequeue, and `--flake`, and the
owner ruled that the long-running merge queue carries all three, behind a repository
adapter, with no monorepo wrapper skill.*

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

**D10. Record reports immediately; reconcile every three minutes.** The landing
runner runs D3 every 180 seconds by default, without waiting for a report. The
landing desk types reports, runs refresh/watch/P0 and red routing, records holds,
and keeps stale, summary, shards, and train duties. It never enqueues beside the
runner.

Each desk pass reads all PR numbers in one `ccx vcs pr status <n1> <n2> ...` call
and the Buildkite build list, never one REST call per PR. The pack's `buildkite_budget`
hook blocks a Buildkite poll under 30 seconds and an unpaced per-job log dump; one
`bk build view <build> -p <pipeline> --json` call and `bk job log <id>` for the one job replace
them, and a lane that needs every plan log of a release reads them in a loop that sleeps 2 seconds
or more between fetches. The same hook warns on a GitHub poll under 5 minutes: a lane loop over `gh` or `ccx vcs pr`
reads, or a `gh run watch` or `gh pr checks --watch` at its 3 s or 10 s default, takes `--interval 300` or reads the drive's cci records instead. Stagger desks and shards by a minute at `:00`, `:01`, and `:02`. One refused prefix never stops another;
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
minutes. The landing runner owns every enqueue.

Stagger desks and shards by a minute at `:00`, `:01`, and `:02`.
Read all PR numbers in the pass with one `ccx vcs pr status` call and the Buildkite
build list. The refresh lock is keyed on the ledger, so shards never race a sync.

Lanes keep reporting to `landing-desk`; the main desk types every message in, uses
D10's report path, and alone sends the root the summary. *Prevents one desk's pass
growing with the board until its pass takes 20 minutes.*

**D13. Register each lane's stack when it starts and whenever it opens a PR.** The root registers the drive with `drive.py start --ledger <id>`. The pack's PR hook then registers every PR a drive session or Orca worker opens under the lane's name. It records the head when the command prints one. This covers in-process subagents and teammates at any depth; `orca-launch.sh` passes the drive to Orca workers, resolving it from the Orca run when the desk runner, which holds no session, launches them. A session no drive claims prints one stderr line from `drive.py record`. The same hook settles the row when a drive session announces a landing with `cci post --kind landed --pr <n>` or closes a PR with `gh pr close`: `drive.py landed` proves the squash or the close against the trunk through `ledger.py landed`, so the row leaves the open set in that step.

Lanes still register their branch at spawn with `ledger.py register --ledger <id> --lane <name> --branch-prefix <branch>`, and still `report` verdicts; a value ending in `/` or `-` is a namespace covering a stack, any other value one exact branch. Run from the lane's worktree, `register` also records that worktree when its native GitHub stack (`.git/gh-stack`) or legacy Graphite stack holds the branch, and every `refresh` then tracks each open PR on that stack, including branches `ccx vcs stack new` cuts later, so a stacked PR needs no hand registration. `register` refuses a prefix that covers the owner's whole `<user>/` namespace or already matches open PRs another lane tracks or that predate the ledger, naming those PRs and suggesting the exact branch, `<user>/<lane-slug>-`, or `--pr`; `ledger.py unregister --ledger <id> --lane <name>` removes a prefix and drops the untouched rows only it pulled in. To un-admit a PR no drive lane owns, `ledger.py unregister --ledger <id> --pr <n>` removes its row whole, so the runner neither mirrors a `fidelity` status onto it nor gates it. Hand-register a PR only when the hook's context line says it was not recorded, using the command it gives. When the root moves a PR to another lane, it runs `ledger.py reassign --ledger <id> --pr <n> --lane <new>`; `route` and `refresh` then address the new lane, and the row's `reassigned` field keeps each move. Read recorded PRs with `ledger.py list --ledger <id> [--lane <name>] [--open] [--json]`.

Each refresh makes one `ccx vcs pr state --repo <repo> <PR numbers> --lane-prefix <prefix>` call with every row's PR number and any explicit `--pr` numbers, repeating `--lane-prefix` for each registered prefix. The same read returns the lanes' open PRs from ccx's machine-wide pull request cache. Every discovered PR enters the same batch and takes the same gates as a reported PR; the desk never lists the repository's pull requests.

*Prevents three PRs a lane never reported sitting unmerged for hours, and the 2026-10-06 `yasyf/` registration in ledger `b29c27a` that pulled six unrelated open PRs into the drive for the landing runner to enqueue.*

**D14. Grade a moved or unreported head like any other.** Every tracked current
head takes D3's gates without a report. The landing
runner routes a `BLOCKED` head at most once per head, and only for red CI, a
merge conflict, a changes-requested review or fidelity grade, or a queue
ejection. A pending or running check, a missing approval, or an ejection that
blames a PR outside the PR's own stack is never routed. A PR that is held,
carries the `hold` label, or is stacked above such a PR gets no route until the
hold lifts. A PR based on another open PR's branch gets no failing-required
route unless a status other than a gate's, such as `buildkite/test`, is red on
its own head. A queue drop
that blames a PR outside the stack re-enqueues the same head after the root's
next `go` record naming the PR, and a `flaky-check` drop goes to the restack lanes,
never to the PR's own lane.
It re-runs the gate immediately before creating the route and suppresses a
blocker that already cleared. A merge conflict whose lane has no dispatch Orca
shows active and no session `cci who` lists, but a cci record in the last 60
minutes, is parked instead: the root gets one `PARKED parked:<pr>` line per PR,
and the conflict routes once the lane is back.

Orca delivery can follow later; this is not a recheck at
that later send. Orca lanes receive a relay; other lanes receive
`cci post --drive <drive> --lane landing-desk --kind blocker --topic <pr> --to <lane> --text "<blocker>"`.
The landing desk never repeats these per-head gate messages and retains
`ledger.py route` for red CI and conflicts.

*Prevents two heads that moved after their reports being ignored for hours.*

**D15. Name what the desk is waiting on and ping the lane in the same pass.** The summary's `waiting:` line groups tracked open PRs as `ungraded`, `refused`, `red`, and `held`. An `ungraded` row lacks a label and a grade at its current head; a `refused` row has a label refusal at that head, with the reason in `stale` or `show`. A `red` row has a CI failure or a `dirty` or `blocked` mergeable state; `held` covers desk holds and lane `held` verdicts on the current head.

In the same pass, run `route` and send its messages. D3's merge queue handles every enqueue. `summary` settles landings first, so a landed row never appears as pending; its `--checkout` defaults to the working directory and its `--repo` to that checkout's origin, the rule every `--repo` verb follows.

*Prevents the desk waiting silently while five ready PRs sat unmerged for hours.*

**D16. The green bottom of a stack lands now.** Enqueue the largest contiguous bottom prefix whose PRs are green, approved, and unheld as one batch. Never wait for the top of a stack to go green before landing a green bottom. PRs above the prefix wait on their CI, review, or hold. After a native GitHub stack prefix merges atomically, inspect whether GitHub automatically rebased the remaining stack before routing an explicit restack; when a restack is needed, route the first PR above the landed prefix to its owning lane, which restacks the remaining PRs on a Sprite with `remote-restack.py`.

The landing runner sends this route after verifying ledger `landed` rows, by relay for Orca lanes or `cci post --drive <drive> --lane landing-desk --kind blocker --topic <pr> --to <lane> --text "<restack route>"` for others. The landing desk never duplicates it.

*Prevents the wait for unfinished PRs above a green bottom that the owner ruled out on 2026-10-01, and made the repo rule in the monorepo's #28601: AGENTS.md "Stacked diffs" lands the largest green bottom prefix with `stack-enqueue <any PR of the stack>` and restacks everything above it right after. Restacking children and rerunning their CI after the prefix lands is an accepted cost.*

**D17. An owner-visible change needs its render approved before the label.** A PR that changes something the owner sees rendered, such as a UI, message, or generated document, holds under D6. Use the reason "render not approved" until the owner has approved that exact render at the head being labelled, unless the owner has granted ship-then-fix for that lane. A re-render at a new head needs a fresh approval; the old one covered a different head.

*Prevents a redesigned modal or a rewritten Slack card landing and reaching users before the owner had seen how it actually rendered.*

**D18. A queued PR is watched until its squash is on the base; an ejection is a P0 the pass it happens.**
The desk arms `ledger.py watch --drive <drive>` under Monitor when it spawns and re-arms it on every
expiry. Forward every `P0` line to the root the moment it prints; the root treats it
like a `RULING NEEDED` line and acts in the same turn. The 3-minute refresh is the
reconciliation pass; the watch detects transitions. Stagger desks and shards by a
minute at `:00`, `:01`, and `:02`. Each pass reads every PR number in one batched
`ccx vcs pr status` call and the Buildkite build list.

Every lane that reports a PR runs
`ccx vcs pr watch --lane-prefix <prefix> --until landed` under
Monitor, re-armed on expiry, or in the foreground as `desk-wait.sh 540 "mailbox:<lane>=<cursor
file>" -- ccx vcs pr watch --lane-prefix <prefix> --until landed` with `timeout: 570000`,
instead of ad-hoc polling. An in-process lane never runs that watch as a bare foreground command.
`ejected` or `conflicting` means rebase now. The lane watches until its PRs land.

*Prevents the #27949 incident in Forge-AI/monorepo on 2026-09-30: priority #1 was
green, approved, and queued at 05:29Z, then ejected on a merge conflict at 05:34Z
after a nine-PR stack landed on `dev`. The desk's pass was minutes away and its summary
half an hour away; the lane had stopped watching, and the owner found it first.*

**D19. Land as many independent stacks at once as possible; shape them so they do not conflict.**
Every green, approved, unheld stack enqueues in the pass it reaches that state. The
desk never holds a ready stack for another stack, never waits for another stack's
squash, and never reads an ejection as a reason to slow down. It routes a `conflicting`
or ejected PR to its lane in the same pass; the lane restacks and the runner re-enqueues
the moment the restack is green.

Stack shaping prevents conflicts; serial landing does not. The lane brief states the
shape. A shared-file edit goes in the smallest additive first PR of its stack, in the
file's declared order. A lane whose change touches a file another open PR touches
stacks on that PR instead of racing it. A generated output is never committed, since
the build generates it, which is a repo rule. A stack that still carries a regenerated
artifact rebases and regenerates it on ejection, never resolving the file by hand.

*Prevents the serial landing the owner revoked on 2026-10-01: "the fix is to have as
many independent PR stacks merging as possible, and shaping them carefully to avoid
conflicts".*

**D20. A fast-tracked fix with a `Release-With` line, or any incident fix, starts its release in the pass it lands.**
A fix reaches users when its components are released, not when its squash is on the
base branch. When a ledger row lands carrying the `merge-fast` label and the PR body
has a `Release-With:` line, the landing runner posts one cci `release` record to the
release owner and the root in the pass that reads the landing. The record carries
`--topic <pr> --pr <pr>`, the squash sha, the components the line names, and the
instruction to start that release now. The release owner is `landing.release_owner`
in `runner.json`, a cci `--to` address such as `peer:<session>`, and the landing
process requires it to start.

The release owner starts the release and posts
`cci post --drive <drive> --lane <lane> --kind release --pr <pr> --target <component> --build <build url> --text <release id>`.
The release tooling posts nothing to cci. The orca runner claims each build such a record
names for its lane with `cci claim --for`, posts the applying announcement to peer roots,
and posts the lift asks for each freeze a lane's `hold` record names, as
`reference/release-lanes.md` shows; the lane posts only the release and hold records.
A `release` record on the drive from another lane, stamped after the landing, that
names one of the components with `--target` or the PR with `--pr` settles the landing.
A `release` record on another drive settles nothing. Until one arrives the runner
posts one `blocker` to the release owner and the root every
`deadlines.release_start_minutes` after the landing, 10 by default, each with the
minutes elapsed.

The root ends them by starting the release and posting that record. When the release
is in hand another way, the root replies to the release record or a blocker with
`--re <seq>` or `--resolves <seq>`. A record cci refuses, such as one addressed to a
`peer:` session that has ended, reaches the root as `RELEASE-FAILED` once per step
and is posted again on the next pass. So does a post that prints no sequence. Each
record carries `--id release:<pr>:<step>`, so a repost returns the stored record and
no step is posted twice.

An incident fix owes its release whatever its label. A landed row whose lane is an
`incident-` or `outage-` lane or a fix lane on the incident clock, or whose PR an open
incident names as its repair, owes a release in the pass that reads the landing:
components from its `Release-With:` lines, or, with none, the release its squash
needs. While an incident clock runs, the step is `deadlines.incident_release_minutes`,
3 by default, instead of `deadlines.release_start_minutes`. An incident landing settles
on a `release` record carrying `--build` and `--target <component>`, or on a root
`--resolves` naming the release record, a blocker, or a RELEASE-HELD-AFTER-LANDING
line; a root `--re` reply does not settle it. A `fix-live` record from another lane
that names the PR or a component settles any landing. An open root `hold` record
that names a landed incident PR, or is addressed to the release owner, is answered
by the runner with one
`RELEASE-HELD-AFTER-LANDING #<pr>: landed <sha> at <time>; its cut is owed now, the other PR gets its own cut when it lands`
line, and the blockers keep their cadence. Only a `hold` record is a hold: a `decide`,
`decision`, or `go` that mentions a PR or says "wait" is not, and a root `--resolves`
on the hold ends it.

Once the record that settles a landing names `--build`, the runner reads
`tools/ci release live <id> --json` each pass, for the release id `releases/<id>`
its text names or its build number, and posts exactly one
`RELEASED <component> <version> (<build>)` record, `--kind fix-live --target <component> --build <url>`,
keyed `release:<pr>:live`, to the root alone once every component
reads live. It is the only publish line the root forwards to the owner.

A cut has a clock too. A cut order is a root `go` or `decision` addressed to a
release lane that carries `--target <component>` and whose text orders a cut or
`release start`, or a D20 `release` record with no build yet whose landing names
`Release-With:` components. Only a cut order opens a clock, and its components come
from those refs, never from a word of the order's text. A root record that mentions a
cut in prose with no `--target` opens no clock, posts no CUT-SILENT, and launches
nothing. The clock closes with no line once the landing it holds settles, once the
root posts `--resolves <seq>` on the order, or once another lane's `release` or
`fix-live` record since it opened names one of its PRs. An order that `--resolves`
the release lane's own `release --build` or `refuse` record is answered by it.
With no `release --build` or `refuse` record from that lane within
`deadlines.cut_minutes`, 4 by default, the orca runner posts
`CUT-SILENT <lane> <component>: no CUT or refusal <n> min after GO #<seq>; hand path: tools/ci release claim <component>/<env> --purpose incident --reason '<trigger> cut by hand' --until <now+2h> && tools/ci release start --manual <component> --env <env> --commit <sha> --emergency`
to the root once. The incident claim makes the manual release the stack's only
writer and lets it into another release's Watch window; `--emergency` only skips
red dev checks. The runner posts the line and queues `manual-release-<component>` on opus xhigh from
`reference/manual-release-brief.md`. That launch waits one minute after the
CUT-SILENT posts. A root record that `--resolves` the CUT-SILENT, or a release
lane answer or closing record arriving in that minute, fails the launch before
it dispatches and logs `WITHDRAWN cut:<key> manual-release-<component>: <why>;
nothing was launched`. A later `refuse` record saying a release
`already ships <component> at <sha> or later` is relayed to that lane in the pass
it arrives.

The runner reads the label from the ledger row's `labels` and the body with one
`gh pr view <pr> --json body` per landing. Each landing is judged once, by the first
pass that reads it, and the landing desk's store keeps the set it has read across
restarts. The desk's first pass records every landing it reads as history and sends
nothing, so a restart or a plugin upgrade never judges an old landing again; a landed
row with no `landed_at` is not read until it has one. A landing
from before the runner process started, first read after a restart, closes with no
record when `tools/ci release live --json` shows its squash or a later commit running,
for one of its `Release-With:` components when it names any.

*Prevents the Oct 20 sand holds. Incident fix #41063 landed by plain squash at 10:52
with no `merge-fast` label, so D20 owed nothing; the root held its cut for unlanded
#41071 and gave five cut orders, and #41063 reached users 24 minutes after landing.
The owner asked five times whether sand was out and got cut orders, because the
only publish truth was a lane reading `sand/latest.json` by hand. The 9:54 cut order
named no watcher, so a refused start sat unseen for six minutes.*

*Prevents a landed fix waiting on a release nobody started. On 2026-10-13 monorepo
#36642, the fix for a customer-facing break, landed at 15:42Z through the merge queue
with the `merge-fast` label and a `Release-With` line naming `sand`. The release
owner's session was busy, the landing runner said nothing about a release, and the
root started the `sand` release by hand about 50 minutes later.*

**D21. A format change lands readers first.** A PR that changes a format another build
reads, such as a journal, seal, session, manifest, or any stored file or row, is a
writer. Its readers ship first, in their own PR, and that PR's release is live on every
client that reads the format before the writer is enqueued: browser bundle, desktop
app, local daemon, and server. A client that never restarts on its own counts until it
refuses the old build. The writer's body carries
`Readers-First: #<reader PR> live in <release>` for each reader, and never a
`Release-With` that pairs it with its reader.

The writer's lane posts `cci post --kind hold --pr <writer> --text "readers-first:
#<reader> not live on every client"` when it opens the writer, so the landing runner
refuses it under D1 until the root lifts that hold with `lift --resolves <seq>`, naming
the release and its live read-back. The landing desk grades each tracked PR's diff for a
format change and posts the same hold, reason "readers-first: no reader named", on a
writer with no `Readers-First:` line.

*Prevents the breaks of 2026-10-13: the two-tier seal, segment links, and migration
passes went live while old daemons and bundles still read the old format, and resume,
forks, SoFi mounts, Chime, and a reporter's cloud run all failed with "cannot read"
errors that each got a forward patch.*

**D22. A PR whose release must avoid a time window is enqueued only when the window allows it, or under a release hold.** Hold it out of the queue until a release lands outside the window. To enqueue earlier, first post `cci post --drive <release owner's drive> --lane <lane> --kind hold --pr <n> --to <release owner> --text "release <component> only after <time>, <reason>"` on the drive `cci who` names. The release owner leaves that PR out of every release until then. A hold in a lane's brief binds no other session's release train, and one on the lane's own drive refuses the enqueue under D1.

*Prevents the 2026-10-15 rsw-go restart. Monorepo #37836, enqueued to release after the 3-11 AM dreaming window, kept its "no rsw-go release after 2:45 AM" hold in its release lane's brief, and the release-v3 session shipped it in release 6869 at 2:55 AM.*

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
the old session open. The landing process is the drive's merge queue and runs D3, D14, D16, and D20.

Each runner's heartbeat is its alive record, below: its attrs carry the runner's pid,
plugin version, config, terminal, and restart command, and every reader reads the newest
one from cci, so a runner may run on any machine and no reader checks a pid. A `cci post`
that fails stays queued for the next pass instead of stopping the runner. When a
`cci post --to orca-desk` finds no orca alive record from
the last five minutes, the `desk_liveness` hook answers `LAUNCH-FAILED` with the restart
command that record carries, and the restarted runner reads the record from its saved
cursor; a cci that does not answer leaves the hook silent. A Bash append to
`orca-desk.md` in a drive session draws `UNREAD` and the `cci post` form, since nothing reads the file.
`handoff` reads the newest record to see a runner re-execute in place, `drive.py digest`
reads it for each desk's version and liveness, and the stall watch posts a `desk` stall
to root when a runner that posted one in its window falls silent for 10 minutes. No
runner writes a heartbeat or view file; `desk-runner.py show` renders the view on demand.
Each runner also posts a cci `state` record to `drive-watchdog` once a minute from a timer
thread, whatever its pass is doing. It reads `<desk> alive <time> pass <n>`, plus
``running `<command>` for <m>m<s>s`` while a command is in flight, so the watchdog alarms
within 10 minutes of silence and sees a pass stuck on one command. A GitHub, cci or ccx read
get 60 seconds each, and a `stack-enqueue --check` and `rulings.py check-pr` 180 each, since a cold
grade of a copy of thousands of files takes about 30. One that runs over is killed, logged as
`TIMED-OUT` in the runner log and read as a failed command; a killed `check-pr` posts the lane
one `CHECK-PR-TIMEOUT` warning that the head's rulings are unproven, and holds nothing. `check-pr`
keeps what a diff alone decides, the rows it touches, the reserved rows it hits and the lines naming a
scope row, per head, base, diff and set of rows, so a big diff is graded once. It diffs with
git's copy detection, so a file copied from one the base still holds adds only the lines it
changes, and its other lines match no ruling. That diff is kept per merge base and head, and
`check-pr` runs eight heads at a time, since each diffs on this machine. Within a pass the landing runner
reads `cci who` once per lane and a PR's landed state and red-by-design body once per PR.
Right after the gate checks it runs `check-pr` for every head it may enqueue, eight at a time.
A blocker still re-reads its gate just before the send. The
merge-mode check runs before the other checks, so a green head gets its merge card without
waiting for its grades; it still enqueues only once every check passes. A pass that raises logs its traceback, posts one `blocker` to the root naming
the desk and the exception, and the runner goes on with its next pass. When the next
pass raises the same exception, the runner posts that too and exits.

The orca runner rotates every `*.md` file beside `orca.desk_inbox` once an hour.
`inbox-rotate.py` records time and stream-end marks and archives through the newest
mark at least six hours old into `<file>.archive/YYYY-MM-DD.md`, so the first
archival happens about six hours after the first mark.

The root never types an `R<n>` or a clock. A key it picks by hand can collide with one
already held, and a key held twice logs `LAUNCH-FAILED`. `desk-launch.py` mints every
keyed line. It takes one past the highest `R<n>` on a cci record addressed to
`orca-desk`, stamps the Pacific clock, and posts the line to `orca-desk` on cci as
`cci post --lane <lane> --kind go --to orca-desk --text '<line>'`.

```sh
desk-launch.py lane <lane> <model> <effort> <brief> [--now]
desk-launch.py relay <lane>[,<lane>...] <text>
desk-launch.py hold <slug> <owner> <what>
desk-launch.py unhold <slug>
desk-launch.py withdraw <R<n> | unfixed:<msg>:<n> | lane>
```

Each verb prints the key and the line it posted. `lane` then waits for the runner's
`LAUNCHED` or `LAUNCH-FAILED`; the others return at once, and the runner's verdict
reaches the root on cci.

The runner reads these lines only from cci records addressed
to `orca-desk`. A relay line is
`R<n> orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>`; the runner
delivers it once per lane and logs `RELAYED` or `RELAY-FAILED`. A launch line is
`R<n> orca-desk: launch <lane> [NOW] <model> <effort> brief=<absolute path>`; the
runner runs `launch` for it once, `NOW` meaning `--owner-directed`, and logs
`LAUNCHED` or `LAUNCH-FAILED`. A repeated key in
the same lane is one action, and `policy` takes the drive's L keys. An
`orca-desk: withdraw <target>` line,
naming `R<n>`, a case launch's `unfixed:<msg>:<n>`, or a lane, fails a launch the
runner accepted and has not started, stops a launched dispatch with worker-stop, marks
the lane withdrawn, and logs `WITHDRAWN`; `WITHDRAW-FAILED` names a launch still
starting, a target that holds nothing, or a stop Orca refused, and
`WITHDRAW-UNCONFIRMED` a stop Orca took but never showed settled with its terminal gone or
idle within 30 seconds; the root runs `orca-gc --done` on it. A root `cci post --kind withdraw --re <seq>` of a launch record does the same. The root never `SendMessage`s a desk. It
receives cci records over the `cci` channel subscription R9 defines.
`show` and the config's `view` file render state; these inbox files are views,
never authority.

A relay or a root reply posted on cci can be any length and span lines. Post it
whole, as one record, and never shorten or split it. The runner reads the full
text from the file `cci post` writes for a text over 400 characters and relays
all of it, line breaks kept.

Lane results reach the root as `FIX-LIVE`,
`MECHANISM`, `READY`, or `OUTCOME` escalation lines, or by Run message id. Never reconstruct
the latest lane state with `grep ... | tail` across several files: file order can
hide an earlier file's newest lines. Never use `orca orchestration inbox | grep -A`
for this: the listing puts newer messages above the match.

[reference/orca-desk-brief.md](reference/orca-desk-brief.md) owns the config,
commands, passes, acknowledgement contract, escalation kinds, and cutover.
[reference/orca-workers.md](reference/orca-workers.md) describes the adapters.

*Prevents G130's `AmiBake` launch being lost behind a desk handoff, R620 going to a
superseded desk, and GO carrying no start deadline (2026-10-01 audit, Brief 3 and
ranked fix 3).*

**O1. Launch through the runner.** The root runs
`desk-launch.py lane <lane> <model> <effort> <brief> [--now]`, which mints the key; the
runner runs `launch` for its line and starts `orca-launch.sh` detached. No model desk runs lifecycle helpers.

**O2. Settle a launch from its line or receipt.** A `ready` or `unsupervised` line
counts as launched, as does a new dispatch receipt when the log is empty. Other
output produces `LAUNCH-FAILED`; neither a line nor a new receipt produces
`UNVERIFIABLE`, never an automatic relaunch. `unsupervised` escalates separately.

**O3. Keep one Run reader.** Only desk-runner consumes the Run. Each pass takes the
Run's oldest unacknowledged Delivery with `orca orchestration check --run <run>`, reads
`orca orchestration inbox --terminal run:<run> --limit N --json` past its saved cursor
oldest first, and acknowledges that Delivery with `--ack <delivery id> --peek` once the
read is relayed. It persists each cursor advance and expands full pages until the
cursor is covered. Its first read starts at the newest sequence without replaying
read history, and nothing launches or relays until that read succeeds. No other loop may
run `orca orchestration check --run <run>` with `--wait` or `--ack`.

The Delivery decides what is acknowledged. The runner saves each relayed message id
in `facts` under `relayed:<run>`. A Delivery message with no saved id is relayed
first, whatever its sequence; one behind the cursor whose type has no relay is logged
`UNRELAYED` with its sender, type, and sequence. Heartbeats need no record.

*Prevents the coordinator's `You have 32 orchestration messages` prompt of 2026-10-11,
which counted 20 relayed `worker_done` messages and 12 skipped heartbeats. Also
prevents acknowledging mail Orca re-addressed to the Run behind the cursor, which
0.7.60 marked read unrelayed.*

**O4. Only the root answers a worker's question.** The runner escalates every
`question` or `escalation` to root as `DECIDE`, carrying the worker's question
and options. It never answers one itself. The root answers with
`relay --reply-to <msg id>`, or with
`cci post --kind answer --to <lane> --re <DECIDE seq> --text "<answer>"`, which
the runner relays as the reply to that question. A root cci reply with `--re` on a
lane's own record also answers that lane's open question, so the question never
outlives the answer. Only this drive's root answers; a peer drive's reply
relays nothing.

*Prevents `msg_9d11bf1237ab` of 2026-10-16, a Sonnet judge's answer to an incident
worker drawn from its brief alone. It contradicted root decision #89633, and the
worker stalled until the root overrode it.*

**O5. Relay to the current dispatch.** The root runs
`desk-launch.py relay <lane> <text>`, which mints the key; a desk submits
`desk-runner.py relay --config C --key R<n> --lane L --text T [--reply-to <msg id>]`.
The runner checks the current receipt and dispatch. A guidance relay sends the action's
thread id and requests `started <key>` before acting and `done <key>: <result>`
after. Its send proves delivery only; the lane's status replies move the action.
Question-reply actions currently complete on send without those worker acks.

**O6. Never end a session.** The runner never stops, signals, releases, or closes
Claude, Codex, Orca, terminals, PTY daemons, or their supervisors. A settled
worker keeps its session open. No launch receipt or missing heartbeat changes this.
The sweep names each newly settled dispatch once in a `RECLAIM` line with
`desk-runner.py gc` and then the config's `orca.gc` command, and the root runs both
under R195. The root,
not the runner, ends a lane it has decided is done (stood down, superseded, or its
deliverable on disk) with `<orca.gc> --run <run> --done <ctx>`. It never messages
the lane to send `worker_done` first.

*Prevents the 12:35Z kill that ended every session of a drive (release v3, 2026-09-30).*

**O7. Keep acceptance, start, result, and verification distinct.** Actions move
through `accepted`, `started`, `completed`, and `verified`, with `failed` and
`unverifiable` recording failure or missing proof. An `unverifiable` send is
settled from Orca `request-show`/`--retry-request` or the recipient's mailbox.

With neither proof thirty seconds on, the runner resends it on the same thread
every thirty seconds until one proof appears, and logs nothing for a delivered
resend. A lane status on the Run naming the relay's R-number or cci sequence is
proof too. Two lost resends log `UNACKED` with the relay text on the record's path.
The root reads the lane's Run messages, then delivers by hand only if the lane
never acted. Never resend a send that has a receipt, and never resend by hand
before `UNACKED`. `show` renders the record.

*Prevents the Oct 14 l32-pass-v2 relays: the lane read each send and answered on
the Run thread with `done R889`, the runner found no message on the relay's
thread, and it resent each relay ten times before logging `LOST`.*

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
question id as the reply address. The runner has no stale-sender fence; this
rule must not be reported as an enforced runner guarantee.

**O11. Treat a capacity fallback like an ask.** Workers whose `ask` returns
`capacity reached` send `question` or `escalation`. Both escalate as `DECIDE`.

**O12. Pause ten seconds between Run reads.** The orca runner sleeps ten seconds
between passes. The landing process uses its configured 180-second interval.
Model desks retain the 60-second inbox-wait rule.

**O13. A prompt is a bug, and the prompt watch finds it.** `prompt_watch.py` is the one
reader of Orca's `agentWait`, as [Prompt watch](#prompt-watch) describes. It covers the
coordinator's terminal, every registered desk, and every in-progress worker. It
answers no prompt and types into no terminal that waits on one. The runner's five-minute sweep reads the watch's last poll and emits
`PROMPT-WATCH-DOWN` once an hour while that poll is over two minutes old. Stale unread
mail gets one terminal wake per message, typed only when the lane's screen shows the
plain empty prompt. Completed or failed dispatch mail emits `STALE-MAIL`; no sweep
relaunches a worker or guesses an answer to its prompt.

*Prevents lanes sitting for hours on a prompt only their own terminal showed
(release v3, 2026-09-30). Also prevents the hook approval that froze a coordinator and
its in-process lane until the owner pointed at it (orca-remote-managed, 2026-10-10).*

**O14. Refuse launches while load exceeds the core count, and tell the root to move the
load.** The runner reads the 1-minute load before each Mac launch. Above the core count
an Orca worktree checkout times out, so no launch starts. Each ten minutes of it posts one
`LOAD-HIGH` blocker to the root, which moves the load that turn: a pass, backfill, dry
run, or bulk rewrite goes to in-region compute (R15), never waits it out. An incident
launch is never held: it starts under the load and posts one `LOAD-INCIDENT` report. A
launch submitted with `--owner-directed` fails at once with `LAUNCH-FAILED`, and
the root launches that lane in its own session. The pack's Agent hook lets that spawn
through when it is named for the refused lane and, within 30 minutes, the runner posted
`LAUNCH-FAILED … refused on load` for it, or a `LOAD-HIGH` and no verdict for it;
`desk-launch.py refused <lane> --session <id>` prints the record the hook reads. Any
other launch stays accepted, `show`
marks it `HELD` with the load and its age, and after `deadlines.load_hold_minutes`
(default 5) it fails with a `LAUNCH-HELD` escalation and a Run mailbox message. It never
kills a worker to lower load. A Sprite launch never reads Mac load.

*Prevents the load of 103 behind the 12:35Z mass kill (release-v3, 2026-09-30).*

*Also prevents the evening of 2026-10-13. Local passes held the Mac at load 85 to 105
on 32 CPUs, every Orca worktree checkout timed out after 180 s, and four incident
launches failed one by one, the clock's Opus fallback among them, before the root
started that fallback in its own session by hand. Owner: "stop failing on broken
tooling".*

**O15. Preserve the codex and incident launch routes.** `sol`, `codex`, and
`gpt-*` use `orca-launch.sh` on Orca's codex agent, on the standard tier.
Incident launches name `--model incident --effort xhigh`: `gpt-6.1-sol`, a
`--no-parent` worktree, and an explicit terminal command with
`--dangerously-bypass-approvals-and-sandbox` and `-c service_tier=fast`. Keep Orca's runtime defaults unchanged. An unsupervised
launch needs a reporting route, never another launch. Inline lanes still use
`Skill(codex)` or `codex:codex-wrapper`; one-off questions use `codex-ask`.

**O16. Ordinary lanes default to a Sprite.**
The launching model places a lane on its brief's `ccx:` line, with `place=remote` for
a Sprite or `place=local` for the Mac. With no `place=`, an ordinary lane goes to a
Sprite, including lanes without `cpu=high`. Fable, incident, an incident clock
fallback, and a standing desk's `role=desk` or `role=watch` brief launch locally
whatever the line says. [Lane brief](#lane-brief) says what a Sprite holds and when
to choose each.

A Sprite launch needs `orca.sprite` configured. It runs the repository's
`worker-launch.sh` and never waits on Mac load, while a lane placed local still
waits under O14. The runner counts no Sprites and sets no limit of its own: the
provider admits or refuses each one. Missing configuration, a lane name a Sprite
refuses, or receipts proving worker-start never ran fail the launch with
`LAUNCH-FAILED`; nothing starts locally in its place. The proof is read from the
brief's `.worker` directory: no `worker-start.json`, and a nonzero exit in
`prepare.status`, or no `prepare.status` when the runner itself saw the launcher
exit. Any other Sprite launch without a ready line may have left a remote worker.
The runner launches nothing locally, marks the launch `unverifiable`, and logs
`SPRITE-UNVERIFIABLE` with the receipt directory and any Task and Dispatch;
inspect them before relaunching. A Sprite launch that prints nothing is
`UNVERIFIABLE` and is never relaunched.

Model and effort are the same on either route, and the runner sets no Codex service
tier on either. Remote API keys travel only to the worker process.

*Prevents the 01:00 cohort's 152-202 s launches at load 54-63 on 32 cores
(release-v3, 2026-10-09).*

## The alerts desk

Production monitor traffic belongs to one long-lived `long-running:lane`, `alerts-desk`,
model sonnet, effort low. Spawn it beside the landing-desk whenever the drive deploys,
applies, releases, or migrates. `scripts/monitor-watch.py` owns the polling and the
dedup; `reference/alerts-desk-brief.md` is the desk's brief, ready to paste.
`reference/active-alert-brief.md` holds the executor's runbook and its lane briefs.

**A1. Report monitor transitions, and nothing else.** The desk runs
`monitor-watch.py watch --alert-drive <drive>` in the foreground under
`desk-wait.sh`, so its mailbox ends the watch, over the drive's
monitors, by tag glob such as `release-target:*` and by named id. It messages the root only on a move into Alert,
Warn, or No Data, or a recovery to OK: monitor id, name, transition time, and a
one-line first read. Under R24 that first read names any `production-operations`
entry from the last 24 hours on the monitor's target. Never on an unchanged state or
a timer tick. It owns no fixes and posts nothing to Slack.

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
parallel, one dispatch per lane per iteration. Lanes never enqueue under D1.
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

## The drive watchdog

`reference/drive-watchdog-brief.md` is the brief, ready to paste.

**W1. One lane watches the whole drive against the plan.** `drive-watchdog`, model
opus, reads every lane's cci records and judges each for off-plan work, distractions,
excuses or deferrals, parked lanes, and root drift. It posts each finding as a
`decide` to the lane and the root with the correction and the rule it cites, and
posts again to the root when the lane has not corrected within 15 minutes. It writes
no code and never stops a lane; the root acts on its second post that turn.

**W2. The watchdog keeps the retro.** Each finding is one entry in its cc-notes log,
tagged `[step <n>]` with the plan step it slowed. That log is the source of the owner
table's last column. Every 30 minutes the watchdog posts the root the top three things
slowing the critical path. It feeds no dashboard (owner instruction of 2026-10-18).

*Prevents drift found only when the owner asked. The owner ruled on 2026-10-16:
"its job is to monitor all other progress via cci and figure out what is going off
track from the original plan, what distractions are happening, what excuses are
being made, and to rectify/course correct those things in real time."*

**W3. No sweep finding or owner pick is lost.** A sweep or audit lane posts each finding
with `bin/findings.py post --sweep <lane> --owner <lane> --verdict <word> [--pr N]
[--line path:line] <text>`, a cci `defect` that opens `[finding:<lane>]`, addressed to the
lane that owns the fix. `findings.py sync` keeps every finding, and every pick or
write-in the owner gives on a board card, as a row of the drive's findings ledger
(`findings.json` in the state dir names it). A row is open until a later `head` or
`landed` record names its PR or seq, or a pick's card, which makes it fixed; an answer
makes it routed and a resolve without a fix makes it closed. `findings.py open` prints
the open and routed rows by sweep, `track <seq>` adds an older record, and `close --why`
settles a one-shot pick nothing will build. A lane that builds a pick names the pick's
seq or card in its `HEAD` record. The fidelity pipeline grades a head `missing` with no
model call while its PR has an open finding, the drive watchdog raises a finding open
two hours with no fix head to root, and the dashboard's What failed section lists the
open rows per sweep. *The owner, Oct 30 3:06 AM: "and probably add some tooling for
these weeps were doing so their findings font get lost" (root #205185).*

## The stall watch

`scripts/stall_watch.py` finds work that stopped moving. Add a `stall` object to
`runner.json`, such as `"stall": {"every_minutes": 5}`, and the orca desk runner starts
one detached pass every `every_minutes` once the last pass has exited. The runner reads
the object on every pass, so adding or editing it needs no restart. Each pass logs to
`stall-watch.log` beside `orca.desk_inbox`.

**S1. Each stall reaches root once, with its owner and next step.** A pass reads the
drive's cci records, the landing ledger, the holds, the landing desk's store, the root's
board, and the root's task list. For each stall it posts one `urgent` record from lane
`stall-watch` to root and the owning lane:
`STALL <kind> <subject>, <age> min: <what stopped> | owner <lane> | <next step>`. A stall
posts again only after it clears and comes back, and never sooner than 30 minutes after
its last post. A `red` stall posts as a `blocker` to the PR's live owning lane alone, and
again every 30 minutes while it stands; the owner is the lane the newest root record that
reassigns the PR names, else the ledger row's lane, and a red no live lane owns reaches root
once per head. Reds whose failed job logged only Buildkite's benign 404 probe fold into one
`report` line to root a day. A stall whose owners are all desks, lanes named `<x>-desk` or
`<x>-desk-<n>`, goes to those desks and not to root. A stall the landing desk owns goes
to `landing.desk_lane` when `runner.json` names one, else to root. Finder and fidelity
stalls never reach root. A `stale` or `board` stall spares root while a lane in
`stall.quiet_lanes` posted about the PR in the last 10 minutes; that list defaults to
`landing.go_lanes` and `landing.board_lane`, each matching its numbered successors. Board
stalls in one stack post as one record, on the stack's lowest PR.

| Kind | Fires when | Minutes | Owner |
|---|---|---|---|
| `go` | a root go names a PR's head, no `QUEUED` record does, no hold covers a PR below it, and no standing refusal names a PR of its stack | 10 | the first PR in the stack, from the bottom, with no go (root, once it is graded and its next step is root's go; none while it is ungraded or waits on the owner's tap, which its own row's stall names), or with a red, conflicting, or unfinished build (its lane), else the landing desk; one record per blocking PR |
| `refusal` | the desk runner or a landing desk lane refused to queue a PR a go or tap names: a needed restack, a conflict, an untracked or closed parent, a lost Graphite parent, mergeability GitHub is still computing, or `FOREIGN`, `TAP-REQUIRED`, `CONTRACT-MISSING`, `PARKED`, `UNOWNED`, `NOT-QUEUEABLE`, `ENQUEUE-*`; no `head` or `--re` answer from the owning lane, no `QUEUED`, no closing record, and the PR still sits at the refused head | 10 | root and the lane the refusal names as `owner lane <lane>` or `<lane>: #<pr>`, else the PR's lane; root for `FOREIGN` and `TAP-REQUIRED`; the landing desk for mergeability. The record names the refusal's command |
| `successor` | the same refusal still stands | 20 | root, asked to spawn the owning lane's successor to run that command |
| `red` | the head's `landing.trunk_check` check has read failed since the ledger row's `red_at`, held or not; the record names the failed Buildkite job, its first error line, and the build | 2 | the PR's live lane, from the newest root reassignment or the ledger row; root once per head when no live lane owns it |
| `unclassed` | the head's build has read red since the ledger row's `red_at`, and no `red` record whose `head=` names the head classes it | 20 | root and the PR's lane, once per PR and head; the record quotes the `cci post --kind red` command |
| `unfixed` | the newest `red` record at the head classes it `real`, and the PR has kept that head since | 60 | root and the class's owner, once per PR and head |
| `sweep` | a red head has stood 20 minutes, and no `red-sweep` or `red-sweep-<n>` lane is live | 20 | root, naming every red PR and its class |
| `tap` | the owner approved a head on the board, and no go names it | 10 | root and `landing.go_lanes` |
| `ungraded` | a `head` record names the head, its build has read green since the ledger row's `green_at`, and no finder review or no fidelity grade names it | 10 | the graders, or the landing desk when the finder desk runs, naming how long its queue has held the pass; root hears every head a pass newly finds ungraded in one `urgent` line |
| `finder` | a `head` record names the head, no finder review does, and the finder desk holds no queued pass on it nor one running past 25 minutes | 30 | the landing desk when the finder desk runs and queued nothing, or its pass ran past 25 minutes; else the PR's lane |
| `fidelity` | a `head` record names the head, no fidelity grade does, and the fidelity desk holds no queued grade on it nor one running past 25 minutes | 30 | the landing desk for a grade past 25 minutes, else `landing.fidelity_lane` |
| `missing` | the newest fidelity grade naming the head reads `fidelity: missing`, and no lane record answers its seq by `--re` or `#<seq>`, nor a PR comment no bot wrote follows it; the record names the grader and the missing text | 20 | root and the PR's lane, as for a red build |
| `regrade` | that grade was answered by such a record or comment, and no grade has followed | 15 | the lane that posted the grade, or the landing desk when the fidelity pipeline did |
| `board` | finder ok, fidelity ok, a green build, a wait on the owner's tap, and no board row or `merge:<pr>:<sha7>` card at the head, counted from the latest of the two grades and the ledger row's `green_at` | 15 | `stall.board_lane`, root by default |
| `carry` | a tap or go names an earlier head, nothing carries it to this head, the board shows no row at it, and no desk runner `TAP-KEPT`, `TAP-CARRIED`, or `TAP-NOT-CARRIED` note names the head, since that note settles the carry | 15 | the fidelity lane when Sonnet reads a patch-identical restack, else root, to ask the owner again |
| `lane` | a lane with an in-progress task on root's task list stays silent past the time its last record promised, or past 30 minutes | 30 | the lane, or root when the lane posted nothing in the window |
| `ask` | a lane's ask to root is open, and no root record answers it | 15 | root |
| `order` | a root `decide` record relays an owner order, Sonnet reads it as naming a change that ships as a new PR, and no lane it names, or a numbered successor, posts a record citing a PR after it, such as `opened`, `done`, or a `state` with a PR reference; it stops at two hours or when a root record closes it | 3 | root, as `urgent` |

**S2. Python checks the facts, and one Sonnet call answers the judgment.** Sonnet judges
whether a new head replays the tapped one unchanged, whether a quiet lane's last
record explains its silence or promises a time, and whether a relayed owner order ships
as a new PR. One call per pass through the judge library's `batch`, Sonnet at low effort
with Claude Code's own system prompt replaced, answers every open question and lands in
`judge/calls.jsonl` as kind `stall`. A verdict is kept against the records it read, so no
question is asked twice. When the call fails, each question takes its mechanical answer.

`stall.minutes` overrides any row's minutes, and `stall.window_hours` (default 24) bounds the
records a pass reads. `stall_watch.py --config
<runner.json> --dry-run` prints the stalls a pass finds, and posts and saves nothing.

*Prevents approvals that never reached the owner's board, finder reviews nobody
noticed were missing, and a go'd PR the landing desk refused at 2:14 AM on Oct 29 that sat
20 minutes with no escalation, each found only when the owner asked. The owner ruled on
2026-10-27: "in general you need to add a watchdog to long-running which runs sonnet and
looks for stalls."*

## Desk inboxes

These I-rules apply to the landing desk, priority desks, and shards. The owner ruled:

> take all ephemeral stuff out of manual files and ccn, and move it into cci (cc-inbox)

Durable owner rulings, decisions, runbooks, and design docs stay in cc-notes.
The orca desk reads its launch, relay, and hold lines only from cci records addressed
to `orca-desk`, in the `R<n>` form the owner's CLAUDE.md
Incident Turn names. The root posts each one with `desk-launch.py`, which mints the
`R<n>` and the clock; by hand, an incident fix lane is
`cci post --drive <drive> --lane root --kind go --to orca-desk --text 'R<n> (<h:mm AM|PM>) orca-desk: launch <name>-fix NOW incident xhigh role=fix brief=<absolute path>'`.
The root never `SendMessage`s a desk.

**I1. A desk's inbox is the set of cci records addressed to it.** The root posts each ruling
with `cci post --drive <drive> --lane root --kind go --to <desk> --text "<ruling>"`.
Keep text under 400 characters; put a longer body in a file and attach it with
`--path <file>`. Link durable records with `--ccn <id>`.

**I2. Read from the saved cursor at the top of every iteration.** Before any other
work, act on the records printed by `desk-wait.sh` or the cci Monitor. `desk-wait.sh`
reads its own `<desk>-wake` cursor, so the read below prints those records again; act
once per seq. Then read with
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
Here `<seq>` names the standing record being superseded. Rule ids are `#<seq>`.
A desk brief and each summary list live standing rules by these ids with their
answer ids, plus the plan's Decisions, never as a sequence range.

A standing rule handed to a lane as a deliverable still gets its own durable
answer and addressed cci record first. The lane's task cites the rule and answer ids, and
completing the task never retires the rule.

*Prevents the release-v3 "release everything as it merges" rule being lost three
times on 2026-09-30 and 10-01: R312 shared a line with the one-off R311, the
landing-desk-2 brief declared only "L65–L107 standing" and dropped L40, and R348
marked "R312 done" once deploy-experience's `tools/deploy --since` task built
toward it.*

**I7. In-process teammate desks wait in the foreground.** Every Agent-spawned
desk is in-process. Every in-process desk and lane names its own team mailbox as the
source `mailbox:<lane name>=<cursor file>`, which desk-wait resolves on every call from
the live team roster, the root process's own `session-<id>` under `~/.claude/teams/`.
`inboxes.py mailbox <lane name>` prints that path. A numbered successor names its own
lane name, never the one baked into a predecessor's scripts. Run one foreground
Bash call with `timeout: 60000`:
`desk-wait.sh 50 cci:<drive>:<desk> mailbox:<desk>=<cursor file> [<other file>=<cursor file>...]`.

It waits at most 50 seconds and returns on new addressed records, a new line in
another watched file, or `MAILBOX <n> unread`, where `n` counts all unread mailbox
entries. Each new unread entry follows that line as a `== <from> <timestamp>` header
and its full text. It clips displayed records and file lines to 400 characters, ending
clipped lines with an ellipsis; mailbox text is never clipped. Claude Code delivers an
in-process teammate's mail only after its turn ends, never at a tool-call boundary, so
the printed text is the lane's only copy until then: act on it, run step 0 on the
output, then rerun the call in a loop. Run desk-wait as the Bash call itself, or from a
script that passes its whole output through; a `>/dev/null`, `| tail`, or `| cut` drops
that copy.

*Prevents the 2026-10-12 oncall-core-reopen-2 hour: seven root and peer messages sat in
its mailbox for 50 minutes across two `MAILBOX` wakes, while the lane searched for a
team named after the root's session id instead of the root process's team.*
A foreground watch in the same loop appends `-- <command>` to that call, so the mailbox ends the watch.

*Prevents the 2026-10-19 release-v3 night. The comms-desk brief named `comms-desk=<cursor>`
as its mailbox, so its poll script waited on an unused cci cursor, and three root
messages from 4:12 to 5:27 AM never reached it. slack-watch-26 ran slack-watch-25's
scripts, which watched slack-watch-25's mailbox through `| tail -12`.*

The `cci:<drive>:<desk>` source runs `cci tail` for records addressed to the desk
past the cci cursor named `<desk>-wake`, advancing that cursor and never the desk's own,
so a broadcast before an addressed record stays in the desk's read. The script also advances
each file's cursor. Act on the printed records and file lines before reading
beyond those cursors (I2). Run the 3-minute reconciliation pass, the 30-minute
summary, and periodic sources such as `ledger.py watch --drive <drive> ... --once` and
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
`incident.py` posts through `bus.py` onto the same cci drive. Open the incident
with `incident.py open --bus <cci drive>`; its comms lane reads and answers with cci.

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
`comms`, and `triage` lanes need no root task. On Orca, `desk` and `watch` make a
standing desk: `orca-launch.sh` gives it a contract that loops until rotation and
sends `worker_done` only then, naming the handoff doc
([its brief](reference/orca-lane-brief.md#a-standing-desks-brief)). `cpu=high` marks a
sweep or heavy research lane. Ordinary lanes go to a Sprite by default;
`place=local` keeps a lane on the Mac (O16).

A brief states the outcome ("one failing database never blocks a release"), never a
design ("report it in the Switch; reuse the handoffz and census readers"). Step 0 of
`Do:` is the lane's own search for an existing grader.

```
ccx: role=<role> [cpu=high] [place=remote|local] tooling-lane=<key, for a tooling lane only>
Authority: <what you do without asking; what stops for the owner>.
Verified facts, do not re-derive: <ids, shas, URLs, state already confirmed>.
Design rulings, verbatim: <each owner ruling on the subsystem this lane touches, quoted
  with its id and its confirmed implications (R19b), and the entry point (symbol at
  file:line) it makes the change call; or "none">. Before READY, state which entry point your diff calls, each ruling it meets,
  and anything it leaves out, as a `contract` record on the bus (R22). Never wait for
  a confirm. A contract quotes the owner's words for each pick. A narrower reading the
  build needs, such as per key for per Mac, is an ask to root, never a choice. Its
  failing-first test asserts the owner's invariant, such as process count flat in chat
  count, not the narrowed case.
Design questions: settle each from this brief, the standing rules register, and the
  base branch yourself, and post the choice as a `contract` record with its reason.
  Ask the root (`cci post --kind ask --to root`) only when the choice changes
  owner-visible behaviour, and keep building the rest while the ask is open. Never
  end a turn waiting for an answer.
Do:
  0. Before designing, name what already grades or handles this signal: monitors,
     the release's Watch, open owner tasks and picks (`ccn task list`,
     `ccn answer search`). If one covers it, route through it or delete the
     duplicate. Building alongside it is a return-early-with-options case.
  1. <step>
  2. <step>
Compute: <for a pass, backfill, dry run, or bulk rewrite: the in-region box recipe,
  region, and the bucket or team each process owns; never this Mac. "none" otherwise>.
Escalate early, do not improvise: scope surprise, an assumption the code refutes,
  an auth or approval gate, or two failed approaches. Return findings + 2-4 options.
AskUserQuestion is unavailable. A decision this brief, the plan, and the owner's
  answers do not settle stops that item: `cci post --kind ask --to root` with 2-4
  options, keep building what does not depend on it, and wait for the root's answer.
  Never take a default or a provisional pick (owner answers 01736af and 752564a).
Do NOT touch: <files, branches, worktrees another lane owns>.
Plan task: register your unit at spawn so the progress record's plan counts include it:
  `ccn task add "<unit>" --plan <plan id> --label step:<n> --label program:<slug> --criterion "<done when>"`,
  then `ccn task start`, `ccn task criterion met|failed`, and `ccn task done` as it moves
  ([Plan progress](#plan-progress)). The root fills the plan id and step, or writes "none".
Worktree: <absolute path, exclusive to this lane>.
Mailbox: `mailbox:<lane>=<cursor file>` in every desk-wait call, <lane> being your own
  name; desk-wait resolves it from the live team roster. Claude Code delivers your mail
  only after your turn ends; on `MAILBOX`, act on the text desk-wait prints with it.
  Run desk-wait as the Bash call itself and never filter or discard its output.
Keep record text to one line; `cci post` spills text over 400 characters to a file, posts its first line, and prints the path. Attach a body you wrote with `--path` or `--ccn`. Orient with `cci digest --drive <drive>` when starting a new lane.
Address `head` and `state` records `--to desk-runner,fidelity-pipeline,finder-desk,landing-desk`, never to root;
  root hears asks, decides, blockers, defects, incidents, corrections and withdraws.
Standing rules register: <id of the newest `standing-rules:<slug>` doc, or "none">.
  Claude lanes receive the register once at start through hooks.
  Above 9,000 characters, read it with `ccn doc show <id7>` before acting.
  For Codex, paste the body from `ccn doc show <register id>` here verbatim.
Every rule binds this lane. Your READY names the rulings your diff touches.
Mechanisms the plan bans: <every implementation brief pastes the drive's list, each
  shape with its rule id: timers and polls, credentials in files, local exec, and any
  the drive adds; or "none">. Before each push, run `rulings.py sweep --diff <file>` over
  your own diff, and fix each `MECHANISM-BANNED` line it prints or cite the owner's
  answer naming that shape. A root answer that names a mechanism names its banned
  shapes too, so "re-request before expiry" never reads as a timer.
Scale: <the N each path this lane changes runs at: chats, teams, sessions, or runs on
  one Mac or one pod, such as "1,000 chats on one Mac"; or "no N">. The bar: an
  interactive step answers within half a second at that N, and processes, timers, and
  connections per host stay flat as N grows, proved by a test at N = 1 and N = 1,000.
  A loop that awaits I/O per item, or a cap, timer, cache, retry, or idle wait standing
  in for a fundamental fix, is a defect the finder grades major; a diff that adds one
  names in the PR body the fix it stands in for and the owner pick that deferred it, or
  builds the fix. The spawn hook and the orca desk runner warn on a new non-incident
  lane brief with no `Scale:` line and never refuse it; a `<lane>-handoff` writer, a
  successor (`<lane>-<n>` or a `Replaces:` line), and a brief written before the rule pass
  quietly.
Standing rules served: <`#<seq>` ids with their answer ids, each with the owner's
  words and confirmed implications (R19b), or "none">. Your task cites
  them; finishing it never retires them, and no report calls them done.
Stack shape, under D19: put each shared-file edit in the smallest additive first PR of
  your stack, in the file's declared order. When a file you change is in another open
  PR, stack on that PR instead of racing it. Open your PR as soon as its parent's PR
  exists, never once it lands: ship on the parent branch, then restack after the parent
  lands with `remote-restack.py --config <runner config> --lane <lane> <top PR>`, which
  replays the stack on a Sprite, pushes, and posts each new head. No rebase runs here.
  Resolve by hand only a conflict it reports, on the branch and commit it names. Never commit a generated output; the build
  generates it. A stack that still carries a regenerated artifact rebases and
  regenerates it on ejection.
Holds file: <path>, root-owned. Never enqueue, merge, or add a `merge` or `merge-fast`
  label: the landing desk is the only enqueuer, and the pack's `lane_landing` hook refuses
  `stack-enqueue`, `gt merge`, `gh pr merge`, and `ledger.py label` from this lane.
  Report READY the moment a PR is green; the landing runner enqueues its largest green,
  approved, unheld bottom prefix against the open cci `hold` records. A held PR
  gets `held` on your tip with the held PR named; release is the root's.
  Keep `ccx vcs pr watch` on the stack; on ejection or conflict, restack at once with
  `remote-restack.py`. After the prefix lands, restack the PRs above it the same way.
Ledger: <id>. Register your branch prefix when spawned:
  `ledger.py register --ledger <id> --lane <name> --repo <owner/name> --branch-prefix <prefix>`.
Report every PR open, push, enqueue, and READY yourself, in this one shape:
  `ledger.py report --ledger <id> --drive <drive> --repo <owner/name> --pr <n> --head <full sha> --lane <name> --verdict <clean|red|conflicting|held> --text "<one line>"`,
  plus `--ask <id>` for an owner ask. READY is `--verdict clean --text "READY ..."`.
  `--repo` is the PR's repository; without it a number names a PR in the ledger checkout's repository.
  The desk's inbox reads that cci record; a SendMessage to a looping desk is never read.
Bus: cci drive <drive>; CLI cci, on PATH by name.
  Subscribe: --topic <each PR, branch prefix, and contract you own or consume> --kind decision.
  First call on every wake, and before every decision, report, or ask:
    `cci tail --drive <drive> --cursor <name> --reader <name> <subscription>`; act on a message only after reading cci.
  While running: one Monitor on `cci watch --drive <drive> --cursor <name>-watch --reader <name> <subscription>`,
    timeout 1800000, re-armed on expiry.
  Use `cci post --drive <drive> --lane <name> --kind <kind> --topic <topic> --text "<text>"`:
    a `head` after every push, a `contract` for anything another lane consumes, a
    `decision` another lane could build on, a `blocker` or `ask` addressed `--to` the lane
    that acts. Answer with `--kind answer --re <seq>`; use `--kind withdraw --re <seq>`
    before you change or retract a record. Set the topic and recipients on replies.
    A SendMessage carries the entry number, never the body.
Registration: the pack binds your thread to your lane before your first command runs, with
  one `<lane>: registered at its first command` state record, and the stall watch tells root
  about a lane that has neither posted nor bound three minutes after its `SPAWNED` record.
Peers: put your `cci me` line in every handoff. `cci who --pr <n>` names the session that
  owns a PR and `cci who --drive <drive> --role root` the root; SendMessage the address it prints.
Codex: call `Skill(codex)` or `codex:codex-wrapper`; a one-off question goes to `codex-ask`.
`# ccx:raw` at the end of a Bash command runs it past the hooks as written. Use it only
  where a hook misreads the command, and name that hook in your report.
Run subagents and codex in the foreground (blocking), or poll the reply file in a foreground loop to a terminal state; never background-and-end-turn.
Record each sub-dispatch with `ledger.py ask` before dispatch and `ledger.py answer` when its reply lands.
Verification: no local test runs, builds, typechecks or lint; CI grades; a red CI step is read from its log, fixed, and pushed.
PR body: beside `## Rulings`, a `## Regression tests` section lists each test id the
  change adds or edits and links one CI run where that test failed against the old tree.
  Nothing checks the link; the fidelity desk reads each named test against the failure
  it claims (784f1ab). The `## Risk profile` states Load and Data in prose.
Deletion: a change that deletes a code path, proposed or in a PR, carries its caller
  trace before the root GOs it: every caller of the removed path, each one read, and what
  the path deletes or changes for each (cc-notes 83695d86 is the worked example). The
  fidelity desk grades what the deletion does to each caller, never the diff's shape.
GitHub budget: read GitHub only through ccx, with `ccx vcs pr status`, `vcs pr state`,
  `vcs pr watch`, or `ccx vcs gh -- <gh args>` for any other read. Writes stay plain `gh`.
  No loop reads faster than every five minutes, and every `ccx vcs gh` read in a loop passes
  `--max-age 5m`. A `ccx: GitHub rate limit floor:` refusal means stop reading until the
  reset it names. The desk runner posts an `urgent` record to root when any quota drops
  under 1500.
Report short deltas with pointers (file:line, PR number, sha, Slack ts, disk path); never
  paste a diff, log, PR body, or thread into a message. Write it to disk and send the path.
Finish: a lane with a PR finishes only once its squash `(#N)` is on the base branch.
  Drive to a terminal state, then SendMessage <orchestrator> exactly one report,
  ≤10 lines: verdict | ids | what changed | what is next. That message is your last
  action. Do not end a turn waiting. Every push to a reported PR re-reports the new
  head with `ledger.py report --drive <drive>` in the same turn; the desk grades without waiting for it.
```

For implementation lanes, prefer the repository's canonical remote
entrypoint when it provides one. In Forge-AI/monorepo, follow
[the Orca skill](https://github.com/Forge-AI/monorepo/blob/dev/.agents/skills/orca/SKILL.md).
It defaults to Sprite prepare+attach and the already-owned native Run with its
sole inbox consumer. The orca desk calls it for ordinary lanes by default (O16),
and runs local lanes through `orca-launch.sh`. Preserve explicit model, effort,
and Codex service tier.

Use `place=local` for a lane that cannot leave the Mac. A Sprite holds only the brief and a
shallow checkout of the ref its config names, `dev` in Forge-AI/monorepo. It has no
Mac path, scratch file, local worktree, or unpushed commit, no Keychain, and no
credential that lives only on the Mac.

Write `place=remote` when the lane can work inside those limits, with every fact in
the brief by value and everything it builds on already pushed. Write `place=local`
when the lane reads a file, worktree, or branch only the Mac has, needs a Mac-only
credential, or drives something on the Mac itself. Without `orca.sprite`, an ordinary
lane fails with `LAUNCH-FAILED`; a failed Sprite launch never starts locally.

A Sprite lane commits, pushes its branch, and opens its PR from the VM like any
lane; the root pulls the branch when it needs the source locally. A remote worker keeps its runtime
and workspace after completion. A local lane the root has decided is done ends under R195's
`--done` route. Dedicated remote API keys belong only to the
worker process; local Fable keeps existing Mac interactive authentication.

A local lane that sends a build to a shared Sprite runs it with
`sprite-cargo.sh <sprite> <lane> <repo> <ref> <command> <path>...`, never
a hand-rolled `sprite exec`. Each lane builds into its own `target-<lane>`, so two
lanes never clobber each other's build-script output, and every call removes the
targets and source trees idle for more than six hours, so idle lanes do not fill
the Sprite's disk.

A lane checks its change before it pushes with
`remote-vet.py --config <runner config> --lane <lane> [<worktree>]`, never a local
`go build`, `go vet` or `cargo check`. It ships everything the worktree changed since
its merge base, uncommitted and untracked files included, to the lane's own worktree
on a Sprite, runs the config's `vet.prepare` code generation, then builds and vets
only the Go packages and Rust crates the change touches, and exits 1 on any `FAIL`.

For in-process subagents, use one of this plugin's two lane types with the
routing table's `model`. Both definitions default to Opus; set a different
model explicitly when the assignment calls for it. Roots run Opus 5.5. Fable is
for exceptional cases only: the most sensitive local implementation. Ordinary
workers and subdesks never run Fable.

The standing subagents, the desk's shards, sequencers, and pollers are
`long-running:lane`. An implementation lane that ships a PR or calls a skill such as submit-pr, open-pr, or
codex is `long-running:lane-ship`. Both leave out `ToolSearch`, every `mcp__*` tool but
cc-slack's, and the deferred-tool list, and both carry the 1h prompt cache a nine-minute poll needs.
`lane` also leaves out the Skill tool and the skill listing, so it starts 16k tokens
lighter than `general-purpose`; `lane-ship` starts 5k lighter. A lane that posts to
Slack is `general-purpose` instead, so it carries every Slack surface the session has
(R20).

If a `lane` needs a skill, give that operation to a separately named `lane-ship`
with a scoped brief.
Keep the original lane running; the new lane must not duplicate its active work.

A lane using the separate desktop Orca adapter is a session the Agent tool cannot
message. Run `desk-launch.py lane <lane> <model> <effort> <brief>`; the runner invokes `orca-launch.sh` using
`reference/orca-workers.md`. `reference/orca-lane-brief.md` is its brief, ready to paste:
a shared contract and lane section concatenated into one file, with a ≤500-character
pointer as the `--spec`. A codex Orca lane launches on Orca's codex agent under O15,
never as a claude worker calling the codex skill; the codex skill is for inline lanes,
and `codex-ask` for one-off questions.

That adapter's launch, relay, and receipt rules apply only to its own lanes.
Canonical remote workers use home Dispatch messages and the repository skill's
source-return workflow. Namespace runs on regular Compute through an explicit config
and stays opt-in; the desk never launches there. Native recipe-picker enablement is a
separate surface.

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
  2. Poll origin/dev for the squash commit "(#<n>)" until it appears or 40 min pass,
     waiting between polls with `desk-wait.sh 30 mailbox:<lane>=<cursor file>`.
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

A plain Bash wait, whether `sleep`, a watch command, or a poll loop, never blocks longer than 60
seconds. Any longer wait goes through `desk-wait.sh` on the lane's own team mailbox. A blocking
watch runs after `--`, and the mailbox ends it within about two seconds:

```sh
desk-wait.sh 540 "mailbox:<lane>=<cursor file>" -- bk build watch <build>
```

Run it with `timeout: 570000`. The helper passes the command's output and exit status
through, and on `MAILBOX` or the deadline it ends the command's process group and prints
`MAILBOX <n> unread` or `QUIET`. Wrap read-only watches only, never a ship, push, or apply.
On `MAILBOX`, act on the message text it printed before re-running the watch.

*Prevents the 2026-10-04 cases where cci-fix-1, cc-inbox-2, sweepers-delete,
api-declarations-502-fix, and test-slow-41463-2 got rulings and stand-downs only after a
CI or build watch ended.*

```sh
deadline=$(( SECONDS + 540 ))
while (( SECONDS < deadline )); do
  state=$(<authoritative query>)
  case "$state" in
    passed|merged|CREATE_COMPLETE|UPDATE_COMPLETE) echo "TERMINAL ok $state"; exit 0 ;;
    failed|broken|canceled|timed_out|ROLLBACK_*|*_FAILED) echo "TERMINAL bad $state"; exit 1 ;;
  esac
  wake=$(desk-wait.sh 30 "mailbox:<lane>=<cursor file>")
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

**GitHub budget.** The gh account's GraphQL and REST hours, and the GitHub App
installation's, are each shared by every lane, desk, watcher, and daemon on the machine.
Drive tools read GitHub only through ccx, with `ccx vcs pr status`, `vcs pr state`,
`vcs pr watch`, or `ccx vcs gh -- <gh args>` for any other read and `GH_REPO` set on a
GraphQL read.
Writes such as a merge, a label, or a comment stay plain `gh`, since ccx refuses them.
ccx charges each read to `CCX_CALLER`, and `ccx vcs auth status --json` lists every
quota with the callers that spent it.

No loop reads faster than every five minutes, and every `ccx vcs gh` read in a loop passes
`--max-age 5m`, so a repeat within five minutes comes from ccx's machine-wide cache
instead of GitHub. A read that decides a merge passes `--max-age 0s`. With under 1000
calls left in a quota, ccx refuses the read with one stderr line starting
`ccx: GitHub rate limit floor:` that names the quota and its reset; the caller stops
reading until that reset and never retries sooner. The desk runner reads every quota
each pass and posts one `urgent` `GITHUB-QUOTA-LOW` record to root, once per reset, for
any quota under 1500, naming the five callers that spent the most. Both lane brief
templates carry this rule.

**Batch and cache every GitHub read.** A desk or tool that needs one fact for many PRs
reads it in one batched query per pass, never one call per PR. A surface that shows a
PR caches it by head, revalidates on a floor of a minute or more, and reads its files
again only when its head or base moves. The secondary limit fires on bursts of writes,
such as a dozen PR creates in a quarter hour, so a ship storm spaces its creates. A
per-PR loop found in any tool is a defect fixed in that tool, not a second reader added
beside it.

*Prevents a per-shard log loop emptying the Buildkite budget mid-release, and a bulk
scan across thousands of logs failing two releases' own pipeline syncs on the same
shared limit; also the account's GraphQL budget running dry at 04:39Z during release v3
(2026-10-01), which refused every lane's ship until the hourly reset.*
*Also prevents Oct 27 8:23 AM: PR pollers spent about 1,800 of the user's 5,000 GraphQL
points an hour while drive scripts read the App's quota with no cache, floor, or alert.*
*Also prevents Oct 27 12:21 AM: a landing pass spent one GraphQL query per open PR, a
board page fetched each PR uncached, and twelve PR creates in thirteen minutes tripped
the secondary limit, so every lane's PR create was refused.*

### Desk cadence

Lanes never enqueue under D1. The desk runs the reconciliation loop below; the root
records asks and verifies their acceptance checks. Create the ledger once at spawn
and put its id in every brief. Each pass reads all PR numbers in one
`ccx vcs pr status <n1> <n2> ...` call and the Buildkite build list, never one REST
call per PR. Run every three minutes, with desks and shards staggered by a minute at
`:00`, `:01`, and `:02`.

Start the landing runner from
[its config](reference/orca-desk-brief.md#config-and-startup), with `landing.adapter`
naming the repository's queue. It owns the enqueue pass and re-reads holds for
every call. The model desk runs the ledger duties below and never adds an enqueue
loop beside it.

A resumed root in a new session runs `drive.py start --drive <id> --ledger <id>`
to join the existing drive. Run `drive.py end` only when the drive is over.
`--register-lane <lane>` names the lane that classifies owner captures. The register gate
posts one blocker per pass listing every unclassified capture, and sends it to that lane
instead of the root while `cci who` lists the lane. The lane holds the `classify`, `names`,
`authorize`, and `supersede` seats. With no lane named, the blocker goes to the root. When
`cci who` does not list the lane, the blocker goes to the root, which resumes the lane by
`SendMessage` with the listed captures.

Once `drive.py start` registers the drive, every `ledger.py` verb resolves `--ledger` on its own:
from `--drive <id or cci drive>` when given, else from the drive this session runs in, so a
lane runs `ledger.py reassign --pr <n> --lane <new>` without the ledger id. Verbs that carry
lane messages default `--drive` to that drive's cci drive the same way.

```sh
LEDGER=$(ledger.py init --title "desk: $DRIVE")
drive.py start --ledger "$LEDGER" [--orca-run <run>] [--state-dir ~/.claude/scratch/<slug>] [--program <plan slug>] [--register-lane <lane>]
drive.py desk --name <desk lane> --terminal <handle> [--environment <name>] [--supervisor]
ledger.py ask     --ledger "$LEDGER" --text "<verbatim>" --lane lightning-eh --accept "<acceptance check>"

# on each report: record it and grade the current head; reports are not a gate
ledger.py report  --ledger "$LEDGER" --drive "$DRIVE" --pr 21221 --head <sha> --lane lightning-eh --verdict clean --ask ask/000001
ledger.py report  --ledger "$LEDGER" --drive "$DRIVE" --repo yasyf/cc-skills --pr 375 --head <sha> --lane skills-tool --verdict clean
ledger.py ruling  --ledger "$LEDGER" --drive "$DRIVE" --lane p2-edge-rows --pr 20284 --text "land without the document form" --options "A land|B hold|C close"
ledger.py enqueue --ledger "$LEDGER" --drive "$DRIVE" --kind idle --pr 21221 --head <sha> --lane lightning-eh --text "done"
ledger.py inbox   --ledger "$LEDGER" --drive "$DRIVE" --take
ledger.py register --ledger "$LEDGER" --lane lightning-eh --branch-prefix lightning/ --pr 21221

# every 3 minutes, staggered :00/:01/:02: reconcile every tracked current head
ledger.py refresh --ledger "$LEDGER"
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

ledger.py summary --repo "$REPO" --ledger "$LEDGER" --drive "$DRIVE" --checkout "$CHECKOUT"
ledger.py show    --ledger "$LEDGER" --asks
ledger.py live    --ledger "$LEDGER" --at "$(date -u +%FT%TZ)" --text "release 37 deployed"
ledger.py drop    --ledger "$LEDGER" --ask ask/000002 --reason "owner withdrew it"
ledger.py answer  --ledger "$LEDGER" --ask ask/000003 --text "<the reply>"

mq.py --config "$RUNNER_CONFIG" status
mq.py --config "$RUNNER_CONFIG" why 21221
mq.py --config "$RUNNER_CONFIG" priority 21221 0
mq.py --config "$RUNNER_CONFIG" dequeue 21221 --reason "the owner pulled it"
mq.py --config "$RUNNER_CONFIG" requeue 21221

drive.py end
```

At spawn, arm the watch under Monitor at its maximum timeout and re-arm on every
expiry. Pass each priority PR the root names with `--priority`; forward every `P0`
line immediately. The three-minute batch reconciles the ledger.

```sh
ledger.py watch --repo "$REPO" --ledger "$LEDGER" --drive "$DRIVE" --checkout "$CHECKOUT" [--priority <n>]... [--shard <lane>,<lane>]
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
ledger. Report each successful enqueue with `ledger.py report --drive <drive>`; refresh records it
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

From the drive's first plan, the root runs the app in `dashboard/app/`:
`python3 dashboard/app/server.py --config <state dir>/dashboard.json`, where the config
names the drive, ledger, board session and URL, repo, progress-doc label, release doc,
story file, keeper lane, host, and port. It serves Overview, Needs you, Review, Releases,
Workers, Timeline, and Ask from the cci daemon's record stream, `cc-present watch` on the
board, the ledger, the drive's cc-notes answers, and the newest progress doc carrying
`## Ordered plan of action`. A build failure posts one `defect` record to root.

The assistant runs in the browser on the design-doc client (`_shared/html/ai-core.js`),
calling Cerebras `gpt-oss-120b` with the key the server hands out at `/ai.json` from
`CEREBRAS_API_KEY`; without the key every assistant control stays hidden. Command-K (or
`/`) jumps to any screen, pull request, stack, plan step, worker, release section, owner
answer or record, and Tab sends the query to the assistant. Needs you sorts the owner's
taps into safe-to-approve-together and read-first, flags a tap that conflicts with a
quoted owner answer, and approves or holds in place. The Overview opens with what changed
since the owner last looked, the Timeline with the day in five sentences, and every pull
request and stack offers a plain-words summary. The `/live-dashboard` layout below is the
older view, kept for drives that do not run the app.

The dashboard is the owner's view, and it answers four questions in this order: what
waits on me, what is broken, what is live, and what is in flight. Review comes after
them. The `drive` preset already lays the page out that way:

- `lr.needs-owner` is pinned in the first section. It shows owner tasks, cci asks and
  decides addressed `--to owner`, and boards that are still asking.
- Broken shows `release.incidents` and `open-records`.
- In flight opens with `lr.plan-progress` and the Plan table, then `cci-lanes`,
  `orca`, `tasks`, and `asks` for the owner's own asks.
- Plumbing is collapsed.

Before the first milestone report, the root adds the drive's own sources, such as the
`release.*` cards for a release drive, per `/live-dashboard` `reference/picking.md`. It
revisits the layout at each milestone and runs `live-dashboard check` after every edit.

Every source card drops settled items itself; `reference/keeping-live.md` lists the
rules. When a row stays on the page after its work closed, the cause is a missing
closing record, so post one with `--resolves` or `--re`. A stale row is a source defect
for a cc-skills lane, never a hand edit to `layout.yaml`. Three things stay off the page:

- status written into `banner`;
- a manual card where a source exists;
- internal ids where a name exists.

The drive's pack adds `lr.needs-owner`, `lr.plan-progress`, `lr.inbox-feed`, `lr.drive`,
`lr.compactions`, and `lr.watches`. The owner's actions on `lr.needs-owner` and `pr-review-queue` arrive at
main as `owner` records; on a `Reviewed #N` record, main sets `owner_reviewed_at` on that
PR's ledger row.

`python3 dashboard/app/keep.py --config <state dir>/dashboard.json` starts the app
detached when it is not answering, logs to `server.log` beside the config, and tells
root; the root hands the owner its tailnet URL once, and never a cc-present
board in its place. `dashboard-keeper`, briefed from `reference/dashboard-keeper-brief.md`,
is the config's keeper lane: it keeps the story file's sentences true, answers each build
`defect` the app posts, and takes the place of `/live-dashboard`'s `dashboard-curator`.

`reference/drive-dashboard.md` is the Overview's contract: one model, one counting
function, seven PR states, the `parts` and `steps.json` a drive supplies, and the
40-word budget at rest. The owner set the app's bar, and every revision is graded
against it:

- A five-second glance tells him how far along the drive is, with nothing to read.
- The first screen is the plan's steps as a visual, interactive table: each step's
  state, what blocks it, and what happens next, with its landed, queued, and
  waiting-on-his-tap counts. The end state sits beside it as a picture, its pieces
  colored done, doing, or not started, with one live box per worker saying what it is
  doing now, and the exact current plan is one click away, full-screen.
- Needs you lists every tap and card he owes. Approve and Hold act in place, posting the
  board's own decision event, and never open the board.
- The PR review screen embeds cc-review for each PR and each stack and cc-present's row,
  never a link out.
- Text comes on demand: Command-K jumps anywhere, and a client-side Cerebras assistant
  says what changed since he looked, what needs him, and what any PR or stack does.
- Phone and desktop both work.
- It moves live. Every state it can read locally (the board store's taps and questions,
  cci heads, gos, landings and lane state, the ledger) reaches the page within seconds
  over server-sent events with no API call; GitHub is read only for CI and review state,
  cached, on a floor.

Every revision of a surface he will use, this app, a board layout, or any other page,
goes through a `user-test` lane briefed from `reference/user-test-brief.md` before he
sees it: codex plays him against the live page, at least three rounds, and the card that
shows him the revision carries the rounds' findings.

*Prevents Oct 26 7:08 PM to Oct 27 12:23 AM: the drive dashboard was a cc-present page
he had to ask for, rebuilt twice from his corrections, a wall of text at a glance, and
shown to him untested.*

#### Plan progress

`lr.plan-progress` shows what is done and what is left, read from cc-notes alone. A plan
counts when it carries `program:<slug>`; a task counts when it carries that label or hangs
off such a plan. The card draws each plan's `### <n>. <title>` headings as steps.

- A task joins step `<n>` through a `step:<n>` label. Without one it joins its plan's
  "No step label" row, and with no plan it lands under "Outside any plan".
- Each step shows a bar of done, in progress, blocked, and open tasks. It opens onto its
  tasks with status, assignee, age, and what each waits on.
- A task is blocked while a `ccn task dep` blocker is unfinished or a criterion has
  failed. Otherwise, it waits on its first unmet criterion.
- The progress record's `## Open tasks` ends with the same counts, one line per plan.

Register a unit when the lane starts:

```
ccn task add "<unit>" --plan <plan id> --label step:<n> --label program:<slug> \
  --criterion "<observable done-when>"
```

Then keep its state current:

- `ccn task start <id>` when work begins.
- `ccn task dep <id> <blocker>` when it waits on another task.
- `ccn task criterion met|failed <id> <criterion>` as each check settles.
- `ccn task done <id>` when the PR lands, or `ccn task cancel <id>` when it is dropped.

The plan's step numbers are the ones its `### <n>.` headings carry. A plan with no task
registered appears in the card's note, never as an empty plan.

#### Plan table

The Plan table panel is a `view` card over `plan-table.md` in the dashboard dir. It
shows the owner report table with each row's PR states refreshed and the watchdog's
newest retro line appended. `scripts/plan-table.py` regenerates the file from the
`owner-table` doc, as `reference/owner-report-table.md` describes. The root runs it
when the owner asks for the dashboard; no lane runs it on a clock. Until the first run
the panel says nothing matches yet. A drive seeded before the preset carried the panel
adds this card to its In flight section:

```
- {use: view, id: plan-table, title: Plan table, width: 3, with: {file: plan-table.md, table: Plan table, columns: [step, what_it_delivers, state, what_blocks_it, what_we_are_doing_to_unblock_it, what_went_wrong_or_slowed_it_so_far]}}
```

### Prompt watch

A terminal stopped at a prompt reports nothing. Its transcript goes quiet and its lane
looks busy. The prompt watch reads Orca instead.

`drive.py start` and root `SessionStart` run `prompt_watch.py start --drive <id>`,
which detaches one `run` per drive. The watch is its own process, so a stuck session
does not stop it. It polls every 30 seconds, holds `<state dir>/prompt-watch/lock` so a second one exits, and ends when
`drive.py end` removes the registry file.

A plugin update signals nothing. The next `start` from the new version locks
`<state dir>/prompt-watch/successor` and waits on the watch's own lock. The running
watch exits after the poll that finds `successor` locked, and the new one takes over.
One watch waits at a time, and one that dies while waiting leaves no lock behind. A
watch older than 0.7.43 never looks at `successor`. Stop it once by hand, and the
waiting one takes over.

The registry names the terminals it polls.

- The coordinator's terminal is `root_terminal`, which `drive.py start` and the hook
  record from `ORCA_TERMINAL_HANDLE`. A prompt raised by an in-process lane shows here.
- Each standing desk is recorded with `drive.py desk --name <lane> --terminal <handle>`,
  plus `--environment <name>` for a desk on a remote host.
- The workers are every in-progress dispatch of the drive's Run, local or remote, from
  `orca orchestration worker-list --include-remote`. With no `orca_run` in the registry
  the watch takes the Run bound to the coordinator's terminal, and none when that
  terminal is unbound. While that list fails, no worker's prompt is closed. Each worker
  keeps its record and reads `unknown` or `unreachable` until the list answers again.

Orca's `agentWait` only says where to look. It stays set after a prompt is answered, so
it never raises a record and never authorizes an answer. The watch reads the rendered
screen with `orca terminal read --screen` and puts each terminal in exactly one state.

| State | The screen shows | Record |
|---|---|---|
| `approval` | an approval dialog, such as a hook's ask, a tool permission, or a trust check | one, on the first poll that sees it |
| `question` | a question picker | one, on the first poll that sees it; none on the coordinator's terminal |
| `stale` | the agent's own input box, with `agentWait` still set | none; it resolves the prompt's record |
| `unknown` | nothing the watch can read, or a wait whose screen matches no dialog or input box it knows | one `report` to `desk-runner` after three polls |
| `unreachable` | nothing, because the remote host does not answer | one `report` to `desk-runner` after three polls |
| `clear` | a working or idle agent with `agentWait` unset | none |

After an `unknown` or `unreachable` report, the watch posts one `READABLE` report to
`desk-runner` once three polls in a row read the terminal again. Neither reaches root,
whose window every record addressed to it lands in. A shorter read posts nothing,
so a terminal or `worker-list` that flaps under load raises no second record for the
same unread state. When every watched terminal reads `runtime_unavailable` or
`timeout`, the Orca app itself is down and no terminal reports alone. The watch posts
one `orca (runtime)` report after three polls only when the last answered
`worker-list` held a worker in progress, and its `READABLE` only after that report. A
drive with no worker in progress hears nothing about the outage.

`unknown` and `unreachable` mean the prompt state is not known. Never read either as
working, and never as stuck. A host running Orca's managed server installs no agent
hook, so `agentWait` stays unset there. The watch therefore reads every remote
worker's screen on every poll, through the `--environment` its `worker-show` names.

A dialog's text can hold a command or a secret, so it stays on disk. The watch saves
the screen of each prompt, and of each screen it could not classify, owner-only under
`<state dir>/prompt-watch/screens/`. A record, a wake, and `show` name that file, the
dialog's class, and the command that reads the live screen. None of them carries the
dialog's text.

Each prompt raises one cci record from lane `prompt-watch` on topic `prompt:<name>`,
never one per poll. The terminal, the dialog's class, and every row of text in the
dialog identify a prompt, so a second command behind the same warning is a second
prompt. Orca rewrites `agentWait.since` every few seconds while one dialog stays open,
so it identifies nothing.

- A worker's or desk's prompt is a `blocker` to `root`. The root reads the screen with
  the command in the record and decides. An approval's record also carries the
  `prompt_watch.py answer` command that settles it, as [Answering a dialog](#answering-a-dialog)
  describes.
- A question dialog on the coordinator's terminal already waits on the owner there, so
  it raises no record, and `show` still lists it. During execution an owner question
  goes on the board, never that dialog (owner answers 01736af, b4dfb16).
- Its own approval cannot wake the coordinator. Register one independent agent as its
  supervisor with `drive.py desk --name <lane> --terminal <handle> --supervisor`. The
  coordinator's approval is then a `blocker` to that lane, and the watch types the
  record's line into the supervisor's own terminal with `orca terminal send --enter`.
- The owner gets a `needs_you` card in the DM status thread of the drive's newest root
  session, through `cc-slack dm-status`, when no supervisor can take the prompt. That
  covers a drive with no supervisor, a supervisor that is itself at a prompt, unknown,
  or unreachable, and a wake Orca refuses. A prompt still open five minutes after the
  supervisor's record gets one too. The card goes out only in a drive whose watch was
  started with `prompt_watch.py start --slack-cards`; without it, the owner gets no
  Slack message for a dialog.

The wake goes out once per prompt, and only to a supervisor that can take it. Orca has
to read the terminal `tui-idle`, and the screen has to read `idle`.

| Supervisor's screen | Claude Code | Codex | The watch |
|---|---|---|---|
| `idle` | the plain empty `❯` box, nothing running | the `›` line with no draft, nothing running | types the line |
| `busy` | a turn running, a draft, or a list under the box | a turn running or a draft | types nothing and looks again next poll |
| `waiting` | a dialog | a dialog | types nothing; the owner gets the `ask` |

The row keeps what Orca reported for the line. `turn_started` means Orca saw the turn
start. `input_accepted` means Orca took the line and saw no turn start, which is
delivery and nothing more. The watch never types a line twice.

A supervisor that holds a foreground wait reads `busy` for as long as it waits, so it runs
`cci watch --drive <cci drive> --to <lane> --for 0` in that wait and the record ends
it; `drive.py desk --supervisor` prints that command. The supervisor reads the live
screen and answers only within what the owner already authorized. For anything else
it answers nothing, and the watch's owner card follows five minutes after the
supervisor heard.

When the prompt leaves the screen, or its terminal leaves the watch, the watch posts
an `unblock` record that resolves each record it raised, and an `info` card under the
owner's card says the prompt cleared. The watch answers no prompt and types into no
terminal that waits on one; the supervisor's wake is the only line
it types. It never stops, restarts, releases, closes, or signals anything.

#### Answering a dialog

Orca refuses `orca terminal send --enter` into an agent that shows an approval dialog,
with `agent_prompt_blocked`, and its `--retry-request` refuses the same way. Whoever
decides answers the dialog with the command the record names:

```bash
prompt_watch.py answer --dispatch <ctx_id> --episode <key> --by root yes
prompt_watch.py answer --dispatch <ctx_id> --episode <key> --by root no --instead "<what to do instead>"
```

`--terminal <handle>`, with `--environment <server>` for a remote one, takes the place of
`--dispatch` for a desk, the coordinator, or a worker known only by its terminal.

- `answer` reads the live screen and refuses, typing nothing, unless an approval dialog
  is open there and, given `--episode`, it is the one the record named.
- It types the number of the first option that reads Yes or Allow, or No or Deny, as raw
  keys, which Orca does not refuse, then reads the screen again until the dialog closes.
  Options come only from the dialog's closing list, so a numbered line in the command
  above it never chooses.
- A denied agent ends its turn, and Orca keeps refusing prompts to it until one is
  typed. A `no` therefore requires `--instead`, which `answer` types and submits as raw
  keys once the screen shows the agent's empty input box.
- It posts one `status` message to the Run mailbox of the worker that owns the terminal,
  or of the Run the terminal coordinates. The message names who chose which option, the
  dialog's title, and its episode, which names the watch's saved screen. The dialog's
  text stays out of the mail. With no Run to hold that line, `answer` refuses before
  typing.
- A dialog whose options carry no numbers, such as a folder trust check, is refused;
  answer it in the terminal.

`prompt_watch.py show --drive <id>` prints the last poll as a count line, then one line
per terminal that is not clear. `ledger.py summary` prints the same lines after its
counts. `PROMPT-WATCH-DOWN` leads them when the last poll is over two minutes old;
run the `start` command it names.

*Prevents a hook approval sitting on the coordinator's terminal with a lane blocked
behind it, seen by nothing that reads transcripts, until the owner pointed at it
(orca-remote-managed, 2026-10-10).*

### Lane bus

The cci drive is the bus; it needs no init. The root puts its name in every brief.
Use that name as `DRIVE` from any worktree. Each lane reads on every wake and before
every decision, report, or ask:

```sh
cci tail --drive "$DRIVE" --cursor pr-plans --reader pr-plans --topic 27510 --topic iam-contract --kind decision
```

While the lane runs, keep one Monitor with timeout 1800000 and re-arm it on expiry.
The watch prints only deliveries and uses a separate cursor:

```sh
cci watch --drive "$DRIVE" --cursor pr-plans-watch --reader pr-plans --topic 27510 --topic iam-contract --kind decision
```

Publish state instead of answering questions about it. Set `HEAD_SHA` to the full
40-hex SHA of the pushed commit:

```sh
cci post --drive "$DRIVE" --lane pr-plans --kind head --topic 27510 --text "$HEAD_SHA"
cci post --drive "$DRIVE" --lane iam-structural --kind contract --topic iam-contract --text "grants derive from row kinds; no hand IAM"
cci post --drive "$DRIVE" --lane iam-structural --kind decision --topic iam-contract --text "IAM wave lands before the pre-hold" --to pr-plans
```

Post asks, answers, blockers, and withdrawals with cci. The printed sequence number
is what the `SendMessage` carries. Use the ask's number as `ASK_SEQ` and the verdict
being retracted as `VERDICT_SEQ`:

```sh
cci post --drive "$DRIVE" --lane pr-plans --kind ask --topic 27510 --text "clear to label before the IAM wave?" --to iam-structural
cci post --drive "$DRIVE" --lane iam-structural --kind answer --topic 27510 --to pr-plans --re "$ASK_SEQ" --text "yes, land it"
cci post --drive "$DRIVE" --lane landing-desk --kind blocker --topic 27510 --text "DESK #27510 3f3acff97: plan job red" --to pr-plans
cci post --drive "$DRIVE" --lane artifact-contract --kind withdraw --topic artifact-reads --to partial-release --re "$VERDICT_SEQ" --text "verdict retracted; contract changed"
```

Read state before asking for it; add `--lane` or `--topic` to narrow the result.
The root reads the digest and replays deliveries from the start when a thread needs
closer reading:

```sh
cci state --drive "$DRIVE"
cci state --drive "$DRIVE" --lane iam-structural --topic iam-contract
cci digest --drive "$DRIVE"
cci tail --drive "$DRIVE" --reader root --since 0
```

Set a reply's topic and recipients explicitly. Address an `answer` to the asker
and a `withdraw` to the original recipients; leave it broadcast if the original
was broadcast. Withdraw only your own records, once, before replacing them.
The lane's named cci cursor survives compaction and advances through printed
records. `--since` reads without changing it; `--since 0` replays retained records.
Repeat a capped tail with the same cursor and filters to continue reading.

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

`LABEL_WATCH_QUEUE` defaults to the repository's ccx queue, so a Trunk repository needs no
setting. When the queue is `trunk`, `label-watch.sh` delegates only `once` mode to the gated
native `stack-enqueue` helper; its legacy mutable-file `watch` and hold-file modes refuse
visibly for Trunk and direct callers to `mq.py` with `trunk-stack-enqueue`. On the legacy
Graphite path, a stack's largest passing bottom prefix goes into the queue in one
`POST /v1/graphite/merge` call, the call `gt merge` makes, sending the prefix's PR numbers
bottom first so Graphite queues them as one batch.

When open PRs remain above it, the prefix top prints `<top> ENQUEUED <sha> prefix`
and the PRs below it `SKIP covered-by #<top>`. Each PR above keeps its `NOT-READY` reason with
`restack-after #<top>` appended. If the prefix reaches the stack's top, that PR
prints `ENQUEUED <sha>` without `prefix`.

After the prefix lands, route the first PR above it to its owning lane to restack
the rest on a Sprite with `remote-restack.py`; Orca routes use
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

### Red register

`drive.py reds --drive <drive>` lists every open drive PR whose build reads red: its
head, failing checks, class, owner, since when, and last action, from the `red` objects
the stall watch writes into `stack.json` each pass. A lane classes a red head with
`cci post --kind red --pr <n> --text "class=<inherited|flake|real|stale> head=<sha> owner=<lane> <what was done>"`;
a head with no such record reads `unclassed`, and a new head starts unclassed. The
dashboard's failing-tests count names the lane holding each red. The standing
`red-sweep` lane works the register; the stall watch's `unclassed`, `unfixed`, and
`sweep` stalls reach root when it falls behind or is missing.

*Prevents sixteen red PRs on the owner's dashboard at 6:06 AM Oct 30 with nobody owning
the set: "also someone is workong on unredding all these red PRs right?"*

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
once 50k tokens or fewer remain before that threshold.

**Nudge.** With 50k tokens left before the threshold, the hook sends the root one nudge,
as context on its next tool call or prompt. It asks for a new progress doc with the
root's narrative when convenient. The hook fills the
handoff from the owner-approved register, live standing cci rules, open asks, tasks,
lanes, monitors, inbox heads and cursors, and lint findings. The root writes only what
those sources lack. The slug comes from `progress:<slug>` in the plan's existing
progress pointer line, or from the plan's file stem if that line has no slug. The nudge is not repeated,
and the nudge never holds the turn.

**Order.** When the nudge fires, the root writes the progress doc before answering any
further record. The doc names the last cci seq the root consumed, because the
root cursor goes stale across a compaction, so the successor resumes with `cci tail --since <seq>`.
After an unplanned compaction the successor's first act is:

```sh
cci tail --since <seq> --reader root --kind decide,ask,blocker,hold,incident --json
```

It answers every record from that output that no later record resolves at once, before any other work.

*Prevents the 2026-10-16 1:50 AM compaction, which lost about three hours of inbound records with no
doc written and about twenty lanes waiting on the root.*

**Root narrative.** At the nudge, create a new cc-notes progress doc with only the
narrative:

```sh
ccn doc add "<drive>: progress <UTC>" --label progress:<slug> --when "Resuming or compacting the <drive> drive: read before anything else, after the plan" --body -
```

Use the nudge's UTC timestamp (`YYYY-MM-DDTHHMMZ`) and pass the body on stdin.
The progress doc is living guidance with a `when` trigger and supersede edges.
Never rewrite the plan; it remains the stable mandate and decisions document.

Label every hand-written progress or handoff doc `progress:<slug>`, the slug in the
plan's pointer line, whatever else it carries; the hook refuses a root `doc add` whose
title says `progress` or `pre-compact`, or whose labels name another `progress:` slug,
without it.

A doc that more than one writer edits, such as a progress doc whose register a desk
stamps, is edited only through `ccn doc edit <id> --checkout` run immediately before
the edit, then the edit to that file, then `ccn doc edit <id> --apply`. Never
`--body` from a copy read earlier, and never apply a checkout taken before another
writer's change.

*Prevents progress doc 75c873ec losing the fidelity desk's register stamps twice on
2026-10-13, when root edits applied from older copies overwrote F343 to F351.*

Write each compaction's narrative as its own doc, self-contained, and add an addendum as
another doc when more happens before the compaction. The hook never folds, trims, or
rewrites the root's text. The newest hand-written doc becomes the record, and every
earlier one since the last generated record is named to read first. Use this shape:

```md
## <UTC> pre-compact handoff

### How the drive runs
<root role, lane contracts, desks, ledger and rulings-log ids>

### Owner asks and state
<every ask, its current state, evidence, and next gate>

### Owner rulings (verbatim)
<each ruling that must survive: the owner's words, its confirmed implications, and its
answer id; an id or a gist alone drops it (R19b)>

### Landed
<completed work and evidence>

### Waiting on the owner
<unresolved decisions and the options already presented>

### Root's next actions
<ordered actions, dependencies, and owed follow-ups>
```

Only when the repo lacks cc-notes or the `ccn` binary is unavailable, write the
narrative as a new file beside the plan at `<plan-stem>-progress/<UTC>.md`. Otherwise,
use a progress doc; a note, a log, or a loose file does not replace it.

**Generated handoff.** The hook writes the handoff with `scripts/handoff.py`, through
the `bin/handoff.py` wrapper. The script uses only the standard library and has two
verbs:

```text
handoff.py generate --program <slug> --plan <path> [--inbox-dir DIR] [--ledger ID] [--session FILE|-] [--strict] [--folder] [--repo PATH]
handoff.py lint (--doc ID | --file PATH) --program <slug> [--plan PATH] [--previous-doc ID | --previous-file PATH] [--repo PATH]
```

The standing rules register is a cc-notes doc labeled `standing-rules:<slug>`, as
owner answers `4d381e0` and `8cb6da7` require.
A consolidation lane proposes at most 35 rules for the owner to approve, the count the
owner approved on answer `5fb1ec1`.
Full answers stay in cc-notes, linked by id from the register.
The `register_injection` hook hands the register to every subagent at start, helper
agents included; one past the injection budget is named for a full read, never truncated.

`generate` reads the newest register doc by `updated_at`.
It never builds, edits, or supersedes a register doc or writes a register file.

The first progress section is `## Standing owner rules`.
It carries one pointer line:

```text
Register `ccn doc show <id7>`: N owner-approved rules, delivered verbatim after every compaction.
```

Once the register ledger exists, `generate` reads it through `rulings.py rows --json`
and the section prints every scope row's words, each implication with its state, and
its names, verbatim. Then come the rows touched in the last 24 hours, the owed gates by
key, the rows not in force with their evidence, and the unconfirmed implication lines.
The progress size cap never measures this section. A failed read leaves a line saying
so and posts one `blocker` to the root. With neither a register doc nor a register
ledger, the section says so. `generate` reads live standing rules from
cci on the drive named by `--program`. Each follows as `- #<seq> [<source>]`, where
the source is `ccn <answer id>` when cited or `cci #<seq>` otherwise. The rule's text
stays in cci.

Each rule missing since the previous handoff appears last, once, as
`- <id> superseded by <successor id>` or
`- <id> superseded by nothing: the sources dropped it ...`.
Old `R<n>` ids retire this way once.
The handoff carries no separate list of durable answer titles.

The lint requires this pointer for the current register. A missing or stale pointer
fails with `does not name register`.

The plugin ships a `bin/rulings.py` launcher.

`rulings.py register --program <slug>` prints the newest register as `{id, body}` or
`null`. `rulings.py match --program <slug>` reads a lane brief from stdin.
It mirrors the drive's `scope:durable` answers into `<state dir>/rulings/<id7>.md` with atomic
per-file replacements so concurrent spawns never read partial files. It queries
`ccx code search --semantic` with the brief's first 2,000 characters.
The drive's answers are those labelled `program:<slug>` plus those anchored to the repo's
current branch. Another drive's answers never enter the corpus.
`capture_standing_rule` labels each owner ruling it records with `program:<slug>`.
Each matched answer absent from the register's citations appears as `- <id7> <title>`
over its quoted body. `--budget` defaults to 8,000 bytes.

`--drive <id>` replaces `--program` and resolves the program and state directory from the drive registry.
With neither flag, every `rulings.py` verb uses the session's drive: the one `CLAUDE_LONG_RUNNING_DRIVE`
names, else the one whose root sessions hold this session.

Claude lane subagents receive the register once at `SubagentStart`.
This includes `long-running:lane`, `long-running:lane-ship`, `general-purpose`,
and all other types except the helpers and relays below.

These helper and relay subagents receive no register:

- `Explore`, `Plan`, `claude-code-guide`, `statusline-setup`, and `output-style-setup`.
- Every agent type from `cc-context`, `cc-review`, `cc-slack`, `cc-present`, and `codex`.
- `open-pr:pr-style-scout`.

Orca Claude worker sessions carrying `CLAUDE_LONG_RUNNING_DRIVE` receive it at
`SessionStart`, once at start. If the body exceeds 9,000 characters, the hook names the register and
tells the lane to read it in full with `ccn doc show <id7>` before acting. Within that
budget, the body arrives verbatim. Claude Code moves context over 10,000 characters
to a file with a 2 KB preview.

`SubagentStart` cannot see the spawn prompt, so the hook keys matched answers by
subagent type. At `SubagentStart`, the hook collects matches from same-type spawns in the
preceding 60 seconds. It deduplicates them and delivers at most 8,000 bytes on the
subagent's first `PostToolUse`.
A lane may therefore receive answers matched to a sibling spawned alongside it.
Rewriting the Agent prompt was rejected because capt-hook keeps only the first
rewrite per call, dropping other hooks' Agent rewrites such as the model upgrade.

An Orca Claude worker's first `UserPromptSubmit` delivers matches for its brief.
All injections warn and fail open. Codex briefs must paste the register body because
Codex workers have no Claude hooks.

*Prevents the release-v3 rule "release everything as it merges" (answer 4ffc9a5)
vanishing from progress doc b0ebc9a and later compactions while stale "(owner's
word)" plan lines re-imposed the gate it withdrew (2026-10-01).*

The record carries open owner asks from the drive's ledger as `ledger.py` ask rows
that are not dropped, answered, or `LIVE`, with their state. It also carries the
root's open tasks, lanes and monitors from the background tasks at the root's last
`Stop`, and the drive registry line with the drive, ledger, Orca run, checkout, and
root sessions. It names no dashboard, running or not.

The open task list shows only `in_progress` tasks, one line each as
`- #<id> <subject>`, with subjects clipped to 120 characters. With none in progress,
it says `- none in progress`. When any tasks are pending, a final
``- N pending in `TaskList` `` line counts them.
Lanes and monitors show only `running` entries, one line each without the status
suffix. They share a limit of ten entries, monitors first, then newest lanes, with
descriptions clipped to 120 characters. A final `- N more running lanes` line
counts the rest.

`Incident lanes` lists every `inc-`, `incident-`, or `outage-` lane that posted on the
drive's cci in the last six hours, one line each as
``- `<lane>`: last <kind> <n> min ago: <text>``. A lane whose newest `MECHANISM` or
`CANDIDATE` has no `OPENED` or `BLOCKER` after it adds
`| MECHANISM <n> min ago, no OPENED or BLOCKER since` and sorts first, so the root
sees a lane that wrote no code without checking by hand.

For at most ten `~/.claude/scratch/<slug>/inbox/*.md` files, the record carries one
line each: `- <file>: head <id>, cursor <id>: <last ruling line>`. The cursor comes
from `<file>.cursor`; the last ruling line is clipped to 120 characters and omitted
when absent. A final `- N more inboxes` line counts the rest. Sections run in this order:
`Standing owner rules`, `Read first`, `Open owner asks`, `Open tasks`, `Lanes and
monitors`, `Incident lanes`, `Inboxes`, `Lint findings`, `Root narrative`.
The `Inboxes` section starts with
`cci digest --drive <drive>`, then `cci tail --drive <drive>`,
and the instruction never to tail, sed, or grep a whole inbox.

The script writes the same markdown to `<plan-stem>-progress/<UTC>-generated.md`.
Without cc-notes, `--folder` writes only that progress file. The hook caps generation
at 120 seconds.

Generation prints JSON `{id, file, register, fresh, digest, read_first, narrative}` with
the active progress doc id in `id` and the register doc id in `register`. `register` is
null when no register exists. Both ids are null in folder mode. `fresh` is true when the
root wrote the narrative for this compaction. `read_first` lists the earlier hand-written
records oldest first, and `narrative` names the record whose text the narrative is, or null.

At the next main-session `Stop` after the root writes a progress doc, the hook runs
`generate --strict`. Generation finds the root's hand-written records itself. They are
every `progress:<slug>` doc it did not write, walked from the active docs back along
their supersede edges to the last generated record. A generated record carries the
`Generated from sources by the long-running compaction hook at` line; generation never
judges a record by its title, session, or age.

The newest hand-written record's text becomes `## Root narrative` verbatim under
`_From doc <id>._`. Each earlier one gets a `Read first` line,
`` - Hand-written handoff, read it in full before `## Root narrative`: `ccn doc show <id7>`: <title> ``.
With no new hand-written record, the previous generated record's narrative and those
lines carry forward unchanged under one provenance line. In file mode, the hand-written
records are the files in `<plan-stem>-progress/` newer than its newest generated file.

A narrative the root edits into the active generated doc in place is fresh too. When that
doc changed after the hook's last generation, the `Stop` path runs `generate --strict`,
and `PreCompact` regenerates even inside its five-minute window. `generate` compares the
doc's narrative with the one its last generation wrote, found in `ccn doc history`. A
changed narrative stays verbatim under `_From doc <id>._`, and the restore counts it as
written.

*Prevents a repeat of the release-v3 handoff at 3:10 PM PT on 2026-10-10, where the root
wrote a 21 KB pre-compact handoff into generated doc 29751a1 in place, `PreCompact` folded
it into one 240-character digest line, and the restore said no narrative was written.*

`generate` refuses a record whose generated sections, everything above
`## Root narrative`, exceed 40,000 bytes. It writes nothing, prints one line naming the
largest `##` section and its largest `###` part when present, with byte counts, and
exits 5. The root narrative is never measured, folded, or truncated. The `Stop` path blocks with
"The drive's progress record is over its size cap. Trim the section it names, then
stop again:" followed by that line. `PreCompact` puts the failure first in the
compaction instructions.

*Prevents the release-v3 progress doc 91b91943 reaching 167,069 bytes from repeated
root dumps, a duplicated register, and unbounded task and lane lists (2026-10-05).
Also prevents the 164b13c4 handoff of 2026-10-10: the hook folded addendum aea91e3 to
three clipped lines, dropped the full handoff 384f2e4, and pointed the resumed root at a
generated doc with an empty narrative.*

The `root_context` Stop check `nudge_unrecorded_standing_rule`, described above,
prompts the root to record standing rules. Durable answers feed brief matching.
The handoff reads the approved register and live standing cci records.

**Lint.** Generation checks that `## Standing owner rules` exists and carries the
pointer for the current register doc. It checks that rule ids from the previous
handoff, including `#<seq>` and old `R<n>` ids, are carried or superseded. Durable
answer titles are not required.
The owner-gate check skips quoted lines prefixed with `  >`, inbox quotes, tasks,
and asks. A narrative line or live standing cci rule that gates on `owner's word`,
`owner approval`, `owner sign-off`, `owner GO`, or `reserved for the owner` needs a live
answer id; a missing citation is a finding. Each finding names its source: the rule's
`ccn <answer id>` or `cci #<seq>`, or the narrative doc and line.

The Stop path runs with `--strict`. On a narrative or standing-rule finding it writes
nothing and blocks the `Stop` with each finding and its remedy until fixed. End a
narrative line with `(answer <id>)`. For a standing rule, post
`cci post --drive <drive> --lane root --kind correction --re <seq> --topic standing --ccn <answer id> --text "<replacement rule>"`.
The plan's own owner-gate lines without live answer ids appear under `## Lint findings` and count in the
restore, but never block generation. `handoff.py lint` runs the same checks on any doc
or file, plus the plan when `--plan` is supplied, and exits 3 on any finding. It also
reads the register rows. A bullet that leads with a row's answer id or key must carry
that row's words verbatim; an id alone or a reworded quotation is a finding. The
sentence "no `standing-rules` register doc" is always a finding. These checks never
hold a compaction, and no lint check reads sweep staleness.

**Supersede and point.** Exactly one `progress:<slug>` doc is active. When the root
wrote hand-written records since the last generated one, `generate` augments the newest
in place: its body becomes the generated sections over its own text, and its title stays.
With no register, its own `## Standing owner rules` is named as the rules source.
Otherwise, `generate` edits the newest active generated record in place, else adds one.
The doc it wrote supersedes every other active `progress:<slug>` doc; earlier
hand-written records stay readable by id and are named in `Read first`. If the label
still lists another, `generate` exits 4 naming each id; the `Stop` path blocks on it, and
`PreCompact` puts the failure first in the compaction instructions.

*Prevents the release-v3 handoff of 2026-10-02 10:07 PM PT leaving hand-written dcef5bb
active beside generated 91b9194, which carried dcef5bb's body under fresher generated
sections, while the plan pointer and compaction instructions named dcef5bb. Owner
ruling: "the hook should augment the existing one if one was recently made for the
upcming compaxt".*

The hook adds one pointer line to the plan the first time. It starts with
`- **Progress (read first after any compaction):**` and names the active doc id and
`ccn doc show <id>`, or the generated filename for the file fallback. It names no drive
label: the slug lives in the hook's state and in the doc's own `progress:<slug>` label,
so a plan handed off as a standalone package stays free of drive names. A pointer that
already names the active doc is left byte-identical, whatever its wording. When the
active doc changes, the hook rewrites the line to the label-free form, and keeps a
package-relative path such as `records/docs/<id>.md` with the new id when the old line
carried one. The handoff never restructures the plan. Let the hook do the superseding
and pointer edit.

**`/compact`.** Once the progress record and pointer are ready, the hook starts a
detached background job at that main-session `Stop` and lets the stop through. The
job waits through orca for the terminal to go idle, reads the screen, and types
`/compact` only when the screen ends in the plain prompt: an empty `❯` box, its mode
line, and right-aligned notices. A picker, a permission or trust dialog, a menu, or the
agent list below the box means it keeps waiting, since typed keys would press its
options (owner, 2026-10-09). It rechecks every 30 seconds and gives up silently after
30 minutes. With `ORCA_TERMINAL_HANDLE` unset, the hook sends the owner one message to
run `/compact` by hand and blocks nothing.

**`PreCompact`.** In the main session only, never a subagent's, `PreCompact` runs
`generate` again unless the hook generated a handoff in the last five minutes and
that handoff's doc is still the newest progress doc. A progress doc the root writes
after the Stop's handoff is adopted here, so the instructions, the plan's pointer,
and the restore all name it. It reads the current register and carries the narrative
verbatim.
This covers Claude Code's auto-compaction before the root writes a narrative.

The instructions point to the plan, each read-first hand-written record, and the
active progress doc. They ask the summary to keep only in-flight details those records
lack. They carry no register text or title list. The copy bar caps hook messages at two
sentences and 300 characters. The instructions end with
`Quote: active progress doc: <id8>; the id in this summary wins over any id captured
earlier in the conversation.` so the summary carries the id the hook just wrote. With
read-first records the quote opens `hand-written handoff <id7>, ..., then` and the ids win.

**After compaction.** On `SessionStart` with source `compact`, the hook injects the digest
`generate` printed. When a register exists, the digest names it first with
`ccn doc show <register id>`. The next main-session tool result or prompt delivers
the register once. It binds every lane brief and outranks the summary.

Read each read-first hand-written record next, oldest first, then the progress doc,
then the plan. Without a register, the digest starts with those records.
Reload Skill `long-running` if its rules are gone.

Digest line two reads `Register: N owner-approved rules, M live standing rules.`
The `Open:` line counts owner asks, tasks, lanes, monitors, and lint findings.
The digest carries no clipped title list.

Claude Code's `PostCompact` hook cannot return context. Delivery after compaction
starts at `SessionStart` with source `compact`. The hook reads the newest register
doc and queues its body. Only compaction queues it again.

The next main-session `PostToolUse` or `UserPromptSubmit` clears the queue and
delivers the register once. Above 9,000 characters, the hook names the register
and tells the lane to read it in full with `ccn doc show <id7>` before acting.
Within that budget, the body follows a one-sentence header inside identical
12-tilde fences spelled `~~~~~~~~~~~~`. The copy bar exempts the fenced body.

*Prevents the release-v3 loss of October 4, 2026: titles-only carry dropped the
ec2881e ruling "No: Pulumi state is the only truth" and left later lane briefs
without it (cc-notes note d07074b).*

Claude Code moves `SessionStart` context over 10,000 characters to a file and injects
a 2 KB preview. capt-hook merges every pack's `SessionStart` context into one output;
cc-notes' compact restores take 7,500 of it, split between 4,500 for answers and 3,000
for touched records. At `2026-10-01 13:55Z`, the root's merged restore was 20,596 bytes.
The long-running pointer, printed last, never reached context.

If generation failed, `SessionStart` gives the reason and asks the root to write the
progress record now. If the record's narrative predates the nudge, it adds that the
root should write one when convenient; a hand-written record written since the nudge, or
an in-place edit, never draws that line. The skill stays active across compaction.

On `SessionStart` with source `resume`, an active drive's newest progress record, by
`ccn doc list --label progress:<program>` or the newest file in the progress folder,
is injected under the same 2,000-byte budget: a line naming `ccn doc show <id7>` and
the plan, then the head of the record's body. Resume keeps the prior context,
so the hook does not re-inject the register.

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
keeps working; it ends the rotation cycle for good. The hook never asks that lane
again, whatever its context does afterwards. The hook nudges the root once: the lane
keeps running in place, nothing to do. After its own compaction, the lane reads its ledger
and saved cursor and continues. Never `TaskStop` or respawn a flushed lane, and
never spawn a second agent under a live lane's name. An `open-pr:pr-watcher` resumes
from its state file after its own compaction.

A lane that drops below its line through its own compaction or leaves
`background_tasks` because it stopped or rotated also ends the cycle. A lane that
received a `STAND-DOWN` message from `team-lead`, still queued in its teammate inbox
or already delivered into its transcript, is never asked or escalated, since its
successor already owns the work. Neither is a lane that has a `<lane>-handoff`
subagent and a live numbered successor: `alerts-watch` once `alerts-watch-2` runs,
`desk-3` once `desk-4` runs, nor a lane a successor's brief names on a
`Replaces: <lane>` line. Numbered siblings with no handoff are still asked.

A live, awake, unfinished lane over its line gets a `ROOT-ACTION` after 10 minutes
without a reply starting `flushed` to its first ask, subject to the cap below. A later
main-session `Stop` queues ``ROOT-ACTION `<lane>`: Rotate it by hand: ...``. This line
and the no-inbox `ROOT-ACTION` ask share a cap of one line per lane per two hours;
a new ask does not reset it. The escalation has three steps:

0. Stop the old lane with `TaskStop`, its name as `task_id`. Never `SendMessage` it
   again, a stand-down included: a message resumes a stopped lane on its stale brief.
1. Spawn `<lane>-handoff` as a subagent from
   [reference/handoff-subagent-brief.md](reference/handoff-subagent-brief.md) to write
   a cc-notes doc. Pass the previous handoff's doc id, if any. It returns the new
   doc id, plus at most three lines on what it could not reconstruct.
2. Spawn `<lane>-N+1` from that file's successor brief, then the old lane's brief, the
   doc id, read with `ccn doc show <id>`, and the cursor it names. `alerts-watch`
   becomes `alerts-watch-2`; `desk-3` becomes `desk-4`. The successor's first act is
   `drive.py lane-status <lane>` and a check that no process still works in any
   worktree it lists; with one live, it writes nothing and posts a `blocker` to root.

Each firing replaces that lane's previous queued `ROOT-ACTION` line instead of
adding another. The root acts on it in the turn it arrives. A reply starting
`flushed` cancels the rotation.

A `SendMessage` to a rotated or stood-down lane resumes it on its stale brief. The pack
rewrites every such message, from any sender, to the end of the lane's successor chain,
which it reads from this session's rotations and from cci's stand-down records
(`cci post --lane root --kind stand-down --to <old> --successor <new>`, read back with
`cci who --successor-of <lane>`), and reminds the root to `TaskStop` the old lane; a
message that mentions a stand-down is never forwarded, only warned. Spawning `<lane>-N+1`
beside `<lane>-handoff`, or any successor whose brief carries `Replaces: <lane>`, rotates
the old name, and the pack posts that stand-down record itself, so cci also delivers to the
successor every record a desk, script or lane addresses to the old name. A `TaskStop`, by name, `<name>@<team>`, or agent id, or a root `STAND-DOWN`
retires a lane with no successor: a lane's or worker's message to it goes to `main`
instead, and the root's is warned once cci records the stand-down. No case blocks.
Respawning under the name clears both.

The same swap applies to a lane that died or outgrew its line before any ask. The
root never reconstructs a handoff inline. It never opens a lane's transcript, its
receipts, its cursor files, or a runtime listing such as `orca orchestration
task-list` for a rotation. The handoff subagent reads all of them in its own
context; the root holds only the doc id it returns.

The session's hook state directory holds `rotation_state.json`. Its `retired` map
names each retired lane's successor, or null for a lane stopped with none, and
`blocked` lists the rotated lanes whose first `SendMessage` the pack blocked. Its
`timeline` list records one entry per `ask`, `flushed`, `compacted`, `finished` or `gone`
event. An `ask` entry holds `via` as `inbox` or `root`, `tokens`, and `line`. A
`flushed` entry holds `ids`, the first line's tokens containing a digit. A
`compacted` entry holds `tokens`. An `escalate` entry holds its first `at`, latest
`last`, `count`, and `tokens`; consecutive escalations for the same lane update
that entry.

*Prevents stopping and respawning lanes from ending live sessions during the
release-v3 drive (2026-09-30).*

*Prevents a rotated lane resuming on its stale brief from the root's stand-down
`SendMessage`, pushing to its successor's PR and entering the successor's conflict
worktree until the root ran `TaskStop` by name 25 minutes later (2026-10-27).*

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
- `orca-launch.sh` gating `worker-start` on `terminal list`'s `agentIdentity`, which reads
  `claude` from the process or first hook before the TUI renders; `worker-start` refused
  `agent_unconfigured` and lost the lane (R1470, 2026-10-08). Gate on `terminal wait --for tui-idle`.

## Checklist before every tool call

1. Could a lane return this as ≤10 lines? → delegate.
2. Is this raw data - log, JSON, resource list, webpage, diff, PR, Slack thread, plan artifact? → delegate to a reader lane.
3. Does a lane already own this question? → scoped resume, do not look.
4. Am I about to wait? → fold the wait into the lane that acts.
5. Am I about to restate status? → send nothing.
6. On each new owner ask, record it with `ledger.py ask` and dispatch its lane this turn; never queue it behind a busy lane.
7. Am I about to report a milestone or answer a status question? → first dispatch every owner ask still unstarted, then report as the owner table with its retro column and a gate on every READY-HELD row.
8. Before stating a PR's state, check `ccx vcs status` or the `(#N)` squash on a fetched trunk; never report it from the plan file.
9. Am I about to relay an ETA for a green PR? → check its gates and label it now if it is a priority PR.
10. Before saying something is assigned, read the summary's `LOST` lines first. Never call an ask done before `LIVE`.
11. Am I about to relay one lane's head, contract, or decision to another? → it goes on the bus, and the other lane reads it.
12. Am I about to create, relaunch, or answer an Orca lane's routine traffic myself? → issue `desk-runner.py relay` or `launch`; read its escalation file, never run a model desk or a second mailbox loop.
13. Before waiting on a desk relay for a priority PR or an owed item, read the PRs in one batched `ccx vcs pr status` call and dispatch every owed item with no PR now.
14. Did a tool just refuse, fall back to `# ccx:raw`, or need a step done by hand, or am I running the same command a third time? → spawn its tooling lane this turn (fix, PR, merge, release, install) and keep going.
15. Is a production alert active? This turn, `incident.py open` with the owner's grants and `incident.py run` in the background, before any verdict, depth mandate, or question. After that, answer only the executor's decisions and relay its `opened`, `live`, and `closed` milestones in Pacific time.
16. Did I just spawn an Agent lane, take an owner ask, or consume its deliverable? → `TaskCreate`/`TaskUpdate` this turn; a lane's word alone completes nothing. Runner actions use their action records under R5, never shadow tasks.
17. Am I about to ask the owner anything (a question card, a planning-round board, a lane's question list)? → check each question against the plan's decisions, `ccn answer list --label scope:durable`, and memory first; apply what is settled and ask only the rest.
18. Am I about to swap a lane because it missed `ROTATE`, outgrew its line, or died? `TaskStop` the old lane by name first and never `SendMessage` it again, spawn `<lane>-handoff` from `reference/handoff-subagent-brief.md`, take back only the doc id, then spawn the successor from that file's successor brief with that id; never open the lane's transcript, receipts, or runtime listings myself.
19. Before writing a desk brief or handoff with an owner rule, record each standing
    rule as its own cci `go` record with `--topic standing` and `--ccn <answer id>`
    under I6. Never mark it done. Only a later `correction` record with
    `--topic standing --re <seq>` and a later answer replaces it.
    A consolidation lane proposes at most 35 register rules for the owner to approve.
    Handoff generation only reads the newest `standing-rules:<slug>` doc.
    Claude lane hooks inject the register once at start, or a read instruction
    above 9,000 characters. They also deliver matched answers.
    Codex briefs must paste the register body verbatim.
    List each standing rule's `#<seq>` id, never a range.
    READY names the rulings the diff touches.
20. Did the owner just paste a Slack link, or am I about to react, reply, or write Slack copy? → spawn the Slack lane (`reference/slack-lane-brief.md`) and the doing lane this turn; the root never composes Slack copy.
21. Am I about to reply to the owner or ask a question? → times in Pacific with no zone
    label; plain words, with no codename, inbox id, or answer id; a 'why did you…'
    answered in this turn in my own words; a delay I caused named as mine.
22. Before briefing, ruling on, or shipping a subsystem change, quote the owner's
    rulings verbatim with the entry point as a symbol at `file:line`. Read the
    lane's design-check contract on the bus and withdraw it only on a mismatch;
    never make a lane wait for a confirm.
    Ship the whole design or hold.
23. Did a subagent's completion notification just arrive, or am I about to spawn? →
    `TaskStop` every finished agent of mine this turn; never report the subagent
    limit as a blocker.
24. Did I or a lane just run a destructive or state-changing production operation on
    the owner's authorization? → its line goes on the `production-operations` log
    this turn. Is a lane or the alerts desk about to name a mechanism? → it reads
    that log's last 24 hours first.
25. Did the owner just rule, or repeat a ruling? → record the words verbatim, write
    and confirm its implications this turn, and apply it to every live artifact and
    lane. Before presenting a board, brief, handoff, or plan, sweep it against every
    ruling's broadest reading (R19b).

Apply D3 to priority PRs before delegating. A call that survives all twenty-five decides
something no lane can decide for you; everything else is a lane.
