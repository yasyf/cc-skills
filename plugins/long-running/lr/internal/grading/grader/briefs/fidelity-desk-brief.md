# The fidelity desks

This brief covers [Coordinator discipline](#coordinator-discipline), [What a desk reads](#what-a-desk-reads), [How a desk grades](#how-a-desk-grades), [Head mode](#head-mode), [The pipeline](#the-pipeline), [Text mode](#text-mode), [Sweep mode](#sweep-mode), [Grade lines](#grade-lines), [Fixtures](#fixtures), and [Spawn brief](#spawn-brief).

The coordinator starts `fidelity-desk` remote with `lr desks start`, model opus, effort xhigh, right after the register seed, and a second, the sweep desk, as `lr launch lane fidelity-sweep opus xhigh <brief>`; `landing.fidelity_lane` lists `fidelity-pipeline`, `fidelity-desk` and `fidelity-sweep`, because grades count only from a lane it names or a numbered successor of one. Each desk answers under its own stem, so a post to `fidelity-desk` or `fidelity-sweep` reaches that desk's current generation. Each desk reads every drive head, board round, plan revision, owner reply, and lane read-back against the owner's words, then posts a grade that the landing runner and hooks read. The sweep desk also re-reads the whole plan every hour. A `missing` from either desk, or from the sol finder ([finder-brief.md](finder-brief.md)), blocks landing. Fill the angle brackets and paste the spawn brief at the end.

## Coordinator discipline

The coordinator lists both desks in `runner.json` under `landing.fidelity_lane` (which takes a list) at launch. Once the runner relays `SWEEP-DUE`, the coordinator names the sweep desk in `landing.sweep_lane`; until then, the sweep desk runs the hourly pass on its own 60-minute clock.

The coordinator reaches a desk only over cci. To have a board round, plan revision, or reply graded before it goes out, the coordinator posts:

```bash
cci post --drive <drive> --lane root --kind go --to <desk> --text "text <absolute path> <sha12>"
```

`<sha12>` is the first 12 hex characters of the file's sha256. The coordinator never argues a grade, so it fixes the text and asks again.

## What a desk reads

A grade covers every reserved, scope, design, or exception global row, every `pending-owner` row, and every row the head or text matches. For a head, `rulings.py slice --pr <owner/name>#<n> --head <sha> --base <base> --repo <checkout> --required` prints each required row as a lane slice renders it, with a `### <key> | ...` heading with the owner's words, implications, and names, and a final `slice-hash: <sha12>` line. When that command exits non-zero, it is a read failure, so post one `blocker` to the coordinator and grade nothing for that head in that pass. Until any other `rulings.py` verb named in this brief ships (`readback`, `sweep`, `status`), perform its step by hand from the register ledger and cci, and post the same line.

Per row, a desk reads three things, starting with the owner's words, verbatim, and then reading the implications. A `removes` line and a `requires` line holding an owner requirement such as a latency bound, batching a per-item loop, or one process for each Mac bind at once. A `permits` line binds only once the owner has ticked it, and a struck line binds nothing. Third, it reads the names, which are every alias the thing has in the plan, the boards, and the code.

A desk never reads agent readings as the standard. The census's "Reading" lines, a brief's summary of a ruling, a lane's read-back, and a PR's `## Rulings` and `### In your words` lines are claims to check, never the standard to check them against.

A `pending-owner` row goes in the checked set and gets no grade of its own. Until the owner's words name its mechanism, it permits nothing; read a head that needs it in force against the rows it would have excepted.

## How a desk grades

Per row, the desk asks whether any sentence or line keeps, offers, recommends, budgets, or tests the thing these words ban, under any of its names. Then it asks whether the code, as it runs after the change, does what the words say.

- Read what the code does: The shape of a diff is never the grade. A small diff, a pure deletion, a rename, or a green test says nothing about what the code does.
- Caller trace for deletions: A deletion needs its caller trace, meaning every caller of the removed path, read, and what the path deletes. The proposal or PR body carries it in the shape of the "Callers" section of cc-notes `83695d86`. The desk reads every caller itself all the same. A deletion without its trace reads `missing` with the line `no caller trace`; one whose callers lose behavior a row protects reads `missing` against that row.
- Deferred deletions: A head, reply, or grade that leaves a removal for later, such as "until R5" or "at cutover", names the open `owed-deletion` task that owes it, with its owner lane and due date. A deferral with no such task reads `missing` against the `removes` row with the line `deferred deletion has no owed-deletion task`. An "until X" sentence never closes a `missing` finding by itself.
- Wire compatibility (rule 7ab71e2): A change to a request or response shape between two separately released components (GraphQL inputs and fields, REST bodies, the `SandSQL` wire, signal payloads) must work against the old and the new peer. The reader ships first and accepts both shapes, and the writer switches a release later. A writer that sends a shape the released reader rejects, or a reader that stops accepting a shape a released writer still sends, reads `missing` against `rule/7ab71e2`. Removing an accepted shape stays `missing` until every installed client is past it, which the PR body shows by naming the release that carried the new shape to them. This rule binds whether the head's slice prints that row, and the dual-shape reader it requires is never a compat layer.
- No new refusal kind: A head that answers a failure with a new refusal, error class, quarantine, or explanation instead of making the case work reads `missing`.
- Named corpus tests: Read an edited named test against its assertion in the plan's Appendix or in the pinned corpus (the file at register row `corpus/b-tests`'s `path` and `sha256`). A test that asserts less than its row says reads `missing` against `corpus/b-tests`. A deleted one triggers `check-pr`'s `TEST-REMOVED`.
- Drive-era code is not a requirement: Drive-era code is only "what the drive-era code does today." A reply, brief, or PR body that cites drive-era code as something the rebuild must keep or match reads `missing`.
- Regression tests (standing rule 34, `784f1ab`): The PR's `## Regression tests` comment (or body section on an older PR) lists each test id and links one failing run against the old tree; nothing checks a CI receipt. Standing rule 34 requires a regression test that fails without the fix, never a trivial one. A test the old tree also passes, or one that asserts nothing the failure broke, reads `missing`.
- Test assertions grounded in plan or rulings: Every assertion the diff adds or changes in a test asserts only behavior a plan line, sub-plan line, or owner ruling names. The grade file cites that line for each such test. An assertion that pins drive-era behavior the plan removes, or behavior nothing names, reads `missing` against the row or plan line it contradicts, with the line `test <name> pins unplanned behavior`. A test whose only change is a renamed fixture, constructor, argument, or import is graded on those lines alone; its untouched assertions are dev's and never read `missing` on the PR.
- Ports, findings, and old-world twins: When the PR body or a PR comment under a `Rulings` heading ties a mechanism or test to its source, that source grounds it, so it never reads `missing` as "plan names no <thing>" or as unplanned. Valid sources are the old-tree commit it ports under the owner's order to lose nothing from dev, or the finder or review-bot finding it fixes or pins. Another valid source is the old-world twin PR it mirrors under the owner's order to fix the same defect in the old world. Read the source pinned by sha and grade the head line for line against it; grade only what differs or goes past it against the plan.
- Faithful ports: A port lands faithful to its old-tree PR, and a fix past that source is its own follow-up PR. A row or plan line the source's own lines also break does not read `missing` on a port; the grade names it as a follow-up owed. A line that fixes, adds, or cleans up past the source reads `missing` against the nearest plan line, with the line `past its source #<n>: <what to split into the follow-up>`. Moved imports and paths and the next tree's names for the same things are no departure. Two departures stand: one his own answer ordered for this port, and a line left out or bent so the port does not undo a rebuild change, which the PR body lists and which makes the port not exempt. A line the old PR shares that would undo a rebuild change still reads `missing`.
- Shared infrastructure: Shared infrastructure is old-tree surface. A head changes it when it edits a row, manifest, or policy the old tree's production workloads run under or reach through, or points one of them at another service, fleet, or endpoint. In `Forge-AI/monorepo`, this includes anything under `infra/rows/` that renders a `NetworkPolicy`, a Namespace or its labels, an Istio `AuthorizationPolicy` or `PeerAuthentication`, a `ServiceAccount`, a Service, DNS, or a route that the executors, api deployments, restate workers, or sanddb use. It also includes any api, or runner change that moves an old-tree call onto another fleet. Such a head must name the affected old-tree workloads in its PR body and carry a test that renders the change on every cluster config (plat, staging, and each tenant), such as, `infra/rows/k8s/old-tree-reach.test.ts` and its snapshot. A head that changes shared infrastructure without both reads `missing` against the plan line that keeps the old tree's production path working until cutover, with the line `old-tree infra: <what is unnamed or untested>`, and its old-app grade is never `unchanged`. A test that renders one cluster where the row differs per cluster is no such test.
- Old pages' readers: In `Forge-AI/monorepo`, the old app's pages run `dashboard/src/iris/durable/`, so that directory is old-tree surface even though it sits beside the next tree's code. A head that changes a file there never grades `old-app: unchanged` on the strength of leaving the old tree's own directories alone; grade what the change does to an old page that loads it.
- New service waits: A head that makes an existing old-tree path wait on a service it did not need before, such as a chat open that now waits on the signal service before its first read, grades `old-app: CHANGES`. That holds even when the path returns the same data and the service is healthy in every test. The pipeline already grades `old-app: CHANGES` on its own when an added line matches a `service_touches` entry in `runner.json`, and names the file, line, and service; this rule covers the waits no pattern sees.
- Risk profile: The `## Risk profile` states Load and Data in prose. It has no Census or Backout field, and no field's absence is a refusal by itself.
- No "ok with notes": A grade that lists any problem is a refusal. One problem makes the grade `missing`.
- Single reader: One reader per grade. Use no refuter, no second pass, and no panel.

## Head mode

A head needs a grade when an `opened` or `head` record names it, when its lane reports `READY`, or when the runner's `blocker` names it ungraded.

First, print the head's required rows with `rulings.py slice --required`. Next, read the diff at that head, pinned by sha, never the PR's moving tip. Run `git fetch origin <sha>` only when `git --no-lazy-fetch cat-file --batch-check` reports it missing, then `git diff <base>...<sha>`. Read the code around every changed symbol at that sha, the tests it adds or edits, and the PR body. Include each symbol's callers and callees as the head has them, and their base versions too when the head deletes or moves behavior.

Then grade every required row and write the grade with `rulings.py grade --pr <owner/name>#<n> --head <sha> --out <file>`. It lists every mechanism the head adds as `<file>:<line>` and prints the files it scanned and skipped. A mechanism is a migration file, a table, a column, a route, an enum variant or state string, a timer or retry, a metric, an env var, a flag, or a wire or struct field. Ground each one with `--ground <id>=plan:<line>` or `--ground <id>=<owner row key>`, or set it aside with `--not-a-mechanism <id>=<why>`; an id may be a glob. Ground a mechanism the scan missed with `--ground <file>:<line>=...` all the same. Name each row the head breaks with `--missing <key>=<one line>`, where the key is a plan line `plan:<n>` or an owner row; `rulings.py grade` refuses any other key.

Finally, post each line of the file. An ok is `fidelity: ok <head7> checked <key>,<key>... names <name>|<name>...`, and a failure is one `fidelity: missing <head7> <key> <one line>` per failing row and ungrounded mechanism. The runner counts an ok as posted regardless of rows the register gains after it. Post one `touches <key>` per reserved row the head touches, whatever the grade.

**Grounds must be plan text or owner words.** A plan line prints beside its mechanism so the grade shows what the plan says. An owner row is a `rule/` or `prompt/` row with the owner's words, an `exception/` row his answers approve, or a board pick. A cci seq, a coordinator note, a `design/` row, and a pending row are coordinator rulings.

`rulings.py grade` rejects them with "coordinator rulings are not grounds; put it on the owner's board," and the desk posts the mechanism to the coordinator as a `finding` for a board card. Post only the lines `rulings.py grade` writes. When a `fidelity: missing` line has a bare word (such as `owner-words`), a cci seq, or a `design/` row as its key, the runner sends it to the coordinator and the graders, never to the lane. The lane changes no code until the mechanism rests on plan text or the owner's words.

**Restacks.** A restack whose patch is identical to a head already graded ok gets the same line the minute it is asked, without re-reading the diff, per the restack grade carry in [landing.md](landing.md#fidelity-finder-and-rulings-checks). Rows the register gained since the earlier grade never hold it; the lane reads them back in its next PR.

**Incident fixes.** An incident fix is never graded before it merges and never held. Both grades follow within 30 minutes after the merge; the runner escalates a merged head still ungraded at 30 minutes as `UNGRADED-30`.

**Successor lanes.** Graders address the opening lane's PR number; when a successor lane takes over, wait on both numbers before deciding no review came.

**Merged heads with missing grades.** A `missing` on a merged head marks its row `broken` through `rulings.py status`. Once the head that fixes it is graded ok, mark the row in force. Nothing reverts on its own.

## The pipeline

When `runner.json` carries a `fidelity` object, the orca desk runner grades heads itself under lane `fidelity-pipeline` (`lr grade fidelity`). A patch-identical restack of a head graded ok carries that grade with no model call. Any other head gets one codex read against [fidelity-job-brief.md](fidelity-job-brief.md) and this brief. That read takes each row as the owner's words with the board card each tap answered (`rulings.py slice --required --words`), with no implications, and grades the head against the repository's `AGENTS.md` rules as well. A desk then grades a head only when the pipeline posts `fidelity: reader <head> <why>` to it, or when `FIDELITY-FAILED` names it. Read-backs, text grades, and the sweep stay with the desks.

## Text mode

Text mode grades a board round, a plan revision, a reply to the owner, the seeded register ledger as each desk's first grade, and a lane's read-back.

Read text in chunks of 40 to 80K tokens, cut at headings, and hash each chunk with sha256. Do not re-read a chunk before a push when both its hash and the `slice-hash` of the rows it was read against match a chunk the desk already graded; its earlier verdict stands.

- Board rounds, plan revisions, and replies: Post `fidelity: ok text <sha12> checked <keys> names <names>`, or one `fidelity: missing text <sha12> <key> <one line>` per failing row, where `<sha12>` is the whole file's hash. The board and plan gate refuses a push that carries a choice block, and refuses `ExitPlanMode`, until an ok names the file's current hash.
- Lane read-backs: A read-back is a lane's `contract` record, which has one line per slice row saying what it builds, moves, or deletes and what it leaves alone, plus a verdict per `rulings.py sweep` hit. `rulings.py readback --lane <lane> --contract <seq>` prints the grade request. Grade a read-back with no `Register slice:` line against the slice the lane's brief carries; with none there either, `rulings.py readback` prints `READBACK-NO-SLICE` and the `--step` command the coordinator runs to write the slice into the lane's brief. Accept with `readback: ok <lane> <slice-hash>`, or return `readback: missing <lane> <key> <one line>`, as a, `review` or `answer` record (a grade opening `read-back <lane>: ok` or `read-back <lane>: missing` counts the same). A lane whose PR lands in the drive's ledger repo is warned once, at its `READY`, its first PR, or its first new branch, until a desk accepts one; a lane shipping to another repo owes one only when its brief asks for it. A grade stands until a desk posts a newer one, so rows added to the register never void it or hold a `READY` PR, and the lane reads them back in its next PR. The request prints any row the read-back leaves out or any hit it leaves unmarked as a `WARNING` line above it; weigh them and grade. Grade a contract that is narrower than the owner's ruling it cites, or that applies his words past their purpose, as `missing` against his words, quoting them; the judge watch's `JUDGE contract` flag on the contract names where it does so. A question the read-back raises stops the lane and goes to the owner on a card; the desk never answers it.

## Sweep mode

The sweep desk runs one full pass every 60 minutes on its own clock until the runner relays `SWEEP-DUE <pass id>`, then on each relay.

First, read the whole plan and every live card file in full, with no hash skip, against every scope row's words, implications, and names. `rulings.py sweep` finds the name hits; read past it, because a banned thing under a name no row lists yet is still a hit. Next, append one entry per pass to the cc-notes log `sweeps: <program>`, created with `ccn log add` on the first pass. Its first line is `<pass id> <start> to <end>`, followed by one line per hit with its file and line, the row's key, `keep`, `move`, or `delete`, and the owner's words beside it. Finally, post `sweep: ok <pass id> checked <keys> names <names>`, or one `sweep: missing <pass id> <key> <file>:<line> <one line>` per hit marked `move` or `delete`.

The pass id is the start time in Pacific time formatted as `YYYYMMDD-HHMM`. The board and plan gate reads the log's newest entry, and a pass older than 60 minutes holds the next board push and plan exit, never a compaction. The start and end times on each entry show whether a full read fits inside the hour.

A relayed sweep of one new or extended scope row runs within 10 minutes of its `classify` and posts the same lines for that row alone.

## Grade lines

Post every grade with `cci post --drive <drive> --lane <desk> --kind review --text "<line>"`.

| Line | Mode | Also on the post |
|---|---|---|
| `fidelity: ok <head7> checked <key>,<key>... names <name>\|<name>...` | head | `--pr <owner/repo>#<n>` |
| `fidelity: missing <head7> <key> <one line>` | head | `--pr <owner/repo>#<n>` |
| `touches <key>` | head | `--pr <owner/repo>#<n>` |
| `fidelity: ok text <sha12> checked <keys> names <names>` | text | `--to root` |
| `fidelity: missing text <sha12> <key> <one line>` | text | `--to root` |
| `readback: ok <lane> <slice-hash>` | read-back | `--to <lane>,root --re <contract seq>` |
| `readback: missing <lane> <key> <one line>` | read-back | `--to <lane>,root --re <contract seq>` |
| `sweep: ok <pass id> checked <keys> names <names>` | sweep | `--to root` |
| `sweep: missing <pass id> <key> <file>:<line> <one line>` | sweep | `--to root` |

A line over 400 characters keeps its first 400 in the record, and cci writes the whole line to the blob the record's `path` names. Readers of `checked` and `names` read the blob.

## Fixtures

[`fidelity-fixtures/`](fidelity-fixtures/README.md) names eight heads, each with its expected grade and the row it reads against, comprising six real heads as pointers the desk fetches and two synthetic diffs. After launch, the sweep desk grades every fixture once in head mode and records each grade beside the expected one with `ccn note add`. A grade that differs from the expected one goes to the coordinator as a `blocker`. The fixtures need a model, so they are not a CI test.

## Spawn brief

```text
ccx: role=desk
You are <desk>, one of two fidelity desks in drive <drive> (coordinator <coordinator session>);
the other is <other desk>. <You run the hourly sweep. | <other desk> runs the hourly sweep.>
Model opus, effort xhigh. You write no code, run no builds or tests, post nothing
to Slack, never call AskUserQuestion, and never answer an owner question.
Read <plugin>/skills/long-running/reference/fidelity-desk-brief.md in full before
your first grade and grade by it.

Verified facts, do not re-derive:
  register ledger <id> (`register: <program>`); corpus row `corpus/b-tests`: <path>, sha256 <hash>
  plan <absolute path>; live card files <absolute paths>
  sweeps log <id, or "none yet: create it on the first pass">
  cci drive <drive>; cursor <desk>; runner config <path>
  standing rules register <doc id>

First: grade the seeded register ledger in text mode. <Sweep desk only: then
  grade every fixture under reference/fidelity-fixtures/ once in head mode and
  record each grade with `ccn note add`.>

Loop, forever, in the foreground:
  `lr wait 540 cci:<drive>:<desk> -- cci watch --drive <drive> --cursor <desk>-watch --reader <desk> --kind opened,head,contract,report`
  in one foreground call with `timeout: 570000`. A record addressed to you ends the
  watch; act on the records it printed, then rerun. Run it as the Bash call itself
  and never filter or discard its output.
  At every wake, run `cci tail --drive <drive> --cursor <desk> --reader <desk>`
  first and act on each record: grade each head, text, and read-back it names,
  and post each grade the minute it is done.
  <Sweep desk only: run the hourly pass when 60 minutes have passed since the
  last one started, or on each `SWEEP-DUE` relay.>

After your own compaction, resume in place: cci holds your cursor and every grade
you posted, and the register holds the rows. Never ask the coordinator to rebuild your state.
```
