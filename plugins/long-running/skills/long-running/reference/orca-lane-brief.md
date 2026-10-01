# The Orca lane brief

The lane brief in `SKILL.md` assumes an Agent-tool subagent the root can reach with
`SendMessage`. A lane started with `orca orchestration worker-start` runs in a separate
session. It reports through Orca instead of `SendMessage`. The landing desk hears from it
through `ledger.py`, never a message. Briefs live as attachments on the drive's
`briefs: <slug>` log; a short `--spec` points to the resolved attachment path.
Paste the templates below and fill the angle brackets.

## Why the spec is a pointer

`worker-start --spec` pastes its text into the worker's terminal. Orca truncates the
spec near 3 KB. The full text stays in `dispatch-show --task`, but the worker sees only
the pasted text. A long brief arrives without its Finish section.

Store `common.md` and each lane's brief as attachments on the drive's briefs log.
Attach each complete brief as `<lane>.full.md`, with the shared contract followed
by the lane's brief.
`orca-launch.sh` still takes a file path; resolve the attachment when launching:

```sh
orca-launch.sh <lane> <model> <effort> \
  "$(ccn -R <drive checkout> attachment path <briefs log> <lane>.full.md)"
```

The launcher generates this pointer spec, at most
300 characters including the lane, brief path, and worktree path:

```text
Lane <lane>: read <brief> in full first and execute it exactly; Orca truncates specs. Worktree <wt>, bypass-permissions mode; the brief's Escalate rules hold.
```

When the pointer would pass 300 characters, the script links the brief at
`~/.claude/<8 hex>` and points there instead; only a worktree path long enough to
overflow on its own fails the launch.

The Orca preamble above the task carries the worker's handle, dispatch capability,
and exact `send`, `ask`, and `check` commands. The brief tells the worker to copy these
commands verbatim. It never restates them.

Launch with `desk-runner.py launch --config C --key R<n> --lane L --model M
--effort E --brief PATH`, resolving `PATH` with `ccn attachment path`, using
[the launch recipe](orca-workers.md).

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
runner judges those messages from your brief exactly like an ask.
Never end a turn waiting and never park. AskUserQuestion is unavailable; on a decision,
take the brief's default, log it with `ccn log append <drive log id>`, and report it.

A question the plan or a recorded owner ruling already answers is not a question:
apply the answer, cite it, and never put it in an ask or in an owner-question list.
An owner-question list you write marks, for every item, which authorities you checked
(plan section, durable answer id, memory) and holds only items none of them settles.

Never end a turn without a Monitor on your own messages. Re-arm it after compaction.
  Poll `orca orchestration check --terminal "$ORCA_TERMINAL_HANDLE" --all --json`
  every 60 s; dedupe on message id in a seen file and print
  `NEW <id> <type> <subject>` for each new id. Create the seen file once; keep it
  across compaction. Run:

  while :; do orca orchestration check --terminal "$ORCA_TERMINAL_HANDLE" --all --json 2>/dev/null | jq -r '.result.messages[]? | "\(.id) \(.type) \(.subject)"' | while read -r id rest; do grep -qx "$id" <seen file> || { echo "$id" >> <seen file>; echo "NEW $id $rest"; }; done; sleep 60; done

Orca messages are pull-only; idle sessions wake only on terminal input or Monitor events.

Runner actions: every guidance relay names its key and thread id. Before acting,
send the preamble's status command with your own dispatch and that thread:
  orca orchestration send --type status --thread-id <thread> --dispatch-id <its dispatch> --subject "started <key>"
After acting, send:
  orca orchestration send --type status --thread-id <thread> --dispatch-id <its dispatch> --subject "done <key>: <result>"
Carry the preamble's addressing and capability arguments. A send proves delivery,
not that you started. The action moves from accepted to started, completed, and
verified; failed and unverifiable record failure or missing proof. Your done reply
records completion; verification needs an external receipt. Do not mark root tasks
complete for runner actions.

A relaunch offers this lane's container to the new dispatch; its first ack takes
ownership at the next owner generation. Until then the old owner retains it.
After transfer, an ack from another dispatch gets a stand-down reply and cannot
move the original action. Obey that stand-down without ending or closing your
session. Never act on a replayed key you already completed.

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
- Shape the stack so it lands beside every other stack under D19: put each shared-file
  edit in the smallest additive first PR, in the file's declared order. When a file you
  change is in another open PR, stack on that PR instead of racing it. Never commit a
  generated output; the build generates it. A stack that still carries a regenerated
  artifact rebases and regenerates it on ejection.

Landing desk (records over cc-notes refs, shared by every checkout):
- Ledger `<id>`; script `ledger.py`, on PATH by name; repo
  `<owner/name>`; base `<base>`.
- Holds file `<path>`, root-owned; #<n> names a held PR, lane:<name> a held lane,
  followed by the reason. Only the root edits it.
- You are a separate session. Register your prefix and report verdicts yourself;
  the hook records opened PRs, with hand registration as the fallback. Never SendMessage a subagent.
- On spawn: `ledger.py register --ledger <id> --lane <lane> --branch-prefix
  <prefix>/<lane>/`.
- On every PR open, push, and READY: `ledger.py report --ledger <id> --pr <n> --head
  <full sha> --lane <lane> --verdict <clean|red|conflicting|held> --text "<one line>"`.
  READY is `--verdict clean --text "READY ..."`; the desk reads only this row.
- The moment your stack has a green, approved bottom prefix, re-read the holds file.
  Before each enqueue, write a fresh digits-only file with
  `grep -o '#[0-9]\+' <holds file> | tr -d '#' > <held file>` and append every open
  PR of a held lane from `ledger.py show --ledger <id> --json` under D3.
  Never cache the held set or self-enqueue above a held PR or any PR of a held
  lane. Report `held` on your tip with the held PR named and leave release to
  the root. Where the checkout carries an enqueue script, call it directly as
  `stack-enqueue <prefix top> --hold $(cat <held file>)`. Drop `--hold` when the
  numeric file is empty; it requires at least one number, never a filename.
  Argparse exit 2 otherwise reads as unsettled. Report the enqueue with
  `ledger.py report`; refresh records it as labelled outside the desk.
  `ledger.py label` cannot pass `--hold` yet. Where the repo has no script, run
  `ledger.py label --repo <owner/name> --ledger <id> --pr <prefix top>
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
- GitHub budget: GraphQL is one 5000-point hourly budget for the whole account, 1 point
  per call. Watch PRs only through `ccx vcs pr watch --state <file>` or REST
  `gh api repos/<owner>/<repo>/commits/<sha>/check-runs`, at most once a minute; never
  loop `gh pr view --json statusCheckRollup` or `gh pr checks`. Probe `rateLimit` at
  most every 5 minutes. Ship only while `rate.remaining` in
  `~/Library/Caches/cc-context/prstate/<owner>/<repo>/state.json` exceeds 1500; never
  gate on REST `gh api rate_limit`, which reads stale.
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
Design rulings, verbatim: <each owner ruling on this subsystem, quoted with its id, and the entry point (symbol at file:line) it makes the change call; or "none">.
Design check: before READY-FOR-SHIP, send `DESIGN-CHECK <symbol at file:line>; <each ruling, met how>; leaves out: <none, or each piece>`. The coordinator confirms it before a ship lane launches; a change that leaves out part of a ruling holds.
Standing rules served: <`R<n>` ids with their answer ids, or "none">; completing this lane never retires them.
Observable acceptance: <the test, output, or PR URL that proves completion>.
```

Orca's task-spec contract requires every spec to include `Target`, `Change`,
`Constraints`, `Ownership`, and `Observable acceptance`. The shared file supplies the
long-running fields `Authority`, `Escalate`, `Do NOT touch`, `Worktree`, and `Finish`.

## Checks before launch

- A lane that touches a subsystem the owner has ruled on quotes each ruling verbatim
  and names its entry point as a symbol at file:line, never as a package list.
- Each lane's Ownership excludes every other lane's files. If two lanes edit a shared
  file, the second is a stacked child of the first.
- Every command in `common.md` runs as written from a lane's worktree.
- The `<lane>.full.md` attachment contains the shared contract followed by the lane
  section. Resolve its path with `ccn attachment path` before launch.
- The generated pointer spec is at most 300 characters.
