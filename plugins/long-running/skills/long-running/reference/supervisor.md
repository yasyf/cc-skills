# A quiet lane cannot happen

Owner, Nov 2 2026, 7:08 PM: "it's not acceptable to just keep saying lanes are going quiet and then bump them. You need to figure out why they're going quiet and make that physically impossible by adding structural fixes." Owner, Oct 31: liveness, restart, successor and stall needs go into one supervisor, and the hacks it replaces are deleted.

This section maps to rev 7's supervisor section (Forge-AI/design-docs#265).

## The one rule

**Every turn of a launched lane ends on a typed record, and the record says who resumes it.**

| The lane's newest record | What it means | Who resumes the work |
| --- | --- | --- |
| `done`, `stopped`, `stand-down` | The lane ended on purpose | Nobody. The row retires, a retired row never gets a successor, and nothing the lane posts afterwards revives it. A PR it still owns goes to the coordinator if it breaks |
| `ask`, `blocker`, or `blocked` to a lane | It waits on that lane's answer | The answer, through the supervisor |
| `blocked` naming a gate: `--pr`, `--target`, `--lane-ref`, or a `card-` id in the text | It waits on an event | That event, through the supervisor |
| Anything else | The turn has open work | Stopping is refused, and the refusal prints the commands |

A lane refused twice with nothing new posted gets a `blocker` written for it, with its last words, and then stops. So the bad state "the turn ended and no record says why" has no path: the model either posts the record or `cci hook stop` posts it.

Three kinds of lane stop freely, because each one's wake already exists: the root thread, a session with a cci channel subscription, and a lane whose brief has a `Standing:` line. A thread with no `spawn` record is not a launched lane.

## One row per mechanism

The first ten come from the brief and root #348144. The gate classes are what `quiet-lanes-cause-1` found for the first ten lanes it read (#348083): all ten ended on `end_turn` at a gate nobody held.

| # | Mechanism | Structural fix: why the state is unreachable | Where | Test |
| --- | --- | --- | --- | --- |
| 1 | A lane ends its turn with open work, on a background wait or a promise | The one rule above. A background wait is a `blocked` record that names its gate | `cci hook stop` and `cci hook subagent-stop` | `internal/hook`: a lane with a `report` as its newest record cannot stop; a second stop writes the blocker; `done`, an open `blocked` and a subscribed session stop |
| 1a | The turn dies on an API error, so no Stop hook runs | A `StopFailure` hook writes the blocker for it, and the supervisor resumes the same lane from that record | `cci hook stop-failure`, `lanes.foldRecord` | `internal/hook`: the failure leaves a blocker. `internal/lanes`: that blocker pages its own lane |
| 2 | A lane posts without a registry row, so nothing supervises it | A new lane name's first post needs a `spawn` record naming it, or the launcher's `CLAUDE_LONG_RUNNING_LANE` naming it. Otherwise the post is refused with the command that registers it | `cci post` | `internal/cli`: first post refused and it names the fix; passes after a spawn record; a drive with no spawn records is unaffected |
| 3 | Compaction without a handoff | `PreCompact` runs `lr lanes handoff`, which writes the doc from the lane's brief, records, PRs and worktree with no model call, and posts `handoff --ccn`. A failure posts a blocker. After compaction, `SessionStart` names the doc and the brief | `lr lanes handoff`, pack hook `lane_handoff` | `internal/lanes`: the doc carries every owned PR and open ask, and the record sets the row's handoff. A failed `ccn` leaves a blocker |
| 4 | A refused post becomes silence | Any refused `cci post`, whoever swallowed its stderr, writes a `blocker` directly to the store with the refusal and the words it refused. The lane's next stored post closes it | `cci post` | `internal/cli`: each refusal leaves one blocker, a repeat leaves no second, and the next post closes it |
| 5 | PR-SILENT: `mechanism`, then neither `opened` nor `blocker` | `mechanism` is not a turn end, so the lane's next stop is refused with `opened` and `blocker` as the fix | `cci hook stop` | `internal/hook`: a lane whose newest record is `mechanism` cannot stop until it posts one |
| 6 | A remote lane stops on a question about Mac-only paths | launch-spill-1 (#1990) owns the launcher half. **The supervisor has no half**: a successor already launches `place=local` (`successorBrief`), and the question is now a typed `ask` that the rule above forces | none | none |
| 7 | A lane's question goes to root, where nobody owns it | launch-spill-1 (#1990) owns this. Referenced, not duplicated | none | none |
| 8 | A successor for a lane that ended on purpose | `done`, `stopped` and a runner `WITHDRAWN` retire the row in `fold`, and only live rows reach `relaunch`. `cci post` refuses `STOPPED <own lane>` in a `state`, `report` or `note`, so an end cannot be typed as prose. lr-go-lanes-1 carries the Go fold half (#348360) | `lanes.foldRegistry`, `cci post` | `internal/lanes`: replay of 345871 and 345908 launches nothing. `internal/cli`: the prose end is refused |
| 9 | A refused Agent spawn draws a row, then a 15-minute not-live blocker | `SPAWNED` posts from `SubagentStart`, not `PreToolUse`, so a refused spawn writes no record | pack hook `record_spawn` | pack test: a spawn another hook refuses posts nothing; a started one posts once |
| 10 | A queued launch runs after its lane stood down | A stand-down of a `launching` row withdraws the queued line in the same fold, and the runner reads both together. lr-go-lanes-1 carries this (#348360) | `lanes.fold` | `internal/lanes`: the stand-down of a launching row withdraws it |
| 11 | Owner gate: ended on a tap or ruling (`disk-first-build-1`, `orphan-prs-1`, `orphan-prs-2`, `cerebras-models-1`) | The wait is a row: `Waiting` and `Awaits` hold the card. The pick clears them and pages the lane, or its successor if the process is gone | `lanes.foldWait`, `lanes.foldGates` | `internal/lanes`: a parked lane with a dead process gets no successor; the pick resumes it |
| 12 | Upstream gate: ended on another PR (`follow-pass-pr-1`, `upload-order-1`) | `blocked --pr N` parks it, and the `landed` record naming N fires the gate. The lane resumes without anyone remembering it | `lanes.foldGates` | `internal/lanes`: `landed` naming N resumes exactly the lanes parked on N |
| 13 | Release gate (`pod-local-ack-1`), cutover gate (`n-enumeration-13`) | `blocked --target T` fires on the `release`, `applied` or `fix-live` record naming T; `blocked --lane-ref L` fires when lane L posts `done`, `stopped` or `fix-live` | `lanes.foldGates` | `internal/lanes`: each fires, and an unrelated record does not |
| 14 | Head and go gate: green at a new head with an old go (`passive-only-1`) | The lane parks on `--pr N`. The landing mover owns the go at the new head; a `blocker` naming N pages the lane and overrides the wait | `lanes.foldPage` | `internal/lanes`: a blocker on N resumes a lane parked on N |
| 15 | Report and end the turn (`disk-first-align-1`) | A `report` is not a turn end | `cci hook stop` | Row 1's test |
| 16 | `done` while the lane still owns an open PR, so the PR orphans and the coordinator launches `orphan-prs-N` | `done` retires the row, whatever is open (communicator #350741: "a stopped, withdrawn or done lane is never relaunched by replay or supervisor"). A lane still waiting on its PR posts `blocked --pr N` instead. When a retired lane's PR goes red or conflicting, one blocker tells the coordinator no lane owns it | `lanes.foldRegistry`, `lanes.ownedWork` | Replay fixture `supervisor-done-lane-keeps-posting`: no launch. `internal/lanes`: a done lane's broken PR draws one blocker |

## What the supervisor owns

One tick does the following, all from one read of the new records and one `cci who`, so its work stays flat at 500 lanes:

- **Liveness:** the process row and the lane's own records.
- **Parking:** every `ask`, `blocked` and `blocker` from a lane sets `Waiting` and `Awaits`. Any later record from that lane clears them, which matches the Stop hook's reading of "newest record".
- **Gates:** an event record clears the wait and sets a page for each lane parked on it.
- **Restart:** a page reaches a live lane through its roster inbox or `orca orchestration send`. A process that is gone gets a successor, because its obligation is on the row.
- **Successor:** only for a live row. A row that ended is retired, and a parked row waits for its gate.

A woken lane that answers nothing for 30 minutes and has made no tool call in that time gets a successor. The successor's existing check for live processes in the worktree keeps two writers out.

## Deleted in this series

| Bandaid | Replaced by |
| --- | --- |
| `probeOwners`' direct ask, "post any record within 15 minutes or root is told you are gone", with `ProbeDead` and its blocker to root | A PR that needs work pages its owner with the fact. A closed one is disowned |
| `Waker`'s silent wake, "seen no record in 15 minutes while your root task is in progress" | The one rule; `StopFailure` for a dead turn |
| The reassign ask to root or the desk | A successor |
| `deadOwner` and `successorNote` | Nothing reads a probe death any more |
| The runner's `PR-SILENT` clock and its `show` line, `incident_lane_pr.py`, and `drive.py pr-owed` | Row 5 |
| `lane_reports_are_records` | Row 1: a SendMessage report no longer ends a turn |

The live Python mover and `stall_watch.py` are deleted by lr-go-lanes-1's cutover PR, which flips `lr.json` to the Go loop. Until that lands, the Go half of rows 8, 10, 11 to 14 and 16 has no effect on a running drive. The cci half of rows 1 to 5, 8 and 15 takes effect when cci is installed.

## What remains possible

- **A Codex lane has no Stop hook wired.** `cci hook stop` reads a Codex payload, and cci already keys on `CODEX_THREAD_ID`, but `~/.codex/hooks.json` does not call it. Wiring it is machine configuration for the drive's one installer.
- **The store cannot answer at the exact moment of a stop.** The hook passes. A teammate lane's peer row retires on `SubagentStop`, so the supervisor relaunches it. An Orca worker's row reads live until its dispatch settles, so it stays idle until something pages it.
