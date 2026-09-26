---
name: long-running
description: Hard rules for orchestrating multi-lane work without burning the orchestrator's context - routine ground truth arrives as a lane's verdict, anything with a body is a lane, every wait folds into the lane that acts, no lane parks and no lane is re-briefed, state lives in cc-notes and the task list, an open-PR ledger grades, routes, and holds every open PR and records every owner ask, and a landing-desk lane with its own desk tool is the message queue and merge coordinator between the lanes and the root. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs at once, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
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

This skill is the context-discipline layer over `~/.claude/CLAUDE.md`. Fan-out shape comes
from §Parallelize Independent Work, lane behavior from §Delegation, per-lane model and
effort from §Model Routing, and depth of checking from §Verification Budget. None of
that is repeated here.

## The eight hard rules

**R1. Take ground truth from the owning lane.** Once a lane can answer a question, never grep a log, list cloud resources, curl an API, open a build page, or parse JSON in the root context. Ask the owning lane with a scoped resume and take back at most five lines. The root checks priority PRs itself under D3.

*Prevents reading CloudWatch and Buildkite output while an assigned triage lane already held the answer.*

"Verify ground truth yourself, never trust silence" and this rule do not conflict.
Outside D3, verifying means asking a lane for the one number, and checking liveness
means asking for a 3-line status. Neither means reading the source. Silence is not progress.
Ask, and set a deadline for the answer.

**R2. Delegate anything with a body.** Any read past one file, any log, any multi-step investigation, any PR or merge mechanics, any bulk enumeration belongs to a lane. The root holds decisions and the task list, and checks and labels priority PRs under D3.

*Prevents repeated small reads and merge calls filling the root's window.*

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
2-3 duplicate idle-notifications per real report. A rotation respawn is not a re-brief:
the old lane is flushed and stopped first, and the fresh one resumes from the ledger, so
nothing runs twice (see Lane rotation). *Prevents lane collisions in a shared worktree
and the reply tax on notifications that carry no news.*

**R5. Write it down, do not hold it.** Findings go to cc-notes from the lane that found
them, never into the orchestrator's window. Lanes carry no `mcp__*` tools, so they
write with `ccn log append`, `ccn investigation open` and `append`, `ccn note add`, and
`ccn task add`. The root may use the `mcp__plugin_cc-notes_*` tools.
`TaskCreate`/`TaskUpdate` is the root's only state. Report to the user on milestones or
when they must act, never per event. Once `long-running` is invoked, the session's
compaction handoff runs on its own, as Compaction handoff describes. *Prevents the
forced mid-drive handoff with nothing written down to hand over.*

Every lane receives the whole task list on every wake. The root deletes a completed task
with `TaskUpdate` status `deleted` once its result is in cc-notes or the plan.

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
the stack's worktree, or check for the squash commit `(#N)` on a freshly fetched base
branch. This applies to the root and the desk.

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

## The landing desk and its ledger

On a drive where many lanes open PRs, the root is the wrong place for their reports.
Each report is a message in the root's window, and each landing is a wait. The desk is
one long-lived lane, `landing-desk`, that takes those reports and owns routine labels;
the root checks and labels priority PRs under D3. Lanes report to the desk, it grades
and labels, and the root hears from it every 30 minutes.

`scripts/ledger.py` is its one tool, over `gh api` REST only. Its one store is a cc-notes
ledger with a row per PR our lanes shipped. The holds, the routing, the label history,
and the landing are fields on that row. Lane messages are `msg/<seq>` rows and owner
asks are `ask/<seq>` rows beside the PR rows.

The desk grades, lands, and tracks only through `ledger.py`, never scripts of its own;
a gap in `ledger.py` is a `RULING NEEDED`, not a workaround.
`reference/landing-desk-brief.md` is the desk's brief, ready to paste;
`reference/desk-contracts.md` holds the message shapes. It is R5 applied to PR state and
R3 applied to the watching.

Spawn it first, before any lane that will open a PR, whenever three or more lanes
will ship through one merge queue or the drive will outlive one context window. Below
that the lane that opened the PR lands it, or the root lists it for the label watch
under Mechanics, and there is no desk.

**D1. Address reports to the desk and act on priority PRs.** A lane's last action on a PR is the three-line report of PR, full head sha, and verdict, sent to `landing-desk`. The root receives only `RULING NEEDED` lines and the 30-minute summary. Never relay a lane's ETA for a green PR. If the owner flags a PR as priority, or it blocks a release or a user, the root checks its gates and adds the merge label itself in the same turn under D3.

*Prevents the root relaying a lane's 45-60 minute ETA for green priority PR #25145 while the owner queued it by hand.*

**D2. Track and grade our lanes' PRs.** `ledger.py report`, `ledger.py register` with a lane's branch prefix, and an explicit `refresh --pr` are the only paths that open a PR row. Owner ask rows open only through `ledger.py ask`. A PR on a registered prefix or reported by a lane is tracked and graded without waiting for a lane report at its current head. The desk never lists the repository's pull requests; a PR it cannot trace to one of our lanes stays outside the ledger and its counts.

*Prevents routing comments and rebase orders landing on other engineers' PRs, which one repo-wide sweep did twenty times in an hour.*

**D3. Label the stack's tip as soon as every PR passes.** `ledger.py label --pr <tip> --expect-head <tip-sha> --checkout <path>` walks base refs to the repo's default branch and re-reads every PR. Each must be open, with approval in force, successful commit status, no failed checks, and a completed, successful latest `ai-review`. Approval on any commit counts; a dismissed or withdrawn approval does not. Each head must have no desk hold or lane `held` verdict on that head, no prior label or pull, and no conflict with its base. Allowed `mergeable_state` values are `clean`, `behind`, and `has_hooks`. An untracked downstack PR, an orphaned base, or an open child outside the stack refuses the whole attempt. `--expect-head` pins the tip you graded; the batch re-reads each tip immediately before grading it. A report is not required; the forge decides whether a head is red or conflicting. When all pass, one `merge` label on the tip enqueues the whole stack, and every row records `label_head`, `labelled_at`, `approved_by`, and `label_stack`. For a priority PR under D1, the root checks the gates itself with `ccx vcs status --refresh` or REST reads of approval, CI, and mergeability, then adds the merge label in the same turn. The desk records that label on its next refresh as `in the queue, labelled outside the desk`; it does not treat it as a bypass.

*Prevents a green tip enqueueing a red parent, an ejected head entering the queue again unchanged, and a green priority PR waiting for the owner to queue it by hand.*

**D4. Landed means the squash is on the base branch.** `ledger.py landed` fetches the
base branch and settles a closed row by `git log` for a subject ending `(#n)`, never by
the PR's `merged` field, which a squash-merging queue leaves false on every PR it lands.
A closed row with no squash becomes `closed-without-squash`, a name that cannot be read
as success, because a child auto-closed by its base's deletion looks exactly like a
landing until the log is read. *Prevents lanes waiting hours on a PR that landed
minutes after they started, and a lost stacked child counted as merged.*

**D5. Route every red or conflicting row in the pass that finds it, once per head.**
`refresh` then `route`, never `refresh` now and `route` when there is time. `route`
records the head, job, and lane it sent, so the same head and job are never routed twice
and a moved head is routed again. Nothing is written to the pull request: a lane is
addressed where it listens, and a comment on a PR reaches whoever happens to read it.
Route to a lane only while it is live. A finished lane's name resumes its whole brief
under R4 and collides with the lane now holding the worktree, so send a red on its PR
to the root to dispatch a fresh fix lane.
*Prevents the red PR that sat for hours because the pass that found it only recorded
it, and the routing comment on someone else's PR. Also prevents a red routed to a
finished infra lane on #25188, which pushed from the worktree a fresh rebase lane held.*

**D6. Every hold has a reason and an expiry, and every message is recorded once.**
`ledger.py hold` takes both; a row parked without them is an untracked row wearing a
ledger's clothes, and the summary does not count it as held. `enqueue` drops a second
message with the same kind, PR, and head, so a duplicate idle notice is neither stored
twice nor answered. *Prevents the hold nobody can lift and the reply tax on notifications
carrying no news.*

**D7. A stale plan is information, never a refusal.** A pull request's plan is computed
at its head. When the base moves under a stack that plan reached, the desk prints the
stacks and the movers in the grade and labels anyway. The landing plans the tree it
actually applies and refuses its own op classes there, so the gate that matters sits
where the tree is real. Any class the desk would refuse on a plan belongs in the
landing's admission rule, not in a pre-merge staleness check. A rebase is owed for a
merge conflict and for nothing else. *Prevents the bounce where a green PR is refused
because an unrelated stack moved and then spends half an hour in a rebase and a CI
re-run that change nothing about what the landing does.*

**D8. Label on the report, and label every clean stack in one batch.** When a lane reports `clean`, run `ledger.py label` for its stack's tip in the same turn the report goes into the ledger. Every pass also runs `ledger.py label --all-clean` over every tracked open row whose current head has never carried the label and is not held. The batch groups candidates into stacks, re-reads each tip immediately before grading it, and labels each passing tip. A report is not required, and a lane's `red` or `conflicting` verdict does not refuse a head the forge passes. One refused stack does not stop the rest; its refusal is recorded on each of its rows and routed under D12.

*Prevents clean PRs waiting for a 20-minute pass that labels one report at a time, until the owner enqueues one in Graphite by hand.*

**D9. Name every clean row older than 30 minutes with its blocker.** `ledger.py stale`
lists every open row whose latest report is `clean` and at least 30 minutes old, with
one blocker each. The blocker is held, in the queue, routed, label refused, head moved
since the report, or never graded. The summary carries the same lines and the median
minutes from a row's last report to its landing over the window.

Clear each stale row's blocker in the pass that sees it with a label, route, hold,
lift, or `RULING NEEDED`. *Prevents a clean row aging silently while the counts line
reads healthy.*

**D10. Shard the desk above 25 active rows.** Spawn parallel sub-lanes named
`landing-desk-<shard>`, each owning a named set of lanes' rows in the same ledger with
`--shard lane-a,lane-b`. A stack's rows belong to the shard of its tip's lane. Each
shard runs `refresh`, `landed`, `route`, `label --all-clean`, and `stale` on its own
rows every five minutes. The refresh lock is keyed on the ledger, so shards never
race a sync.

Lanes keep reporting to `landing-desk`; the main desk types every message in, labels
on each clean report, and alone sends the root the summary. *Prevents one desk's pass
growing with the board until a five-minute cadence is a 20-minute one again.*

**D11. Register each lane's stack when it starts and whenever it opens a PR.** The lane sends the desk its branch prefix and PR numbers to record with `ledger.py register --ledger <id> --lane <name> --branch-prefix <prefix> [--pr N]...`. The prefix must be unique to the lane and end in `/`. Each refresh discovers its open PRs through `GET repos/<repo>/git/matching-refs/heads/<prefix>`, then one scoped `pulls?head=<owner>:<branch>&state=open` lookup per branch. Every discovered PR enters the same batch and takes the same gates as a reported PR; the desk never lists the repository's pull requests.

*Prevents three PRs a lane never reported sitting unmerged for hours.*

**D12. Grade a moved or unreported head like any other.** Every tracked current head takes D3's gates without a report. For each refused head, send `new head <sha9>: <blocker>` to its lane once per head and blocker. A head that moved since the refresh is graded on the next pass, without a route; red CI and conflicts go through `route` and get no duplicate message from the batch.

*Prevents two heads that moved after their reports being ignored for hours.*

**D13. Name what the desk is waiting on and ping the lane in the same pass.** The summary's `waiting:` line groups tracked open PRs as `ungraded`, `refused`, `red`, and `held`. An `ungraded` row lacks a label and a grade at its current head; a `refused` row has a label refusal at that head, with the reason in `stale` or `show`. A `red` row has a CI failure or a `dirty` or `blocked` mergeable state; `held` covers desk holds and lane `held` verdicts on the current head. In the same pass, run `route` and `label --all-clean`, and send each lane the messages they print. `summary` requires `--repo` and `--checkout` and settles landings first, so a landed row never appears as pending.

*Prevents the desk waiting silently while five ready PRs sat unmerged for hours.*

**D14. A stack lands whole.** When the ledger holds several PRs in one stack, never label a lower PR as its tip while any PR above it is open. A red, conflicting, or held PR anywhere holds the whole stack. Route the blocker under D5 and label the tip once every PR passes on its final head. A stack whose root is ruled out by retargeting to the base branch or closing is still one stack. Retarget the next PR to the base branch and label the remaining tip, never each survivor alone.

*Prevents landing the root alone in six stacked PRs, #25188 through #25199 in Forge-AI/monorepo on 2026-09-25, which would have rebased five children onto a moving base and re-run CI on each for nothing; the owner ruled "merge the whole stack at once but first fix the failing CI on it".*

`refresh` regrades the rows the ledger holds and merges the forge's fields into them, so
the fields the desk writes are never overwritten: `lane`, `declared_intent`, the holds,
the routing, the label history, and the landing. A refresh that cannot reach the forge
exits non-zero and writes nothing. The records live on `refs/cc-notes/*` and survive
compaction, a session restart, and a handoff. A fresh `landing-desk` lane spawned with the
ledger id reads the inbox, the holds, the routes, the label history, and the landings
exactly as the last one left them. None of that goes into session memory or the plan file.
That is what makes the desk cheap to rotate; see Lane rotation.

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
Do NOT touch: <files, branches, worktrees another lane owns>.
Worktree: <absolute path, exclusive to this lane>.
Register your branch prefix with landing-desk when spawned and whenever you open a PR.
For an owner ask, report each PR to landing-desk with its ask id for `report --ask <id>`.
Run subagents and codex in the foreground (blocking), or poll the reply file in a foreground loop to a terminal state; never background-and-end-turn.
Record each sub-dispatch with `ledger.py ask` before dispatch and `ledger.py answer` when its reply lands.
Finish: drive to a terminal state, then SendMessage <orchestrator> exactly one report,
  ≤10 lines: verdict | ids | what changed | what is next. That message is your last
  action. Do not end a turn waiting. Every push to a reported PR re-reports the new
  head to landing-desk in the same turn; the desk grades without waiting for it.
```

Spawn every lane as one of this plugin's two lane types, with the routing table's
`model`. The landing desk, its shards, sequencers, and pollers are `long-running:lane`.
An implementation lane that ships a PR or calls a skill such as submit-pr, open-pr, or
codex is `long-running:lane-ship`. Both leave out `ToolSearch`, the `mcp__*` tools, and
the deferred-tool list, and both carry the 1h prompt cache a nine-minute poll needs.
`lane` also leaves out the Skill tool and the skill listing, so it starts 16k tokens
lighter than `general-purpose`; `lane-ship` starts 5k lighter. A `lane` that turns out
to need a skill is rotated or respawned as `lane-ship`, never worked around.

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

### Desk cadence

The desk owns the PR loop below; the root records asks and verifies their acceptance
checks. The ledger is created once, when the desk is spawned, and its id goes into
the brief; everything after that is `ledger.py`.

```sh
LEDGER=$(ledger.py init --title "desk: $DRIVE")
ledger.py ask     --ledger "$LEDGER" --text "<verbatim>" --lane lightning-eh --accept "<acceptance check>"

# on each report: record it and grade the current head; reports are not a gate
ledger.py report  --ledger "$LEDGER" --pr 21221 --head <sha> --lane lightning-eh --verdict clean --ask ask/000001
ledger.py label   --repo "$REPO" --ledger "$LEDGER" --pr <tip> --expect-head <tip-sha> --checkout "$CHECKOUT"
ledger.py ruling  --ledger "$LEDGER" --lane p2-edge-rows --pr 20284 --text "land without the document form" --options "A land|B hold|C close"
ledger.py enqueue --ledger "$LEDGER" --kind idle --pr 21221 --head <sha> --lane lightning-eh --text "done"
ledger.py inbox   --ledger "$LEDGER" --take
ledger.py register --ledger "$LEDGER" --lane lightning-eh --branch-prefix lightning/ --pr 21221

# every 5 minutes: refresh, then grade every tracked current head, reported or not
ledger.py refresh --repo "$REPO" --ledger "$LEDGER"
ledger.py landed  --repo "$REPO" --ledger "$LEDGER" --checkout "$CHECKOUT"
ledger.py route   --repo "$REPO" --ledger "$LEDGER"
ledger.py label   --repo "$REPO" --ledger "$LEDGER" --all-clean --checkout "$CHECKOUT"
ledger.py stale   --ledger "$LEDGER"

# for a stack the batch did not reach: pin the graded tip; route or hold each blocker
# a stack lands whole: label its tip only, once every PR passes; never a lower PR alone
ledger.py label --repo "$REPO" --ledger "$LEDGER" --pr <tip> --expect-head <tip-sha> --checkout "$CHECKOUT"
ledger.py route --repo "$REPO" --ledger "$LEDGER" --pr 21221 --job "plan comment missing for this head"
ledger.py hold  --ledger "$LEDGER" --pr 20284 --reason "waits on #20314" --hours 4

# every 30 minutes, and the only desk output the root reads
ledger.py summary --repo "$REPO" --ledger "$LEDGER" --checkout "$CHECKOUT"
ledger.py show    --ledger "$LEDGER" --asks
ledger.py live    --ledger "$LEDGER" --at "$(date -u +%FT%TZ)" --text "release 37 deployed"
ledger.py drop    --ledger "$LEDGER" --ask ask/000002 --reason "owner withdrew it"
ledger.py answer  --ledger "$LEDGER" --ask ask/000003 --text "<the reply>"

# each shard runs the batch for its lanes
ledger.py label --repo "$REPO" --ledger "$LEDGER" --all-clean --shard lane-a,lane-b --checkout "$CHECKOUT"
```

`label --dry-run` runs every guard, prints the stack it would enqueue, and writes
nothing; run it once on a repo before the first live label. `label --all-clean` supports
`--dry-run` and prints each stack to enqueue. It considers every tracked open row whose
current head has never carried the label and is not held, and re-reads each tip before
grading it. `--expect-head` pins the graded tip for a single-stack call.

Reports open rows, carry the lane's text, and feed `stale` and p50 report-to-landing
minutes; the desk grades without waiting for them.

`stale --minutes N` sets the report-age threshold, which defaults to 30 minutes. `route` without `--pr`
sweeps every red or conflicting row, reads the first failing line from the Buildkite
log, prints the message to send each lane, and records it; `route --dry-run` prints
and records nothing. `unlabel --reason` records why a label came off and blocks a
re-label of that head; it does not stop a queue that already took the PR. A lane
asking what it owns gets `ledger.py show --red`, never the raw table.

### Label watch

`scripts/label-watch.sh` labels the PRs the root holds outside a ledger, which are
priority PRs under D1 and every PR on a drive too small for a desk. The desk keeps to `ledger.py`.
Each PR passes this gate before it gets the label:

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

The label goes on through REST `POST issues/<n>/labels`; `gh pr edit` is GraphQL.

```sh
export LABEL_WATCH_APPROVERS='forge-pr-reviewer[bot],poetic-svc' LABEL_WATCH_CHECKOUT=~/Code/monorepo
label-watch.sh once 25742
printf "%s\n" 25763 25780 >> "$LIST"
label-watch.sh watch "$LIST"
```

`LABEL_WATCH_REPO`, `LABEL_WATCH_TRUNK`, `LABEL_WATCH_LABEL`, and `LABEL_WATCH_INTERVAL`
default to the checkout's origin, its `origin/HEAD`, `merge`, and 240 seconds. Run
`watch` in the background. Add a PR by appending its number to the list file. Each sweep
reads Graphite once for the whole list and GitHub only for PRs that are not queued. The
checks and reviews reads run only after the earlier gates pass. `watch` deletes `LABELLED` and
`SKIP` entries, keeps `CONFLICT`, `HELD`, `NOT-READY`, and `API-FAIL` ones, prints a line
only when a PR's result changes, and exits when the list is empty.

The trunk is fetched into `refs/label-watch/<trunk>`, never `refs/remotes/origin/<trunk>`,
so a shared clone's other fetches cannot hold its ref lock. A fetch that still fails after
three tries prints `API-FAIL trunk-fetch` for every PR that sweep and labels nothing.

The queued PRs are the ones `ccx vcs pr status` reads `queued` on the list, and in
`watch` every PR the watch saw queued or labelled, until it closes. A PR labelled earlier
in a sweep is a conflict base for the rest of that sweep. The downstack walks base
branches through `pulls?head=`, and only for a stacked head while a queued PR exists.
*Prevents #25907 being evicted for conflicting with #25890, which the queue already held
ahead of it.*

A `CONFLICT` or `red` line goes to the lane that owns the PR, to rebase or fix. Never
answer it with a label, and never re-queue an evicted PR before its lane pushes a fixed head. The PR stays on the list, and the watch labels the rebased head once it passes.

A PR sent back for rework gets the `hold` label and leaves the list in the same turn.
Taking the label off does not dequeue it, and neither does converting it to a draft.
*Prevents a reworked PR landing anyway, as one did after Graphite had enqueued it,
through a removed label and a conversion to draft.*

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

**Archive and nudge.** At the threshold the hook archives the plan itself, as
`<stem>.<YYYY-MM-DD>-<HHMMSS>-pre-compact.md` in UTC beside it. Never archive by hand —
the guard that lets a plan rewrite through checks for exactly this sibling — and never
`cat >` the plan to dodge that guard; write over it and let the archive carry the loss.

It then sends the root one nudge, as context on its next tool call or prompt. The nudge
gives used tokens against the threshold, asks for the plan to be rewritten as the
current restart state when convenient, and names the archive path. It is not repeated,
and the turn is never held.

**Rewrite the plan.** First record any durable state still living only in this
conversation in the ledger, the rulings log, or a cc-notes note; the archive is history,
not a store. Then rewrite the plan with one `Write`, dropping finished work, superseded
state, and anything the archives already hold. The hook enforces no shape, but a plan
that restarts cleanly usually carries these sections:

```md
# <title> (compacted <date>Z)

## Restart here (read first)
<role, the lane-contract path, the landing-desk agent + its ledger id, the rulings-log id>

## Mandate (owner, verbatim)
## Standing constraints
## End state
## Owner decisions (never re-ask)
## State at <date>Z
## Live lanes
## Owed follow-ups
## Owner actions pending
## Key notes

## Done means
```

`Restart here` front-loads what a cold restart needs before anything else: the role
this session is playing, where the lane contract lives, which agent is the landing desk
and its ledger id, and the rulings-log id. It carries no recap of finished work. A
mid-drive gotcha that matters to a fresh restart belongs there too, not buried in
`Key notes`.

**Links and `/compact`.** When the root next writes or edits the plan, the hook appends
`## Archived plans (history only, never needed to restart)`, one link per
`<stem>.*-pre-compact.md` sibling, newest first, unless the plan already has it. At the
next main-session `Stop` after that rewrite, the hook starts a detached background job
and lets the stop through. The job waits through orca for the terminal to go idle, reads
the screen, and types `/compact` only when the draft is empty and the input line holds no
typed text. It rechecks every 30 seconds and gives up silently after 30 minutes. With no
orca terminal handle, the hook sends the owner one message to run `/compact` by hand,
and blocks nothing.

After compaction, `SessionStart` points the fresh context at the plan, which supersedes
the summary; read it first. When the plan still awaits its rewrite, `SessionStart` says
so. Claude Code's own auto-compaction can fire before the plan is rewritten.
`PreCompact` and `SessionStart` re-ground on the plan either way, so nothing is lost,
only unplanned.

### Lane rotation

Every turn a lane takes re-reads its whole history. A desk at 400k tokens pays about
that many tokens again per wake to type in a three-line report, while everything it
needs to continue already sits on its ledger. A long-lived lane is therefore rotated,
not kept: flushed, stopped, and respawned fresh under the same name.

**Threshold.** A lane's context is its last assistant turn's input plus cache tokens.
A lane is due at 0.7 of its own compaction threshold, computed from its live model the
way Compaction handoff describes. On a 600k window, that lands around 400k tokens.
`LONG_RUNNING_LANE_ROTATE_TOKENS` sets the line outright. Once `long-running` is
invoked, the same capt-hook pack checks the lanes on every main-session `Stop`, and
never blocks it.

**Liveness.** A lane is live only while it appears as a running teammate or subagent in
the `Stop` payload's `background_tasks`, matched by the `description` in its meta.
Membership in a team config never counts, so a dead or stopped lane is never asked. A
lane whose newest turn is more than an hour behind the root's is dormant: its cache is
cold, it costs nothing until it wakes, and the hook skips it.

**Delivery.** The hook asks the lane itself, appending the request to the lane's
teammate inbox, `~/.claude/teams/<team>/inboxes/<name>.json`, in Claude Code's own
message format and under its lock:

`ROTATE: record anything not yet in the ledger or cc-notes, reply "flushed <ledger id>" to team-lead, then stop.`

It asks at most three lanes per 15 minutes, highest token count first, and each lane at
most twice, at least 30 minutes apart. A lane with no teammate inbox draws one root
nudge instead, naming at most three lanes, to `SendMessage` them the same request.

**Handoff.** When a lane's `flushed <ids>` reply reaches the root, the hook nudges once:
stop that lane and respawn it from its handoff note at a natural pause.

1. `TaskStop` the flushed lane first. A spawn under a name a running lane still holds
   gets a different name.
2. Then spawn a fresh lane of the same type with the `Agent` tool under the same name,
   with its original spawn brief plus the ledger id; a lane that now needs a skill comes
   back as `long-running:lane-ship`. Messages addressed by name reach the newest agent.
   The next plan rewrite records the new agent in `## Restart here`.

The root stops only a lane that has replied `flushed`, one lane per reply, never a batch
of lanes at once. Never `SendMessage` the stopped lane. That resumes the same transcript
and reloads the whole history the rotation dropped. An `open-pr:pr-watcher` needs no
flush, since its state file is its ledger. `TaskStop` it and spawn a fresh one with the
same inputs, which resumes from that file.

## Anti-patterns seen

- Reading build and cloud logs in the root window while an assigned lane owned the question.
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
- Re-deriving the archive name or hand-typing the compaction prompt instead of letting
  the compaction-handoff hook do both.
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
- A lane backgrounded a codex subagent and ended its turn; completion went to the
  root session and the lane never woke. The "Use ccx for" ask for `AGENTS.md` died
  at `00:48Z`.

## Checklist before every tool call

1. Could a lane return this as ≤10 lines? → delegate.
2. Is this raw data - log, JSON, resource list, webpage, diff? → delegate.
3. Does a lane already own this question? → scoped resume, do not look.
4. Am I about to wait? → fold the wait into the lane that acts.
5. Am I about to restate status? → send nothing.
6. On each new owner ask, record it with `ledger.py ask` and dispatch its lane this turn; never queue it behind a busy lane.
7. Am I about to report a milestone? → first dispatch every owner ask still unstarted.
8. Before stating a PR's state, check `ccx vcs status` or the `(#N)` squash on a fetched base; never report it from the plan file.
9. Am I about to relay an ETA for a green PR? → check its gates and label it now if it is a priority PR.
10. Before saying something is assigned, read the summary's `LOST` lines first.
    Never call an ask done before `LIVE`.

Apply D3 to priority PRs before delegating. A call that survives all ten decides
something no lane can decide for you; everything else is a lane.
