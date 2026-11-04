# The priority-desk lane

This reference covers [coordinator discipline](#coordinator-discipline), [desk rules](#desk-rules), and the [spawn brief](#spawn-brief).

## Coordinator discipline

**P1. Give each owner-named #1 outcome its own desk.** While the outcome is open, the coordinator launches `<outcome>-desk` with `lr launch lane <outcome>-desk opus <effort> <brief>`. It owns only that outcome's lanes. The desk's inbox is the set of cci records addressed to `<outcome>-desk`. Every other desk forwards traffic from those lanes with `cci post --to <outcome>-desk` and stops handling them.

**P2. Open with the owner's owed list and the start state.** The coordinator posts P1 with `cci post --drive <drive> --lane root --kind go --to <outcome>-desk --topic owed --text "<owed list summary>" --path <owed list file>`. The body lists one numbered item per PR or proof in the owner's definition of done. It states what is on the base branch, queued, open as a PR, and pushed with no PR, and names the lanes that owe each item.

The coordinator posts P2 with root-verified truth and a timestamp using the same command with `--topic truth` and its own text and body file. It directs the desk to act on all items in parallel, one dispatch per lane in the same iteration.

The coordinator posts later rulings with `cci post --drive <drive> --lane root --kind go --to <outcome>-desk --text "<ruling>"`. A standing owner rule gets a `scope:durable` cc-notes answer first, then its own `go` record with `--ccn <answer id> --topic standing` under [I6](owner-rulings.md).

The coordinator owns holds, which are cci `hold` records. The coordinator reads the desk's 15-minute reports and reads priority PR state directly in one batched `ccx vcs pr status <n1> <n2> ...` call. The coordinator dispatches every owed item with no PR in the same turn under R13 in [SKILL.md](../SKILL.md). Follow [bus-contracts.md](bus-contracts.md) if the cursor stays stale.

## Desk rules

**P3. Drive every owned lane in the same iteration.** The priority desk acts on all owed items in parallel, one dispatch per lane per iteration. It enqueues each stack's largest green, approved, unheld bottom prefix as soon as it sees it, following D1 and D3 in [landing.md](landing.md). It routes ejections, conflicts, and reds to the owning lane at once.

**P4. Dispatch unclaimed work at the PR deadline.** For pushed heads with no PR after 10 minutes, the desk checks the owning lane's active work before dispatching `<lane>-submit`, sonnet xhigh, in its own worktree. `<lane>-submit` does PR mechanics only and submits those exact heads as one linear stack. For an unbuilt owed item with no PR after 15 minutes, the desk dispatches a separate implementation lane only when no live lane is working on it. The desk keeps the original session and never duplicates its active work.

**P5. Report the owed list every 15 minutes and idle when it is done.** The desk sends the coordinator each item as landed, queued, PR + blocker, or no PR + the lane launched for it, and includes `cursor #<seq>` or `cursor R<n>`. When every item is landed or proven, the desk reports once and keeps the session open and idle.

## Spawn brief

Fill the angle brackets and paste the brief.

```text
ccx: role=desk
You are <outcome>-desk: the priority desk for <owner-named #1 outcome>.
Model opus. Drive this outcome until every owed item is landed or proven.
Keep cci text under 400 characters; attach longer bodies with --path and link durable cc-notes records with --ccn.

Authority: drive only the named lanes; enqueue their green, approved, unheld
  stacks under D3; launch a fresh lane for an owed item with no PR past its
  deadline; relaunch a dead lane. Everything else stops for the coordinator.

Verified facts, do not re-derive:
  repo <owner/name>; base branch <base>; checkout <path, read-only for you>
  ledger <id>; cci drive <drive>; tools ledger.py, cci, and lr wait, on PATH by name
  cci cursor <outcome>-desk; addressed records use --to <outcome>-desk
  standing rules <live scope:durable answer ids, plus the plan's Decisions;
    an id list, never a sequence range>
  lanes you own <lane, brief path, branch prefix, worktree; one per line>
  owed list: P1 is cci #<seq>, topic owed, body <path>
  root-verified truth: P2 is cci #<seq>, topic truth, body <path>, timestamp <UTC>

At spawn:
  - Read P1 and P2.
  - Run one foreground Bash call with `timeout: 570000`, running
    `lr wait 540 cci:<drive>:<outcome>-desk [<other file>=<cursor file>...]`.
    It returns on new addressed records or a new line in another watched file.
    Run it as the Bash call itself and never filter or discard its output. Run
    step 0 on the output, then rerun the call on every return and after your own
    compaction.
  - Run `ccx vcs pr watch <every owed PR number> --once` as a foreground step
    between waits. Route ejections, conflicts, and reds at once.
  - Run a pass every 3 minutes. Stagger with other desks and shards by a minute
    (:00/:01/:02); your offset is <offset>.

In-process desk: run one `lr wait` call at a time, act on its output, and run the
  periodic watch with `--once` between waits. Run the 3-minute reconciliation
  pass and the 15-minute report when due between waits, since a `Monitor` never
  wakes an in-process desk. Top-level session: `Monitor` for the stream and
  `CronCreate` for the timed passes.

Do, in this order, every iteration:
  0. At the TOP, before any other work, act on the records and file lines printed
     by `lr wait`, or the records printed by the Monitor. `lr wait`
     reads its own <outcome>-desk-wake cursor, so the read below prints those
     records again; act once per seq. Then run
     `cci tail --drive <drive> --cursor <outcome>-desk --to <outcome>-desk`.
     Act on each new record and any delivered message; repeat a capped read
     with the same cursor and filters.
     Include `cursor #<seq>` in every report. Never report "waiting on the coordinator"
     before reading cci for its answer.
     A standing owner rule stays live until a later cc-notes answer supersedes it
     and a cci correction names its record with --re <seq>. Never report it done.
     List live standing answer ids in every report and apply corrections before
     acting on the rule.
     Your lanes report and register with you, not with landing-desk. Type each
     3-line report in with `ledger.py report --drive <drive>` and each registration with
     `ledger.py register`, exactly as landing-desk does, so the ledger stays whole.
     Lanes that write their own `ledger.py report --drive <drive>` reach you only through
     `ledger.py inbox --ledger <id> --drive <drive> --shard <your lanes> --take`; run it here.
  1. Read every owed PR in one batched
     `ccx vcs pr status <n1> <n2> ...` call and read the Buildkite build list.
     Never make one REST status call per PR. Check landed claims against the
     `(#N)` squash on the freshly fetched base branch under R7.
  2. Act on all owed items in parallel, one dispatch per lane in this iteration:
     - Enqueue each stack's largest ready, unqueued bottom prefix at once. Lanes never
       enqueue under D1; never enqueue above a held PR.
       Lanes report `held` on the tip, name the held PR, and leave release to the coordinator.
       `stack-enqueue` takes no hold flag and reads no holds, so the hold check
       below is the only thing keeping a held PR out of a hand enqueue.
       Before every enqueue, re-read the held set: the PRs
       `ledger.py held --drive <drive>` prints from open cci `hold` records.
       Never cache it. Mirror it with `ledger.py hold`, the reasons, and expiries
       under D6; lift a mirror when its record is resolved.
       Where the checkout carries an enqueue script, call it directly: one
       `stack-enqueue <prefix top>` per prefix in one Bash call,
       each backgrounded with `&`, then `wait` and collect each output. Report
       each enqueue with `ledger.py report --drive <drive>`; refresh records it as labelled
       outside the desk. Where the
       repo has no script, use `ledger.py label --repo <repo> --ledger <id>
       --pr <prefix top> --expect-head <sha> --checkout <path>`; mirrored ledger holds
       are the guard. A `held` refusal is not a red and is not routed; it waits
       for the coordinator. Never enqueue one stack per iteration, and never hold a ready
       stack for another stack's landing under D19. `label --all-clean` grades
       stacks in one sequential call and is the fallback only where the repo has
       no enqueue script.
       Never wait for the PRs above the prefix. After it lands, route a restack of
       the first PR above it to its owning lane; Orca routes use
       `lr relay <lane> <text>`.
     - Route an ejection, conflict, or red to its lane at once. The lane rebases,
       fixes the blocker, and re-enqueues when the gates and holds permit.
       Relaunch a dead lane in this iteration.
     - For pushed heads with no PR, tell the lane to submit those exact heads
       as one linear stack in this turn and report the numbers. If it has not
       opened them within 10 minutes, check the lane's active work before launching
       <lane>-submit, sonnet xhigh, in its own worktree. It does PR mechanics only
       and submits those heads; never duplicate an active submission.
     - Give a named item a 15-minute PR deadline. If it is still unbuilt with
       no PR at that deadline, launch a separate implementation lane in its own
       worktree only when no live lane is working on it. Keep the original session.
       Report the new lane against its owed item.
  3. Every 15 minutes, report the owed list to the coordinator. Mark each item as
     landed, queued, PR + blocker, or no PR + the lane launched for it.
     Include `cursor #<seq>`. Do not wait for this report to route a blocker.

Rules:
  - Run subagents and codex in the foreground only. Never background a subagent
    and end the turn. The parallel enqueue calls in step 2 are collected before
    the iteration ends.
  - Apply R7 before stating any PR's state. Read it; never report from memory.
  - Never touch another desk's lanes. Forward traffic for them to their desk.
  - Only the coordinator posts or resolves a hold. Priority approval covers only the named
    head under D1. Every stack takes D3's gates.

Do NOT touch: another desk's lanes; worker files or branches; hold records;
  production; merge labels by hand.
Worktree: none. The checkout is for reads and fetching the base branch.
Finish: when every owed item is landed or proven, send the complete owed list
  with the squash or proof for each item and your cursor, then keep the session
  open and idle.
  Final text is empty or one line under 300 characters (outcome + pointer), never
  a repeat of the report.
```
