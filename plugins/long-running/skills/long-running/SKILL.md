---
name: long-running
description: Hard rules for orchestrating multi-lane work without burning the orchestrator's context - routine ground truth arrives as a lane's verdict, anything with a body is a lane, every wait folds into the lane that acts, no lane parks and no lane is re-briefed, state lives in cc-notes and the task list, an open-PR ledger grades, routes, and holds every open PR, and a landing-desk lane with its own desk tool is the message queue and merge coordinator between the lanes and the root. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs at once, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
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

## The seven hard rules

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

**R4. Never re-brief a lane, never answer a duplicate.** `SendMessage` to a finished
agent resumes it carrying its whole original brief and it re-runs that brief.
`SendMessage` to a running agent spawns a second copy that restarts the lane while the
original never sees the message. Reply to nothing you have already acted on, and expect
2-3 duplicate idle-notifications per real report. A rotation respawn is not a re-brief:
the old lane is flushed and stopped first, and the fresh one resumes from the ledger, so
nothing runs twice (see Lane rotation). *Prevents lane collisions in a shared worktree
and the reply tax on notifications that carry no news.*

**R5. Write it down, do not hold it.** Findings go to cc-notes from the lane that found
them (`log_append`, `investigation_*`, `note_add`, `task_add`), never into the
orchestrator's window. `TaskCreate`/`TaskUpdate` is the root's only state. Report to the
user on milestones or when they must act, never per event. Once `long-running` is
invoked, the session's compaction handoff runs on its own — see Compaction handoff
below. *Prevents the forced mid-drive handoff with nothing written down to hand over.*

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

## The landing desk and its ledger

On a drive where many lanes open PRs, the root is the wrong place for their reports.
Each report is a message in the root's window, and each landing is a wait. The desk is
one long-lived lane, `landing-desk`, that takes those reports and owns routine labels;
the root checks and labels priority PRs under D3. Lanes report to the desk, it grades
and labels, and the root hears from it every 30 minutes.

`scripts/ledger.py` is its one tool, over `gh api` REST only. Its one store is a cc-notes
ledger with a row per PR our lanes shipped. The holds, the routing, the label history,
and the landing are fields on that row, and each lane message is a `msg/<seq>` row
beside them. `reference/landing-desk-brief.md` is the desk's brief, ready to paste;
`reference/desk-contracts.md` holds the message shapes. It is R5 applied to PR state and
R3 applied to the watching.

Spawn it first, before any lane that will open a PR, whenever three or more lanes
will ship through one merge queue or the drive will outlive one context window. Below
that the lane that opened the PR lands it, and there is no desk.

**D1. Address reports to the desk and act on priority PRs.** A lane's last action on a PR is the three-line report of PR, full head sha, and verdict, sent to `landing-desk`. The root receives only `RULING NEEDED` lines and the 30-minute summary. Never relay a lane's ETA for a green PR. If the owner flags a PR as priority, or it blocks a release or a user, the root checks its gates and adds the merge label itself in the same turn under D3.

*Prevents the root relaying a lane's 45-60 minute ETA for green priority PR #25145 while the owner queued it by hand.*

**D2. Track and grade our lanes' PRs.** `ledger.py report`, `ledger.py register` with a lane's branch prefix, and an explicit `refresh --pr` are the only paths that open a row. A PR on a registered prefix or reported by a lane is tracked and graded without waiting for a lane report at its current head. The desk never lists the repository's pull requests; a PR it cannot trace to one of our lanes stays outside the ledger and its counts.

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
*Prevents the red PR that sat for hours because the pass that found it only recorded
it, and the routing comment on someone else's PR.*

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
Finish: drive to a terminal state, then SendMessage <orchestrator> exactly one report,
  ≤10 lines: verdict | ids | what changed | what is next. That message is your last
  action. Do not end a turn waiting. Every push to a reported PR re-reports the new
  head to landing-desk in the same turn; the desk grades without waiting for it.
```

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

### Desk cadence

The desk owns the whole loop below. The ledger is created once, when the desk is
spawned, and its id goes into the brief; everything after that is `ledger.py`.

```sh
LEDGER=$(ledger.py init --title "desk: $DRIVE")

# on each report: record it and grade the current head; reports are not a gate
ledger.py report  --ledger "$LEDGER" --pr 21221 --head <sha> --lane lightning-eh --verdict clean
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
ledger.py label --repo "$REPO" --ledger "$LEDGER" --pr <tip> --expect-head <tip-sha> --checkout "$CHECKOUT"
ledger.py route --repo "$REPO" --ledger "$LEDGER" --pr 21221 --job "plan comment missing for this head"
ledger.py hold  --ledger "$LEDGER" --pr 20284 --reason "waits on #20314" --hours 4

# every 30 minutes, and the only desk output the root reads
ledger.py summary --repo "$REPO" --ledger "$LEDGER" --checkout "$CHECKOUT"

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

### Compaction handoff

Once `long-running` is invoked — the Skill call itself, or a `/long-running` prompt —
this plugin's capt-hook pack keeps the session's compaction handoff on autopilot for
the rest of the session, compactions included. Nothing inside the session clears it
early.

**Threshold.** The window is `CLAUDE_CODE_AUTO_COMPACT_WINDOW` env, else the
`autoCompactWindow` setting, else the model default (1M for `[1m]`, 200k otherwise).
`threshold = window − 33k`. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` can only lower it further.
The hook fires on the main-session `Stop`, never a subagent's, once used tokens cross
80% of that threshold.

**Archive, then rewrite.** The hook archives the plan itself, as
`<stem>.<YYYY-MM-DD>-<HHMM>-pre-compact.md` in UTC beside it. Never archive by hand —
the guard that lets a plan rewrite through checks for exactly this sibling — and never
`cat >` the plan to dodge that guard; write over it and let the archive carry the loss.

It then blocks the turn with a directive: do not enter plan mode. Record any durable
state still living only in this conversation in cc-notes first (ledger, rulings log,
notes) — the archive is history, not a store. Then rewrite the plan with one `Write`,
dropping everything unnecessary (finished work, superseded state, anything the
archives already hold), using exactly this skeleton:

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

## Archived plans (history only, never needed to restart)
- <one line per archive, newest first>
```

`Restart here` front-loads what a cold restart needs before anything else: the role
this session is playing, where the lane contract lives, which agent is the landing desk
and its ledger id, and the rulings-log id. It carries no recap of finished work. A
mid-drive gotcha that matters to a fresh restart belongs there too, not buried in
`Key notes`.

Then end the turn. The hook types `/compact` through orca itself; outside orca it
allows the stop and tells the user to run `/compact` by hand instead. After compaction,
`SessionStart` points the fresh context at the rewritten plan — that plan supersedes
the summary, read it first.

Auto-compaction can still fire mid-turn, ahead of the proactive 80% check, if one turn
alone grows past 20% of the threshold. `PreCompact` and `SessionStart` re-ground on the
plan either way, so nothing is lost, only unplanned.

### Lane rotation

Every turn a lane takes re-reads its whole history. A desk at 400k tokens pays about
400k per wake to type in a three-line report, while everything it needs to continue
already sits on its ledger. A long-lived lane is therefore rotated, not kept: flushed,
stopped, and respawned fresh under the same name.

**Threshold.** A lane's context is its last assistant turn's input plus cache tokens.
At 150k it is due. Once `long-running` is invoked, the same capt-hook pack checks every
live named lane on the main-session `Stop` and blocks the turn with each lane over the
line and its count, once per lane transcript. Every compaction handoff directive also
lists the live lanes over the line. Rotate them before ending that turn, and record each
new agent in `## Restart here`.

**Protocol.**

1. Send the lane one message:
   `ROTATE: record anything not yet in the ledger or cc-notes, reply "flushed <ledger id>", then stop.`
2. On `flushed <ledger id>`, `TaskStop` it first. A spawn under a name a running lane
   still holds gets a different name.
3. Then spawn a fresh lane with the `Agent` tool under the same name, with its original
   spawn brief plus the ledger id. Messages addressed by name reach the newest agent.

Never `SendMessage` the stopped lane. That resumes the same transcript and reloads the
whole history the rotation dropped. An `open-pr:pr-watcher` needs no flush, since
its state file is its ledger. `TaskStop` it and spawn a fresh one with the same inputs,
which resumes from that file. A lane with nothing left to do is stopped, not respawned.

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
- Every new owner ask appended to one busy lane's queue; ten asks sat unstarted for
  hours behind its four open PRs until the owner asked.
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

## Checklist before every tool call

1. Could a lane return this as ≤10 lines? → delegate.
2. Is this raw data - log, JSON, resource list, webpage, diff? → delegate.
3. Does a lane already own this question? → scoped resume, do not look.
4. Am I about to wait? → fold the wait into the lane that acts.
5. Am I about to restate status? → send nothing.
6. Did an owner ask just arrive? → dispatch its lane this turn; never queue it behind a busy lane.
7. Am I about to report a milestone? → first dispatch every owner ask still unstarted.
8. Am I about to state a PR's state? → check it first: `ccx vcs status` or the `(#N)` squash on a fetched base.
9. Am I about to relay an ETA for a green PR? → check its gates and label it now if it is a priority PR.

Apply D3 to priority PRs before delegating. A call that survives all nine decides
something no lane can decide for you; everything else is a lane.
