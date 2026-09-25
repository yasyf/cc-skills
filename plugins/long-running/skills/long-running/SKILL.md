---
name: long-running
description: Hard rules for orchestrating multi-lane work without burning the orchestrator's context - ground truth arrives only as a lane's verdict, anything with a body is a lane, every wait folds into the lane that acts, no lane parks and no lane is re-briefed, state lives in cc-notes and the task list, an open-PR ledger grades, routes, and holds every open PR, and a landing-desk lane with its own desk tool is the message queue and merge coordinator between the lanes and the root. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs at once, landing PRs through a merge queue from many lanes, or on any task that will plainly exceed one context window.
---

# Long-running orchestration

Delegating every unit of execution does not protect the orchestrator's window. Every
tool result and every inbound lane message lands in it anyway. What protects it is
refusing to look: the orchestrator reads verdicts, holds decisions, and touches no raw
data. A drive that delegated all of its work and still ran out of context spent it on
logs it read itself, JSON it parsed itself, duplicate notifications it answered, and
status it restated per event.

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

## The five hard rules

**R1. Ground truth reaches you only as a lane's verdict.** Once a lane can answer a
question, never grep a log, list cloud resources, curl an API, open a build page, or
parse JSON in the root context. Ask the owning lane with a scoped resume and take back
≤5 lines. *Prevents the largest single sink: reading CloudWatch and Buildkite output
while an assigned triage lane already held the answer.*

"Verify ground truth yourself, never trust silence" and this rule do not conflict.
Verifying means asking a lane for the one number, and checking liveness means asking
for a 3-line status. Neither means reading the source. Silence is not progress.
Ask, and set a deadline for the answer.

**R2. Anything with a body is a lane.** Any read past one file, any log, any
multi-step investigation, any PR or merge mechanics, any bulk enumeration. The root
context holds decisions and the task list, nothing else. *Prevents the slow leak that
no single call looks responsible for.*

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
2-3 duplicate idle-notifications per real report. *Prevents lane collisions in a shared
worktree and the reply tax on notifications that carry no news.*

**R5. Write it down, do not hold it.** Findings go to cc-notes from the lane that found
them (`log_append`, `investigation_*`, `note_add`, `task_add`), never into the
orchestrator's window. `TaskCreate`/`TaskUpdate` is the root's only state. Report to the
user on milestones or when they must act, never per event. At roughly half the window on
a long drive, write the handoff plan and hand off instead of continuing. *Prevents the
forced mid-drive handoff with nothing written down to hand over.*

## The landing desk and its ledger

On a drive where many lanes open PRs, the root is the wrong place for their reports.
Each report is a message in the root's window, each landing is a wait, and each label is
a REST call the root must not make. The desk is one long-lived lane, `landing-desk`,
that takes all of that. Lanes report to it, it grades and labels, and the root hears
from it once an hour.

`scripts/ledger.py` is its one tool, over `gh api` REST only. Its one store is a cc-notes
ledger with a row per PR our lanes shipped. The holds, the routing, the label history,
and the landing are fields on that row, and each lane message is a `msg/<seq>` row
beside them. `reference/landing-desk-brief.md` is the desk's brief, ready to paste;
`reference/desk-contracts.md` holds the message shapes. It is R5 applied to PR state and
R3 applied to the watching.

Spawn it first, before any lane that will open a PR, whenever three or more lanes
will ship through one merge queue or the drive will outlive one context window. Below
that the lane that opened the PR lands it, and there is no desk.

**D1. Lanes address the desk, never the root.** A lane's last action on a PR is the
3-line report of PR, full head sha, and verdict, sent to `landing-desk`. The root receives only
`RULING NEEDED` lines and the hourly summary. *Prevents the root window filling with
forty lanes' ship reports and their duplicate idle notices.*

**D2. A PR is the desk's only because a lane reported it.** `ledger.py report` and an
explicit `refresh --pr` are the only paths that open a row. The desk never lists the
repository's pull requests, and a PR it cannot trace to a report is not tracked, not
graded, not labelled, and not counted; there is no "unknown" list. *Prevents routing
comments and rebase orders landing on other engineers' PRs, which one repo-wide sweep did
twenty times in an hour.*

**D3. The label goes on the stack's tip once, after every PR is re-read and passes.**
`ledger.py label --pr <tip>` walks base refs to the repo's default branch and re-reads
every PR. Each must be open, unheld, approved (any commit counts; a dismissed or
withdrawn approval does not), and unchanged from `--expect-head` at the tip or its
row's `reported_head` downstack. Each head must be at least a minute old, never
labelled or pulled before, with successful commit status, no failed checks, and a
completed, successful latest `ai-review`. Allowed `mergeable_state` values are `clean`,
`behind`, and `has_hooks`; `--checkout` also checks each head for conflicts with its base.
An untracked downstack PR, an orphaned base, or an open child outside the enqueued
stack refuses the attempt. One red PR refuses the whole stack; nothing is labelled.
When all pass, one `merge` label on the tip enqueues the whole stack as one entry.
Every row records `label_head`, `labelled_at`, `approved_by`, and `label_stack`.
A refusal names the reason; the desk routes or holds, it never retries the same head.
*Prevents a green tip enqueueing a red parent, and the re-queue loop where an ejected
head is relabelled unchanged and ejected again until someone notices.*

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

`refresh` regrades the rows the ledger holds and merges the forge's fields into them, so
the fields the desk writes are never overwritten: `lane`, `declared_intent`, the holds,
the routing, the label history, and the landing. A refresh that cannot reach the forge
exits non-zero and writes nothing. The records live on `refs/cc-notes/*` and survive
compaction, a session restart, and a handoff. A fresh `landing-desk` lane spawned with the
ledger id reads the inbox, the holds, the routes, the label history, and the landings
exactly as the last one left them. None of that goes into session memory or the plan file.

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
Finish: drive to a terminal state, then SendMessage <orchestrator> exactly one report,
  ≤10 lines: verdict | ids | what changed | what is next. That message is your last
  action. Do not end a turn waiting.
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

# as each lane message arrives, typed in verbatim; duplicates are dropped
ledger.py report  --ledger "$LEDGER" --pr 21221 --head <sha> --lane lightning-eh --verdict clean
ledger.py ruling  --ledger "$LEDGER" --lane p2-edge-rows --pr 20284 --text "land without the document form" --options "A land|B hold|C close"
ledger.py enqueue --ledger "$LEDGER" --kind idle --pr 21221 --head <sha> --lane lightning-eh --text "done"
ledger.py inbox   --ledger "$LEDGER" --take

# every 20 minutes, one REST batch, in this order
ledger.py refresh --repo "$REPO" --ledger "$LEDGER"
ledger.py landed  --repo "$REPO" --ledger "$LEDGER" --checkout "$CHECKOUT"
ledger.py route   --repo "$REPO" --ledger "$LEDGER"

# per clean stack: every guard on every PR, then one tip label; per blocker the forge cannot see: one route or hold
ledger.py label --repo "$REPO" --ledger "$LEDGER" --pr <tip> --expect-head <tip-sha> --checkout "$CHECKOUT"
ledger.py route --repo "$REPO" --ledger "$LEDGER" --pr 21221 --job "plan comment missing for this head"
ledger.py hold  --ledger "$LEDGER" --pr 20284 --reason "waits on #20314" --hours 4

# hourly, and the only desk output the root reads
ledger.py summary --ledger "$LEDGER"
```

`label --dry-run` runs every guard, prints the stack it would enqueue, and writes
nothing; run it once on a repo before the first live label. `route` without `--pr`
sweeps every red or conflicting row, reads the first failing line from the Buildkite
log, prints the message to send each lane, and records it; `route --dry-run` prints
and records nothing. `unlabel --reason` records why a label came off and blocks a
re-label of that head; it does not stop a queue that already took the PR. A lane
asking what it owns gets `ledger.py show --red`, never the raw table.

### Handoff plan

At roughly half the window, write this and stop driving.

```md
## State
<what is true now, verified, with ids>

## Live lanes
| lane | owns | last verdict | expected next message |

## Automatic chains
<what lands with nobody acting, and what fires when it does>

## Owner actions
<what only the human can do: console clicks, approvals, credentials>

## Ids
<PRs, builds, stacks, worktrees, cc-notes ids>
```

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

## Checklist before every tool call

1. Could a lane return this as ≤10 lines? → delegate.
2. Is this raw data - log, JSON, resource list, webpage, diff? → delegate.
3. Does a lane already own this question? → scoped resume, do not look.
4. Am I about to wait? → fold the wait into the lane that acts.
5. Am I about to restate status? → send nothing.

A call that survives all five decides something no lane can decide for you. Everything
else is a lane.
