# Lessons

No lane needs this file to do its job. It is history for a reader asking why a rule exists. Each line gives one way a drive went wrong and where the rule that now prevents it lives. Follow the rule, not this file. The dated version with the owner's words is the cc-notes doc `1b3c91f` in the monorepo. Each line reads `<what goes wrong> -> <rule id, or file and section>`.

## Contents

- [The coordinator's own conduct](#the-coordinators-own-conduct)
- [Landings and the merge queue](#landings-and-the-merge-queue)
- [Grading](#grading)
- [Orca workers, lanes and the bus](#orca-workers-lanes-and-the-bus)
- [Incidents and releases](#incidents-and-releases)
- [Slack](#slack)
- [Desks and the dashboard](#desks-and-the-dashboard)
- [Watchers, rotation and compaction](#watchers-rotation-and-compaction)

## The coordinator's own conduct

Reporting and reading
- Reporting PRs as "open" or "in CI" while stacks sat on merge conflicts for days -> R10, landing.md
- Giving a PR's state from memory or a plan table after it had landed, or with no ledger row -> R7, R8
- Restating status after every event, or with PR numbers and record ids the owner cannot map to the plan -> owner-report-table.md, communicator-brief.md
- Reading PR bodies, diffs, audits, logs and build pages in the coordinator's window, or parsing JSON there, until its context filled -> R1, R2
- Relaying a lane's ETA for a priority PR instead of reading and labeling it directly -> R1, R13
- Acting on a stale message or a withdrawn verdict instead of the current record -> R18, bus-contracts.md B2
- Naming an incident mechanism without reading what production operations were logged the day before -> R24

Lanes, messages and waits
- Owed items and owner asks left as branches with no PR, or appended to a busy lane's queue until the owner asked -> R6, R13
- A lane parked at a confirm gate, one Monitor or Bash watcher per build, or a completion lost because a subagent or codex ran in the background with no live wait to wake the turn -> R3, orca-lane-brief.md, bus-contracts.md I7
- Messaging a running or finished agent re-ran its brief or spawned a second copy; answering duplicate idle notifications -> R4, bus-contracts.md I4
- Lanes or merge loops sharing one active worktree, so HEAD moved and a diff or scratch file landed in the wrong PR -> R4, orca-lane-brief.md
- A handoff that stopped dozens of lanes at once with nothing flushed -> rotation in place, supervisor.md, watchers.md
- A Claude Orca worker wrapping codex through the codex skill -> orca-workers.md
- Finished subagents left running, then the subagent limit reported as a blocker -> R23
- Orca worker traffic run inline, losing launches and sending rulings to superseded desks -> R9, orca-desk-brief.md O1
- Rulings sent by a channel the lane does not read, or a stand-down never confirmed on disk -> R18, bus-contracts.md I4
- Watches that died silently, or an open `AskUserQuestion` holding back records -> R9, bus-contracts.md I7
- An urgent hold left without a decision owner or a clock -> R9a

Process, tooling and the task list
- Process fixes and lessons left in scratch files or reports until the owner prompted a PR -> R14
- The same tooling defect worked around repeatedly instead of sent to the meta lane -> R14, meta-lane-brief.md
- A fix for a misfiring supervisor waiting in a stack -> R14
- A tooling change that widened a permission, built from an inference of the owner's words -> meta-lane-brief.md
- A forced handoff with nothing written, a plan rewritten at each handoff, or a hand-rolled supersede chain -> R5, compaction.md
- Lanes with no task, finished lanes left `in_progress`, owner asks with no task -> R5, Records and the task list in SKILL.md
- A desk kept alive all drive, re-reading its history instead of rotating on its ledger and cursor -> R4, R5, R8

Landing, releases and CI
- Release cuts approved unasked, or cut with known fixes unlanded so each failed on the next open bug -> R11, release-lanes.md
- Reds left for an hour with idle owning lanes, or `STALLED-RED` lines printed and never sent -> R12, red-sweep-brief.md
- Local builds, test suites and batch jobs on the Mac starving incident checkouts, machine load holding a remote build, a remote wrapper falling back to the local compiler -> R15
- A desk landing one stack per pass, one PR at a time bottom-up, relabeling ejected heads, or pacing labels every 20 minutes -> R9, R10, landing.md D3, D16, D19
- A desk run on hand-rolled scripts, with stale refreshes and moved or unreported heads waiting silently -> R8, R9, landing.md D10, D14
- PRs and asks missing from the ledger, or a repo-wide desk commenting on other people's PRs -> R8, landing.md D2

Owner words and rulings
- An owner routing instruction applied late, leaving extra lanes and PRs for one fix -> R17
- Times in UTC, a "why" question delegated, drive codenames and ids in owner-facing text, no human layer first -> R21, communicator-brief.md
- A design built in part, or sent to a ship lane before its contract and footers -> R22
- Design checks confirmed one at a time, parking lanes -> R22, orca-lane-brief.md contract records
- An exit clause added to an absolute ruling, lane question lists forwarded unfiltered, an option marked recommended before the options lane replied -> owner-rulings.md R19
- PRs held for a confirmation the plan or a board pick had already given -> R19, owner-rulings.md
- A ruling recorded as an id and a gist and applied to one mechanism while siblings, budgets, cards and briefs kept the old assumption -> R19, owner-rulings.md
- A lane contract narrowing the owner's pick, accepted without comparing his words -> owner-rulings.md `judge_watch.py`, `SHAPE-UNTESTED`
- A standing rule lost by sharing a record with a one-off, being listed as a range, or being marked done -> bus-contracts.md I6, owner-rulings.md
- The coordinator's own rule cited as the owner's to hold an incident fix -> owner-rulings.md, drive-watchdog-brief.md
- Slack watched by the coordinator or lanes -> R20, slack-lane-brief.md
- Incident mode held open for hours on a path the plan deletes, or after failures stopped -> R16, active-alert-brief.md
- Option branches not built, deployed to staging or tested while the owner was away; a testing lane stood down on a PR bar -> The owner's surfaces and R23 in SKILL.md
- A card left on the owner's board after the event it asked about; PRs said to wait on taps with no board row -> The owner's surfaces in SKILL.md, `cci owner-waits`

## Landings and the merge queue

- Green approved stacks waiting on one serial desk, or a prefix enqueued above a held PR -> landing.md D3, D16, D19
- A queued PR ejected on a conflict while nobody watched -> landing.md D18
- A whole-user branch namespace registered, pulling unrelated PRs into the drive -> landing.md D13
- A lane enqueuing on its own under a hold, or a hand-written line freeing the PR it meant to hold -> landing.md D1, D6
- A PR enqueued without its fidelity grade and landing without its test -> landing.md fidelity gate on every prefix head
- A fast-tracked release PR landing with nobody starting its release -> landing.md D20
- Writers shipped before every reader of the old format was live -> landing.md D21
- A time-window hold kept only in a lane brief, then shipped inside the window -> landing.md D22
- A patch-identical restack discarding grades and holding an incident fix -> landing.md grades carry across identical restacks
- An incident fix landing without a release or a `fix-live` check -> landing.md D20, active-alert-brief.md
- Drive machinery inside `stack-enqueue`, with no merge mode asked -> landing.md merge mode and taps
- A tapped PR out of the queue for an hour, and stale rows on the owner's board -> landing.md go-to-queue clock and board rows
- Taps lost when restacks moved heads, a review-bot hold read as no verdict, a red trunk caught late -> landing.md tap carry, `Held` findings, `DEV-RED`
- A hand `--dequeue` read back as a queue eviction -> landing.md holds and dequeue
- A hand-edited `runner.json` keeping the landing runner down -> landing.md runner config
- A squash carrying other open PRs into trunk unreported -> landing.md `CARRIED`
- Reds routed to finished lanes, or foreign trunk reds treated as the drive's own -> landing.md D2, D8
- Conflicting hot-set PRs never reaching the queue -> landing.md D7, merge-train-brief.md
- A conflicting PR relabeled in a loop, a stale `NOT-READY` line hiding an eviction, no trial merge against queued PRs, a reworked PR landing anyway -> landing.md, merge-train-brief.md

## Grading

- Row removals landing after the live environment was destroyed, failing every landing build -> grading-principles.md
- Landing read as done or not done from weak evidence such as stacked parents, moved trunk, label state, zsh word-splitting or a deleted base -> grading-principles.md, landing.md D4
- A child retargeted after its parent landed hit a deleted base; two PRs labeled apart lost the child -> grading-principles.md, landing.md D16
- A neutral `ai-review` or a wedged labeled PR left for hours -> grading-principles.md
- A stack graded by the landing build's overall color, or a green landing job read as applied when its plan artifact refused -> grading-principles.md
- Trust, policy or access removals judged safe without the source history or the owner -> grading-principles.md
- Checks that fail open, or never run against a known control, through empty inputs, empty derived sets, rate limits, wrong ids or truncated pages -> grading-principles.md
- A tooling defect reported as a PR regression when the branch only predated the fix -> grading-principles.md
- A pulled merge label treated as a stop, or a gate probed with a real label -> grading-principles.md
- The import-class bar applied to apply-path stacks -> grading-principles.md
- Plans compared across branches with no regard for plan time and intervening applies; a pre-apply plan read as post-apply under a confident negative headline -> grading-principles.md
- Heads moved by restacks mid-verdict, or a hold quoted against a moved head -> grading-principles.md, `--expect-head`
- A docs-only diff moving secrets to a path too many roles can read -> grading-principles.md
- Failure claims with no build number or time -> grading-principles.md
- Resource name fields missing under disabled autonaming; deletes with no provider read as data loss; a pruned build read as one with no plan artifacts -> grading-principles.md
- A guard or bar change verified without reading the fields it writes -> grading-principles.md
- A merge-label removal on pickup read as a queue ejection -> grading-principles.md
- A labeling window missed because the head moved during grading -> grading-principles.md, pin to the graded sha
- A refused stack reported as one harmless op instead of its whole pending census -> grading-principles.md
- A branch green on a moved trunk that renamed what it imports -> grading-principles.md
- A retirement PR green only because its tests were pruned, or orphaning live resources -> grading-principles.md
- Trunk claims read from a shared working tree on another branch -> grading-principles.md
- A scanner check judged from its title instead of its conclusion and body -> grading-principles.md
- An external constant taken from one source; a surface called absent without checking artifacts -> grading-principles.md
- Wire shapes changed between separately released components without dual-shape readers first -> finder-brief.md, fidelity-desk-brief.md
- Shared infrastructure changed without naming the old-tree workloads it touches or testing every cluster config -> finder-brief.md, fidelity-desk-brief.md

## Orca workers, lanes and the bus

- Lanes building on something other lanes were deleting, each PR green alone -> semantic-collisions-brief.md
- A mass kill of worker sessions for load or cleanup -> orca-desk-brief.md O6, orca-workers.md
- Lanes stuck on terminal prompts only their terminals showed, or a hook prompt freezing a coordinator -> orca-desk-brief.md O13, watchers.md
- Codex launches timing out at readiness with the spec undelivered -> orca-workers.md
- Rulings sent by `SendMessage` to a looping desk -> bus-contracts.md I4
- A worker blocked after its answer went by dispatch instead of `reply --id` -> orca-workers.md
- Retained terminals and worktrees from settled dispatches holding the Mac at high load -> orca-workers.md `orca-gc`
- A backgrounded desk pass or Monitor missing records, or a mailbox wait resolved against the wrong lane name or truncated -> bus-contracts.md I7
- Finished lanes asked for `worker_done` before cleanup -> orca-workers.md
- Standing desks ending after one quiet cycle -> orca-lane-brief.md standing desk contract
- Slow launches on a loaded Mac -> orca-desk-brief.md O14, O16
- Relayed mail counted as unread by the delivery cursor -> orca-desk-brief.md
- Incident clocks missing, or a fallback started by hand an hour late -> orca-desk-brief.md, active-alert-brief.md
- A resume fix reported ready on a neighboring case, with uncovered cases left in backlog -> active-alert-brief.md, unfixed-case-brief.md
- Relays answered on the Run thread resent until marked lost -> orca-desk-brief.md O2
- Case lanes launched from `worker_done` text after a stand-down -> active-alert-brief.md, unfixed-case-brief.md
- A judge model answering a worker's question from its brief alone -> orca-desk-brief.md O4
- A lane silent after posting its mechanism -> supervisor.md
- A credential reported out of reach without checking the parameter store -> orca-lane-brief.md
- A brief ordering a key rotation into a parameter the owner writes, stopped only by AWS access -> orca-lane-brief.md credential stores, `credential_writes_need_his_words`
- A coordinator `decide` stopping the incident clock, or `--re` directives ignored -> orca-desk-brief.md
- Many conflicting PRs waiting for individual rebases -> merge-train-brief.md

## Incidents and releases

- Chained launch and fence lines in one record failing at the runner -> alerts-desk-brief.md
- A verdict gate before the fix lane, a wrong launch route, comms waiting on the coordinator, a merged config left stale with no owner -> active-alert-brief.md
- Customer and `#outage` updates waiting for the mechanism, and a late retro lane -> active-alert-brief.md
- A deploy watch that passed while runs stopped starting -> alerts-desk-brief.md
- Hand-written launch lines, a rejected withdrawal, PR prose requested from the coordinator -> active-alert-brief.md
- A down runner met with a stop for an owner choice, then a duplicate lane -> active-alert-brief.md
- Incident lanes without a mechanism past the fix-live bar -> active-alert-brief.md
- Briefs hand-written without standing orders when the run verb was refused, or launched with unfilled placeholders -> active-alert-brief.md
- Case lanes withdrawn by the wrong key, or an echoed `UNFIXED` line launching another generation -> active-alert-brief.md
- A release freeze set for a PR waiting on review, or a claim or hold on releases unasked -> active-alert-brief.md, manual-release-brief.md
- A fix brief that opened with a rollback -> active-alert-brief.md
- Another drive's incident fix lane left stopped or unruled -> active-alert-brief.md
- Workers chasing an alert whose cause is the CDN edge -> active-alert-brief.md
- An approved incident fix held by a non-required check or the merge queue -> active-alert-brief.md
- A failure on our own build treated as someone else's; a fix lane stopped on "attribution unproven" -> active-alert-brief.md
- Process and session-id gates holding incident fixes -> active-alert-brief.md
- A release smoke team closed as a false alarm by scoping the gate out -> active-alert-brief.md, alerts-desk-brief.md
- Monitors replaced by a release misread as real, a mute asked for in prose, credentials read from a store -> alert-fix-brief.md, alerts-desk-brief.md
- Incident releases shipped before the reporter's case passed -> active-alert-brief.md

## Slack

- An owner Slack link treated as context and answered inline with wrong times and unlinked numbers -> R20, slack-lane-brief.md
- Incident account updates waiting for a mechanism or a fresh approval -> slack-lane-brief.md
- Reporter threads answered with questions telemetry could answer, over-posted, with unproven causes stated as fact -> slack-lane-brief.md
- The updates channel silent for hours, then hourly clock posts -> slack-lane-brief.md
- Comms lanes unable to post for the wrong agent type or a broken tool -> slack-lane-brief.md
- The owner's direct messages and team channel filled with status cards and lane prose -> slack-lane-brief.md
- A Slack reader waited on before the incident lane launched -> R20, slack-lane-brief.md
- The owner asked repeatedly for a thread grant he had already given -> slack-lane-brief.md grants
- A person answered from a lane's one-line summary of a thread -> slack-lane-brief.md, active-alert-brief.md

## Desks and the dashboard

- A shared desk queue slowing the owner's number-one outcome, with owed items not listed up front -> priority-desk-brief.md P1, P2
- Off-plan drift going uncorrected -> drive-watchdog-brief.md W1, W2
- A dashboard built only on request, rebuilt from corrections, and shown untested -> drive-dashboard.md, user-test-brief.md
- Sweep findings and board picks with no ledger -> drive-watchdog-brief.md W3
- Reds and conflicts mentioned once then forgotten, and a set of reds nobody owned -> red-sweep-brief.md
- A dashboard redesigned without opening the live one or its design record -> orca-lane-brief.md design lane, meta-lane-brief.md
- Rounds of a measure-and-improve loop running for days with no stop-or-go -> drive-watchdog-brief.md W4

## Watchers, rotation and compaction

- CI and GitHub budgets exhausted by per-item log and query loops, and burst PR creation hitting secondary limits -> watchers.md
- A standing ruling dropped across compactions while stale plan text re-imposed the gate it withdrew -> compaction.md
- Two active progress docs after a handoff, restore output truncated, answer titles carried without the ruling text -> compaction.md
- A generated progress doc growing without bound, or an in-place handoff edit folded to a digest -> compaction.md
- The `/compact` command typed into a picker or dialog -> compaction.md
- Shared docs overwritten by edits from an older copy -> compaction.md
- A compaction with no progress doc, losing hours of records -> compaction.md
- A prompt on a coordinator terminal unseen by transcript readers -> watchers.md prompt watch
- Approvals, reviews and refusals stalled unnoticed -> watchers.md stall watch
- Stacks sitting unrebased overnight because their lanes died -> watchers.md, supervisor.md
- A PR left on a stale base after its parent landed -> watchers.md
- A stale `NOT-READY` line hiding an eviction, or a PR evicted for conflicting with one already queued -> watchers.md
- A PR sent back for rework landing anyway -> watchers.md
