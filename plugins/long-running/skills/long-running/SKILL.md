---
name: long-running
description: Hard rules for orchestrating multi-lane work without burning the orchestrator's context - ground truth arrives only as a lane's verdict, anything with a body is a lane, every wait folds into the lane that acts, no lane parks and no lane is re-briefed, state lives in cc-notes and the task list, and an open-PR ledger grades, routes, and holds every open PR. Use when orchestrating multi-lane work, driving a CI or infra bring-up, running a migration or audit across many units, supervising background agents or PR landings, tracking more than ten open PRs at once, or on any task that will plainly exceed one context window.
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

## The open-PR ledger

`scripts/ledger.py` keeps one row per open PR in a cc-notes ledger, over `gh api` REST
only. It is R5 applied to PR state and R3 applied to the watching. The ledger is where PR
state gets written down instead of held, and refresh-then-route is one sequencer lane,
never a root-context poll.

Arm it on any orchestration carrying more than ten open PRs, and on any drive where a PR
can go red without a lane noticing. Below that the lane that opened the PR still owns it
and there is nothing to track.

**L1. Refresh every 20 minutes.** One sequencer lane owns the cadence, and the
orchestrator reads the desk's hourly summary rather than the ledger itself. *Prevents the
root-context `gh pr list` that R1 already forbids and the ledger makes unnecessary.*

**L2. Route every red or conflicting row in the pass that finds it.** `refresh` then
`route`, never `refresh` now and `route` when there is time. An unrouted red row is
indistinguishable from a tracked one. Both are a line in a table nobody has acted on.
*Prevents the red PR that sat for hours because the pass that found it only recorded it.*

**L3. A parked row carries `hold_reason` and `hold_since`.** A row parked without a
reason is an untracked row wearing a ledger's clothes, and a report that counts it as held
is reporting a decision nobody made. *Prevents the hold nobody can lift because nobody
remembers what it was waiting for.*

**L4. Grade the head you read, and record it.** `route` writes `last_graded_head`, and a
row whose `head` no longer equals it has moved since it was last routed, so the prior
verdict is void and the row is routed again. *Prevents a stale green and a stale red
alike, both of which name a sha nobody graded.*

`lane`, `hold_reason`, `hold_since`, and `declared_intent` are orchestrator-owned:
`refresh` merges fields instead of replacing them, so it never overwrites them. It regrades
the rows the ledger already holds plus any `--pr` it is handed, and never lists the
repository's pull requests: a PR is in the ledger because one of our lanes reported it,
and a PR no lane reported is nobody's to grade, route, or count. A closed row stays,
marked `state=closed`, until `desk.py landed` settles it from the squash on its base
branch. Merged is a fact about the trunk, never about PR state: the queue leaves a landed
PR reading closed with merged false, and a PR auto-closed because its base branch was
deleted reads identically.

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

### Ledger cadence

One lane owns the whole cadence. `refresh` regrades the rows the ledger holds and syncs
them; `route` emits one verdict per red or conflicting row and stamps `last_graded_head`.
Neither writes to the pull request: a lane is addressed where it listens, and a comment on
a PR reaches whoever happens to read it.

```sh
LEDGER=$(ccn ledger add --title "open PRs: $DRIVE")   # once, when the drive arms

# every 20 minutes, in this order, in one sequencer lane
ledger.py refresh --repo "$REPO" --ledger "$LEDGER"
ledger.py route   --repo "$REPO" --ledger "$LEDGER"
```

`refresh --pr <n>` admits a PR a lane just reported; without `--pr` it regrades what it
holds. A refresh that cannot reach the forge exits non-zero and writes nothing, rather
than syncing the rows it managed to grade: a partial row set reports a state nobody
observed while the caller believes it refreshed. `route --dry-run` prints every verdict it
would record and writes nothing. Route skips a row whose `last_graded_head` already equals
its `head`, so a re-run after a crash re-grades nothing. A lane asking what it owns gets
`ledger.py show --red`, never the raw table.

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

## Checklist before every tool call

1. Could a lane return this as ≤10 lines? → delegate.
2. Is this raw data - log, JSON, resource list, webpage, diff? → delegate.
3. Does a lane already own this question? → scoped resume, do not look.
4. Am I about to wait? → fold the wait into the lane that acts.
5. Am I about to restate status? → send nothing.

A call that survives all five decides something no lane can decide for you. Everything
else is a lane.
