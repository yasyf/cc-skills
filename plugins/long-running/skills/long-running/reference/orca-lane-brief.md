# The Orca lane brief

The lane brief in `SKILL.md` assumes an Agent-tool subagent the root can reach with
`SendMessage`. A lane started with `orca orchestration worker-start` runs in a separate
session. It reports through Orca instead of `SendMessage`. The landing desk hears from it
through `ledger.py`, never a message. The brief lives in files on disk; a short
`--spec` points to them. Paste the templates below and fill the angle brackets.

## Why the spec is a pointer

`worker-start --spec` pastes its text into the worker's terminal. Orca truncates the
spec near 3 KB. The full text stays in `dispatch-show --task`, but the worker sees only
the pasted text. A long brief arrives without its Finish section. Store the briefs
outside every repo, in one shared contract file and one file per lane. Concatenate
them into `<spec dir>/<lane>.full.md` before launch:

```sh
cat "<spec dir>/common.md" "<spec dir>/<lane>.md" > "<spec dir>/<lane>.full.md"
```

Pass that full brief to `orca-launch.sh`. It generates this pointer spec, at most
300 characters including the lane, brief path, and worktree path:

```text
Lane <lane>: read <brief> in full first and execute it exactly; Orca truncates specs. Worktree <wt>, bypass-permissions mode; the brief's Escalate rules hold.
```

The Orca preamble above the task carries the worker's handle, dispatch capability,
and exact `send`, `ask`, and `check` commands. The brief tells the worker to copy these
commands verbatim. It never restates them.

Launch through the orca-desk using [Orca workers: launch recipe](orca-workers.md).

## `common.md`: the contract every lane shares

```
# <drive>: lane brief (shared contract)

You are one worker in <drive>. Read <plan path> in full before anything else; <owner
plan or source of truth> is the source of truth behind it.

Authority: everything inside your Ownership below, without asking. Anything that
changes the plan, touches production (<apply, deploy, Slack write, state move>) or
is listed under Escalate stops for the coordinator: use the preamble's
`orca orchestration ask` with the question and 2-4 options. If it returns
"capacity reached", use the preamble's `send --type question` or `--type escalation`
instead. Keep working on everything that does not depend on the answer; the
orca-desk treats those messages exactly like an ask.
Never end a turn waiting and never park. AskUserQuestion is unavailable; on a decision,
take the brief's default, log it with `ccn log append <drive log id>`, and report it.

Never end a turn without a Monitor on your own messages. Re-arm it after compaction.
  Poll `orca orchestration check --terminal "$ORCA_TERMINAL_HANDLE" --all --json`
  every 60 s; dedupe on message id in a seen file and print
  `NEW <id> <type> <subject>` for each new id. Create the seen file once; keep it
  across compaction. Run:

  while :; do orca orchestration check --terminal "$ORCA_TERMINAL_HANDLE" --all --json 2>/dev/null | jq -r '.result.messages[]? | "\(.id) \(.type) \(.subject)"' | while read -r id rest; do grep -qx "$id" <seen file> || { echo "$id" >> <seen file>; echo "NEW $id $rest"; }; done; sleep 60; done

Orca messages are pull-only; idle sessions wake only on terminal input or Monitor events.

Binding rules (owner):
- <one rule per bullet, each as the owner stated it>

Worktree and VCS:
- Your worktree is exclusive to you; never touch another lane's worktree, branch, or
  files outside your Ownership, and never write probes into another worktree.
- Branch prefix: <prefix>/<lane>/. Orca created your worktree on the branch
  <prefix>/<lane>, which git cannot keep beside refs under <prefix>/<lane>/; rename it
  before your first stack: `git branch -m <prefix>/<lane> <prefix>/<lane>/<topic>`.
  One stack per lane; ship with <the repo's ship
  command>. Load <the repo's submit skill> before opening a PR. <title and body format>.
  A PR without a body, or left as a draft, is forbidden.
- <the repo's forbidden commands, verbatim>
- Commit and push every coherent checkpoint. Open a non-draft PR with a body for
  finished changes in the same turn. Drive it through landing under D1.
- When your stack depends on another lane's unlanded branch, stack on top of it and
  say so in the PR body; never copy their diff.

Landing desk (records over cc-notes refs, shared by every checkout):
- Ledger `<id>`; script `<plugin root>/skills/long-running/scripts/ledger.py`; repo
  `<owner/name>`; base `<base>`.
- Holds file `<path>`, root-owned; #<n> names a held PR, lane:<name> a held lane,
  followed by the reason. Only the root edits it.
- You are a separate session. Run register/report yourself; never SendMessage a subagent.
- On spawn: `python3 <ledger.py> register --ledger <id> --lane <lane> --branch-prefix
  <prefix>/<lane>/`.
- On every PR open or push: `python3 <ledger.py> report --ledger <id> --pr <n> --head
  <full sha> --lane <lane> --verdict <clean|red|conflicting|held> --text "<one line>"`.
- The moment your stack has a green, approved bottom prefix, re-read the holds file.
  Before each enqueue, write a fresh digits-only file with
  `grep -o '#[0-9]\+' <holds file> | tr -d '#' > <held file>` and append every open
  PR of a held lane from `python3 <ledger.py> show --ledger <id> --json` under D3.
  Never cache the held set or self-enqueue above a held PR or any PR of a held
  lane. Report `held` on your tip with the held PR named and leave release to
  the root. Where the checkout carries an enqueue script, call it directly as
  `stack-enqueue --hold <held file> <prefix top>`, then report the enqueue with
  `python3 <ledger.py> report`; refresh records it as labelled outside the desk.
  `ledger.py label` cannot pass `--hold` yet. Where the repo has no script, run
  `python3 <ledger.py> label --repo <owner/name> --ledger <id> --pr <prefix top>
  --expect-head <sha> --checkout <worktree>`; mirrored ledger holds are the guard.
  Enqueue the largest contiguous green, approved, unheld bottom prefix as one batch.
  Never wait for the PRs above it. After it lands, restack the rest with
  `ccx vcs stack submit`. Never end a turn with a ready, unheld prefix unenqueued.
  No ruling needed.
- After opening or enqueueing a PR, run
  `ccx vcs pr watch --lane-prefix <branch-prefix> --until landed` under Monitor
  (re-arm on expiry) or in a foreground loop instead of ad-hoc polling. `ejected` or
  `conflicting` means rebase now: `ccx vcs stack submit` from your lane's worktree,
  then re-enqueue at once when the gates pass and the holds file permits it.
  `red <check>` means fix it. Your watch ends only when your PRs land.
- Findings, decisions, and handoffs go to cc-notes (`ccn note add`, `ccn log append`,
  `ccn papercut`), never only into your report.
- Fix or accept each reviewer-bot finding with the risk
  named, and run one finder pass over your diff before you call a PR done.

Escalate early, never improvise: scope surprise, an assumption the code refutes, an
auth or approval gate, or two failed approaches. Ask with 2-4 options.

Finish: a lane with a PR finishes only once its squash `(#N)` is on the base branch.
Drive to a terminal state, then send the preamble's `worker_done` with
`--outcome succeeded|failed`, `--task-id <taskId>`, and `--dispatch-id <dispatchId>`
from that preamble. The three-sentence body names what changed, what was
found, and what is left; the PR numbers, full head shas, and any tool refusal, verbatim,
go in the report file its `--report-path` names.

Coordinator ids: <run id>; <cc-notes ruling and answer ids>.
```

## `<lane>.md`: one lane's part

```
## Lane: <name> (<agent> <model> <effort>). <the mission in one line>.
Target: <files, component, or environment in scope>.
Change: <the concrete result to produce>.
Constraints: <invariants, compatibility rules, do-not-touch boundaries>.
Ownership: <what this lane edits; every shared file and who edits it after it>.
Verified facts, do not re-derive: <ids, shas, URLs, state already confirmed>.
Observable acceptance: <the test, output, or PR URL that proves completion>.
```

Orca's task-spec contract requires every spec to include `Target`, `Change`,
`Constraints`, `Ownership`, and `Observable acceptance`. The shared file supplies the
long-running fields `Authority`, `Escalate`, `Do NOT touch`, `Worktree`, and `Finish`.

## Checks before launch

- Each lane's Ownership excludes every other lane's files. If two lanes edit a shared
  file, the second is a stacked child of the first.
- Every command in `common.md` runs as written from a lane's worktree.
- `<spec dir>/<lane>.full.md` contains the shared contract followed by the lane section.
- The generated pointer spec is at most 300 characters.
