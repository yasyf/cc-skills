# The Orca lane brief

The lane brief in `SKILL.md` assumes an Agent-tool subagent the root can reach with
`SendMessage`. A lane started with `orca orchestration worker-start` runs in a separate
session. It reports through Orca instead of `SendMessage`. The landing desk hears from it
through `ledger.py`, never a message. Briefs live as attachments on the drive's
`briefs: <slug>` log; a short `--spec` points to the resolved attachment path.
Paste the templates below and fill the angle brackets.

The register is the owner-approved cc-notes doc labeled `standing-rules:<program>`.
Use the newest doc. It has at most 35 rules and links to full answers by id.
The release-v3 register, doc `0cf17c9`, is about 8 KB.
Its body is mirrored as the plan file's last section.

Orca Claude workers carrying `CLAUDE_LONG_RUNNING_DRIVE` receive the register once
at `SessionStart`. The body arrives whole and verbatim up to 9,000 characters.
Above that limit, the hook names it with `ccn doc show <id7>`.
Read it in full before acting. Codex workers have no hooks, so their briefs paste
the register body verbatim. A brief carries the register, never full-answer quotes.

At key moments, `ruling_judge.py` searches the two nearest durable answers among this
drive's: those labelled `program:<slug>` and those anchored to the current branch.
It excludes answers the register already cites and answers already injected into the lane.
With no candidate left, it makes no model call.
Otherwise the drive's judge asks of each whether the action makes the decision that answer
settled without carrying all of it. The hook injects the first answer it flags, verbatim,
with the judge's sentence, once per lane per answer.

This covers inbox decisions, pull requests, plan edits, and lane spawns
in Claude drive sessions. Slack writes are out: each one already carries the owner's approval. The full durable-answer corpus is for retrieval only.
The judge is advisory, never blocks, and fails open.

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

The launcher generates this pointer spec from the lane, brief path, and worktree
path:

```text
Lane <lane>: read <brief> in full first and execute it exactly; it outranks Orca's preamble and any leave-uncommitted default, so commit, push, open PRs and post as it says. Worktree <wt>, bypass-permissions mode; its Escalate rules hold.
```

Orca's preamble forbids posting to GitHub, and codex's global instructions leave
delegated edits uncommitted. The pointer makes the brief outrank both, so a lane
whose brief says to ship opens its PR without asking.

A brief whose `ccx:` header line says `role=desk` or `role=watch` is a standing
desk, and the launcher appends the desk contract to its pointer:

```text
 Standing desk: loop until rotation; setup and quiet cycles are not done. Send worker_done only at rotation, naming the handoff doc.
```

Every other role keeps the one-shot pointer. Orca's preamble tells a worker to send
`worker_done` once its task is done and then go idle, so a desk launched without
the contract read setup or one quiet cycle as done. The role in the brief is the only
switch; the launch line carries none.

*Prevents landing-sweep-36, iac-drift-census-27, and incident-slack-watch-49 each
sending `worker_done` after setup or one quiet cycle (release-v3, 2026-10-08).*

When the pointer would pass 500 characters, the script links the brief at
`~/.claude/<8 hex>` and points there instead. A long lane or worktree path never
fails the launch, since Orca truncates a pasted spec only near 3 KB.

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
Keep cci text under 400 characters; attach longer bodies with --path and link durable cc-notes records with --ccn.

Authority: everything inside your Ownership below, without asking. Anything that
changes the plan, touches production (<apply, deploy, Slack write, state move>) or
is listed under Escalate stops for the coordinator: use the preamble's
`orca orchestration ask` with the question and 2-4 options. If it returns
"capacity reached", use the preamble's `send --type question` or `--type escalation`
instead. Keep working on everything that does not depend on the answer; the
runner escalates those messages to the root exactly like an ask.
Never end a turn waiting and never park. AskUserQuestion is unavailable. A decision
the brief, the plan, and the owner's answers do not settle stops that item until the
coordinator answers the ask above; never take a default or a provisional pick (owner
answers 01736af and 752564a).

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

Fix-lane milestones: send `mechanism: <evidence>` as a status Run message when
known. The brief names the exact live-check command and expected live output;
the root uses that command too. Run it every two minutes and post its output as a
status Run message until it flips, then send status subject
`fix-live: <h:mm PM PT> <evidence>`. Carry the preamble's Run addressing.

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
- Before every push run `remote-vet.py --config <runner config> --lane <lane>` from your
  worktree: it formats the Go and Rust you changed on a build machine, applies the
  formatting back into your worktree, then builds and vets only the packages and crates
  you touched; never a local `go build`, `go vet`, `cargo check`, `gofmt` or `cargo fmt`.
  Exit 3 means every build machine is busy: push and let CI grade.
- Commit and push every coherent checkpoint. Open a non-draft PR with a body for
  finished changes in the same turn. Drive it through landing under D1.
- When a PR's title no longer says what its diff does, rewrite it yourself with the
  ship command's `--pr-title`, without asking.
- When your stack depends on another lane's unlanded branch, stack on top of it and
  say so in the PR body; never copy their diff.
- Shape the stack so it lands beside every other stack under D19: put each shared-file
  edit in the smallest additive first PR, in the file's declared order. When a file you
  change is in another open PR, stack on that PR instead of racing it. Never commit a
  generated output; the build generates it. A stack that still carries a regenerated
  artifact rebases and regenerates it on ejection.

Landing desk (PR rows in cc-notes, lane messages in cci, shared by every checkout):
- Ledger `<id>`; script `ledger.py`, on PATH by name; repo
  `<owner/name>`; base `<base>`.
- Holds are root cci `hold` records; `ledger.py held --drive <drive>` prints the held PRs.
- You are a separate session. Register your prefix and report verdicts yourself;
  in a Claude session `ccx vcs ship` records each PR you open in the ledger and posts its
  `opened` record on cci, so never post `opened` yourself; a Codex session runs the
  `drive.py record --opened` line under `## PR record` right after each ship instead.
  Never SendMessage a subagent.
- On spawn: `ledger.py register --ledger <id> --lane <lane> --repo <owner/name> --branch-prefix
  <prefix>/<lane>/`.
- On every PR open, push, and READY: `ledger.py report --ledger <id> --drive <drive> --repo <owner/name> --pr <n> --head
  <full sha> --lane <lane> --verdict <clean|red|conflicting|held> --text "<one line>"`.
  READY is `--verdict clean --text "READY ..."`; the desk reads only this cci record.
- Never enqueue, merge, or add a `merge` or `merge-fast` label: the landing desk is the
  only enqueuer, and the pack's `lane_landing` hook refuses `stack-enqueue`, `gt merge`,
  `gh pr merge`, and `ledger.py label` from a lane. Report READY the moment a PR is green;
  the landing runner enqueues the largest green, approved, unheld bottom prefix against the
  open cci `hold` records. On a held PR, report `held` on your tip with the held
  PR named and leave release to the root. After the prefix lands, inspect whether GitHub already rebased the remaining stack onto the base branch before restacking with
  `ccx vcs stack submit`.
- After opening a PR, run
  `ccx vcs pr watch --lane-prefix <branch-prefix> --until landed` under Monitor
  (re-arm on expiry) or in a foreground loop instead of ad-hoc polling. `ejected` or
  `conflicting` means rebase now: `ccx vcs stack submit` from your lane's worktree,
  then report the new head; the landing runner re-enqueues it.
  `red <check>` means fix it. Your watch ends only when your PRs land.
- GitHub budget: read GitHub only through ccx, with `ccx vcs pr status`, `vcs pr state`,
  `vcs pr watch`, or `ccx vcs gh -- <gh args>` for any other read. Writes stay plain
  `gh`. No loop reads faster than every five minutes, and every `ccx vcs gh` read in a loop
  passes `--max-age 5m`. A `ccx: GitHub rate limit floor:` refusal means stop reading
  until the reset it names. The desk runner posts an `urgent` record to root when any
  quota drops under 1500.
- Before designing, name what already grades or handles this signal: monitors, the
  release's Watch, open owner tasks and picks (`ccn task list`, `ccn answer search`).
  If one covers it, route through it or delete the duplicate. Building alongside it
  is a return-early-with-options case.
- Findings, decisions, and handoffs go to cc-notes (`ccn note add`, `ccn log append`,
  `ccn papercut`), never only into your report.
- Fix or accept each reviewer-bot finding with the risk
  named. Post a `head` record for every push, addressed
  `--to desk-runner,fidelity-pipeline,finder-desk,landing-desk` and never to root, as are your
  `state` records. A restack that moves several PRs posts one record carrying every head,
  `cci post --kind head --head <pr>=<was>..<sha> --head <pr>=<was>..<sha> ...`, with a
  note after the sha such as `patch-identical` when the range-diff says so, never one record per PR: the desk runner's finder reads that head and posts its `finder:` record to you; fix or rebut each major before you call
  a PR done. On a drive whose runner has no finder, run one finder pass yourself.

Escalate early, never improvise: scope surprise, an assumption the code refutes, an
auth or approval gate, or two failed approaches. Ask with 2-4 options.

Finish: a lane with a PR finishes only once its squash `(#N)` is on the base branch.
It posts one PR comment opening with `## Rulings` and one opening with `## Regression
tests`, which lists each test id and links one CI run where that test failed against
the old tree, since nobody edits a PR body after it opens. The body's `## Risk profile`
states Load and Data in prose. A change that deletes a code path carries its caller
trace before the root GOs it: every caller of the removed path, each one read, and
what the path deletes or changes for each.
Drive to a terminal state, post `cci post --kind done --to root` with the outcome, since
the lane supervisor relaunches a lane that stops without one, then send the preamble's `worker_done` with
`--outcome succeeded|failed`, `--task-id <taskId>`, and `--dispatch-id <dispatchId>`
from that preamble. The three-sentence body names what changed, what was
found, and what is left; the PR numbers, full head shas, and any tool refusal, verbatim,
go in the report file its `--report-path` names. `worker_done` is your last act: the
root's gc then closes your idle terminal and removes your worktree once it is clean
and pushed, so push everything and leave no background process running first.
A standing desk, `role=desk` or `role=watch`, never reaches a terminal state: it
loops until rotation, through setup and quiet cycles alike, and never ends a turn
waiting. At rotation, on the supervisor's `ROTATE` record, it writes its handoff doc, posts
`cci post --kind handoff --ccn <doc id> --re <its seq>`, then sends `worker_done --outcome succeeded`
with the doc id in the body; the supervisor launches its successor from that doc.

Coordinator ids: <run id>; <cc-notes ruling and answer ids>.
```

## `<lane>.md`: one lane's part

```
## Lane: <name> (<agent> <model> <effort>). <the mission in one line>.
ccx: role=<role; desk or watch for a standing desk> [cpu=high] [place=remote|local] tooling-lane=<key, for a tooling lane only>
Target: <files, component, or environment in scope>.
Change: <the outcome to produce, such as "one failing database never blocks a release", never a design>.
Constraints: <invariants, compatibility rules, do-not-touch boundaries>.
Ownership: <what this lane edits; every shared file and who edits it after it>.
Compute: <for a pass, backfill, dry run, or bulk rewrite: the in-region box recipe, region, and the bucket or team each process owns; never this Mac (SKILL.md R15). "none" otherwise>.
Verified facts, do not re-derive: <ids, shas, URLs, state already confirmed>.
Design rulings, verbatim: <relevant register rules, their linked answer ids, and the entry point (symbol at file:line) each requires; or "none">. A contract quotes the owner's words for each pick. A narrower reading the build needs, such as per key for per Mac, is an ask to root, never a choice. Its failing-first test asserts the owner's invariant, such as process count flat in chat count, not the narrowed case.
Design check: before READY-FOR-SHIP, post `DESIGN-CHECK <symbol at file:line>; <each ruling, met how>; leaves out: <none, or each piece>` as a `contract` record on the bus and keep building; never wait for a confirm. The coordinator withdraws it only on a mismatch; a change that leaves out part of a ruling holds.
Design questions: settle each from this spec, the register, and the base branch yourself and post the choice as a `contract` record. Ask the coordinator only when the choice changes owner-visible behaviour, and keep building the rest meanwhile.
Stacking: open your PR as soon as its parent's PR exists, never once it lands; restack with `ccx vcs stack submit` after the parent lands.
Standing rules register: <id of the newest `standing-rules:<slug>` doc, or "none">.
  Carry the owner-approved register's at most 35 rules, never full-answer quotes.
  Claude workers carrying CLAUDE_LONG_RUNNING_DRIVE receive it once at SessionStart.
  Above 9,000 characters, read it with `ccn doc show <id7>` before acting.
  For Codex, paste the body from `ccn doc show <register id>` here verbatim.
  Codex workers have no hooks. Claude drive sessions receive a relevant full answer
  only when the key-moment judge selects it, once per lane per answer.
Every rule binds this lane. Your READY names the rulings your diff touches.
Mechanisms the plan bans: <the drive's list, each shape with its rule id, or "none">. Before each push, run `rulings.py sweep --diff <file>` over your own diff, and fix each `MECHANISM-BANNED` line it prints or cite the owner's answer naming that shape.
Scale: <the N each path this lane changes runs at: chats, teams, sessions, or runs on one Mac or one pod; or "no N">. The bar: an interactive step answers within half a second at that N, and processes, timers, and connections per host stay flat as N grows, proved by a test at N = 1 and N = 1,000. A loop that awaits I/O per item, or a cap, timer, cache, retry, or idle wait standing in for a fundamental fix, is a defect the finder grades major; a diff that adds one names in the PR body the fix it stands in for and the owner pick that deferred it, or builds the fix. The runner refuses a non-incident launch whose brief has no `Scale:` line.
Standing rules served: <`#<seq>` ids with their answer ids, or "none">; your task cites them, and completing this lane never retires them.
Plan task: register your unit at spawn so the progress record's plan counts include it: `ccn task add "<unit>" --plan <plan id> --label step:<n> --label program:<slug> --criterion "<done when>"`, then `ccn task start`, `ccn task criterion met|failed`, and `ccn task done` as it moves (SKILL.md Plan progress). The coordinator fills the plan id and step, or writes "none".
Observable acceptance: <the test, output, or PR URL that proves completion>.
```

Orca's task-spec contract requires every spec to include `Target`, `Change`,
`Constraints`, `Ownership`, and `Observable acceptance`. The shared file supplies the
long-running fields `Authority`, `Escalate`, `Do NOT touch`, `Worktree`, and `Finish`.

The `ccx:` line also places the lane. `place=remote` sends it to a Sprite and
`place=local` keeps it on the Mac; with neither, `cpu=high` sends it to a Sprite and
any other lane stays local (SKILL.md O16). A lane placed remote gets a brief it can
run from the Sprite's checkout alone, with no Mac path or Mac-only credential. A
Sprite has no `cci`, so a remote lane's brief never tells it to read or post the drive's
bus: it reports through native Orca status, `ask`, and `worker_done` on its Dispatch. A
standing desk launches locally whatever the line says.

## A standing desk's brief

A desk on Orca, its first launch or a successor after rotation, uses this brief. The
`ccx: role=desk` line, or `role=watch` for a watch, is what gives it the standing
desk contract; a brief without it launches one-shot.

```
# <lane>-<N>: <desk's job>, successor of <lane>-<N-1>
ccx: role=desk
Read cc-notes doc <handoff or desk doc id> (ccn doc show <id>) and continue exactly as it says.
<live standing rules by #<seq> id; anything this successor changes>.
Report on cci as <lane>-<N>. Loop until rotation at <hours>h; setup and quiet cycles are not done.
Rotate: write the handoff doc, then send worker_done naming its id.
```

## Checks before launch

- A lane that touches a subsystem the owner has ruled on quotes the relevant register
  rules verbatim. It cites their answer ids and names the entry point as a symbol at file:line.
- Each lane's Ownership excludes every other lane's files. If two lanes edit a shared
  file, the second is a stacked child of the first.
- A lane that runs a pass, backfill, dry run, or bulk rewrite names in-region compute
  on its `Compute:` line. A brief that runs one on the Mac is rewritten before launch.
- Every command in `common.md` runs as written from a lane's worktree.
- The `<lane>.full.md` attachment contains the shared contract followed by the lane
  section. Resolve its path with `ccn attachment path` before launch.
- The generated pointer spec names a brief path of at most 8 hex characters under
  `~/.claude` whenever the full brief path would push it past 500 characters.
