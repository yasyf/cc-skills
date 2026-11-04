# The Orca lane brief

## Contents

- [Lane brief mechanics and placement](#lane-brief-mechanics-and-placement)
- [Subagent types and Orca sessions](#subagent-types-and-orca-sessions)
- [The standing rules register and judge](#the-standing-rules-register-and-judge)
- [Why the spec is a pointer](#why-the-spec-is-a-pointer)
- [In-process subagent brief template](#in-process-subagent-brief-template)
- [The `common.md` contract every lane shares](#commonmd-the-contract-every-lane-shares)
- [One lane's `<lane>.md` part](#lanemd-one-lanes-part)
- [A design lane's part](#a-design-lanes-part)
- [A standing desk's brief](#a-standing-desks-brief)
- [Scoped resume](#scoped-resume)
- [Sequencer lane](#sequencer-lane)
- [Checks before launch](#checks-before-launch)

## Lane brief mechanics and placement

A brief states the outcome, such as "one failing database never blocks a release," and never a design, such as "report it in the Switch; reuse the handoffz and census readers." Step 0 of `Do:` is the lane's own search for an existing grader. Every editing lane gets one isolated checkout; two agents in one checkout race `HEAD`, the index, and untracked files, and a restack under a running ship lands its staged diff on whatever branch is checked out at commit time.

The `ccx:` line names the lane's role and placement for the hooks. A `fix` role with incident wording routes to `orca-desk`, while `helper`, `reader`, `watch`, `export`, `evidence`, `handoff`, `comms`, and `triage` lanes need no coordinator task. On Orca, `desk` and `watch` make a standing desk that loops until rotation and sends `worker_done` only then, naming the handoff doc. A standing desk always launches locally regardless of `place`.

With `ORCA_LAUNCH_SPARSE=1`, in a repo whose HEAD tracks more than 5,000 files, `lr launch start` gives every lane a sparse checkout of the coordinator's files, `.agents`, `.claude`, `tools`, `infra/ci/hooks/lib`, `release`, `infra/envs`, and each directory the `ccx:` line names as `paths=<dir>,<dir>`. A smaller repo, such as cc-skills, keeps full lanes, and so does every launch without `ORCA_LAUNCH_SPARSE=1`. Name the directories the lane edits or reads, such as `paths=go/ci,infra/ci`; a Go lane names its own module, since the monorepo's Go code is 28 separate modules under `go/`. The lane widens with `git sparse-checkout add <dir>` before it reads outside that cone.

The flag `cpu=high` marks a sweep or heavy research lane. Under O16 in `reference/orca-desk-brief.md`, `place=remote` sends a lane to a Sprite and `place=local` keeps it on the Mac. With neither flag set, every ordinary lane goes to a Sprite on the orca desk, fail with `LAUNCH-FAILED` without `orca.sprite`, and never start locally when a Sprite launch fails.

For implementation lanes, prefer the repository's canonical remote entrypoint when it provides one. In a repository with an `.orca.json`, follow the `orca` plugin's `orca` skill, which defaults to Sprite prepare+attach and the already-owned native Run with its sole inbox consumer. The orca desk calls it for ordinary lanes by default under O16 in `reference/orca-desk-brief.md` and runs local lanes through `lr launch start`. Preserve explicit model, effort, and Codex service tier.

Canonical remote workers use home Dispatch messages and the repository skill's source-return workflow. Namespace runs on regular Compute through an explicit config and stays opt-in, so the desk never launches there. Native recipe-picker enablement is a separate surface.

A Sprite holds the brief, a copy of each file under the Mac home and each `ccn doc show <id>`, `ccn plan show <id>` or `ccn task show <id>` the brief names under `/home/sprite/drive/carried` with the brief's references rewritten to match, and a shallow checkout of the ref its config names (`dev` in `Forge-AI/monorepo`) with the repo's `refs/cc-notes/*` fetched into it at launch, so its `ccn` reads every answer, note and doc pushed to the repo. A launch whose fetch fails starts no worker and names `notes.log`. It has no other Mac path, local worktree, unpushed commit, Keychain, Mac-only credential, or `cci`. Write `place=remote` when the lane can work inside those limits with every fact in the brief by value and everything it builds on already pushed. A remote lane's brief never tells it to read or post the drive's `cci` bus; it reports through native Orca status, `ask`, and `worker_done` on its Dispatch. A Sprite lane commits, pushes its branch, and opens its PR from the VM like any lane, and the coordinator pulls the branch when it needs the source locally. A remote worker keeps its runtime and workspace after completion.

Write `place=local why=<word>` when the lane reads a worktree or branch only the Mac has, needs a Mac-only credential, or drives something on the Mac itself; it then starts on the Mac at any load. `place=local` with no `why=` goes to a Sprite, except `role=read`, whose answers need the Mac's whole record history and stores: a Sprite's cci store starts at its launch. `role=read` and `why=predecessor-worked-here` only prefer the Mac: while its one-minute load average is above `orca.load_limit`, which defaults to its core count, they start on a Sprite. A file or ccn entity the brief names is no reason for it, since the launch carries it to the box. A local lane the coordinator has decided is done ends under R195's `--done` route. Dedicated remote API keys belong only to the worker process; local Fable keeps existing Mac interactive authentication.

A lane that needs a devbox or an EC2 Mac box is `place=local why=devbox`, since a Sprite holds no AWS identity and can neither claim nor reach one. Its brief names the command: `AWS_PROFILE="$DEVBOX_AWS_PROFILE" tools/ci devbox claim --gh`, and the same prefix on `tools/ci mac-box`. The Mac's local config sets `DEVBOX_AWS_PROFILE` to a profile that needs no sign-in, and `--gh` settles the clone credential. No lane asks the owner to sign in or to pick a clone credential; a refusal from that command is a blocker to the coordinator, carrying its error. Any Namespace devbox, Linux or Mac, is fine for a lane or a test box. Only Namespace's plain Macs are off limits, meaning a macOS machine type from `nsc create` that is not a devbox: they count against CI's limit of eight VMs, and every long-lived one makes CI jobs wait. A Mac that is not a Namespace devbox is an EC2 Mac box through the monorepo's `ci mac-box` and its `test-mac` skill.

A local lane that sends a build to a shared Sprite runs it with `sprite-cargo.sh <sprite> <lane> <repo> <ref> <command> <path>...`, never a hand-rolled `sprite exec`. Each lane builds into its own `target-<lane>` so two lanes never clobber each other's build-script output, and every call removes targets and source trees idle for more than six hours.

A lane checks its change before pushing with `go build` and `go vet` over the Go packages it touched and the workspace typecheck over the TypeScript it touched, run locally or, on a Mac, through `remote-vet.py --config <runner config> --lane <lane> [<worktree>]`; never a local `cargo check`, `gofmt`, or `cargo fmt`. The `vet_before_push` hook refuses a lane's push when no such run follows the last edit, and names the commands. `remote-vet.py` ships everything the worktree changed since its merge base with the branch below it in its stack, including uncommitted and untracked files. The script sends those changes to the lane's own worktree on the least loaded Sprite in the config's `vet.sprite`, which lists Sprites where no finder or fidelity pass runs.

`remote-vet.py` runs the config's `vet.prepare` code generation, formats changed Go files and touched crates with `gofmt -w` and `cargo fmt`, and builds and vets only the touched Go packages and Rust crates. It then applies the formatting back as a patch to the lane's worktree for the lane to commit before pushing, and exits `1` on any `FAIL`. When every vet Sprite is over load 16, `remote-vet.py` exits `3` at once, and the lane pushes and lets CI grade the change.

## Subagent types and Orca sessions

For in-process subagents, use one of this plugin's two lane types with the routing table's `model`. Use `long-running:lane` for standing subagents, desk shards, sequencers, and pollers. It omits `ToolSearch`, every `mcp__*` tool except `cc-slack`'s, `mcp__sentry__*`, and the Datadog read tools, the deferred-tool list, the `Skill` tool, and the skill listing. Use `long-running:lane-ship` for implementation lanes that ship a PR or call a skill such as `submit-pr`, `open-pr`, or `codex`. It omits `ToolSearch`, every `mcp__*` tool except `cc-slack`'s, `mcp__sentry__*`, and the Datadog read tools, and the deferred-tool list. An incident lane of either type reads Sentry and Datadog itself. Use `general-purpose` for any lane that posts to Slack so it carries every Slack surface the session has under R20 in `SKILL.md`.

Both `long-running:lane` and `long-running:lane-ship` default to Opus and carry the 1h prompt cache a nine-minute poll needs; set a different model explicitly when the assignment calls for it. The coordinator runs Fable 5.1 on the Mac. Beyond it, Fable is only for exceptional cases such as the most sensitive local implementation, and ordinary workers and subdesks never run Fable. If a `long-running:lane` needs a skill, give that operation to a separately named `long-running:lane-ship` with a scoped brief while keeping the original lane running without duplicating its active work.

A lane using the separate desktop Orca adapter (`orca orchestration worker-start`) runs in a separate session that the Agent tool cannot reach with `SendMessage`. It reports through Orca instead of `SendMessage`, and the landing desk hears from it through `ledger.py`, never a message. Launch it with `lr launch lane <lane> <model> <effort> <brief>`, which invokes `lr launch start` using `reference/orca-workers.md`. A codex Orca lane launches on Orca's codex agent under O15 in `reference/orca-desk-brief.md`, never as a Claude worker calling the codex skill; use the codex skill for inline lanes and `codex-ask` for one-off questions. That adapter's launch, relay, and receipt rules apply only to its own lanes.

## The standing rules register and judge

Standing rules register mechanics, limits, `SessionStart` injection, and key-moment `ruling_judge.py` behavior are in `reference/owner-rulings.md`.

## Why the spec is a pointer

`worker-start --spec` pastes its text into the worker's terminal and truncates it near 3 KB. The full text stays in `dispatch-show --task`, but the worker sees only the pasted text.

Store `common.md` and each lane's brief as attachments on the drive's `briefs: <slug>` log. Attach each complete brief as `<lane>.full.md`, containing the shared contract followed by the lane's brief. Resolve the attachment path when launching with `lr launch start`:

```sh
lr launch start <lane> <model> <effort> \
  "$(ccn -R <drive checkout> attachment path <briefs log> <lane>.full.md)"
```

`lr launch start` generates this pointer spec from the lane, brief path, and worktree path so the brief outranks Orca's preamble and any leave-uncommitted default. A sparse lane's pointer ends with the widen sentence below, and a lane in a repo with `tools/hooks/warm-lane.sh` also carries the shared `node_modules` line:

```text
Lane <lane>: read <brief> in full first and execute it exactly; it outranks Orca's preamble and any leave-uncommitted default, so commit, push, open PRs and post as it says. Worktree <wt>, bypass-permissions mode; its Escalate rules hold. This lane is sparse: widen with `git sparse-checkout add <dir>` before reading outside it.
```

When a brief's `ccx:` header line says `role=desk` or `role=watch`, `lr launch start` and `lr launch worker` append the standing desk contract to the pointer:

```text
 Standing desk: loop until rotation; setup and quiet cycles are not done. Send worker_done only at rotation, naming the handoff doc.
```

Every other role keeps the one-shot pointer. The role in the brief is the only switch, and the launch line carries none. When the pointer would pass 500 characters, `lr launch start` symlinks the brief at `~/.claude/<8 hex>` and points there instead. The Orca preamble above the task carries the worker's handle, dispatch capability, and exact `send`, `ask`, and `check` commands; the brief tells the worker to copy those commands verbatim without restating them.

When the `ccx:` line says `pr=none`, as it does for work on a design the owner has not approved, `lr launch start` and `lr launch worker` append the no-PR line, which the monorepo's Stop hooks read so they never order a ship for that lane's branch:

```text
 This brief orders no PR: push the work as a branch and never open a PR for it.
```

## In-process subagent brief template

Paste and fill this template for an Agent-tool subagent:

```
ccx: role=<role> [cpu=high] [place=remote|local] [paths=<dir>,<dir>] [pr=none] [monitor=<Datadog monitor id>] tooling-lane=<key, for a tooling lane only>
Standing: <the duty the drive keeps for good, such as "devbox perf and bug hunting"; only
  on a lane whose work never ends. The supervisor raises a blocker when a standing duty
  has no live owner for ten minutes, so a dead or stood-down standing lane is never
  silent. Omit the line on a one-shot lane.>
Authority: <what you do without asking; what stops for the owner>.
Verified facts, do not re-derive: <ids, shas, URLs, state already confirmed>.
Design rulings, verbatim: <each owner ruling on the subsystem this lane touches, quoted
  with its id and its confirmed implications (R19b), and the entry point (symbol at
  file:line) it makes the change call; or "none">. Before READY, state which entry point your diff calls, each ruling it meets,
  and anything it leaves out, as a `contract` record on the bus (R22). Never wait for
  a confirm. A contract quotes the owner's words for each pick. A narrower reading the
  build needs, such as per key for per Mac, is an ask to the coordinator, never a choice. Its
  failing-first test asserts the owner's invariant, such as process count flat in chat
  count, not the narrowed case.
Design questions: settle each from this brief, the standing rules register, and the
  base branch yourself, and post the choice as a `contract` record with its reason.
  Ask the coordinator (`cci post --kind ask --to root`) only when the choice changes
  owner-visible behaviour, and keep building the rest while the ask is open. With
  nothing left to build, the open `ask` is what lets the turn end: its answer wakes you.
  Every record addressed to `root` costs the coordinator a read for the rest of its context, so
  address it only with what it must rule on or asked for. A `state`, `head` or `note`, or
  an answer to another lane, names only the lanes that act on it.
Do:
  0. Before designing, name what already grades or handles this signal: monitors,
     the release's Watch, open owner tasks and picks (`ccn task list`, and the
     `ccn answer search` and `ccn relevant` hits under the slice's step one). If one covers it, route through it or delete the
     duplicate. Building alongside it is a return-early-with-options case.
     Run those searches again yourself before any ask, decide with options, or PR;
     the `answer_search_first` hook warns a lane that does one with no search first.
  0a. For a bug fix, before any code: (a) name which tree ran the failing path, old
     tree or next, from the run's own record (its build, binary version or pod image),
     never from the report's wording, and fix that tree; (b) read the fix against
     the register's rows on the subsystem it touches and quote the ones it meets,
     such as the local-sync rows (rule/394dfd6, rule/b0fda44: a local action never
     waits on a remote upload). A fix built against the wrong tree or against a row
     is closed, not shipped (Nov 2, #48651).
  1. <step>
  2. <step>
Compute: <for a pass, backfill, dry run, or bulk rewrite: the in-region box recipe,
  region, and the bucket or team each process owns; never this Mac. "none" otherwise>.
  A box a lane tests on is an EC2 box: `ci devbox`, or `ci mac-box` for a Mac. Namespace belongs to CI, never to a lane's testing. A Namespace box a lane still holds
  is extended with `lr nsc extend <id> --drive <drive> --lane <name> -- --duration <d>`, never bare `nsc`, so its deadline is watched.
  Any Namespace devbox, Linux or Mac, is fine for a lane or a test box. Only Namespace's plain Macs are off limits, meaning a macOS machine type from `nsc create` that is not a devbox: they count against CI's limit of eight VMs, and every long-lived one makes CI jobs wait. A Mac that is not a Namespace devbox is an EC2 Mac box through the monorepo's `ci mac-box` and its `test-mac` skill.
Escalate early, do not improvise: scope surprise, an assumption the code refutes,
  an auth or approval gate, or two failed approaches. Return findings + 2-4 options.
AskUserQuestion is unavailable. A decision this brief, the plan, and the owner's
  answers do not settle stops that item: `cci post --kind ask --to root` with 2-4
  options, keep building what does not depend on it, and wait for the coordinator's answer.
  Never take a default or a provisional pick (owner answers 01736af and 752564a).
Do NOT touch: <files, branches, worktrees another lane owns>.
Plan task: register your unit at spawn so the progress record's plan counts include it:
  `ccn task add "<unit>" --plan <plan id> --label step:<n> --label program:<slug> --criterion "<done when>"`,
  then `ccn task start`, `ccn task criterion met|failed`, and `ccn task done` as it moves
  ([Plan progress](#plan-progress)). The coordinator fills the plan id and step, or writes "none".
Worktree: <absolute path, exclusive to this lane>. If the launch made it sparse, widen with
  `git sparse-checkout add <dir>` before reading outside it.
  In a repo with tools/hooks/warm-lane.sh, node_modules is a shared symlink farm; never run yarn install, ask warm-lane to rebuild for a changed lockfile.
Mailbox: `mailbox:<lane>=<cursor file>` in every lr wait call, <lane> being your own
  name; lr wait resolves it from the live team roster. Claude Code delivers your mail
  only after your turn ends; on `MAILBOX`, act on the text lr wait prints with it.
  Run lr wait as the Bash call itself and never filter or discard its output.
Keep record text to one line; `cci post` spills text over 400 characters to a file, posts its first line, and prints the path. Attach a body you wrote with `--path` or `--ccn`. Orient with `cci digest --drive <drive>` when starting a new lane.
Address `head` and `state` records `--to desk-runner,fidelity-pipeline,finder-desk,landing-desk`, never to the coordinator;
  the coordinator hears asks, decides, blockers, defects, incidents, corrections and withdraws.
Address every lane by its stem, the name without a trailing `-<n>`, such as `--to board-refresher`; cci delivers it to the
  stem's newest live lane, and a stood-down lane's mail to its successor.
A choice that is the owner's goes to him as one record: `cci post --kind ask --to board-refresher "CARD: card-<slug>: <question>" --path <card.json>`, options recommended first, with a `From:` line: the plan step and date, his ruling id, or whose PR it ports. Prose naming his pick is flagged as an untyped choice.
Standing rules register: <id of the newest `standing-rules:<slug>` doc, or "none">.
  Claude lanes receive the register once at start through hooks.
  Above 9,000 characters, read it with `ccn doc show <id7>` before acting.
  For Codex, paste the body from `ccn doc show <register id>` here verbatim.
Every rule binds this lane. Your READY names the rulings your diff touches.
Mechanisms the plan bans: <every implementation brief pastes the drive's list, each
  shape with its rule id: timers and polls, credentials in files, local exec, and any
  the drive adds; or "none">. Before each push, run `rulings.py sweep --diff <file>` over
  your own diff, and fix each `MECHANISM-BANNED` line it prints or cite the owner's
  answer naming that shape. A coordinator answer that names a mechanism names its banned
  shapes too, so "re-request before expiry" never reads as a timer.
Scale: <the N each path this lane changes runs at: chats, teams, sessions, or runs on
  one Mac or one pod, such as "1,000 chats on one Mac"; or "no N">. The bar: an
  interactive step answers within half a second at that N, and processes, timers, and
  connections per host stay flat as N grows, proved by a test at N = 1 and N = 1,000.
  A loop that awaits I/O per item, or a cap, timer, cache, retry, or idle wait standing
  in for a fundamental fix, is a defect the finder grades major; a diff that adds one
  names in the PR body the fix it stands in for and the owner pick that deferred it, or
  builds the fix. The spawn hook and the relay warn on a new non-incident
  lane brief with no `Scale:` line, and `lr launch` refuses it before posting, naming
  every missing line together; a `<lane>-handoff` writer, a
  successor (`<lane>-<n>` or a `Replaces:` line), and a brief written before the rule pass
  quietly. Both also warn when a judge reads a brief's `Scale:` line as missing the N its
  paths run at.
Standing: <only for a duty the drive keeps for good, such as a desk, a sweep, or bug
  hunting: what it keeps doing>. The lane supervisor reads it; once the newest lane on
  the duty has had no live owner for 10 minutes, the coordinator hears one blocker naming the lane
  and this brief. Stand such a lane down only with a successor; a hook warns on a
  stand-down that names none. Deleting the line ends
  the duty.
Standing rules served: <`#<seq>` ids with their answer ids, each with the owner's
  words and confirmed implications (R19b), or "none">. Your task cites
  them; finishing it never retires them, and no report calls them done.
Stack shape, under D19: a deliverable is one feature-sized PR. Fixes, follow-ups and
  review responses are commits on it through `ccx vcs ship`, with `--amend` to fold into
  its commit or `--tip-only` when only the tip changed, never a stacked child. Stack only
  when a parent must land before other lanes can proceed, or when review needs the
  split. Put each shared-file edit in the smallest additive first PR of
  your stack, in the file's declared order. When a file you change is in another open
  PR, stack on that PR instead of racing it. When you stack, open your PR as soon as its
  parent's PR exists, never once it lands: ship on the parent branch, then restack after the parent
  lands with `lr landing restack --runner <runner config> --lane <lane> <top PR>`, which
  replays the stack on a Sprite, pushes, and posts each new head. No rebase runs here.
  Resolve by hand only a conflict it reports, on the branch and commit it names. Never commit a generated output; the build
  generates it. A stack that still carries a regenerated artifact rebases and
  regenerates it on ejection.
Holds file: <path>, coordinator-owned. Never enqueue, merge, or add a `merge` or `merge-fast`
  label: the landing desk is the only enqueuer, and the pack's `lane_landing` hook warns on
  `stack-enqueue`, `gt merge`, `gh pr merge`, and `ledger.py label` from this lane.
  Report READY the moment a PR is green; the landing pass enqueues its largest green,
  approved, unheld bottom prefix against the open cci `hold` records. A held PR
  gets `held` on your tip with the held PR named; release is the coordinator's.
  Keep `ccx vcs pr watch` on the stack; on ejection or conflict, restack at once with
  `lr landing restack`. After the prefix lands, restack the PRs above it the same way.
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
  While running: wait in the foreground with `lr wait 540 cci:<drive>:<name>` at
    `timeout: 570000`, rerun on every return; never a `cci watch` Monitor.
  Use `cci post --drive <drive> --lane <name> --kind <kind> --topic <topic> --text "<text>"`:
    a `head` after every push, a `contract` for anything another lane consumes, a
    `decision` another lane could build on, a `blocker` or `ask` addressed `--to` the lane
    that acts. Answer with `--kind answer --re <seq>`; use `--kind withdraw --re <seq>`
    before you change or retract a record. Set the topic and recipients on replies.
Registration: the pack binds your thread to your lane before your first command runs, with
  one `<lane>: registered at its first command` state record; a codex lane's brief copy makes
  that post its first command.
Peers: put your `cci me` line in every handoff. `cci who --pr <n>` names the session that
  owns a PR and `cci who --drive <drive> --role root` the coordinator; reach either with `cci post --to <its lane>`.
Codex: call `Skill(codex)` or `codex:codex-wrapper`; a one-off question goes to `codex-ask`.
`# ccx:raw` at the end of a Bash command runs it past the hooks as written. Use it only
  where a hook misreads the command, and name that hook in your report.
Shell text: Claude Code holds any Bash command holding `$$(`, `$${` or `$$[` on a
  "Dangerous rm operation not ruled out" dialog, even inside a quoted heredoc. Write such a
  file with the Write tool and run it with a Bash command free of them.
Run subagents and codex in the foreground (blocking), or poll the reply file in a foreground loop to a terminal state; never background-and-end-turn.
Record each sub-dispatch with `ledger.py ask` before dispatch and `ledger.py answer` when its reply lands.
Verification: before each push, `go build` and `go vet` over the Go packages you touched and the typecheck over the TypeScript you touched; no local test runs or lint; CI grades; a red CI step is read from its log, fixed, and pushed.
PR comments: nobody edits a PR body after it opens, so the evidence goes in two comments
  the lane posts: one opening with `## Rulings`, and one opening with `## Regression tests`
  that lists each test id the change adds or edits and links one CI run where that test
  failed against the old tree. Nothing checks the link; the fidelity desk reads each named
  test against the failure it claims (784f1ab). The body's `## Risk profile` states Load
  and Data in prose.
Deletion: a change that deletes a code path, proposed or in a PR, carries its caller
  trace before the coordinator GOs it: every caller of the removed path, each one read, and what
  the path deletes or changes for each (cc-notes 83695d86 is the worked example). The
  fidelity desk grades what the deletion does to each caller, never the diff's shape.
Credentials: before reporting a credential out of reach, check the parameter store
  (SSM) and the secret stores this brief names, and report where it sits. Reading a value
  takes the owner's Touch ID tap on that exact read, on the Mac only:
  `VALUE=$(cc-sudo run -- /bin/launchctl asuser <uid> /usr/bin/sudo -H -u <user> <absolute read>)`.
  Keep the argv literal, pick an AWS profile with `--profile` since an `AWS_PROFILE=` prefix
  does not reach the read, and assign stdout so the value stays out of the transcript.
  capt-hook's `secret_stores` guard refuses any other form and names this one. While the owner
  is away from the Mac no prompt fires and the guard queues a card, so hold that step and keep
  going on the rest. A Sprite has no `cc-sudo`, so its reads stay refused.
GitHub budget: read GitHub only through ccx, with `ccx vcs pr status`, `vcs pr state`,
  `vcs pr watch`, or `ccx vcs gh -- <gh args>` for any other read. Writes stay plain `gh`.
  No loop reads faster than every five minutes, and every `ccx vcs gh` read in a loop passes
  `--max-age 5m`. A `ccx: GitHub rate limit floor:` refusal means stop reading until the
  reset it names. The supervisor posts an `urgent` record to the coordinator when any quota drops
  under 1500.
Report short deltas with pointers (file:line, PR number, sha, Slack ts, disk path); never
  paste a diff, log, PR body, or thread into a message. Write it to disk and send the path.
Finish: a lane with a PR finishes only once its squash `(#N)` is on the base branch.
  Drive to a terminal state, then post one cci `done` record to <orchestrator>,
  ≤10 lines: verdict | ids | what changed | what is next. That record is your last action.
  A turn ends only on a typed record, and `cci hook stop` refuses every other stop:
  `done`, `stopped`, or an open `ask`, `blocker` or `blocked`. To wait on an event, post
  `cci post --kind blocked` with `--pr N`, `--target T`, `--lane-ref L`, or a `card-` id in
  its text, and stop; that event wakes you, and a red PR you parked on wakes you. `done` retires you for good, so post it only when nothing is left: while you own an open PR it is refused, and you post `blocked --pr N` instead. Handing work to
  another lane is `blocked --lane-ref <it>`, not a sentence. Every push to a reported PR re-reports the new
  head with `ledger.py report --drive <drive>` in the same turn; the desk grades without waiting for it.
```

## `common.md` the contract every lane shares

```
# <drive>: lane brief (shared contract)

You are one worker in <drive>. Read <plan path> in full before anything else; <owner
plan or source of truth> is the source of truth behind it.
Keep cci text under 400 characters; attach longer bodies with --path and link durable cc-notes records with --ccn.

Authority: everything inside your Ownership below, without asking. Anything that
changes the plan, touches production (<apply, deploy, Slack write, state move>) or
is listed under Escalate stops for the coordinator: post
`cci post --kind ask --to root` with the question and 2-4 options. `cci post
--kind ask|report|blocker` is the only way to reach a lane or the coordinator; the
preamble's `orca orchestration send` and `ask` carry the dispatch lifecycle alone
(heartbeats, the runner-action acks below, `worker_done`). Keep working on everything
that does not depend on the answer.
End a turn on a wait only after posting it as a cci `ask` or `blocked` record. AskUserQuestion is unavailable. A decision
the brief, the plan, and the owner's answers do not settle stops that item until the
coordinator answers the ask above; never take a default or a provisional pick (owner
answers 01736af and 752564a).

A question the plan or a recorded owner ruling already answers is not a question:
apply the answer, cite it, and never put it in an ask or in an owner-question list.
An owner-question list you write marks, for every item, which authorities you checked
(plan section, durable answer id, memory) and holds only items none of them settles.

A question that reaches his board has its recommended answer built on a branch as
soon as the question is asked. Push the branch, open no PR for it until he answers,
and name it on the card's recommended option as `build`
(`drive.py ask-owner --branch OWNER/NAME:BRANCH`), or give `build.none` with the
reason a pick such as a process choice has nothing to build. The branch is something
he can try, not a provisional pick: nothing lands from it before his answer, and a
branch he rejects is deleted. `board_text_gate` refuses a card that names neither
an hour after its first draw.

A concrete rule inside his design order ships only with his pick, cited in the PR body
as `owner:m<N>` or a ccn answer id. So does a change to where data lives or how long
it is kept, such as a volume, claim, reclaim, retention or eviction field. Only
the incident fix and its headroom go first. `design_picks.py` refuses a PR without the
cite when it touches an open design card's paths or those fields under `infra/rows/`.

Never end a turn waiting, and never loop over `orca orchestration check`: Orca
  delivers its mail into your terminal, and `lr wait 540 cci:<drive>:<name> -- orca
  orchestration check --terminal "$ORCA_TERMINAL_HANDLE" --wait --timeout-ms 540000 --json`
  blocks on Orca's own long poll and on the bus in one foreground call.

Runner actions: every guidance relay names its key and thread id. Before acting,
send the preamble's status command with your own dispatch and that thread:
  orca orchestration send --type status --thread-id <thread> --dispatch-id <its dispatch> --subject "started <key>"
After acting, send:
  orca orchestration send --type status --thread-id <thread> --dispatch-id <its dispatch> --subject "done <key>: <result>"
Carry the preamble's addressing and capability arguments. A send proves delivery,
not that you started. The action moves from accepted to started, completed, and
verified; failed and unverifiable record failure or missing proof. Your done reply
records completion; verification needs an external receipt. Do not mark coordinator tasks
complete for runner actions.

A relaunch offers this lane's container to the new dispatch; its first ack takes
ownership at the next owner generation. Until then the old owner retains it.
After transfer, an ack from another dispatch gets a stand-down reply and cannot
move the original action. Obey that stand-down without ending or closing your
session. Never act on a replayed key you already completed.

Fix-lane milestones: send `mechanism: <evidence>` as a status Run message when
known. The brief names the exact live-check command and expected live output;
the coordinator uses that command too. Run it every two minutes and post its output as a
status Run message until it flips, then send status subject
`fix-live: <evidence>`. Carry the preamble's Run addressing.

Binding rules (owner):
- <one rule per bullet, each as the owner stated it>

Worktree and VCS:
- Your worktree is exclusive to you; never touch another lane's worktree, branch, or
  files outside your Ownership, and never write probes into another worktree.
- If the launch made this worktree sparse, widen with `git sparse-checkout add <dir>` before reading outside it.
- In a repo with tools/hooks/warm-lane.sh, node_modules is a shared symlink farm; never run yarn install, ask warm-lane to rebuild for a changed lockfile.
- Branch prefix: <prefix>/<lane>/. The launch created your worktree on a branch named
  for this launch, <prefix>/<lane>-<run>-<n>; rename it into that prefix before your
  first stack: `git branch -m <prefix>/<lane>-<run>-<n> <prefix>/<lane>/<topic>`.
  One stack per lane; ship with <the repo's ship
  command>. Load <the repo's submit skill> before opening a PR. <title and body format>.
  A PR without a body, or left as a draft, is forbidden.
- <the repo's forbidden commands, verbatim>
- Before every push run `go build` and `go vet` over the Go packages you touched and the
  typecheck over the TypeScript you touched. On a Mac, `remote-vet.py --config <runner config>
  --lane <lane>` from your worktree does the Go part on a build machine: it formats the Go and
  Rust you changed, applies the formatting back into your worktree, then builds and vets only
  the packages and crates you touched. Never a local `cargo check`, `gofmt` or `cargo fmt`, and
  tests run only in CI. Exit 3 means every build machine is busy: push and let CI grade.
- Commit every coherent checkpoint and push at checkpoints, never per edit, because each
  push supersedes the running build and reruns CI and both review bots. Still push within
  the hour, since origin is the backup. Open a non-draft PR with a body for
  finished changes in the same turn. Drive it through landing under D1.
- Fixes, follow-ups and review responses are commits on your open PR, never a stacked
  child. Stack only when a parent must land before other lanes can proceed, or when
  review needs the split.
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
- Holds are coordinator cci `hold` records; `ledger.py held --drive <drive>` prints the held PRs.
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
  only enqueuer, and the pack's `lane_landing` hook warns on `stack-enqueue`, `gt merge`,
  `gh pr merge`, and `ledger.py label` from a lane. Report READY the moment a PR is green;
  the landing pass enqueues the largest green, approved, unheld bottom prefix against the
  open cci `hold` records. On a held PR, report `held` on your tip with the held
  PR named and leave release to the coordinator. After the prefix lands, inspect whether GitHub already rebased the remaining stack onto the base branch before restacking with
  `ccx vcs stack submit`.
- After opening a PR, run
  `ccx vcs pr watch --lane-prefix <branch-prefix> --until landed` under Monitor
  (re-arm on expiry) or in a foreground loop instead of ad-hoc polling. `ejected` or
  `conflicting` means rebase now: `ccx vcs stack submit` from your lane's worktree,
  then report the new head; the landing pass re-enqueues it.
  `red <check>` means fix it. Your watch ends only when your PRs land.
- Before reporting a credential out of reach, check the parameter store (SSM) and the
  secret stores the brief names, and report where it sits. Reading a value takes the owner's
  Touch ID tap on that exact read, on the Mac only:
  `VALUE=$(cc-sudo run -- /bin/launchctl asuser <uid> /usr/bin/sudo -H -u <user> <absolute read>)`.
  Keep the argv literal, pick an AWS profile with `--profile` since an `AWS_PROFILE=` prefix
  does not reach the read, and assign stdout so the value stays out of the transcript.
  capt-hook's `secret_stores` guard refuses any other form and names this one. While the owner
  is away from the Mac no prompt fires and the guard queues a card, so hold that step and keep
  going on the rest. A Sprite has no `cc-sudo`, so its reads stay refused.
- GitHub budget: read GitHub only through ccx, with `ccx vcs pr status`, `vcs pr state`,
  `vcs pr watch`, or `ccx vcs gh -- <gh args>` for any other read. Writes stay plain
  `gh`. No loop reads faster than every five minutes, and every `ccx vcs gh` read in a loop
  passes `--max-age 5m`. A `ccx: GitHub rate limit floor:` refusal means stop reading
  until the reset it names. The supervisor posts an `urgent` record to the coordinator when any
  quota drops under 1500.
- Before designing, name what already grades or handles this signal: monitors, the
  release's Watch, open owner tasks and picks (`ccn task list`, `ccn answer search`).
  If one covers it, route through it or delete the duplicate. Building alongside it
  is a return-early-with-options case.
- Findings, decisions, and handoffs go to cc-notes (`ccn note add`, `ccn log append`,
  `ccn papercut`), never only into your report.
- Fix or accept each reviewer-bot finding with the risk
  named. Post a `head` record for every push, addressed
  `--to desk-runner,fidelity-pipeline,finder-desk,landing-desk` and never to the coordinator, as are your
  `state` records. A restack that moves several PRs posts one record carrying every head,
  `cci post --kind head --head <pr>=<was>..<sha> --head <pr>=<was>..<sha> ...`, with a
  note after the sha such as `patch-identical` when the range-diff says so, never one record per PR: the supervisor's finder reads that head and posts its `finder:` record to you; fix or rebut each major before you call
  a PR done. On a drive whose runner has no finder, run one finder pass yourself.
  A `## Body addendum` that answers a second-fix major by naming the class the fix
  removes posts one `head` record at the unchanged sha, `--head <pr>=<sha>`, with the
  note `body edit`: the finder reads the body again against that head's major, with no
  new codex pass, and posts `finder: ok` when the second fix was its one major.

Escalate early, never improvise: scope surprise, an assumption the code refutes, an
auth or approval gate, or two failed approaches. Ask with 2-4 options.

Finish: a lane with a PR finishes only once its squash `(#N)` is on the base branch.
It posts one PR comment opening with `## Rulings` and one opening with `## Regression
tests`, which lists each test id and links one CI run where that test failed against
the old tree, since nobody edits a PR body after it opens. The body's `## Risk profile`
states Load and Data in prose. A change that deletes a code path carries its caller
trace before the coordinator GOs it: every caller of the removed path, each one read, and
what the path deletes or changes for each.
Drive to a terminal state, post `cci post --kind done --to root` with the outcome, since
the lane supervisor relaunches a lane that stops without one, then send the preamble's `worker_done` with
`--outcome succeeded|failed`, `--task-id <taskId>`, and `--dispatch-id <dispatchId>`
from that preamble. The three-sentence body names what changed, what was
found, and what is left; the PR numbers, full head shas, and any tool refusal, verbatim,
go in the report file its `--report-path` names. `worker_done` is your last act: the
coordinator's gc then closes your idle terminal and removes your worktree once it is clean
and pushed, so push everything and leave no background process running first.
A standing desk, `role=desk` or `role=watch`, never reaches a terminal state: it
loops until rotation, through setup and quiet cycles alike, and never ends a turn
waiting. At rotation, on the supervisor's `ROTATE` record, it writes its handoff doc, posts
`cci post --kind handoff --ccn <doc id> --re <its seq>`, then sends `worker_done --outcome succeeded`
with the doc id in the body; the supervisor launches its successor from that doc.

Coordinator ids: <run id>; <cc-notes ruling and answer ids>.
```

## `<lane>.md` one lane's part

Orca's task-spec contract requires every spec to include `Target`, `Change`, `Constraints`, `Ownership`, and `Observable acceptance`. The shared `common.md` file supplies `Authority`, `Escalate`, `Do NOT touch`, `Worktree`, and `Finish`.

```
## Lane: <name> (<agent> <model> <effort>). <the mission in one line>.
ccx: role=<role; desk or watch for a standing desk> [cpu=high] [place=remote|local] [paths=<dir>,<dir>] tooling-lane=<key, for a tooling lane only>
Target: <files, component, or environment in scope>.
Change: <the outcome to produce, such as "one failing database never blocks a release", never a design>.
Constraints: <invariants, compatibility rules, do-not-touch boundaries>.
Ownership: <what this lane edits; every shared file and who edits it after it>.
Compute: <for a pass, backfill, dry run, or bulk rewrite: the in-region box recipe, region, and the bucket or team each process owns; never this Mac (SKILL.md R15). "none" otherwise>.
A box a lane tests on is an EC2 box: `ci devbox`, or `ci mac-box` for a Mac. Namespace belongs to CI, never to a lane's testing. A Namespace box a lane still holds is extended with `lr nsc extend <id> --drive <drive> --lane <name> -- --duration <d>`, never bare `nsc`, so its deadline is watched. Any Namespace devbox, Linux or Mac, is fine for a lane or a test box. Only Namespace's plain Macs are off limits, meaning a macOS machine type from `nsc create` that is not a devbox: they count against CI's limit of eight VMs, and every long-lived one makes CI jobs wait. A Mac that is not a Namespace devbox is an EC2 Mac box through the monorepo's `ci mac-box` and its `test-mac` skill.
Verified facts, do not re-derive: <ids, shas, URLs, state already confirmed>.
Design rulings, verbatim: <relevant register rules, their linked answer ids, and the entry point (symbol at file:line) each requires; or "none">. A contract quotes the owner's words for each pick. A narrower reading the build needs, such as per key for per Mac, is an ask to the coordinator, never a choice. Its failing-first test asserts the owner's invariant, such as process count flat in chat count, not the narrowed case.
Design check: before READY-FOR-SHIP, post `DESIGN-CHECK <symbol at file:line>; <each ruling, met how>; leaves out: <none, or each piece>` as a `contract` record on the bus and keep building; never wait for a confirm. The coordinator withdraws it only on a mismatch; a change that leaves out part of a ruling holds.
Design questions: settle each from this spec, the register, and the base branch yourself and post the choice as a `contract` record. Ask the coordinator only when the choice changes owner-visible behaviour, and keep building the rest meanwhile.
Stacking: one feature-sized PR per deliverable, with fixes and review responses as commits on it; stack only when a parent must land first or review needs the split. A stacked PR opens as soon as its parent's PR exists, never once it lands; restack with `ccx vcs stack submit` after the parent lands.
Standing rules register: <id of the newest `standing-rules:<slug>` doc, or "none">.
  Carry the owner-approved register's at most 35 rules, never full-answer quotes.
  Claude workers carrying CLAUDE_LONG_RUNNING_DRIVE receive it once at SessionStart.
  Above 9,000 characters, read it with `ccn doc show <id7>` before acting.
  For Codex, paste the body from `ccn doc show <register id>` here verbatim.
  Codex workers have no hooks. Claude drive sessions receive a relevant full answer
  only when the key-moment judge selects it, once per lane per answer.
Every rule binds this lane. Your READY names the rulings your diff touches.
Mechanisms the plan bans: <the drive's list, each shape with its rule id, or "none">. Before each push, run `rulings.py sweep --diff <file>` over your own diff, and fix each `MECHANISM-BANNED` line it prints or cite the owner's answer naming that shape.
Scale: <the N each path this lane changes runs at: chats, teams, sessions, or runs on one Mac or one pod; or "no N">. The bar: an interactive step answers within half a second at that N, and processes, timers, and connections per host stay flat as N grows, proved by a test at N = 1 and N = 1,000. A loop that awaits I/O per item, or a cap, timer, cache, retry, or idle wait standing in for a fundamental fix, is a defect the finder grades major; a diff that adds one names in the PR body the fix it stands in for and the owner pick that deferred it, or builds the fix. The runner warns on a non-incident launch whose brief has no `Scale:` line and launches it anyway.
Standing rules served: <`#<seq>` ids with their answer ids, or "none">; your task cites them, and completing this lane never retires them.
Plan task: register your unit at spawn so the progress record's plan counts include it: `ccn task add "<unit>" --plan <plan id> --label step:<n> --label program:<slug> --criterion "<done when>"`, then `ccn task start`, `ccn task criterion met|failed`, and `ccn task done` as it moves (SKILL.md Plan progress). The coordinator fills the plan id and step, or writes "none".
Observable acceptance: <the test, output, or PR URL that proves completion>.
```

## A design lane's part

A lane that designs, mocks or redesigns anything the owner sees, such as a dashboard, a board, a Slack card format or a skill's UI, carries these lines in its `<lane>.md` part, before `Change:`. Its brief never says to design from a blank page or "not from today's layout"; a change the owner wants away from the live artifact still starts from it.

```
Artifact: <the live artifact: its URL or app, and the repo path that renders it>.
Design record: <the ccn docs, answers and owner picks on this artifact, by id, or "none found" with the searches run>.
First step, before any mockup: open the live artifact and screenshot it as it is; read its design record; list the owner's prior picks on it, each with its answer id and his words. Post the list as a `contract` record. Then draw each proposed change inside the live artifact, never as a standalone page.
Draw: every visual deliverable, such as a page or screen layout, a component mockup, a design pass over a UI, or a navigation shape, is drawn by gpt-6.1-sol through codex, never by a Claude model alone. A page redesign runs a Fable pass and a codex pass, then one combined design with codex's taste weighted higher, and a sidebar over a top bar where the two disagree.
Card: the card that shows the change sets `design.changes` to this artifact, `design.live` and `design.inPlace` to the two screenshots on the card, and `design.record` to the record above; cc-present refuses it without them.
Live: a real screenshot of the new render, such as the `lr dashboard restart --proof` shots, goes on a card before the change ships, and every live or landed record of it cites `card:<card id>` or the `owner:<m-id>` row of his yes there, since live means he saw and accepted the render; `dashboard_live_needs_his_yes` refuses the record otherwise.
```

## A standing desk's brief

A desk on Orca, whether on its first launch or as a successor after rotation, uses this brief. The `ccx: role=desk` line (or `role=watch` for a watch) gives it the standing desk contract; a brief without it launches one-shot.

```
# <lane>-<N>: <desk's job>, successor of <lane>-<N-1>
ccx: role=desk
Standing: <the duty this desk keeps>
Read cc-notes doc <handoff or desk doc id> (ccn doc show <id>) and continue exactly as it says.
<live standing rules by #<seq> id; anything this successor changes>.
Report on cci as <lane>-<N>. Loop until rotation at <hours>h; setup and quiet cycles are not done.
Rotate: write the handoff doc, then send worker_done naming its id.
```

## Scoped resume

Use Scoped resume only for a lane that has already reported. Never message a running lane; put anything it may need in a file its brief tells it to read, or wait. Never resume a `codex:codex-wrapper` after it reports; spawn a fresh one with a scoped brief instead.

```
SCOPED FOLLOW-UP. Your original brief is CLOSED. Do not redo any of it.
Already done, do not repeat: <one line>.
Answer only: <the one question>.
Reply ≤5 lines, then stop. No re-investigation, no new edits, no new files.
```

## Sequencer lane

One sequencer lane owns a whole wait-then-do chain and sends one message at the end of it so the orchestrator never polls the queue itself.

```
Do:
  1. gh pr edit <n> --repo <repo> --add-label merge
  2. Poll origin/dev for the squash commit "(#<n>)" until it appears or 40 min pass,
     waiting between polls with `lr wait 30 mailbox:<lane>=<cursor file>`.
     A queue-merged PR reads state=CLOSED, mergedAt=null; the commit is the truth.
  3. On landing, <the dependent step this chain exists to trigger>.
Finish: one report - landed sha, dependent step result, or the blocking state.
```

## Checks before launch

- A lane that touches a subsystem the owner has ruled on quotes the relevant register rules verbatim, cites their answer ids, and names the entry point as a symbol at `file:line`.
- Each lane's `Ownership` excludes every other lane's files. If two lanes edit a shared file, the second is a stacked child of the first.
- A lane that runs a pass, backfill, dry run, or bulk rewrite names in-region compute on its `Compute:` line. Rewrite any brief that runs one on the Mac before launch.
- Every command in `common.md` runs as written from a lane's worktree.
- The `<lane>.full.md` attachment contains the shared contract followed by the lane section. Resolve its path with `ccn attachment path` before launch.
- The generated pointer spec names a brief path of at most 8 hex characters under `~/.claude` whenever the full brief path pushes the pointer past 500 characters.
