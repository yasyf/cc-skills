---
name: long-running
description: Hard rules for orchestrating multi-lane work without burning the orchestrator's context - routine ground truth arrives as a lane's verdict, anything with a body is a lane, every wait folds into the lane that acts, no lane parks and no lane is re-briefed, state lives in cc-notes and the task list, an open-PR ledger records every PR and owner ask, lanes self-enqueue their stacks against a root-owned holds list, a landing-desk reconciles landings, an orca-desk owns worker traffic, each owner-named number-one outcome gets a priority desk, and a lane bus carries decisions, heads, contracts, blockers, and asks from each lane's cursor. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs at once, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
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

The standing subagents are `landing-desk`; `orca-desk` when any lane runs
through Orca; `alerts-desk` when the drive touches production; and one
priority desk per owner-named #1-priority outcome while that outcome is open.
The root spawns a subagent for every task with a body, including its own routine work.

This skill is the context-discipline layer over `~/.claude/CLAUDE.md`. Fan-out shape comes
from §Parallelize Independent Work, lane behavior from §Delegation, per-lane model and
effort from §Model Routing, and depth of checking from §Verification Budget. None of
that is repeated here.

## The nineteen hard rules

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
Complete what was consumed; re-own or delete the rest. Every lane receives the whole
task list on every wake. The root deletes a completed task with `TaskUpdate` status
`deleted` once its result is in cc-notes or the plan.

Report to the user on milestones or when they must act, never per event. Once
`long-running` is invoked, the compaction hook nudges the root to write a new progress
doc, then handles superseding, the plan pointer, and `/compact`, as Compaction handoff
describes. The plan stays the drive's mandate and decisions. *Prevents the forced
mid-drive handoff with nothing written down to hand over.*

The pack's `task_list` hooks enforce this. They nudge when a spawned lane has no task by
turn end, when a lane's done report names a task still `in_progress`, and when an owner
ask has no task after three tool calls or by turn end. Every 20 turns they list the drift
in one line. Treat each nudge as a `TaskCreate` or `TaskUpdate` due now.

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

**R9. Orca changes nothing about R1-R2.** Running workers through Orca does not license
the root to do lifecycle, inbox, script, or retry work inline. Worktree and terminal
creation, relaunch sweeps, the `check --wait`/ack loop, routine replies from a lane's own
brief, and any helper script belong to a dedicated `long-running:lane` subagent, the
orca-desk, spawned beside the landing-desk before the first Orca worker. The root holds
decisions, owner asks, and rulings; it hears from the orca-desk only as ≤5-line ruling
requests and `worker_done` outcomes that need action.

*Prevents the root spending its window relaunching 33 lanes and answering scope questions the briefs already settled (release v3, 2026-09-30).*

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
time with parallelism capped at `-j 8` or the tool's equivalent. Before any local run,
a lane reads the 1-minute load; above the core count, it finishes its current command
and starts no build.

*Prevents release-v3 lanes pushing the box to load 103 with local rust builds on
2026-09-30, until the 12:35Z mass kill.*

**R16. An active production alert gets its fix lane in the same turn.** A Datadog
monitor in Alert, a Sentry alert, an `#outage` report, an on-call page, or an alert
link from the owner is P0 from the moment it is seen. Run this checklist that turn,
before anything else.

- (a) Spawn the fix lane with apply authority in its brief. PR through the repo's
  submit skill; break-glass or hand apply pre-authorized at `0 deletes` / `0 replaces`,
  counts reported first, runbook-logged. Any delete or replace goes to the owner.
  Start at the code path the alert names. Inside a drive, append
  `orca-desk: launch <name> NOW` to the desk's inbox; the desk runs
  `scripts/orca-launch.sh <name> sol xhigh <brief>` for an Orca codex worker on
  `gpt-6.1-sol`, fast tier, `xhigh`. Outside a drive, use an inline background
  `codex:codex-wrapper` (`codex-ask -m sol`) on the same model, tier, and effort.
  On a miss, use Claude Opus 5.5 (`claude-opus-5-5`). Never fable or astra.
- (b) Spawn an evidence lane for telemetry, logs, and the deploy timeline, feeding
  the fix lane by name. It gates nothing.
- (c) Fence the target from further applies and deploys in the same inbox line as
  both launches. The fence exempts the fix lane's apply under (a).
- (d) Send a one-line owner report at spawn with the alert, both lane names, and authority given.
  Send one line at mechanism and one at fix-live. Never inside a status wall.

**Never between the alert and (a).**

- A "real-or-not" or "ours-or-not" verdict
- A mechanism-depth mandate
- An `AskUserQuestion`
- Treating a mute as resolution

Diagnosis redirects the fix lane; it never precedes it. An owner's "if it is real,
fix it" means fix lane plus evidence lane, not a verdict gate.

Check each incident lane every 10 minutes. At 15 minutes without a mechanism, start
a second lane on a different model in parallel (Opus 5.5 after sol); keep the first
running. `reference/active-alert-brief.md` holds both briefs; O15 and
`reference/orca-workers.md` hold launch mechanics.

*Prevents the 2026-10-01 release-v3 failures, when the fix lane started 5.6 min late
behind a verdict gate and the owner's sol routing was applied 6.4 min late.*

**R17. Owner routing applies to the next spawn and to every live lane on that problem.**
An owner's routing or process instruction applies in the turn it arrives.
Both "use Orca sol for incident response" and "pass the fast tier flag" count.
Stand down every live lane on that problem and relaunch on the named route.
Never record it for "new lanes" only or weigh it against the routing table.
Never keep a lane because it is "already deep in the code" or defer the ruling to
the lane editing the skill.

*Prevents the 2026-10-01 instruction at `04:14:42Z` taking until `04:21:03Z` to apply,
with three lanes and two PRs (#28594, #28598) for one fix.*

**R18. A ruling reaches a lane the way it reads.** A desk looping on an inbox file
gets the ruling as a line in that file with the verb it keys on
(`orca-desk: launch`), never a policy sentence or `SendMessage` alone. A running
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

The pack's `settled_questions` hook blocks the root's first asking call in a burst (`AskUserQuestion`, `cc-present start --doc`, `push`, `update-block`) while a drive is active and quotes the tracked plan's Decisions section.
Re-issue the call after filtering; asking calls then pass for ten minutes.

*Prevents the release-v3 parity board of 2026-10-01 (05:51Z), which asked the owner seven keep-or-drop questions (ack gate, finish, on-call swap, dev-check card, divider rows, Start button, start refusals) and a DAG question the plan's Decisions and §TM-dag already answered, because the root encoded "nothing is dropped" with an "or an explicit owner drop" exit and forwarded the audit lane's question list unfiltered: "the board showed those cards because you asked those questions in the first place instead of following the plan."*

## The landing desk and its ledger

On a drive where many lanes open PRs, the root is the wrong place for their reports.
Each report is a message in the root's window, and each landing is a wait. The desk is
one long-lived lane, `landing-desk`, that takes those reports and reconciles landings.
Lanes enqueue their own stacks under D1; the desk enqueues any ready stack they leave
unqueued. The root owns the holds file and checks and enqueues priority PRs under D3.
It receives P0 lines immediately and a summary every 30 minutes.

`scripts/ledger.py` is its one tool. It uses `ccx vcs pr state` for refreshes and
`ccx vcs pr watch` for transitions. Both read ccx's machine-wide pull request cache,
which polls each repository at most once every 30 seconds. Its one store is a cc-notes
ledger with a row per PR our lanes shipped. The holds, the routing, the label history,
and the landing are fields on that row. Lane messages are `msg/<seq>` rows and owner
asks are `ask/<seq>` rows beside the PR rows. cc-notes finds the ledger through the
working directory's repository, so a lane outside that checkout passes
`ledger.py -C <checkout> <verb>`.

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

Where the checkout carries an enqueue script, call it directly as `stack-enqueue --hold <held file> <prefix top>`, then `ledger.py report` the enqueue. The desk records it on refresh as `in the queue, labelled outside the desk`. `ledger.py label` cannot pass `--hold` yet. Where the repo has no enqueue script, run `ledger.py label --repo <repo> --ledger <id> --pr <prefix top> --expect-head <sha> --checkout <its worktree>`; the holds the desk has mirrored into the ledger are the guard.

The lane keeps `ccx vcs pr watch` on the stack. On ejection or conflict it rebases and re-enqueues at once. It is not finished until its squash `(#N)` is on the base branch, and never ends a turn with a green, approved, unheld bottom prefix unenqueued. After the prefix lands, the lane restacks the PRs above it with `ccx vcs stack submit`. No ruling is needed.

Only the root appends or edits the holds file. Each line names held PRs as `#<n>` and whole held lanes as `lane:<name>`, then the reason. Every `#<n>` in the file is held, so a reason names another PR without the `#`. A lane never self-enqueues a prefix containing or sitting above a held PR, including any PR of a held lane. It reports `held` on its tip, names the held PR, and leaves that PR and those above it to the root to release. The desk mirrors each entry as a `ledger.py hold` with that reason so `label` refuses it, and lifts it when the root removes the line.

Lanes send the desk the three-line report of PR, full head sha, and verdict. The root receives `P0` and `RULING NEEDED` lines immediately and the 30-minute summary. If the owner flags a PR as priority, or it blocks a release or a user, the root checks its gates and enqueues it itself in the same turn under D3. That approval covers only the head the owner named; if the PR gains commits or scope, the root gets fresh approval naming the new head. Never relay an ETA for a green priority PR.

*Prevents green approved stacks waiting on one serial desk, which prompted the owner's 2026-09-30 ruling to enqueue more than one thing at once. Also prevents a tip enqueueing held parents. Tip #28102 sat above held #28081 and #28082 on 2026-09-30, kept out of the queue only by red CI.*

**D2. Track and grade our lanes' PRs.** The pack's PR hook, `ledger.py report`, `ledger.py register`, and an explicit `refresh --pr` are the only paths that open a PR row. Owner ask rows open only through `ledger.py ask`. The desk grades every tracked PR without waiting for a lane report at its current head. The desk never lists the repository's pull requests; a PR it cannot trace to one of our lanes stays outside the ledger and its counts.

*Prevents routing comments and rebase orders landing on other engineers' PRs, which one repo-wide sweep did twenty times in an hour.*

**D3. Enqueue every ready bottom prefix in parallel.** `<tip>` is the top of the largest contiguous bottom prefix whose PRs pass. `ledger.py label --pr <tip> --expect-head <tip-sha> --checkout <path>` walks base refs to the repo's default branch and re-reads every PR in that prefix. Each must be open, with approval in force, successful commit status, no failed checks, and a completed, successful latest `ai-review`. Approval on any commit counts; a dismissed or withdrawn approval does not. Each head must have no desk hold or lane `held` verdict on that head, no prior label or pull, and no conflict with its base.

Allowed `mergeable_state` values are `clean`, `behind`, and `has_hooks`; a PR above the bottom of a stack may also read `unstable` while Graphite's `mergeability_check` is its only unfinished check. `--expect-head` takes a lowercase hex prefix of the tip's sha, 7 to 40 characters long. An untracked downstack PR or an orphaned base refuses the whole attempt.

An open child outside the prefix is allowed only when a lane tracks its ledger row and its head carries Graphite's `Graphite / mergeability_check` check run. Restack any other child through Graphite or retarget it to the trunk before adding the label. `--expect-head` pins the tip you graded; each call re-reads its tip immediately before grading it. A report is not required; the forge decides whether a head is red or conflicting.

When the prefix passes, its top goes into the queue with every PR below it as one Graphite batch through `.agents/skills/submit-pr/scripts/stack-enqueue --hold <held file> <prefix top>` when the checkout carries it, which is the monorepo's rule, and otherwise through `ledger.py label` with one `merge` label on the prefix top. Report a direct enqueue with `ledger.py report`; the desk records it on refresh as `in the queue, labelled outside the desk`. `stack-enqueue` applies its own gate first; when it names a blocker, the requested prefix is refused with its per-PR lines. `stack-enqueue --check --hold <held file> <prefix top>` gates without enqueueing. A queue that drops part of the prefix after the enqueue counts as enqueued, so the same heads are never queued twice; read each PR's Merge activity comment.

The lane enqueues first under D1, the desk reconciles, and the root enqueues priority PRs. Each pass the desk enqueues the largest green, approved, unheld, unqueued bottom prefix of every open tracked stack. It starts one `stack-enqueue --hold <held file> <prefix top>` per ready prefix together in one Bash call, each backgrounded with `&`, then `wait`, collecting each call's output. Never enqueue one stack per pass.

Each call re-reads the root's holds file and builds a fresh numeric held file under D1, extended with every open ledger row whose lane is named as `lane:<name>` in the holds file. Read those rows with `ledger.py show --ledger <id> --json`, across the whole ledger even for a shard. Priority desks and shards use the same held set. A stack refused as `held` waits for the root; never treat it as a red or route it.

Where the repo has no enqueue script, use `ledger.py label --pr <tip> --expect-head <tip-sha> --checkout <path>` instead. `label --all-clean` walks stacks one at a time, so it is the fallback sweep only where the repo has no enqueue script.

For priority PRs, the root reads their gates itself in one batched `ccx vcs pr status <n1> <n2> ...` call and enqueues in the same turn. Priority approval covers only the named head under D1. The desk records an outside label on its next refresh as `in the queue, labelled outside the desk`; it does not treat it as a bypass.

*Prevents a green tip enqueueing a red parent, an ejected head entering the queue again unchanged, and a green priority PR waiting for the owner to queue it by hand.*

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

**D10. Enqueue on the report, and reconcile every three minutes.** When a lane reports `clean`, enqueue its stack's largest ready, unheld, unqueued bottom prefix in the same turn. Every three-minute pass reads every PR number in one `ccx vcs pr status <n1> <n2> ...` call and the Buildkite build list, never one REST call per PR. Desks and shards stagger their reads by a minute at `:00`, `:01`, and `:02`.

Enqueue all ready prefixes together under D3. A report is not required, and a lane's `red` or `conflicting` verdict does not refuse a head the forge passes. One refused prefix does not stop the rest; its refusal is recorded on each of its rows and routed under D14.

*Prevents clean PRs waiting for a 20-minute pass that labels one report at a time, until the owner enqueues one in Graphite by hand.*

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
shard runs `refresh`, `landed`, `route`, D3's parallel enqueues, and `stale` on its own
rows every three minutes.

Stagger desks and shards by a minute at `:00`, `:01`, and `:02`.
Read all PR numbers in the pass with one `ccx vcs pr status` call and the Buildkite
build list. The refresh lock is keyed on the ledger, so shards never race a sync.

Lanes keep reporting to `landing-desk`; the main desk types every message in, labels
on each clean report, and alone sends the root the summary. *Prevents one desk's pass
growing with the board until its pass takes 20 minutes.*

**D13. Register each lane's stack when it starts and whenever it opens a PR.** The root registers the drive with `drive.py start --ledger <id>`. The pack's PR hook then registers every PR a drive session or Orca worker opens under the lane's name. It records the head when the command prints one. This covers in-process subagents and teammates at any depth; `orca-launch.sh` passes the drive to Orca workers.

Lanes still register a unique branch prefix ending in `/` at spawn with `ledger.py register --ledger <id> --lane <name> --branch-prefix <prefix>` and still `report` verdicts. Hand-register a PR only when the hook's context line says it was not recorded, using the command it gives. Read recorded PRs with `ledger.py list --ledger <id> [--lane <name>] [--open] [--json]`.

Each refresh makes one `ccx vcs pr state --repo <repo> <PR numbers> --lane-prefix <prefix>` call with every row's PR number and any explicit `--pr` numbers, repeating `--lane-prefix` for each registered prefix. The same read returns the lanes' open PRs from ccx's machine-wide pull request cache. Every discovered PR enters the same batch and takes the same gates as a reported PR; the desk never lists the repository's pull requests.

*Prevents three PRs a lane never reported sitting unmerged for hours.*

**D14. Grade a moved or unreported head like any other.** Every tracked current head takes D3's gates without a report. For each refused head, send `new head <sha9>: <blocker>` to its lane once per head and blocker. A head that moved since the refresh is graded on the next pass, without a route; red CI and conflicts go through `route` and get no duplicate message from the batch.

*Prevents two heads that moved after their reports being ignored for hours.*

**D15. Name what the desk is waiting on and ping the lane in the same pass.** The summary's `waiting:` line groups tracked open PRs as `ungraded`, `refused`, `red`, and `held`. An `ungraded` row lacks a label and a grade at its current head; a `refused` row has a label refusal at that head, with the reason in `stale` or `show`. A `red` row has a CI failure or a `dirty` or `blocked` mergeable state; `held` covers desk holds and lane `held` verdicts on the current head. In the same pass, run `route` and D3's parallel enqueues, and send each lane the messages they print. `summary` requires `--repo` and `--checkout` and settles landings first, so a landed row never appears as pending.

*Prevents the desk waiting silently while five ready PRs sat unmerged for hours.*

**D16. The green bottom of a stack lands now.** Enqueue the largest contiguous bottom prefix whose PRs are green, approved, and unheld as one Graphite batch. Never wait for the top of a stack to go green before landing a green bottom. PRs above the prefix wait on their CI, review, or hold. After the prefix lands, route a restack of the first PR above it to its owning lane; the lane restacks the remaining PRs with `ccx vcs stack submit`. For Orca lanes, append the route to `inbox/orca-desk.md` under D5.

*Prevents the wait for unfinished PRs above a green bottom that the owner ruled out on 2026-10-01. Restacking children and rerunning their CI after the prefix lands is an accepted cost.*

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

`refresh` regrades the rows the ledger holds and merges the forge's fields into them, so
the fields the desk writes are never overwritten: `lane`, `declared_intent`, the holds,
the routing, the label history, and the landing. A refresh that cannot reach the forge
exits non-zero and writes nothing. The records live on `refs/cc-notes/*` and survive
compaction, a session restart, and a handoff. After compaction, the same `landing-desk`
reads its inbox, holds, routes, label history, and landings from the ledger and resumes
from its cursor. None of that goes into session memory or the plan file.
See Lane rotation.

## The orca-desk

Orca worker traffic outside a priority desk's lanes belongs to one long-lived
`long-running:lane`, `orca-desk`.
It creates worktrees and terminals, checks receipts, reads the worker inbox, and
answers what the briefs already settle. The root holds decisions, owner asks, and
rulings. It receives only ≤5-line ruling requests and `worker_done` outcomes that
need action.

Spawn it beside the landing-desk before the first Orca worker, whenever a drive runs
Orca workers. `scripts/orca-launch.sh` owns launches and relaunches;
`scripts/orca-check.sh` owns each blocking inbox check.
Its `--stale --inbox <inbox file>` mode flags aged unread worker messages and unread work for completed or failed dispatches on every pass.
`reference/orca-desk-brief.md` is the desk's brief, ready to paste;
`reference/orca-workers.md` holds the launch recipe and script interfaces.

**O1. Launch every Claude worker through `scripts/orca-launch.sh`.** Worktree and terminal
creation, relaunch sweeps, retries, and helper scripts stay in the desk. The root
dispatches the lane's brief and rules on exceptions.

*Prevents the root relaunching 33 lanes inline (release v3, 2026-09-30).*

**O2. Count a `ready` receipt or O15's `unsupervised` result.** A supervised launch prints
`<lane> ready task=<id> dispatch=<id> terminal=<handle> worktree=<path>`.
For Claude, the terminal runs the custom command in bypass-permissions mode; the
script checks its screen for `bypass permissions on` before printing that line.
Anything else is a failed launch.

**O3. Keep one foreground check loop and acknowledge deliveries.** Run
`scripts/orca-check.sh`: one blocking `check --wait --types worker_done,escalation,question`.
Process the whole batch, then pass the printed `delivery <id>` as `--ack <id>` on
the next call. That id is `result.deliveryId`. A message id acknowledges nothing;
an unacknowledged batch replays.

**O4. Answer from the brief, escalate the rest.** Answer routine questions from the
lane's brief file. Forward anything it does not settle to the root with the message
id, lane, and 2-4 options, in ≤5 lines.

*Prevents the root answering scope questions the lane briefs already settled (release v3, 2026-09-30).*

**O5. Relay the root's ruling to the current dispatch.** Answer a question with
`orca orchestration reply --id <question message id> --body "<ruling>"`;
only a reply to that original id wakes an `orca orchestration ask` wait (R56).
Send other guidance with
`orca orchestration send --to dispatch:<current dispatch> --type dispatch --subject "<subject>" --body "<ruling>"`.
Read the current dispatch before sending; a prior receipt is not a live address.

**O6. Never end a session.** A settled dispatch keeps its terminal open and idle.
Claude and Codex sessions, Orca, terminal hosts, and PTY daemons are never stopped,
signalled, released, or closed; working workers have no cap, and the only launch
throttle is a 1-minute load average above the core count. Forward only outcomes
that need the root to act.

*Prevents the 12:35Z kill that ended every session of a drive (release v3, 2026-09-30).*

**O7. Record every relayed ruling.** Append the message id, lane, dispatch, and ruling
to the drive's cc-notes log with `ccn log append`. A sent reply without its log entry
is unfinished work.

**O8. Check liveness hourly.** Run `orca orchestration task-list --run <run>` and
`orca orchestration worker-show --dispatch <id>` for each active worker. A worker
with no heartbeat for 30 minutes goes to the root with its current dispatch and
terminal state. A stale heartbeat never authorizes a relaunch. Resume the existing
session in place; a separate successor must never stop it or duplicate its active work.

**O9. Never re-brief.** Edit the lane's brief file, then send its pointer to the
current dispatch with `send --type dispatch`. Never paste the whole brief into a
message or start a second lane to carry a follow-up.

**O10. Acknowledge stale questions without answering.** For a question from a stopped
or superseded dispatch, record it as stale and acknowledge its delivery. Never
answer it or send its answer to the replacement worker.

**O11. Treat a capacity fallback like an ask.** Under load, `orchestration ask`
returns `capacity reached`. The worker sends `--type question` or
`--type escalation` instead and keeps working on everything that does not depend
on the answer. The desk applies the same brief check and ruling path to those messages.

**O12. Follow Desk inboxes before every wait.** Every inbox wait is at most
60 seconds; `orca-check.sh` defaults `ORCA_CHECK_TIMEOUT_MS` to `60000`.
The same wait limit applies to the landing-desk.

*Prevents 26 rulings sitting undelivered for over an hour in `SendMessage` to a looping desk (2026-09-30).*

**O13. A lane stuck on a prompt is a desk bug.** Every worker's launch command
disallows `AskUserQuestion`, `EnterPlanMode`, and `ExitPlanMode`. Every pass, the desk
reads `observation.agentWait` from `orca orchestration worker-show` for each
in-progress dispatch, and answers or escalates any prompt it names in that pass.
`ledger.py summary`, run inside an Orca terminal, prints `WAITING-ON-PROMPT` for any
worker parked five minutes or more.

*Prevents lanes sitting for hours on an AskUserQuestion only their own terminal showed (release v3, 2026-09-30).*

**O14. Launch nothing while the box is saturated.** Before every launch or relaunch,
read the 1-minute load. While it exceeds the core count, start no new worker; hold the
launch until it falls. This is a standing rule, not a per-drive ruling.

*Prevents the load of 103 behind the 12:35Z mass kill (release-v3, 2026-09-30).*

**O15. A codex Orca lane runs on Orca's codex agent.** Launch it with
`scripts/orca-launch.sh <lane> codex xhigh <brief>`; a `codex` or `gpt-*` model runs
`worker-start --agent codex --model <id> --effort <level>` with no custom terminal,
since Orca's codex default arguments already skip approvals. Never launch a claude
worker whose brief calls the codex skill. An inline lane, an Agent-tool subagent or
the root's own turn, still uses `Skill(codex)` or `codex:codex-wrapper`, and a one-off
question still goes to `codex-ask`.

Incident lanes use `scripts/orca-launch.sh <lane> sol xhigh <brief>`, model
`gpt-6.1-sol`, in a top-level (`--no-parent`) worktree. `worker-start` has no
service-tier flag, so the script creates the terminal itself with
`codex ... -c service_tier=fast` on its command line and starts the worker with
`--terminal`. Only sol lanes run fast. Orca's codex runtime config stays
`service_tier = "default"`; no script or lane edits it.

On 2026-10-01, supervised dispatch `ctx_a1c0260ecb02` started on fast but failed at
`agent_readiness` with `timeout`, as did the root's two hand-launched sol workers;
codex was at its prompt and the spec never arrived. Earlier custom-terminal
dispatch `ctx_e6b256d5c0c8` did read `ready`. On a readiness timeout with a live codex
or sol terminal, the script delivers the spec pointer itself and prints
`<lane> unsupervised task=... dispatch=... terminal=... worktree=...`.
Count it as launched and report it to the root. It has no Orca `worker_done` or
escalation plumbing; the lane reports through its inbox/bus file. The desk starts
the script asynchronously and never waits on the `agent_readiness` timeout.

## The alerts desk

Production monitor traffic belongs to one long-lived `long-running:lane`, `alerts-desk`,
model sonnet, effort low. Spawn it beside the landing-desk whenever the drive deploys,
applies, releases, or migrates. `scripts/monitor-watch.py` owns the polling and the
dedup; `reference/alerts-desk-brief.md` is the desk's brief, ready to paste.
`reference/active-alert-brief.md` holds the fix and diagnosis lane briefs.

**A1. Report monitor transitions, and nothing else.** The desk keeps one Monitor on
`monitor-watch.py watch` over the drive's monitors, by tag glob such as
`release-target:*` and by named id. It messages the root only on a move into Alert,
Warn, or No Data, or a recovery to OK: monitor id, name, transition time, and a
one-line first read. Never on an unchanged state or a timer tick. It owns no fixes
and posts nothing to Slack.

Each alert is P0 for the root. Spawn a fix lane and a diagnosis lane in parallel
that turn under R16, with no verdict gate. The monitor's
targets stay fenced from deploys, applies, and enqueues until it recovers or diagnosis
clears the alert; the fence never blocks the fix lane's own apply under R16.

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

These rules apply to the landing desk, orca-desk, priority desks, and shards.

**I1. Give every desk one append-only inbox file.** The root appends numbered lines
`R<n> ...`. It never rewrites or truncates the file.

**I2. Read from the saved cursor at the top of every iteration.** Read before any
other work, act on each line, and advance the cursor every iteration. Every report
names the cursor as `cursor R<n>`.

**I3. Read the inbox before reporting a wait on the root.** Check for the answer
before saying an item is waiting on the root.

**I4. Never `SendMessage` a running desk.** When its reported cursor stays behind
the root's last line for more than one iteration, the root appends one inbox line
naming the unread range and records the stall in its progress record. The desk
reads that line at the top of its next iteration (I2). Resume only a desk that
has already reported and ended its loop, through Scoped resume, in place with the
same identity.

*Prevents the 26 rulings left unread in a looping desk on 2026-09-30.*

**I5. Keep about 15 lanes per desk.** Split early into a second desk or a priority
desk.

## The lane bus

`SendMessage` is fire-and-forget into an inbox. A message lands while its reader is
idle or mid-poll, and when the reader finally acts it acts on the state the message
described: one lane waited on an OK another lane had already given, and one waited on
a verdict its author had already withdrawn. `scripts/bus.py` is the shared record
between lanes: one cc-notes log per drive, one entry per post, each lane reading from
its own cursor. It is R5 applied to what lanes tell each other.
`reference/bus-contracts.md` holds the entry kinds and the delivery rule.

The root creates it once with `bus.py init --title "bus: <drive>"` and puts the id in
every brief beside the ledger id. The records live on `refs/cc-notes/*` beside the
ledger and survive compaction, rotation, and a session restart.

**B1. Post the state, message the pointer.** A decision another lane could build on, a
head after every push, a contract whenever a lane exposes something another lane
consumes, a blocker, and an ask each go on the bus with `bus.py post`, addressed with
`--to` when a named lane must act. The `SendMessage` that wakes that lane carries the
entry number and nothing else. *Prevents a lane acting on the body of a stale message
when the log already holds the newer entry.*

**B2. Read at every wake and before every decision or report.** A lane's first tool
call on any wake is `bus.py read --lane <name>` with its brief's subscription, and it
reads again before it decides, reports, or asks. A read that shows `[ANSWERED #n]` or
`[WITHDRAWN #n]` on an entry ends any wait on it. A message is never acted on before
the read. *Prevents the wait on an answer already given and the act on a verdict
already retracted.*

**B3. Watch while running.** A lane arms one Monitor on `bus.py watch --lane <name>`
with the same subscription, at the maximum timeout, and re-arms it when it expires. It
prints an entry only when one is delivered, so a running lane hears a blocker or an
answer within the interval instead of at its next wake. This is the one Monitor a
lane keeps; R3 still forbids one per build. *Prevents the idle lane that never saw its
CI red until the owner did.*

**B4. Withdraw, never overwrite.** A retracted verdict, a moved head, or a changed
interface is a `withdraw --re <entry>` from its poster, then a new entry. Every read of
the old entry shows the withdrawal, so a lane that already acted learns it and a lane
that has not yet acted never does. *Prevents two lanes carrying two versions of one
verdict.*

**B5. Read `state` before asking, and `summary` instead of the log.** `bus.py state`
is the live head and contract per lane and topic; a question it answers is never sent
to a lane. The root reads `bus.py summary` beside the desk's summary: counts, every open
ask and blocker with its age, the latest decisions, at most ten lines. An open ask
past its lane's cadence is the root's to dispatch under R6. Two contracts on one topic
from two lanes are a collision to rule on before either ships. *Prevents the root
relaying by hand what any lane could read, and the contradiction found after both
sides landed.*

The desk posts each `route` line as `blocker --topic <pr> --to <lane>` in the pass
that prints it, so a red reaches an idle lane at its next wake whatever became of the
message.

## Mechanics

### Lane brief

```
Authority: <what you do without asking; what stops for the owner>.
Verified facts, do not re-derive: <ids, shas, URLs, state already confirmed>.
Do:
  1. <step>
  2. <step>
Escalate early, do not improvise: scope surprise, an assumption the code refutes,
  an auth or approval gate, or two failed approaches. Return findings + 2-4 options.
AskUserQuestion is unavailable; on a decision, take the brief's default, log it with
  `ccn log append <drive log id>`, and report it.
Do NOT touch: <files, branches, worktrees another lane owns>.
Worktree: <absolute path, exclusive to this lane>.
Holds file: <path>, root-owned; rebuild a fresh numeric held file before every
  enqueue from its #<n> entries and held lanes' open PRs under D3.
Self-enqueue: take the largest green, approved, unheld bottom prefix and run
  `stack-enqueue --hold <held file> <prefix top>` at once, then `ledger.py report` the
  enqueue. Where the repo has no enqueue script, use
  `ledger.py label --repo <repo> --ledger <id> --pr <prefix top> --expect-head <sha> --checkout <worktree>`.
  Never self-enqueue above a held PR or any PR of a held lane. Report `held` on
  your tip, name the held PR, and leave release to the root.
  Keep `ccx vcs pr watch` on the stack; on ejection or conflict, rebase and
  re-enqueue. After the prefix lands, restack the PRs above it with
  `ccx vcs stack submit`. Never end a turn with a ready, unheld prefix unenqueued.
Register your branch prefix with landing-desk when spawned and whenever you open a PR.
For an owner ask, report each PR to landing-desk with its ask id for `report --ask <id>`.
Bus: <id>; script <plugin root>/skills/long-running/scripts/bus.py; --repo <drive checkout>.
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
Run subagents and codex in the foreground (blocking), or poll the reply file in a foreground loop to a terminal state; never background-and-end-turn.
Record each sub-dispatch with `ledger.py ask` before dispatch and `ledger.py answer` when its reply lands.
Verify through CI: never run a whole-package build or suite locally (buck2/cargo build,
  `yarn tsc:*`, `bun test`, jest, `go test ./...`). Run locally only the one failing test
  that reproduces a red CI step, or a single artifact this brief names, one at a time with
  `-j 8` or the tool's equivalent. With the 1-minute load above the core count, finish
  the current command and start no build.
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
  action. Do not end a turn waiting. Every push to a reported PR re-reports the new
  head to landing-desk in the same turn; the desk grades without waiting for it.
```

Spawn every lane as one of this plugin's two lane types, with the routing table's
`model`. The standing subagents, the desk's shards, sequencers, and pollers are
`long-running:lane`. An implementation lane that ships a PR or calls a skill such as submit-pr, open-pr, or
codex is `long-running:lane-ship`. Both leave out `ToolSearch`, the `mcp__*` tools, and
the deferred-tool list, and both carry the 1h prompt cache a nine-minute poll needs.
`lane` also leaves out the Skill tool and the skill listing, so it starts 16k tokens
lighter than `general-purpose`; `lane-ship` starts 5k lighter.

If a `lane` needs a skill, give that operation to a separately named `lane-ship`
with a scoped brief.
Keep the original lane running; the new lane must not duplicate its active work.

A lane that runs as an Orca worker is a separate session the Agent tool cannot message.
The orca-desk launches it through `scripts/orca-launch.sh` using
`reference/orca-workers.md`. `reference/orca-lane-brief.md` is its brief, ready to paste:
a shared contract and lane section concatenated into one file, with a ≤300-character
pointer as the `--spec`. A codex Orca lane launches on Orca's codex agent under O15,
never as a claude worker calling the codex skill; the codex skill is for inline lanes,
and `codex-ask` for one-off questions.
When a lane needs its own machine for tests or builds off the owner's Mac, or the
running platform, it creates a remote Orca workspace through the repository's Orca
skill if it ships one (Forge-AI/monorepo: `.agents/skills/orca`, "Remote workspaces").

One worktree per lane, always. Two agents in one checkout race HEAD, the index, and
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
`semantic-collisions` lane, a `long-running:lane` on fable, holds the bird's-eye view
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

```sh
deadline=$(( SECONDS + 540 ))
while (( SECONDS < deadline )); do
  state=$(<authoritative query>)
  case "$state" in
    passed|merged|CREATE_COMPLETE|UPDATE_COMPLETE) echo "TERMINAL ok $state"; exit 0 ;;
    failed|broken|canceled|timed_out|ROLLBACK_*|*_FAILED) echo "TERMINAL bad $state"; exit 1 ;;
  esac
  sleep 30
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

Before each enqueue, re-read the root's holds file and
build a fresh numeric held file from its `#<n>` entries and every open ledger row
of a `lane:<name>` entry under D3. Never reuse a held file from an earlier call.
Set `HOLDS_FILE` to the root's file and `OUTPUT_DIR` to the pass's output directory.

A resumed root in a new session runs `drive.py start --drive <id> --ledger <id>`
to join the existing drive. Run `drive.py end` only when the drive is over.

```sh
LEDGER=$(ledger.py init --title "desk: $DRIVE")
drive.py start --ledger "$LEDGER" [--orca-run <run>]
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
for TIP in "<tip-a>" "<tip-b>"; do
  (
    HELD_FILE=$(mktemp)
    grep -o '#[0-9]\+' "$HOLDS_FILE" | tr -d '#' > "$HELD_FILE"
    ledger.py show --ledger "$LEDGER" --json |
      jq -r --rawfile holds "$HOLDS_FILE" '
        ($holds | [scan("lane:([^[:space:]]+)")[0]]) as $lanes
        | .rows[]
        | select(.key | test("^[0-9]+$"))
        | select((.fields.state // "open") == "open")
        | select(.fields.lane as $lane | $lanes | index($lane))
        | .key
      ' >> "$HELD_FILE"
    "$CHECKOUT/.agents/skills/submit-pr/scripts/stack-enqueue" --hold "$HELD_FILE" "$TIP"
  ) > "$OUTPUT_DIR/$TIP" 2>&1 &
done
wait
cat "$OUTPUT_DIR/<tip-a>" "$OUTPUT_DIR/<tip-b>"
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

# fallback sweep over one shard's lanes
ledger.py label --repo "$REPO" --ledger "$LEDGER" --all-clean --shard lane-a,lane-b --checkout "$CHECKOUT"

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
the fallback sweep there because it enqueues stacks one at a time.
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
the rest with `ccx vcs stack submit`; Orca routes go to `inbox/orca-desk.md`.
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

Once `long-running` is invoked — the Skill call itself, or a `/long-running` prompt —
this plugin's capt-hook pack runs the session's compaction handoff for the rest of the
session, compactions included. Nothing inside the session clears it early. The hook
never blocks a turn. It does the mechanical steps itself and sends the root one nudge.

**Threshold.** The hook reads the live model at every check, from the transcript's
newest non-synthetic assistant turn, never from the model the session started on. The
window is `CLAUDE_CODE_AUTO_COMPACT_WINDOW` env, else the `autoCompactWindow` setting,
else the model default, and never more than the model's own window. That is 1M for a
`[1m]` suffix and for the native-1M models: sonnet-5, opus-5 and 5-5, fable-5 and 5-1,
and mythos-5 and 5-1. Every other model is 200k, including haiku-4-5, sonnet-4-x,
opus-4-0 through 4-6, and every claude-3 model.

`threshold = window − 33k`, and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` can only lower it
further. The check runs after each main-session tool call, never a subagent's, and fires
once used tokens cross 80% of that threshold.

**Nudge.** At 80% of the threshold, the hook sends the root one nudge, as context
on its next tool call or prompt. It gives used tokens against the threshold and asks
for the drive's whole execution state in a new progress doc when convenient. The
slug comes from `progress:<slug>` in the plan's existing progress pointer line, or
from the plan's file stem if that line has no slug. The nudge is not repeated, and
the turn is never held.

**Write the progress doc.** Create a new cc-notes doc for each handoff:

```sh
ccn doc add "<drive>: progress <UTC>" --label progress:<slug> --when "Resuming or compacting the <drive> drive: read before anything else, after the plan" --body -
```

Use the nudge's UTC timestamp (`YYYY-MM-DDTHHMMZ`) and pass the body on stdin.
The progress doc is living guidance with a `when` trigger and supersede edges.
Never rewrite the plan; it remains the stable mandate and decisions document.
Carry the whole execution state into the new doc, with every owner ask and
obligation accounted for under these sections:

```md
# <drive>: progress <UTC>

## How the drive runs
<root role, lane contracts, desks, ledger and rulings-log ids>

## Owner asks and state
<every ask, its current state, evidence, and next gate>

## Lanes and binding rulings
<current agents, dispatches, briefs, ownership, and rulings still in force>

## Landed
<completed work and evidence>

## Waiting on the owner
<unresolved decisions and the options already presented>

## Root's next actions
<ordered actions, dependencies, and owed follow-ups>
```

Only when the repo lacks cc-notes or the `ccn` binary is unavailable, write the same
record as a new file beside the plan at `<plan-stem>-progress/<UTC>.md`. Otherwise,
use a progress doc; a note, a log, or a loose file does not replace it.

**Supersede and point.** At the next main-session `Stop` after a doc appears under
the label that was not active at the nudge, the hook selects the newest such doc and
runs `ccn doc supersede OLD --by NEW` for every other active doc under that label.
Exactly one doc stays active; history is the supersede chain. A failed supersede
leaves the handoff pending, and the next `Stop` retries it; each `ccn` call is capped
at 20 seconds.

The hook adds one pointer line to the plan the first time. It starts with
`- **Progress (read first after any compaction):**` and names the label and current
doc id. Later handoffs change only that line's id. The handoff never restructures
the plan. Let the hook do the superseding and pointer edit.

For the file fallback, the hook waits for a file that did not exist at the nudge and points the
same line at the newest file in the folder; later handoffs change only its filename.
In the release-v3 example, the plan's last line names
`progress:release-v3` and doc `d473abdd`.

**`/compact`.** Once the progress record and pointer are ready, the hook starts a
detached background job at that main-session `Stop` and lets the stop through. The
job waits through orca for the terminal to go idle, reads the screen, and types
`/compact` only when the draft is empty and the input line holds no typed text. It
rechecks every 30 seconds and gives up silently after 30 minutes. With
`ORCA_TERMINAL_HANDLE` unset, the hook sends the owner one message to run `/compact`
by hand and blocks nothing.

**Resume.** After compaction, `SessionStart` says to read the plan before anything
else, then `ccn doc list --label progress:<slug>` and `ccn doc show <id>`. With the
file fallback, read the newest file in the progress folder after the plan. The plan
and progress record supersede the summary. `PreCompact` carries the same read order
and keeps only in-flight details from the last turn that those records lack.
Claude Code's own auto-compaction can fire before the progress record is written;
`SessionStart` says so and asks the root to write it when convenient. The skill stays
active; reload `long-running` if its rules are no longer in context.

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
never blocks it.

**Liveness.** A lane is live only while it appears as a running teammate or subagent in
the `Stop` payload's `background_tasks`. A subagent matches by id. Claude Code labels
an in-process teammate by its prompt's first 50 characters plus `...`, not by the
`description` in its meta, so a teammate matches on either one. Many lanes share one
label, so a label match only counts a running task and never names one.

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
already asked is asked again every 30 minutes until it replies `flushed <ids>`, with
no cap. Re-asks do not count toward the limit for new lanes.

A lane with no teammate inbox is asked through the root. The hook queues a
``ROOT-ACTION `<lane>`: SendMessage it now ...`` line carrying the same `ROTATE` text
into the root's context on its next tool call or prompt. The root sends the request
with `SendMessage` to the lane's name in that turn.

**Handoff.** A `flushed <ids>` reply confirms the lane recorded its state and keeps
working; it ends the rotation cycle. The hook nudges the root once: the lane keeps
running in place, nothing to do. After its own compaction, the lane reads its ledger
and saved cursor and continues. Never `TaskStop` or respawn a flushed lane, and
never spawn a second agent under a live lane's name. An `open-pr:pr-watcher` resumes
from its state file after its own compaction.

A lane that drops below its line through its own compaction or leaves
`background_tasks` because it stopped or rotated also ends the cycle. A compacted
lane starts a fresh cycle if it crosses the line again.

If a lane has not replied `flushed <ids>` within 10 minutes of its first ask and is
still live, awake, and over its line, every later main-session `Stop` queues a
``ROOT-ACTION `<lane>`: rotate it by hand now.`` line. It names the lane's tokens
against its line, transcript size, running time, ask count, and first-ask time,
with two steps:

1. Spawn `<lane>-N+1` from the old lane's brief plus its handoff of ledger rows,
   cc-notes, and cursor. `alerts-watch` becomes `alerts-watch-2`; `desk-3` becomes
   `desk-4`.
2. Once the successor reports, `TaskStop` the old lane by the id named in the line:
   `<name>@<team>` for a teammate, which `TaskStop` resolves by name, or the agent id
   for a subagent. Never pass a `t…` task id from the label match; it can belong to
   any lane sharing that label.

Each firing replaces that lane's previous queued `ROOT-ACTION` line instead of
adding another. The root acts on it in the turn it arrives. This is the one case
where the root stops a lane, and it stops it only after the successor has reported.
A `flushed <ids>` reply cancels the rotation.

The session's hook state directory holds `rotation_state.json` with a `timeline`
list. It records one entry per `ask`, `flushed`, `compacted`, or `gone` event. An
`ask` entry holds `via` as `inbox` or `root`, `tokens`, and `line`. A `flushed` entry
holds `ids`, and a `compacted` entry holds `tokens`. An `escalate` entry holds its
first `at`, latest `last`, `count`, and `tokens`; consecutive escalations for the
same lane update that entry.

*Prevents stopping and respawning lanes from ending live sessions during the
release-v3 drive (2026-09-30).*

*Prevents alerts-watch sitting over its line from 07:02Z until the owner ordered its
rotation by hand (release-v3, 2026-10-01).*

*Prevents a `ROOT-ACTION` for ccx-guard-eperm naming four different task ids
across four firings, three of which stopped other lanes sharing its label
(release-v3, 2026-10-01).*

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
12. Am I about to create, relaunch, or answer an Orca lane's routine traffic myself? → the orca-desk does it; append root rulings to its inbox file, never `SendMessage` its running loop.
13. Before waiting on a desk relay for a priority PR or an owed item, read the PRs in one batched `ccx vcs pr status` call and dispatch every owed item with no PR now.
14. Did a tool just refuse, fall back to `# ccx:raw`, or need a step done by hand, or am I running the same command a third time? → spawn its tooling lane this turn (fix, PR, merge, release, install) and keep going.
15. Is a production alert active? This turn, (a) fix lane with apply authority, (b) evidence lane feeding it by name, (c) target fence, both launches and fence in one inbox line using `orca-desk: launch <name> NOW`, (d) one-line owner reports at spawn, mechanism, and fix-live; no verdict gate. Check each lane every 10 minutes; at 15 without a mechanism, add a different-model lane (Opus 5.5 after sol) and keep the first running.
16. Did I just spawn a lane, take an owner ask, or consume a deliverable? → `TaskCreate`/`TaskUpdate` this turn; a lane's word alone completes nothing.
17. Am I about to ask the owner anything (AskUserQuestion, a board, a lane's question list)? → check each question against the plan's decisions, `ccn answer list --label scope:durable`, and memory first; apply what is settled and ask only the rest.

Apply D3 to priority PRs before delegating. A call that survives all seventeen decides
something no lane can decide for you; everything else is a lane.
