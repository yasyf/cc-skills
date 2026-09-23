---
name: pr-loop
description: Attach to an already-open pull request and iterate it until CI is green and the bots are quiet — poll checks, review verdicts, and bot comments through a Monitor on the bundled poll script, apply the fixes the failure itself determines (formatter output, single-resolution compile and type errors, stale lockfiles and snapshots, review suggestion blocks, bot nits naming the exact replacement), ship each through ccx vcs ship, rebut bot findings the code refutes, and bring everything else back with 2–4 concrete options. Four mechanical safety gates precede every edit and a per-PR state file caps attempts per check across restarts. The human-driven counterpart to open-pr's background watcher. Use when the user says "watch this PR", "fix CI on PR 123", "get the PR green", "address the review comments", or "why is CI failing on my PR", when driving a PR someone else opened, or when resuming a PR whose background watcher already reported.
allowed-tools: Bash(gh:*, ccx:*, jq:*, bash:*), Read, Edit, Write, Grep, Monitor, TaskStop, Agent
---

# PR Loop

Attach to an already-open PR and iterate until CI is green and the bots are
quiet. `open-pr` opens a PR and hands the watch to a background agent that
runs unattended with interim eviction reports and a final verdict. This
skill is the surface a human drives — a day later, on a red check, on
review comments, or on a PR someone else
opened. The human is present, so decisions go to them directly.

## Attach

Resolve the slug and PR number from the user's words or the current branch
(`gh pr view --json number,url,title,state,headRefOid`). A merged PR needs
no loop. A closed PR is not yet a verdict: a merge queue squash-merges onto
trunk and closes the PR it landed, so a queue-merged PR reports
`state: CLOSED` with `mergedAt: null` and no merge commit — the state field
alone reads a landed PR as an abandoned one. The landing resolves it: the
squash subject on the base branch, or the queue's "Merged by" line. The
closer actor does not — the queue closes what it drops as well.

```bash
git log --format='%H%x09%s' origin/<base> | grep -E '\(#<pr>\)$'
gh api repos/<owner>/<name>/issues/<pr>/comments --paginate \
  --jq '.[] | select((.body // "") | test("Merged by the \\[?Graphite merge queue"; "i")) | .id'
```

Confirm a queue merge in trunk history, anchored to the subject suffix —
`git log --format='%H%x09%s' origin/<trunk> | grep -E '\(#<pr>\)$'` —
because PR-number mentions in commit bodies false-positive. Either way the
loop is over; report merged or abandoned, never bare "closed". Check out
the PR branch when not already on it (`gh pr checkout <pr>`).

Per-PR state lives at `<cache>/pr/<number>.json`, where `<cache>` comes from
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/pr-cache.sh" path <owner/repo>`. It
carries `head_at_last_pass`, comment, review, and event watermarks, queue
and conflict state, `checks_seen`,
an `attempts` map per check, and the `applied` log — the schema is in
[reference/triage.md](reference/triage.md). Read it before the first poll:
the attempts map is what carries the two-attempt cap across a restart, and a
PR the background watcher already worked has its history here.

## Watch

Foreground `sleep` is blocked in this harness, so the watch is a Monitor
over the bundled poll script:

```
Monitor(command: 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/pr-poll.sh" <repo> <pr> <state-file>',
        description: "CI checks and bot comments on <repo>#<pr>",
        persistent: true)
```

One stdout line per event:

```
CHECK    <name> <bucket> <link>
REVIEW   <author> <state> <id>
COMMENT  <author> <id> <first-80>
QUEUED   <actor> <label|comment-id>
UNQUEUED <actor>
DONE     all-green | merged | queue-merged | closed | checks-failed |
         conflicted | deadline-still-open
DONE     evicted <conflicts|failed-ci|downstack|head-moved|other|unknown> <detail>
```

The script reads the PR, check runs, commit statuses, issue events, reviews,
and comments through REST. `PR_POLL_INTERVAL` defaults to 120 seconds, with
a floor of 120 seconds or 10 times `PR_POLL_STACK`, whichever is larger.
`PR_POLL_STACK` is the number of concurrent PR watches (default 1). Prefix
the Monitor command with `PR_POLL_QUEUE_LABEL=<label>` when the repo's queue
label is not `merge`.

`QUEUED` keeps the watch armed through green checks. `UNQUEUED` means a
human removed the queue label; neither event ends the round.
`queue-merged` is a merge: the queue
squash-merges, so the PR reads `CLOSED` with a null `mergedAt` and only the
squash on the base branch or the queue's "Merged by" line distinguishes it
from an abandoned one. `deadline-still-open` ends the watch on time instead
of polling forever; `PR_POLL_DEADLINE` sets it, four hours by default.

`pr-poll.sh` exits after any `DONE` line, which ends that watch —
`persistent: true` only removes the timeout, so a monitor whose command
exited stays stopped. Each `DONE` is therefore the end of a round, not the
end of the loop.

The states behind the tokens, by meaning: open (checks running or red),
green (checks passed, `mergeable: true`, neither queued nor evicted pending
relabel), queued for merge (still watched through green), evicted (a bot
removed the queue label or a merge-activity bullet logged a drop), merged,
abandoned, and conflicted. A conflict needs one `mergeable_state: dirty`
read, or two `mergeable: false` reads with no true between; null/unknown
mergeability is not a read. It fires once per head.

Report conflicts immediately: run `git fetch origin <base>`, then
`git merge-tree --write-tree --name-only --no-messages origin/<base> <head>`.
The lines after the first tree oid are the conflicted paths. Report
`blocked: conflicts with <base> in <paths>` with the options of rebasing
onto the base tip or resolving by hand; rewriting the branch is the user's
call. `TaskStop` the monitor and end the loop.

Report `evicted: <reason> <detail>` immediately, including the same
merge-tree paths for conflicts. Then **arm a fresh Monitor on the same
state file** and keep watching for the caller's relabel. The recorded
eviction suppresses a second report of the same label removal and prevents `all-green`;
a recorded conflict stays silent on the same head. Relabel clears the
eviction and emits `QUEUED`. The watch still sees a new head, a landing, or
the deadline.

The caller relabels after pushing any fix; the watcher never
relabels. Triage `failed-ci` like `checks-failed`, fix the named PR for
`downstack #N`, relabel after `head-moved`, and report the text for
`other` or `unknown`.

Green, merged, abandoned, and the deadline end the loop: `TaskStop` the
monitor and report. On a queue lane, merged versus abandoned comes from the
landing on the base branch (see Attach), never the state field or the
closer actor. On failed checks, triage the reds; ship or rebut what triage
settles, then **arm a fresh Monitor** on the new head and keep going.
Exhausted attempts or a fix needing a decision end the loop with a report;
an eviction report keeps the watch armed for relabel.

## Ground truth

A poll line says something changed; these say what:

```bash
gh pr checks <pr> --repo <slug> --json name,state,bucket,link,description,workflow
gh pr view <pr> --json state,mergedAt,headRefOid,url,title
gh pr diff <pr> --name-only
gh api "repos/<slug>/issues/<pr>/comments?since=<watermark>"
gh api "repos/<slug>/pulls/<pr>/comments?since=<watermark>"   # inline threads; ```suggestion lives here
gh api "repos/<slug>/pulls/<pr>/reviews"
```

## Gates — before writing a byte

Four checks, in order, all mechanical. When one fails, describe the fix and
what blocked it instead of editing — a blocked fix described precisely costs
one message; a fix landed on top of someone's uncommitted work costs them
the work.

1. `ccx vcs diff` reports no pending change — the working copy is clean.
2. Local head equals `gh pr view <pr> --json headRefOid` — the edit lands on
   what the PR contains, not something ahead of or behind it.
3. Every file the fix touches appears in `gh pr diff <pr> --name-only` —
   the loop iterates the PR; growing it is a decision to bring back.
4. The target check has fewer than 2 recorded attempts in the state file —
   a third attempt at the same red check is a guess.

## Fix, rebut, or ask

Asking is the default verdict, not the fallback: the human is present, and
a question with 2–4 concrete options costs one message, while a wrong
autonomous fix costs a revert. Fix without asking only when the failure or
the reviewer already wrote the fix: formatter output, a compile or type
error with exactly one resolution, a stale lockfile or snapshot the diff
was supposed to regenerate, a review `suggestion` block, a bot nit quoting
the exact replacement.

Rebut when a review bot is wrong — a normal outcome, not an edge case. A
rebuttal is a thread reply carrying the evidence — the guard the bot
missed, the test that already covers the path — and no code change. It
beats fixing when the suggested change would worsen correct code; it beats
asking when the code itself settles the claim, leaving the human nothing to
decide. A human reviewer's wrong claim is still the human's voice: draft
the rebuttal and bring it back as an option.

Everything else — two live resolutions, an opinion, anything that grows the
diff — goes to the user. The full taxonomy with worked examples is in
[reference/triage.md](reference/triage.md); the line in three:

<example label="failure determines the fix">
`ruff format --check` fails and `ruff format` rewrites the file — the tool
printed the answer; run it, ship it.
</example>
<example label="bot is wrong — rebut">
A bot flags a possible nil dereference on a pointer the function's first
line already guards — reply quoting the guard, resolve the thread; no edit,
no question.
</example>
<example label="fixing means deciding">
`test_retry_backoff` asserts 3 retries and the code makes 2 — either could
be right; bring back "cap at 2 and update the test, or restore 3?"
</example>

After a fix: re-run the check's local equivalent where one exists, ship with
`ccx vcs ship -m "<subject in the repo's convention>" --no-watch`, record
the attempt and the `applied` entry in the state file, and keep watching —
the monitor reports the rerun.

Options brought back are 2–4 and concrete: named files, named tradeoffs, a
recommendation. "The test fails, what should I do?" wastes the human's
presence.

## Delegate the heavy reads

Logs and review threads stay out of this context:

- A red check → spawn `cc-context:ci-triage` with the run id; it returns the
  root cause, a minimal excerpt, and a next step.
- A review or comment burst → spawn `cc-context:pr-review-triage` with the
  PR (and the review id when one event triggered it); it returns per comment
  a verdict, the concrete change, a draft reply, and the `gh api` recipe to
  post it.

Spawn shapes are in [reference/triage.md](reference/triage.md).
`ccx vcs reviews <pr>` is the richer review stream but blocks instead of
emitting a line per event, so it composes with a watching human, not with
Monitor.

<success_criteria>
The loop ends with a report: the PR is green and quiet (checks passing,
`mergeable: true`, neither queued nor evicted, every actionable comment
answered); it merged or was abandoned, told apart by the landing on the
base; or it is blocked by conflicts, exhausted attempts, a decision, or the
deadline. Evictions report immediately and keep the watch armed for
relabel. Every shipped fix passed all four gates first and appears in the
state file's `applied` log. The monitor is stopped when the loop ends.
</success_criteria>
