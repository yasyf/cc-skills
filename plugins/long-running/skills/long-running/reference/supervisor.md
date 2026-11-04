# A quiet lane cannot happen

Before `lr supervisor install`, set `alert_channels` in `lr.json` to the comma-separated Slack channel ids that carry the drive's alerts, the releases channel among them; with none set, no alert opens its own lanes.

The supervisor makes a quiet lane structurally impossible; it does not report one and bump it. One supervisor owns liveness, restart, successor and stall needs, and the hacks it replaced are deleted.

## Contents

- [The one rule](#the-one-rule)
- [How a turn ends](#how-a-turn-ends)
- [Spawns, names and identity](#spawns-names-and-identity)
- [Waits, gates and wakes](#waits-gates-and-wakes)
- [Compaction and refused posts](#compaction-and-refused-posts)
- [What the supervisor owns](#what-the-supervisor-owns)
- [Dead lanes](#dead-lanes)
- [Release builds, flakes and other supervisor posts](#release-builds-flakes-and-other-supervisor-posts)
- [What remains possible](#what-remains-possible)

## The one rule

**Every turn of a launched lane ends on a typed record, and the record says who resumes it.**

| The lane's newest record | What it means | Who resumes the work |
| --- | --- | --- |
| `done`, `stopped`, `stand-down` | The lane ended on purpose | Nobody. The row retires, a retired row never gets a successor, and nothing the lane posts afterwards revives it. A `done` is refused while the lane owns an open PR, so it posts `blocked --pr <n>` instead; a PR it still owns after a `stopped` or `stand-down` goes to the coordinator if it breaks. A lane that still owes a deletion is held instead (see Owed deletions) |
| `ask`, `blocker`, or `blocked` to a lane | It waits on that lane's answer | The answer, through the supervisor |
| `blocked` naming a gate: `--pr`, `--target`, `--lane-ref`, or a `card-` id in the text | It waits on an event | That event, through the supervisor |
| Anything else | The turn has open work | Stopping is refused, and the refusal prints the commands |

- A lane refused twice with nothing new posted gets a `blocker` written for it, with its last words, and then stops (`cci hook stop`). The bad state "the turn ended and no record says why" has no path: the lane posts the record or the hook posts it.
- Four kinds of lane stop freely, because each one's wake already exists: the coordinator's thread, a session with a cci channel subscription, a lane whose brief has a `Standing:` line, and a lane's root thread whose Stop payload lists a live `background_tasks` or `session_crons` entry: its Agent, `Monitor` or `CronCreate` notification wakes it. A thread with no `spawn` record is not a launched lane.

## How a turn ends

| Situation | Rule | Enforced by |
| --- | --- | --- |
| Open work on a promise | A wait on a gate is a `blocked` record naming it; a live harness wait wakes the thread itself. A lane whose newest record is a `report` cannot stop. `done`, an open `blocked`, a subscribed session and a Stop payload with a live background task or session cron stop | `cci hook stop`, `cci hook subagent-stop` |
| The turn dies on an API error, so no Stop hook runs | A `StopFailure` hook writes the blocker, and the supervisor resumes the same lane from that record | `cci hook stop-failure` |
| `mechanism` then neither `opened` nor `blocker` (PR-SILENT) | `mechanism` is not a turn end; the next stop is refused with `opened` and `blocker` as the fix | `cci hook stop` |
| A `report` or `handoff --ccn` as the last act, or a handoff in prose ("X owns the rest") | Neither is a turn end. A lane waiting for another lane's work posts `blocked --lane-ref L`, and L's `done` wakes it | `cci hook stop` |
| A lane waiting on its own PR, ended on a `report` | A `report` cannot end the turn, so the lane parks with `blocked --pr N`. A green PR needs nothing from it; a red or conflicting one wakes it, once until a new head | `cci hook stop` |
| A lane with no shell cannot post | The second stop with nothing new writes the blocker and lets it stop, so one lane costs one wake | `cci hook stop` |
| A teammate lane's stop hook ran out of its deadline | `cci hook subagent-stop` is a direct hook that reads one store row and no transcript, so no deadline runs out. A named teammate is held; a helper stops | `cci hook subagent-stop` |
| Told to stop, or done | Not a loss. The typed end retires the row, and a later assignment is a new launch with its own row. `cci post` refuses `STOPPED <own lane>` in a `state`, `report` or `note`, so an end cannot be typed as prose | `cci post` |
| Idle by its own brief until the coordinator messages it | A `Standing:` brief stops freely. Without that line the lane posts an `ask` or `blocked` that says who wakes it | `cci hook stop` |
| A tool is running, or no lane was ever launched | Not a turn end. A lane in a long tool call is alive and its row says so; a brief with no launch has no row, so nothing can call it quiet | none |

## Spawns, names and identity

- **First post of a new lane name.** It needs a `spawn` record naming it, or the launcher's `CLAUDE_LONG_RUNNING_LANE` naming it. Otherwise, `cci post` refuses and prints the command that registers it. A drive with no spawn records is unaffected.
- **Refused spawns.** `SPAWNED` posts from `PostToolUse` of the Agent call, never `PreToolUse`, so a refused spawn writes no record (pack hook `record_spawn`).
- **A queued launch after a stand-down.** A stand-down of a `launching` row withdraws the queued launch line in the same fold, and the runner reads both together.
- **A remote lane's question about Mac-only paths.** The supervisor has no part in it: a successor already launches `place=local`, and the question is a typed `ask`.
- **Drive processes take their own lane.** Every drive process sets `CLAUDE_LONG_RUNNING_LANE` and `LR_LANE` to its own name at start and clears the session identity `CCI_AGENT_ID`, `CCI_LANE`, `CLAUDE_CODE_SESSION_ID` and `CODEX_THREAD_ID`; its launchd plist or launcher sets the same. The desk runner hands `lr launch start` the lane that posted the launch line, so a `SPAWNED` record names the supervisor for its relaunches and the runner for its own launches (`lr supervisor run`, `lr desks start`, `desk-runner.py run`, `board_watch.py start`, `prompt_watch.py run`).
- **Nested `claude -p`.** A window registers its launcher's lane only when its `claude` process started without `CLAUDE_CODE_SESSION_ID`, which a Claude session sets only for what it runs. A launcher's window and a Sprite desk loop's `claude -p` lane register; a nested run from a lane's Bash does not. A process the hook cannot read registers (pack hook `register_injection.register_lane_at_first_command`).
- **A lane finds its drive** through the supervisor's registry row for its `CLAUDE_LONG_RUNNING_LANE` when no drive lists its session, so a Claude or a Codex successor resolves the same way (`drive.py lane-status`). A lane no registry row names is refused.
- **Standing sessions register.** A `SPAWNED` record whose brief carries a `Standing:` line registers whatever its role. Each tick adopts the lane of a standing session such as `chores` when `cci who` lists it live with no row, from `<state dir>/<lane>/brief.md`.
- **Desk launches.** `lr desks start` finds each desk by role through its newest numbered row. A live row is up. A held or retired one that has posted nothing for 15 minutes relaunches under the next number no row or post has used, from a copy of the brief its row records at `<state dir>/<successor>/brief.md`. A role with no row launches from `<state dir>/<role>/brief.md`.
- **Runner config.** Preflight every `runner.json` edit before a roll and use the coordinator's lane name verbatim; a base name missing from `go_lanes` fails landing preflight.
- **Standing desks are lr-launched lanes.** Every standing desk is an lr-launched lane registered to the coordinator, on a Sprite unless its brief says `place=local` and names why it needs the Mac. In the communicator's and the coordinator's window, an `Agent` spawn named for a desk role, whose prompt opens as one, or whose prompt carries a `Standing:` line is refused, with `lr desks start` and `lr launch lane` as the way to start it. A short read lane with no `Standing:` line passes (pack hook `desk_spawns`).
- **Desk waits use `cci:`.** A desk brief waits on `cci:<drive>:<desk>`; a `mailbox:` source stays for an in-process `Agent` teammate such as a landing shard (I7 and B3 in `reference/bus-contracts.md`). `lr launch` refuses a brief whose `lr wait` names the launched lane's own mailbox, or its role's, and names the `cci:` wait to use.
- **One canonical name.** A lane's name is canonical in one place, which drops the drive's initials prefix that an Orca workspace adds. Supervisor records, `cci who` rows, `lr lanes`, `lr route`, `lr request`, `lr done-check` and `lr desks start` all read it.
- **A stem names its live lane.** A stored recipient addresses a lane when it is that lane or its stem, and the fold reads a stem recipient as the stem's live lane; every map of ended or closed lanes is keyed by stem. A lane argument with no `-<n>` resolves once, through cci's registry, to the stem's newest live lane in `lr lanes`, `lr lanes handoff`, `lr lanes adopt-meta`, `lr route`, `lr request` (the lane, never `--successor` or `--by`), `lr done-check` and the runner's relay line. A numbered name stays itself; a stem no lane holds stays as typed.
- **Stand-downs retire only what they name.**
  - A root stand-down drops any named lane that succeeds another named one, and every word after `for`.
  - The queued relaunch of a stood-down lane is withdrawn only while it has posted nothing.
  - A stand-down for a successor points the stood-down lane at it even when that lane already stopped, so the chain reaches the live end.
  - A launching lane that posts its own record has come up.
  - `lr lanes` reads a held or retired lane that posted within 15 minutes as `posting`, never `silent`.
  - `lr desks start` treats a role's holder as live when its row is up or it posted within 15 minutes under either name; it launches nothing for it and prints the holder, its newest record and the stand-down that frees the role (pack hook `lane_rotation`).
- **Takeovers post the route.** Every takeover the registry records posts the `stand-down --successor` that cci routes by, whether the spawn named its predecessor or only outnumbered it, and so does the relaunch shortcut. `lr launch lane <stem>` picks the stem's next number from cci's registry of every name the stem ever had.
- **A successor keeps its predecessor's role** when its spawn names none, so a meta desk's successor is the meta desk whoever launched it and its helpers read meta through it. A role the spawn names wins.

## Waits, gates and wakes

A wait is a row: `Waiting` and `Awaits` hold the gate. The matching event clears the wait and pages the lane, or its successor if the process is gone. A parked lane with a dead process gets no successor.

| Gate | Parks with | Fires on |
| --- | --- | --- |
| Owner or decision gate (a tap, a ruling, a carried override) | `blocked` naming the `card-` id; `ask` | The owner's pick, or a record answering the ask |
| Upstream PR | `blocked --pr N` | A `landed` record naming N, resuming exactly the lanes parked on N |
| Release or cutover | `blocked --target T` | A `release`, `applied` or `fix-live` record naming T |
| Another lane's work | `blocked --lane-ref L` | L's `done`, `stopped` or `fix-live` |
| Head and go (green at a new head with an old go) | `blocked --pr N` | The landing mover owns the go at the new head. A `blocker` naming N pages the lane and overrides the wait |
| Expired credential, case unrecovered | `blocked` or `ask` | The answer |

An unrelated record fires no gate.

- **Retired lane with a broken PR.** `done` retires the row, whatever is open; replay and the supervisor never relaunch a lane that stopped, was withdrawn or is done. When a retired lane's PR goes red or conflicting, one blocker tells the coordinator no lane owns it and names the `lr request owner <lane> --pr N` that hands it on.
- **Card gates.** Every card id a `blocked` record names is a gate, and any `owner` record naming one of them, bare or in parentheses, wakes the lane. With `build_recommended_order` in `lr.json` set to the standing order's token, such as `owner:m104`, the hourly pass wakes every lane parked on a card, citing the token, and sends the meta desk one line naming them. A lane whose card recommends nothing re-parks with `no-recommendation` and is left alone.
- **Board-built branches.** The hourly pass records each card it sends a lane to build on a branch. An `owner` record naming one marks it answered. Once the last one is answered, the supervisor pages the lane holding the branch to open its PR, relaunching it if it has retired, and sends the meta desk one line naming the lanes told. A lane with one card still open is left alone. If that lane owns no new PR 15 minutes after the order, the meta desk gets a `defect`, never the owner a card.
- **Off-board parks.** A `blocked` record that names a cc-present board (a `/p/<slug>` URL or `board <id>`) other than the artifact bound to the coordinator's session, or a card with no Slack copy, is a defect, never a wait; so is any other record from a lane naming such a board. `lr choices` posts one `OFF-BOARD` note a day to the lane's launcher and the meta desk, naming what the owner cannot see and the move: `lr ask-owner`, which posts on both. It replaces the `PARKED` note for that record and refuses nothing. With the main board unread it says nothing.
- **Parked lanes and the desk runner.** A lane whose newest record is `blocked` and newer than the supervisor's last wake of it (the `woke` stamp `lr lanes --json` prints) is parked, so the runner reads Orca's stop notice as the park and raises no `DECIDE`. A coordinator reply relays to a parked lane only when it answers the record it parked on or a `DECIDE` raised after it; any other reply logs one `PARKED` line and relays nothing. The lane wakes on what its record names, through the gates above (`desk-runner.py`).
- **Auditing owner orders.** Before calling an owner order dropped, read the reply notes and the owner's board waits. Replies name no row id, and `lr board waits` lists his picks.
- **Unread records.** A record a person or lane addressed by name to a live lane is tracked until the lane's next tool call or record, and wakes it; only an order (`ask`, `lr.ask`, `decide`, a failed grade) also reaches the notice desk or loses a desk. The clocks (30 seconds for an order to a standing lane, 5 minutes for anything else, 10 minutes to the notice desk) are in the "Check liveness and wake parked lanes" part of [watchers.md](watchers.md#supervise-and-rotate-lanes).

## Compaction and refused posts

- **Compaction writes a handoff.** `PreCompact` runs `lr lanes handoff`, which writes the doc from the lane's brief, records, PRs and worktree with no model call, and posts `handoff --ccn`. A failure posts a blocker. After compaction, `SessionStart` names the doc and the brief (pack hook `lane_handoff`). The doc carries every owned PR and open ask; a desk's or the communicator's doc also carries the open owner orders from `lr owner audit`. The record sets the row's handoff.
- **A refused post is never silence.** Any refused `cci post`, whoever swallowed its stderr, writes a `blocker` directly to the store with the refusal and the words it refused. A repeat leaves no second blocker, and the lane's next stored post closes it.
- **Several waits due at once.** When several waits fall due together, the supervisor raises each wait's own row but posts one record per kind and addressee, with every id and its line in the file `--path` names. A lone wait keeps its own record.
- **Late replicas.** The fold reads each record's origin and own clock and measures its delay against the newest record already folded. `lr lanes` prints the node and delay and says `lagging` until that delay has passed; only `silent` is silence.
- **A watcher that posts only on an event.** `monitor-watch.py --alert-drive` posts one `state` record as monitor-watch after a Datadog read succeeds, when its last one is 15 minutes old or there is none, and keeps the time beside its state file so a re-armed watch keeps the cadence. A watcher that dies or stops reading posts nothing and is held at the hour.

## What the supervisor owns

One tick does all of the following from one read of the new records and one `cci who`, so its work stays flat at 500 lanes.

- **Liveness:** the process row, the Orca agent's verdict on it, and the lane's own records.
- **Parking:** every `ask`, `blocked` and `blocker` from a lane sets `Waiting` and `Awaits`. Any later record from that lane clears them, which matches the Stop hook's reading of "newest record".
- **Gates:** an event record clears the wait and sets a page for each lane parked on it.
- **Restart:** a page reaches a live lane through its roster inbox or `orca orchestration send`. A process that is gone gets one successor when its row holds an open PR or a standing duty, because its obligation is on the row; a row holding neither retires once silent past the threshold (see Dead lanes).
- **Successor:** only for a live row. A row that ended is retired, and a parked row waits for its gate.
- **A woken lane** that answers nothing for 30 minutes and has made no tool call in that time gets a successor. The successor's check for live processes in the worktree keeps two writers out.

### Owed deletions

A lane cannot retire while its line owns an open `owed-deletion` task whose code is still on `origin/<trunk>`.
- The task's assignee is its owner, followed through successors to the lane now at the head. A task with no anchors counts as present until it closes.
- Its own `done`, `stopped`, `stand-down` or handoff, the runner's `worker_done` or `WITHDRAWN`, and a coordinator stand-down that names no successor all hold the lane. One `defect` to the coordinator and the lane names each task.
- Each hour the tick posts one `blocker` digest to the coordinator, listing every path or symbol an in-force `removes` row names that lacks both an `owed-deletion` task and an `exception` row, is not in the row's `guards`, and is still on trunk at the row's anchored paths: one line per target, with the rows that remove it. The digest's id hashes that list, so an unchanged set of gaps is never reposted.
- The owner update lists each such task of the drive's program whose code is still there and whose owner has no live lane, under "Owed deletions with no live owner".
- A task past its due date with its code still on trunk draws one `blocker` to the live head of its owner's line, or to the coordinator when there is none, and the update lists it under "Owed deletions past due".

## Dead lanes

A lane reads live only while its agent is alive. Liveness comes from the agent, not the dispatch: the tick's one `orca orchestration worker-list --include-remote` read carries each worker's `projection.liveness` verdict, which Orca gets from the Sprite.

| Verdict | Meaning |
| --- | --- |
| `live` | Live |
| `exited` | No process |
| `unverifiable` (a cold or unreachable Sprite) | Proves nothing |

A teammate the roster lists whose turn has ended reads idle.

**Each lane then gets one decision.**
- An open PR, a `Standing:` line, or the role `desk`, `meta` or `release` relaunches it once. A lane retired by a `done` or `stopped` record never relaunches, whatever it holds. A lane the supervisor already relaunched that dies again is held with one `defect` to the coordinator, or to the head of its launcher's line for a meta lane.
- A lane holding none of those, silent past `retire_silent_minutes` in `lr.json` (60 by default), retires. Each tick sends each desk its retirements, with why, as one `RETIRED` record. An idle teammate counts here only.
- An unproven lane that holds work, or holds nothing and is silent past the threshold, gets a proof from its host before any decision.
- A lane parked on a recorded wait is left alone, and a retired lane, such as one whose Sprite the autostop stopped, never reaches the decision.

**Proof for a lane on a Sprite.**
1. The tick reads the Sprites list once, `sprite api /sprites` with its pages, which carries each Sprite's `status` and `last_running_at`. The read stays one call whether the drive runs 1 lane or 1,000. A warm Sprite no lane runs on is never read, a settled lane takes the path below, and a failed list read logs a warning and decides nothing for a lane placed on a Sprite.
2. A Sprite that is running gets one `sprite exec`, which looks for a `claude` or `codex` process, and all of a tick's probes run concurrently in one call.
   - A running worker leaves the lane alone, and the box is not probed again for 15 minutes, so a sleeping Sprite gets to sleep.
   - A box that answers with no worker reads dead and takes the dead path above.
   - A box that does not answer waits 30 minutes and then takes the same path, which relaunches a lane holding a PR and retires only a lane holding nothing.
3. An unverifiable lane the Sprites list names gets this proof whatever its recorded placement, so a Sprite lane launched with no placement is still read.

**Proof for an unverifiable lane no probe settles.** This covers a lane on no Sprite, and one whose probe could not run or printed something unexpected.
1. The supervisor posts one relay through the Orca desk asking the lane to post its state. A parked lane wakes on it, and a Sprite lane receives it as cci mail.
2. Any record within 15 minutes ends it with no blocker and no relaunch. Orca reading the lane live ends it the same way, and a lane Orca reads live is never asked. A lane that later goes quiet again is asked again.
3. No record in 15 minutes is the proof, and the lane takes the dead path above: a lane holding a PR relaunches from its brief and handoff, and one holding nothing retires.
4. A lane the supervisor already relaunched, or whose brief is gone, is held instead. Its one blocker names what failed and goes to its desk, or to the drive's meta desk when it has none; root gets it only when the drive has no meta desk.

A coordinator reading unverifiable, and a lane with no process row, are held with that one blocker and get no relay.

**A lost Sprite lane.** An unproven Sprite lane that has not settled, whose Sprite has not run for 5 minutes, is lost, whatever it holds and however long it has been quiet.
- The supervisor posts one `LANE-LOST` report, naming the Sprite and when it last ran, to the lane that launched it and to the meta desk. It relaunches the lane from its brief as the next `<stem>-N`, whose brief opens with a `Partial work:` line naming what the lost lane left.
- A lost lane the supervisor already relaunched once is held, and its report says so.
- The same loss covers a Sprite lane whose host Orca has reported `host_unavailable` for 10 minutes, and one with no record 15 minutes after its launch that Orca does not report live. A quiet lane Orca reports live is left alone, unless it is a standing desk with an order unread for 30 minutes.
- A lane that recovers before the relaunch is never relaunched. One that recovers after is stood down for its successor, so the stem's newest lane wins.
- A Sprite successor's brief carries only what the bus holds: its predecessor's records, handoff and pushed PRs, never a path on the lost box or the Mac.
- Unread orders on a Sprite lane go to its notice lane once at 10 minutes, since no relay reaches a Sprite and the supervisor sees no tool calls from one.
- A standing desk other than the meta lane, anywhere, with an order unread for 30 minutes and no record or tool call since is lost whatever Orca reports: the same `LANE-LOST` report and relaunch as above. A live row alone proves nothing about a desk, because a desk idle at its prompt after its wake died reads live.

**A refused launch** (before it posts, or by a runner verdict such as `LAUNCH-DUPLICATE`) is retried once. A second refusal holds the lane with one blocker to the meta desk naming it. No `RELAUNCHED` report goes out for a launch the launcher did not post.

**A `worker_done` with no record on the bus** never retires the lane, whether Orca's row or the runner's `OUTCOME` line carries it. The supervisor holds it and raises one blocker to the coordinator naming `lr request done <lane>`. Once per retired lane, the tick reads the open PRs by branch from GitHub in one call and gives the coordinator every open PR no lane owns whose branch is the lane's name, with or without its `-N`, or starts with that name and a `/`, in one `ORPHANED` report.

## Release builds, flakes and other supervisor posts

- **Release builds.** With the drive's `lr.json` setting `release_pipelines` to comma-separated `org/pipeline` slugs and `buildkite_cli` to the Buildkite client command line relative to the checkout, the tick reads each listed pipeline's 20 newest builds in one `graphql` call through that client, once a minute.

| Build | Posts | To |
| --- | --- | --- |
| No record names it (by its URL in the text, topic or refs) 5 minutes after it started | one `RELEASE-SEEN <build> <pipeline> <state>` blocker, once per build | the release lane |
| Failing or failed | one `RELEASE-FAILED <build> <failing step> <url>` blocker per failed step, once per build and step whether Buildkite reports `failing` or `failed` first, under an id cci keeps across restarts | the release lane and any other lane that named it |
| Started from a trigger step with no creator (an automatic promotion), or a `RELEASE_START` mode of `dry-run` | no `RELEASE-SEEN`; still `RELEASE-FAILED` | |
| Failed, with a newer build's `RELEASE_START` naming its branch in `replans` | nothing; it waits on that resume, which posts its own `RELEASE-FAILED` if it fails | |

  The release lane is the holder of the live lane with role `release`; the coordinator gets the alert only when no such lane is live.
- **Release nags.** A release nag goes to the current holder of `landing.release_owner`: the canonical name while it is live or held, else the end of its stand-down chain, else the newest live lane under the same stem. Any record from that role replying to a nag with `--re` or `--resolves`, or naming the PR, stops the nag, and the answer's seq is saved on the PR's `release-owed` fact so a restart does not resume it. A build or `fix-live` still settles the release itself.
- **Incident fixes.** A landing is an incident fix only while an open `incident` record names its PR or lane, or an adopted repair's incident is unsettled. An incident lane name or fix launch with no live alert is ordinary landing work.
- **Posts to a PR's lane** (the `LANDED` record, the incident squash's and gates' records) go to the holder of that lane, so a retired lane's successor receives them.
- **A post the stall watch's sweep or meta pass cannot make** is returned with its subject while the pass goes on to every other lane, and the tick logs it.
- **Flakes.** A flake goes to the live lane of the `landing.flake_lane` stem.
  - With none live, the supervisor writes the stem's standing brief to `<state>/<stem>/brief.md`, launches the next number from it through `orca-desk`, and root gets one `note` it need not act on.
  - The record names the failing test, or the job when no test failed, and calls a job that failed on a GitHub quota or rate limit `INFRA`, not a flake. It is due in two hours.
  - Each pass, an open record whose lane is gone gets the next lane of the stem, handed over in a `note`, and it shows in the owner update.
  - A repeat failure of the same test or job attaches to the open record. A record closed by a resolver that names no merged PR reopens to the same owner.
  - An open record past its due time, or with no live lane on it, becomes an ask, so it shows in the owner update and on the owner's Your asks page.
  - Automatic retries stay, and a green retry never closes a flake.

The supervisor's landing-pass checks (dequeueing an untapped queued PR, the incident squash's green-head rule, grouped escalations to root, held-head `DECIDE`s, ungraded heads, `CLOBBERED`, and what counts as the drive's own PR) are rules of `reference/landing.md`. `LANDED-NOT-LIVE`, the alert launch and the release gate are in [watchers.md](watchers.md#supervise-and-rotate-lanes).

## What remains possible

- **A Codex lane has no Stop hook wired.** `cci hook stop` reads a Codex payload, and cci already keys on `CODEX_THREAD_ID`, but `~/.codex/hooks.json` does not call it. Wiring it is machine configuration for the drive's one installer.
- **The store cannot answer at the exact moment of a stop.** The hook passes. A teammate lane's peer row retires on `SubagentStop`, so the supervisor relaunches it. An Orca worker's row reads live while its agent's verdict is `live`.
- **A lane no host probe settles is held for a person.** A lane on no Sprite, or one whose `sprite exec` cannot run, has nothing the supervisor can ask. One blocker goes to its desk or the meta desk, which reads the host and relaunches it or waits.
- **A Sprite with no `lr.json` writes no compaction handoff.** The hook finds no drive there and does nothing, and the lane resumes from its transcript's summary.
- **A lane can post `done` with work unfinished.** Owed deletions are the exception. Otherwise, the row retires, because `done` is the lane's own word, and only a PR it owned reaches the coordinator, if that PR breaks. Prose such as "X owns the rest" stays prose. `blocked --lane-ref L` is the typed form, and the lane brief tells lanes to use it. An acknowledged transfer record would close this, and it needs an owner pick because it adds a record kind.
- **`inboxes.py wait` prints a `JSONDecodeError` and then `QUIET`.** A lane reads that as no mail and keeps working, so no turn ends. Report it to the meta desk, since Python drive tooling takes no new work here.
