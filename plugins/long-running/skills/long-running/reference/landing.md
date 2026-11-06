# Merge queue and landing rules

Contents:
- [Roles and architecture](#roles-and-architecture)
- [Rules D1 to D23](#rules-d1-to-d23)
- [Queue adapters and runner passes](#queue-adapters-and-runner-passes)
- [The check registry](#the-check-registry)
- [Fidelity, finder, and rulings checks](#fidelity-finder-and-rulings-checks)
- [Merge mode, taps, and board blocks](#merge-mode-taps-and-board-blocks)
- [Holds, priority, dequeue, and requeue](#holds-priority-dequeue-and-requeue)
- [Go to queue](#go-to-queue)
- [Queue down](#queue-down)
- [Trunk health and redundant skips](#trunk-health-and-redundant-skips)
- [Incident fixes and release on landing](#incident-fixes-and-release-on-landing)
- [Desk cadence and commands](#desk-cadence-and-commands)
- [Message contracts](#message-contracts)

## Read by job

| You are | Read |
| --- | --- |
| A lane shipping a PR | [Rules D1 to D23](#rules-d1-to-d23), the ones that govern a lane (D1, D2, D13, D16 to D19, D21 to D24), [Who lands what](#who-lands-what), [Holds, priority, dequeue, and requeue](#holds-priority-dequeue-and-requeue), [Incident fixes and release on landing](#incident-fixes-and-release-on-landing) if you are an incident lane, and [The lane report](#the-lane-report-three-lines) |
| The landing desk | Roles, every D-rule, [Queue adapters and runner passes](#queue-adapters-and-runner-passes), [The check registry](#the-check-registry), [Fidelity, finder, and rulings checks](#fidelity-finder-and-rulings-checks), Holds, [Go to queue](#go-to-queue), [Trunk health and redundant skips](#trunk-health-and-redundant-skips), [Desk cadence and commands](#desk-cadence-and-commands), [Message contracts](#message-contracts) |
| The coordinator | Roles, [Merge mode, taps, and board blocks](#merge-mode-taps-and-board-blocks), Holds, Go-to-queue, [Queue down](#queue-down), Trunk health, Incident fixes and release, and Message contracts for asks, `RULING NEEDED` and the summary |
| A merge-train lane | D7 here, then `reference/merge-train-brief.md` |

## Roles and architecture

- The supervisor's landing pass (`lr landing pass`) is the drive's merge queue and owns D3, D14, D16, and D20 across all shards. No lane enqueues, labels, dequeues, or requeues beside it.
- Each landing pass that ends posts one `landing.pass` record. The supervisor on the host whose movers include `lanes` but not `landing` reads the newest one from the bus every 30 seconds and never reads the landing host itself. Once 15 minutes pass with no pass ended, it posts one `landing.silent` record to root and meta-desk, since no tap becomes a land order while the landing host is down, and one more, `state=recovered`, when a pass ends again. It says nothing until it has seen a first pass.
- The coordinator owns holds (see [Holds, priority, dequeue, and requeue](#holds-priority-dequeue-and-requeue)), receives `P0` and `RULING NEEDED` lines immediately, and receives a summary every 30 minutes.
- `scripts/ledger.py` is the desk's one tool, applying R5 and R3 in `SKILL.md` to PR state and watching. The desk grades, lands, and tracks only through it, never through scripts of its own; a gap in `ledger.py` is a `RULING NEEDED`, not a workaround. Scripts are on `PATH` by name.
- `ledger.py` reads state with `ccx vcs pr state` and transitions with `ccx vcs pr watch`; both use ccx's machine-wide pull request cache, which polls each repository at most once every 30 seconds.
- The PR store is a cc-notes ledger on `refs/cc-notes/*` with one row per PR our lanes shipped: `lane`, `declared_intent`, holds, routing, label history, landing fields. Lane messages are cci records keyed `#<seq>` on the drive named by `runner.json` `drive`. Owner asks are `ask/<seq>` rows beside the PR rows.
- cc-notes finds the ledger through the working directory's repository; a lane outside that checkout passes `ledger.py -C <checkout> <verb>`. Once `drive.py start` registers the drive, every `ledger.py` verb resolves `--ledger` and `--drive` from `--drive <id or cci drive>` when given, else from the session's drive.

## Rules D1 to D23

D24 sits at the end of this section.

### Who enqueues and who reports (D1, D2, D13, D18)

**D1. The landing pass is the only enqueuer; lanes report; the coordinator owns holds and priority PRs.**
- A lane never runs `stack-enqueue` outside `--check` and `--status`, `gt merge`, `gh pr merge`, or `ledger.py label`, and never adds a `merge` or `merge-fast` label.
- On a Sprite neither read works, since `tools/ci` fetches its binary from a bucket a Sprite holds no AWS identity for. A Sprite lane reads queue state, checks, approval and a verdict with `ccx vcs pr status <n>`. It asks nobody for the go: the landing host's go sweep gates every ready head each pass, and a question about its answer goes to `root`.
- The pack's `lane_landing` hook reads the drive registry and warns on those commands against the drive's repo, without blocking, from every lane except desk lanes (a name ending in `-desk` or `-desk-<n>`), `desk-runner`, `merge-train` lanes, and incident lanes.
- An incident lane is named `incident-` or `outage-`, briefed with `ccx: incident=<id>` other than `none`, or named `<x>-fix` and briefed with `Register slice: lane=<x>-fix step=incident`. On a Sprite the hook reads the brief file the worker's first prompt points at. The hook warns it only on `stack-enqueue` and `gh pr merge --admin` and lets it run `gh pr merge <n> --squash`.
- The same pack warns on `tools/ci trunk dequeue`, `restart`, and `prioritize` from every lane, desks included, because only the coordinator steers the queue. A tooling lane merges its own PRs in other repos.
- The moment a PR is green, the lane reports `"READY"` with `--verdict clean` on `ledger.py report --drive <drive>` and keeps `ccx vcs pr watch` on the stack.
- On ejection or conflict, the lane restacks at once with `lr landing restack --runner <runner config> --lane <lane> <top PR>`, which replays the stack on a Sprite, pushes, and posts each new head. The lane resolves by hand only a conflict that tool reports; the runner re-enqueues.
- When a coordinator `go` names the conflicting head, the landing pass runs that same restack under the lane's name, once per head; the lane hears of the conflict only when the replay moved nothing.
- A branch that holds the head of another open PR outside its stack, such as one whose GitHub base reads `dev` while its unlanded parents are still in it, is never restacked or queued by the landing host. Each pass reads every open PR's head from GitHub, so a host with a fresh store sees it too. The restack pushes nothing and posts `RESTACK-REFUSED` naming those PRs, a queued head is pulled with the same reason, and its lane puts it back on its real base.
- A lane is not finished until its squash `(#N)` is on the base branch. After a bottom prefix lands, it restacks the PRs above it the same way, with no ruling needed.
- A lane may restack its PR onto the current trunk proactively; no ruling is needed.
- Never push to a tapped PR the desk may enqueue: the queue rebuilds on dev anyway, so a rebase for an inherited red after the enqueue is pointless.

**D2. Track and grade our lanes' PRs.**
- Only the pack's PR hook, `ledger.py report`, `ledger.py register`, and an explicit `refresh --pr` open a PR row. `refresh` admits every open PR on a branch prefix or stack a lane registered, and the landing pass registers a PR carrying the `drive:<drive>` label under its head branch.
- `ledger.py unadmit --pr <n>` withdraws a PR admitted by mistake: its row stays unregistered until a lane runs `register --pr` for it, and any merge label the desk set comes off.
- Owner ask rows open only through `ledger.py ask`.
- The desk grades every tracked PR at its current head without waiting for a lane report, and never lists the repository's pull requests.
- The landing pass and `ledger.py label` refuse any stack holding a PR the drive did not admit. An `opened` record, a `head` record, or the author sign-in alone does not make a PR ours.
- A PR is the drive's when a lane of this drive opened it: a lane holds it and the drive's login wrote it, or the owner tapped it. The test reads the lane rows and the open-PR list the pass already reads, which names the author of every open PR, including one only an open item keeps in the snapshot. A PR whose author the pass could not read counts as the drive's.
- A PR that is not the drive's opens no item, so it draws no card, tap ask, grade, enqueue, dequeue, restack or hold lift, and the merge list skips it even on an item opened before.
- The ungraded escalation, the watchdog's red list and the incident squash still read a PR no lane owns, and drop only one another person wrote that the owner never tapped; a red row on one he tapped names its author. Another person's fix that an incident lane carries is never squashed.

**D13. Register each lane's stack when it starts and whenever it opens a PR.**
- The coordinator registers the drive with `drive.py start --ledger <id>`. The pack's PR hook registers every PR a drive session or Orca worker opens under the lane's name and records the head when the command prints one. `lr launch start` resolves the drive from the Orca run for workers the relay launched. A session no drive claims prints one stderr line from `drive.py record`.
- The hook settles the row through `drive.py landed` and `ledger.py landed` when a session posts `cci post --kind landed --pr <n>` or runs `gh pr close`.
- Lanes register their branch at spawn: `ledger.py register --ledger <id> --lane <name> --branch-prefix <branch>`. The prefix ends in `/` or `-` for a stack namespace; otherwise it names one exact branch.
- Run from the lane's worktree, `register` records the worktree when `.git/gh-stack` or a legacy Graphite stack holds the branch, so `refresh` tracks branches `ccx vcs stack new` cuts later.
- `register` refuses a prefix that covers the owner's whole `<user>/` namespace or matches open PRs another lane tracks or that predate the ledger. It names those PRs and suggests the exact branch, `<user>/<lane-slug>-`, or `--pr`.
- `ledger.py unregister --ledger <id> --lane <name>` removes a prefix and drops untouched rows only it pulled in. `ledger.py unregister --ledger <id> --pr <n>` removes a single PR row whole.
- Hand-register a PR only when the hook's context line says it was not recorded. To move a PR to another lane, the coordinator runs `ledger.py reassign --ledger <id> --pr <n> --lane <new>`. Read recorded PRs with `ledger.py list --ledger <id> [--lane <name>] [--open] [--json]`.
- Each `refresh` makes one `ccx vcs pr state --repo <repo> <PR numbers> --lane-prefix <prefix>` call, repeating `--lane-prefix` for each registered prefix.

**D18. A queued PR is watched until its squash is on the base. An ejection is a P0 the pass it happens.**
- The desk arms `ledger.py watch --drive <drive>` under Monitor when it spawns and re-arms it on every expiry. It forwards every `P0` line to the coordinator immediately for same-turn action.
- Every lane that reports a PR runs `ccx vcs pr watch --lane-prefix <prefix> --until landed` under Monitor, re-armed on expiry. In the foreground it runs `lr wait 540 cci:<drive>:<lane> -- ccx vcs pr watch --lane-prefix <prefix> --until landed` with `timeout: 570000`, never as a bare foreground command.
- `ejected` or `conflicting` means rebase now; the lane watches until its PRs land.

### What enqueues and when (D3, D16, D19, D22, D23)

**D3. Enqueue every ready bottom prefix in priority order.** `<tip>` is the top of the largest contiguous bottom prefix whose PRs pass.
- Under the `label` adapter, `ledger.py label --pr <tip> --checkout <path>` walks base refs to the repo's default branch and re-reads every PR in that prefix.
- Each PR must be open with an approval in force on any commit not dismissed or withdrawn, successful commit status, no failed checks, a completed successful latest `ai-review`, no desk hold or lane `held` verdict on that head, no prior label or pull, and no conflict with its base.
- Allowed `mergeable_state` values are `clean`, `behind`, and `has_hooks`. On a legacy Graphite stack, a PR above the bottom may also read `unstable` while Graphite's `mergeability_check` is its only unfinished check.
- `--expect-head` takes a 7-to-40-character lowercase hex prefix of the tip's sha and re-reads the tip immediately before grading. An untracked downstack PR or an orphaned base refuses the whole attempt.
- An open child outside the prefix is allowed only when a lane tracks its ledger row and its stack records it: the GitHub `stack` field on a native stack, or the `Graphite / mergeability_check` check run on a legacy Graphite stack. If not, link it with `ccx vcs stack submit`, or retarget it to the trunk with `gh api -X PATCH repos/<repo>/pulls/<n> -f base=<base>`, before adding the label.
- A lane report is not required; the forge decides whether a head is red or conflicting.
- When the repository carries a `stack-enqueue` script, each pass starts one `stack-enqueue <prefix top>` per ready prefix together in one Bash call, each backgrounded with `&`, then runs `wait` and collects each output. Each shard does the same for its lanes, using held rows from the whole ledger.
- Each sweep trial-merges every candidate PR against all PRs already in the merge queue and all PRs newly enqueued in that sweep, and holds back a candidate that would conflict with one of them, because the queue evicts a PR that conflicts with one queued ahead of it.

**D16. The green bottom of a stack lands now.**
- Enqueue the largest contiguous bottom prefix whose PRs are green, approved, and unheld as one batch; never wait for the top of a stack to go green.
- After a native GitHub stack prefix merges atomically, inspect whether GitHub automatically rebased the remaining stack before routing an explicit restack.
- When a restack is needed, the landing pass verifies ledger `landed` rows and routes the first PR above the landed prefix: by relay for Orca lanes, otherwise `cci post --drive <drive> --lane supervisor --kind blocker --topic <pr> --to <lane> --text "<restack route>"`. It sets the route verified when the child's head moves or lands.
- The owning lane restacks as D1 describes. The landing desk never duplicates this route.
- A downstack PR left on its parent's old head (`Waiting for restack`) publishes no review above it: walk the chain and restack from the first stale link.
- Before calling a PR's commits stale, find its real parent with `git branch -r --contains` and retarget the base; push nothing.

**D19. Land as many independent stacks at once as possible. Shape them so they do not conflict.**
- Every green, approved, unheld stack enqueues in the pass it reaches that state. Never hold a ready stack behind another stack's landing or slow down on an ejection.
- Shape stacks in the lane brief: put a shared-file edit in the smallest additive first PR in the file's declared order, and stack on another open PR that touches the same file instead of racing it.
- Never commit a generated output; on ejection, rebase and regenerate it instead of resolving by hand.

**D22. A PR whose release must avoid a time window stays out of the queue until a release outside the window can carry it.**
- Every merged change ships in the next release anyone cuts, and no hold record on any drive reaches another person's release.
- Hold the PR with `cci post --kind hold --pr <n>`, which refuses the enqueue under D1, or land it behind a code gate or feature flag that keeps it inert until then.

**D23. A PR that waits to go live names how it is safe on dev.**
- A body saying the PR waits for a card, a later tap, a release, or a hold before it goes live (such as `lands only after #<n> is live`) must also name a code gate, a feature flag, or that the change is inert until a named caller lands, as in `nothing calls <verb> yet`.
- Without one, the landing pass refuses it with `UNSAFE-ON-DEV` to its lane and the coordinator, `lr landing go` and `lr landing gate` print the same reason, and its tap card lists it.
- The lane adds the gate to the code and names it in the body, or drops a wait that no longer holds.

### Grade and route (D4 to D12, D14, D15)

**D4. Landed means the squash is on the base branch.**
- `ledger.py reconcile` fetches the trunk once, lands every row a squash subject ending `(#n)` names, and settles each closed row left by tree equality. It does one `ccx vcs pr state` read over the rest, with per-PR forge calls only for a closed row no squash names. `--dry-run` prints the changes and writes nothing.
- The queue deletes a stacked PR's base on landing, so `reconcile` never fetches that base and never settles a row by the PR's `merged` field. A closed row with neither match becomes `closed-without-squash`.

**D5. Route every red or conflicting row in the pass that finds it, once per head.**
- Run `refresh` then `route` in the same pass. `route` records the head, job, and lane it sent, so the same head and job are never routed twice; a moved head or a different job on the same head is routed again. Write nothing to the pull request.
- Route to a lane only while it is live. When a lane finishes, record it with `ledger.py gone` per R4 in `SKILL.md` and route its reds under D8.

**D6. Every hold has a reason and an expiry, and every message is recorded once.**
- `ledger.py hold` requires `--reason` and `--hours`; the summary excludes any row parked without them from its held count.
- `ledger.py enqueue` drops a second message with the same kind, PR, and head, so duplicate idle notices are stored and answered once.

**D7. The hot set lands by train.**
- A `merge-train` lane, spawned with the desk, owns every reported PR whose files hit the hot set. Route a hot-set row to the train, not to its lane. Every two hours it fetches trunk. `reference/merge-train-brief.md` holds how the train runs.

**D8. Route ejections, conflicts, and reds in the pass that sees them.**
- The owning lane handles its own ejection from its watch. The desk routes an ejection or conflict to the lane at once when the watch or a pass shows it.
- A row whose lane is gone goes to `merge-train` for the hot set, or to the standing `red-desk`, in that same pass.
- Hold a step red on trunk's latest build as `dev-red:<step>` with a 6-hour expiry instead of routing it.
- `ledger.py hold` refuses a PR that has green children unless `--stack` holds them all.
- Before calling a stack's red new, compare it against dev at the stack's base with `git log <base>..origin/dev -- <test files>` and restack before queueing; a red already fixed on dev after the base is not new.

**D9. A stale plan is information, never a refusal.**
- A pull request's plan is computed at its head. When the base moves under a stack that plan reached, the desk prints the stacks and movers in the grade and labels anyway.
- The landing plans the tree it applies and refuses its own op classes there. A rebase is owed only for a merge conflict or after a bottom prefix lands under D16.

**D10. Record reports immediately; reconcile every three minutes.**
- The landing pass runs D3 every 180 seconds by default without waiting for a report.
- The landing desk types reports, runs refresh, watch, P0 and red routing, records holds, and keeps the stale, summary, shard, and train duties.
- Each desk pass reads all PR numbers in one `ccx vcs pr status <n1> <n2> ...` call plus the Buildkite build list, never one REST call per PR, following the API budgets in `reference/watchers.md`.
- Stagger desks and shards by a minute at `:00`, `:01`, and `:02`. One refused prefix never stops another. A lane's `red` or `conflicting` verdict does not refuse a head the forge passes.

**D11. Name every clean row older than 30 minutes with its blocker.**
- `ledger.py stale` lists every open row whose latest report is `clean` and at least 30 minutes old, with one blocker per row: `held: <reason> until <stamp>`, `in the queue since <stamp>`, `in the queue, labelled outside the desk`, `routed: <job>`, `label refused: <reason>`, `head moved since the report`, or `never graded: run label --all-clean`.
- The summary carries the same lines and the p50 report-to-landing minutes.
- Clear each stale row's blocker in the pass that sees it with a label, route, hold, lift, or `"RULING NEEDED"`.

**D12. Shard at 15 lanes or 25 active rows, whichever comes first.**
- Split early. Spawn parallel `long-running:lane` sub-lanes, each owning a named set of lanes' rows in the same ledger with `--shard lane-a,lane-b`. A stack's rows belong to the shard of its tip's lane.
- `--shard` filters `refresh`, `landed`, `reconcile`, `route`, `label --all-clean`, `stale`, `summary`, and `inbox` to rows and cci messages whose `lane` is in the set. All shards share the ledger and its per-ledger refresh lock.
- Lanes keep reporting with `ledger.py report`; the main desk types every message in and alone sends the coordinator the summary.
- When rows fall back under both thresholds, tell the shard lanes the drive is over for them.

**D14. Grade a moved or unreported head like any other.**
- Every tracked current head takes D3's gates without a report.
- The landing pass routes a `BLOCKED` head at most once per head, and only for red CI, a merge conflict, a changes-requested review or fidelity grade, or a queue ejection.
- It never routes a pending or running check, a missing approval, an ejection blaming a PR outside the stack, or a PR that is held, labeled `hold`, or stacked above a held PR. It also skips any PR based on another open PR's branch unless a non-gate status such as `buildkite/test` is red on its own head.
- A queue drop blaming a PR outside the stack re-enqueues the same head after the coordinator's next `go` record naming the PR. A `flaky-check` drop goes to `landing.restack_lane`, never the PR's own lane.
- The runner re-runs the gate immediately before creating the route and suppresses a blocker that already cleared, without re-reading at a later Orca delivery.
- When a merge conflict's lane has no active Orca dispatch and no session in `cci who`, but has a cci record in the last 60 minutes, the runner parks the conflict: the coordinator gets one `PARKED parked:<pr>` line per PR, and the conflict routes once the lane is back.
- Orca lanes receive a relay; other lanes receive `cci post --drive <drive> --lane supervisor --kind blocker --topic <pr> --to <lane> --text "<blocker>"`.
- Under the `label --all-clean` fallback, send each refused head `new head <sha9>: <blocker>` once per head and blocker. If the head moved since the refresh, the next pass grades the new head without a route, while red CI and conflicts go through `route` without a duplicate message from the batch.
- The landing desk retains `ledger.py route` for red CI and conflicts and never repeats the runner's per-head gate messages.

**D15. Name what the desk is waiting on and ping the lane in the same pass.**
- The summary's `waiting:` line groups tracked open PRs: `ungraded` (the current head lacks a label and grade), `refused` (the head has a label refusal, reason in `stale` or `show`), `red` (a CI failure or `dirty`/`blocked` `mergeable_state`), `held` (a desk hold or lane `held` verdict at the current head).
- Run `route` and send its messages in the same pass. `summary` settles landings first; `--checkout` defaults to the working directory and `--repo` to that checkout's origin.

### Holds and renders (D17, D21)

**D17. An owner-visible change needs its render approved before the label.**
- Hold any PR that changes a UI, message, or generated document the owner sees rendered under D6 with reason `"render not approved"`.
- Keep that hold until the owner approves the exact render at the head being labeled, unless the owner granted ship-then-fix for that lane. A re-render at a new head requires fresh approval.

**D21. A format change lands readers first.**
- A PR that changes a format another build reads (a journal, seal, session, manifest, stored file, or row) is a writer. Ship its readers first in their own PR, and wait until that reader release is live on every reading client, including any browser bundle, desktop app, local daemon, or server, before enqueueing the writer. A client that never restarts on its own counts until it refuses the old build.
- The writer's body carries one `Readers-First: #<reader PR>` line per reader from its first ship, and never a `Release-With` pairing it with its reader. Prose such as "lands a release after #<reader>" holds nothing: the landing pass reads only the line, and a ship or body edit that says reader-first without it gets a warning.
- The landing pass that first reads the line posts the keyed hold `readers-first: #<reader> not live on every client` itself, one per reader, so the row is not ready, no card asks for his tap, and a tap waits for the lift. A coordinator `go` does not lift it. Only the coordinator does, with `lift --resolves <seq>` naming the live release that carries the reader and its live read-back, and the lift stands at the writer's later heads.
- The landing desk grades each tracked PR's diff for a format change and posts a hold with reason `"readers-first: no reader named"` on any writer lacking a `Readers-First:` line. The desk never lifts a readers-first hold itself.

### Release on landing and stuck mergeability (D20, D24)

**D20. A fast-tracked fix with a `Release-With` line, or any incident fix, starts its release in the pass it lands.** See [Incident fixes and release on landing](#incident-fixes-and-release-on-landing).

**D24. A PR GitHub never finishes computing is retried by the supervisor, not by hand.**
- On the landing host, an open row whose GitHub mergeability has read unknown at one head for 10 minutes gets a no-op base PATCH and one fresh read, at most every 10 minutes per PR. This keeps its head, approvals, and grades.
- After 3 PATCHes, a row with no tap at that head, no hold, not queued, and the same true of every row below it in its stack is rebased on its base once, and its lane gets one record naming what moved. At most 2 such rebases run per pass.
- A tapped or held row keeps its PATCHes and never moves.
- A native stack member refuses the PATCH with a 422; the refusal counts toward the 3, and after the third no more are sent at that head.
- Conflict and behind rows stay on their restack path, and a Trunk read that failed is not this case.
- A lane or the coordinator may bump such a PR at once instead, by a rebase on the trunk or an empty commit; then read its mergeable state again and queue it. A bump is a `rebase` head under [tap carry](#tap-carry-and-blanket-approval), so his tap stays with it.

## Queue adapters and runner passes

`lr landing pass` reaches the repository's Trunk queue through the `stack-enqueue` script `landing.queue_script` names in `lr.json`, and reads queue and CI state with `ccx vcs pr status`.

**Each pass.** It reads every open drive PR as facts and classifies each head into a state, a mover and a clock.
- A head that passes every gate enqueues as part of the largest clear bottom of its stack.
- A queued head whose head moved, or that sits under an open hold, leaves the queue top first with every PR stacked on it.
- An ejected head gets one resubmit, then goes to its lane as a red item.
- The pass renews the supervisor's lease right before it enqueues, so a supervisor another one has replaced enqueues nothing.
- One `a landing pass ended` log line per pass gives how long it spent reading facts, sweeping gos, gating and enqueuing, and in total.
- Its log says why each cleared head did not enqueue: a PR below it is not clear, its base is not trunk or an open drive PR, a named gate refused it, its CI is red, GitHub has not computed its mergeability, or its merge-queue state did not read. It also logs the queue's answer for each head it enqueued.

**The runner checkout.** The pass and the supervisor's background checks share one checkout. Two of them write its tree, the enqueue and the PR ledger's fast-forward, and each holds `<checkout>.tree.lock` while it does. The enqueue fetches trunk into `refs/lr/queue/<trunk>` and checks that ref out. The pass's diff read, the sweep's port read and `ledger.py` fetch into their own refs under `refs/lr/` and `refs/desk/`, so no fetch there writes `refs/remotes/origin/*` and nothing reads `FETCH_HEAD`.

**Where runner records go.**
- When `landing.desk_lane` in `runner.json` names one lane or a list, the runner sends `QUEUED`, `LANDED`, `MERGE-ASK`, `BELOW-BLOCKED`, and `FORK-DEFERRED` to that lane, each `landing.board_lane`, and the PR's owning lane (each read as its live successor), not to the coordinator. Without `landing.desk_lane` they go to the coordinator.
- A ledger row that settles landed with no enqueue or squash of the runner's own gets its own `LANDED` record the pass the ledger reads its squash.
- A drive PR that GitHub shows merged by anyone but `trunk-io`, the merge queue, gets one `MERGED-OUTSIDE-QUEUE` report to owner-relay in the pass that reads it landed. It names the PR, its head and its merger. The owner's tap, an exempt go, a standing word or the incident squash's merge at that head stops it. It warns and blocks nothing. The merger comes from the member read the pass already makes.
- A landed PR whose branch carried other open PRs into the trunk gets one `CARRIED` report to the coordinator and each `landing.board_lane`, listing every carried PR from the GitHub commits on the landed PR with the owner's tap, fidelity grade, and finder state.
- `GO-UNQUEUED`, `GO-TAP-MISSING`, and `REFUSED` go to each `landing.go_lanes` lane, the desk lanes, and the owning lane, and to the coordinator only when no lane owns the PR.
- Every pass posts one `state` record to the coordinator on topic `landing-state` with the queued, waiting, held, and refused counts.

**Owning lane.**
- A PR's owning lane for every blocker and restack the runner routes is the poster of the newest cci `head`, `opened`, `ready`, or `claim` record naming it, never the coordinator or a desk; else the lane that registered its ledger row. To hand a PR to a new lane, post a `claim` record as that lane.
- A cci blocker also goes to the coordinator when `cci who` lists no live session for that lane.
- A lane with no live session or dispatch and no cci record on the drive in the last 60 minutes is dead. Its blocker goes to `landing.restack_lane`, or the coordinator if none is named or all are dead, opening with `owner lane dead`, and its conflict is never parked.

**Escalations to root.** Each landing pass groups the open items it already keeps by cause and reads nothing new, since its snapshot and store hold every fact.
- Three or more PRs the queue refused with the same words (each PR's own number set aside) for 30 minutes, or that waited an hour on the supervisor, the grading pool or a reviewer, raise one escalation to root naming the cause and every PR, once while the oldest of them stays. Two alike refusals never raise it.
- A ready PR, or one whose row waits only on the change below it, held by a stack base whose item is past its deadline, or that no lane drives, raises one escalation to root per base head naming the base, what it waits on and the PRs it holds. A base under a hold names that hold, and a base whose row waits on something names what the row says, never the lane. The PRs it holds open no item of their own, and the base is the lowest PR in the stack that waits on something other than its row.

**Ungraded heads.**
- A `blocked` or `ready` record from a live `build` or `fix` lane claims each PR it names that no lane owns, and never takes one from another lane.
- Each pass stamps a head the first time it reads green with a grade missing, and skips a draft or a head that must restack first. At an hour one note goes to the grader desk and the PR's lane; at three hours one escalation goes to root, once, unless the head is approved, which [Row waits](#row-waits) has already raised.
- A PR no lane owns is not drive work and tells nobody.

<a id="row-waits"></a>
**Row waits.** The supervisor reads each review row's `waits_on` and `ready_for_tap` on every tick, with no GitHub read. A wait past its clock posts one blocker to root, once per PR and head, naming who owes it and what the owner is waiting for. A new head starts its clocks over, and a teammate's PR raises none.

| The row | Clock | Who it names |
|---|---|---|
| `waits_on` reads `not graded at this head`, so every gate before the grade is clear | 30 minutes | The live `finder-desk` or `fidelity-desk` for each grade missing, and the pass running or that none is left |
| Open and not ready for a tap, with no hold and no open parent, its lane gone | 1 hour | Root, with the command that names an owner |
| `waits_on` reads red, and its newest `red` record at this head reads `class=inherited` | 1 hour | The first other `#<n>` in that record as the PR that broke it, and the class's `owner` |

A red nobody classed raises no row wait, so a lane or `red-sweep` classes it first ([red-sweep-brief.md](red-sweep-brief.md)).

**Clobbered merges.** Each landing pass reads the trunk squashes since the last one it read.
- For each, it fetches the PR's head and recomputes every merge commit in the head's history with `git merge-tree --write-tree`. Lines the clean merge has and the recorded one lacks, in files the squash touches outside the ones that conflicted, are work the merge dropped.
- Each trunk landing that merge took in is credited with the dropped lines it added, so a landing whose change the stale tree kept goes unnamed, and a file or PR the squash's description names is skipped.
- One `CLOBBERED` report names both PRs, the files and the line count, to root and to each PR's lane. It never holds or cards a PR. A read that fails is logged, and the pass moves on without reading that squash again.

**Editing `runner.json`.** Edit it only through `lr runner config set KEY VALUE` and `lr runner config unset KEY` (a dotted key such as `landing.desk_lane`), never by hand; both replace the file by rename and only after the edited file loads: entries such as fidelity desks added before they register keep the landing runner down.

## The check registry

Every prefix passes the check registry before it enqueues. `landing.checks` lists the checks in order; every built-in runs when it lists none.

| Check | Behavior |
| --- | --- |
| `hold` | Reads the `hold` label and the PR's cci `hold` and `ready` records fresh just before the enqueue call. Runs first on every enqueue path, including the landing pass and `lr landing gate`. An incident record that only mentions a PR as context holds nothing. A `hold` holds exactly the PRs it was posted with as `--pr`; every `#<n>` in its text is prose, the leading one included. |
| `precondition` | Scans the PR body and cci records outside quotes, code, and block quotes for `lands only after`, `merge only after`, `before it lands`, `do not land until`, or `not for landing`. Never refuses a head; sends the coordinator one `note` warning per head quoting the sentence, including on incident squashes, so the coordinator can post a `hold` if it is a real dependency. |
| `rulings` | Runs `rulings.py check-pr` at the head ([Fidelity, finder, and rulings checks](#fidelity-finder-and-rulings-checks)). |
| `fidelity` | Reads the fidelity lane's cci `review` grade at the head (same section). |
| `finder` | Reads the sol finder's cci `review` record at the head (same section). |
| `merge-mode` | Enforces `landing.merge_mode`, `landing.tap_rules`, and reserved rows at accept and again just before the enqueue call. |
| `approvals` and `required-ci` | The adapter's own verdict on the PR. Red CI counts only checks required by `dev`'s rulesets plus Trunk's queue check; unrequired checks such as `buildkite/release-pr-check` do not block or refuse. A stack check failing because `origin` lacks the PR's named branch refuses that head to its lane, naming the branch to push or retarget. |

**What a refusal does.**
- The check that refuses a prefix posts its blocker or ask and stops it there. The pass posts no commit status and adds or removes no label, so a hand `gt merge`, web-UI enqueue, or hand adapter call bypasses every gate.
- `lr board waits --pr <n>` says what a PR waits on, and `lr landing gate <n>...` prints each gate a hand enqueue passes over; neither posts anything.
- `lr landing gate` can take over 40 seconds. Read its output to a file with no timeout wrapper and no grep, and read its exit code: empty output means nothing unless the exit code is 0, and any "gate clear" claim quotes the exit code.
- When a PR shows a conflict-style `NOT-READY` line but the merge queue's activity shows it was evicted, the sweep prints `EVICTED` for it instead of the stale line and re-runs the gate on that PR in the same sweep.
- A head whose stored queue answer is Trunk's `PR_NOT_ENQUEUEABLE` refusal naming a PR above it in the same stack that is already in the merge queue reads as queued at that head and is watched as queued, not enqueued again. An evicted read still wins, and a new head starts without the answer.
- A failing required check posts one `blocker` per PR, refusal, red job, and cause, where the cause is the status description with run numbers and durations folded. A later head failing the same way gets one `note` under that blocker; a changed cause or new red job opens a new blocker.

**One enqueue per set of heads.** Each exact set of prefix heads gets one enqueue action. The runner permits another attempt for the same heads only when earlier attempts are proven to have enqueued nothing, or when `ccx vcs pr status` reads a believed-queued PR `not queued` or `evicted` at the admitted head.

**Ejection classes.** The adapter's verdict, never PR comment text, classifies the drop:
- `conflict`: stays out at that head, and the gate routes its ejection to the lane.
- `own-red`: gated again, and the adapter blocks it until a new head.
- `batch-mate-red`: gated again and re-enqueued at the same head. One blaming a PR outside the PR's own stack logs `AWAITS-GO` and waits for a coordinator `go` naming the PR.
- `flaky-check`: a red check that read green on the PR's own head and failed only in a queue draft holding nothing but its stack. It stays out at that head after its first ejection, sends one blocker naming the failing job to `landing.restack_lane` or the coordinator, and re-enters only at a new head.

**Batches with PRs left out.** The runner sends the coordinator `HELD-OUT`, `BATCH-WAITS`, or `FORK-DEFERRED`:
- `HELD-OUT`: a hold covers a PR the check sweeps into the batch. That PR and every PR above it stay out; the enqueue names only the tops below it with `--partial` on `graphite-stack-enqueue`; no check or enqueue call ever names a held PR.
- `BATCH-WAITS`: a coordinator `go #<n> at <sha> with #<a> #<b>` binds its PRs and the `with` PRs into one batch, and the prefix holds only part of it because one is held, red, or untracked. Every PR of the batch and above stays out until the whole batch can enqueue in one call.
- `FORK-DEFERRED`: the batch sits on an open fork outside it and waits until that fork lands, naming the waiting PRs and the fork.
- A batch that forks within itself goes in as one stack-enqueue call per linear chain in the same pass, bottom chain first.

## Fidelity, finder, and rulings checks

**Fidelity check.**
- The lanes whose fidelity grades count are one list in the `lr` code, `bus.FidelityLanes`, and a numbered successor of a listed lane counts, so hosts cannot disagree. A new base name needs a one-line PR and an `lr` release. `landing.fidelity_lane` in `runner.json` is read now only by `rulings.py` and the board text hook, until they move to Go.
- The runner reads cci `review` records from those lanes that match `--pr <owner/repo>#<n>` or bare `#<n>`, whose text starts `fidelity: ok` or `fidelity: missing`, and whose first short sha is the PR's current head, such as `fidelity: ok <id> at <head7>`. Each lane's newest grade at the head stands for that lane.
- A `missing` blocks until that same lane grades the head `ok`, a coordinator record about the PR resolves it, or a coordinator record reads `fidelity-override #<n> at <sha>`. Here `#` and `at` are optional, and any record id, `<seq>@<head8>`, or the drive copy's seq may follow the colon.
- An `ok` counts once posted; register rows added later never void it.
- A head graded `missing`, graded `ok` only at an older head, or ungraded gets one cci `blocker` per head and grade to the PR's lane and the fidelity lanes. The prefix is cut below it, enqueueing passing PRs below with `--partial` and naming the cut in the blocker. The check runs again just before the enqueue call.

**Finder check.**
- The newest `finder:` review record naming the head from `finder-desk` or a numbered successor must read `finder: ok`; a lane's own record or a reply never grades a head.
- A head with none, or whose newest is `finder: major`, gets one `FINDER-PENDING #<n>` blocker per head to its lane and `finder-desk`.
- A finder pass answers a major with a new head's record or a later `finder: ok <head7> answered: <how>`. A coordinator `decide` reading `finder-override #<n> at <sha>` settles a finder major at that head the same way as `fidelity-override`. Only a `decide` from `root` or `supervisor` counts, at the go gate and on his row alike; the same words in a `decision` or any other kind settle nothing. grader-appeals, under any lane number, posts the same `decide` itself when it cites owner rows: the lanes machine, whose notes are current, checks that every `rule/`, `prompt/` or `answer <id>` it cites exists and posts the same override as `supervisor`, which is the record every host counts, so no landing host reads an owner row. An appeal citing no row that exists counts for nothing and comes back to that lane as `OVERRIDE-REFUSED`, since an override citing no owner row goes to root. On a new head whose own diff is the same, the landing pass posts the override again as `supervisor`; a head whose diff differs gets no carry. The `owner/deletion` check fires only when the title's deleted thing is unnamed by the cited owner words and the diff removes more lines than it adds or deletes a file.
- A lane takes back its own override with `cci post --kind withdraw --re <seq> --pr <n>`, or `--resolves <seq>`, naming the record it posted. From then on no record carrying that same override, `<grader>-override #<n> at <sha>: <cite>`, counts at that head: not root's repost, not the `supervisor` count, not a repost posted later, and a later repost leaves a different override standing there in place. The lanes machine answers any later appeal or root `decide` carrying it with `OVERRIDE-REFUSED` and posts no `supervisor` count of it. The go gate then prints its `no counted decide` clause and his row shows the grade waiting on a verdict again. A withdraw from any other lane takes back nothing. To override again after a withdraw, cite a different record; that counts as usual.
- The stall watch reads overrides the same way. A coordinator `decide` whose override omits the PR or the head draws one `OVERRIDE-REFUSED` blocker to the coordinator.

**Grade carry on a new head.** A grade carries to a new head when nothing meaningfully changed since the head it graded; a head where something did gets a full pass.
- When no grade names the current head, the runner compares `git patch-id --stable` of the PR's own commits at the current head and at any older head whose newest grades are `ok`. It reads those commits from the GitHub compare of `<head>~<n>...<head>` with `n` as the PR's commit count, or diffs against the PR's base. A compare GitHub refuses logs `PATCH-READ-FAILED` once and carries nothing.
- Equal patch ids carry the `ok` in that pass as `fidelity: ok at <new head>, patch-identical to <graded head>` under the graded record's cci seq. A fidelity lane asked to grade such a head answers immediately with the same line without re-reading. The newest `finder: ok` carries the same way.
- The finder and fidelity passes carry on their own as well. Each pairs the PR's own commits at the new head with those at the newest head it graded clean through `git range-diff`: for the finder a `finder: ok`, for fidelity a head where every lane's newest grade is `ok` or a `missing` the coordinator settled. A head whose every pair is equal, or amended only by lines the base now carries, gets the earlier grade with no model call.
- Any other head past a clean grade gets the re-grading read first, in the same pass: one `claude -p` at Sonnet, effort high, given the range-diff, the earlier grade and the PR's title and body, with read-only git tools and a ten-minute deadline. It answers `carry` or `sol`, each with one sentence of why. A carry posts the grade at the new head naming the record and head it came from, `finder: ok <head> carried from #<seq> at <head7>, judged unchanged in meaning by sonnet over its range-diff: <why>` or `fidelity: ok <head> carried from <head7> (cci #<seq>), judged unchanged in meaning by sonnet over its range-diff: <why>.`, and no gpt-6.1-sol session starts.
- A `sol` answer, a read that fails, times out or gives no answer, a head with no earlier clean grade, an earlier grade with an open finding, a fidelity head past the owner's tap, and an `lr grade regrade` all get the gpt-6.1-sol pass over the delta exactly as before. Nothing passes silently.
- Each pass's `result.json` keeps the read's answer as `regrade`, `carry: <why>` or `sol: <why>`, so the Sol sessions it saved and the answers themselves can be read back per head.

**Runner-side finder pipeline.** It runs when `runner.json` carries `"finder": {"model": "sol", "concurrent": 2, "checkout": "<repo checkout>", "to": ["<lane>"], "timeout_minutes": 60}` and `lr.json` lists `grading`.
- `lr supervisor run` starts a sol finder pass on each `head` record, reading multiple `head:<pr>` attributes on one record as distinct heads.
- Each head retries up to 3 times before posting `FINDER-FAILED #<n>` to the coordinator and `to`. A desk with pending heads and no completion for 15 minutes posts `FINDER-DESK-STALLED`.
- A coordinator record reading `finder: whole` switches the desk to whole-program mode (`reference/finder-desk-brief.md`).

**Runner-side fidelity pipeline.** It runs when `runner.json` carries `"fidelity": {"model": "sol", "concurrent": 6, "baseline": "<live old-app baseline>", "to": ["<lane>"], "timeout_minutes": 60}`; it starts a pass on each `head` and `opened` record (`reference/fidelity-desk-brief.md`, `reference/fidelity-job-brief.md`). With `fidelity.sprite` set, `fidelity.sprite_keys` names a command for `ANTHROPIC_API_KEY` beside the codex and GitHub keys, since a grader Sprite has no Claude sign-in and a head codex hands to a reader runs `claude -p` in the same pass.
- The `fidelity` object's optional `service_touches` list holds `{"service": "<name>", "path": "<glob>", "added": "<regexp>"}` entries. An added non-test line under a path whose leading directories match `path` and whose text matches `added` makes the pass grade `old-app: CHANGES` and name that file, line and service, whatever the read said. An entry whose glob or regexp does not compile is logged and skipped. An entry `{"service": "old-app pages", "path": "dashboard/src/iris/durable", "added": "\\S"}` covers the directory old pages run in `Forge-AI/monorepo`.
- A `reader` verdict on a head whose old-app line names such touches posts `fidelity: reader <head> OLD-APP-PENDING: <why>` to the lane and `to` only, never to every fidelity desk. The head holds in grading until a fidelity desk posts a grade at that head.
- A coordinator `decide` reading `class wrong` re-runs the classifier. A `fidelity: reader <head> <why>` record grades a reader PR. Three failed attempts post `FIDELITY-FAILED #<n>` to the coordinator and `to`.

**Both pipelines.**
- Each `to` lane is addressed by its stem, the name without a trailing `-<n>`, so a stale number in the config still reaches that lane's current generation.
- A pass that dies tells the PR's lane `GRADER-FAILED #<n>` once per head, at its first failure, and a pass killed for no progress tells it at each kill. A pass lost with its Sprite, which ends with no result, tells it `GRADER-LOST #<n>` the same way, and the pool runs it again.
- Both compare patches with lazy fetches off, after one fetch of every blob on both sides of each diff they compare, so a blobless checkout answers in seconds and a missing blob fails the pass at once.
- A pass skipped because the PR moved does not answer its head: if the PR returns to that SHA, the next pass runs a new attempt. A settled pass whose head still has no grade reposts its logged records once, in case the store refused them while it ran.
- A grade item never sits unclaimed. Once an item is past its deadline, if ten more minutes pass and every grader it is missing has nothing left to run, the runner posts one `GRADE-STOPPED` report to `grader-appeals`, `tooling_lane` and the PR's lane, naming why for each grader. It then records `grade-stopped` on the head, which closes the grade item and opens a verdict item for the lane on the lane's clock. Any grade posted at that head still clears it, and a new head starts over. A coordinator `fidelity-override` or `finder-override` at a head with no grade from that grader counts as its grade, stopped or not, on every host, so the landing pass and the go gate read the same head alike. A later failing grade from that grader takes the override back.
- A tapped or go'd head that waits past its deadline on a mover the landing host does not run says so: the landing pass logs `a tapped head cannot move` at warn on every pass and posts one `landing.stuck` record to the PR's lane and root, which every store folds onto the row's `waits_on`. A later `landing.stuck` with no reason clears it.
- A slow pass is not a dead one. A worker that dies is seen in minutes, since its `sprite exec` returns and the wrapper writes `no_result`. A pass with no progress for 15 minutes is killed on its Sprite, checked against the process id and start time it recorded, and runs again at once on another Sprite when one is free. Progress is `job.log` growing here or any file changing in its directory on the Sprite, codex's event log included. The kill says so in `GRADER-FAILED`, naming the Sprite and the minutes, once per attempt, and that Sprite takes no new pass for ten minutes. The `timeout_minutes` deadline fires only on a worker still making progress and stops it the same way, so an attempt never starts beside the one it replaces.
- A Sprite's home is a network disk whose writes run hundreds of times slower than its local `/tmp`. So `lr grade` on a Sprite moves the clone's `.git` to `/tmp/lr-git/<hash of the checkout>` on the first pass that finds it on home, under the same lock as the fetch, and leaves a link behind. `/tmp` survives a Sprite's restart. A clone that arrives shallow takes its whole history under that lock too, since a shallow trunk reads every commit behind a head as the head's own.
- A grader's git never touches the clone's worktree on home, where one lookup can take seconds and a copy search walks every directory. `lr grade` sets `attr.tree=HEAD` on the clone, so attributes come from objects on `/tmp`, and the pass's git calls and the rulings diff run with `GIT_INDEX_FILE` on an absent path, so git stats no worktree file to reuse it for a blob.
- The rest of what a pass reads moves too. The first pass on a Sprite starts one detached background move, which rebuilds the clone's worktree on `/tmp` from objects and links ccx's state there, keeping the old directory beside it as `<path>.home`. `.local/bin` and `.codex` stay whole on home, since overlayfs cannot rename a directory from its lower layer: the move keeps a copy of each on `/tmp`, refreshed when home's changes, and `lr grade` puts it first through `PATH`, `SPRITE_TOOLS_PATH` and `CODEX_HOME`, so a Sprite with no copy falls through to home. Codex's `sessions` stay on home, since they only grow. `sprite-job.sh` puts each job directory and each shipped copy on `/tmp` behind a link and removes the job directory once it is copied back, and it deletes a shipped version an hour unused once no job holds its lock.

**What a grader reads of the owner's answers.**
- A pass's `rows.md` holds the register rows the head touches, the owner's answers on the topics it names, every answer the PR cites, and his top-ranked durable answers on the scope of its title.
- A cited id goes through `lr rules answers`, which reads it by id from the answer store as `lr rules row` does, so an answer no register row carries still reaches the grader with his words. A superseded answer brings its newest with it. Only an id nothing holds, such as a commit sha, is named as unknown.
- Beyond those, the brief points the grader at `ccn answer show` and `ccn answer search` before it calls anything of his absent.

**Rulings check.**
- The `rulings` check runs `rulings.py check-pr --repo <checkout> --drive <drive> --pr <owner/repo>#<n> --head <sha> --base <base>` once per head per pass and holds no queued PR head.
- It fails open: a non-0/1 exit, a line naming no cause, or a `BLOCKER` line passes the head with nothing posted. `reference/owner-rulings.md` details `NAME-HIT`, `MECHANISM-BANNED`, `RULINGS-UNCITED`, and `rulings.py sweep`.
- `STORE-UNCOUNTED` names a head whose removed lines drop a stateful store, such as Valkey, Redis, ElastiCache, a volume claim or a `DROP TABLE`, while no body line counts the records it held before and after.
- Each cause it prints except `RESERVED-NO-ANSWER` sends one cci `note` warning per head and set of causes to the PR's lane. It warns again only for a new cause, and never once an `answer` replies to or resolves one.
- The pack's `rulings_at_merge` hook runs `check-pr` on `gh pr merge` in a drive session and advises with each cause. On an incident lane's merge it refuses with `RULINGS-REFUSED` while `check-pr --incident` names `RULINGS-UNCITED`, `RESERVED-NO-ANSWER`, or `TEST-REMOVED`.

## Merge mode, taps, and board blocks

### Who lands what

- Every PR goes to the owner. A PR that is green, approved and graded at its head reaches his tap card within one runner pass and lands on his tap: the runner draws the card from the row alone, no lane posts anything, and whether he has answered earlier cards never holds one back. A limit in front of a wait on him needs an exit that does not depend on his answering, and hitting it is never silent. A row turning ready draws its own card. A row that moves to a new head draws a card for that head, and only then does the card at the old head leave his board: its Slack card loses its buttons and says Replaced, linking the new card. When more than one row comes due in a pass, those whose gates miss only his tap arrive instead as one list post, incident fixes first, then oldest, each row with what it does for a user, its plan line, what holds it, and its Approve and Hold. The list is edited in place as rows leave it. No constant caps how many reach him: a list splits only where Slack's 50 blocks a message force it, 15 rows at three blocks each. A row on a list that moves to a new head leaves it and draws its own card. A row reads as shown only once a post stands, never because something read its card: cc-slack tells `lr board card` after a post succeeds, so a failed post or a render leaves it unmarked for the next pass. A draw that fails raises one alarm to owner-relay naming the PR; a list post that fails raises the pass's alarm and sends again on the next pass. Only the classes he exempted land on a go; nothing new joins them without his word, and the `landing_approval` hook refuses a record or brief that changes it.
- A stale PR or a draft never reaches his board.
- A PR he withdraws or rejects is closed on GitHub by its lane in the turn it hears his word, with a comment saying he withdrew it; it never sits open. In a public repo the comment paraphrases him.
- Every push of PR rows to the owner's board refuses a row for a tap already given at that head, a rejected head, or a stale head, so he is never asked twice for the same tap.
- When two PRs implement one ruling, he gets one tap card for it, never two competing ones: the coordinator picks which lands or folds one into the other first.
- When he says he is going to sleep, every lane keeps shipping: the exempt classes land on go records all night, and every other green, graded PR waits as a tap row for his return.
- [Tap carry](#tap-carry-and-blanket-approval) decides whether his tap stays with a later head. A head it does not cover returns to his board saying what changed.
- The row in `lr board rows --json` is the only list of drive PRs; no lane keeps its own. A PR waits on him only when its row says `ready_for_tap`, and a line saying otherwise is refused. `lr board waits --pr <n>` reads what a PR waits on. A PR's state changes by posting its record, never by asking for a hand entry.
- Anything else waiting on him is what `cci owner-waits` prints, never a runner note.
- Every `gh` write posts under his GitHub login, so an approval or a tap verdict a drive posts must cite `board verdict #<seq>`, the `owner` record of his verdict on that PR's row at its current head, or the `owner_login_verdicts` hook refuses it.

### The land order (`lr.tap`)

- An `lr.tap` record on the bus is the land order, and the only tap the landing pass and the desks read. It lives on the bus because the landing pass may run on another machine than the board.
- The supervisor writes it within 30 seconds of the owner's record for a tap on any surface: a merge row approved, held or undone on the review screen or the board, an approval card, or a pick whose label starts with land, merge, approve, ship or hold on a card naming one PR. A record naming no head lands at the PR's head when the order is written.
- Only the landing host writes the order: the Slack merge card writes an owner record, because an owner machine's relay never carries a dotted kind like `lr.tap`.
- A thread answer or a lane's hand carry of a tap has no land order yet, so relay or the board watch records his words as kind `owner` naming the PR, and the lane holding it runs `lr landing order <PR> --record <seq>`, which writes one. It runs from any box: on one with no drive `lr.json`, such as a Sprite, it posts that owner record at the PR's full head on GitHub with the writing lane in its `writer` attr, and the landing host gates it on its next tick as it gates a card tap, and a head the PR has moved past meets the same carry rule. Any other kind or lane is refused, however it names the PR, and the refusal prints the post to make. The record is read whole, its spilled body included.
- A tap with no order after 60 seconds posts a blocker to `meta-desk` and `owner-relay`, and is retried; nothing is held for it. A land or hold pick whose card names several PRs can get no order of its own. When a later record carries the same order for each PR it names, as owner-relay does one PR at a time, it posts nothing; otherwise it posts that blocker once it is 60 seconds old, and a lane names the PR and runs `lr landing order`.

### A tap reads the row

- Only the host with the landing mover answers a tap. A tap from any surface stands at the head it names whatever the row still waits on: CI, a grade, a review, mergeability, the change below it, the merge queue, or a Summary, plan row or Risk profile missing from its body. The landing pass lands only a row that is ready and tapped, so it lands once the checks clear, and a row already queued stays queued. A tap at a head the row has moved past stands at that head, and the fold's carry rule decides whether it counts at the new one. A row that is held, superseded, closed, a draft, landed, or waiting to go live refuses it: no `lr.tap` is written; one `tap.refused` record names the reason, a `blocker` tells owner-relay what stops it, and the reason shows on that row until a later tap stands. A refusal for a hold keeps the order pending for the hour: it is written when the hold lifts with the head unchanged and dropped when the head moved. A tap on a row no record has named a head for yet is not refused: one `tap.pending` record says it waits, no blocker is posted, the progress view shows the PR tapped and waiting for that record, and the order is written once a head is recorded, at that head when the tap named none or named the same one, and at the tap's own head otherwise, so the carry rule decides whether it counts. A tap still waiting after the hour posts one blocker to `meta-desk` and `owner-relay`. The tap card drawer reads GitHub and the bus before it draws, never the rows alone: a PR GitHub reads as merged or closed, or one he tapped in the last hour that no `lr.tap` or `tap.refused` has answered yet, gets no card, and neither does a head his standing tap carries to under the carry rule. A head whose fidelity grades at that head read `ok` and name an exempt class waits for the go sweep instead of drawing a card, until his tap or a `go.refused` stands at it. When the sweep refuses that head, it posts a `go.refused` to the PR's lane naming each reason, and the card is drawn on the next pass. A tap card shown or open at an exempt head keeps it on his tap. The exception is a head the finder passed whose class lands with no tap: test-only, CI-only or ci-tooling, a pure port, an old-tree port, or a flake fix. It lands on root's go past its own tap card, and the drawer then takes the card off his board. His answer on that card still stands, and a Hold keeps the head waiting. Board-link takes a tap card off the board the moment a `landed` record names its PR, and raises no alarm for a refused draw when his tap for that PR is on the bus. A hold with `--attr until=tap`, or whose text says no landing until he answers, taps, approves or lands, is an until-tap hold, and his tap ends it. A Slack merge card tap on a PR with no row writes the same record, unless the host's store holds no row for any PR yet: that tap waits for the first fold, writes no record and no blocker before the hour, and the `tap-orders` cursor holds at it. A tap on a PR that is merged or closed, by its row or by GitHub when no row is folded yet, is dropped with no record. His typed answer to a card, recorded by owner-relay as `Owner: "<label>" #<pr>`, is a tap when the label starts with land, merge, approve or ship and the text names one PR of this repository; any other typed label orders nothing. A Slack merge card Approve on a head that moved stands at the head the card named, and its Hold holds the row at its current head.
- A refused tap never revives: when the row turns ready, the PR returns to Waiting on you and needs a new tap, so lifting an ordinary hold never lands something he tapped while it was held. An `until=tap` hold keeps its row ready, so his tap stands and ends it, under [Holds](#holds-priority-dequeue-and-requeue). A hold or an undo is always written.
- The landing pass, `lr landing gate` and `lr landing go` read the same row: a PR lands only when its row is ready and it is tapped, or it is exempt under its rule. `lr landing go` still needs a row at that head.
- A row not ready on a PR that passes every other gate opens a `row-wait` item for its lane, and `lr board rows` says what it waits on. Past its deadline the item asks that lane or its heir, as a red does. When the lane stopped itself or went silent, the ask goes to it and the supervisor relaunches it once from its brief; any other lane that is not running reaches root as UNOWNED. Three or more still open after an hour tell root in one record.
- A tapped PR with no row lands nothing and tells `meta-desk` once.
- A tap that carries lands when the row is ready at the new head.

### Untapped queue entries

- A queued PR with no tap and no exempt class at its head is dequeued the pass it is read when the drive queued it: lr recorded an enqueue of it, at any of its heads, or of an open PR below it in the stack. The dequeue is an `UNTAPPED-DEQUEUED` blocker to its lane and the meta desk. A queued untapped PR no longer clears the PRs above it.
- An untapped queue entry the drive did not enqueue is a person's. It stays queued, and owner-relay gets one `PERSON-QUEUED` report per head so it can record the owner's landing. An older head or an open hold still dequeues it.
- An exempt PR stays queued. An incident fix the squash would merge is left to the squash: untapped and squashable it is squashed, not dequeued; untapped and not squashable it is dequeued.
- A queued PR with a withdrawn tap or a conditional tap counts as untapped.
- A person who finds an untapped queue entry takes it straight to the coordinator, the only one who may dequeue; asking a desk first costs the minutes Trunk needs to land it.

### Merge mode

- At execution start, before the landing pass starts, the coordinator asks the owner once which merge mode the drive runs in and records the answer with `ccn answer add` using the question verbatim and label `program:<drive>`. It then writes the mode to `runner.json` as `landing.merge_mode`; the runner refuses to start without it, posts `MERGE-MODE-UNSET`, and re-reads it on every use.
- `every`: every PR waits for the owner's answer before it enqueues.
- `auto`: every PR enqueues once green and approved.
- `high-risk`: the runner reads each head's changed files once with `gh api --paginate repos/<repo>/pulls/<n>/files`, logging `FILES-READ-FAILED` and enqueueing nothing in that prefix if the read fails. Only a PR matching a glob in `landing.high_risk_paths` waits; the globs cover data/migrations, SQL, backfills, credentials/secrets, release pipeline, delete fences, storage formats, and infra rows. Every other PR enqueues once green and approved.
- Every verdict is read live from the cc-present store through `cc-present outcomes` and the coordinator's `go` records when it is used; no writer, row, card, or dashboard keeps a copy.
- A repository owned by the runner's own GitHub account (`gh api user`) lands once green under every merge mode with no merge card, though a reserved touch still asks.

### Tap rules and exemptions

- `landing.tap_rules` maps a register row key to path globs, such as `{"rule/<id>": ["go/sand/next/*", "infra/ci/src/pipelines/test/sand.ts"]}`. In every merge mode, a head whose changed files match a row's globs while `rulings.py rows` lists that row in force waits until an approval on the coordinator's board names some head of the PR. A coordinator `go` never stands in for that tap. The one class past it is a proven old-tree port, under [Old-tree forward port](#old-tree-forward-port).
- While waiting, the runner posts one `TAP-REQUIRED` blocker per head naming the row to the PR's lane, the coordinator, and each `landing.board_lane`, or one `TAP-UNREAD` warning to the PR's lane, the coordinator, and each `landing.desk_lane` if changed files, register, or board cannot be read.
- A head whose newest fidelity grade matches `landing.exempt_grades` needs no tap or `go` in any merge mode. The grades default to `pure port, no interaction` and `test-only or CI-only, no non-test code`; legacy `landing.port_phrase` replaces the first. A port grade admits nothing by its words alone: the gate also reads the proof under [Old-tree forward port](#old-tree-forward-port).
- Such a head goes straight to the merge queue once hold, fidelity, finder, build, and CI checks pass, sending one `EXEMPT-ADMITTED` note to the coordinator and each `landing.board_lane` when a tap rule names its files.
- Both classes and any `go` carrying `[exempt: <class>]` are verified against every file the PR changes against its base.
- A class is a test or CI class when it names a test, CI, `harness`, or fixture word, so `ci-tooling` is one; any other class is the coordinator's to claim.
- `fidelity-pipeline` reads both lists too. Its `ok` names a class the changed paths alone decide whenever every file the head changes fits it: `test-only`, `ci-only` and the default test-only grade take test and CI files, and `ci-tooling` adds the remote-builder, cc-remote and mise pins. The model never names those; it names only a class that takes judgment, such as `regression` or `owner-pick`. A head with one product file gets no path class.
- A test-only or CI-only claim keeps the tap on any file `lr/internal/gates/classes.go` does not read as a test or CI file. That file owns the list, and the list is an allowlist: only the CI tooling it names under `go/ci/`, `infra/ci/`, `.buildkite/`, `.github/` and `tools/ci/` counts, so a path nobody has listed, a new package included, keeps the tap, while a test file anywhere stays a test file. The claim opens the `TAP-REQUIRED` blocker with `TEST-ONLY-MISMATCH` naming the first such file.
- A `go` at an older head carries to a later head whose own diff is the same, and never by a fidelity judgment. The own diff runs from the newest merge-base the head shares with its base or the trunk, so a stack replay after the PR below lands changes nothing even before GitHub retargets the base, and the queued head stays queued.

### Old-tree forward port

Until cutover, every old-tree PR that is not the rebuild's own work is copied into `go/sand/next` as a port PR, whether it is still open or already on `dev`. An old-tree PR is one whose head changes a file under `go/sand/` outside `go/sand/next/`. Porting runs without being asked. A port lands faithful to its old-tree PR first, and any fix past that source is its own follow-up change after it (his answer 484d3dd). Where a port meets the rebuild's changes, the rebuild wins, and a port never regresses them (his answer d0643ed).

- **Where a port is owed.** The PR ledger shows it for every PR the pass has read since the field shipped, open ones included. Each pass's batched read of the drive's PRs reads their changed paths, and each row in `lr board rows --json` carries `port`, written with the row. The field starts at its install, so it misses old-tree PRs that merged before then. `old-tree-port-desk`'s walk of `dev` stays the check that covers every merged PR, and the field adds the open ones and the PRs that walk never sees:
  - `none`: the head changes nothing in the old tree and is no port.
  - `port of #<n>`: the row is itself a port; its head record says `port-of=<n>`.
  - `port #<n>, <state>`: an open or landed row's head record says `port-of=<this PR>`.
  - `in this PR: ...`: the same head also changes a file under `go/sand/next/`, which it names, so the PR carries its own port.
  - `no port: <reason>`: a `decide` naming the PR carries `--attr no-port=<reason>`, posted by `old-tree-port-desk` or root for an old-only or superseded change. The reason holds at the head it was posted on, so a new head with old-tree files is owed again.
  - `owed: ...`: none of these, naming the old-tree file.

  An owed row stays in `lr board rows` after it lands or closes, without `--all`. #50525 was an open, approved old-tree fix that a walk of merged commits on `dev` never saw.
- **A PR no drive lane opened.** Each pass also runs one GitHub search for the repository's PRs updated since its last search, walking forward 100 at a time. It keeps each old-tree PR that has no drive row and does not change the next tree too. These PRs never become rows, so they never reach his tab, his board, his update or an item. `lr board rows --outside` lists the ones whose port is owed, with the same `port` readings, and `--all` adds the rest. A `no-port` decide naming one counts the same way, until a new head. When a lane later opens or registers it, the PR moves to its row.
- **Who runs it.** `old-tree-port-desk` walks `dev` and reads the owed rows in the ledger and `lr board rows --outside` and keeps the census: ported, in flight, not ported, superseded, old-only. It gets a lane for each owed PR, and root names the lane if the desk cannot. Its brief carries a `Standing:` line, so the supervisor relaunches it when it is lost. Without that line the desk stopped silently and nothing relaunched it.
- **Keeping a port moving.** The landing pass restacks a port that conflicts with or falls behind its base, with no tap or `go`, unless a hold names it. A replay that conflicts pushes nothing and posts `RESTACK-CONFLICT` to the port's lane in the same pass, and a red port goes to its lane as `fix-red` in the pass that reads it red. Each row carries `moved_at`, the last time its head, grade or queue changed. The owner's update lists any open port with no such change for an hour under "Forward ports with no move in an hour", along with what it waits on.
- **What proves it runs.** Each walk posts `cci post --kind state --topic port-census "<counts>"`, also when nothing new is owed. The stall watch tells root once when the newest walk is more than 6 hours old. A drive that never posts a census is left alone.
- **When it ends.** At cutover and no sooner: the desk posts a last census with `--attr ended=cutover`, which stops the alert, then stands down. Plan step 12.0 reads "port census has no gaps" before the promote.
- **What a faithful port is.** The head does what its old-tree PR does, line for line, with that PR's tests. It may move imports and paths and use the next tree's names for the same things. It adds no fix, option or cleanup the old PR lacks, even one a grader or a review bot asks for: those wait for the follow-up. Fidelity grades a line past the source `missing`, starting `past its source #<n>:`, unless his own answer ordered that departure for this port. This overrides anything a repo guide says about a port's contents until the guide catches up: fidelity and the finder read a guide line the old PR's lines break as a follow-up owed.
- **A defect the old source also has.** A finding on lines the old PR has too, doing the same wrong thing, does not hold the port, whatever its severity. The finder writes it as `follow-up:` citing the old PR's lines, and its `ok` lists it as a follow-up owed. Fidelity names it in its summary and grades no `missing` for it. The port's lane then opens the follow-up: one PR on top of the port, opening `follow-up to port of #<n>`, graded like any PR. It also tells root that the old tree runs the same defect, and the old tree gets a fix only on root's word. A defect the old source lacks still holds the port like any finding: a wrong import, a dropped line, or next-tree wiring that fails.
- **A second fix in an area the drive already fixed.** A faithful port cannot make a class fix, so the finder's second-fix check draws no major on a body that opens `port of #<n>`. Its `ok` lists the class fix as a follow-up owed, naming the earlier fixes, and the follow-up PR takes it. That PR opens `follow-up to port of #<n>`, so the second-fix rule applies to it like any other PR. A port's own defect still holds the port, beside the listed follow-up.
- **A grade that already holds a faithful port.** A finder major or fidelity `missing` from an earlier pass, on a row the old source shares, needs no owner row beyond 484d3dd. `grader-appeals` checks the old PR's lines and asks for the coordinator's `finder-override #<n> at <sha>: <seq>` or `fidelity-override`, citing the grade, and names the follow-up. It sends a port lane to fix it in place only when the defect is the port's own. A second-fix major needs no appeal: a `head` record at the unchanged sha with the note `body edit` makes the finder read the body again, and a body that opens `port of #<n>` turns a second fix that was the one major into `finder: ok`, naming the follow-up.
- **The one case where a port cannot be faithful.** A line of the old PR would change or undo what a rebuild change or plan step rewrote. His word is that our changes supersede, so our change stays, the port leaves out or bends that one line, and the PR body lists each such line. Everything else in the port stays faithful. A shared defect that would regress our change is this case too, not a follow-up, because landing the port would break what already works. The lane writes `port-interacts=yes` when the port changes anything a rebuild change or plan step rewrote, beyond moving imports, and posts one card, recommending that our change supersedes the port. The PR lands only on his tap.
- **The words.** A port says so first, everywhere he reads it: `port of #<n>` is the first line of the PR body, opens each card and opens each reply about that one PR. A reply listing several PRs names each once, in its own line or card, and never flattens their `port of` words into one opening line. The finder takes `follow-up:` only on a body that opens that way. `lr ask-owner --port-of <n>` titles the card. `lr reply` opens a reply naming one PR with it from the row's `port-of`. Any other post gets a warning from the first-question hook when it names a port and leaves the words out.
- **What proves a PR is a port.** A word in its title proves nothing. The lane says so on the `head` record of every push, `--head <pr>=<was>..<sha> --attr port-of=<old PR> --attr port-interacts=no|yes`, and the record at the exact head decides. The newest such record wins. A restack with the same diff keeps its go, as any go does, and the supervisor carries the claim to the new head in a `decide` record, so a lane posts nothing for a patch-identical head. A head whose patch changed gates in full and needs its own head record.
- **A clean port needs no tap.** `lr landing go` posts `[exempt: old-tree-port]` past `landing.tap_rules` when all of these hold at the head:
  - A grader's newest fidelity at the head is `ok`, whichever class it names, and no grader's newest is `missing`; a counted `fidelity-override` stands in for either.
  - The head record says `port-of=<n>` and `port-interacts=no`; a record silent on interaction counts as interacting.
  - `#<n>` is a merged pull request, or a commit on trunk whose subject ends `(#<n>)`, since Graphite's queue landed some sources and left GitHub reading them closed.
  - Every changed file is under `go/sand/next/`.
  `gates.LandsOnWord` and `gates.PortRefusals` are the one read of this: the row's fold, the go, `lr landing gate` and the tap path all read them, so they cannot disagree. The go records what it read of the source and the files as facts at the head. Its row reads `ready_for_tap` false from the head record on, says it lands on his standing word, and no drawer offers it; `lr landing gate` prints `NO-GO-YET` rather than `TAP-REQUIRED`, and the go clears that line. If the go's read or the sweep's gate refuses the claim, the row turns ready for his tap and says why, and the gate prints `TAP-REQUIRED`.
- **Everything else stays.** CI, approval, holds, open cards and the row's other waits apply to a port like any PR, and the finder and fidelity grade it as above. A port with one file outside the tree lands on his tap.

### Test-flake fixes

A PR whose whole change fixes a test flake needs no tap. The owner gave that as a standing word, and this class sits beside the port class.

- **What proves it.** The lane names the test that flaked on the `head` record of every push whose patch changed, `--attr flake-fix=<test or red record>`; the supervisor carries it across a patch-identical restack, as for a port. A test-only PR without that record is not a flake fix: it keeps its own class and stays under `landing.tap_rules`. When the claim alone would land a refused head, the sweep sends its lane one `FLAKE-UNCLAIMED` note per head with the claim command, and `lr landing go` prints the same line.
- **What the gate checks at the head.** Every changed file is test or fixture code, which CI config is not, and no added line names a timeout or deadline. A file GitHub returned no patch for counts as unproven. The go reads `[exempt: flake-fix]`, past any `landing.tap_rules` row.
- **A helper the paths miss.** The gate reads paths, and a test helper package can sit where the paths read product code. Its `ok` grade naming the test-only class supplies the read: a reader checked that no product binary links the file. The pipeline names that class on a head carrying the `flake-fix` record, so a clean helper needs no desk. A file the paths read as CI, docs or lint config never passes this way, since a test-only grade over those comes from the paths alone.
- **Everything else stays.** CI, approval, finder, fidelity's `ok`, holds and open cards apply. A fix that touches product code lands on his tap.
- **The row.** Same as a clean port: `ready_for_tap` false, saying it lands on his standing word, until the gate posts its go or refuses, and a refused row returns to him with the reason.

### Exempt go gate

- The landing pass runs this gate itself, as lane `supervisor`, over every open head that is green, approved and graded with no go, hold or tap; no desk has to be alive for a clean head to get its go. It runs right after the pass reads its facts and before it classifies, gates and enqueues, so a head it posts a go for enters the queue in that same pass.
- The sweep reads what the pass already holds: the PR's body from the pass's read, a port source the store already records as merged, and one read of every record posted since the last sweep. A refused head keeps its verdict, and is not read again, until something its gate read changes: a record naming the PR, its head, base or body, an owner card naming it or named in its body, the runner's classes or tap rules, or a record the gate read expiring. A port whose source has not merged, and a head its review row has not read, are gated every pass. Its `SWEPT` line counts the heads it gated, the ones left unchanged, and the gos it posted.
- A head whose read fails, such as its port source's status or its changed files, is skipped that pass with one `GO-UNREAD` note to its lane, and the sweep goes on to the next head.
- A lane posts an exempt go only through `lr landing go <pr>... --lane <lane>`, never by hand or from its own script. A go counts as exempt only when it carries the `exempt-class` attr the gate writes when it admits the head. A go that types `[exempt: <class>]` by hand, from root or a `go_lanes` lane too, lands nothing on its words: the sweep gates that head in the same pass, posts the go that counts when the gate finds the class, and otherwise posts one `go.refused` to the PR's lane and the go's lane naming each reason, and the head lands on his tap. Without `--lane` the call is a dry pass: it prints, for the PR at its exact head, every reason the gate refuses an exempt go, and posts nothing. A lane writing an exempt go runs the dry pass first and reads the class from the files, never from a grade's words or a guess.
- The gate reads each PR's records once and posts only when all of these hold at the PR's current head on GitHub:
  - The finder-desk's newest grade is `ok`, or a counted `decide` (root, or `supervisor` carrying an override or counting a grader-appeals one) reads `finder-override #<n> at <sha>: <seq>` citing a record that exists.
  - Every fidelity lane's newest grade is `ok`, or a counted `decide` reads `fidelity-override #<n> at <sha>`; and some `ok` grade names one of `landing.exempt_grades` or `landing.go_classes`, which becomes the go's class. Only the gate reads `landing.go_classes`, so a class listed there never sends a head to the queue without a go.
  - No typed `hold` record holds the PR, and no open card on the owner's board names it or is named by it. A hold stops holding once cci reads it closed, whichever record closed it, so a lift that names no PR still lifts it; a `--before` replay counts only the closers among its own records. A card names a PR by its number in the card's id or title; a PR names a card by `card <id>` or the card's full id in its body, so a PR built ahead of a card drawn before it waits on that card. A head whose class lands with no tap skips its own tap card here, though no other card. A row ready for his tap refuses nothing: an exempt head lands under its class.
  - No `landing.tap_rules` glob matches a changed file, and no live go already names the head.
- A refused PR prints every reason and posts nothing. Text in an ask, answer, note, or blocker is never an override or a pick. A hold the coordinator writes as prose stops nothing, so it posts every hold as a `hold` record. `lr landing go <pr> --head <sha> --before <seq>` replays a past go over the records before it.
- Before promising a go or saying what admits a PR, dry-run `lr landing go` from a monorepo checkout. A class only admits through the go sweep, which runs in the landing pass, and a test-only fix can still land on the owner's tap. No lane holds `go-sweep`, `go-desk` or `landing-desk`, all stood down: `cci post --to` one of them accepts the record, nobody reads it, and a note says so.
- Landings go through the landing pass, and a go posts as the supervisor. Read a name's registry row before routing to it: a go posted through a nonexistent landing desk landed on an exempt-class fidelity grade.

**Reading a go.** A go is read fresh before every enqueue line.
- Re-tail cci for each line, and never cite seqs with `#`.
- Match `go #N at <head>`; a finder override is not a go.
- Read every `decide` naming the PR, since a same-minute `decide` can add fix-first conditions, and scan `decide` and correction text for free-text withdrawals.
- A dequeue check looks for a newer go before it dequeues.

**Root reads what it approves.** The `root_approval_reads` hook warns on a root override, go or adoption when this session ran neither `cci show` on a number it names nor `gh pr diff` on one, and on an approved plan when this session read no plan file. It warns and never refuses.

**Override quotes.**
- An override that names an owner row or answer quotes his exact words right after the key: `rule/<id>, "<his exact words>"` or `answer <id>, "<his exact words>"`. Read the row whole first with `lr rules row <key>`, every repeat and implication with it; a cut read misdescribes him.
- The override still counts whatever the check says. Root gets an `OVERRIDE-WARNING` when a quote is not in the row, starts or stops mid clause with no `...` marking the cut, has no row id, or its row or answer could not be read. Root reads those warnings. Never filter them out of a batch's output; read each. An override that quotes the owner's class-fix rule quotes it whole, not the half that excuses the finding.
- `lr rules quotes` runs the same check on stdin before a post. It catches a wrong key or a cut, not a whole clause used for the wrong thing, so the row's words still have to say what the override says they say.

**Gate inputs.**
- The gate reads the board's open cards, `landing.tap_rules`, the exempt classes and `go_lanes` from the newest `landing-gate-inputs` note the Mac's supervisor posts, within a minute of a change and every five minutes otherwise. It needs no drive registry or board daemon and runs the same on a Sprite. With no such note in 15 minutes it refuses to run rather than read the board as empty.
- The same note carries the lanes, fix lanes and incident clocks the landing mover reads from the lanes machine: each lane whose retirement chain ends at a lane that is not retired, and every fix lane not retired or retired in the last 14 days, each with the PRs it owns; the live incident clocks; and the board session a reserved row's card draws on.
- A supervisor whose `movers` lack `lanes` copies those lanes and their PRs into its own store every minute, so a refusal still reaches the lane that holds the PR and an incident fix still squashes. Until it has read a fresh note, it names no lane a fix.
- The lanes mover's checks, tick and relay run only where `lanes` is a mover, so the Mac and a landing-only Sprite never both run them.

### Reserved rows

- A head whose own diff matches a reserved row's `ops` waits for the owner in every mode. A grader's `touches <key>` line alone never holds a head, and a coordinator `go` never covers a reserved row.
- Its `MERGE-ASK` card or board row quotes the row's key and the first sentence of its words.
- After an affirmative tap or pick, the coordinator runs `rulings.py authorize --drive <drive> --row <key> --action <tap ts>` with the Slack `action_ts` or board row `at`, `--block <choice block> --option <picked option>`, or `--block <approval block>`. That records an `authorizes:<key>` answer so `check-pr` stops naming the row.

### Slack merge cards (`landing.merge_ask_surface`: `slack`, default)

- The merge card is the one automated Slack message the drive writes, posted at the root of his DM with its own thread. For each waiting PR and head, the runner posts or edits in place one merge card with `cc-present ask --session <coordinator's newest session in drive registry>`, logging `MERGE-ASK` or `MERGE-CARD-FAILED` to retry next pass.
- The card carries the question in plain words, title, author, size, review state, a live checks hyperlink, and buttons `Merge (Recommended)`, `Hold`, `Other`, and `Explain more` waiting on `merge:<n>:<short sha>`. It is never an `AskUserQuestion` and never hand-written by the coordinator.
- The card's PR block always reads the PR's live head. A card whose PR head moved is redrawn within a minute, and the landing runner refuses a tap on the stale head.
- On a `slack.action` directive:
  - `Merge`: the coordinator posts `cci post --drive <drive> --lane root --kind go --pr <n> --text "merge #<n> at <short sha>"`. Only the opening `go` or `merge` clause followed by `#<n> at <short sha>` pairs joined by commas or `and` names a PR; prose mentions, routing notes, or a `go` answered by a coordinator `withdraw --re` merge nothing.
  - `Hold`: the coordinator runs `cci post --kind hold --pr <n> --text "hold #<n> <why>"`.
  - `Other`: opens a Slack modal whose text reaches the coordinator as a capture and merges nothing.
  - `Explain more`: resolves nothing; the communicator answers within five minutes from the card body with `cc-present reply --block <waits id> --body <answer>`.

### Board surface (`landing.merge_ask_surface`: `board`)

- The runner posts no Slack card. A merge question is the PR's row on the review tab, read from `lr board rows`; nobody writes PR rows onto the board.
- Under `every`, the runner checks only heads a coordinator `go` names and bars every other open PR like a held one: no `stack-enqueue` call names or sweeps it in, and a go'd PR stacked on it waits, with one note to the coordinator per stack when its un-go'd parents change.
- After go'd trees enqueue, the pass's `ccx vcs pr status` read routes conflicts, red CI, and requested changes to lanes and runs accept checks on landable heads so `MERGE-ASK` reaches the row.
- A coordinator `go` claiming the owner's tap on a non-exempt head without a matching board approval draws one `GO-TAP-MISSING` blocker to the coordinator.
- Under `every`, the merge-mode check refuses a head with `TAP-REQUIRED merge mode every` until the owner's newest board verdict on any block and at any head approves the PR. A later rejection vacates any prior approval, dequeues a queued head on the next pass, and is re-checked before a direct merge.

### Tap carry and blanket approval

- A tap at an older head stays with the PR while nothing meaningful changes. The lane that pushes says what its push did on its `head` record, `--attr delta=<class>`, and the `head_records_name_their_delta` hook warns, never blocks, when the class is missing:

| Class | The push | His tap |
|---|---|---|
| `rebase` | moved the PR onto a new base and changed nothing else | carries |
| `review-fix` | answered a review finding with no change in behavior or scope | carries |
| `ordered-fix` | made a fix he ordered | carries |
| `behavior` | changed what the PR does | returns to him |
| `scope` | changed what the PR covers | returns to him |

- Each pass reads the heads from his tap to the current one, joined by the `<was>..<sha>` a head record names or else the head the row held before it. The first of these that holds decides:
  1. The PR's own diff is the same as at the tapped head. It carries, whatever a class says, since he approved exactly this diff.
  2. A head since his tap says `behavior` or `scope`. It returns.
  3. Fidelity judged a change since the tapped head. It returns, whatever class the lane gave.
  4. Every head back to his tap says `rebase`, `review-fix` or `ordered-fix`, or has the same PR diff as the head before it. It carries, naming the classes.
  5. Fidelity judged behavior and scope unchanged since the tapped head. It carries.
  6. Otherwise it returns until fidelity judges it, which it does for every untapped head with a tap behind it.
- A carried head still needs every other gate at that head.
- The row says which, for every host, through the `pr.read` record. A carried row reads as tapped, and `waits_on` adds "your tap at <head> carried to this head" with the reason. A returned row waits on him, and `waits_on` adds "your tap at <head> does not carry" with what changed.
- A carry also posts one note on topic `board-write`, `TAP-CARRIED #<n> from <head> to <head> by <class>: <reason>`, whose `op=carry` attributes `board_watch.py` applies with `cc-present verdict carry`. A failed apply is a `defect` record.
- An owner word that approves the whole board, such as "every pending PR is approved", reaches a landing path only as verdict records. The coordinator records it with `board_watch.py blanket --session <board> --word <seq> --at <time of his word> --program <drive>`, which approves each row of `lr board rows` that is ready for his tap and untapped, whose head was pushed by that time, at that head. A head pushed after the word waits on his tap at its own row. Check each row's up-time before citing an approve-all for a later PR: it covers only the rows that were up when he gave it.
- The runner, `mq.py gate`, `mq.py enqueue`, and a desk's own tap watch read those records, never the order that quotes the word.

## Holds, priority, dequeue, and requeue

**Holds.**
- Holds travel only as cci records or the GitHub `hold` label; no adapter takes a hold flag.
- The coordinator holds a PR with `cci post --kind hold --pr <n> --text "hold #<n> <why>"`. A record holds the one PR its text leads with as `hold #<n>`, else every PR its refs name; a later `#<n>` in the text is prose. A held row is not ready for his tap: it leaves his tab and Slack card, and his tap on a card he still has is refused and answered in the thread with the hold's reason. Posting such a hold warns the holder, naming each tap-ready PR it takes off his board.
- A hold placed while any message of ours asks him for a tap is `cci post --kind hold --pr <n> --attr until=tap --text "hold #<n> <why>"`, naming one PR. No other hold may be used for it, because a row that leaves his tab while we ask him to tap it refuses the tap he was asked for.
  - The row stays `ready_for_tap`, on his tab and Slack card, and its `waits_on` says the hold's reason. A tap from before the hold, carried or written over, does not count, and nothing lands.
  - His tap at the row's head ends the hold and writes the land order, with no second step: the landing host posts the `lr.tap` and a `note` resolving the hold to the lane that placed it. A tap at another head stands at that head and leaves the hold open.
  - Any other open hold on the PR still reads as held. A hold on several PRs is an ordinary hold, whatever its `until`.
- A held PR takes no finder or fidelity pass and stays out of the grade queue and the ungraded reports, so a restack under a hold costs no Sol. When the hold lifts, its head is graded as any new head is. Two heads are graded while they hold: one under an `until=tap` hold, since his tap card reads the grade, and one put first with `lr grade first`, since a first asks for that grade by name.
- Only a typed `--resolves` on a hold lifts it, and his tap on an `until=tap` hold is the one thing that writes that for you; a `go` never does.
- A new hold reaches the landing host's follower as the record arrives. The follower reads only the PRs the hold names and the rows it knows as queued, and a queued head under an open hold leaves the queue top first with every PR stacked on it and a `DEQUEUED` record per PR, without waiting for a pass. A hold on a head not in the queue does nothing there. Each pass still reads every open hold, so a hold the follower missed, or a PR stacked on the held one that its row did not yet read as queued, leaves on the next pass. A merge Trunk has already started can still win.
- A head the owner tapped under an ordinary open hold, the owner's or the coordinator's, is `held` and the coordinator's to decide within the hour. The pass sends the coordinator one `DECIDE` per head and hold that names the hold's record and the tapped head and asks it to lift the hold or tell the owner why not. Nothing lifts the hold for it, and a root go never lifts the owner's hold.
- An untapped head under an ordinary hold still waits on the owner's 12-hour clock and posts nothing.
- A lane whose stack sits on a held PR reads why with `lr board waits --pr <n>` and leaves release to the coordinator.
- An order to hold a PR becomes a typed hold record the turn it lands. A spoken hold or a `SendMessage` never reaches a polling land lane, which will enqueue PRs past it.
- A PR sent back to its lane for rework gets the `hold` label (or a cci hold record) in the same turn and leaves the watch list that turn. Removing the merge label or converting it to a draft is not enough, because Graphite can still land a PR it has already enqueued.
- Re-read every open hold at each lane retirement, each tap on a held PR and each checkpoint: a hold naming a lane to post its lift outlives that lane, and a hold mirrored from an old note can outlive its condition by hours. Read a hold's own lift condition before lifting it, and verify live state before opening a hold from an old note.

**Priority PRs.**
- When the owner flags a PR as priority or it blocks a release or user, the coordinator runs `lr board waits --pr <n>` in the same turn under D3 and routes whatever it names.
- Priority approval covers only the head the owner named; if the PR gains commits or scope, the coordinator gets fresh approval naming the new head.
- Never relay an ETA for a green priority PR.

**Dequeue and hand enqueue.**
- To take a PR out of the queue, the coordinator posts a `hold` on it: the landing host pulls it and every PR above it as the record arrives and posts `DEQUEUED`, or `DEQUEUE-REFUSED` with what the queue said. Resolving the hold lets it enter again. A queue cancel after a pull reads as that pull, never as an eviction.
- A hand `stack-enqueue --dequeue` records nothing and draws a warning naming `cci post --kind hold`.
- A hand `stack-enqueue` runs `lr landing gate <n>...` through the `lane_landing` hook and warns on failing checks, so never wrap `stack-enqueue` in a custom script. The hook follows a program held in a shell variable on the same line, and reads no script file; the landing pass dequeues what gets past it.
- In a Forge-AI/monorepo drive, every hand landing, whether `gh pr merge`, `ccx vcs merge`, `gh stack merge`, `stack-enqueue`, `ci trunk submit`, or a `merge` label, is blocked when that gate prints `TAP-REQUIRED`, the same tap read the runner makes, so a carried or standing tap counts. For a green, approved head with no tap the gate asks the go sweep's own decision, which reads the grades from the bus rather than the landing store, so a grade that arrives mid-pass counts in that pass, `gates.ExemptGo` at that head: a refusal prints `TAP-REQUIRED` with every reason and names each `go` record that does not count at that head, and an admission prints `NO-GO-YET`, which warns and does not block, since the sweep posts the go that same pass. A read that fails refuses nothing and warns, naming the failure. The refusal names the PR and head and points to a card asking the owner's tap; it and the warning both name `cci post --kind ready --pr <n>`, which hands the PR to the landing pass from any host, a Sprite included.
- A hand landing that merges outside the queue, whether `gh pr merge`, `ccx vcs merge`, `gh stack merge`, or a `PUT` on the merge endpoint, is warned when the gate, run with `--direct`, prints `QUEUE-UNTESTED`: the queue's own check has not passed at that head. This warns and does not block, so a tapped hand merge can still land untested.
- `desk-runner`, `merge-train`, and desk landings, an incident lane's plain squash, and merges in other repos such as yasyf/* are not hand landings. A gate that does not read warns, names the failure, and lets the landing through. A Sprite with no drive `lr.json` cannot read the tap, so there the hand landing is refused and the refusal names the missing file.

## Go to queue

- A green PR a coordinator `go` names at its head enters the merge queue in the next pass after that `go`, and a head the supervisor's sweep gives a go enters it in the pass that posts the go. Only a go from root, the supervisor or `go_lanes` counts, and an exempt one only when the gate posted it. `lr landing go --lane` from any other lane says so when it runs: the gate admits, this record does not move it, and the sweep posts the go that counts. When the sweep will not post a go for a head on a standing word, or a head a lane posted a go for, it posts one `go.refused` record per reason to the PR's lane and the go's lane, and both stores fold it into the row. That includes a head it does not gate at all: a lane's go on a red, unapproved, held, draft or tapped head gets a `go.refused` naming which.
- Every head a `go` or exemption admits gets, from the pass that admits it, one `QUEUED` or `REFUSED` record per change of outcome. `REFUSED` names the gate line or check, `REFUSED unsettled:` with the last branch if unsettled, or `gating off: <why>` when gating is off.
- When the bot's newest `ai-review` run reads `Held`, the line states the bot holds it on N blocking findings and routes one record per head to the owning lane to fix or accept them. `Waiting for restack` reads as waiting for a restack push. Neither is a missing verdict.

## Queue down

- Trunk failures or unreadable queue states stay `BLOCKED` or `UNKNOWN`. The runner never merges around the queue and runs no automated orphan, own-red, or flake recovery. Native retry is a manual `stack-enqueue --flake` after inspecting verification.
- A tapped hand merge around a down queue is not blocked: it draws the `QUEUE-UNTESTED` warning and lands untested. Whoever takes that risk merges serially: rebase each PR on the latest dev, grade it green on that exact base (never on a no-file-overlap shortcut), and re-read the inbox before each `gh pr merge`.

## Trunk health and redundant skips

**Trunk health (`landing.trunk_check`).**
- With `"trunk_check": "buildkite/test"` in `landing`, every pass reads that status over the trunk's last 50 commits.
- A red run failing a job that a PR build also runs posts a `DEV-RED for <n> min` incident to the coordinator and `drive-watchdog` on topic `dev-red`, naming the jobs, first red commit, and landed PR, and stating no PR can land until fixed. It repeats every 30 minutes while red, then posts `DEV-GREEN` once when it passes. Until a fix PR is named, each post also asks the orca desk for `dev-red-<first red commit>-fix`, as a `POST-MERGE-RED` does: a live lane gets the repeat, and a finished one is launched again. The fix lane names its PR with `cci post --topic dev-red --pr <n>`.
- The runner holds no row, because required checks hold what cannot land. Close a dev-red lane, or say dev is green, only after reading the build at dev's current head in that turn: an earlier `DEV-GREEN` or a summary line is not evidence, because one green build does not end an intermittent fault. The pack refuses any `"READY"` report or coordinator ruling calling a head `"green except"` a job, a job `"known red"`, or a trunk `"red by design"`.
- When a red run fails only jobs no PR build runs, or a newer finished trunk build read through `landing.ci_build` fails or soft-fails a job more often than the build before it, the runner posts `POST-MERGE-RED` to the coordinator, `landing.desk_lane`, and the lanes of PRs landed between the two builds. Landing continues and a fix is owed.
- A failed Buildkite read does not silence the trunk. `DEV-RED` and `DEV-GREEN` come from GitHub's statuses and still post, and a red whose jobs cannot be read reads as `DEV-RED`, since nothing proves its jobs run only after a merge. Job comparisons and flake reports wait for a pass that can read the build.
- A 401 escalates as `not a red build`, followed by the auth failure. A failed GitHub history read posts nothing that pass. After any background pass that escalated succeeds, the supervisor posts one `<pass> recovered` note to the lane that heard the failure.

**Buildkite read token (`landing.ci_token_command`).**
- It is the argv that prints this host's read-only Buildkite token, the same command `bk.json` names as `token_command`. The supervisor hands its value as `BUILDKITE_TOKEN` to the trunk watch's `ci_build` and landing-build reads, the release-build watch's `buildkite_cli`, the page watch's `pages.build`, the build-failure gate's `build_failures.annotations`, and `release_cli status` and `release_cli live`.
- A write, such as an incident's rebuild, keeps its own credentials. No read uses a person's `bk auth login`, since that OAuth token lasts one hour and `bk auth token` prints it without renewing it.
- Each reader runs the command at its first read and keeps the value in memory until Buildkite answers 401, then runs it again at its next read; the trunk watch also runs it once per pass. The value is never written to a file or a record.
- Without the setting, those reads run nothing and their log line or escalation names it. A 401 reads as `auth failure: Buildkite refused the read token landing.ci_token_command printed`.

**Flakes (`landing.flake_lane`).**
- A trunk job that fails and passes on retry, or a flaky notice in `landing.flaky_channel`, posts one `FLAKE` defect naming the failing test, or the job when no test failed. A job whose log shows a GitHub quota or rate-limit refusal and no failing test posts `INFRA` instead, so it is never called a flaky test.
- The defect goes to the live lane of the `flake_lane` stem. With none live, the supervisor writes the stem's standing brief to `<state>/<stem>/brief.md` and launches the next number from it, and root gets one `note` it need not act on.
- The fix is due in two hours. Each pass, an open record with no live lane on it gets the next lane of the stem, handed over in a `note`.
- A repeat failure of the same test or job attaches to the open record as a `note`. A resolver that names no merged PR reopens the record to the same owner. A merged PR closes that record only, never the class part of an owner order ([communicator-brief.md](communicator-brief.md#route-meta-orders)).
- An open record past its due time, or with no live lane on it, becomes an ask in the program ledger, so it shows in the owner update and on the Your asks page.
- Automatic retries stay, and a retry that turns the build green never closes the flake.

**Merges past their own red (`merge_own_red`).**
- In every session, drive or not, the pack blocks `gh pr merge` (with or without `--admin`), `gh stack merge`, and `ccx vcs merge` when a finished failing check on a PR the merge lands is that PR's own: red on the PR and green on the base.
- A yasyf/* PR, in a native stack or not, merges with `ccx vcs merge <n> -R <owner/repo> --admin --match-head-commit <full head sha>`. It refuses a head other than the one named, and a finished failing check on any PR it lands unless `--red-on-main <run>` names a default-branch run that fails the same job of the same workflow. A stack member goes through GitHub's asynchronous merge API, which also lands every open PR below it, so `ccx vcs merge` refuses one with open PRs below unless `--with-downstack` is given, then names each PR it landed with its merge commit. `--admin` is refused on a repository the signed-in account does not own.
- `gh pr merge` lands one PR outside a native stack; on a stack member GitHub refuses it with "must be merged using the asynchronous merge REST API". Never loop `gh pr merge` over stacked PRs: before GitHub retargets a child's base, it merges the child into its parent's branch, exits 0 and reads `MERGED`. Where branches chain by base alone, merge one PR, read the next one's base until it is the trunk, then merge it. `gh stack merge` given a PR lands it and every open PR below it in the native stack; given a stack number or nothing, it lands the whole stack.
- `lr own-red` makes that decision for every path by reading the same check of the same workflow on the base in one batched call: the nearest finished run at or below the PR's merge base, because path filters and `[skip ci]` leave most base commits without one. The files a failure names play no part, since a change breaks tests it does not touch.
- Green there blocks, listing each PR, check, and failing job's link. Red there is inherited, so the merge runs with a note naming it. No readable run lets the merge go with a note saying the base branch state is unread. A pending check never blocks.
- A failure that may be a flake still reads as own, and the block names the step: rerun that check with `gh run rerun --job <id>`. The read takes each check's latest attempt, so a passing rerun clears it.
- In a lane's own window, the same `lr own-red` call first reads the lane's records through its cursor, `cci tail --cursor <lane> --reader <lane>`, and blocks while one another lane addressed to it has no closing reply, no resolving record, and no answer from the lane. The lane's own records never count.
- The read is never paged: one block lists every such record, then the oldest and newest 20 of the rest with a line naming how to read those between, and the next attempt has read them. A window with no lane binding reads nothing.

**Redundant skips (`landing.ci_build` and `landing.ci_grade`).**
- With `"ci_build": "tools/ci bk api /pipelines/{pipeline}/builds/{number}"`, the runner reads each passing Buildkite build at a green head once.
- A head whose only passing build is Graphite's `"Redundant within its stack"` skip is graded `unproven: redundant skip, child #<n> <state>`, sent to the lane as a blocker, and kept out of merge cards and enqueues along with every PR above it. It waits until a full build of that pipeline passes on a commit containing the head, either at the stack's tip or named by the lane in a `full run green <build>: #<pr> #<pr>` note or report.
- A failed build at the head itself always wins as `unproven: its own build <url> failed`.
- With `"ci_grade": "tools/ci bk api -X POST -d '{\"commit\":\"{commit}\",\"branch\":\"{branch}\",\"ignore_pipeline_branch_filters\":true}' /pipelines/{pipeline}/builds"` also set, when no tip build covers or is running for the head, the runner points branch `ci-grade/<pr>` at the head and runs `ci_grade` once per head, logging `CI-GRADE-FAILED` to retry next pass on failure. The head reads `unproven: redundant skip, ..., so full build <url> grades <sha>` until that build passes.

## Incident fixes and release on landing

**Incident fix merges.**
- An alert fix is a PR from a lane named `incident-` or `outage-`, or from a fix lane the orca desk launched for an alert. Alert fixes land by plain squash through `gh pr merge <n> --squash --match-head-commit <head>`. A fix that is a native stack member lands through `gh stack merge <n>` instead, never the queue. They never enter the trunk queue or use `--fast`, and they merge under every merge mode and even during `checks_wait_for_go`.
- The squash runs in the pass of the host whose movers include `lanes`, the Mac, so an alert fix lands while the landing host is down. The landing host only keeps alert fixes out of the queue; it never merges one or posts about one. A merge that loses to another host, read as `MERGED` on GitHub, records the fix as merged and posts nothing. A merge that lands posts the `landed` record its row folds.
- An alert fix whose lane has a live orca-desk incident clock, that an open incident record names by the PR or its lane, or that he approved, merges in the pass its head is approved and green. Any other alert fix waits `deadlines.alert_wait_minutes` (default 60, his pick) from the first minute its approval post reached him at that head, which `lr board card` records when cc-slack says the post stands, then merges in the first pass its head is approved and green. His post names that minute in Pacific time and is redrawn in place when it moves. Hold stops the wait; Approve lands it at once. A PR belongs to the lane the registry owns it by, never the lane that pushed it last, so a restack the supervisor posts leaves a fix an alert fix. A new head whose own diff is the same, by the rule his tap carries by, keeps the minute; a real change starts the wait again when its new post reaches him. No wait, fence or freeze ever holds a fix an open incident names. The squash merges only a head whose required checks are green at that head, rebased or not, so what lands outside the queue has passed the same test the queue would run on it against dev. A fix rebased onto dev waits while CI runs at its new head and merges once green.
- The fidelity desks grade an incident fix row first in their queue and never waive the plan check. The finder grade does not gate the merge; the runner logs `UNGRADED-30` once if no `finder: ok` arrives within 30 minutes after landing.
- `check-pr --incident` refuses the merge with one cci `RULINGS-REFUSED` blocker to the PR's lane and the coordinator if it names `RULINGS-UNCITED`, `RESERVED-NO-ANSWER`, or `TEST-REMOVED`; the fix merges in the first pass they clear.
- Just before `gh pr merge`, the runner reads open cci `hold` records again. If no open incident record names the fix or its lane, a hold refuses it. While an open incident record names the fix or its lane, a hold logs `INCIDENT-HOLD-WARNED` once, the fix merges anyway, and the incident clock launches no Opus fallback.

**Release on landing (D20).**
- `landing.release_owner` in `runner.json`, a cci `--to` address such as `peer:<session>`, is required for the landing pass to start.
- The runner judges each landing once. On its first pass after startup or upgrade it records existing landings as history, waits until `landed_at` is set, and closes pre-start landings silently when `tools/ci release live --json` already shows the squash or a later commit live.
- A `merge-fast` landing whose body has no `Release-With:` line is read once and sends nothing. An incident fix owes components from its `Release-With:` lines, or else the release its squash needs, whatever its label.
- When any incident fix lands, or when a row lands with the `merge-fast` label and a `Release-With:` line in its body read with `gh pr view <pr> --json body`, the runner posts:

`cci post --drive <drive> --lane desk-runner --kind release --to <landing.release_owner>,root --topic <pr> --pr <pr> --id release:<pr>:<step> --text "<squash, components, start the release now>"`

- A post cci refuses or that prints no sequence logs `RELEASE-FAILED` once per step and retries next pass.
- The release owner starts the release and posts `cci post --drive <drive> --lane <lane> --kind release --pr <pr> --target <component> --build <build url> --text <release id>` on this drive. `reference/release-lanes.md` covers runner claims, announcements, and pre/post-cut `pup` and `endstate.py` checks.
- Until settled, the runner posts one `blocker` to `<landing.release_owner>,root` every `deadlines.release_start_minutes` (default `10`), or every `deadlines.incident_release_minutes` (default `3`) while an incident clock runs. A landing first read after a step has passed gets the `release` record then and its first blocker at the next step.

**What settles a release.**
- A `merge-fast` landing settles on a `release` record on this drive stamped at or after landing from any lane but `desk-runner` naming a component with `--target` or the PR with `--pr`; a `fix-live` record from another lane naming the PR or component; or a coordinator `--re <seq>` or `--resolves <seq>` on the release record or a blocker.
- An incident landing settles only on a `release` record carrying both `--build` and `--target <component>`; a `fix-live` record from another lane; or a coordinator `--resolves` naming the release record, a blocker, or `RELEASE-HELD-AFTER-LANDING`. `--re` does not settle it.
- An open coordinator `hold` record naming a landed incident PR or addressed to the release owner draws one `RELEASE-HELD-AFTER-LANDING #<pr>: landed <sha> at <time>; its cut is owed now, the other PR gets its own cut when it lands` line, while blockers keep their cadence. Only a `hold` record is a hold: a `decide`, `decision`, or `go` that mentions a PR or says `"wait"` is not. A coordinator `--resolves` on the hold ends it.
- Once the settling record names `--build`, each pass reads `tools/ci release live <id> --json`. When every component reads live, it reads `tools/ci release live --json` for the build that deployed each component on each env that release reads current, and posts one `RELEASED <component> <version> (<build>)` record naming those builds, with `--kind fix-live --target <component> --build <url>` and key `release:<pr>:live`, to the coordinator alone. The settling record's build is never cited for its own sake: a build cancelled with nothing applied, whose commit a later build carried live, reads live too. It is the only publish line the coordinator forwards to the owner.

**Cut clocks (`CUT-SILENT`).**
- A cut order is a coordinator `go` or `decision` addressed to a release lane, carrying `--target <component>`, whose text orders a cut or `release start`. A D20 `release` record with no build yet whose landing names `Release-With:` components is also a cut order. Prose mentions that omit `--target` do not open a clock.
- The release lane answers an order with a `release --build` or `refuse` record that `--resolves` the order. The clock stays open until landing settlement, the coordinator `--resolves`, or another lane's `release` or `fix-live` record closes it.
- If the release lane posts no `release --build` or `refuse` record within `deadlines.cut_minutes` (default `4`) while the clock is open, the orca runner posts once to the coordinator: `CUT-SILENT <lane> <component>: no CUT or refusal <n> min after GO #<seq>; hand path: tools/ci release start --manual <component> --env <env> --commit <sha> --emergency`. That post claims nothing, and `--emergency` only skips red dev checks.
- After a 1-minute wait, the runner queues `manual-release-<component>` on opus high from `reference/manual-release-brief.md`. It withdraws the launch as `WITHDRAWN cut:<key> manual-release-<component>: <why>; nothing was launched` if the order is resolved or answered during that minute, and relays any later `already ships <component> at <sha> or later` refusal to that lane.

## Desk cadence and commands

Create the ledger once at spawn and put its id in every brief. A resumed coordinator in a new session runs `drive.py start --drive <id> --ledger <id>` to join the existing drive, and runs `drive.py end` only when the drive is over.

`--register-lane <lane>` names the lane that classifies owner captures. While it is listed in `cci who`, it receives the register gate's unclassified-capture blocker and holds the `classify`, `names`, `authorize`, and `supersede` seats; otherwise the blocker goes to the coordinator, which resumes the lane by `SendMessage`.

```sh
LEDGER=$(ledger.py init --title "desk: $DRIVE")
drive.py start --ledger "$LEDGER" [--orca-run <run>] [--state-dir ~/.claude/scratch/<slug>] [--program <plan slug>] [--register-lane <lane>]
drive.py desk --name <desk lane> --terminal <handle> [--environment <name>] [--supervisor]
ledger.py ask     --ledger "$LEDGER" --text "<verbatim>" --lane lightning-eh --accept "<acceptance check>"

# on each report: record it and grade the current head; reports are not a gate
ledger.py report  --ledger "$LEDGER" --drive "$DRIVE" --pr 21221 --head <sha> --lane lightning-eh --verdict clean --ask ask/000001
ledger.py report  --ledger "$LEDGER" --drive "$DRIVE" --repo yasyf/cc-skills --pr 375 --head <sha> --lane skills-tool --verdict clean
ledger.py ruling  --ledger "$LEDGER" --drive "$DRIVE" --lane p2-edge-rows --pr 20284 --text "land without the document form" --options "A land|B hold|C close"
ledger.py enqueue --ledger "$LEDGER" --drive "$DRIVE" --kind idle --pr 21221 --head <sha> --lane lightning-eh --text "done"
ledger.py inbox   --ledger "$LEDGER" --drive "$DRIVE" --take
ledger.py register --ledger "$LEDGER" --lane lightning-eh --branch-prefix lightning/ --pr 21221

# every 3 minutes, staggered :00/:01/:02: reconcile every tracked current head
ledger.py refresh --ledger "$LEDGER"
ledger.py reconcile --repo "$REPO" --ledger "$LEDGER" --checkout "$CHECKOUT"
ledger.py route   --repo "$REPO" --ledger "$LEDGER"
ledger.py stale   --ledger "$LEDGER"

ledger.py route --repo "$REPO" --ledger "$LEDGER" --pr 21221 --job "plan comment missing for this head"
ledger.py hold  --ledger "$LEDGER" --pr 20284 --reason "waits on #20314" --hours 4
ledger.py hold  --ledger "$LEDGER" --pr 20290 --reason "argo cutover waits on the owner" --hours 12 --stack
ledger.py hold  --ledger "$LEDGER" --pr 21052 --reason "dev-red:infra-plan-observability" --hours 6

# hot-set conflicts and a gone lane's hot-set rows go to the train, its other rows to the red desk
ledger.py gone  --ledger "$LEDGER" --lane lightning-eh
ledger.py route --repo "$REPO" --ledger "$LEDGER" --train merge-train --paths 'infra/ci/src/pipelines/release/**' 'infra/engine.ts' --fallback red-desk

ledger.py summary --repo "$REPO" --ledger "$LEDGER" --drive "$DRIVE" --checkout "$CHECKOUT"
ledger.py show    --ledger "$LEDGER" --asks
ledger.py live    --ledger "$LEDGER" --at "$(date -u +%FT%TZ)" --text "release 37 deployed"
ledger.py drop    --ledger "$LEDGER" --ask ask/000002 --reason "owner withdrew it"
ledger.py answer  --ledger "$LEDGER" --ask ask/000003 --text "<the reply>"

lr board waits --pr 21221
lr landing gate 21221
cci post --kind hold --pr 21221 --text "hold #21221 the owner pulled it"

drive.py end
```

At spawn, arm the watch under Monitor at its maximum timeout and re-arm on every expiry:

```sh
ledger.py watch --repo "$REPO" --ledger "$LEDGER" --drive "$DRIVE" --checkout "$CHECKOUT" [--priority <n>]... [--shard <lane>,<lane>]
```

| Verb / flag | Behavior |
| --- | --- |
| `ledger.py watch` | Re-reads non-terminal PR rows each pass, records transitions, settles landings and closures through `landed`, writes a P0 inbox message, and prints `P0 #<n> <event> <lane>` for every ejection and for `conflicting` or `red` on a priority, desk-labeled/enqueued, or previously queued row. A PR with no entry in the snapshot when a pass first saw it that already landed or closed emits nothing and settles nothing (`landed` settles it), while a standing `conflicting`, `red`, or `ejected` still emits once. `--shard` keeps its own snapshot at `~/.cache/ccn-ledger/<ledger>.<lanes>.watch.json`. |
| `ledger.py label` | For repos with no enqueue script; takes `--pr <tip> --expect-head <sha> --checkout <path>`, or `--all-clean` for the sequential fallback sweep. Records `label_refused`, `label_refused_head`, and `label_refused_at` on refused stacks and clears them on success. Run `label --dry-run` once on a repo before the first live label. `unlabel --reason` records why a label came off and blocks re-labeling that head. |
| `ledger.py stale` | `--minutes N` sets the clean-report age threshold, default `30`. Also names every open PR opened at least `--hours` ago, default `60`, as `aged #N <h>h <lane>: <state>`; land or close it before the 72-hour full re-review applies. |
| `ledger.py route` | Without `--pr`, sweeps every red or conflicting row, reads the first failing line from the Buildkite log, prints the message to send, and records it; `--dry-run` prints only. With `--train <lane> --paths <globs> --fallback <lane>`, conflicting rows matching `<globs>` and any matching row of a `gone` lane go to `<train>`, a `gone` lane's other rows go to `<fallback>`, and a live lane keeps its own reds. Skips rows held `dev-red:<step>` until expiry. |
| `ledger.py show` | `--asks` prints each ask with its computed state, or `[pending]` while it is too fresh to grade; `--red` answers a lane asking what it owns. The desk never sends the raw table. |

## Message contracts

### The lane report, three lines

A lane sends this when it ships a PR, pushes a new head, or reaches a terminal state on one; registration goes separately:

```
PR #<n> <head sha, full> <clean|red|conflicting|held>
<what changed on this head, one line>
<what is next, one line, or "none">
```

- For an owner ask, append an optional fourth line, `ask <id>`, such as `ask/<seq>` or its number.
- A lane with the ledger id and cci drive records the report itself: `ledger.py report --ledger <id> --drive <drive> --repo <owner/name> --pr <n> --head <sha> --lane <name> --verdict <v> --text "<line 2>" [--ask <id>]`. `"READY"` is `--verdict clean`; an unknown ask id exits non-zero.
- A PR row's key is `<owner/repo>#<n>`, defaulting `--repo` to the ledger checkout's repository. Duplicate reports with the same PR, head, and verdict are dropped.

Reporting lanes follow four rules:
- Pass the full pushed head sha, and verify `git ls-remote origin refs/heads/<branch>` matches local `HEAD` before reporting, so a detached worktree does not silently push nothing.
- Re-report every new push in the same turn, and check `ccx vcs pr status <n>` before pushing to a reported PR. If it is `queued`, ship the delta as a new stacked PR instead of amending a branch the queue already snapshotted. After the `merge` label goes on, the branch is not the lane's to amend and further work is a new branch off a fresh trunk.
- Report `conflicting` while mid-rebase and push nothing labeled; report `clean` again only on the rebased head. Put prose in a cc-notes note cited on line 2.
- Running `ccx vcs ship` records each PR it opens through the hook and posts `opened` on cci in cci 0.19.0 or later, carrying the raw PR body and the repo, branch, base, head and parent attrs the review row folds. A later push posts `head` the same way, with the body when the call changes it. A drive lane's ship that would open a PR with no `--pr-body-file` is refused before it runs. A Codex worker gets neither from `ship` and runs `drive.py record --opened` after it, or on a Sprite `cci post --kind opened --pr <n>`, which the fold carries to its lane. `drive.py record --pr N=SHA` also posts that head as a `head` record, so the head it registers in the drive ledger is the head the landing gate reads; a push after the first runs `drive.py record --pr N=SHA` again.
- A push nobody's hook saw still reaches the row. The GitHub webhook posts each pull request event as a `github` record on drive `github-pr`, and every host's fold moves a row it already holds to the pushed head, lands it on a merge, closes it on a close, and takes an edited description into for_users and risk, keeping the row's lane. A PR with no row gets none. The receiver also posts each required check's result there: a required failure turns the row's ci red, a required check running again at the head, such as a rerun, turns it pending, and every required check passing at the head turns it green, even while an optional check still runs. The landing host's next GitHub read and the next event both overwrite ci, so the newer one stands. A push to the trunk lands there too: once dev moves, no row offers a tap until a host has read mergeability after the move. The landing host reads in its pass. The host whose movers include `lanes`, the Mac, reads every open row in its own pass whether or not a landing host is up, so a pushed head reads its CI within a pass while the landing host is down: one GraphQL query per 25 rows carries head, state, base, reviews, mergeable, labels and the checks at the head, and it posts a `pr.read` for each row whose read changed. `lr board read` makes the same read from any host, and `lr board read <PR>...` reads only the PRs it names. Any other host that is up makes one mergeable-only read. Each read posts one `trunk.read` record per move, the mergeable-only one naming each row GitHub calls conflicting at its head, every host folds it, and a row keeps its last mergeable while it waits. Only repositories whose webhook reaches the receiver post these: Forge-AI's org webhook does, and yasyf repositories have none yet.
- Grading never waits on a landing read. A host that runs lanes and grading starts its pass from its own check every 3 minutes, not behind the lanes tick. A row the landing host has not read yet still owes its grades at the head the row holds. Each pass start reads that one PR on GitHub and starts nothing, spending no attempt, when the PR is closed, is a draft, or has moved off that head; the row's next head opens the next pass.

### Owner asks in the ledger

The coordinator records every owner ask in the turn it arrives per R8 in `SKILL.md`, with `ledger.py ask --ledger <id> --text "<verbatim>" --lane <lane> --accept "<acceptance check>" [--standing [HOURS]]`, which prints `ask/000001 <lane>: <text>`. Lanes record their own sub-dispatches the same way with `ledger.py ask` before dispatch and `ledger.py answer` when the reply lands. A PR carrying an `IN-PR` ask takes no distinct second ask; `report --ask` refuses it, so put the new ask on a stacked follow-up PR.

| Ask status | Meaning and transition |
| --- | --- |
| `LIVE` | Every linked PR has landed and `ledger.py live --ledger <id> --at <ISO>` recorded the shipping pipeline running at or after the latest landing, or the ask was pinned with `ledger.py ask --state live`; a per-ask state outranks the ledger-wide `live --at`. Only `LIVE` counts as done; before then, the coordinator says `"in #N, not live yet: <blocker>"`. |
| `LANDED-NOT-LIVE` | Every linked PR has landed, awaiting `ledger.py live`. |
| `IN-PR` | At least one linked PR is still open. Escalated in the summary once older than 60 minutes. |
| `IN-PROGRESS` | No linked PR, but `asked_at` or the latest `ledger.py progress --ledger <id> --ask <id> --note "<what moved>" [--artifact <url or id>]...` entry is under 30 minutes old, or under `HOURS`, which defaults to `24`, for a `--standing` ask. |
| `LOST` | No open or landed PR and at least 30 minutes past `asked_at` or the latest `progress` entry, or pinned with `--state lost`. Dispatched by the coordinator in the same turn. |
| `STALE` | A `--standing` ask whose `asked_at` and latest `progress` entry are older than `HOURS`. |
| `BACKLOG` | Marked with `ledger.py ask --ledger <id> --state backlog --ask <id> --note "<where it went>"`, while `--state open` restores computed state. |
| `dropped` / `answered` | Terminal states set by `ledger.py drop --ledger <id> --ask <id> --reason "<why>"` when withdrawn or `ledger.py answer --ledger <id> --ask <id> --text "<reply>"` on a question or sub-dispatch. |

### One-line `RULING NEEDED` format

Recorded by `ledger.py ruling --drive <drive> --text "<question>" --options "A <..>|B <..>|C <..>"`; two rulings with the same question on the same PR are one ruling. The desk forwards it immediately to the coordinator, which answers with the letter alone and never replies to an idle notice:

```
RULING NEEDED: <the question, one line>; options: A <..> / B <..> / C <..>
```

### Routing, desk to lane

Printed by `ledger.py route` and sent once per PR, head, and failing job:

```
DESK #<n> <head9>: <failing job, or "rebase onto <base>">
<the first failing line from the Buildkite log, when the desk read one>
Push the fix as a new head on the same branch, then send the 3-line report with `ledger.py report`
(PR, head, verdict); the merge label waits on that head reading green and merge-clean.
```

### The desk to coordinator summary, every 30 minutes

- `ledger.py summary --drive <drive>` runs `reconcile` over every non-terminal row in the ledger first, then prints the summary.
- `merged/h` counts squash commits on the trunk, the repo's default branch, inside the window, read from the git log by `ledger.py landed`, which fetches the trunk and settles closed rows by tree equality and then a squash subject ending `(#n)`.
- `p50` is the median minutes from each landed row's last report to its landing over the window, or `-` when nothing landed.
- The desk sends the summary unchanged, preceded by any non-clear lines from `lr watch prompts show` as `reference/watchers.md` describes, and followed by `lr handoff standing --drive <drive>` output per I6 in `SKILL.md` and `reference/owner-rulings.md` plus `cursor #<seq>`.

```
desk <stamp> | open N | merged/h N | labelled N | held N | rulings N | p0 N | routed N | stale N | p50 report→landed Nm | lost N | landed-not-live N
LOST ask/<n> <lane>: <text>
STALE ask/<n> <lane> standing 24h: <text> | progress 1500m: <note> <artifact>
LANDED-NOT-LIVE ask/<n> <lane>: <text>
IN-PROGRESS ask/<n> <lane>: <text> | progress 12m: <note> <artifact>
IN-PR 61m ask/<n> <lane>: <text>: #<pr> <blocker>
stale #i 47m <lane>: <blocker>
waiting: ungraded #a | refused #b | red #c | held #d
merged: #a #b
labelled: #c #d
P0 #e <lane>: <text>
RULING NEEDED: <question>; options: A / B / C
held #f: <reason> until <stamp>[ EXPIRED]
routed, awaiting a new head: #g #h
```

Prompt-watch lines preceding the ask lines take this shape:

```text
WAITING-ON-PROMPT <name> <role> approval|question <n>m terminal=<handle>: screen saved at <path>[; supervisor <state>]
PROMPT-STALE <name> <role> <n>m terminal=<handle>: <detail>
PROMPT-UNKNOWN <name> <role> <n>m terminal=<handle>: <detail>
PROMPT-UNREACHABLE <name> <role> <n>m terminal=<handle> environment=<name>: <detail>
PROMPT-WATCH-DOWN <drive>: <detail>; start it with `<command>`
```

- Submit any worker-directed ruling from prompt watch with `lr relay <lane> <text>`, and never send it to an orca-desk lane.
- Ask lines sit outside the ten-line cap on desk lines; when more desk lines remain, the ten-line block ends with a pointer to `ledger.py show`.
- Beside the summary, the coordinator reads `cci digest --drive <drive>` over its default 24-hour window, widened with `--since`, and `cci state --drive <drive>`.
- An ask older than its lane's cadence is dispatched under R6, and a blocker on a finished lane's PR gets a fresh fix lane. The coordinator reads `cci state --drive <drive>` before asking for a head or contract.
- When the 30-minute summary arrives, the coordinator updates its task list and sends nothing back.
