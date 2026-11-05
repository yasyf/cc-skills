# Watchers and lane supervision

## Contents

- [Run the stall watch](#run-the-stall-watch)
- [Run the prompt watch](#run-the-prompt-watch)
- [Answer a terminal dialog](#answer-a-terminal-dialog)
- [Respect shared API budgets](#respect-shared-api-budgets)
- [Write a watch that cannot miss](#write-a-watch-that-cannot-miss)
- [Supervise and rotate lanes](#supervise-and-rotate-lanes)
  - [Run the supervisor](#run-the-supervisor)
  - [Track lane ownership and retirement](#track-lane-ownership-and-retirement)
  - [Check liveness and wake parked lanes](#check-liveness-and-wake-parked-lanes)
  - [Query lanes and PR ownership](#query-lanes-and-pr-ownership)
  - [Relaunch, rotate and keep standing duties](#relaunch-rotate-and-keep-standing-duties)
  - [What the supervisor does without the coordinator](#what-the-supervisor-does-without-the-coordinator)
  - [Rewrite SendMessage to a retired lane](#rewrite-sendmessage-to-a-retired-lane)
  - [Kill a process the owner named](#kill-a-process-the-owner-named)

## Run the stall watch

**The supervisor finds work that stopped moving; there is nothing to start.** Each head has a state, a mover and a clock. An item past its deadline goes to the coordinator once.

| Waiting on | Clock |
| --- | --- |
| A grade or a supervisor step | 10 minutes |
| A lane | 20 minutes |
| The queue | 30 minutes |
| CI | 2 hours |
| The owner | 12 hours |

**`lr watch` covers the waits no PR state carries.** It runs on the supervisor's tick and reads open asks, gates, repeated defects, unfinished ledger rows (retro rows, owner asks, sweep findings and picks; a pick naming a PR that waits on his tap is his wait and never reaches root), `meta/items.md` and records that name a deadline with no plan. `lr watch deadlines` runs one pass by hand.

**The red register is `lr board reds`.** It prints one line per open drive PR whose build reads red: head, failing checks, class, owner, red since and last action. The class comes from the newest `red` record that names the head; a head with none reads `unclassed`. A red head's `fix-red` item goes to the coordinator past the lane's 20 minutes, classed or not, so no red depends on a `red-sweep` lane being live.

**`lr board waits --pr <n>` says what a PR waits on,** including the PR below it when an unlandable parent is the reason.

## Run the prompt watch

**`lr watch prompts` detects terminals stopped at interactive prompts.** A terminal stopped at a prompt reports nothing in its transcript and its lane looks busy, so the prompt watch reads Orca directly.
- The supervisor runs it as one of its checks every 30 seconds, so a stuck session does not stop it. It holds `<state dir>/prompt-watch/lock` while it polls.
- `drive.py start` records the coordinator's terminal. When the coordinator resumes in another one, the `SessionStart` hook records the new one with `lr watch prompts bind --root-terminal`.

**Poll every terminal in the drive registry.**
- The coordinator's terminal is `root_terminal`, which `drive.py start` and the hook record from `ORCA_TERMINAL_HANDLE`. A prompt raised by an in-process lane appears here.
- Each standing desk is recorded with `drive.py desk --name <lane> --terminal <handle>`, plus `--environment <name>` for a desk on a remote host.
- The workers are every in-progress dispatch of the drive's Run, local or remote, from `orca orchestration worker-list --include-remote`. With no `orca_run` in the registry, the watch uses the Run bound to `root_terminal`, or none when that terminal is unbound.
- While `worker-list` fails, no worker's prompt is closed; each worker keeps its record and reads `unknown` or `unreachable` until the list answers again.

**Classify each terminal from its rendered screen.** Orca's `agentWait` only indicates where to look; it stays set after a prompt is answered, so it never raises a record and never authorizes an answer. The watch reads the screen with `orca terminal read --screen` and puts each terminal in one state:

| State | The screen shows | Record |
|---|---|---|
| `approval` | an approval dialog, such as a hook's ask, a tool permission, or a trust check | one, on the first poll that sees it |
| `question` | a question picker | one, on the first poll that sees it; none on the coordinator's terminal |
| `stale` | the agent's own input box, with `agentWait` still set | none; it resolves the prompt's record |
| `unknown` | nothing the watch can read, or a wait whose screen matches no dialog or input box it knows | one `report` to `desk-runner` after three polls |
| `unreachable` | nothing, because the remote host does not answer | one `report` to `desk-runner` after three polls |
| `clear` | a working or idle agent with `agentWait` unset | none |

**Report unreadable terminals and runtime outages to `desk-runner`, never to `root`.**
- After an `unknown` or `unreachable` report, the watch posts one `READABLE` report to `desk-runner` once three polls in a row read the terminal again. A shorter read posts nothing, so a flapping terminal or `worker-list` raises no second record for the same unread state.
- When every watched terminal reads `runtime_unavailable` or `timeout`, the Orca app itself is down and no terminal reports alone. The watch posts one `orca (runtime)` report after three polls, only when the last answered `worker-list` held a worker in progress, and posts `READABLE` only after that report. A drive with no worker in progress hears nothing about the outage.
- `unknown` and `unreachable` mean the prompt state is unknown; never treat either as working or stuck.

**Read remote worker screens on every poll.** A host running Orca's managed server installs no agent hook, so `agentWait` stays unset there. The watch reads every remote worker's screen on every poll through the `--environment` that `worker-show` names.

**Store dialog text on disk only.** A dialog's text can hold a command or a secret, so the watch saves the screen of each prompt and each unclassified screen owner-only under `<state dir>/prompt-watch/screens/`. A record, a wake, and `lr watch prompts show` name that file, the dialog's class, and the command that reads the live screen, never the dialog's text.

**Post one cci record per prompt.** Each prompt raises one cci record from lane `prompt-watch` on topic `prompt:<name>`, never one per poll. The terminal, the dialog's class, and every row of text in the dialog identify a prompt, so a second command behind the same warning is a second prompt. Orca rewrites `agentWait.since` every few seconds while one dialog stays open, so it identifies nothing.
- A worker's or desk's prompt posts as a `blocker` to `root`. The coordinator reads the screen with the command in the record and decides. An approval record also carries the `lr watch prompts answer` command that settles it.
- A question dialog on `root_terminal` already waits on the owner there, so it raises no record, while `lr watch prompts show` still lists it. During execution, an owner question goes on the board, never in that dialog (R8 in `SKILL.md`).
- The coordinator's own approval cannot wake the coordinator. Register one independent agent as its supervisor with `drive.py desk --name <lane> --terminal <handle> --supervisor`. The coordinator's approval then posts as a `blocker` to that lane, and the watch types the record's line into the supervisor's terminal with `orca terminal send --enter`.
- Once `lr watch prompts bind --drive <id> --slack-cards` has set the drive's `slack_cards`, the watch posts a `needs_you` card at the root of the owner's DM with its own thread, for the drive's newest coordinator session, by using `cc-slack dm-status`, whenever no supervisor can take the prompt. That covers a drive with no supervisor, a supervisor at a prompt, `unknown`, or `unreachable`, a wake Orca refuses, or a prompt still open five minutes after the supervisor's record. Without it, the owner gets no Slack message for a dialog.

**Wake an idle supervisor once per prompt.** Orca must read the supervisor terminal `tui-idle`, and the screen must read `idle`:

| Supervisor's screen | Claude Code | Codex | The watch |
|---|---|---|---|
| `idle` | the plain empty `❯` box, nothing running | the `›` line with no draft, nothing running | types the line |
| `busy` | a turn running, a draft, or a list under the box | a turn running or a draft | types nothing and looks again next poll |
| `waiting` | a dialog | a dialog | types nothing; the owner gets the `ask` |

The row records what Orca reported for the line: `turn_started` means Orca saw the turn start, and `input_accepted` means Orca took the line without seeing a turn start. The watch never types a line twice.

**Wait with `cci watch` in the supervisor.** A supervisor holding a foreground wait reads `busy` while waiting, so it runs `cci watch --drive <cci drive> --to <lane> --for 0` (printed by `drive.py desk --supervisor`) so the record ends the wait. The supervisor reads the live screen and answers only within what the owner already authorized. For anything else, it answers nothing, and the owner card follows five minutes after the supervisor heard.

**Resolve records when the prompt clears.** When the prompt leaves the screen or its terminal leaves the watch, the watch posts an `unblock` record resolving each record it raised, and an `info` card under the owner's card stating that the prompt cleared. The watch answers no prompt, types into no terminal waiting on a prompt, and never stops, restarts, releases, closes, or signals anything.

## Answer a terminal dialog

**Use `lr watch prompts answer` to settle an approval dialog.** Orca refuses `orca terminal send --enter` and `--retry-request` into an agent showing an approval dialog with `agent_prompt_blocked`. Answer the dialog with the command the record names:

```bash
lr watch prompts answer --dispatch <ctx_id> --episode <key> --by root yes
lr watch prompts answer --dispatch <ctx_id> --episode <key> --by root no --instead "<what to do instead>"
```

- `--terminal <handle>`, with `--environment <server>` for a remote terminal, replaces `--dispatch` for a desk, the coordinator, or a worker known only by its terminal.
- `answer` reads the live screen and types only when an approval dialog is open there and matches `--episode` when given; otherwise it refuses. It types the number of the first option in the dialog's closing list that reads Yes or Allow, or No or Deny, as raw keys, then reads the screen until the dialog closes. Numbered lines in the command preceding the closing list are ignored. A denied agent ends its turn, and Orca refuses new prompts until one is typed.
- `no` requires `--instead`, which `answer` types and submits as raw keys once the screen shows the agent's empty input box.
- `answer` posts one `status` message to the Run mailbox of the worker that owns the terminal, or of the Run the terminal coordinates, naming who chose which option, the dialog's title, and its episode, which names the watch's saved screen. The dialog's text stays out of the mail. With no Run to hold that line, `answer` refuses before typing.
- A dialog whose options carry no numbers, such as a folder trust check, is refused; answer it in the terminal.

**Check prompt watch health with `show`.** `lr watch prompts show --drive <id>` prints the last poll as a count line, then one line per terminal that is not `clear`. `ledger.py summary` prints the same lines after its counts. `PROMPT-WATCH-DOWN` leads them when the last poll is over two minutes old; run the `start` command it names.

## Respect shared API budgets

**Share Buildkite and GitHub rate limits across the machine.** Every lane, desk, watcher, and daemon on the machine shares the `gh` account's GraphQL and REST limits, the GitHub App installation's limits, and the Buildkite REST limit.
- Read a job's log once and keep it on disk. Never loop a log read over a build's plan shards, and never scan more than the handful of shards a failure names.
- Size any scan of more than a few hundred logs and send the count to the coordinator before running it.
- Prefer an artifact or annotation over scraping a log.

**Run one poller per PR or build (R3 in `SKILL.md`).** Resuming a poller after a wake preserves its backoff, while starting a second poller or restarting from scratch resets it. A 429 or 403 mid-poll gets a fixed wait and a retry, never an immediate retry or a fresh loop.

**Read GitHub only through `ccx`.** Drive tools read GitHub with `ccx vcs pr status`, `vcs pr state`, `vcs pr watch`, or `ccx vcs gh -- <gh args>`, setting `GH_REPO` on GraphQL reads. Writes such as a merge, label, or comment stay plain `gh`, since `ccx` refuses them. `ccx` charges each read to `CCX_CALLER`, and `ccx vcs auth status --json` lists every quota and the callers that spent it.

**Enforce the five-minute cache floor and quota floors.**
- No loop reads faster than every five minutes, and every `ccx vcs gh` read in a loop passes `--max-age 5m` so repeats within five minutes hit `ccx`'s machine-wide cache. A read that decides a merge passes `--max-age 0s`.
- With under 1,000 calls left in a quota, `ccx` refuses the read with one stderr line starting `ccx: GitHub rate limit floor:` naming the quota and its reset; the caller stops reading until that reset.
- The relay checks every quota each pass and posts one `urgent` `GITHUB-QUOTA-LOW` record to `root`, once per reset, for any quota under 1,500, naming the five callers that spent the most.
- Both lane brief templates carry this rule.

**Batch and cache every GitHub read.** A desk or tool that needs one fact for many PRs reads it in one batched query per pass, never one call per PR. A surface showing a PR caches it by head, revalidates on a floor of one minute or more, and re-reads its files only when its head or base moves. The secondary limit fires on bursts of writes, such as a dozen PR creates in a quarter hour, so a ship storm spaces its creates. Fix any per-PR loop in the tool itself instead of adding a second reader beside it.

**A lane writes no poll loop, and never GNU `timeout`.**
- The `shell_waits` hook blocks a Bash call that waits on a shell loop, a sleep of a minute or more, or a sleeper script, and the `lane_waits` hook warns on one that backgrounds a process, wraps a shell in `timeout`, or runs a watcher.
- A wait goes through `Monitor`, an Agent notification or `CronCreate`.
- A watch that must hold the call runs as `lr wait <seconds> <source>... -- <command>`, which ends the command's whole process tree at the deadline, when a source wakes it, or when `lr wait` itself is signalled. The tree includes a nested `timeout`, which starts its own process group, and every orphan reparented to launchd.
- A command already running in its own ancestry exits 3, so a script that reaches itself through another fails at once instead of stacking one copy per pass.
- `bounded.py <seconds> -- <command>` gives a desk script, not a lane, the same bound.
- When a lane stops, the `lane_watchers` hook ends every `timeout`, `sleep`, `tail`, `cci watch` or `lr wait` tree the lane left reparented to launchd, and tells the lane what it reaped.

## Write a watch that cannot miss

**Read checks on the exact head.** Right after a push a check read can belong to the old head or list too few checks.
- A watcher reads check runs on the exact head, counts the full set, and requires the named required jobs (such as the lr-go job) before it says green. "None pending" right after a push means nothing has registered yet.
- A merge-on-green watcher requires the rollup checks on the PR's current `headRefOid`, so an amend cannot merge untested. An auto-merge set is not merged.
- A CI watch carries a time bound from its base run and reports when the wait exceeds four times the base duration.
- Never filter inherited build failures out of a watch: an `INHERITED-BUILD-FAILURE` line stays visible.

**Wake on the records, not only on the PR.**
- A watch on PR state also wakes on new records about that PR addressed to the lane, because a PR view can lag origin.
- A lane's `cci watch` filters on `--to` alone; a `--topic` filter drops the coordinator's untagged orders. Silence past a deadline means `cci grep` by hand.
- A desk's wait filter keys on the record's own PR, never on seq ranges or an exclusion list.
- A blocked lane arms a `Monitor` on `cci watch` for its records, or ends the turn on a typed `blocked` record, so the supervisor wakes it.
- A wait on another lane's PR checks its owner: read the PR's `updatedAt` and the owner's records by the second tick, then ask the coordinator with a handoff.

## Supervise and rotate lanes

The rule these lines serve: every turn of a launched lane ends on a typed record that says who resumes it. [supervisor.md](supervisor.md) holds that rule and each way a lane went quiet. This section describes the Go supervisor; the Python mover keeps its older wake and direct ask until the cutover.

### Run the supervisor

`lr supervisor run` runs under launchd and keeps required lanes alive without coordinator action.
- Each tick reads the bus once and `cci who` once. A fresh store adopts only the lanes live at that moment, and a tick whose sources do not all answer acts on nothing.
- The supervisor never signals a process; a lane leaves only through the record that stands it down.

### Track lane ownership and retirement

The `lanes` table of the drive store holds one row per worker, and only the supervisor writes it.
- A worker enters it from the `SPAWNED` state record that `drive.py spawned` posts at each launch. `lr launch start` posts placement `orca`, the relay posts `sprite` for a Sprite launch, and the Agent hook posts `teammate` for an in-process lane. The record carries the brief, model, effort and role.
- A relaunch also carries `successor-of`, from `--successor-of` or the brief's `Replaces:` line. The successor takes that worker's PRs, and the supervisor posts the one `stand-down --successor` record cci routes mail by.
- Helper roles stay out unless the brief carries a `Standing:` line, and `<lane>-handoff` writers stay out. Each tick also adopts the lane of a standing session such as `chores` when `cci who` lists it live with no row, from `<state dir>/<lane>/brief.md` and the model and effort `lr launch` gives it.
- The coordinator and every desk change a row only through `drive_supervisor.py request <stand-down|park|unpark|owner|done> <lane> --config lr.json`, with `--successor <lane>`, `--last-read <seq>`, `--pr <n>` and, on a desk's stand-down, `--open '<items>'` or `--open none` as the verb needs. Nobody posts a `stand-down` for another lane. `drive_supervisor.py route <lane> --config lr.json` prints where that lane's mail goes.
- A lane's `head` and `opened` records move their PRs to it, `landed` records drop them, and an `owner` request moves them.
- A `<lane>-handoff` writer's `handoff <lane>: doc <id>` becomes the newest handoff of the lane holding that work.

**A lane is retired only by typed records, never by prose or by its name:**
- its own `done` with no `--re` (a `done --re` closes that one record's item and leaves the lane live), `stopped`, or `stand-down` record;
- a `stand-down` request, which retires it or hands it to the successor it names;
- for a lane that never came up, another lane's records taking its last PR.

A numbered spawn such as `desk-4` takes over `desk-3` only when its launch names it. A lane told in prose to stand down remains active until it posts `cci post --kind done`, so a death mid-task still gets a successor. The relay asks for `done` when a lane's Orca worker reports `worker_done succeeded`.

**A stand-down of a live lane, or an `owner` request that takes every PR a live lane holds, carries `--last-read <seq>`,** the newest record of that lane the coordinator read with `cci tail --lane <lane> --since <its wake seq> --oldest`. An `owner` that leaves the lane a PR needs none.
- `lr request` runs the same check before it posts: on an unread lane it posts nothing, exits non-zero, and prints that lane's unread records with the command to run again.
- The supervisor still refuses one that reaches it citing none, or one older than the lane's newest record, since a lane can post between the two. It holds the lane with its PRs and posts a `defect` to the coordinator and the lane. A held lane is never relaunched or retired, and its next record makes it live again.

**Stop an ended lane's Sprite.** Every five minutes `lr stop-sprites` runs `cc-remote orca stop <lane>` for each retired Sprite lane whose dispatch settled. A lane the Run lists no dispatch for qualifies when its `done` record or a withdrawal retired it.
- A cc-remote older than 0.27.4, the first whose `orca stop` works on a finished lane's Sprite, skips the pass, and its ended lanes wait for the pin to reach that release.
- A failed stop of a Sprite `sprite list` no longer shows closes. A stop that failed on a 5xx or a timeout is tried every pass until it stops, is gone or a person leaves it through `lr stop-sprites`, under one `SPRITE-STOP-FAILED` blocker and one closing line. Any other failed stop gets three tries and then one blocker; [orca-sprite-workers.md](orca-sprite-workers.md) has the rules.
- A live, held or unsettled lane keeps its Sprite.

### Check liveness and wake parked lanes

A lane is dead only when it lacks both a live process and a record within `STALE_AFTER` (15 minutes). Silence and activity are measured from the lane's own cci records, never from `cci who`. A `cci who` row is a process fact only, because every in-process teammate of a live coordinator session reads live there, however long ago it stopped. A live process is a live `cci who` row, a live Orca dispatch, or an in-process teammate on a live lead session's team roster.

- **Parking.** A lane's `ask`, `blocked`, or `blocker` parks its row, with the gates the record names: `--pr`, `--target`, `--lane-ref`, or a `card-` id. A parked lane with no live process gets no successor. Any later record from the lane clears the wait.
- **Events that clear the wait and page the lane:** the answer with `--re` or `--resolves`; `landed` naming the PR; `release`, `applied` or `fix-live` naming the target; the named lane's `done`, `stopped` or `fix-live`; the owner's pick on the card.
- **A `blocker` addressed to a lane about one of its open PRs,** or a coordinator `ask` naming one of its PRs by using `--pr` or `#<n>`, pages it once per blocker `topic`, parked or not.
- **Unread records.** A record addressed by name to a live lane stays unread until the lane reads it, whatever its kind, unless the machine wrote it: `supervisor`, `desk-runner`, `board-watch`, `pr-read`, `prompt-watch` or `stall-watch` as author, or a `head`, `state`, `opened`, `heartbeat`, `github`, `claim`, `spawn`, `board.card`, `queue` or `lr.*` record, or a `review` other than a failed grade. An `ask`, `lr.ask`, `decide` or failed grade is an order whoever wrote it, and so is an `owner` record a lane wrote. The cci hook hands mail only to a running turn, so a lane idle at its prompt reads nothing. Read means a tool call after the record, the `seen_at` of the lane's `cci who` row, or a record from the lane after it. A live Orca dispatch row proves neither. A standing desk other than the meta lane with an order unread for 30 minutes is lost, relaunched from its brief whatever Orca reports ([supervisor.md](supervisor.md)).
  - A standing lane holding an order gets one `WAKE` on the first tick 30 seconds or more after it, and any live lane holding any unread record at 5 minutes, parked or not, unless its Orca or roster inbox still holds an unread `WAKE`: that one already tells it to read all its mail, so a lane in a long tool call answers one `WAKE`, not one per record. A Sprite lane gets its `WAKE` typed at 30 seconds for any unread record, since mail does not start a turn there.
  - After 10 minutes any live lane still holding an order reaches its notice desk, `root` unless it is a meta lane, in one `blocker` per tick naming every such lane and order. A later order on that lane starts its own 10 minutes.
  - `--resolves` or `also_resolves`, retirement and a hold drop the order. A failed read or post skips only this chase for the tick and raises nothing.
- **A page** reaches a live lane through its roster inbox or `orca orchestration send`. A process that is gone gets a successor after the stale window, carrying the page.
- **A page drops** when a record resolves the blocker by using `--resolves` or `also_resolves`, or when the PR lands, moves to another lane, or gets a new head. A fresh store pages no lane from re-read records.
- **No answer to a page.** If a paged lane posts nothing within 30 minutes of its wake and has made no tool call in that time, it gets a successor. The successor's check for live processes in the worktree keeps two writers out.

**A PR that needs work pages its owner.** Quiet is never dead.
- A red or conflicting PR wakes the lane parked on it with `--pr`, once until a new head.
- A lane that posted nothing for one hour and owns an open PR that is red, conflicting, or unapproved gets one page naming those PRs, unless it is parked on something else.
- A closed PR is disowned. `done` is refused while the lane owns an open PR, so it posts `blocked --pr <n>` until each lands or closes.
- A retired lane stays retired whatever it still owns, and nothing it posts afterwards revives it. When its PR goes red or conflicting, one blocker tells the coordinator no lane owns it and names the `lr request owner <lane> --pr N` that hands it on.
- A green, approved, conflict-free PR waits only on the owner's tap and needs no live owner.
- PR states come from one GraphQL read per 50 PRs, reread every 10 minutes.

### Query lanes and PR ownership

Every desk, and the coordinator, checking whether a lane is at work runs `drive_supervisor.py lanes [<lane> ...] --config lr.json [--json]`, never `cci who`, never a `cci tail --lane` piped through `tail` or `grep`, and never its own script. The `root_reads` hook nudges a coordinator that tails instead.
- Named lanes print one line each with state, liveness, the newest record's kind and seq, PRs, successor and standing duty, and `unregistered` for a name the registry lacks. When that lane has posted, the line adds `posting` or `silent`, the time it was last heard, and its newest record's seq, so a lane that posts never reads as unowned.
- Every lane that has posted has a row, marked `unregistered` when no `SPAWNED` record named it, and a lane reads `live` until its row retires.
- Each PR has one owner, the row that lists it. That owner is the last lane to post a `head` or `opened` record for the PR that is not addressed to its current owner, so a desk relaying a rebase leaves ownership in place. The owner also moves to the lane an `owner` request names, while prose namings are ignored, or to the successor of a stand-down or of a launch naming the lane it takes over.

### Relaunch, rotate and keep standing duties

**Relaunch dead lanes as `<lane>-<n+1>`.** A lane that stopped or went silent gets a successor `<lane>-<n+1>`, which takes the stem's mail when its launch record registers it, so posters keep addressing the stem. The supervisor builds its brief with four parts:
- A `ccx:` line that keeps the old lane's placement: `place=local why=...` when its brief pinned it to the Mac, `place=remote` otherwise.
- A `Register slice:` line for the new name.
- The newest handoff doc, or the order to spawn `<lane>-handoff` from [handoff-subagent-brief.md](handoff-subagent-brief.md) when none exists.
- Before any write, `drive.py lane-status --held` by its installed path for the worktrees, then `cci who` for the predecessor's session: only a live session whose status is not `idle` stops the successor. Then the old brief.

The supervisor launches the successor with `lr launch lane` and posts one `RELAUNCHED` report. Once the successor appears in `cci who`, one `stand-down --successor` record carrying the PRs moves the old name's mail to it. A `SPAWNED` record for that name from another brief, such as a lane the coordinator spawned by hand, withdraws the supervisor's launch.

**Hold failing launches and report hourly.** The supervisor holds a lane and posts one `blocker` to `root` when a launch is not live 15 minutes later. It does the same when successors die within 30 minutes of launch twice in a row, or when the lane's brief is missing. Retired lanes are never relaunched. Once an hour, the supervisor posts one owner-facing report to `root` naming each lane it restarted and why.

**Rotate lanes in place with `ROTATE`.** A live lane that posted within the window and passed its context line gets one `ROTATE` ask on cci. An idle lane, or a lane whose newest record is `handoff`, `done`, `stand-down`, or `stopped`, is never asked. The asked lane flushes in place, so its `handoff --ccn <doc>` reply becomes its newest handoff and the lane keeps working. The supervisor never relaunches a lane for size; the handoff seeds a successor only if the lane later dies. A lane's "flushed" reply is a pre-compaction checkpoint, not a handoff: leave it running.

**Keep every standing duty owned.** A brief's `Standing: <duty>` line marks work the drive keeps for good.
- A lane quiet for ten minutes is checked at its host, never read as dead from its silence. A Sprite still running its worker is left alone, so a same-family launch never stands a live desk down.
- On proof it is gone, or when its worker ended with the duty open, the supervisor relaunches it from its brief with no model and no root step, and logs one `RELAUNCHED` record to the meta desk.
- A desk that comes back and dies three times within an hour is held with one `blocker` to the meta desk naming the cause.
- A lane retired by a `done` or `stopped` record, or stood down on an order, never relaunches: its duty ended, and one `blocker` names its brief.
- A lane launched as the successor of a standing lane, by `--successor` or by the next number of its name, holds that duty in its registry row whatever its brief says, so its own `done` record leaves it live. A coordinator's `lr request done <lane>` ends that duty with the lane.
- `lanes` prints the duty on the row. A stand-down of a standing lane without a successor is refused at post time. Deleting the brief's `Standing:` line ends the duty its brief declares.
- `lr request revive <lane>` sets a retired lane back to live, and the revived lane takes the standing duty of the lane whose name it succeeds.

### What the supervisor does without the coordinator

| Check | What it does | What reaches the coordinator |
| --- | --- | --- |
| Lane liveness (`lr lanes`, the supervisor under launchd) | One registry row per lane, relaunch from brief and newest handoff, rotation in place, a `Standing:` duty with no live owner for ten minutes. A lane that posted `done` or `stopped` is retired for good | One `RELAUNCHED` record to the meta desk per relaunched duty; one `blocker` per duty held after three deaths in an hour or ended by an order, a `done` or a `stopped`; one `defect` when a relaunched lane dies again; one `blocker` to its desk or the meta desk for a lane no host probe settles |
| Sprite stops (`lr stop-sprites`) | The Sprite of every lane that ended, stopped once with `cc-remote orca stop`; a live, held or unsettled lane keeps its Sprite | One `blocker` per stop that fails |

The alert launch, ordered-release, landed-not-live, Namespace and Mac checks follow.

**Launch incident lanes for an alert nobody answered.** The supervisor's Alerts check reads the drive's alert records each tick.
- An alert with no `incident` record and no fix lane naming it after three minutes is launched by the supervisor itself: the fix and evidence lanes from `reference/alert-fix-brief.md` and `reference/alert-evidence-brief.md`, and the Slack writer from `reference/alert-comms-brief.md`, through `lr launch incident`, with one spawn record to `root`. A `blocker` to `root` goes out only when that launch fails. The coordinator never has to read the alert first.
- A fix lane that misses gets its Opus fallback on `incident-opus`, the only alias that runs Claude fast mode.
- The check reads each `alert_channels` channel every tick, and a card's thread only when its `reply_count` or `latest_reply` moved within the hour. It recognises a red Watch or red tenant smoke line in a card or reply and launches `<slug>-fix`, `<slug>-evidence` and `<slug>-comms` on the first tick that sees it. Only a failed launch of the fix lane raises `ALERT-UNOPENED`; a refused evidence or comms lane is its own `LAUNCH-FAILED` record.
- The slug is stable per monitor, component and env. A later red on the same key within a day sends the open fix lane one `ALERT-AGAIN` note and launches nothing. The fix brief carries the component, env, monitor, release, who released it and both links.

**Raise an ordered release still at its gate.** Each minute the lanes mover reads every release a drive record names with `release` and `order` attrs through `tools/ci release status --json`. One waiting at its approval gate five minutes after the owner's order, or five minutes after it reached the gate when that came later, draws one `RELEASE-GATE` `blocker` to the coordinator (`root` on the wire) naming the release, the gate and the lane. An apply that refuses a delete or replace outside the approved plan draws one `RELEASE-STOP` the same way (`reference/release-lanes.md`).

**Name every landing that is not live.** Each tick the supervisor compares landed PRs against the releases their targets have shipped since. A PR's targets are the components `tools/ci release select --pr` reaches from the files its landed commit changed, never its title scope.
- A landing at least an hour old is owed on each of its targets that runs it nowhere and that no running release build or recent release carries.
- A build that is not failed, closed or a dry run carries the components its unstopped `apply-<component>--<env>` jobs deploy, at its commit. A failed build carries nothing, so its PRs count again.
- A release from `tools/ci release status --json` that is not a dry run carries the components its `unchanged` list names, at its commit: its plan found nothing to deploy there, so Pulumi recorded no apply and `release live` still shows an older commit.
- All owed targets share one `blocker` to `root` naming each target's PRs and saying the next release that picks the target ships them and the owner starts it. It prints no release command, since a lane never starts a one-off release. A new one, resolving the last, posts only when that set changes; `ledger.py summary` marks the same rows `LANDED-NOT-LIVE`.
- A target that an open `hold` from root names in `--target` stays quiet while the hold is open, and until its `until` attr passes when it carries one, an RFC 3339 time. Root posts that hold when the owner pauses releases, as when routine cuts wait for the morning while the owner sleeps.

**Extend Namespace boxes before their deadline.** The nsc check covers every box `nsc list` shows with a deadline that `lr nsc create` or `lr nsc extend` recorded on the bus. An hour before the deadline the supervisor extends the box by its create duration while its lane is live, and tells the lane. A refused extend warns the lane to recreate the box. A box whose lane is retired, or that has no lane label, is left to expire after one `urgent` to the coordinator.

**Note Mac queue waits.** When the drive's `runner.json` names `landing.mac_queue` (`org`, `queue`, `limit_vcpu`) and a CI job on that queue has waited ten minutes, the supervisor notes the lane holding each of the longest-held macOS boxes, one box per waiting job. The note names the job, how many macOS boxes lanes hold against the limit, and the hand remedy: cancel the job, retry it; never inside a trunk-merge build without asking. `lr nsc create` of a macOS shape prints the same count and limit. The check touches no box and no job, and sends one `report` per holder per Mac wait.

### Rewrite SendMessage to a retired lane

A `SendMessage` to a stood-down lane resumes it on its stale brief. The hook pack rewrites every such message, from any sender, to where the registry routes that lane, `drive_supervisor.py route <lane>`: its successor, or `main` when it retired with no successor.
- The coordinator's own message to a lane retired with no successor is only warned.
- A message mentioning a stand-down is only warned, never forwarded.
- A `cci post --kind stand-down` naming any lane but the poster's own is blocked with the `request` command to run instead, so only the supervisor posts the stand-down cci routes mail by.

### Kill a process the owner named

A recorded owner card answer that names a process is the owner's own word naming it for the process guard: kill that process once, for the lane the card belongs to, within the hour of the tap. When the owner says kill it, kill it, with no argument.
