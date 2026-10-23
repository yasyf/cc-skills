#!/usr/bin/env python3
"""The scripted desk: root commands become durable actions addressed to a lane's current Orca dispatch, the Run inbox is read by sequence, ready bottom prefixes enqueue, and deadlines are checked, all without a model turn.

    desk-runner.py relay  --config PATH --key KEY --lane LANE --text TEXT [--reply-to MSG] [--deadline-minutes N]
    desk-runner.py launch --config PATH --key KEY --lane LANE --model M --effort E --brief PATH [--owner-directed]
    desk-runner.py policy --config PATH --key KEY --landing prefix|whole --revision REV --source TEXT [--supersedes REV]
    desk-runner.py run    --config PATH --desk orca|landing [--once]
    desk-runner.py rebind --config PATH
    desk-runner.py show   --config PATH
    desk-runner.py gc     --config PATH [--dry-run]

STDLIB ONLY. Every action lives in the store `actions.py` owns: one container per lane,
`desk-lane-<lane>`, whose owner is the lane's current Orca dispatch, plus `desk-landing`
and `desk-runner`. An action id is the command's key, so a command submitted twice
yields one action. A relaunch offers the lane's container to the new dispatch, and that
dispatch's first `started` reply takes it at the next owner generation, so a stale
dispatch can never start or finish an action.

A relay is accepted, then started and completed by the lane itself: it replies
`started <key>` and `done <key>: <result>` on the action's thread. The send is its own
`send:` action, delivery evidence only. A send whose response is lost, Orca's runtime
unreachable or its output not JSON, becomes `unverifiable` and is settled from Orca's request receipt or the recipient's mailbox.
With neither thirty seconds on, the runner sends the relay again on the same thread,
under the same key, and repeats that once every thirty seconds until Orca holds a
receipt or the message shows on the thread. A delivered resend logs nothing. A status
message the lane posts on the Run that names the relay's R-number or cci sequence
acknowledges it, as `started` when its subject starts so and `done` otherwise, and
settles every send of it, since a lane that already read its mailbox leaves no
message there to find. Two resends all lost the same way log `UNACKED` once, with
the count and the relay text on the record's path, and nothing more is sent. Any other
Orca error fails the send, though Orca names a request id on every refused mutation,
so a refusal is never resent as lost. A reply Orca refuses with `answer_conflict`, its question
already holding another answer, goes at once to the dispatch as a message on the relay's thread
instead. A send with a receipt is never resent,
and a send to a dispatch that is no longer the lane's live one logs `UNVERIFIABLE`
and is never resent. An accepted relay to a lane with no live dispatch and no
launch in flight fails at once as `RELAY-FAILED`, unless a send already delivered it;
a delivered relay is never failed or reported again. A lane has no live dispatch only
when it has no receipt or Orca answers that its dispatch is gone. When Orca gives no
answer, a runtime or connection error, a timeout, or output that is not its JSON, the
pass stops there and changes no dispatch's liveness and no relay's state. The first
such pass logs `ORCA-UNREACHABLE` once for the streak, each failed pass doubles the
wait before the next, up to two minutes, and the first pass Orca answers again logs
`ORCA-BACK`. `cci post` keeps only the first
line of a text over its 400-character limit in the record, clipped, and writes the
whole text to a file the record names by path. The runner reads a relay record's or a
root reply's text from that file and relays all of it, line breaks kept: a relay's
first line is the directive and its text runs to the end of the record. A file whose
first line is not the record's text is an attachment, and the record's own text is
relayed, unless cci clipped the record. A record whose file cannot be read on this
machine is refused as `RELAY-FAILED` naming the path, and nothing is relayed.

`run --desk orca` relays, launches, consumes the Run mailbox, sweeps stale mail, and
checks relay deadlines. Each pass takes the Run's oldest unacknowledged Delivery before
it reads the inbox and acknowledges it once that read is relayed, so Orca stops counting
a relayed or skipped message as unread. Each relayed message id is saved, and a Delivery
message with no saved id is relayed before the acknowledgement whatever its sequence, or
logged `UNRELAYED` when it sits behind the cursor and its type has no relay. Prompts
belong to `prompt_watch.py`, the one reader
of Orca's agentWait; the sweep reads its last poll and escalates `PROMPT-WATCH-DOWN` once an hour
while that poll is over two minutes old. It also reads the drive's desk inbox stream and turns
each new `orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>` line into one relay
per lane, a reply to the lane's latest open question no other reply claims, logged once as
`RELAYED` or `RELAY-FAILED`. Each new `orca-desk: launch <lane> [NOW] <model> <effort> [role=<role>] brief=<absolute path>`
line is the `launch` command under its key, `NOW` meaning `--owner-directed` and `role=` meaning `--role`, refused as
`LAUNCH-FAILED` while the lane has a live dispatch or a launch in flight, or while its brief has no `Register slice:`
line and the launch is not an incident lane's. Before any launch the runner refreshes the register slice under that
line with `rulings.py slice --refresh`, first writing `Register slice: lane=<lane> step=incident` into an incident
brief that has none, and writes the slice into every brief it attaches itself; a slice that does not read logs
`SLICE-UNREAD` and the launch goes ahead. Every verified
launch logs `LAUNCHED` with its dispatch and terminal. The runner reads each lane's
dispatch and terminal from the Run's receipt directory, `~/.claude/scratch/orca-launch/<run>`,
where orca-launch.sh writes by default, so a lane launched by hand is seen like one the
runner launched. An `orca-desk: alert <slug> <link> :: <what fired>`
line, which monitor-watch writes, records the transition and launches nothing. An
`orca-desk: incident` line in the same form, with `reported=<HH:MM>` or `reported=<H:MM AM|PM>`
before the `::` naming the first report's Pacific time, which the alerts desk writes when it judges a lane necessary,
launches `<slug>-fix` on the incident alias from the alert-fix brief, unless another owner holds the
incident: the root's `done` resolving a record that names the slug, its `stopped` addressed to
`<slug>-fix`, or another drive's root `answer` addressed to `root@<this drive>` naming the slug. The
runner checks at intake and again before the launch starts, fails a pending launch, and posts one
`state` record `INCIDENT-OWNED <key> <lane>: incident <slug> owned by <who>; no launch`.
Every incident fix lane the runner launched is on a clock from its verified launch: a
launch on the incident alias, or on a brief whose `ccx:` line says `role=incident` or names
an `incident=`, unless the launch's own role or that brief names a role other than `fix` or
`incident`, or the lane's name has an `evidence`, `proof`, `closeout`, `desk`, or `audit` part;
`desk-launch.py incident` launches its evidence lane with `role=evidence`. Each pass
reads the lane's own cci records of kind `mechanism`, `opened`, `ready`, `fix-live`,
`not-ours`, `stopped`, and `stand-down`, a `report` or `incident` that starts `MECHANISM`
or `READY`, and its `mechanism:`, `ready:`, and `fix-live:` Run statuses. A lane's records
count under `<name>` and `<name>-fix` alike, since the desk launches `<name>-fix` and its
lane may post as `<name>`. With no mechanism at
`deadlines.incident_mechanism_minutes` (default 10), or no PR at
`deadlines.incident_pr_minutes` (default 15), the runner launches `<lane>-opus` once per
launch, urgent, on opus xhigh in Claude fast mode, from the lane's own brief under a
fallback header it attaches to the briefs log as `<lane>-opus.full.md`, and logs
`INCIDENT-CLOCK <lane> mechanism` or `INCIDENT-CLOCK <lane> pr`. With no `ready` at
`deadlines.incident_ready_minutes` (default 20) it logs `INCIDENT-CLOCK <lane> ready`,
naming the missing milestones and the minutes each phase took. A later milestone counts
for the earlier ones, and each threshold logs once per launch. A `-opus` lane is clocked
and gets no fallback of its own; a lane whose `<lane>-opus` already has a live dispatch or
a launch in flight gets no second. When the Orca launch of `<lane>-opus` fails, on load or
otherwise, the runner launches it once more under `<key>:retry`; when that fails too while
the lane's clock runs, it logs `INCIDENT-CLOCK <lane> in-process`, naming both failures and
the fallback brief, for the root to start `<lane>-opus` in its own session as a
`long-running:lane-ship` Agent. A fix lane gets one Opus fallback: a lane an incident
executor adopted gets none from the clock while that executor's own Opus backup is launching
or live, and `incident.py` launches no backup while the clock's `<lane>-opus` is. The clock
stops at `ready`, `fix-live`, `not-ours`, `stopped`, or `stand-down`, or when the dispatch
settles, and `show` prints each clocked lane's minutes and milestones. Any milestone a sibling lane on the same
incident reached, a clocked fix lane sharing the lane's incident name, its name less `-opus`, `-case<n>`, and its last
`-<part>` such as `-fix`, `-fix-2`, or `-cred`, or its `incident=` slug, or the executor's Opus backup
for one of them, counts as the lane's mechanism, so no mechanism fallback launches; the PR and ready thresholds stay the
lane's own. A lane the root withdrew, or sent `stand-down` or `stopped`, ends its clock as `withdrawn by <who>` and logs
nothing. While any clock runs, a DEADLINE, UNACKED, HOLDS-UNREAD, LIVENESS, STALE-MAIL, or RECLAIM about anything outside
the incident waits in the view, and the pass after the last clock ends posts them as one `DEFERRED <n> escalations held
during <lanes>` record. A root `go` or `decision` to a release lane that orders a cut or `release start`, or a landing's
`release` record this runner posted, opens a cut clock only when it names components: the order's `--target` refs, or the
landing's `Release-With:` components, never a word of the order's text. With no `release` record carrying a build and no
`refuse` from that lane within `deadlines.cut_minutes` (default 4), the runner logs one `CUT-SILENT <lane> <component>`
with the hand command and queues `manual-release-<component>` on opus xhigh from `reference/manual-release-brief.md`, and
relays to it each later `refuse` that says a release already ships the commit or later. The launch waits `CUT_GRACE` after
the CUT-SILENT posts and fails as WITHDRAWN when the root resolves the CUT-SILENT or the order is answered or closed first.
The clock closes silently once the landing it holds settles, the root posts `--resolves` on the order, another lane's
`release` or `fix-live` record since it opened names one of its PRs, or the order resolves the lane's answering record.
A mechanism-to-PR clock covers every incident fix lane, the runner's and those the root
started in-process: a lane the runner launched as a fix lane, or, launched elsewhere, one named
`inc-`, `incident-`, or `outage-` whose name or brief, `<state>/briefs/<lane>.md` or
`<state>/<lane>/brief.md`, names no evidence, proof, closeout, desk, or audit role. Each pass reads the drive's records past a
saved cursor, the last hour on a first read. A `mechanism` record, or a `report`, `incident`, or
`state` whose text starts `MECHANISM` or `CANDIDATE`, starts the lane's clock. An `opened`, `head`,
`ready`, `blocker`, `blocked`, or closing record, or such a text record starting `OPENED`, `BLOCKER`,
`READY`, or `NOT-OURS`, from the lane or the other of its `<name>` and `<name>-opus` pair, under
either `-fix` alias, stops it, as do a root `stand-down` or `stopped` addressed to the lane and a
root `--re` or `--resolves` naming one of its PR-SILENT records. So does any lane's `opened`, `head`,
`ready`, or `fix-live`, or a root `go` or `decision`, that answers the mechanism: one that names its
seq with `--re`, shares its topic, or names one of its incident tags, where `SAND-5A`, `[SAND-5A]`, and
`SAND5A` are one tag. An answer posted before the mechanism counts, so that mechanism starts no clock.
A root `decide` or `decision` addressed to the lane, and the lane's own `done`, or a text record
starting `DONE` or `STOPPED`, settle it too. At
`deadlines.pr_silent_minutes` (default 10) after the mechanism, and every
`deadlines.pr_silent_every_minutes` (default 5) after that, the runner logs
`PR-SILENT <lane> #<seq>: MECHANISM at <time>, <n> min with no OPENED or BLOCKER record; <lane status> | <lane>: …`,
A lane with no worktree that is neither a fix lane the runner launched nor named `-fix`, `-fix-<n>`,
`-opus`, or `-case<n>`, such as an evidence or closeout lane, gets no PR-SILENT. Otherwise the lane
status is `lanes.lane_status`'s line for each worktree the lane owns, found from the
runner's launch receipts, the lane's cci records of the last day, the briefs checkout's worktrees, and
its PRs' head branches: path, branch, dirty files, commits ahead of `origin/dev`, last commit, push
state, and PR, or `no worktree found for lane <lane>`. It is a blocker and never deferred.
`show` lists each lane whose mechanism waits as `pr-silent <lane>: …` with its `drive.py lane-status` command.
A class record, a lane's `incident` record or any record, such as a `state`, whose text starts
`CLASS` or `NEW CLASS`, that names an incident tag or topic and has no later root `go`, `decide`, or `decision` naming one of
them `deadlines.pr_silent_minutes` after it logs one `CLASS-UNOWNED <class>` blocker per class.
A launch, from the inbox or the `launch` verb, whose lane shares an incident key with a live lane,
or whose brief names the PR a live lane's branch carries, is refused with `LAUNCH-DUPLICATE` naming
that lane and its lane status when that lane has commits ahead or a pushed branch; `NOW` and
`--owner-directed` launch anyway.
Only an incident fix lane's partial-fix reports launch case lanes: when its `ready:` or
`fix-live:` Run status carries `UNFIXED <lane>: <case>` lines in its body, the runner launches
`<first>-case<n>` on the incident alias for each case in the same pass, where `<first>` is
the lane's name without its trailing `-opus` and `-case<n>` parts and `<n>` is the lowest
no lane container holds. A `worker_done` launches nothing. The brief is the
unfixed-case brief filled with the case verbatim, the reporting lane, the path of that
lane's brief, and the PRs its cci `opened` records name, attached to the briefs log as
`<first>-case<n>.full.md`. The runner logs
`INCIDENT <lane> <time>: its fix does not cover: <case> | case lane <first>-case<n> launching …`
for the root to ratify. Only a line that names the reporting lane counts, and
`UNFIXED <lane>: none` launches nothing. A case launches once across a lane and its `-opus`
fallback, however often and by whichever of them it is reported, and a case whose launch is
refused launches on the next report that names it. An incident launches at most
`incident.case_lanes` case lanes (default 2). Every other new case of a report goes to the
root in one `DECIDE` that carries, per case, the ready
`orca-desk: launch <first>-case<n> NOW incident xhigh brief=<path>` line from a brief
already attached: each case past that cap, each new case a case lane reports, since case
lanes are one generation, and each case of a report whose text, or whose lane's newest
`mechanism` record or status, says `UNKNOWN`. An incident ends at a `not-ours` or
`stand-down` record its fix lane or `-opus` fallback posts, at a root `stand-down` or `stopped` addressed
to either or to the case lane, and, for a report, at a `stopped` its lane posted before it.
A report whose subject or first body line opens with `NOT-OURS`, `stood down`, or `Stopped` launches no case
lane and files no `DECIDE`. A report after the end, or from a lane the root withdrew, logs
`UNFIXED <lane> <time>: <cases> | no case lane launched: …` and launches nothing, and a
case launch still pending at the end fails and logs `WITHDRAWN`. A case already known,
such as a case lane's own, launches nothing. A case
lane is an incident fix lane, so the clock covers it.
Each cci record addressed to `orca-desk` past the runner's saved sequence is read as one
desk inbox line, starting at the drive's newest record, except a record `cci import` took
from the desk inbox itself or one that carries `--re`; a record that is no directive logs
`DIRECTIVE-FAILED`, and a failed `cci tail` logs `CCI-TAIL-FAILED` and rereads from the same
sequence. An `orca-desk: withdraw <target>` line names a launch key, `R<n>` or a case
launch's `unfixed:<msg>:<n>`, or a lane. It fails each launch under the key, or each of the
lane's launches, that the runner accepted and has not started, and stops the dispatch the
key or lane launched with worker-stop, as `orca-gc --done` does. It logs `WITHDRAWN` only once
Orca shows the dispatch settled and its terminal gone or at an idle prompt within 30 seconds,
and `WITHDRAW-UNCONFIRMED` with the dispatch and terminal otherwise, as when worker-stop
answers `stop_unknown`. The
lane is marked withdrawn: its `UNFIXED` lines launch no case lane, and an Orca question it
leaves, such as the offer to dispatch it again, logs `WITHDRAWN` instead of `DECIDE`; a
case lane whose dispatch the root stopped by hand is marked the same way. A launch still
starting, a settled launch with no dispatch, a key or lane the runner holds nothing under,
or a worker-stop Orca refuses logs `WITHDRAW-FAILED`. Each root cci record carrying `--re`, read in sequence
order with the records addressed to `orca-desk`, is a reply: one to the runner's `DECIDE` on a worker question replies to that
question, one to a lane's own record replies to that lane's open Orca question, which the cci
reply superseded, and a `withdraw` of a launch record addressed to `orca-desk` withdraws that
launch.
Orca lets only the terminal bound to the Run call
worker-start. Every pass the runner reads the Run's coordinator from Orca and acts as that
terminal: each orca call and each launcher it starts carries ORCA_TERMINAL_HANDLE set to the
coordinator and an empty ORCA_PANE_KEY, so a root that rotates to a new terminal needs no
runner restart. It records the coordinator, the Run's generation, and how the binding was
obtained, at start and before each pass that launches. When the Run has no coordinator, or
Orca finds that terminal gone, a runner started inside an Orca terminal rebinds the Run to
that terminal, and one started outside any refuses to start and prints the rebind command; a
binding lost mid-run escalates UNBOUND once and fails every launch at once with
`LAUNCH-FAILED`, which no incident clock defers. A launch still accepted with no dispatch 60 seconds after its
`accepted_at` logs one `LAUNCH-STALLED` blocker naming the `orca-desk: withdraw` line and the `orca-launch.sh` command
that launches it by hand, whatever held it; no incident clock defers it. `rebind` runs
`orca orchestration run-use` from the current terminal and records it; `show` prints the
binding first. No Mac launch starts while the 1-minute load
is above the core count, because an Orca worktree checkout times out under that load, and
each ten minutes of it logs one `LOAD-HIGH` blocker telling the root to move the load. An
incident or `--owner-directed` launch fails at once with `LAUNCH-FAILED`; any other launch
waits for at most `deadlines.load_hold_minutes`, then reports the failure to root through
cci and the Run mailbox. With `orca.sprite` configured, a launch whose brief's `ccx:` line
says `place=remote`, or `cpu=high` with no `place=`, runs `orca.sprite.launcher`, the
repository's worker-launch.sh, on a Sprite instead, never waiting on Mac load. The
runner counts no Sprites and sets no limit: the provider admits or refuses each one.
`place=local` keeps a `cpu=high` launch on the Mac. Fable, incident, an incident clock
fallback, and a `role=desk` or `role=watch` brief stay local whatever the line says. A lane name a Sprite refuses,
or a Sprite launch whose receipts prove worker-start never ran, a provider refusal
among them, starts the same launch locally, and the fallback logs `SPRITE-FALLBACK`.
The proof is read from the brief's `.worker` directory: no `worker-start.json`,
and a nonzero exit in `prepare.status`,
or no `prepare.status` when the runner itself saw the launcher exit. A runner that
restarted mid-launch saw no exit. Any other Sprite launch that prints no ready line
may have left a remote worker: it becomes `unverifiable` and logs
`SPRITE-UNVERIFIABLE` with that directory and the Task and Dispatch its `worker-start.json`
names, and nothing is launched locally, retried, or cleaned up. A ready Sprite
launch's receipts are copied beside the local ones. `run --desk landing` is the merge queue: it gates and enqueues ready
prefixes under the accepted landing policy, verifies landings by squash, and routes
blockers and restacks. Every queue call goes through the repository adapter `landing.adapter` names, from `merge_queue.py`:
`graphite-stack-enqueue` runs the script `landing.adapter.script` names under the landing checkout, and `label` runs
`ledger.py label`; the landing desk refuses to start with `MQ-CONFIG-INVALID` on an adapter, a check, or a high-risk setting it cannot run.
Each prefix passes the check registry, `landing.checks` in order, every built-in when it names none: `hold`, `rules-review`,
`rulings`, `fidelity`, `finder`, and `merge-mode` are the desk's own, and `approvals` and `required-ci` are the adapter's verdict. Trees gate in
`(priority, root PR)` order, a tree's priority the lowest ledger `priority` among its PRs, and accepted enqueues run one parallel
wave per priority, lower first. A check that reads no verdict first settles its tips through `ledger.py landed`, which
reads each tip's squash on the freshly fetched trunk, and drops every tip whose row then reads landed; only the tips left
log `GATE-FAILED`. `mq.py` reads and steers the same queue. Each landing pass first fast-forwards the landing checkout to the remote trunk, so every
adapter call runs the trunk's script; a checkout that cannot fast-forward logs `CHECKOUT-STALE` to root and gates nothing that pass.
A prefix `ccx vcs pr status` reads out of the queue at the heads it admitted is classified from the adapter's verdict on each dropped
PR, never from comment text. A `conflict` stays out at that head and its ejection goes to the lane as the gate's blocker. An `own-red`
is gated again, and the adapter blocks it until a new head. A `batch-mate-red` the adapter retries is gated again; one blaming a PR outside the
PR's own stack is the batch's failure: it routes no blocker, logs one `AWAITS-GO` line to root, and the same head re-enqueues once a root
`go` record naming the PR is posted after the drop, with no new head. A `flaky-check`, a red check that read green on the PR's own head and
failed only in a queue draft holding nothing but its stack, goes back in at the same head through the adapter's `flake` once on the desk's
policy, which the adapter comments on the PR, and routes no blocker meanwhile; a second at the same head logs `AWAITS-GO`, and the root's next `go`
naming the PR puts it back the same way. A retry the adapter refuses logs `FLAKE-RETRY-FAILED`. The lane a PR's blocker or restack goes to is the poster of the newest cci `head`, `opened`, `ready`,
or `claim` record naming the PR, else the lane that registered its ledger row; a blocker posted on cci also goes to the root
when `cci who` lists no live session for that lane. A conflict blocker for a lane with no dispatch Orca shows active and no session
`cci who` lists routes nothing: the root gets one `PARKED` line per PR, and that head routes once the lane is back. A gate's `BLOCKED` verdict goes to the lane at most once per head, as the blocker its adapter names, and only for red CI,
a merge conflict, a changes-requested review or fidelity grade, or a queue ejection, never for a pending or running check or a
missing approval or an ejection blaming a PR outside its stack, and never for a PR that is held, carries GitHub's `hold` label, or is
stacked above such a PR until that hold lifts. A PR whose base is another open row's branch gets no failing-required-check blocker
unless a commit status other than `release-pr-check` or `emergency-head` is red on its own head. It gates only tips the ledger lists as `ours`, every tip of one stack in one call, and never enqueues a prefix
holding a PR the drive did not admit. A PR is the drive's by what GitHub shows of the PR itself, never by record text: the
branch prefix or stack a lane registered, which `ledger.py refresh` admits, or the drive's `drive:<drive>` label, which the
desk reads once per PR and head with `gh pr view --json labels,headRefName` for an unadmitted row or a rider and registers
under its head branch, so the next pass gates it as ours. A row `ledger.py unadmit` withdrew is never admitted again
except by an explicit `ledger.py register`. It logs one `NOT-OURS` escalation per head of an open stack-top PR it skips
for that reason. Every PR an open cci `hold` record
names is held until a `lift --resolves <seq>` closes that record. A `hold:all <reason>` line in
the holds file holds every open row except those a `release #<n>` or `release lane:<name>` line names. A
`<lane> HOLD <reason>` or `HOLD <lane> <reason>` line holds a lane's open rows the way `lane:<name>` does. `holds.py`
parses the file. A line that matches none of its forms, or a lane line naming a lane no ledger row carries in any state,
may hold something the runner cannot name. The runner checks the file first thing on each pass, an empty ledger included: on such a pass it
gates and enqueues nothing, and logs `HOLDS-UNREAD` once per distinct line, quoting it. Each enqueue re-reads the file under the same
rule just before its call and completes as `blocked` on such a line. The same moment it reads every hold fresh, whatever the
pass read earlier: the holds file, the `hold` label GitHub serves for each PR, and the PR's cci `hold` records. An enqueue completes as
`refused`, with one `REFUSED` line naming each hold, when a line, label, or open record holds a PR in its prefix, when a `hold` record of any status was
posted after the enqueue was accepted, or when the PR's newest `hold` record is newer than its newest `ready` record, so a stale
`ready` never enqueues a PR after a hold. The same read repeats once the adapter's enqueue returns success: a hold that appeared during the
call completes the enqueue as `dequeued`, after the adapter's dequeue takes each held PR and each PR above one back out of the queue
top first, with a `DEQUEUED` line and a cci `stopped` record to every drive naming each; a dequeue that fails logs `DEQUEUE-FAILED`. Both outcomes enqueue again once nothing holds the prefix. The holds file stays the root's interface; a `hold` label
on a PR holds it too, and the runner never adds or removes that label. A prefix
enqueues only when every PR in it has a fidelity ok at its current head. A grade is a cci `review` record from
a fidelity lane (`landing.fidelity_lane`, one lane name or a list of them, which the landing desk refuses to start without), read by the PR's
`owner/repo#N` reference, whose text starts `fidelity: ok` or `fidelity: missing` and whose first short sha is the head it grades. Each
lane's newest grade of the head stands for that lane: a `missing` blocks until the same lane grades the head ok, whatever another lane
posts, and an ok counts as posted, whatever rows the register gained since (4236611). A head no
grade names carries the ok of an older head whose lanes all stand at ok, when `git patch-id --stable` of both heads'
diffs against the PR's base, read from GitHub's compare, is equal, cited as `fidelity: ok at <head>, patch-identical to <graded head>`;
a compare GitHub refuses for the PR's own head logs `PATCH-READ-FAILED` once and carries nothing.
Any other head gets one cci `blocker` per head and grade, to the PR's lane and the fidelity lane, and nothing
in its prefix is enqueued. The `finder` check needs the newest `finder:` review record naming the head, from any lane, to read
`finder: ok`; a head with none, or whose newest is `finder: major`, gets one `FINDER-PENDING` blocker per head to its lane (E4.1). A finder ok of an older head
carries to a head with no finder record when both patches against the PR's base match, as a fidelity ok does. A PR whose body opens with "Red by design" draws no
blocker for its failing required checks.
The `rulings` check runs `rulings.py check-pr` once per head a pass and holds no head: each cause it prints but `RESERVED-NO-ANSWER`,
which merge mode settles, is a warning, one `note` record per head and set of causes to the PR's lane (4236611). A run that exits with a code other than 0 or 1 or
prints a line naming no cause passes the head and logs `RULINGS-UNREAD` once per head and error text, the process id in a `refs/rulings/` ref aside, and so does each `BLOCKER` line it prints.
Each pass reads the commit statuses of every open row's head in one GraphQL query, and reuses a
head's statuses for 60 s; a head read that fails counts as unread for that pass, and the pass ends with one
`STATUS-READ-FAILED` per cause naming the count and each PR and head, once per cause and set of heads. Each pass starts with one free `rate_limit` read of the GitHub App
installation's quotas through `ccx vcs gh`. With the REST or GraphQL quota under 5% of its limit, the pass reads nothing from GitHub and
logs one `GITHUB-BACKOFF` naming the calls left and the reset per quota window; accepted enqueues stay accepted and root `go` records
stay on cci, so both run on the first pass after the reset. A quota read that fails logs `QUOTA-READ-FAILED` once an hour, and the pass runs. A `cci tail` that fails in a landing pass
ends that pass before it gates or enqueues further; it logs `CCI-TAIL-FAILED` and the runner keeps running.
An incident fix PR on the trunk, one whose ledger lane is an `incident-` or `outage-` lane or a fix lane the runner clocks, or whose
number an open executor incident names as its `repair_pr`, is never enqueued: in the pass its head reads `APPROVED` and every
required check passes and the holds file, read again just before, holds no line over it,
the runner runs `gh pr merge <n> --squash --match-head-commit <head>` once per head, recorded as
`land:<pr>@<head>`, and posts `landed` to the PR's lane and the root; a merge GitHub refuses logs `SQUASH-FAILED`. What
`rulings.py check-pr --incident` names of `RULINGS-UNCITED`, `RESERVED-NO-ANSWER`, or `TEST-REMOVED` is a warning, one `note` to the PR's
lane per head and set of causes, and the fix merges anyway (4236611). Neither grade
gates the merge: a landed fix whose head has no counted fidelity ok or no finder ok 30 minutes on logs `UNGRADED-30` once (d968e1d). A PR whose ledger row lands carrying the `merge-fast`
label, and whose body has a `Release-With:` line, or an incident fix's PR whatever its label and body, a PR this runner
merged by plain squash among them however its incident reads by then, sends one cci `release` record to `landing.release_owner` and the root in the pass
that reads the landing, with `--topic <pr> --pr <pr>`, naming the squash, the components, and the release to start. Until a cci
`release` record on the drive from another lane, stamped after the landing, names one of those components with `--target` or the
PR with `--pr`, the runner posts one `blocker` to both each `deadlines.release_start_minutes` (default 10) after the landing, with
the minutes elapsed, or each `deadlines.incident_release_minutes` (default 3) while an incident clock runs. A `fix-live` record
from another lane that names the PR or a component settles a landing too. An incident fix's landing settles only on such a
`fix-live` record, a `release` record that carries `--build` and a `--target`, one of its components when it names any, or a
root `--resolves` on one of the runner's records about it, its RELEASE-HELD-AFTER-LANDING lines included, and
each open root `hold` record after its landing that names the PR or is addressed to the release owner logs one
`RELEASE-HELD-AFTER-LANDING`; no other kind is a hold, and a root `--resolves` on the hold closes it. Once the record that settles a landing carries a build, each pass reads
`<landing.release_cli> live <id> --json` for the release id its text names, or its build, and posts one `fix-live` record
`RELEASED <component> <version> (<build>)` to the root alone once every component reads live, keyed
`release:<pr>:live`. A `release` record on another drive, which `cci tail` returns as peer mail, settles nothing. A record the
drive's root posts with `--re` or `--resolves` naming the release record or one of its blockers ends a merge-fast landing's too. Each landing is
judged once, by the first pass that reads it, and the set read persists in the landing desk's store: the desk's first pass
records every landing it reads as history and sends nothing, so a restart or an upgrade never judges an old landing again,
and a landed row with no `landed_at` is not read until it has one. A landing from before the
runner process started, first read after it, closes with no record when `<landing.release_cli> live --json` shows its squash or a
later commit running, for one of its components when it names any. Each record carries
`--id release:<pr>:<step>`, so a repost after a crash returns the stored record and no step is posted twice. A record cci refuses,
or a post that prints no `#<seq>`, is posted again on the next pass and escalated to root as `RELEASE-FAILED`, once per
`deadlines.release_start_minutes` step. A worker's question escalates to root as `DECIDE`, and only a reply this
drive's root posts with `--re` on that `DECIDE` or on the lane's own record answers it.
Between orca passes the runner waits ten seconds, cut short within a quarter second by a desk
inbox append or an exiting launch, and its sweep shows only dispatches it has not
named for reclaim, once each in a `RECLAIM` line that names `gc` and then `orca.gc` for the worktrees.
`gc`, which the root runs and no pass ever does, reads each `<lane>.terminal.json`, the reply orca-launch.sh saved
from its last `orca terminal create`, and closes that terminal through Orca only when its launch settled: the runner
holds no open launch of the lane, and either the receipt's dispatch reads completed or failed in the Run's worker list,
or no dispatch of the Run references the terminal and the runner's latest started launch of the lane, now settled, wrote
the receipt. A handle Orca calls stale stands for the connected terminal in the lane's worktree whose `tabId:leafId` is
the receipt's pane key, unless an unsettled dispatch works in that worktree.
It closes only a terminal no unsettled dispatch holds, read idle at an empty prompt that asks nothing with no output
for ten minutes by a known output time, closing the tab too when no other connected terminal shares it, and then deletes the receipt unless a launch rewrote it, as it
does at once for a terminal Orca shows closed. Everything else is kept and named; `--dry-run` closes and deletes nothing.
Each escalation runs
`cci post --drive <drive> --lane desk-runner --to root --kind <k> --topic <key>`,
once per cause. The key is the line's second token. DECIDE maps to decide, INCIDENT to incident,
INCIDENT-OWNED to state, UNBOUND, UNOWNED, CLASS-UNOWNED, UNACKED, INCIDENT-CLOCK, PR-SILENT, LAUNCH-STALLED, LAUNCH-DUPLICATE, HOLDS-UNREAD,
RULINGS-UNREAD, UNGRADED-30, BATCH-POST-OWED, and SLICE-UNREAD to blocker, any *-FAILED to defect, and all other labels to report.
Text over cci's 400-character limit is clipped; the full line is written to
`<receipts>/cci/<hash>.txt` and attached with `--path`. A line relaying an Orca question,
escalation, worker_done, status, decision gate or handoff is a summary: its receipt carries
the full line and then the whole message, subject and body, kept under
`<receipts>/cci/messages/<id>.md` when the message arrives. Landing restack routes
sent through cci use `--kind blocker --to <lane> --topic <pr>` and the same text limit.
Each orca pass also couples the drive to its releases. A lane's `release` record that names a build has the build's owner,
`<lane>@<drive>`, recorded with `drive.py release` and the build claimed for the lane with
`cci claim --for <lane>@<drive> --build <build> --rel started`, logging `RELEASE-CLAIM-FAILED` once when either fails.
With `landing.applying` set, every 15 seconds the runner reads each tracked build until it finishes and posts one
`applying <component>/<env> <commit> (<release>)` release record, keyed `applying:<job>`, to the root of every live
drive `cci who --role root` lists, the first time a job of a watched component reads running. A lane's `hold`
record of the form `froze <scope> until <RFC 3339> as freeze <id>` is tracked until it closes: past its until the
lane gets one lift `ask`, and once the lane has no live session the root gets one. The runner's first read
on a drive starts at the newest record.
`landing.release_cli` is the repo's release command line, such as `tools/ci release`, its first word relative to the landing
checkout, which `run --desk landing` requires: without it the desk refuses to start with `MQ-CONFIG-INVALID`, whose line names it
among every missing member.
The config requires `drive` and `orca.desk_inbox`, and `run --desk landing` requires `landing.release_owner`,
a cci `--to` address such as `peer:<session>`, and `landing.merge_mode`, the owner's pick of `every`, `high-risk`, or `auto`,
re-read on every use. A repository the GitHub account the runner acts as owns, which `gh api user` names, lands once green under every mode (de6c88c). Otherwise,
under `every`, under `high-risk` for a PR whose changed files a `landing.high_risk_paths` glob names, and in
every mode for a head `check-pr` reads as touching a reserved row no answer authorizes, a prefix
that would enqueue posts one PR card per waiting PR and head instead, logs one `MERGE-ASK` naming it, and enqueues once a root `go` record names each
waiting PR and its head by a short sha. The card goes through `cc-present ask` with `--session` set to the drive root's newest session in the drive registry,
so it lands on the root's board and in the root's Slack status thread like every other card: the question, the PR's title, author, size, and review state, the
PR linked with its live checks, waiting on `merge:<pr>:<short sha>` with `Merge (Recommended)`, `Hold`, `Other`, and `Explain more` (93037c3); a card already
open for the head is edited in place, never re-posted, and a post that fails
logs `MERGE-CARD-FAILED` once per head and cause and tries again next pass. A reserved touch puts the first sentence of the row's words in the card's question, whose
tap is the authorization: the root `go` for the head, posted after the runner logged a card naming the row or naming the row's key itself, covers it (4236611), and so
does an answer `rulings.py authorize` labels `authorizes:<key>`, which `check-pr` reads. A PR that no ledger `progress` entry with
an artifact lists by `#<n>` in its note gets no `MERGE-ASK`; its batch logs one `BATCH-POST-OWED` instead (8908f83). A quiet pass writes nothing.
The orca runner rotates every *.md inbox beside `orca.desk_inbox` once an hour,
using the default six-hour window.
Each pass of either desk writes its heartbeat beside `orca.desk_inbox`, naming its pid, config, restart command, and the
plugin version the runner runs. A runner behind the version `installed_plugins.json` lists logs one
`RUNNER-STALE <running> < <installed>` report per installed version, naming the hand restart command, and re-executes into the installed version after
that pass's wait, keeping its pid, config, and desk; `--once` never re-executes. An `env` object under a desk's config
object, such as `"landing": {"env": {"AWS_PROFILE": "release-operator"}}`, is merged into that desk's process environment
each time `run` starts, a re-execution included, and is carried by its restart command and its `handoff` successor. `handoff` stops the live runner at the end
of its pass and starts the next one with the same config and desk, appending to `<desk>-runner.log`. `drive.py digest`
prints each desk's running and installed versions.
While ruling cards `rulings.py classify` wrote queue under the drive's `cards/`, each landing pass logs one `RULING-ASK`
per board round; the root runs `rulings.py round`, which puts at most ten of them on the live cc-present board as one
round, foundational rows first.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from zoneinfo import ZoneInfo

import actions
import cci
import drive
import holds
import inboxes
import lanes
import merge_queue
import prompt_screen
from incident import CLAUDE_FAST_MODE, CLOCK_FALLBACK, latest

SCRIPTS = Path(__file__).resolve().parent
RUNNER_BIN = SCRIPTS.parents[2] / "bin" / "desk-runner.py"
PACIFIC = ZoneInfo("America/Los_Angeles")
LANE_PREFIX = "desk-lane-"
LANDING = "desk-landing"
RULING_CARDS = "cards"
MERGE_CARDS = "merge_asks"
MERGE_CARD_FIELDS = "title,author,additions,deletions,reviewDecision"
SLACK_MESSAGE = re.compile(r"^slack: \S*/p(\d{10})(\d{6})\b", re.MULTILINE)
RULING_ROUNDS = "cards/rounds"
RUNNER = "desk-runner"
ORCA_DESK = "orca-desk"
LAUNCH_STATE = Path(".claude/scratch/orca-launch")
CCI_TEXT = 400
ESCALATION_KINDS = {"DECIDE": "decide", "INCIDENT": "incident", "INCIDENT-OWNED": "state", "UNBOUND": "blocker", "UNOWNED": "blocker", "CLASS-UNOWNED": "blocker", "UNACKED": "blocker", "INCIDENT-CLOCK": "blocker", "HOLDS-UNREAD": "blocker", "LOAD-HIGH": "blocker", "CUT-SILENT": "blocker", "PR-SILENT": "blocker", "RELEASE-HELD-AFTER-LANDING": "blocker", "LAUNCH-STALLED": "blocker", "LAUNCH-DUPLICATE": "blocker", "RULINGS-UNREAD": "blocker", "UNGRADED-30": "blocker", "BATCH-POST-OWED": "blocker", "SLICE-UNREAD": "blocker"}
UNLAUNCHED = "unlaunched"
BINDING = "binding"
TRUNK = "dev"
INACTIVE = frozenset({"completed", "failed"})
RECLAIM_NAMED = 10
CREATED = ".terminal.json"
STALE_HANDLE = "terminal_handle_stale"
IDLE_PROBE_MS = 1500
QUIET = timedelta(minutes=10)
DONE_MARKER = re.compile(r"^✻ .* · done ")
NOT_IDLE = "not idle: busy, prompt typed or asking, or output in the last 10 minutes"
MODELS = re.compile(r"opus|sonnet|fable|astra|codex|sol|incident|claude-[\w.-]+|gpt-[\w.-]+")
INCIDENT_MODEL = "incident"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
RETRYABLE = frozenset({"blocked", "superseded", "foreign", "dropped", "refused", "dequeued"})
BELIEVED_QUEUED = frozenset({"enqueued", "noop"})
ADMITTED = BELIEVED_QUEUED | {"dropped", "ejected"}
OUT_OF_QUEUE = frozenset({"not queued", "evicted"})
OWNER_KINDS = ("head", "opened", "ready", "claim", "report")
FIDELITY_OK = "fidelity: ok"
FIDELITY_MISSING = "fidelity: missing"
FIDELITY_GRADES = (FIDELITY_OK, FIDELITY_MISSING)
FINDER_OK = "finder: ok"
FINDER_GRADES = (FINDER_OK, "finder: major")
CHECK_PR_CAUSES = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED", "FINDER-MAJOR", "NAME-HIT", "RULINGS-PLAIN", "LANE-DECIDED", "CONTRACT-MISSING", "TESTS-CORPUS"})
INCIDENT_CHECKS = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED"})
RESERVED_TOUCH = "RESERVED-NO-ANSWER"
CHECK_PR_BLOCKER = "BLOCKER"
UNGRADED_AFTER = timedelta(minutes=30)
UNGRADED_READ = "ungraded_read"
DIFF_MEDIA = "Accept: application/vnd.github.diff"
HOLD_LABEL = "hold"
MERGE_MODES = ("every", "high-risk", "auto")
ACCEPT_CHECKS = ("rules-review", "rulings", "fidelity", "finder", "merge-mode")
HOLD_CHECKS = ("hold",)
ENQUEUE_CHECKS = ("rules-review", "rulings", "fidelity", "finder", "merge-mode")
CHECK_STAGES = {"hold": "enqueue", "rules-review": "accept, enqueue", "rulings": "accept, enqueue", "fidelity": "accept, enqueue", "finder": "accept, enqueue", "merge-mode": "accept, enqueue", "approvals": "adapter check", "required-ci": "adapter check"}
DEFAULT_PRIORITY = 100
HIGH_RISK_STARTER = ("*migrations/*", "*.sql", "*backfill*", "*secret*", "*credential*", "*creds*")
PULLED = "pulled"
FLAKE_RETRY = "flake"
SHORT_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
VOLATILE = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\d+")
RELAYED_PR = re.compile(r"^(?:blocker|restack):(\d+):")
RED_BY_DESIGN = re.compile(r"^\W*red by design\b", re.IGNORECASE)
MERGE_FAST = "merge-fast"
RELEASE_WITH = re.compile(r"^Release-With:[ \t]*(\S.*)$", re.MULTILINE)
RELEASE_READ = "release-read"
INCIDENT_NAME = re.compile(r"^(?:incident|outage)-")
EXECUTOR_SETTLED = frozenset({"closed", "recovered"})
SQUASH_PASS = "pass"
LANDED_BY_SQUASH = "landed by plain squash"
RELEASE_ID = re.compile(r"\breleases/(\d{4}-\d{2}-\d{2}/\d+)\b")
BUILD_NUMBER = re.compile(r"(\d+)/?$")
NOT_LIVE = "not live"
RELEASE_LANE = re.compile(r"(?:^|-)release(?:-|$)")
CUT_ORDER = re.compile(r"\b(?:cut|release start)\b", re.IGNORECASE)
CUT_KINDS = ("go", "decision")
SETTLING_KINDS = ("release", "fix-live")
PARKED = "parked"
ALREADY_SHIPS = re.compile(r"already ships .* or later")
CUT_CLOCKS = "cut-clocks"
CUT_CURSOR = "cut-cursor"
CUT_LAUNCH = "cut:"
CUT_GRACE = timedelta(minutes=1)
CUT_CLAIM = timedelta(hours=2)
RELEASE_CURSOR = "release-cursor"
RELEASE_BUILDS = "release-builds"
FREEZES = "freezes"
FROZE = re.compile(r"\bfroze (?P<scope>\S+) until (?P<until>\S+) as freeze (?P<id>[\w.-]*\w)")
BUILD_URL = re.compile(r"/(?P<pipeline>[\w.-]+)/builds/(?P<number>\d+)/?$")
RELEASE_POLL = timedelta(seconds=15)
APPLYING_KEYS = ("build", "step", "components")
RELEASE_CLI = "tools/ci release"
MANUAL_RELEASE = "manual-release-"
MANUAL_TEMPLATE = SCRIPTS.parent / "reference" / "manual-release-brief.md"
DEFERRABLE = frozenset({"DEADLINE", "UNACKED", "HOLDS-UNREAD", "LIVENESS", "STALE-MAIL", "RECLAIM"})
DEFERRED = "deferred-escalations"
POSTED = re.compile(r"#(\d+)")
SWEEP_EVERY = timedelta(minutes=5)
PASS_SECONDS = 10
CARD_ROLLUP = 50
CARD_REFS = 10
CARD_DETAIL = 200
CARD_STEPS = 1000
CARD_NAME = 80
CARD_LABEL = 75
COMMAND_TIMEOUT_SECONDS = 1800
TIMED_OUT = 124
WAKE_SECONDS = 0.25
STOP_CONFIRM = timedelta(seconds=30)
STOP_POLL_SECONDS = 2
SHOW_WORKERS = 8
GATE_WORKERS = 4
GATE_STATUSES = frozenset({"release-pr-check", "emergency-head"})
RED_STATES = frozenset({"failure", "error"})
RUN_NUMBERS = re.compile(r"\d+")
POSTED_SEQ = re.compile(r"#(\d+)")
HEAD_FLOOR = timedelta(seconds=60)
HEADS_PER_QUERY = 100
QUOTA_FLOOR = 0.05
QUOTA_RESOURCES = ("core", "graphql")
ROTATE_EVERY = timedelta(hours=1)
HEARTBEATS = drive.HEARTBEATS
DESKS = tuple(HEARTBEATS)
PINNED_REF_PID = re.compile(r"(refs/rulings/[0-9a-f]+)-[0-9]+")
RUNNING_VERSION = drive.plugin_manifest()["version"]
LAUNCH_STALL = timedelta(seconds=60)
ORPHANED_SEND = timedelta(minutes=2)
RESEND_AFTER = timedelta(seconds=30)
RESEND_LIMIT = 2
RESEND = ":resend"
RETRY = ":retry"
SEND_ATTEMPTS = 3
INBOX_PAGE_SIZE = 100
UNPARSEABLE = "unparseable"
UNREAD = "unread"
UNREACHABLE = frozenset({"runtime_unavailable", "remote_runtime_unavailable", "runtime_timeout", "invalid_runtime_response", UNPARSEABLE})
ANSWER_CONFLICT = "answer_conflict"
UNREACHABLE_FACT = "orca-unreachable"
UNREACHABLE_WAIT = timedelta(minutes=2)
HANDOFF_WAIT = timedelta(minutes=5)
RELAY_REF = re.compile(r"(?:^|cci#)(R?\d+)$")
RUN_REF = re.compile(r"R\d+|\d+")
ACK = re.compile(r"^(?P<verb>started|done)\b[: ]*(?P<rest>.*)$", re.DOTALL)
LAUNCHED = re.compile(r"^(?P<lane>\S+) (?P<how>ready|unsupervised) task=\S+ dispatch=(?P<dispatch>\S+) terminal=(?P<terminal>\S+) worktree=\S+$", re.MULTILINE)
LANDING_POLICIES = ("prefix", "whole")
DRIVE_LABEL = "drive:"
NOT_ADMITTED = "the drive did not admit it: no lane registered it, by name or by a branch prefix or stack its branch is on, and it carries no drive label"
ADMIT_HOW = "the lane that opened it runs ledger.py register --lane <lane> --branch-prefix <its branch> or --pr {pr}, or labels it drive:<drive>"
RELAY_GRAMMAR = "orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>"
LAUNCH_GRAMMAR = "orca-desk: launch <lane> [NOW] <model> <effort> [role=<role>] brief=<absolute path>"
ALERT_GRAMMAR = "orca-desk: alert <slug> <link> :: <what fired>"
INCIDENT_GRAMMAR = "orca-desk: incident <slug> <link> reported=<HH:MM | H:MM AM/PM, Pacific> :: <what fired>"
HOLD_GRAMMAR = "orca-desk: hold <slug> owner=<lane> :: <what is held, and on what>"
UNHOLD_GRAMMAR = "orca-desk: unhold <slug>"
WITHDRAW_GRAMMAR = "orca-desk: withdraw <R<n> | unfixed:<msg>:<n> | lane>"
INBOX_DIRECTIVE = re.compile(r"^(?:-\s+)?(?:(?P<key>R\d+)\s+(?:\([^)]*\)\s+)?)?orca-desk: (?P<verb>relay|launch|alert|incident|hold|unhold|withdraw)\b(?P<rest>.*)$", re.DOTALL)
RELAY_TO = re.compile(r"^ to (?P<lanes>[\w.-]+(?:(?:, (?:and )?| and )[\w.-]+)*):\s+(?P<text>\S.*)$", re.DOTALL)
LANE_LIST = re.compile(r", (?:and )?| and ")
LAUNCH_SPEC = re.compile(r"^ (?P<lane>[\w.-]+)(?P<now> NOW)? (?P<model>\S+) (?P<effort>\S+)(?: role=(?P<role>[\w-]+))? brief=(?P<brief>/\S+)$")
ALERT_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9.-]*) (?P<link>\S+) :: (?P<what>\S.*)$")
INCIDENT_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9_.-]*) (?P<link>\S+) reported=(?P<reported>(?:[01]?\d|2[0-3]):[0-5]\d|(?:1[0-2]|0?[1-9]):[0-5]\d [AP]M) :: (?P<what>\S.*)$")
INCIDENT_BAR = timedelta(minutes=20)
ALERT_TEMPLATE = SCRIPTS.parent / "reference" / "alert-fix-brief.md"
UNFIXED_TEMPLATE = SCRIPTS.parent / "reference" / "unfixed-case-brief.md"
SLICE_LINE = "Register slice:"
SLICE_HEADER = re.compile(rf"^{SLICE_LINE}", re.MULTILINE)
INCIDENT_STEP = "incident"
UNFIXED = re.compile(r"^[ \t>*-]*(?P<tick>`?)UNFIXED (?P<lane>[\w.-]+):[ \t]*(?P<case>\S.*?)(?P=tick)[ \t]*$", re.MULTILINE)
UNFIXED_CASES = "unfixed-cases"
UNFIXED_KEY = "unfixed:"
UNKNOWN_CAUSE = re.compile(r"\bUNKNOWN\b")
HANDED_BACK = re.compile(r"^(?:[\w-]+:\s*)?(?:NOT-OURS|stood down|stopped)\b", re.IGNORECASE)
MONITOR_ID = re.compile(r"(?:monitors/|\bmonitor |\bDatadog )(\d{4,})")
INCIDENT_ANNOTATION = re.compile(r"^ccx:.*\bincident=([\w.-]+)", re.MULTILINE)
NAMES_NOTHING = "none"
ROLE = re.compile(r"^ccx:(?:.*[ \t])?role=([\w-]+)", re.MULTILINE)
FIX_ROLES = frozenset({"fix", INCIDENT_MODEL})
CLOCK = "incident-clock"
MILESTONE_STATUSES = "milestone-statuses"
MILESTONE_SUBJECTS = ("fix-live:", "mechanism:", "ready:")
CLOCK_MILESTONES = {"mechanism": "mechanism", "opened": "PR", "ready": "ready"}
CLOCK_ENDS = ("fix-live", "not-ours", "stopped", "stand-down")
STAND_DOWN = "stand-down"
INCIDENT_ENDS = ("not-ours", STAND_DOWN)
STOPPED = "stopped"
TEXT_MARK = re.compile(r"^(?P<mark>MECHANISM|READY)\b")
TEXT_MARK_KINDS = ("report", "incident")
FIX_ALIAS = "-fix"
CASE = "-case"
LINEAGE = re.compile(rf"(?:{re.escape(CLOCK_FALLBACK)}|{re.escape(CASE)}\d+)+$")
INCIDENT_PART = re.compile(r"-[^-]+(?:-\d+)?$")
CLAUDE_ARGS = "ORCA_LAUNCH_CLAUDE_ARGS"
PR_SILENT = "pr-silent"
CLASS_UNOWNED = "class-unowned"
CLASS_TEXT = re.compile(r"^(?:NEW )?CLASS\b")
FIX_GO_KINDS = frozenset({"go", "decide", "decision"})
FIX_GO = re.compile(r"\bfix\b", re.IGNORECASE)
UNPUSHED = frozenset({"not pushed", "no origin branch"})
PR_NAMED = re.compile(r"(?<![\w/])#(\d{3,})\b|/pull/(\d+)\b")
PR_SILENT_HORIZON = timedelta(hours=1)
ANSWERS_KEPT = 2000
LANE_RECORDS = timedelta(days=1)
FALLBACK_BRIEF = """ccx: lane={backup} role=incident effort=xhigh

# {backup}: Opus fallback for {lane}

You are the Opus fallback for `{lane}`. The orca desk's incident clock started you
{minutes} minutes after `{lane}` launched, because `{lane}` had posted no {missing}.
Work the incident in the brief below in parallel with `{lane}`; the first PR wins.
Before you start, read what `{lane}` has posted with
`cci tail --drive {drive} --lane {lane} --since 2h`, so you repeat none of it.
Post on cci as `{backup}`, and address `{lane}` and `root` on every record.
Wherever the brief below tells its lane to post or report as `{lane}`, post as
`{backup}`.

The brief `{lane}` was launched on follows, unchanged.

{brief}"""
CCX_KEY = r"^ccx:(?:.*[ \t])?{}(?:\s|$)"
CPU_HIGH = re.compile(CCX_KEY.format("cpu=high"), re.MULTILINE)
PLACE = re.compile(CCX_KEY.format("place=(remote|local)"), re.MULTILINE)
STANDING_DESK = re.compile(CCX_KEY.format("role=(?:desk|watch)"), re.MULTILINE)
SPRITE_LANE = re.compile(r"[a-z0-9][a-z0-9-]{0,54}")
SPRITE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
SPRITE_IDS = {"task": "taskId", "dispatch": "dispatchId"}
SPRITE_PREPARED = "prepare.status"
SPRITE_RECEIPT = "worker-start.json"
SPRITE_MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "sol": "gpt-6.1-sol", "codex": "gpt-6.1-sol", "astra": "gpt-6-astra"}
SPRITE_LOCAL_MODELS = frozenset({"fable", INCIDENT_MODEL})
LAUNCHER = SCRIPTS.parents[2] / "bin" / "orca-launch.sh"
HOLD_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9.-]*) owner=(?P<owner>[\w.-]+) :: (?P<what>\S.*)$")
UNHOLD_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9.-]*)$")
WITHDRAW_SPEC = re.compile(r"^ (?P<target>[\w.:-]+)$")
LAUNCH_KEY = re.compile(r"R\d+|unfixed:[\w-]+:\d+|inbox@\S+")
WITHDRAWN = "withdrawn"
FENCED = "dispatch_inactive"
DECIDE_LINE = re.compile(r"^DECIDE (?P<msg>msg_\S+) (?P<lane>[\w.-]+): ")
ROOT = "root"


@dataclass
class Done:
    code: int
    out: str
    err: str


class CciTailFailed(RuntimeError):
    pass


class OrcaUnreachable(RuntimeError):
    pass


class Shell:
    """The single side-effect boundary: every subprocess, the clock, and the load average pass through here.

    Input reaches a child from a file, never a pipe the runner writes while the child's output
    fills, and a child still running after COMMAND_TIMEOUT_SECONDS is killed and fails its pass.
    """

    def run(self, argv: list[str], stdin: str | None = None, env: dict[str, str] | None = None) -> Done:
        with tempfile.TemporaryFile("w+") as feed:
            feed.write(stdin or "")
            feed.seek(0)
            try:
                proc = subprocess.run(argv, stdin=feed, capture_output=True, text=True, env={**os.environ, **env} if env else None, timeout=COMMAND_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                return Done(TIMED_OUT, "", f"{' '.join(argv)} timed out after {COMMAND_TIMEOUT_SECONDS}s and was killed")
        return Done(proc.returncode, proc.stdout, proc.stderr)

    def adopt(self, values: dict[str, str]) -> None:
        os.environ.update(values)

    def reexec(self, argv: list[str]) -> None:
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(argv[0], argv)

    def spawn(self, argv: list[str], out: Path, env: dict[str, str], append: bool = False) -> subprocess.Popen:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a" if append else "w") as sink:
            return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT, env={**os.environ, **env}, start_new_session=True)

    def stat(self, path: Path) -> tuple[int, int] | None:
        try:
            info = path.stat()
        except FileNotFoundError:
            return None
        return info.st_size, info.st_mtime_ns

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def load(self) -> float:
        return os.getloadavg()[0]

    def cores(self) -> int:
        return os.cpu_count() or 1

    def env(self, name: str) -> str:
        return os.environ.get(name, "")


def brief_incidents(brief: Path) -> set[str]:
    return set(INCIDENT_ANNOTATION.findall(brief.read_text())) if brief.is_file() else set()


def incident_fix(lane: str, model: str, brief: Path, role: str) -> bool:
    """Whether a launch is an incident fix lane's: the incident alias, or a brief whose `ccx:` line says `role=incident` or names an `incident=`, and in either case a launch and brief naming no role but fix or incident, for a lane whose name names no evidence, proof, closeout, desk, or audit role."""
    roles = (set(ROLE.findall(brief.read_text())) if brief.is_file() else set()) | ({role} if role else set())
    return roles <= FIX_ROLES and not lanes.NOT_FIX_ROLES & set(lane.split("-")) and (model == INCIDENT_MODEL or INCIDENT_MODEL in roles or bool(brief_incidents(brief) - {NAMES_NOTHING}))


def fix_launch(incident: actions.Incident) -> actions.Action | None:
    """A lane container's newest verified launch, when it is an incident fix lane's."""
    launch = next((action for action in reversed(incident.actions.values()) if action.kind == "launch" and action.status == "verified"), None)
    if not launch:
        return None
    spec = json.loads(launch.target)
    return launch if incident_fix(incident.incident_id.removeprefix(LANE_PREFIX), spec["model"], Path(spec["brief"]), spec.get("role", "")) else None


def first_lane(lane: str) -> str:
    """The lane an incident's fallback and case lanes descend from: the name without its trailing `-opus` and `-case<n>` parts."""
    return LINEAGE.sub("", lane)


def duplicate_line(key: str, lane: str, holder: str) -> str:
    return f"LAUNCH-DUPLICATE {key} {lane}: {holder} already holds this work; relay to it, or launch with NOW or --owner-directed when the owner directs a second lane; nothing was launched"


def incident_name(lane: str) -> str:
    """The incident a lane named `<incident>-<part>` works, so `inc-laptop-fix`, `inc-laptop-cred`, and `inc-laptop-fix-2-opus` share `inc-laptop`; a two-part name such as `api-fix` names its own."""
    first = first_lane(lane)
    head = INCIDENT_PART.sub("", first)
    return head if "-" in head else first


def is_case_lane(lane: str) -> bool:
    return CASE in lane.removeprefix(first_lane(lane))


def unfixed_cases(text: str, lane: str) -> dict[str, str]:
    """The cases this lane's own `UNFIXED <lane>: <case>` lines name, in order and as first written, keyed by the text with its whitespace runs collapsed; `UNFIXED <lane>: none` names no case."""
    cases: dict[str, str] = {}
    for match in UNFIXED.finditer(text):
        if match["lane"] == lane and match["case"].rstrip(".").lower() != NAMES_NOTHING:
            cases.setdefault(" ".join(match["case"].split()), match["case"])
    return cases


def handed_back(message: dict) -> bool:
    """Whether a lane's report opens, in its subject or the first line of its body, with NOT-OURS, `stood down`, or `Stopped`: the lane gave its incident back, so the cases it lists are no case lane's to launch."""
    return any(HANDED_BACK.match(line.strip()) for line in (message.get("subject") or "", (message.get("body") or "").strip().partition("\n")[0]))


def clock_mark(record: dict) -> str:
    """The incident clock milestone a lane's cci record marks, or empty: its kind, or `mechanism` or `ready` for a `report` or `incident` whose text starts MECHANISM or READY."""
    if record["kind"] in TEXT_MARK_KINDS:
        return mark["mark"].lower() if (mark := TEXT_MARK.match(record["text"])) else ""
    return record["kind"] if record["kind"] in (*CLOCK_MILESTONES, *CLOCK_ENDS) else ""


def lane_aliases(lane: str) -> tuple[str, str]:
    """The names one incident lane posts under: the desk launches `<name>-fix`, and its lane may post as `<name>`."""
    base = lane.removesuffix(FIX_ALIAS)
    return base, f"{base}{FIX_ALIAS}"


def pr_family(lane: str) -> str:
    """The name one incident's fix lane and its Opus fallback share, so the first PR either opens settles both."""
    return lane.removesuffix(CLOCK_FALLBACK).removesuffix(FIX_ALIAS)


def whole_minutes(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def ccn_failure(done: Done) -> str:
    return (done.err or done.out).strip()[-300:] or f"ccn exited {done.code}"


def clock_phases(launched: datetime, reached: dict[str, str], now: datetime) -> str:
    """How long each phase of an incident lane took, from its milestones' own stamps: `launch to mechanism 7 min, mechanism to PR 6 min, PR to now 7 min`."""
    marks = sorted((actions.parse_stamp(at), CLOCK_MILESTONES[name]) for name, at in reached.items() if name in CLOCK_MILESTONES)
    points = [(launched, "launch"), *marks, (now, "now")]
    return ", ".join(f"{start} to {end} {whole_minutes(at, until)} min" for (at, start), (until, end) in itertools.pairwise(points))


def sprite_placed(brief: Path) -> bool:
    if not brief.is_file():
        return False
    text = brief.read_text()
    if STANDING_DESK.search(text):
        return False
    placed = PLACE.search(text)
    return placed[1] == "remote" if placed else bool(CPU_HIGH.search(text))


def sprite_model(model: str) -> str:
    return SPRITE_MODELS.get(model, model)


def sprite_attempt(brief: Path) -> Path:
    return brief.parent / f"{brief.name.rsplit('.', 1)[0]}.worker"


def exit_status(path: Path) -> str:
    return path.read_text().strip() if path.is_file() else ""


def sprite_never_started(attempt: Path, exited: bool) -> bool:
    """Whether a Sprite attempt's receipts prove worker-start never ran: no `worker-start.json` was opened, and prepare exited nonzero or the launcher exited with no prepare exit recorded."""
    if (attempt / SPRITE_RECEIPT).exists():
        return False
    prepared = exit_status(attempt / SPRITE_PREPARED)
    return prepared not in ("", "0") or (exited and not prepared)


def sprite_started_as(receipt: Path) -> str:
    """` as task=<id> dispatch=<id>` for the plain ids a worker-start receipt names, empty when it names none."""
    try:
        result = json.loads(receipt.read_text())["result"]
        named = {name: result[key] for name, key in SPRITE_IDS.items() if key in result}
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return ""
    ids = [f"{name}={value}" for name, value in named.items() if isinstance(value, str) and SPRITE_ID.fullmatch(value)]
    return f" as {' '.join(ids)}" if ids else ""


def checkout_argv(checkout: Path, line: str) -> list[str]:
    """A command line whose first word is relative to the landing checkout, as an argv."""
    first, *rest = shlex.split(line)
    return [str(checkout / first), *rest]


def release_cli(checkout: Path, landing: dict) -> list[str]:
    """`landing.release_cli` as an argv."""
    return checkout_argv(checkout, landing["release_cli"])


def release_build_argv(checkout: Path, template: str, build: str) -> list[str] | None:
    """`landing.applying.build` for one build, its first word relative to the landing checkout, with `{build}`, `{pipeline}`, and `{number}` filled from the build's URL; None when it names a pipeline the build does not carry."""
    found = BUILD_URL.search(build)
    values = {"build": build, "pipeline": found["pipeline"] if found else "", "number": found["number"] if found else build}
    if "{pipeline}" in template and not values["pipeline"]:
        return None
    first, *rest = (word.format(**values) for word in shlex.split(template))
    return [str(checkout / first), *rest]


def parse_until(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.rstrip(".,;:"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def applying_fault(landing: dict | None) -> str:
    """What `landing.applying` lacks for the runner to announce applies, or empty when it is whole or absent."""
    applying = (landing or {}).get("applying")
    if applying is None:
        return ""
    if missing := [key for key in APPLYING_KEYS if not applying.get(key)]:
        return f"landing.applying names no {', '.join(missing)}"
    try:
        groups = re.compile(applying["step"]).groupindex
    except re.error as failure:
        return f"landing.applying.step is no regular expression: {failure}"
    return "" if {"component", "env"} <= set(groups) else "landing.applying.step has no `component` and `env` named groups"


def pacific(moment: datetime) -> str:
    return moment.astimezone(PACIFIC).strftime("%H:%M")


def lane_container(lane: str) -> str:
    return f"{LANE_PREFIX}{lane}"


@dataclass
class Config:
    source: Path
    store: Path | None
    drive: str
    view: Path
    desk_inbox: Path
    run: str
    receipts: Path
    briefs_repo: str
    briefs_log: str
    launch_env: dict[str, str]
    sprite: dict | None
    gc: str | None
    start_minutes: int
    launch_minutes: int
    load_hold_minutes: int
    enqueue_minutes: int
    hold_minutes: int
    release_start_minutes: int
    incident_release_minutes: int
    cut_minutes: int
    incident_mechanism_minutes: int
    incident_pr_minutes: int
    incident_ready_minutes: int
    pr_silent_minutes: int
    pr_silent_every_minutes: int
    case_lanes: int
    landing: dict | None
    alert_facts: Path | None
    desk_env: dict[str, dict[str, str]]

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = json.loads(path.read_text())
        orca = raw["orca"]
        deadlines = raw.get("deadlines", {})
        alert = raw.get("alert", {})
        return cls(
            source=path.expanduser().resolve(),
            store=Path(raw["store"]).expanduser() if raw.get("store") else None,
            drive=raw["drive"],
            view=Path(raw["view"]).expanduser(),
            desk_inbox=Path(orca["desk_inbox"]).expanduser(),
            run=orca["run"],
            receipts=Path.home() / LAUNCH_STATE / orca["run"],
            briefs_repo=str(Path(orca["briefs"]["repo"]).expanduser()),
            briefs_log=orca["briefs"].get("log", ""),
            launch_env=orca.get("launch_env", {}),
            sprite=orca.get("sprite"),
            gc=orca.get("gc"),
            start_minutes=deadlines.get("start_minutes", 10),
            launch_minutes=deadlines.get("launch_minutes", 15),
            load_hold_minutes=deadlines.get("load_hold_minutes", 5),
            enqueue_minutes=deadlines.get("enqueue_minutes", 15),
            hold_minutes=deadlines.get("hold_minutes", 15),
            release_start_minutes=deadlines.get("release_start_minutes", 10),
            incident_release_minutes=deadlines.get("incident_release_minutes", 3),
            cut_minutes=deadlines.get("cut_minutes", 4),
            incident_mechanism_minutes=deadlines.get("incident_mechanism_minutes", 10),
            incident_pr_minutes=deadlines.get("incident_pr_minutes", 15),
            incident_ready_minutes=deadlines.get("incident_ready_minutes", 20),
            pr_silent_minutes=deadlines.get("pr_silent_minutes", 10),
            pr_silent_every_minutes=deadlines.get("pr_silent_every_minutes", 5),
            case_lanes=raw.get("incident", {}).get("case_lanes", 2),
            landing=raw.get("landing"),
            alert_facts=Path(alert["facts"]).expanduser() if alert.get("facts") else None,
            desk_env={desk: (raw.get(desk) or {}).get("env", {}) for desk in DESKS},
        )


class Book:
    """Generation-checked edits over the actions store; a change raising ValueError writes nothing."""

    def __init__(self, store: actions.Store, shell: Shell):
        self.store = store
        self.shell = shell

    def stamp(self) -> str:
        return actions.stamp(self.shell.now())

    def ensure(self, container: str, owner: str) -> actions.Incident:
        return self.store.create(actions.Incident(container, owner, 0, self.stamp(), {}))

    def load(self, container: str) -> actions.Incident:
        return self.store.load(container)

    def containers(self, prefix: str) -> list[str]:
        return [name for name in self.store.ids() if name.startswith(prefix)]

    def edit(self, container: str, change: Callable[[actions.Incident], object]) -> object:
        while True:
            generation = self.store.load(container).owner_generation
            try:
                with self.store.owned(container, generation) as incident:
                    return change(incident)
            except actions.StaleGeneration:
                continue

    def attempt(self, container: str, change: Callable[[actions.Incident], object]) -> bool:
        try:
            self.edit(container, change)
        except (ValueError, actions.UnknownAction):
            return False
        return True

    def as_owner(self, container: str, owner: str, change: Callable[[actions.Incident], object]) -> bool:
        """Apply `change` only while `owner` holds the container at the generation read; a transfer in between refuses it."""
        incident = self.store.load(container)
        if incident.owner != owner:
            return False
        try:
            with self.store.owned(container, incident.owner_generation) as live:
                change(live)
        except (actions.StaleGeneration, ValueError, actions.UnknownAction):
            return False
        return True

    def known(self, container: str, action_id: str) -> actions.Action | None:
        return self.store.load(container).actions.get(action_id) if self.store.path(container).is_file() else None

    def accept(self, container: str, action_id: str, kind: str, target: str, authority: str, deadline: datetime | None) -> tuple[actions.Action, bool]:
        if known := self.known(container, action_id):
            return known, False

        def change(incident: actions.Incident) -> tuple[actions.Action, bool]:
            created = action_id not in incident.actions
            action = incident.accept(action_id, kind, target, authority, self.shell.now())
            if created and deadline:
                action.deadline = actions.stamp(deadline)
            return action, created

        return self.edit(container, change)

    def actions(self, container: str, kind: str | None = None, status: str | None = None) -> list[actions.Action]:
        return [action for action in self.store.load(container).actions.values() if (kind is None or action.kind == kind) and (status is None or action.status == status)]


@dataclass(frozen=True)
class RunView:
    statuses: dict[str, str]
    named: dict[str, set[str]]
    live: frozenset[str]
    live_worktrees: frozenset[str]

    @classmethod
    def of(cls, rows: list[dict]) -> RunView:
        """The Run's worker list as gc reads it: each dispatch's status, the dispatches naming each terminal, and the terminals and worktrees an unsettled dispatch holds."""
        named: dict[str, set[str]] = {}
        for row in rows:
            if row.get("agentTerminalHandle"):
                named.setdefault(row["agentTerminalHandle"], set()).add(row["dispatchId"])
        unsettled = [row for row in rows if row["dispatchStatus"] not in INACTIVE]
        workspaces = [(row.get("resource") or {}).get("worktreeId") or row["projection"]["workspace"]["id"] for row in unsettled]
        return cls(
            statuses={row["dispatchId"]: row["dispatchStatus"] for row in rows},
            named=named,
            live=frozenset(row["agentTerminalHandle"] for row in unsettled if row.get("agentTerminalHandle")),
            live_worktrees=frozenset(workspace.partition("::")[2] for workspace in workspaces),
        )


@dataclass
class Dispatch:
    lane: str
    id: str
    terminal: str
    status: str
    live: bool
    fenced: bool = False


class Orca:
    def __init__(self, shell: Shell, config: Config):
        self.shell = shell
        self.config = config
        self.sender: dict[str, str] = {}

    def call(self, *argv: str) -> dict:
        done = self.shell.run(["orca", *argv, "--json"], env=self.sender or None)
        try:
            return json.loads(done.out)
        except json.JSONDecodeError:
            return {"ok": False, "error": {"code": UNPARSEABLE, "message": (done.out or done.err).strip()[:300]}}

    def ask(self, *argv: str) -> dict:
        """`call`, raising OrcaUnreachable when Orca gave no answer: a runtime or connection error, a timeout, or output that is not its JSON."""
        reply = self.call(*argv)
        if not reply.get("ok") and (error := reply["error"])["code"] in UNREACHABLE:
            raise OrcaUnreachable(": ".join(filter(None, [error["code"], error.get("message")])))
        return reply

    def receipt(self, lane: str) -> str:
        """The lane's current dispatch, or empty while no launch has written a whole receipt."""
        path = self.config.receipts / f"{lane}.json"
        try:
            return json.loads(path.read_text())["result"]["dispatchId"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            return ""

    def terminal(self, lane: str) -> str:
        path = self.config.receipts / f"{lane}.terminal"
        return path.read_text().strip() if path.is_file() else ""

    def lanes(self) -> list[str]:
        return sorted(path.stem for path in self.config.receipts.glob("*.terminal"))

    def lane_of(self, handle: str) -> str:
        return next((lane for lane in self.lanes() if handle in (self.terminal(lane), f"dispatch:{self.receipt(lane)}")), handle)

    def show(self, lane: str, dispatch: str | None = None) -> Dispatch | None:
        """The lane's dispatch as Orca shows it, or None when the lane has no receipt or Orca answers that the dispatch is gone; raises OrcaUnreachable when Orca gives no answer."""
        dispatch = dispatch or self.receipt(lane)
        shown = self.ask("orchestration", "worker-show", "--dispatch", dispatch) if dispatch else {}
        if not shown.get("ok"):
            return None
        result = shown["result"]
        observation = result.get("observation") or {}
        return Dispatch(
            lane=lane,
            id=dispatch,
            terminal=(result.get("terminal") or {}).get("handle") or self.terminal(lane),
            status=result["dispatch"]["status"],
            live=observation.get("status") == "live",
            fenced=bool(result["dispatch"].get("capabilityRevokedAt")),
        )

    def stop(self, dispatch: str) -> tuple[str, str]:
        """Fence the dispatch as `orca-gc --done` does: worker-stop, or worker-abandon once Orca already fenced it; its state, or empty and the error code."""
        stopped = self.call("orchestration", "worker-stop", "--dispatch", dispatch)
        if not stopped.get("ok") and stopped["error"]["code"] == FENCED:
            stopped = self.call("orchestration", "worker-abandon", "--dispatch", dispatch)
        if not stopped.get("ok"):
            return "", stopped["error"]["code"]
        return stopped["result"]["state"], ""

    def mailbox(self, terminal: str) -> list[dict] | None:
        listed = self.call("orchestration", "check", "--terminal", terminal, "--all")
        return (listed.get("result") or {}).get("messages") or [] if listed.get("ok") else None

    def request_state(self, request: str) -> str:
        shown = self.call("orchestration", "request-show", "--request", request)
        return (shown.get("result") or {}).get("state", "absent") if shown.get("ok") else UNREAD

    def at_rest(self, terminal: str) -> bool:
        screen = self.call("terminal", "read", "--terminal", terminal, "--screen")
        return bool(screen.get("ok") and prompt_screen.input_state(screen["result"]["terminal"]) == prompt_screen.IDLE)

    def wake(self, terminal: str, text: str) -> bool:
        screen = self.call("terminal", "read", "--terminal", terminal, "--screen")
        if not (screen.get("ok") and prompt_screen.idle_prompt(screen["result"]["terminal"])):
            return False
        self.shell.run(["orca", "terminal", "send", "--terminal", terminal, "--text", text, "--enter", "--json"])
        return True

    def workers(self) -> list[dict]:
        rows: list[dict] = []
        cursor: list[str] = []
        while True:
            page = self.ask("orchestration", "worker-list", "--run", self.config.run, "--limit", "100", *cursor)["result"]
            rows += page["workers"]
            if not (after := (page.get("page") or {}).get("nextCursor")):
                return rows
            cursor = ["--cursor", after]

    def created(self, lane: str) -> dict | None:
        """The terminal orca-launch.sh's last `terminal create` for the lane answered with, or None when that create named none."""
        path = self.config.receipts / f"{lane}{CREATED}"
        try:
            reply = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return reply["result"]["terminal"] if reply.get("ok") and (reply.get("result") or {}).get("terminal", {}).get("handle") else None

    def connected_in(self, worktree: str) -> list[dict] | str:
        listed = self.ask("terminal", "list", "--worktree", f"path:{worktree}", "--limit", "1000")
        if not listed.get("ok"):
            return f"orca terminal list {listed['error']['code']}"
        return [row for row in listed["result"]["terminals"] if row.get("connected")]

    def quiet(self, terminal: str) -> bool:
        """Whether the terminal's agent sits at an empty prompt, asking nothing, with no output in the last ten minutes."""
        shown = self.ask("terminal", "show", "--terminal", terminal)
        if not (shown.get("ok") and shown["result"]["terminal"]["connected"]):
            return False
        if not (output := shown["result"]["terminal"].get("lastOutputAt")) or self.shell.now().timestamp() * 1000 - output <= QUIET.total_seconds() * 1000:
            return False
        waited = self.ask("terminal", "wait", "--terminal", terminal, "--for", "tui-idle", "--timeout-ms", str(IDLE_PROBE_MS))
        if not (waited.get("ok") and waited["result"]["wait"]["satisfied"]):
            return False
        screen = self.ask("terminal", "read", "--terminal", terminal, "--screen")
        return bool(screen.get("ok")) and prompt_screen.input_state(screen["result"]["terminal"]) == prompt_screen.IDLE and not asking(screen["result"]["terminal"])

    def close(self, terminal: str, worktree: str) -> str:
        """Close the terminal, and its tab when no other connected terminal shares it; empty, or the error code."""
        listed = self.connected_in(worktree)
        if isinstance(listed, str):
            return listed
        tab = next((row.get("tabId") for row in listed if row["handle"] == terminal), None)
        shared = any(row.get("tabId") == tab and row["handle"] != terminal for row in listed)
        closed = self.ask("terminal", "close", "--terminal", terminal, *(() if shared else ("--tab",)))
        return "" if closed.get("ok") else closed["error"]["code"]


def drop_receipt(receipt: Path, read: str) -> None:
    """Delete the receipt gc read, unless a launch has since written another in its place."""
    if receipt.read_text() == read:
        receipt.unlink()


def asking(terminal: dict) -> bool:
    """Whether the agent's last line above its input box is a question."""
    tail = [line.rstrip() for line in terminal.get("tail") or []]
    said = [line.strip() for line in tail[: prompt_screen.composer(tail)] if line.strip() and not prompt_screen.is_rule(line) and not DONE_MARKER.match(line.strip())]
    return bool(said) and said[-1].endswith("?")


def clipped_by_cci(text: str) -> bool:
    return len(text) == CCI_TEXT and text.endswith("…")


def kept_by_cci(text: str) -> str:
    """What `cci post` keeps in the record of a text it writes to a file: the first non-blank line, clipped to cci's text limit."""
    return inboxes.clip(next((line.strip() for line in text.split("\n") if line.strip()), ""), CCI_TEXT)


def request_id(reply: dict) -> str:
    return ((reply.get("error") or {}).get("data") or {}).get("orchestrationRequestId", "")


def thread_of(container: str, key: str) -> str:
    return f"{container}/{key}"


def delivers(message: dict, container: str, action: actions.Action) -> bool:
    if action.kind == "reply":
        return message.get("thread_id") in (json.loads(action.target)["reply_to"], thread_of(container, action.action_id)) and (message.get("body") or "").rstrip().endswith(f"({action.authority_ref})")
    return message.get("thread_id") == thread_of(container, action.action_id)


def resends(tries: list[actions.Action]) -> list[actions.Action]:
    return [attempt for attempt in tries if RESEND in attempt.action_id]


class Runner:
    def __init__(self, shell: Shell, config: Config, store: actions.Store):
        self.shell = shell
        self.config = config
        self.book = Book(store, shell)
        self.orca = Orca(shell, config)
        self.launching: dict[str, subprocess.Popen] = {}
        self.sprites: set[str] = set()
        self.pass_binding: dict | None = None
        self.unreachable_passes = 0
        self.pr_silent_skipped: set[str] = set()
        self.release_polled = datetime.min.replace(tzinfo=timezone.utc)
        self.book.ensure(RUNNER, RUNNER)

    def now(self) -> datetime:
        return self.shell.now()

    def escalate(self, key: str, kind: str, about: str, text: str) -> None:
        """Record one escalation per key; `flush` posts it to root through cci with the key as its topic."""
        self.record(f"escalation:{key}", f"{kind} {key} {about}: {' '.join(text.split())}")

    def record(self, action_id: str, line: str) -> None:
        self.book.accept(RUNNER, action_id, "escalation", line, "runner", None)

    def orca_down(self, down: OrcaUnreachable) -> None:
        """Count a pass Orca gave no answer to, and log `ORCA-UNREACHABLE` once for the streak it starts or continues."""
        self.unreachable_passes += 1
        streak = self.book.edit(RUNNER, lambda incident: dict(incident.facts.setdefault(UNREACHABLE_FACT, {"since": self.book.stamp(), "error": str(down)})))
        since = pacific(actions.parse_stamp(streak["since"]))
        self.escalate(f"orca:{streak['since']}", "ORCA-UNREACHABLE", "runner", f"{streak['error']}; since {since} the runner reads no dispatch as gone and fails no relay, and retries each pass with backoff up to {int(UNREACHABLE_WAIT.total_seconds())}s; ORCA-BACK follows when Orca answers")

    def orca_back(self) -> None:
        """End an unreachable streak with one `ORCA-BACK` once a pass Orca answered completes."""
        self.unreachable_passes = 0
        if UNREACHABLE_FACT not in self.book.load(RUNNER).facts:
            return
        streak = self.book.edit(RUNNER, lambda incident: incident.facts.pop(UNREACHABLE_FACT, None))
        if streak:
            since = actions.parse_stamp(streak["since"])
            self.escalate(f"orca-back:{streak['since']}", "ORCA-BACK", "runner", f"Orca answers again; it gave no answer from {pacific(since)} for {whole_minutes(since, self.now())} min ({streak['error']})")

    def pass_wait(self) -> float:
        return min(PASS_SECONDS * 2**self.unreachable_passes, UNREACHABLE_WAIT.total_seconds())

    def cci_post(self, kind: str, text: str, to: str, topic: str, key: str, message: str = "", pr: str = "", record_id: str = "", flags: list[str] | None = None, drive: str = "") -> Done:
        """Post one cci record, on this drive unless `drive` names another; a body over cci's text limit, or the whole Orca message it relays, goes to a receipts file named by `key` and rides as its path. With `record_id`, cci stores the record once and a repost returns the stored one."""
        argv = ["cci", "post", "--drive", drive or self.config.drive, "--lane", RUNNER, "--kind", kind, "--to", to, "--topic", topic, *(flags or [])]
        if pr:
            argv += ["--pr", pr]
        if record_id:
            argv += ["--id", record_id]
        if len(text) > CCI_TEXT or message:
            body = self.config.receipts / "cci" / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.txt"
            body.parent.mkdir(parents=True, exist_ok=True)
            body.write_text("\n\n".join(filter(None, [text, message.strip()])) + "\n")
            argv += ["--path", str(body)]
        if len(text) > CCI_TEXT:
            text = text[: CCI_TEXT - 1] + "…"
        return self.shell.run([*argv, "--text", text])

    def flush(self) -> bool:
        """Post each accepted escalation to root. While an incident clock runs, a DEADLINE, UNACKED, HOLDS-UNREAD, LIVENESS, STALE-MAIL, or RECLAIM about anything outside the incident waits in the view, and the pass after the last clock ends posts them as one DEFERRED record."""
        failures = []
        clocks = self.live_clocks()
        incident = {alias for lane in clocks for name in (lane, *self.siblings(lane)) for alias in lane_aliases(name)}
        if not clocks:
            self.release_deferred()
        held = set(self.book.load(RUNNER).facts.get(DEFERRED, {"ids": []})["ids"])
        for action in self.book.actions(RUNNER, kind="escalation", status="accepted"):
            label, topic = action.target.split()[:2]
            if clocks and label in DEFERRABLE and action.target.split()[2].rstrip(":") not in incident:
                if action.action_id not in held:
                    self.defer(action, clocks)
                continue
            kind = ESCALATION_KINDS.get(label, "defect" if label.endswith("-FAILED") else "report")
            message = self.message_file(topic)
            done = self.cci_post(kind, action.target, "root", topic, action.action_id, message.read_text() if message.is_file() else "")
            if done.code != 0:
                failures.append(f"cci post exited {done.code}: {(done.err or done.out).strip()[:300]}")
                continue
            self.book.attempt(RUNNER, lambda incident, key=action.action_id: (incident.start(key, self.now()), incident.complete(key, {"at": self.book.stamp(), "posted": done.out.strip()})))
        if failures:
            print(f"{len(failures)} escalation(s) left for the next pass; {failures[0]}", file=sys.stderr)
        return not failures

    def defer(self, action: actions.Action, clocks: list[str]) -> None:
        def hold(incident: actions.Incident) -> None:
            held = incident.facts.setdefault(DEFERRED, {"incident": ", ".join(sorted(clocks)), "ids": []})
            if action.action_id not in held["ids"]:
                held["ids"].append(action.action_id)

        self.book.edit(RUNNER, hold)

    def release_deferred(self) -> None:
        """Settle every escalation deferred during the incident and log them as one DEFERRED line naming each."""
        held = self.book.load(RUNNER).facts.get(DEFERRED)
        if not held:
            return
        lines = [self.book.load(RUNNER).actions[key].target for key in held["ids"]]

        def settle(incident: actions.Incident) -> None:
            for key in held["ids"]:
                if incident.action(key).status == "accepted":
                    incident.start(key, self.now())
                    incident.complete(key, {"at": self.book.stamp(), "deferred": True})
            incident.facts.pop(DEFERRED)

        self.book.edit(RUNNER, settle)
        self.record(f"escalation:deferred:{held['ids'][0]}", f"DEFERRED {len(lines)} escalations held during {held['incident']}: {' | '.join(lines)}")

    def runner_log(self, desk: str) -> Path:
        return self.config.desk_inbox.parent.parent / f"{desk}-runner.log"

    def restart_command(self, desk: str) -> str:
        env = "".join(f"{name}={shlex.quote(value)} " for name, value in self.config.desk_env[desk].items())
        return f"nohup {f'env {env}' if env else ''}desk-runner.py run --config {self.config.source} --desk {desk} >> {self.runner_log(desk)} 2>&1 < /dev/null &"

    def beat(self, desk: str) -> None:
        inbox = self.config.desk_inbox.parent
        beat = {"pid": os.getpid(), "at": self.now().isoformat(), "config": str(self.config.source), "terminal": self.shell.env("ORCA_TERMINAL_HANDLE"), "restart": self.restart_command(desk), "version": RUNNING_VERSION}
        inbox.mkdir(parents=True, exist_ok=True)
        staged = inbox / f".{HEARTBEATS[desk]}"
        staged.write_text(json.dumps(beat) + "\n")
        staged.replace(inbox / HEARTBEATS[desk])

    def stale(self, desk: str) -> bool:
        """Whether a newer plugin version is installed than this runner runs, noting it once per installed version; the loop re-executes into it at the end of the pass."""
        installed = drive.installed_version()
        if not drive.behind(RUNNING_VERSION, installed):
            return False
        self.escalate(
            f"stale:{desk}:{RUNNING_VERSION}<{installed}",
            "RUNNER-STALE",
            desk,
            f"{RUNNING_VERSION} < {installed}: this {desk} runner, pid {os.getpid()}, re-executes into {installed} at the end of this pass; nothing to do; a hand restart is `{self.restart_command(desk)}`",
        )
        return True

    def reexec(self, desk: str) -> None:
        self.shell.reexec([str(RUNNER_BIN), "run", "--config", str(self.config.source), "--desk", desk])

    def stop_marker(self) -> Path:
        return self.config.source.with_name(f"{self.config.source.name}.stop")

    def stop_requested(self) -> bool:
        marker = self.stop_marker()
        return marker.is_file() and marker.read_text().strip() == str(os.getpid())

    def rest(self, seconds: float) -> bool:
        for _ in range(int(seconds / WAKE_SECONDS)):
            if self.stop_requested():
                return True
            self.shell.sleep(WAKE_SECONDS)
        return self.stop_requested()

    @staticmethod
    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def handoff(self, desk: str) -> int:
        beat = self.config.desk_inbox.parent / HEARTBEATS[desk]
        pid = json.loads(beat.read_text())["pid"] if beat.is_file() else None
        if pid is not None and self.alive(pid):
            marker = self.stop_marker()
            marker.write_text(f"{pid}\n")
            deadline = self.now() + HANDOFF_WAIT
            while marker.exists():
                if self.now() >= deadline:
                    print(f"desk-runner handoff: runner {pid} wrote no exit mark within {HANDOFF_WAIT}; {marker} stays until it does, and no successor started", file=sys.stderr)
                    return 1
                self.shell.sleep(WAKE_SECONDS)
        started = self.shell.spawn([str(RUNNER_BIN), "run", "--config", str(self.config.source), "--desk", desk], self.runner_log(desk), self.config.desk_env[desk], append=True)
        print(f"desk-runner handoff: {f'runner {pid} exited' if pid is not None else f'no live {desk} runner'}; started runner {started.pid}, logging to {self.runner_log(desk)}")
        return 0

    def binding(self, via: str = "") -> dict:
        """Ask Orca whether the Run's coordinator is a live terminal that coordinates it, and record the answer, with how it was obtained, in the runner's state."""
        prior = self.book.load(RUNNER).facts.get(BINDING) or {}
        shown, held = self.follow_coordinator()
        terminal = held.get("coordinator_handle") or ""
        current = self.orca.call("orchestration", "run-current") if terminal else {}
        coordinates = ((current.get("result") or {}).get("run") or {}).get("id") or ""
        state = {
            "terminal": terminal,
            "run": self.config.run,
            "bound": bool(terminal) and coordinates == self.config.run,
            "generation": held.get("consumer_generation"),
            "at": self.book.stamp(),
        }
        if not shown.get("ok"):
            error = shown.get("error") or {}
            state["why"] = f"orca orchestration run-show failed: {error.get('code')}: {error.get('message')}"
        elif not terminal:
            state["why"] = f"Orca binds {self.config.run} to no terminal"
        elif not current.get("ok"):
            error = current.get("error") or {}
            state["why"] = f"orca orchestration run-current as coordinator {terminal} failed: {error.get('code')}: {error.get('message')}"
        elif not state["bound"]:
            state["why"] = f"coordinator {terminal} coordinates {coordinates or 'no Run'}, not {self.config.run}"
        if via:
            state["via"] = via
        elif state["bound"] and prior.get("bound") and (prior.get("terminal"), prior.get("generation")) == (terminal, state["generation"]):
            state["via"] = prior.get("via", "")
        elif state["bound"]:
            state["via"] = f"coordinator read from Orca at generation {state['generation']}"
        self.book.edit(RUNNER, lambda incident: incident.facts.update({BINDING: state}))
        return state

    def follow_coordinator(self) -> tuple[dict, dict]:
        """Orca resolves the sender from the pane key before the handle, so the pane key is cleared for the coordinator's handle to count."""
        self.orca.sender = {}
        shown = self.orca.call("orchestration", "run-show", "--id", self.config.run)
        held = ((shown.get("result") or {}).get("run")) or {}
        if coordinator := held.get("coordinator_handle"):
            self.orca.sender = {"ORCA_TERMINAL_HANDLE": coordinator, "ORCA_PANE_KEY": ""}
        return shown, held

    def rebind_command(self) -> str:
        return f"desk-runner.py rebind --config {self.config.source}"

    def unbound(self, state: dict) -> str:
        return f"desk-runner cannot start workers on {self.config.run}: {state['why']}. From the coordinator's Orca terminal run `{self.rebind_command()}`; the runner follows the new coordinator on its next pass, so it needs no restart"

    def binding_line(self, state: dict | None) -> str:
        if not state:
            return f"binding: unchecked; `{self.rebind_command()}` binds this terminal"
        checked = pacific(actions.parse_stamp(state["at"]))
        if not state["bound"]:
            return f"binding: UNBOUND {state['why']}; checked {checked}; fix: `{self.rebind_command()}` from the coordinator's terminal"
        return f"binding: terminal {state['terminal']} coordinates {state['run']} at generation {state['generation']}, via {state['via']}; checked {checked}"

    def bound_for_launch(self) -> bool:
        """Check the binding once per delivery pass that launches; an unbound runner fails its launches and escalates once per binding state."""
        if self.pass_binding is None:
            self.pass_binding = self.binding()
            state = self.pass_binding
            if not state["bound"]:
                self.escalate(f"unbound:{state['terminal']}:{state['generation']}", "UNBOUND", "runner", f"{self.unbound(state)}; each launch fails with LAUNCH-FAILED until it is bound")
        return self.pass_binding["bound"]

    def rebind(self) -> int:
        self.orca.sender = {}
        if not self.shell.env("ORCA_TERMINAL_HANDLE"):
            print(f"desk-runner rebind: no ORCA_TERMINAL_HANDLE; run `{self.rebind_command()}` from the coordinator's Orca terminal", file=sys.stderr)
            return 3
        previous = ((self.orca.call("orchestration", "run-show", "--id", self.config.run).get("result") or {}).get("run")) or {}
        used = self.orca.call("orchestration", "run-use", "--id", self.config.run)
        if not used.get("ok"):
            error = used.get("error") or {}
            print(f"desk-runner rebind: orca orchestration run-use failed: {error.get('code')}: {error.get('message')}", file=sys.stderr)
            return 1
        state = self.binding(via=f"orca orchestration run-use by desk-runner rebind at {pacific(self.now())}, replacing {previous.get('coordinator_handle') or 'no terminal'} at generation {previous.get('consumer_generation')}")
        print(self.binding_line(state))
        return 0 if state["bound"] else 1

    def lane(self, lane: str) -> str:
        container = lane_container(lane)
        self.book.ensure(container, self.orca.receipt(lane) or UNLAUNCHED)
        return container

    def accept_relay(self, key: str, lane: str, text: str, reply_to: str, minutes: int) -> tuple[actions.Action, bool]:
        target = json.dumps({"text": text, "reply_to": reply_to})
        return self.book.accept(self.lane(lane), key, "reply" if reply_to else "relay", target, key, self.now() + timedelta(minutes=minutes))

    def read_inbox(self) -> None:
        """Act on each complete line appended to the desk inbox since the saved byte offset; a first read starts at the end, so history never replays."""
        path = self.config.desk_inbox
        fact = f"desk-inbox:{path}"
        cursor = self.book.load(RUNNER).facts.get(fact)
        if cursor is None:
            start = inboxes.Inbox(path).end() if path.is_file() else 0
            self.book.edit(RUNNER, lambda incident: incident.facts.update({fact: start}))
            return
        if not path.is_file():
            return
        for line in inboxes.Inbox(path).lines(cursor):
            self.inbox_line(str(line.start), line.text.strip())
            self.book.edit(RUNNER, lambda incident, at=line.end: incident.facts.update({fact: at}))

    def read_cci(self) -> None:
        """Act in sequence order on each cci record past the saved sequence that is addressed to the orca desk, as one desk inbox line, or that this drive's root posted with `--re`, as a reply; a peer drive's root reply relays nothing. A reply to the runner's `DECIDE` on a worker question answers that question, a reply to a lane's own record answers that lane's open question, which the cci reply superseded, and a `withdraw` of a launch record withdraws that launch. A first read starts at the newest record, and records imported from the desk inbox were already read there."""
        fact = f"cci:{self.config.drive}:{ORCA_DESK}"
        cursor = self.book.load(RUNNER).facts.get(fact)
        try:
            if cursor is None:
                newest = cci.records(self.cci_tail, self.config.drive, "--limit", "1")
                self.book.edit(RUNNER, lambda incident: incident.facts.update({fact: max((record["seq"] for record in newest), default=0)}))
                return
            addressed = cci.records(self.cci_tail, self.config.drive, "--to", ORCA_DESK, since=cursor)
            replies = [record for record in cci.records(self.cci_tail, self.config.drive, "--lane", ROOT, since=cursor) if record.get("re")]
            already_read = f"import:{self.config.desk_inbox.resolve()}"
            for record in sorted({record["seq"]: record for record in [*addressed, *replies]}.values(), key=lambda record: record["seq"]):
                if record.get("re"):
                    if record["lane"] == ROOT and record["drive"] == self.config.drive:
                        self.root_reply(record, self.cci_record(record["re"]))
                elif record["source"] != already_read:
                    self.cci_line(record)
                self.book.edit(RUNNER, lambda incident, at=record["seq"]: incident.facts.update({fact: at}))
        except CciTailFailed as failure:
            self.tail_failed(failure)

    def tail_failed(self, failure: CciTailFailed) -> None:
        self.escalate(f"cci-tail:{actions.stamp(self.now())[:15]}", "CCI-TAIL-FAILED", "runner", str(failure))

    def cci_record(self, seq: int) -> dict | None:
        out = self.cci_tail(["cci", "tail", "--drive", self.config.drive, "--since", str(seq - 1), "--json", "--budget", str(cci.PAGE_BYTES)])
        return next((record for line in out.splitlines() if line.strip() and (record := json.loads(line))["seq"] == seq), None)

    def root_reply(self, record: dict, closed: dict | None) -> None:
        at = f"cci#{record['seq']}"
        if not closed:
            return
        if record["kind"] == "withdraw":
            directive = INBOX_DIRECTIVE.match(closed["text"].strip())
            if ORCA_DESK in (closed.get("to") or []) and directive and directive["verb"] == "launch":
                self.withdraw(at, at, directive["key"] or f"inbox@cci#{closed['seq']}")
            return
        if closed["lane"] == RUNNER and (decide := DECIDE_LINE.match(closed["text"])):
            if text := self.whole_text(at, at, decide["lane"], record):
                self.relay_to(at, at, decide["lane"], text, self.unclaimed(at, decide["lane"], [decide["msg"]]))
            return
        dispatch = self.orca.show(closed["lane"])
        if dispatch and dispatch.status not in INACTIVE and (questions := self.open_questions(dispatch)) and (text := self.whole_text(at, at, closed["lane"], record)):
            self.relay_to(at, at, closed["lane"], f"The root answered on cci #{record['seq']} (re #{closed['seq']}), which supersedes this question: {text}", self.unclaimed(at, closed["lane"], questions))

    def whole_text(self, at: str, key: str, about: str, record: dict) -> str:
        """The record's text in full: the file its `refs.path` names when cci clipped the record or kept that file's first line as the record's text, else the record's own text; empty, logging `RELAY-FAILED`, when that file cannot be read here."""
        kept, path = record["text"].strip(), (record.get("refs") or {}).get("path")
        if not path:
            return kept
        try:
            written = Path(path).read_text(errors="replace").strip()
        except OSError:
            self.record(f"escalation:relay:{at}", f"RELAY-FAILED {key} {about}: the record's full text at {path} is not readable here; nothing was relayed; post it again on the machine this runner runs on")
            return ""
        return written if clipped_by_cci(kept) or kept_by_cci(written) == kept else kept

    def cci_tail(self, argv: list[str]) -> str:
        done = self.shell.run(argv)
        if done.code != 0:
            raise CciTailFailed(f"cci tail exited {done.code}: {(done.err or done.out).strip()[:300]}")
        return done.out

    def cci_line(self, record: dict) -> None:
        seq, line = record["seq"], record["text"].strip()
        directive = INBOX_DIRECTIVE.match(line)
        if directive and directive["verb"] == "relay" and not (line := self.whole_text(f"cci#{seq}", directive["key"] or f"inbox@cci#{seq}", "inbox", record)):
            return
        if not self.inbox_line(f"cci#{seq}", line):
            self.record(f"escalation:cci:{seq}", f"DIRECTIVE-FAILED cci#{seq} inbox: a record addressed to {ORCA_DESK} is one `orca-desk: <relay|launch|alert|incident|hold|unhold|withdraw> …` line, or a reply with --re; nothing was done")

    def inbox_line(self, at: str, line: str) -> bool:
        directive = INBOX_DIRECTIVE.match(line)
        if not directive:
            return False
        key = directive["key"] or f"inbox@{at}"
        verbs = {"relay": self.relay_line, "launch": self.launch_line, "alert": self.alert_line, "incident": self.incident_line, "hold": self.hold_line, "unhold": self.unhold_line, "withdraw": self.withdraw_line}
        verbs[directive["verb"]](at, key, directive["rest"])
        return True

    def relay_line(self, at: str, key: str, rest: str) -> None:
        parsed = RELAY_TO.match(rest)
        if not parsed or "; relay " in parsed["text"].partition("\n")[0]:
            self.record(f"escalation:relay:{at}", f"RELAY-FAILED {key} inbox: one relay per line, in the form `{RELAY_GRAMMAR}`; nothing was relayed")
            return
        for lane in LANE_LIST.split(parsed["lanes"]):
            self.relay_to(at, key, lane, parsed["text"])

    def relay_to(self, at: str, key: str, lane: str, text: str, question: str | None = None) -> None:
        """Accept what `relay` accepts for one lane: a reply to `question`, or to the current dispatch's latest open question no other reply claims when none is named, else a plain relay."""
        log = f"escalation:relay:{at}:{lane}"
        dispatch = self.orca.show(lane)
        if not dispatch or dispatch.status in INACTIVE:
            self.record(log, f"RELAY-FAILED {key} {lane}: no live dispatch{f' ({dispatch.id} is {dispatch.status})' if dispatch else ''}")
            return
        question = self.unclaimed(key, lane, self.open_questions(dispatch)) if question is None else question
        action, created = self.accept_relay(key, lane, text, question, self.config.start_minutes)
        if not created and json.loads(action.target)["text"] != text:
            self.record(log, f"RELAY-FAILED {key} {lane}: {key} already holds a different relay to {lane} ({action.status})")
            return
        self.record(log, f"RELAYED {key} {lane}: {f'reply to question {question} of' if question else 'relay to'} dispatch {dispatch.id}")

    def launch_line(self, at: str, key: str, rest: str) -> None:
        """Accept what `launch` accepts, `NOW` meaning `--owner-directed`, once per key and only for a lane with no live dispatch and no launch in flight."""
        log = f"escalation:launch:{at}"
        spec = LAUNCH_SPEC.match(rest)
        if not spec:
            self.record(log, f"LAUNCH-FAILED {key} inbox: one launch per line, in the form `{LAUNCH_GRAMMAR}`; nothing was launched")
            return
        lane, brief = spec["lane"], Path(spec["brief"]).resolve()
        if refusal := self.launch_refusal(key, lane, spec["model"], spec["effort"], brief, spec["role"] or ""):
            self.record(log, f"LAUNCH-FAILED {key} {lane}: {refusal}; nothing was launched")
            return
        if not spec["now"] and (holder := self.duplicate_holder(lane, brief)):
            self.record(log, duplicate_line(key, lane, holder))
            return
        self.refresh_slice(key, lane, brief)
        self.accept_launch(key, lane, spec["model"], spec["effort"], str(brief), bool(spec["now"]), role=spec["role"] or "")

    def withdraw_line(self, at: str, key: str, rest: str) -> None:
        spec = WITHDRAW_SPEC.match(rest)
        if not spec:
            self.record(f"escalation:withdraw:{at}", f"WITHDRAW-FAILED {key} inbox: one withdrawal per line, in the form `{WITHDRAW_GRAMMAR}`; nothing was withdrawn")
            return
        self.withdraw(at, key, spec["target"])

    def withdraw(self, at: str, key: str, target: str) -> None:
        """Withdraw the launch under a launch key, or every launch of a lane named: a launch not yet started fails, so it never launches, and a launched dispatch is stopped and its lane marked withdrawn."""
        log = f"escalation:withdraw:{at}"
        keyed = bool(LAUNCH_KEY.fullmatch(target))
        if keyed:
            held = [(container.removeprefix(LANE_PREFIX), [action]) for container in self.book.containers(LANE_PREFIX) if (action := self.book.load(container).actions.get(target)) and action.kind == "launch"]
        else:
            known = lane_container(target) in self.book.store.ids()
            held = [(target, self.book.actions(lane_container(target), kind="launch") if known else [])] if known or self.orca.receipt(target) else []
        if not held:
            self.record(log, f"WITHDRAW-FAILED {key} {target}: the runner holds no launch or lane under {target}; nothing was withdrawn")
        for lane, launches in held:
            for action in launches:
                if action.status == "accepted":
                    self.book.edit(self.lane(lane), lambda incident, action=action: incident.fail(action.action_id, f"withdrawn by {key}"))
                    self.record(f"{log}:{lane}:{action.action_id}", f"WITHDRAWN {action.action_id} {lane}: by {key}; nothing was launched")
                elif action.status == "started":
                    self.record(f"{log}:{lane}:{action.action_id}", f"WITHDRAW-FAILED {key} {lane}: launch {action.action_id} is still starting; withdraw it again once it logs LAUNCHED")
            if keyed:
                dispatch = launches[0].dispatch_id if launches[0].status == "verified" else None
            else:
                dispatch = self.orca.receipt(lane)
            if dispatch:
                self.stop_withdrawn(f"{log}:{lane}", key, target, lane, dispatch)
            elif keyed and launches[0].status not in ("accepted", "started"):
                self.record(f"{log}:{lane}", f"WITHDRAW-FAILED {key} {lane}: launch {target} is {launches[0].status}; nothing was withdrawn")

    def stop_withdrawn(self, log: str, key: str, target: str, lane: str, dispatch: str) -> None:
        """Mark the lane's dispatch withdrawn, so no report of it launches a case lane and Orca's offer to dispatch it again asks no one, then stop it unless it already settled."""
        self.book.edit(self.lane(lane), lambda incident: incident.facts.update({WITHDRAWN: {"dispatch": dispatch, "by": key}}))
        shown = self.orca.show(lane, dispatch)
        if shown and shown.status in INACTIVE:
            self.record(log, f"WITHDRAWN {target} {lane}: by {key}; dispatch {dispatch} was already {shown.status}, and no report of it launches a case lane")
            return
        state, error = self.orca.stop(dispatch)
        if not state:
            self.record(log, f"WITHDRAW-FAILED {key} {lane}: worker-stop of dispatch {dispatch} failed: {error}; it is marked withdrawn, so no report of it launches a case lane; stop it with orca-gc --done")
            return
        if unconfirmed := self.unconfirmed_stop(lane, dispatch):
            self.record(log, f"WITHDRAW-UNCONFIRMED {key} {lane}: worker-stop of dispatch {dispatch} answered {state}, but after {int(STOP_CONFIRM.total_seconds())}s Orca shows {unconfirmed}; it is marked withdrawn, so no report of it launches a case lane; stop it with orca-gc --done")
            return
        self.record(log, f"WITHDRAWN {target} {lane}: by {key}; dispatch {dispatch} stopped ({state}), and no report of it launches a case lane")

    def unconfirmed_stop(self, lane: str, dispatch: str) -> str:
        """Poll Orca until the stopped dispatch is settled and its terminal is gone or at an idle prompt; empty once it is, or what Orca last showed when STOP_CONFIRM ran out first."""
        deadline = self.now() + STOP_CONFIRM
        while True:
            shown = self.orca.show(lane, dispatch)
            if shown is None:
                seen = f"no dispatch {dispatch}, and terminal {self.orca.terminal(lane) or 'unknown'} unchecked"
            elif shown.status not in INACTIVE:
                seen = f"dispatch {dispatch} still {shown.status} on terminal {shown.terminal}"
            elif shown.live and not self.orca.at_rest(shown.terminal):
                seen = f"dispatch {dispatch} {shown.status}, but terminal {shown.terminal} still at work"
            else:
                return ""
            if self.now() >= deadline:
                return seen
            self.shell.sleep(STOP_POLL_SECONDS)

    def withdrawn_by(self, lane: str) -> str:
        """Who withdrew the lane's current dispatch, or empty."""
        container = lane_container(lane)
        mark = (self.book.load(container).facts.get(WITHDRAWN) or {}) if container in self.book.store.ids() else {}
        return mark.get("by", "") if mark and mark["dispatch"] == self.orca.receipt(lane) else ""

    def alert_line(self, at: str, key: str, rest: str) -> None:
        """Record a monitor transition and launch nothing, or relay a repeat to the fix lane already on it; the alerts desk decides whether an incident lane is necessary."""
        log = f"escalation:alert:{at}"
        spec = ALERT_SPEC.match(rest)
        if not spec:
            self.record(log, f"ALERT-FAILED {key} inbox: one alert per line, in the form `{ALERT_GRAMMAR}`; nothing was recorded")
            return
        slug, link, what = spec["slug"], spec["link"], spec["what"]
        lane = f"{slug}-fix"
        dispatch = self.orca.show(lane)
        if dispatch and dispatch.status not in INACTIVE:
            self.relay_to(at, f"{key}:again", lane, f"The alert fired again at {pacific(self.now())}: {what} {link}")
            return
        self.record(log, f"ALERT {slug} {pacific(self.now())}: {what} {link} | no lane launched; the alerts desk writes `{INCIDENT_GRAMMAR}` when it judges one necessary")

    def incident_line(self, at: str, key: str, rest: str) -> None:
        """Launch the alert's incident fix lane on a brief freshly attached to the briefs log from the template, or relay a repeat to the lane already on it; the root ratifies from the INCIDENT line."""
        log = f"escalation:incident:{at}"
        spec = INCIDENT_SPEC.match(rest)
        if not spec:
            self.record(log, f"INCIDENT-FAILED {key} inbox: one incident per line, in the form `{INCIDENT_GRAMMAR}`; nothing was launched")
            return
        slug, link, what = spec["slug"], spec["link"], spec["what"]
        lane = f"{slug}-fix"
        dispatch = self.orca.show(lane)
        if dispatch and dispatch.status not in INACTIVE:
            self.relay_to(at, f"{key}:again", lane, f"The alert fired again at {pacific(self.now())}: {what} {link}")
            return
        monitors = set(MONITOR_ID.findall(f"{link} {what}"))
        if monitors and (peer := self.incident_lane(monitors, lane)):
            self.record(log, f"LAUNCH-SKIPPED {key} {lane}: duplicate of {peer}, already on monitor {', '.join(sorted(monitors))}")
            return
        try:
            owner = self.incident_owner(slug, lane)
        except CciTailFailed as failure:
            self.tail_failed(failure)
            owner = ""
        if owner:
            self.escalate(key, "INCIDENT-OWNED", lane, f"incident {slug} owned by {owner}; no launch")
            return
        if refusal := self.launch_refusal(key, lane, INCIDENT_MODEL, "xhigh", ALERT_TEMPLATE):
            self.record(log, f"INCIDENT {slug} again at {pacific(self.now())}: {what} {link} | {lane} not launched: {refusal}")
            return
        incident = "".join(f" incident={monitor}" for monitor in sorted(monitors)[:1])
        reported = datetime.strptime(spec["reported"], "%I:%M %p" if spec["reported"].endswith("M") else "%H:%M")
        brief, failure = self.attach_brief(
            lane,
            lambda briefs: self.sliced(
                key,
                lane,
                ALERT_TEMPLATE.read_text().format(lane=lane, incident=incident, slug=slug, link=link, what=what, onset=pacific(self.now()), reported=f"{reported:%H:%M}", due=f"{reported + INCIDENT_BAR:%H:%M}", repo=self.config.briefs_repo, log=briefs, facts=self.drive_facts()),
            ),
            f"INCIDENT {slug}: fix brief for {lane}: {what} {link}",
        )
        if brief is None:
            self.record(log, f"INCIDENT {slug} {pacific(self.now())}: {what} {link} | {lane} not launched: {failure}")
            return
        self.accept_launch(key, lane, INCIDENT_MODEL, "xhigh", str(brief), True, incident=slug)
        self.record(log, f"INCIDENT {slug} {pacific(self.now())}: {what} {link} | fix lane {lane} launching on incident xhigh, brief {brief}; root: ratify, spawn the evidence and incident-doc lanes, fence the target, start comms in the affected account channels and #outage")

    def drive_facts(self) -> str:
        return self.config.alert_facts.read_text().strip() if self.config.alert_facts else "none recorded for this drive"

    def attach_brief(self, lane: str, render: Callable[[str], str], entry: str) -> tuple[Path | None, str]:
        """Attach the brief `render` writes for the resolved briefs log to that log as `<lane>.full.md`; its path, or why it has none."""
        if failure := self.resolve_briefs_log():
            return None, f"the briefs log did not resolve: {failure}"
        if failure := self.attach(f"{lane}.full.md", render(self.config.briefs_log), entry):
            return None, f"the brief did not attach: {failure}"
        brief = self.attachment(f"{lane}.full.md")
        return (brief, "") if brief else (None, "the attached brief has no path")

    def unfixed(self, lane: str, message: dict) -> None:
        """Launch an incident lane for each case an incident fix lane's `ready:` or `fix-live:` report names as `UNFIXED <lane>: <case>`, up to `incident.case_lanes` per incident, so a case its fix does not cover is never left as backlog. A case is known once its launch is taken, and known across the lane, its fallback, and its case lanes. Every other new case goes to the root as one `DECIDE` carrying a launch line per case: each past the cap, each from a case lane, since case lanes are one generation, and each from a lane whose report or newest mechanism says its cause is UNKNOWN. A report from a lane the root withdrew, or after its incident ended, launches nothing and goes to the root as one line."""
        cases = unfixed_cases(message.get("body") or "", lane)
        if not cases or handed_back(message) or lane_container(lane) not in self.book.store.ids():
            return
        launch = fix_launch(self.book.load(lane_container(lane)))
        if not launch:
            return
        first = first_lane(lane)
        container = self.lane(first)
        taken = self.book.load(container).facts.get(UNFIXED_CASES) or []
        fresh = [(index, known, case) for index, (known, case) in enumerate(cases.items(), 1) if known not in taken]
        if not fresh:
            return
        key = f"{UNFIXED_KEY}{message['id']}"
        unlaunched = f"UNFIXED {lane} {pacific(self.now())}: {'; '.join(case for _, _, case in fresh)} | no case lane launched"
        if by := self.withdrawn_by(lane):
            self.record(f"escalation:{key}", f"{unlaunched}: the root withdrew {lane} ({by})")
            return
        try:
            ended = self.incident_end(first, lane, actions.parse_stamp(message["created_at"]))
            unknown = "" if ended else self.unknown_cause(lane, launch, message)
        except CciTailFailed as failure:
            self.tail_failed(failure)
            ended, unknown = "", "cci could not be read to tell whether the incident is open and its cause known"
        if ended:
            self.record(f"escalation:{key}", f"{unlaunched}: the incident ended with {ended}")
        elif is_case_lane(lane):
            self.offer_cases(key, first, lane, launch, [case for _, _, case in fresh], "a case lane reports new cases, past the one-generation cap")
        elif unknown:
            self.offer_cases(key, first, lane, launch, [case for _, _, case in fresh], f"{unknown}, so no case lane launches until the root ratifies it")
        else:
            room = max(self.config.case_lanes - len(taken), 0)
            for index, known, case in fresh[:room]:
                if self.case_lane(next(self.case_names(first)), lane, launch, case, f"{key}:{index}"):
                    taken = [*taken, known]
                    self.book.edit(container, lambda incident, taken=taken: incident.facts.update({UNFIXED_CASES: taken}))
            if fresh[room:]:
                self.offer_cases(key, first, lane, launch, [case for _, _, case in fresh[room:]], f"past the cap of {self.config.case_lanes} case lanes per incident")

    def case_names(self, first: str) -> Iterator[str]:
        """`<first>-case<n>` for each n no lane container holds yet, in order."""
        held = set(self.book.store.ids())
        return (name for n in itertools.count(1) if lane_container(name := f"{first}{CASE}{n}") not in held)

    def incident_end(self, first: str, lane: str, reported: datetime | None = None) -> str:
        """The record that ended the incident `first` leads, as `#<seq> <KIND> <lane>`: a `not-ours` or `stand-down` its fix lane or Opus fallback posted, the root's `stand-down` or `stopped` to either of them or to `lane`, or a `stopped` that `lane` posted before `reported`; empty while the incident is open."""
        owners = {*lane_aliases(first), *lane_aliases(f"{first}{CLOCK_FALLBACK}")}
        own = set(lane_aliases(lane))
        launch = fix_launch(self.book.load(lane_container(first)))
        since = actions.parse_stamp(launch.verification_receipt["at"]) if launch else datetime.min.replace(tzinfo=timezone.utc)
        posted = cci.records(self.cci_tail, self.config.drive, *(arg for name in sorted(owners | own | {ROOT}) for arg in ("--lane", name)), *(f"--kind={kind}" for kind in (*INCIDENT_ENDS, STOPPED)))
        for record in posted:
            at = actions.parse_stamp(record["at"])
            if record["drive"] != self.config.drive or at < since:
                continue
            if (
                (record["lane"] in owners and record["kind"] in INCIDENT_ENDS)
                or (record["lane"] == ROOT and record["kind"] in (STAND_DOWN, STOPPED) and (owners | own) & set(record.get("to") or []))
                or (record["lane"] in own and record["kind"] == STOPPED and reported is not None and at < reported)
            ):
                return f"#{record['seq']} {record['kind'].upper()} {record['lane']}"
        return ""

    def incident_owner(self, slug: str, lane: str) -> str:
        """Who else holds the incident `slug`, as `<lane>@<drive> <kind> #<seq>`, or empty while this drive does: the root's `done` that resolves a record naming the slug, its `stopped` addressed to `lane`, or another drive's root `answer` addressed to `root@<this drive>` that names the slug."""
        named = {record["seq"] for record in cci.records(self.cci_tail, self.config.drive, "--kind=incident") if record["drive"] == self.config.drive and (record.get("topic") == slug or slug in record["text"])}
        names = set(lane_aliases(lane))
        posted = cci.records(self.cci_tail, self.config.drive, "--lane", ROOT, "--kind=done", "--kind=stopped", since=min(named, default=0))
        answered = cci.records(self.cci_tail, self.config.drive, "--reader", ROOT, "--kind=answer")
        claims = [
            *(record for record in posted if record["drive"] == self.config.drive and ((record["kind"] == "done" and record.get("resolves") in named) or (record["kind"] == STOPPED and names & set(record.get("to") or [])))),
            *(record for record in answered if record["drive"] != self.config.drive and record["lane"] == ROOT and record["kind"] == "answer" and f"{ROOT}@{self.config.drive}" in (record.get("to") or []) and slug in record["text"]),
        ]
        return next((f"{record['lane']}@{record['drive']} {record['kind']} #{record['seq']}" for record in claims), "")

    def unknown_cause(self, lane: str, launch: actions.Action, message: dict) -> str:
        """Which of the lane's records says its cause is UNKNOWN: the report itself, or its newest mechanism record or status since its launch; empty when none does."""
        if UNKNOWN_CAUSE.search(f"{message.get('subject') or ''}\n{message.get('body') or ''}"):
            return f"its report {message['id']} says the cause is UNKNOWN"
        launched = actions.parse_stamp(launch.verification_receipt["at"])
        marks = [(record["at"], f"#{record['seq']}", record["text"]) for record in self.lane_records(lane, "mechanism", *TEXT_MARK_KINDS) if clock_mark(record) == "mechanism"]
        statuses = self.book.load(lane_container(lane)).facts.get(MILESTONE_STATUSES) or {}
        marks += [(status["at"], f"its status {msg}", path.read_text()) for msg, status in statuses.items() if status["name"] == "mechanism" and (path := self.message_file(msg)).is_file()]
        newest = max((mark for mark in marks if actions.parse_stamp(mark[0]) >= launched), key=lambda mark: actions.parse_stamp(mark[0]), default=None)
        return f"its mechanism {newest[1]} says the cause is UNKNOWN" if newest and UNKNOWN_CAUSE.search(newest[2]) else ""

    def case_brief(self, key: str, lane: str, parent: str, launch: actions.Action, case: str) -> tuple[Path | None, str]:
        """The unfixed-case brief for the case with its register slice, freshly attached to the briefs log; its path, or why it has none."""
        prs = self.opened_prs(parent, launch)
        return self.attach_brief(
            lane,
            lambda briefs: self.sliced(
                key,
                lane,
                UNFIXED_TEMPLATE.read_text().format(
                    lane=lane, parent=parent, case=case, parent_brief=json.loads(launch.target)["brief"], prs=prs, onset=pacific(self.now()), reported=pacific(self.now()), due=pacific(self.now() + INCIDENT_BAR), log=briefs, facts=self.drive_facts()
                ),
            ),
            f"INCIDENT {parent}: unfixed case brief for {lane}: {case}",
        )

    def case_lane(self, lane: str, parent: str, launch: actions.Action, case: str, key: str) -> bool:
        """Launch a case lane on the incident alias from the unfixed-case brief, freshly attached to the briefs log, and say whether the launch was taken; the root ratifies from the INCIDENT line."""
        log = f"escalation:{key}"
        line = f"INCIDENT {parent} {pacific(self.now())}: its fix does not cover: {case}"
        if refusal := self.launch_refusal(key, lane, INCIDENT_MODEL, "xhigh", UNFIXED_TEMPLATE):
            self.record(log, f"{line} | case lane {lane} not launched: {refusal}")
            return False
        brief, failure = self.case_brief(key, lane, parent, launch, case)
        if brief is None:
            self.record(log, f"{line} | case lane {lane} not launched: {failure}")
            return False
        self.accept_launch(key, lane, INCIDENT_MODEL, "xhigh", str(brief), True)
        self.record(log, f"{line} | case lane {lane} launching on incident xhigh, brief {brief}; root: ratify, and keep the reporter told that this case is still open")
        return True

    def offer_cases(self, key: str, first: str, parent: str, launch: actions.Action, cases: list[str], reason: str) -> None:
        """Put cases to the root as one `DECIDE` that carries, for each, the launch line that starts its lane from an attached brief."""
        if self.book.known(RUNNER, f"escalation:{key}"):
            return
        names = self.case_names(first)
        offers = []
        for case in cases:
            lane = next(names)
            brief, failure = self.case_brief(key, lane, parent, launch, case)
            offers.append(f"{case} | to launch its lane, post `orca-desk: launch {lane} NOW incident xhigh brief={brief}`" if brief else f"{case} | no launch line: {failure}")
        self.record(f"escalation:{key}", f"DECIDE {key} {parent}: {reason}: {'; '.join(offers)}")

    def opened_prs(self, lane: str, launch: actions.Action) -> str:
        """The PRs a lane's own cci `opened` records name since its launch, each once, as `#1 owner/repo#2`."""
        launched = actions.parse_stamp(launch.verification_receipt["at"])
        try:
            opened = self.lane_records(lane, "opened")
        except CciTailFailed as failure:
            self.tail_failed(failure)
            return "unknown, cci could not be read"
        prs = {pr_name(pr) for record in opened if actions.parse_stamp(record["at"]) >= launched for pr in record["refs"].get("prs", [])}
        return " ".join(sorted(prs)) or "none opened yet"

    def lane_records(self, lane: str, *kinds: str) -> list[dict]:
        """A lane's own cci records of these kinds on this drive, under either of its `lane_aliases`; `cci tail` also returns the caller's peer mail, which is dropped."""
        names = lane_aliases(lane)
        posted = cci.records(self.cci_tail, self.config.drive, *(arg for name in names for arg in ("--lane", name)), *(f"--kind={kind}" for kind in kinds))
        return [record for record in posted if record["drive"] == self.config.drive and record["lane"] in names and record["kind"] in kinds]

    def holds(self, slug: str) -> list[actions.Action]:
        return [action for action in self.book.actions(RUNNER, kind="hold", status="accepted") if json.loads(action.target)["slug"] == slug]

    def hold_line(self, at: str, key: str, rest: str) -> None:
        """Start the clock on an urgent hold; `aged_holds` turns one older than `deadlines.hold_minutes` into a DECIDE line for the root."""
        spec = HOLD_SPEC.match(rest)
        if not spec:
            self.record(f"escalation:hold:{at}", f"HOLD-FAILED {key} inbox: one hold per line, in the form `{HOLD_GRAMMAR}`; no clock started")
            return
        if self.holds(spec["slug"]):
            return
        target = json.dumps({"slug": spec["slug"], "owner": spec["owner"], "what": spec["what"]})
        self.book.accept(RUNNER, f"hold:{spec['slug']}@{self.book.stamp()}", "hold", target, key, self.now() + timedelta(minutes=self.config.hold_minutes))

    def unhold_line(self, at: str, key: str, rest: str) -> None:
        spec = UNHOLD_SPEC.match(rest)
        if not spec:
            self.record(f"escalation:unhold:{at}", f"HOLD-FAILED {key} inbox: lift a hold in the form `{UNHOLD_GRAMMAR}`")
            return
        for action in self.holds(spec["slug"]):
            self.book.edit(RUNNER, lambda incident, held=action.action_id: incident.verify(held, {"at": self.book.stamp(), "by": key}))

    def aged_holds(self) -> None:
        moment = self.now()
        for action in self.book.actions(RUNNER, kind="hold", status="accepted"):
            if not action.overdue(moment):
                continue
            held = json.loads(action.target)
            minutes = int((moment - actions.parse_stamp(action.accepted_at)).total_seconds() // 60)
            self.record(
                f"escalation:aged:{action.action_id}",
                f"DECIDE hold:{held['slug']} {held['owner']}: held {minutes} min: {held['what']} | the root decides it now with {held['owner']}, ahead of any open owner question on another subject",
            )

    def incident_lane(self, monitors: set[str], lane: str) -> str | None:
        """The lane already on one of these monitors: a launch in flight or the live dispatch of a launch whose brief's `ccx: incident=` names one, or a launch line later in the desk inbox naming one that this pass has yet to read."""
        for container in self.book.containers(LANE_PREFIX):
            other = container.removeprefix(LANE_PREFIX)
            launches = [action for action in self.book.actions(container, kind="launch") if brief_incidents(Path(json.loads(action.target)["brief"])) & monitors]
            if other == lane or not launches:
                continue
            dispatch = self.orca.show(other)
            live = dispatch.id if dispatch and dispatch.status not in INACTIVE else None
            if any(action.status in ("accepted", "started") or (live and action.dispatch_id == live) for action in launches):
                return other
        inbox = self.config.desk_inbox
        offset = self.book.load(RUNNER).facts[f"desk-inbox:{inbox}"]
        for line in inboxes.Inbox(inbox).lines(offset) if inbox.is_file() else []:
            directive = INBOX_DIRECTIVE.match(line.text.strip())
            if line.start <= offset or not directive or directive["verb"] != "launch" or not (spec := LAUNCH_SPEC.match(directive["rest"])) or spec["lane"] == lane:
                continue
            if brief_incidents(Path(spec["brief"])) & monitors:
                return spec["lane"]
        return None

    def launch_refusal(self, key: str, lane: str, model: str, effort: str, brief: Path, role: str = "") -> str:
        try:
            launch_model(model)
        except argparse.ArgumentTypeError as error:
            return str(error)
        if effort not in EFFORTS:
            return f"{effort} is not an effort: {', '.join(EFFORTS)}"
        if not brief.is_file():
            return f"brief {brief} is not a file"
        if not SLICE_HEADER.search(brief.read_text()) and not (model == INCIDENT_MODEL or incident_fix(lane, model, brief, role)):
            return f"brief {brief} has no `{SLICE_LINE} lane=<name> step=<n>` line; add one and run `rulings.py slice --refresh {brief}`"
        container = lane_container(lane)
        if container in self.book.store.ids():
            if held := self.book.load(container).actions.get(key):
                return f"{key} already holds a {held.kind} for {lane} ({held.status})"
            if flight := next((action for action in self.book.actions(container, kind="launch") if action.status in ("accepted", "started")), None):
                return f"launch {flight.action_id} for {lane} is {flight.status}"
        dispatch = self.orca.show(lane)
        if dispatch and dispatch.status not in INACTIVE:
            return f"dispatch {dispatch.id} is {dispatch.status}; relay to it instead"
        return ""

    def open_questions(self, dispatch: Dispatch) -> list[str]:
        """The questions the dispatch asked the Run that no message on the dispatch's terminal answers, newest first."""
        asked = (self.orca.call("orchestration", "check", "--terminal", f"run:{self.config.run}", "--all", "--types", "question").get("result") or {}).get("messages") or []
        mine = [message for message in asked if message.get("from_handle") in (f"dispatch:{dispatch.id}", dispatch.terminal)]
        if not mine:
            return []
        received = (self.orca.call("orchestration", "check", "--terminal", dispatch.terminal, "--all").get("result") or {}).get("messages") or []
        answered = {message.get("thread_id") for message in received}
        return [message["id"] for message in sorted(mine, key=lambda message: message["created_at"], reverse=True) if message["id"] not in answered]

    def unclaimed(self, key: str, lane: str, questions: list[str]) -> str:
        """The first of `questions` no other unfailed reply to the lane already answers, or empty: Orca refuses a second, different answer to one question."""
        claimed = {json.loads(reply.target)["reply_to"] for reply in self.book.actions(self.lane(lane), kind="reply") if reply.action_id != key and reply.status != "failed"}
        return next((question for question in questions if question not in claimed), "")

    def duplicate_holder(self, lane: str, brief: Path) -> str:
        """The live lane already holding the work a launch's brief names, with commits ahead or a pushed branch, as `<lane>: <lane status>`, or empty: a fix lane on the same incident, a lane sharing an incident the brief names, or one whose branch carries a PR the brief names."""
        keys = brief_incidents(brief) - {NAMES_NOTHING}
        text = brief.read_text(errors="replace") if brief.is_file() else ""
        prs = {number for found in PR_NAMED.finditer(text) for number in found.groups() if number}
        for container in self.book.containers(LANE_PREFIX):
            other = container.removeprefix(LANE_PREFIX)
            launched = self.book.actions(container, kind="launch")
            if other == lane or not launched:
                continue
            ours = keys | ({incident_name(lane)} if fix_launch(self.book.load(container)) else set())
            shared = ours & self.incident_keys(other, launched[-1])
            if not (shared or prs) or not ((dispatch := self.orca.show(other)) and dispatch.status not in INACTIVE):
                continue
            try:
                held = [tree for tree in self.lane_worktrees(other) if tree.ahead or tree.pushed not in UNPUSHED]
            except CciTailFailed as failure:
                self.tail_failed(failure)
                return ""
            if held and (shared or prs & {number for tree in held for number in re.findall(r"\d+", tree.pr)}):
                return f"{other}: {lanes.summary(other, held)}"
        return ""

    def rulings(self, verb: str, *argv: str) -> Done:
        return self.shell.run([sys.executable, str(SCRIPTS / "rulings.py"), verb, "--repo", self.config.briefs_repo, "--drive", self.config.drive, *argv])

    def refresh_slice(self, key: str, lane: str, brief: Path) -> None:
        """Write a fresh register slice under the brief's `Register slice:` line, adding `lane=<lane> step=incident` to a brief with none; a register or slice that does not read logs one `SLICE-UNREAD` and never holds the launch."""
        text = brief.read_text()
        if not SLICE_HEADER.search(text):
            brief.write_text(f"{text.rstrip()}\n\n{SLICE_LINE} lane={lane} step={INCIDENT_STEP}\n")
        done = self.rulings("slice", "--refresh", str(brief))
        if done.code:
            reason = ((done.err.strip() or done.out.strip()).splitlines() or ["no output"])[-1]
            self.escalate(f"slice:{key}", "SLICE-UNREAD", lane, f"`rulings.py slice --refresh` exited {done.code}: {reason}; {lane} launches on its brief without a fresh register slice")

    def sliced(self, key: str, lane: str, text: str) -> str:
        """`text` with the register slice written in, the way `refresh_slice` writes it into a brief file."""
        with tempfile.TemporaryDirectory() as staging:
            brief = Path(staging) / f"{lane}.md"
            brief.write_text(text)
            self.refresh_slice(key, lane, brief)
            return brief.read_text()

    def accept_launch(self, key: str, lane: str, model: str, effort: str, brief: str, owner_directed: bool, fast: bool = False, role: str = "", incident: str = "") -> tuple[actions.Action, bool]:
        urgent = owner_directed or model == INCIDENT_MODEL
        target = json.dumps({"model": model, "effort": effort, "brief": brief, "prior": self.orca.receipt(lane), "urgent": urgent, "fast": fast, "role": role, "incident": incident})
        return self.book.accept(self.lane(lane), key, "launch", target, key, self.now() + timedelta(minutes=self.config.launch_minutes))

    def landing_policy(self) -> actions.Action | None:
        self.book.ensure(LANDING, LANDING)
        verified = self.book.actions(LANDING, kind="policy", status="verified")
        superseded = {json.loads(action.target)["supersedes"] for action in verified}
        return next((action for action in verified if json.loads(action.target)["revision"] not in superseded), None)

    def accept_policy(self, key: str, rule: str, revision: str, source: str, supersedes: str) -> tuple[actions.Action, bool]:
        current = self.landing_policy()
        target = json.dumps({"rule": rule, "revision": revision, "supersedes": supersedes})
        action, created = self.book.accept(LANDING, key, "policy", target, source, None)
        if not created:
            return action, created
        if current and supersedes != json.loads(current.target)["revision"]:
            held = json.loads(current.target)
            reason = f"names {supersedes or 'no'} predecessor; the accepted landing policy is {held['rule']} at {held['revision']} ({current.authority_ref})"
            self.book.attempt(LANDING, lambda incident: incident.fail(key, reason))
            self.escalate(key, "STALE-POLICY", "landing", f"{rule} at {revision} from {source} rejected: {reason}")
        else:
            self.book.attempt(LANDING, lambda incident: incident.verify(key, {"at": self.book.stamp(), "supersedes": supersedes}))
        return self.book.load(LANDING).actions[key], created

    def transfer(self, lane: str) -> None:
        """Offer the container to the lane's receipt dispatch when it moved; ownership changes only on that dispatch's ack."""
        container = self.lane(lane)
        incident = self.book.load(container)
        dispatch = self.orca.receipt(lane)
        if dispatch and dispatch not in (incident.owner, incident.pending_owner):
            try:
                self.book.store.transfer(container, incident.owner_generation, dispatch)
            except actions.StaleGeneration:
                return

    def deliver(self) -> None:
        self.pass_binding = None
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            self.transfer(lane)
            pending = self.book.actions(container, status="accepted")
            relays = [action for action in pending if action.kind in ("relay", "reply")]
            for action in pending:
                if action.kind == "launch" and not self.holds_case_launch(container, lane, action) and not self.holds_owned_launch(container, lane, action) and not self.holds_cut_launch(container, lane, action):
                    self.launch(container, lane, action)
            if relays:
                self.deliver_relays(container, lane, relays)
            self.reconcile_sends(container, lane)

    def holds_case_launch(self, container: str, lane: str, action: actions.Action) -> bool:
        """Fail a pending case lane launch once its incident has ended, and say whether the launch must wait: failed, or left for the next pass when cci cannot be read."""
        if not action.action_id.startswith(UNFIXED_KEY):
            return False
        try:
            ended = self.incident_end(first_lane(lane), lane)
        except CciTailFailed as failure:
            self.tail_failed(failure)
            return True
        if ended:
            self.book.edit(container, lambda incident: incident.fail(action.action_id, f"the incident ended with {ended}"))
            self.record(f"escalation:{container}/{action.action_id}:ended", f"WITHDRAWN {action.action_id} {lane}: the incident ended with {ended}; nothing was launched")
        return bool(ended)

    def holds_owned_launch(self, container: str, lane: str, action: actions.Action) -> bool:
        """Fail a pending incident launch once another owner holds its incident, and say whether the launch must wait: failed, or left for the next pass when cci cannot be read."""
        slug = json.loads(action.target).get("incident")
        if not slug:
            return False
        try:
            owner = self.incident_owner(slug, lane)
        except CciTailFailed as failure:
            self.tail_failed(failure)
            return True
        if owner:
            self.book.edit(container, lambda incident: incident.fail(action.action_id, f"incident {slug} owned by {owner}"))
            self.escalate(action.action_id, "INCIDENT-OWNED", lane, f"incident {slug} owned by {owner}; no launch")
        return bool(owner)

    def holds_cut_launch(self, container: str, lane: str, action: actions.Action) -> bool:
        """Hold a manual release launch until `CUT_GRACE` after its CUT-SILENT posts, and fail it once the root resolves that CUT-SILENT or the order is answered or closed, and say whether the launch must wait."""
        if not action.action_id.startswith(CUT_LAUNCH):
            return False
        key = action.action_id.removeprefix(CUT_LAUNCH)
        runner = self.book.load(RUNNER)
        silent = runner.actions[f"escalation:cut:{key}"]
        if silent.status == "accepted":
            return True
        try:
            withdrawn = self.cut_withdrawn(key, runner.facts[CUT_CLOCKS][key], silent)
        except CciTailFailed as failure:
            self.tail_failed(failure)
            return True
        if withdrawn:
            self.book.edit(container, lambda incident: incident.fail(action.action_id, withdrawn))
            self.book.edit(RUNNER, lambda incident: incident.facts[CUT_CLOCKS][key].update({"ended": f"withdrawn: {withdrawn}"}))
            self.record(f"escalation:{container}/{action.action_id}:withdrawn", f"WITHDRAWN {action.action_id} {lane}: {withdrawn}; nothing was launched")
            return True
        return self.now() - actions.parse_stamp(silent.response["at"]) < CUT_GRACE

    def cut_withdrawn(self, key: str, clock: dict, silent: actions.Action) -> str:
        """Why a silent cut's manual release must not launch: the root resolved its CUT-SILENT, or the order was answered or closed since; empty while none did."""
        if posted := POSTED.search(silent.response.get("posted", "")):
            seq = int(posted[1])
            replies = cci.records(self.cci_tail, self.config.drive, "--lane", ROOT, since=seq)
            if resolved := next((record for record in replies if (record["lane"], record["drive"]) == (ROOT, self.config.drive) and record.get("resolves") == seq), None):
                return f"#{resolved['seq']} resolves CUT-SILENT #{seq}"
        return self.cut_closed(key, clock) or self.cut_answer(clock)

    def deliver_relays(self, container: str, lane: str, relays: list[actions.Action]) -> None:
        """Send each accepted relay to the lane's live dispatch; with none and no launch in flight to bring one, fail each relay no send delivered as RELAY-FAILED now."""
        dispatch = self.orca.show(lane)
        if dispatch and dispatch.status not in INACTIVE:
            for action in relays:
                self.send(container, dispatch, action)
            return
        if any(action.status in ("accepted", "started") for action in self.book.actions(container, kind="launch")):
            return
        reason = f"no live dispatch{f' ({dispatch.id} is {dispatch.status})' if dispatch else ''}"
        for action in relays:
            if self.delivered(container, action):
                continue
            if self.book.attempt(container, lambda incident, key=action.action_id: incident.fail(key, reason)):
                self.record(f"escalation:relay:{container}/{action.action_id}", f"RELAY-FAILED {action.action_id} {lane}: {reason}; nothing was relayed")

    def delivered(self, container: str, action: actions.Action) -> bool:
        return any(send.authority_ref == action.action_id and send.status == "completed" for send in self.book.actions(container, kind="send"))

    def sends(self, container: str, key: str, dispatch: str) -> list[actions.Action]:
        prefix = f"send:{key}:{dispatch}"
        return [action for action in self.book.actions(container, kind="send") if action.action_id.rsplit("#", 1)[0] == prefix]

    def send(self, container: str, dispatch: Dispatch, action: actions.Action) -> None:
        tries = self.sends(container, action.action_id, dispatch.id)
        if any(attempt.status != "failed" for attempt in tries):
            return
        if len(tries) >= SEND_ATTEMPTS or resends(tries):
            self.escalate(f"{container}/send:{action.action_id}:{dispatch.id}", "SEND-FAILED", dispatch.lane, f"{action.authority_ref} to {dispatch.id} failed {len(tries)} times: {tries[-1].reason}")
            return
        send_id = f"send:{action.action_id}:{dispatch.id}#{len(tries) + 1}"
        self.book.accept(container, send_id, "send", dispatch.id, action.action_id, None)
        if not self.book.attempt(container, lambda incident: incident.start(send_id, self.now(), dispatch_id=dispatch.id)):
            return
        self.post(container, dispatch, action, send_id, "")

    def post(self, container: str, dispatch: Dispatch, action: actions.Action, send_id: str, retry: str) -> None:
        spec = json.loads(action.target)
        thread = thread_of(container, action.action_id)
        answers = action.kind == "reply" and not self.answered_elsewhere(container, action, dispatch)
        if answers:
            argv = ["orchestration", "reply", "--id", spec["reply_to"], "--body", f"{spec['text']}\n\n({action.authority_ref})"]
        elif action.kind == "reply":
            argv = [
                "orchestration", "send", "--to", f"dispatch:{dispatch.id}", "--type", "dispatch", "--subject", f"{action.authority_ref}: on answered question {spec['reply_to']}",
                "--body", f"{spec['text']}\n\n({action.authority_ref})", "--thread-id", thread, "--payload", json.dumps({"action": thread}),
            ]
        else:
            body = f"{spec['text']}\n\nReply on thread {thread}: subject `started {action.action_id}` before acting, then `done {action.action_id}: <result>`."
            argv = [
                "orchestration", "send", "--to", f"dispatch:{dispatch.id}", "--type", "dispatch", "--subject", f"{action.authority_ref}: act {action.action_id}",
                "--body", body, "--thread-id", thread, "--payload", json.dumps({"action": thread}),
            ]
        reply = self.orca.call(*argv, *(["--retry-request", retry] if retry else []))
        if reply.get("ok"):
            message = ((reply.get("result") or {}).get("message") or {}).get("id", "sent")
            self.book.attempt(container, lambda incident: incident.complete(send_id, {"message": message, "at": self.book.stamp()}))
            if action.kind == "reply":
                self.book.attempt(container, lambda incident: (incident.start(action.action_id, self.now()), incident.complete(action.action_id, {"message": message, "at": self.book.stamp()})))
            if not answers:
                self.orca.wake(dispatch.terminal, f"{action.authority_ref}: read Orca message on thread {thread} now")
        elif answers and reply["error"]["code"] == ANSWER_CONFLICT:
            self.book.attempt(container, lambda incident: incident.fail(send_id, f"{ANSWER_CONFLICT}: {reply['error'].get('message', '')}"[:300]))
            self.send(container, dispatch, action)
        elif reply["error"]["code"] in UNREACHABLE:
            self.book.attempt(container, lambda incident: lost(incident, send_id, {"request_id": request_id(reply)}))
        else:
            self.book.attempt(container, lambda incident: incident.fail(send_id, json.dumps(reply.get("error"))[:300]))

    def answered_elsewhere(self, container: str, action: actions.Action, dispatch: Dispatch) -> bool:
        """Whether Orca refused a reply of `action` because its question already holds another answer, so the text goes to the dispatch as a message instead."""
        return any(send.status == "failed" and (send.reason or "").startswith(ANSWER_CONFLICT) for send in self.sends(container, action.action_id, dispatch.id))

    def reconcile_sends(self, container: str, lane: str) -> None:
        """Settle every send whose response was lost from Orca's request receipt or the recipient's mailbox; while both reads succeed and show neither, resend it on the same thread once per `RESEND_AFTER`, and report `RESEND_LIMIT` resends all lost the same way as UNACKED."""
        cutoff = self.now() - ORPHANED_SEND
        for send in self.book.actions(container, kind="send", status="started"):
            if actions.parse_stamp(send.started_at) < cutoff:
                self.book.attempt(container, lambda incident, key=send.action_id: incident.lose(key))
        for send in self.book.actions(container, kind="send", status="unverifiable"):
            action = self.book.load(container).actions[send.authority_ref]
            request = (send.response or {}).get("request_id", "")
            state = self.orca.request_state(request) if request else "absent"
            if state in ("pending", UNREAD):
                continue
            dispatch = self.orca.show(lane)
            current = dispatch if dispatch and dispatch.id == send.target else None
            if state == "completed" and current:
                self.post(container, current, action, send.action_id, request)
                continue
            mailbox = self.orca.mailbox(current.terminal) if current else []
            if mailbox is None:
                continue
            found = next((message for message in mailbox if delivers(message, container, action)), None)
            if found:
                receipt = {"message": found["id"], "at": found.get("created_at", self.book.stamp()), "reconciled": True}
                self.book.attempt(container, lambda incident, key=send.action_id, receipt=receipt: incident.complete(key, receipt))
                if action.kind == "reply":
                    self.book.attempt(container, lambda incident, key=action.action_id, receipt=receipt: (incident.start(key, self.now()), incident.complete(key, receipt)))
            elif not current or current.status in INACTIVE:
                self.escalate(f"{container}/{send.action_id}", "UNVERIFIABLE", lane, f"{action.authority_ref} send to {send.target} lost its response; Orca holds no receipt and no message on its thread; it was not resent")
            elif self.now() - actions.parse_stamp(send.started_at) >= RESEND_AFTER:
                sent_again = len(resends(self.sends(container, action.action_id, current.id)))
                if sent_again >= RESEND_LIMIT:
                    self.report_unacked(container, lane, action, send, sent_again)
                else:
                    self.resend(container, current, action, send, sent_again + 1)

    def report_unacked(self, container: str, lane: str, action: actions.Action, send: actions.Action, sent_again: int) -> None:
        log = f"escalation:unacked:{container}/{send.action_id}"
        if self.book.known(RUNNER, log):
            return
        self.keep({"id": action.action_id, "body": json.loads(action.target)["text"]})
        self.record(log, f"UNACKED {action.action_id} {lane}: the send to {send.target} and {sent_again} resends lost their response, and the lane has not named {action.action_id} on its thread or the Run; Orca holds no receipt and no message on its thread; nothing more is sent; check the lane's Run messages before delivering the relay text at this record's path by hand")

    def resend(self, container: str, dispatch: Dispatch, action: actions.Action, send: actions.Action, number: int) -> None:
        send_id = f"{send.action_id.split(RESEND)[0]}{RESEND}{number}"

        def retire_and_start(incident: actions.Incident) -> None:
            incident.fail(send.action_id, f"response lost with no receipt and no message on its thread; resent as {send_id}")
            incident.accept(send_id, "send", dispatch.id, action.action_id, self.now())
            incident.start(send_id, self.now(), dispatch_id=dispatch.id)

        if self.book.attempt(container, retire_and_start):
            self.post(container, dispatch, action, send_id, "")

    def launch_log(self, container: str, key: str) -> Path:
        return (self.config.store or actions.incidents_dir()).parent / "desk-runner-launches" / f"{container}-{key}.out"

    def sprite_log(self, container: str, key: str) -> Path:
        return self.launch_log(container, key).with_suffix(".sprite.out")

    def launch(self, container: str, lane: str, action: actions.Action) -> None:
        """Start orca-launch.sh, or worker-launch.sh for a Sprite, detached, so a readiness wait never holds a relay; `reap` records its printed line."""
        spec = json.loads(action.target)
        if not self.bound_for_launch():
            self.refuse_launch(container, lane, action, f"an unbound runner: {self.pass_binding['why']}", f"the root launches it by hand with orca-launch.sh, or runs `{self.rebind_command()}` from its own terminal and submits it again under a new key")
            return
        refusal = self.sprite_refusal(lane, spec)
        if refusal is not None and (held := self.load_refusal()):
            self.move_load(held)
            if spec["urgent"]:
                self.refuse_launch(container, lane, action, held, "the root launches it in-process or once the load is moved")
            elif self.now() - actions.parse_stamp(action.accepted_at) >= timedelta(minutes=self.config.load_hold_minutes):
                self.expire_launch(container, lane, action, held)
            return
        if not self.book.attempt(container, lambda incident: incident.start(action.action_id, self.now(), deadline=actions.parse_stamp(action.deadline))):
            return
        if refusal is None:
            self.launch_sprite(container, lane, action)
            return
        if refusal:
            self.escalate(f"{container}/{action.action_id}:sprite", "SPRITE-FALLBACK", lane, f"{action.action_id} launches locally: {refusal}")
        self.launch_local(container, lane, action)

    def launch_local(self, container: str, lane: str, action: actions.Action) -> None:
        spec = json.loads(action.target)
        argv = [str(LAUNCHER), lane, spec["model"], spec["effort"], spec["brief"]]
        env = {**self.config.launch_env, **self.orca.sender, "ORCA_LAUNCH_STATE": str(self.config.receipts)}
        if spec.get("fast"):
            env[CLAUDE_ARGS] = " ".join(filter(None, [env.get(CLAUDE_ARGS, self.shell.env(CLAUDE_ARGS)), CLAUDE_FAST_MODE]))
        self.launching[f"{container}/{action.action_id}"] = self.shell.spawn(argv, self.launch_log(container, action.action_id), env)

    def launch_sprite(self, container: str, lane: str, action: actions.Action) -> None:
        spec = json.loads(action.target)
        sprite = self.config.sprite or {}
        argv = [sprite["launcher"], lane, sprite_model(spec["model"]), spec["effort"], spec["brief"]]
        env = {**self.config.launch_env, **self.orca.sender, "WORKER_RUN": self.config.run, **sprite.get("env", {})}
        key = f"{container}/{action.action_id}"
        self.launching[key] = self.shell.spawn(argv, self.sprite_log(container, action.action_id), env)
        self.sprites.add(key)

    def sprite_refusal(self, lane: str, spec: dict) -> str | None:
        """None when the launch goes to a Sprite; why it stays local otherwise, empty when it was never a Sprite candidate."""
        if not self.config.sprite or spec["model"] in SPRITE_LOCAL_MODELS or spec.get("fast") or not sprite_placed(Path(spec["brief"])):
            return ""
        if not SPRITE_LANE.fullmatch(lane):
            return f"lane {lane} is not a Sprite name of up to 55 lowercase letters, digits, and dashes"
        return None

    def load_refusal(self) -> str:
        """Why no launch may start on the Mac now, or empty while the 1-minute load is at or below the core count."""
        load, cores = self.shell.load(), self.shell.cores()
        if load <= cores:
            return ""
        return f"load {load:.0f} above {cores} cores"

    def move_load(self, held: str) -> None:
        """Tell the root, once per ten minutes, that launches are refused on load and that the load has to move."""
        self.escalate(f"load:{actions.stamp(self.now())[:15]}", "LOAD-HIGH", "runner", f"{held}: Orca launches are refused while it lasts, because a worktree checkout times out under it. root: move the load off this Mac now; a pass, backfill, dry run, or bulk rewrite runs on in-region compute")

    def refuse_launch(self, container: str, lane: str, action: actions.Action, held: str, remedy: str) -> None:
        """Fail a launch at once that cannot wait: an incident or owner-directed one on load, or any on an unbound runner."""
        key = action.action_id
        text = f"{key} refused on {held}; nothing was launched, and {remedy}"
        if self.book.attempt(container, lambda incident: incident.fail(key, text)):
            self.escalate(f"{container}/{key}", "LAUNCH-FAILED", lane, text)

    def stalled_launches(self) -> None:
        """Log one `LAUNCH-STALLED` for each launch still accepted with no dispatch `LAUNCH_STALL` after its `accepted_at`, naming the withdraw and the hand launch; no incident clock defers it."""
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            for action in self.book.actions(container, kind="launch", status="accepted"):
                if self.now() - (since := actions.parse_stamp(action.accepted_at)) < LAUNCH_STALL:
                    continue
                spec = json.loads(action.target)
                self.escalate(
                    f"stalled:{container}/{action.action_id}",
                    "LAUNCH-STALLED",
                    lane,
                    f"{action.action_id} accepted at {pacific(since)} has no dispatch {int((self.now() - since).total_seconds())} s on, with the 1-minute load {self.shell.load():.0f} of {self.shell.cores()} cores;"
                    f" withdraw it with `orca-desk: withdraw {action.action_id}`, then launch it by hand: `orca-launch.sh {lane} {spec['model']} {spec['effort']} {spec['brief']}`",
                )

    def expire_launch(self, container: str, lane: str, action: actions.Action, held: str) -> None:
        key = action.action_id
        text = f"{key} launch failed after {self.config.load_hold_minutes}m held, {held}; move the load off this Mac, then submit it under a new key"
        if not self.book.attempt(container, lambda incident: incident.fail(key, text)):
            return
        self.escalate(f"{container}/{key}", "LAUNCH-HELD", lane, text)
        self.orca.call("orchestration", "send", "--to", f"run:{self.config.run}", "--run", self.config.run, "--type", "status", "--subject", f"LAUNCH-HELD {lane}", "--body", text)

    def reap(self) -> None:
        """Settle each started launch from its printed line; a launch with neither a line nor a new receipt is unverifiable, never relaunched."""
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            for action in self.book.actions(container, kind="launch", status="started"):
                process = self.launching.get(f"{container}/{action.action_id}")
                if process and process.poll() is None:
                    continue
                self.launching.pop(f"{container}/{action.action_id}", None)
                if f"{container}/{action.action_id}" in self.sprites or (self.sprite_log(container, action.action_id).is_file() and not self.launch_log(container, action.action_id).is_file()):
                    self.sprites.discard(f"{container}/{action.action_id}")
                    self.settle_sprite(container, lane, action, exited=process is not None)
                    continue
                self.settle_launch(container, lane, action)

    def settle_sprite(self, container: str, lane: str, action: actions.Action, exited: bool) -> None:
        """Copy a ready Sprite launch's receipts beside the local ones and settle it; start the same launch locally only when its receipts prove worker-start never ran."""
        key = action.action_id
        out = self.sprite_log(container, key).read_text().strip()
        if not out:
            self.settle_launch(container, lane, action)
            return
        attempt = sprite_attempt(Path(json.loads(action.target)["brief"]))
        try:
            ready = json.loads(out.splitlines()[-1])
            line = f"{lane} ready task={ready['task']} dispatch={ready['dispatch']} terminal={ready['terminal']} worktree={ready['worktree']}"
            receipt = (attempt / SPRITE_RECEIPT).read_text()
        except (json.JSONDecodeError, KeyError, TypeError, FileNotFoundError):
            reason = " ".join(out.split())[:300]
            if sprite_never_started(attempt, exited):
                self.escalate(f"{container}/{key}:sprite", "SPRITE-FALLBACK", lane, f"{key} launches locally: {reason}")
                self.launch_local(container, lane, action)
                return
            self.book.attempt(container, lambda incident: incident.lose(key))
            started = sprite_started_as(attempt / SPRITE_RECEIPT)
            self.escalate(
                f"{container}/{key}:sprite",
                "SPRITE-UNVERIFIABLE",
                lane,
                f"{key} does not launch locally: its Sprite launch is unverified and a remote worker may be running{started}; receipts in {attempt}, nothing retried or cleaned up: {reason}",
            )
            return
        self.config.receipts.mkdir(parents=True, exist_ok=True)
        (self.config.receipts / f"{lane}.json").write_text(receipt)
        (self.config.receipts / f"{lane}.terminal").write_text(f"{ready['terminal']}\n")
        self.launch_log(container, key).write_text(f"{line}\n")
        self.settle_launch(container, lane, action)

    def settle_launch(self, container: str, lane: str, action: actions.Action) -> None:
        log = self.launch_log(container, action.action_id)
        out = log.read_text().strip() if log.is_file() else ""
        launched = LAUNCHED.search(out)
        prior = json.loads(action.target)["prior"]
        receipt = self.orca.receipt(lane)
        key = action.action_id
        if launched and launched["lane"] == lane:
            proof = {"line": launched.group(0), "dispatch": launched["dispatch"], "at": self.book.stamp()}
        elif not out and receipt and receipt != prior:
            proof = {"line": f"receipt dispatch {receipt}", "dispatch": receipt, "at": self.book.stamp()}
        elif out:
            reason = next((line for line in reversed(out.splitlines()) if line.startswith(f"{lane} failed ")), " ".join(out.split())[:300])
            self.book.attempt(container, lambda incident: incident.fail(key, reason))
            self.escalate(f"{container}/{key}", "LAUNCH-FAILED", lane, reason)
            return
        else:
            self.book.attempt(container, lambda incident: incident.lose(key))
            self.escalate(f"{container}/{key}", "UNVERIFIABLE", lane, "launch outcome unknown: no launch line and no new receipt; it was not relaunched")
            return
        self.book.attempt(container, lambda incident: (incident.complete(key, proof, dispatch_id=proof["dispatch"]), incident.verify(key, proof)))
        self.record(f"escalation:{container}/{key}:launched", f"LAUNCHED {key} {lane}: dispatch {proof['dispatch']} terminal {launched['terminal'] if launched else self.orca.terminal(lane)}")
        self.transfer(lane)
        if launched and launched["how"] == "unsupervised":
            self.escalate(f"{container}/{key}", "UNSUPERVISED", lane, f"launched without Orca supervision: {proof['line']}")

    def check(self) -> bool:
        """Read the Run past the persisted cursor, relay any message of the Delivery Orca handed over before that read that holds no relay record, then acknowledge the Delivery; False while no cursor exists, so nothing dispatches before the first read sets one."""
        key, relayed = f"inbox:{self.config.run}", f"relayed:{self.config.run}"
        facts = self.book.load(RUNNER).facts
        cursor = facts.get(key)
        delivery = self.orca.call("orchestration", "check", "--run", self.config.run).get("result") or {}
        unrelayed = {message["id"] for message in delivery.get("messages") or [] if message["type"] != "heartbeat"} - set(facts.get(relayed, []))
        limit = INBOX_PAGE_SIZE
        while True:
            reply = self.orca.ask("orchestration", "inbox", "--terminal", f"run:{self.config.run}", "--limit", str(limit))
            if not reply["ok"]:
                self.escalate(f"orca-inbox:{actions.stamp(self.now())[:15]}", "ORCA-INBOX", "runner", json.dumps(reply["error"]))
                return cursor is not None
            messages = reply["result"]["messages"]
            if cursor is None:
                newest = max((message["sequence"] for message in messages), default=0)
                self.book.edit(RUNNER, lambda incident: incident.facts.update({key: newest}))
                return True
            if len(messages) < limit or (messages[-1]["sequence"] <= cursor + 1 and unrelayed <= {message["id"] for message in messages}):
                break
            limit *= 2
        for message in reversed(messages):
            behind = message["sequence"] <= cursor
            if behind and message["id"] not in unrelayed:
                continue
            cursor = max(cursor, message["sequence"])
            if message["type"] == "heartbeat":
                self.book.edit(RUNNER, lambda incident: incident.facts.update({key: cursor}))
                continue
            lane = self.orca.lane_of(message["from_handle"])
            if not self.message(message | {"lane": lane}) and behind:
                self.keep(message)
                self.escalate(message["id"], "UNRELAYED", lane, f"{message['type']} from {message['from_handle']} at sequence {message['sequence']}, behind cursor {cursor}, has no relay record and its type has no relay: {message.get('subject') or ''}")
            self.book.edit(RUNNER, lambda incident: (incident.facts.update({key: cursor}), incident.facts.setdefault(relayed, []).append(message["id"])))
            unrelayed.discard(message["id"])
        if delivery.get("deliveryId") and not unrelayed:
            self.orca.call("orchestration", "check", "--run", self.config.run, "--ack", delivery["deliveryId"], "--peek")
        return True

    def message_file(self, msg: str) -> Path:
        return self.config.receipts / "cci" / "messages" / f"{msg}.md"

    def keep(self, message: dict) -> None:
        """Write the whole Orca message where `flush` finds it, so the cci record relaying it carries every word by path."""
        path = self.message_file(message["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{message.get('subject') or ''}\n\n{message.get('body') or ''}".strip() + "\n")

    def message(self, message: dict) -> bool:
        """Relay one Run message by its type; False for a type the runner relays nowhere."""
        decoded = json.loads(message.get("payload") or "{}")
        payload = decoded if isinstance(decoded, dict) else {}
        lane = message.get("lane") or self.orca.lane_of(message.get("from_handle", ""))
        sender = payload.get("dispatchId") or (self.orca.receipt(lane) if self.orca.terminal(lane) == message.get("from_handle") else "")
        subject = message.get("subject") or ""
        acked = ACK.match(subject)
        thread = message.get("thread_id") or ""
        if acked and "/" in thread:
            container, key = thread.split("/", 1)
            result = acked["rest"].removeprefix(key).lstrip(": ").strip()
            self.acknowledge(container, key, sender, acked["verb"], result or (message.get("body") or "").strip(), message["id"])
        elif message["type"] in ("question", "escalation"):
            self.keep(message)
            if not self.withdrawn_ask(lane, message):
                self.escalate(message["id"], "DECIDE", lane, f"{message.get('subject', '')}: {(message.get('body') or '')[:300]}")
        elif message["type"] == "worker_done":
            self.keep(message)
            self.escalate(message["id"], "OUTCOME", lane, f"worker_done {payload.get('outcome', '?')} dispatch={sender}: {message.get('subject', '')}")
        elif message["type"] == "status" and subject.lower().startswith(MILESTONE_SUBJECTS):
            kind = subject.split(":", 1)[0].upper()
            self.keep(message)
            body = " ".join((message.get("body") or "").split())[:300]
            self.escalate(message["id"], kind, lane, f"{subject}: {body}")
            self.stamp_status(lane, message["id"], kind.lower())
            if kind != "MECHANISM":
                self.unfixed(lane, message)
        elif message["type"] == "status" and (key := self.relay_named(lane, f"{subject}\n{message.get('body') or ''}")):
            self.keep(message)
            verb, result = (acked["verb"], acked["rest"].strip()) if acked else ("done", subject)
            self.acknowledge_on_run(lane_container(lane), key, sender, verb, result or (message.get("body") or "").strip(), message["id"])
        elif message["type"] in ("decision_gate", "handoff"):
            self.keep(message)
            self.escalate(message["id"], message["type"].upper(), lane, f"{message.get('subject', '')}: {(message.get('body') or '')[:200]}")
        else:
            return False
        return True

    def relay_named(self, lane: str, text: str) -> str | None:
        container = lane_container(lane)
        if container not in self.book.store.ids():
            return None
        named = set(RUN_REF.findall(text))
        return next(
            (
                action.action_id
                for action in self.book.actions(container)
                if action.kind in ("relay", "reply") and action.status in ("accepted", "started") and (ref := RELAY_REF.search(action.action_id)) and ref[1] in named
            ),
            None,
        )

    def acknowledge_on_run(self, container: str, key: str, sender: str, verb: str, text: str, message: str) -> None:
        receipt = {"message": message, "at": self.book.stamp(), "acknowledged": True}
        for send in self.book.actions(container, kind="send"):
            if send.authority_ref == key and send.status in ("started", "unverifiable"):
                self.book.attempt(container, lambda incident, settled=send.action_id: incident.complete(settled, receipt))
        self.acknowledge(container, key, sender, verb, text, message)

    def withdrawn_ask(self, lane: str, message: dict) -> bool:
        """Take a lane's question as moot when the root withdrew its dispatch, or fenced a case lane's dispatch by hand: the lane is marked withdrawn, never offered to be dispatched again, and the root gets one line in place of a `DECIDE`."""
        by = self.withdrawn_by(lane)
        if not by and is_case_lane(lane) and (dispatch := self.orca.show(lane)) and dispatch.fenced:
            by = f"a stop of dispatch {dispatch.id}"
            self.book.edit(self.lane(lane), lambda incident: incident.facts.update({WITHDRAWN: {"dispatch": dispatch.id, "by": by}}))
        if not by:
            return False
        self.escalate(message["id"], "WITHDRAWN", lane, f"withdrawn by {by}, so it is not dispatched again and its question needs no answer: {message.get('subject', '')}")
        return True

    def stamp_status(self, lane: str, message: str, name: str) -> None:
        """Keep when a lane's milestone status arrived on its container, where the incident clock reads it beside the lane's cci records."""
        container = lane_container(lane)
        if container in self.book.store.ids():
            self.book.edit(container, lambda incident: incident.facts.setdefault(MILESTONE_STATUSES, {}).setdefault(message, {"name": name, "at": self.book.stamp()}))

    def acknowledge(self, container: str, key: str, sender: str, verb: str, text: str, message: str) -> None:
        """Apply a lane's started/done reply only from the dispatch that owns the container; a pending dispatch's first ack takes ownership first."""
        if container not in self.book.store.ids() or key not in self.book.load(container).actions:
            return
        incident = self.book.load(container)
        if sender and sender == incident.pending_owner:
            self.book.store.ack(container, sender)
        moved = verb == "started" and self.book.as_owner(container, sender, lambda live: live.start(key, self.now(), dispatch_id=sender))
        if verb == "done":
            response = {"text": text[:500], "message": message, "at": self.book.stamp()}
            moved = self.book.as_owner(container, sender, lambda live: finish(live, key, response, sender))
        if not moved and sender and sender != self.book.load(container).owner:
            self.stand_down(container, key, sender, message)

    def stand_down(self, container: str, key: str, sender: str, message: str) -> None:
        incident = self.book.load(container)
        notice = f"stand-down:{key}:{sender}"
        text = f"Do not execute {key}: dispatch {incident.owner} owns {container} at generation {incident.owner_generation}."
        action, created = self.book.accept(container, notice, "reply", json.dumps({"text": text, "reply_to": message}), notice, None)
        dispatch = self.orca.show(container.removeprefix(LANE_PREFIX))
        if created and dispatch:
            self.send(container, Dispatch(dispatch.lane, sender, dispatch.terminal, dispatch.status, dispatch.live), action)

    def brief_for(self, lane: str) -> Path | None:
        container = lane_container(lane)
        launched = [json.loads(action.target)["brief"] for action in self.book.actions(container, kind="launch", status="verified")] if container in self.book.store.ids() else []
        candidates = [Path(path) for path in launched[-1:]] + [self.attachment(f"{lane}.full.md"), self.attachment(f"{lane}.md")]
        return next((path for path in candidates if path and path.is_file()), None)

    def briefs(self, *argv: str) -> Done:
        return self.shell.run(["ccn", "-R", self.config.briefs_repo, *argv])

    def resolve_briefs_log(self) -> str:
        """Keep `orca.briefs.log` on a log that resolves: the configured one while `ccn log show` finds it, else the drive's `briefs: <drive>` log, created when the clone has none, with the repoint reported to root; why it could not, or ""."""
        stale = self.config.briefs_log
        if stale and self.briefs("log", "show", stale).code == 0:
            return ""
        title = f"briefs: {self.config.drive}"
        listed = self.briefs("log", "list", "--json")
        if listed.code != 0:
            return ccn_failure(listed)
        found = [log["id"] for log in json.loads(listed.out) if log["title"] == title]
        if not found:
            added = self.briefs("log", "add", title, "--label", f"program:{self.config.drive}", "--json")
            if added.code != 0:
                return ccn_failure(added)
            found = [json.loads(added.out)["id"]]
        self.config.briefs_log = found[0]
        self.escalate(f"briefs-log:{stale or 'unset'}", "BRIEFS-LOG", "runner", f"briefs log {stale or '(unset)'} does not resolve on {self.config.briefs_repo}; briefs now attach to {found[0]} ({title}), so set orca.briefs.log to it")
        return ""

    def attachment(self, name: str) -> Path | None:
        done = self.briefs("attachment", "path", self.config.briefs_log, name)
        return Path(done.out.strip()) if done.code == 0 else None

    def attach(self, name: str, text: str, entry: str) -> str:
        with tempfile.TemporaryDirectory() as staging:
            path = Path(staging) / name
            path.write_text(text)
            done = self.briefs("log", "append", self.config.briefs_log, "--entry", entry, "--attach", str(path), "--replace")
        return "" if done.code == 0 else ccn_failure(done)

    def idle(self, seconds: float) -> None:
        """Wait out the gap between passes, cut short by a desk inbox append or a launch process exiting."""
        inbox, running = self.shell.stat(self.config.desk_inbox), self.running()
        for _ in range(int(seconds / WAKE_SECONDS)):
            if self.shell.stat(self.config.desk_inbox) != inbox or self.running() < running or self.stop_requested():
                return
            self.shell.sleep(WAKE_SECONDS)

    def running(self) -> int:
        return sum(process.poll() is None for process in self.launching.values())

    def rotate_inboxes(self) -> None:
        for path in sorted(self.config.desk_inbox.parent.glob("*.md")):
            inboxes.Inbox(path).rotate(self.now().timestamp())

    def sweep(self) -> None:
        """Unread mail on a live dispatch gets one wake, typed only at its idle prompt; mail a settled dispatch never read, a dispatch that is not live, or a prompt watch that stopped polling escalates once."""
        done = self.shell.run([str(SCRIPTS / "orca-check.sh"), "--stale"], env={"ORCA_CHECK_STATE": str(self.config.receipts), "ORCA_LAUNCH_RUN": self.config.run})
        for line in done.out.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0] != "STALE":
                continue
            lane, status, item = parts[1], parts[3], parts[4]
            if status != "unread":
                self.escalate(f"stale:{item}", "STALE-MAIL", lane, f"{item} unread by a {status} dispatch")
                continue
            if (
                not self.book.known(RUNNER, f"wake:{item}")
                and (terminal := self.orca.terminal(lane))
                and self.orca.wake(terminal, f"unread Orca message {item}; read it now")
            ):
                self.book.accept(RUNNER, f"wake:{item}", "wake", item, lane, None)
        hour = actions.stamp(self.now())[:13]
        named = {action.action_id for action in self.book.actions(RUNNER, kind="reclaim")}
        settled: list[Dispatch] = []
        lanes = [lane for lane in self.orca.lanes() if f"reclaim:{self.orca.receipt(lane)}" not in named]
        with ThreadPoolExecutor(SHOW_WORKERS) as pool:
            shown = list(pool.map(self.orca.show, lanes))
        for lane, dispatch in zip(lanes, shown):
            if not dispatch:
                continue
            if dispatch.status in INACTIVE:
                settled.append(dispatch)
                continue
            if not dispatch.live:
                self.escalate(f"liveness:{dispatch.id}:{hour}", "LIVENESS", lane, f"dispatch={dispatch.id} terminal={dispatch.terminal} is not live; resume it in place, never relaunch on this alone")
        if settled:
            self.reclaim(settled)
        watch = json.loads(self.shell.run([sys.executable, str(SCRIPTS / "prompt_watch.py"), "show", "--cci-drive", self.config.drive, "--json"]).out or "{}")
        for row in watch.get("rows") or []:
            if row["state"] == "down":
                self.escalate(f"prompt-watch:{hour}", "PROMPT-WATCH-DOWN", "runner", row["detail"])

    def reclaim(self, settled: list[Dispatch]) -> None:
        named = " ".join(f"{dispatch.lane}={dispatch.id}:{dispatch.terminal}" for dispatch in settled[:RECLAIM_NAMED])
        more = f" and {len(settled) - RECLAIM_NAMED} more" if len(settled) > RECLAIM_NAMED else ""
        scope = "".join(f" --dispatch {dispatch.id}" for dispatch in settled) if len(settled) <= RECLAIM_NAMED else ""
        worktrees = f"{self.config.gc} --run {self.config.run}{scope}" if self.config.gc else "close each other idle terminal and remove each finished worktree under R195"
        step = f"run {SCRIPTS / 'desk-runner.py'} gc --config {self.config.source}, then {worktrees}"
        self.escalate(f"reclaim:{settled[0].id}", "RECLAIM", "runner", f"{len(settled)} settled dispatch(es) still hold their terminal: {named}{more}; {step}")
        for dispatch in settled:
            self.book.accept(RUNNER, f"reclaim:{dispatch.id}", "reclaim", dispatch.terminal, dispatch.lane, None)

    def gc(self, dry_run: bool) -> int:
        """Close each terminal a settled launch's receipt says orca-launch.sh created, once its agent reads idle, and drop that receipt; print one line per receipt and a tally."""
        run = RunView.of(self.orca.workers())
        tally: Counter[str] = Counter()
        for lane in sorted(path.name.removesuffix(CREATED) for path in self.config.receipts.glob(f"*{CREATED}")):
            verb, target, reason = self.gc_lane(lane, run, dry_run)
            tally[verb] += 1
            print(f"{verb} {target} {reason}", flush=True)
        print(" ".join(f"{verb}={count}" for verb, count in sorted(tally.items())) or "nothing to reclaim")
        return 1 if tally["error"] else 0

    def gc_lane(self, lane: str, run: RunView, dry_run: bool) -> tuple[str, str, str]:
        """What gc does with one lane's created terminal: `close` it, `drop` a receipt whose terminal is already closed, `keep` it with the reason, or `error`."""
        receipt = self.config.receipts / f"{lane}{CREATED}"
        raw = receipt.read_text()
        if (created := self.orca.created(lane)) is None:
            return "keep", lane, "its last terminal create named no terminal"
        handle = created["handle"]
        container = f"{LANE_PREFIX}{lane}"
        launches = self.book.actions(container, kind="launch") if container in self.book.store.ids() else []
        if unsettled := next((action.action_id for action in launches if action.status in actions.OPEN), ""):
            return "keep", handle, f"launch {unsettled} of {lane} is {self.book.known(container, unsettled).status}"
        if self.orca.terminal(lane) == handle and (dispatch := self.orca.receipt(lane)):
            if run.statuses.get(dispatch) not in INACTIVE:
                return "keep", handle, f"dispatch {dispatch} is {run.statuses.get(dispatch) or 'not in the Run'}"
            settled = f"dispatch {dispatch} is {run.statuses[dispatch]}"
        elif handle in run.named:
            return "keep", handle, f"dispatch {', '.join(sorted(run.named[handle]))} references it"
        elif not self.wrote_receipt(container, launches, receipt):
            return "keep", handle, f"launch {lane} has no dispatch, and no settled launch of it by this runner wrote its receipt"
        else:
            settled = f"launch {lane} has no dispatch"
        worktree_receipt = self.config.receipts / f"{lane}.worktree"
        worktree = worktree_receipt.read_text().strip() if worktree_receipt.is_file() else ""
        shown = self.orca.ask("terminal", "show", "--terminal", handle)
        if shown.get("ok") and not shown["result"]["terminal"]["connected"]:
            if not dry_run:
                drop_receipt(receipt, raw)
            return "drop", handle, f"{settled}, and its terminal is already closed"
        target, replacing = handle, ""
        if shown.get("ok"):
            worktree = worktree or shown["result"]["terminal"].get("worktreePath") or ""
        elif shown["error"]["code"] != STALE_HANDLE:
            return "keep", handle, f"orca terminal show {shown['error']['code']}"
        elif not (pane := created.get("paneKey")) or not worktree:
            return "keep", handle, "stale handle, and its launch receipt records no pane or worktree"
        elif worktree in run.live_worktrees:
            return "keep", handle, "stale handle, and an unsettled dispatch works in its worktree"
        elif isinstance(listed := self.orca.connected_in(worktree), str):
            return "keep", handle, f"stale handle, {listed}"
        elif not (target := next((row["handle"] for row in listed if f"{row.get('tabId')}:{row.get('leafId')}" == pane), "")):
            return "keep", handle, "stale handle, and no connected terminal carries its launch pane"
        else:
            replacing = f", replacing stale {handle}"
        if target in run.live:
            return "keep", target, f"serves unsettled dispatch {', '.join(sorted(run.named[target]))}"
        if not self.orca.quiet(target):
            return "keep", target, NOT_IDLE
        if not dry_run:
            if error := self.orca.close(target, worktree):
                return "error", target, f"orca terminal close {error}"
            drop_receipt(receipt, raw)
        return "close", target, f"{settled}{replacing}"

    def wrote_receipt(self, container: str, launches: list[actions.Action], receipt: Path) -> bool:
        """Whether the runner's latest started launch of the lane wrote the receipt: it changed after that launch started and before its launch log last did."""
        latest = max((action for action in launches if action.started_at), key=lambda action: action.started_at, default=None)
        if latest is None or (written := self.shell.stat(receipt)) is None or (logged := self.shell.stat(self.launch_log(container, latest.action_id))) is None:
            return False
        return actions.parse_stamp(latest.started_at).timestamp() * 1e9 <= written[1] <= logged[1]

    def overdue(self, container: str, about: str) -> None:
        moment = self.now()
        for action in self.book.load(container).actions.values():
            if action.kind not in ("relay", "reply", "launch", "enqueue") or not action.overdue(moment) or action.status not in ("accepted", "started"):
                continue
            if action.kind in ("relay", "reply") and action.status == "started" or self.lane_moved(about, action):
                continue
            sent = [send for send in self.book.actions(container, kind="send") if send.authority_ref == action.action_id]
            missing = {
                "relay": f"delivered to {sent[-1].target}, no started reply" if sent and sent[-1].status == "completed" else "never delivered: no live dispatch" if not sent else f"send {sent[-1].status}",
                "reply": "never delivered: no live dispatch" if not sent else f"send {sent[-1].status}",
                "launch": "launch never finished" if action.status == "started" else "launch never started",
                "enqueue": "the adapter's enqueue never returned" if action.status == "started" else "enqueue never ran",
            }[action.kind]
            self.escalate(f"deadline:{container}/{action.action_id}:{action.status}", "DEADLINE", about, f"{action.authority_ref} {action.kind}: {missing}; accepted {pacific(actions.parse_stamp(action.accepted_at))}")

    def lane_moved(self, lane: str, action: actions.Action) -> bool:
        if action.kind != "relay" or not (relayed := RELAYED_PR.match(action.action_id)):
            return False
        since = actions.parse_stamp(action.accepted_at)
        try:
            posted = self.lane_records(lane, "head", "review")
        except CciTailFailed as failure:
            self.tail_failed(failure)
            return True
        return any(names_pr(record, relayed[1]) and actions.parse_stamp(record["at"]) >= since for record in posted)

    def deadlines_orca(self) -> None:
        for container in self.book.containers(LANE_PREFIX):
            self.overdue(container, container.removeprefix(LANE_PREFIX))

    def incident_clock(self) -> None:
        """Hold each live incident fix lane the runner launched to its budget from the verified launch: past a threshold without its milestone, launch the Opus fallback and log INCIDENT-CLOCK, once per launch; `ready`, a closing record, or a settled dispatch stops the clock."""
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            incident = self.book.load(container)
            launch = fix_launch(incident)
            kept = incident.facts.get(CLOCK) or {}
            current = bool(launch) and kept.get("key") == launch.action_id
            if not launch or (current and kept["ended"]):
                continue
            launched = actions.parse_stamp(launch.verification_receipt["at"])
            clock = {"key": launch.action_id, "launched": launch.verification_receipt["at"], "reached": kept["reached"] if current else {}, "ended": ""}
            withdrawn = self.withdrawn_by(lane)
            if not withdrawn and not (dispatch := self.orca.show(lane)):
                continue
            if withdrawn:
                clock["ended"] = f"{WITHDRAWN} by {withdrawn}"
            elif dispatch.id != launch.dispatch_id:
                clock["ended"] = f"relaunched as dispatch {dispatch.id}"
            elif dispatch.status in INACTIVE:
                clock["ended"] = f"dispatch {dispatch.status}"
            else:
                try:
                    stood_down = self.stood_down(lane, launched)
                    clock["reached"] = self.reached(container, lane, launched)
                except CciTailFailed as failure:
                    self.tail_failed(failure)
                    continue
                clock["ended"] = f"{WITHDRAWN} by {stood_down}" if stood_down else next((name for name in (*CLOCK_ENDS, "ready") if name in clock["reached"]), "")
                if not clock["ended"]:
                    self.overrun(lane, launch, launched, clock["reached"])
            if clock != kept:
                self.book.edit(container, lambda incident, clock=clock: incident.facts.update({CLOCK: clock}))

    def reached(self, container: str, lane: str, launched: datetime) -> dict[str, str]:
        """Each milestone a lane reached since `launched`, at its earliest stamp: the lane's own cci records by kind, and its milestone statuses from the Run. Any milestone a sibling lane on the same incident reached counts as this lane's mechanism, so the PR and ready thresholds stay the lane's own."""
        marks = [(mark, record["at"]) for record in self.lane_records(lane, *CLOCK_MILESTONES, *CLOCK_ENDS, *TEXT_MARK_KINDS) if (mark := clock_mark(record))]
        marks += [(status["name"], status["at"]) for status in (self.book.load(container).facts.get(MILESTONE_STATUSES) or {}).values()]
        marks += [("mechanism", at) for at in self.sibling_milestones(lane)]
        reached: dict[str, str] = {}
        for name, at in sorted(marks, key=lambda mark: actions.parse_stamp(mark[1])):
            if actions.parse_stamp(at) >= launched:
                reached.setdefault(name, at)
        return reached

    def stood_down(self, lane: str, launched: datetime) -> str:
        """The root's `stand-down` or `stopped` addressed to the lane since its launch, as `#<seq>`, or empty."""
        names = set(lane_aliases(lane))
        posted = cci.records(self.cci_tail, self.config.drive, "--lane", ROOT, f"--kind={STAND_DOWN}", f"--kind={STOPPED}")
        return next((f"#{record['seq']}" for record in posted if record["drive"] == self.config.drive and record["lane"] == ROOT and names & set(record.get("to") or []) and actions.parse_stamp(record["at"]) >= launched), "")

    def incident_keys(self, lane: str, launch: actions.Action) -> set[str]:
        """What names the incident a fix lane works: its `incident_name`, the incident slug its launch carries, and its brief's `incident=` annotations."""
        spec = json.loads(launch.target)
        return {incident_name(lane), *(brief_incidents(Path(spec["brief"])) - {NAMES_NOTHING}), *([spec["incident"]] if spec.get("incident") else [])}

    def siblings(self, lane: str) -> set[str]:
        """Every other lane on the incident `lane` works: the fix lanes the runner launched that share one of its `incident_keys`, and the Opus backup an incident executor launched for any of them."""
        own = fix_launch(self.book.load(lane_container(lane)))
        keys = self.incident_keys(lane, own) if own else {incident_name(lane)}
        family = {lane}
        for container in self.book.containers(LANE_PREFIX):
            other = container.removeprefix(LANE_PREFIX)
            if other != lane and (launch := fix_launch(self.book.load(container))) and keys & self.incident_keys(other, launch):
                family.add(other)
        store = actions.Store()
        for incident in map(store.load, store.ids()):
            if (incident.facts.get("adopted") or {}).get("fix") in family and (backup := latest(incident, "dispatch:backup")):
                family.add(backup.target)
        return family - {lane}

    def sibling_milestones(self, lane: str) -> list[str]:
        """The stamps of every mechanism, PR, or ready milestone the lane's siblings reached, from their cci records and their milestone statuses."""
        siblings = self.siblings(lane)
        if not siblings:
            return []
        names = {name for sibling in siblings for name in lane_aliases(sibling)}
        posted = cci.records(self.cci_tail, self.config.drive, *(arg for name in sorted(names) for arg in ("--lane", name)), *(f"--kind={kind}" for kind in (*CLOCK_MILESTONES, *TEXT_MARK_KINDS)))
        stamps = [record["at"] for record in posted if record["drive"] == self.config.drive and record["lane"] in names and clock_mark(record) in CLOCK_MILESTONES]
        for sibling in siblings:
            if lane_container(sibling) in self.book.store.ids():
                stamps += [status["at"] for status in (self.book.load(lane_container(sibling)).facts.get(MILESTONE_STATUSES) or {}).values()]
        return stamps

    def live_clocks(self) -> list[str]:
        """The incident fix lanes whose clock is running."""
        return [container.removeprefix(LANE_PREFIX) for container in self.book.containers(LANE_PREFIX) if (clock := self.book.load(container).facts.get(CLOCK)) and not clock["ended"]]

    def overrun(self, lane: str, launch: actions.Action, launched: datetime, reached: dict[str, str]) -> None:
        """Log each threshold the lane has passed without its milestone or a later one, once per launch; the mechanism and PR thresholds launch the Opus fallback first, and the ready threshold names the gap."""
        elapsed = whole_minutes(launched, self.now())
        limits = {"mechanism": self.config.incident_mechanism_minutes, "opened": self.config.incident_pr_minutes, "ready": self.config.incident_ready_minutes}
        names = list(CLOCK_MILESTONES)
        missing = names[max((names.index(name) + 1 for name in reached if name in names), default=0) :]
        for name in missing:
            label = CLOCK_MILESTONES[name]
            log = f"escalation:{CLOCK}:{launch.dispatch_id}:{name}"
            if elapsed < limits[name] or self.book.known(RUNNER, log):
                continue
            phases = clock_phases(launched, reached, self.now())
            if name == "ready":
                text = f"not ready {elapsed} min after its launch at {pacific(launched)}, limit {limits[name]} min; missing {', '.join(CLOCK_MILESTONES[gap] for gap in missing)}; {phases}"
            else:
                text = f"no {label} {elapsed} min after its launch at {pacific(launched)}, limit {limits[name]} min; {phases} | {self.fallback(lane, launch, label, elapsed)}"
            self.record(log, f"INCIDENT-CLOCK {lane} {label.lower()}: {text}")

    def fallback(self, lane: str, launch: actions.Action, missing: str, minutes: int) -> str:
        """Launch `<lane>-opus` once per clocked launch, on opus xhigh in Claude fast mode, from the lane's own brief under a fallback header attached to the briefs log; the clause returned tells the blocker what was done."""
        backup = f"{lane}{CLOCK_FALLBACK}"
        key = f"{CLOCK}:{launch.dispatch_id}"
        if lane.endswith(CLOCK_FALLBACK):
            return f"{lane} is itself an Opus fallback, so none is launched for it"
        if self.book.known(lane_container(backup), key):
            return f"the Opus fallback {backup} is already launched"
        if rival := self.executor_backup(lane):
            return f"no fallback launched for {backup}: the incident executor's Opus backup {rival} is launching or live, and a fix lane gets one Opus fallback"
        brief = self.brief_for(lane)
        if not brief:
            return f"no fallback launched for {backup}: {lane} has no brief file"
        if refusal := self.launch_refusal(key, backup, "opus", "xhigh", brief, INCIDENT_MODEL):
            return f"no fallback launched for {backup}: {refusal}"
        text = self.sliced(key, backup, FALLBACK_BRIEF.format(backup=backup, lane=lane, minutes=minutes, missing=missing, drive=self.config.drive, brief=brief.read_text()))
        attached, failure = self.attach_brief(backup, lambda _: text, f"INCIDENT-CLOCK {lane}: Opus fallback brief for {backup}, no {missing} at +{minutes} min")
        if attached is None:
            return f"no fallback launched for {backup}: {failure}"
        self.accept_launch(key, backup, "opus", "xhigh", str(attached), True, fast=True)
        return f"the Opus fallback {backup} is launching on opus xhigh in fast mode, brief {attached}"

    def pr_silent(self) -> None:
        """Hold each incident fix lane's mechanism to `deadlines.pr_silent_minutes`: a MECHANISM or CANDIDATE with no OPENED or BLOCKER since logs one PR-SILENT naming the lane's worktree state, and again every `deadlines.pr_silent_every_minutes` until one lands, the lane ends, or the root answers it."""
        try:
            self.pr_silent_escalate(self.pr_silent_scan())
        except CciTailFailed as failure:
            self.tail_failed(failure)

    def class_unowned(self) -> None:
        """Log one `CLASS-UNOWNED` for each class record that names an incident key and, `deadlines.pr_silent_minutes` after it, has no later root GO, decide, or decision naming one of its keys."""
        try:
            records = [record for record in cci.records(self.cci_tail, self.config.drive, since=actions.stamp(self.now() - PR_SILENT_HORIZON)) if record["drive"] == self.config.drive]
        except CciTailFailed as failure:
            self.tail_failed(failure)
            return
        after = timedelta(minutes=self.config.pr_silent_minutes)
        gos = [record for record in records if record["lane"] == ROOT and record["kind"] in FIX_GO_KINDS and FIX_GO.search(record.get("text") or "")]
        for record in records:
            if record["lane"] in (ROOT, RUNNER) or not (record["kind"] == "incident" or CLASS_TEXT.match(record.get("text") or "")):
                continue
            if not (keys := cci.incident_keys(record)):
                continue
            label = min(keys)
            log = f"escalation:{CLASS_UNOWNED}:{label}"
            if self.book.known(RUNNER, log):
                continue
            at = actions.parse_stamp(record["at"])
            if self.now() - at < after or any(go["seq"] > record["seq"] and keys & cci.incident_keys(go) for go in gos):
                continue
            named = ", ".join(sorted(keys))
            self.record(
                log,
                f"CLASS-UNOWNED {label} {record['lane']}: #{record['seq']} at {pacific(at)}, {whole_minutes(at, self.now())} min with no fix-lane GO naming {named} | root: GO a fix lane for this class",
            )

    def pr_silent_escalate(self, pending: dict[str, dict]) -> None:
        after, every = timedelta(minutes=self.config.pr_silent_minutes), timedelta(minutes=self.config.pr_silent_every_minutes)
        for lane, mechanism in pending.items():
            at = actions.parse_stamp(mechanism["at"])
            elapsed = self.now() - at
            log = f"escalation:{PR_SILENT}:{lane}:{mechanism['seq']}:{(elapsed - after) // every}"
            if elapsed < after or log in self.pr_silent_skipped or self.withdrawn_by(lane) or self.book.known(RUNNER, log):
                continue
            worktrees = self.lane_worktrees(lane)
            if not worktrees and not self.owns_fix(lane):
                self.pr_silent_skipped.add(log)
                continue
            self.record(
                log,
                f"PR-SILENT {lane} #{mechanism['seq']}: {mechanism['mark']} at {pacific(at)}, {whole_minutes(at, self.now())} min with no OPENED or BLOCKER record; {lanes.summary(lane, worktrees)} | {lane}: commit and push the fix, open its PR with the submit skill and post OPENED, or post BLOCKER",
            )

    def pr_silent_scan(self) -> dict[str, dict]:
        """Read the drive's records since the saved cursor, or the last `PR_SILENT_HORIZON` on a first read, into the incident lanes whose mechanism waits on a PR."""
        kept = self.book.load(RUNNER).facts.get(PR_SILENT) or {}
        pending = {lane: dict(mechanism) for lane, mechanism in (kept.get("lanes") or {}).items()}
        answered = dict(kept.get("answered") or {})
        state = {key: value for key, value in kept.items() if key == "cursor"}
        for record in cci.records(self.cci_tail, self.config.drive, since=kept.get("cursor") or actions.stamp(self.now() - PR_SILENT_HORIZON)):
            if record["drive"] != self.config.drive:
                continue
            state["cursor"] = max(state.get("cursor", 0), record["seq"])
            self.pr_silent_read(record, pending, answered)
        state["lanes"] = pending
        state["answered"] = dict(sorted(answered.items(), key=lambda item: item[1])[-ANSWERS_KEPT:])
        if state != kept:
            self.book.edit(RUNNER, lambda incident: incident.facts.update({PR_SILENT: state}))
        return pending

    def pr_silent_read(self, record: dict, pending: dict[str, dict], answered: dict[str, int]) -> None:
        lane = record["lane"]
        if lane == RUNNER:
            return
        if answers := cci.answers_mechanism(record):
            answered.update(dict.fromkeys(answers, record["seq"]))
            for silent, mechanism in list(pending.items()):
                if answers & set(mechanism.get("keys") or [f"seq:{mechanism['seq']}"]):
                    pending.pop(silent)
        if lane == ROOT:
            names, answered = set(record.get("to") or []), {record.get("resolves"), record.get("re")} - {None}
            for silent, mechanism in list(pending.items()):
                if (record["kind"] in (STAND_DOWN, STOPPED) and names & set(lane_aliases(silent))) or answered & self.pr_silent_posts(silent, mechanism["seq"]):
                    pending.pop(silent)
            return
        if (mark := cci.mechanism_mark(record)) and self.pr_silent_lane(lane):
            keys = cci.incident_keys(record)
            if lane not in pending and not keys & set(answered):
                pending[lane] = {"seq": record["seq"], "at": record["at"], "mark": mark, "keys": sorted({*keys, f"seq:{record['seq']}", *(f"lane:{alias}" for alias in lane_aliases(lane))})}
        elif cci.settles_mechanism(record):
            for silent in [silent for silent in pending if pr_family(silent) == pr_family(lane)]:
                pending.pop(silent)

    def pr_silent_lane(self, lane: str) -> bool:
        """Whether a lane is an incident fix lane: one the runner launched as a fix lane, or, launched elsewhere, one named `inc-`, `incident-`, or `outage-` whose name and brief name no evidence, proof, closeout, desk, or audit role."""
        container = lane_container(lane)
        incident = self.book.load(container) if container in self.book.store.ids() else None
        if incident and any(action.kind == "launch" and action.status == "verified" for action in incident.actions.values()):
            return fix_launch(incident) is not None
        return bool(cci.INCIDENT_LANE.match(lane)) and not lanes.owes_no_pr(self.config.desk_inbox.parent.parent, lane)

    def pr_silent_posts(self, lane: str, seq: int) -> set[int]:
        """The cci seqs of every PR-SILENT the runner posted for this mechanism."""
        prefix = f"escalation:{PR_SILENT}:{lane}:{seq}:"
        return {int(found[1]) for action in self.book.actions(RUNNER, kind="escalation") if action.action_id.startswith(prefix) and (found := POSTED.search((action.response or {}).get("posted", "")))}

    def lane_worktrees(self, lane: str) -> list[lanes.Worktree]:
        """What code the lane holds, by `lanes.lane_status` over the runner's launch receipts, the lane's cci records of the last day, and the briefs checkout's worktrees."""
        records = [record for record in cci.records(self.cci_tail, self.config.drive, "--lane", lane, since=actions.stamp(self.now() - LANE_RECORDS)) if record["drive"] == self.config.drive and record["lane"] == lane]
        repo = (self.config.landing or {}).get("repo", "")
        return lanes.lane_status(lane, records, [self.config.receipts], Path(self.config.briefs_repo), repo, TRUNK, self.now(), self.checked)

    def owns_fix(self, lane: str) -> bool:
        """Whether the lane owes a PR without having made a worktree yet: a fix lane the runner launched, or one named as a fix, fallback, or case lane."""
        container = lane_container(lane)
        return bool(cci.FIX_LANE.search(lane)) or (container in self.book.store.ids() and fix_launch(self.book.load(container)) is not None)

    def checked(self, argv: list[str]) -> str:
        done = self.shell.run(argv)
        if done.code:
            raise subprocess.CalledProcessError(done.code, argv, done.out, done.err)
        return done.out

    def in_process(self) -> None:
        """While a fix lane's clock runs, launch its Opus fallback once more after a failed Orca launch, and after a second failure tell the root to start it in-process."""
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            incident = self.book.load(container)
            launch, clock = fix_launch(incident), incident.facts.get(CLOCK) or {}
            backup = f"{lane}{CLOCK_FALLBACK}"
            if not launch or clock.get("key") != launch.action_id or clock["ended"] or lane_container(backup) not in self.book.store.ids():
                continue
            key = f"{CLOCK}:{launch.dispatch_id}"
            tries = [action for action in self.book.actions(lane_container(backup), kind="launch") if action.action_id in (key, f"{key}{RETRY}")]
            if not tries or any(action.status != "failed" for action in tries) or self.executor_backup(lane):
                continue
            brief = json.loads(tries[0].target)["brief"]
            if len(tries) == 1:
                if not self.launch_refusal(f"{key}{RETRY}", backup, "opus", "xhigh", Path(brief), INCIDENT_MODEL):
                    self.accept_launch(f"{key}{RETRY}", backup, "opus", "xhigh", brief, True, fast=True)
                continue
            reasons = " | ".join(action.reason or "" for action in tries)
            self.record(
                f"escalation:{CLOCK}:{launch.dispatch_id}:in-process",
                f"INCIDENT-CLOCK {lane} in-process: the Orca launch of the Opus fallback {backup} failed twice: {reasons} | root: start {backup} in-process now, an Agent of type long-running:lane-ship named {backup} on opus in fast mode with the brief {brief}",
            )

    def release_coupling(self) -> None:
        """Couple the drive to the releases its lanes start: claim each started build for its lane, announce each watched apply to every live drive root, and ask to lift each freeze a lane left behind."""
        if not self.config.landing:
            return
        try:
            self.read_release_records()
            if self.now() - self.release_polled < RELEASE_POLL:
                return
            self.release_polled = self.now()
            self.watch_applies()
            self.ask_lifts()
        except CciTailFailed as failure:
            self.tail_failed(failure)

    def read_release_records(self) -> None:
        """Track each build a lane's `release --build` record names and each freeze a lane's `hold` record names as `froze <scope> until <time> as freeze <id>`; a build is claimed for its lane the pass it is first read. A first read starts at the newest record."""
        drive = self.config.drive
        cursor = self.book.load(RUNNER).facts.get(RELEASE_CURSOR)
        if cursor is None:
            newest = cci.records(self.cci_tail, drive, "--limit", "1")
            self.book.edit(RUNNER, lambda incident: incident.facts.update({RELEASE_CURSOR: max((record["seq"] for record in newest), default=0)}))
            return
        builds: dict[str, dict] = {}
        freezes: dict[str, dict] = {}
        for record in cci.records(self.cci_tail, drive, "--kind=release", "--kind=hold", since=cursor):
            cursor = max(cursor, record["seq"])
            if record["drive"] != drive or record["lane"] in (RUNNER, ROOT):
                continue
            if record["kind"] == "release":
                release = found[0] if (found := RELEASE_ID.search(record["text"])) else ""
                for build in (record.get("refs") or {}).get("builds") or []:
                    builds.setdefault(str(build), {"lane": record["lane"], "release": release, "seq": record["seq"], "done": False})
            elif record["kind"] == "hold" and (froze := FROZE.search(record["text"])):
                freezes.setdefault(froze["id"], {"lane": record["lane"], "scope": froze["scope"], "until": froze["until"], "hold": record["seq"], "asked": []})
        known = self.book.load(RUNNER).facts.get(RELEASE_BUILDS) or {}
        fresh = {build: info for build, info in builds.items() if build not in known}
        for build, info in fresh.items():
            self.claim_release(build, info)

        def keep(incident: actions.Incident) -> None:
            incident.facts.setdefault(RELEASE_BUILDS, {}).update(fresh)
            held = incident.facts.setdefault(FREEZES, {})
            for freeze, info in freezes.items():
                held.setdefault(freeze, info)
            incident.facts[RELEASE_CURSOR] = cursor

        self.book.edit(RUNNER, keep)

    def claim_release(self, build: str, info: dict) -> None:
        """Record the build's owner, `<lane>@<drive>`, in the drive registry and claim the build for that lane with `cci claim --for`; a step that fails escalates RELEASE-CLAIM-FAILED once."""
        owner = f"{info['lane']}@{self.config.drive}"
        registry = [sys.executable, str(SCRIPTS / "drive.py"), "release", "--cci-drive", self.config.drive, "--owner", owner, "--build", build, *(["--release", info["release"]] if info["release"] else [])]
        failures = [
            f"`{' '.join(argv[1:] if argv[0] == sys.executable else argv)}` exited {done.code}: {' '.join((done.err or done.out).split())[:200]}"
            for argv in (registry, ["cci", "claim", "--for", owner, "--build", build, "--rel", "started"])
            if (done := self.shell.run(argv)).code != 0
        ]
        if failures:
            self.escalate(f"release-claim:{build}", "RELEASE-CLAIM-FAILED", info["lane"], f"build {build} started by {owner}: {'; '.join(failures)}")

    def watch_applies(self) -> None:
        """Read each tracked build through `landing.applying.build` and post one `applying <component>/<env> <commit> (<release>)` release record to every live drive root for each job whose step key `landing.applying.step` matches, of a component `landing.applying.components` names, the first pass it reads running; a finished build is no longer read."""
        applying = self.config.landing.get("applying")
        if not applying:
            return
        for build, info in (self.book.load(RUNNER).facts.get(RELEASE_BUILDS) or {}).items():
            if info["done"]:
                continue
            argv = release_build_argv(Path(self.config.landing["checkout"]).expanduser(), applying["build"], build)
            if argv is None:
                self.escalate(f"applying-read:{build}", "APPLYING-UNREAD", info["lane"], f"build {build} is no Buildkite build URL, and landing.applying.build names its pipeline; no apply of it is announced")
                self.book.edit(RUNNER, lambda incident, build=build: incident.facts[RELEASE_BUILDS][build].update({"done": True}))
                continue
            done = self.shell.run(argv)
            if done.code != 0:
                self.escalate(f"applying-read:{build}", "APPLYING-UNREAD", info["lane"], f"`{' '.join(argv)}` exited {done.code}: {' '.join((done.err or done.out).split())[:200]}; it is read again next pass")
                continue
            try:
                shown = json.loads(done.out)
            except json.JSONDecodeError:
                self.escalate(f"applying-read:{build}", "APPLYING-UNREAD", info["lane"], f"`{' '.join(argv)}` printed no JSON: {' '.join(done.out.split())[:200]}; it is read again next pass")
                continue
            for job in shown.get("jobs") or []:
                step = re.fullmatch(applying["step"], job.get("step_key") or "")
                if step and job.get("state") == "running" and any(fnmatch(step["component"], glob) for glob in applying["components"]):
                    self.announce_apply(build, info, step["component"], step["env"], job["id"], shown.get("commit") or "")
            if shown.get("finished_at"):
                self.book.edit(RUNNER, lambda incident, build=build: incident.facts[RELEASE_BUILDS][build].update({"done": True}))

    def announce_apply(self, build: str, info: dict, component: str, env: str, job: str, commit: str) -> None:
        key = f"applying:{job}"
        if self.book.known(RUNNER, key):
            return
        done = self.shell.run(["cci", "who", "--role", "root", "--json"])
        if done.code != 0:
            raise CciTailFailed(f"cci who exited {done.code}: {(done.err or done.out).strip()[:300]}")
        drives = [json.loads(line).get("drive") or "" for line in done.out.splitlines() if line.strip()]
        to = ",".join(dict.fromkeys([ROOT, *(f"{ROOT}@{drive}" for drive in drives if drive and drive != self.config.drive)]))
        stack = f"{component}/{env}"
        text = f"applying {stack} {commit[:8] or 'at an unread commit'} ({info['release'] or f'build {build}'})"
        posted = self.cci_post("release", text, to, stack, key, record_id=key, flags=["--stack", stack, "--build", build])
        if posted.code == 0 and POSTED.match(posted.out):
            self.book.accept(RUNNER, key, "applying", json.dumps({"build": build, "stack": stack, "to": to}), info["lane"], None)
        else:
            self.escalate(key, "APPLYING-FAILED", info["lane"], f"{text} was not posted: {' '.join((posted.err or posted.out).split())[:200]}; it is posted again on the next poll that reads the job running")

    def ask_lifts(self) -> None:
        """For each freeze a lane's open `hold` record names, post one `ask` when it ran past its until, to its lane, and one to root when its lane has no live session; a hold that closed drops the freeze."""
        for freeze, info in list((self.book.load(RUNNER).facts.get(FREEZES) or {}).items()):
            hold = self.cci_record(info["hold"])
            if hold is None or hold["status"] != "open":
                self.book.edit(RUNNER, lambda incident, freeze=freeze: incident.facts[FREEZES].pop(freeze))
                continue
            close = f"close hold #{info['hold']} with `cci post --kind lift --resolves {info['hold']}`"
            asks = []
            if (until := parse_until(info["until"])) and self.now() >= until:
                asks.append(("lapsed", info["lane"], f"lift freeze {freeze} on {info['scope']}: it ran to its until, {info['until']}; {close}, or freeze again to extend it"))
            if not self.lane_live(info["lane"]):
                asks.append(("orphaned", ROOT, f"lift freeze {freeze} on {info['scope']}: lane {info['lane']}, its owner, has no live session; run `{self.release_command()} unfreeze {freeze}` and {close}, or hand it to another lane"))
            for cause, to, text in asks:
                if cause in info["asked"]:
                    continue
                key = f"lift-ask:{freeze}:{cause}"
                posted = self.cci_post("ask", text, to, freeze, key, record_id=key, flags=["--target", info["scope"].split("/")[0]])
                if posted.code == 0 and POSTED.match(posted.out):
                    self.book.edit(RUNNER, lambda incident, freeze=freeze, cause=cause: incident.facts[FREEZES][freeze]["asked"].append(cause))
                else:
                    self.escalate(key, "LIFT-ASK-FAILED", info["lane"], f"{text} was not posted: {' '.join((posted.err or posted.out).split())[:200]}; it is posted again next pass")

    def lane_live(self, lane: str) -> bool:
        done = self.shell.run(["cci", "who", "--drive", self.config.drive, "--lane", lane, "--json"])
        if done.code != 0:
            raise CciTailFailed(f"cci who exited {done.code}: {(done.err or done.out).strip()[:300]}")
        return bool(done.out.strip())

    def cut_clock(self) -> None:
        """Hold every ordered cut to `deadlines.cut_minutes`: a root `go` or `decision` to a release lane that orders a cut or `release start`, or a landing's release record the landing runner posted with no build yet, opens a clock for the order's `--target` refs or the landing's components, and a `release` record with a build or a `refuse` from that lane stops it, as does anything `cut_closed` names. Past the limit the runner logs one CUT-SILENT with the hand command and launches `manual-release-<component>`; a later `refuse` that says a release already ships the commit or later goes to that lane."""
        try:
            self.open_cut_clocks()
            for key, clock in (self.book.load(RUNNER).facts.get(CUT_CLOCKS) or {}).items():
                self.press_cut(key, clock)
        except CciTailFailed as failure:
            self.tail_failed(failure)

    def open_cut_clocks(self) -> None:
        cursor = self.book.load(RUNNER).facts.get(CUT_CURSOR)
        if cursor is None:
            newest = cci.records(self.cci_tail, self.config.drive, "--limit", "1")
            self.book.edit(RUNNER, lambda incident: incident.facts.update({CUT_CURSOR: max((record["seq"] for record in newest), default=0)}))
            return
        clocks: dict[str, dict] = {}
        orders = cci.records(self.cci_tail, self.config.drive, "--lane", ROOT, *(f"--kind={kind}" for kind in CUT_KINDS), since=cursor)
        for record in orders:
            cursor = max(cursor, record["seq"])
            lanes = [lane for lane in record.get("to") or [] if RELEASE_LANE.search(lane)]
            refs = record.get("refs") or {}
            if (record["lane"], record["drive"]) != (ROOT, self.config.drive) or record["kind"] not in CUT_KINDS or not lanes or not refs.get("targets") or not CUT_ORDER.search(record["text"]):
                continue
            commit = refs.get("commit") or next(iter(SHORT_SHA.findall(record["text"])), "<sha>")
            clocks[f"go:{record['seq']}"] = {"lane": lanes[0], "components": refs["targets"], "since": record["at"], "trigger": f"GO #{record['seq']}", "commit": commit, "env": ",".join(refs.get("envs") or []) or "<env>", "pr": "", "prs": [str(pr).rsplit("#", 1)[-1] for pr in refs.get("prs", [])], "resolves": record.get("resolves"), "ended": ""}
        if self.config.landing:
            self.book.ensure(LANDING, LANDING)
            for action in self.book.actions(LANDING, kind="release"):
                landed = json.loads(action.target)
                if action.status == "completed" and landed["components"]:
                    clocks.setdefault(f"release:{landed['pr']}", {"lane": self.config.landing["release_owner"], "components": landed["components"], "since": action.response["at"], "trigger": f"the release record for #{landed['pr']}", "commit": landed["sha"], "env": "<env>", "pr": landed["pr"], "prs": [landed["pr"]], "ended": ""})

        def keep(incident: actions.Incident) -> None:
            held = incident.facts.setdefault(CUT_CLOCKS, {})
            for key, clock in clocks.items():
                held.setdefault(key, clock)
            incident.facts[CUT_CURSOR] = cursor

        self.book.edit(RUNNER, keep)

    def cut_records(self, clock: dict, *kinds: str) -> list[dict]:
        """The drive's records of these kinds that answer the clock: from its release lane, or, for a landing's release, from any lane but the runner's that names the PR or one of its components, posted since the clock opened or resolved by the order that opened it."""
        since = actions.parse_stamp(clock["since"])
        components = set(clock["components"])

        def answers(record: dict) -> bool:
            targets = set((record.get("refs") or {}).get("targets", []))
            if record["lane"] == clock["lane"]:
                return not targets or bool(targets & components)
            return bool(clock["pr"]) and record["lane"] != RUNNER and bool(targets & components or names_pr(record, clock["pr"]))

        posted = cci.records(self.cci_tail, self.config.drive, *(f"--kind={kind}" for kind in kinds))
        return [record for record in posted if record["drive"] == self.config.drive and record["kind"] in kinds and (actions.parse_stamp(record["at"]) >= since or record["seq"] == clock.get("resolves")) and answers(record)]

    def cut_answer(self, clock: dict) -> str:
        """The release lane's answer to the clock's order, a `release` record with a build or a `refuse`, as `#<seq> <kind>`; empty while none came."""
        answered = next((record for record in self.cut_records(clock, "release", "refuse") if record["kind"] == "refuse" or (record.get("refs") or {}).get("builds")), None)
        return f"#{answered['seq']} {answered['kind']}" if answered else ""

    def press_cut(self, key: str, clock: dict) -> None:
        component = clock["components"][0] if "prs" in clock or not key.startswith("go:") else "<component>"
        manual = f"{MANUAL_RELEASE}{component}"
        if clock["ended"] == "silent":
            for record in self.cut_records(clock, "refuse"):
                if record["lane"] != RUNNER and ALREADY_SHIPS.search(record["text"]):
                    self.accept_relay(f"cut-refuse:{record['seq']}", manual, f"{record['lane']} posted on cci #{record['seq']}: {record['text']}", "", self.config.start_minutes)
            return
        if clock["ended"]:
            return
        if closed := self.cut_closed(key, clock):
            self.end_cut(key, closed)
            return
        if answered := self.cut_answer(clock):
            self.end_cut(key, answered)
            return
        minutes = whole_minutes(actions.parse_stamp(clock["since"]), self.now())
        if minutes < self.config.cut_minutes:
            return
        until = actions.stamp(self.now() + CUT_CLAIM)
        stacks = " ".join(f"{component}/{env}" for env in clock["env"].split(","))
        release = self.release_command()
        hand = f"{release} claim {stacks} --purpose incident --reason '{clock['trigger']} cut by hand' --until {until} && {release} start --manual {component} --env {clock['env']} --commit {clock['commit']} --emergency"
        line = f"CUT-SILENT {clock['lane']} {component}: no CUT or refusal {minutes} min after {clock['trigger']}; hand path: {hand}"
        launched = self.manual_release(key, manual, component, clock, hand) if not component.startswith("<") else "no manual release lane launched: the order names no component"
        self.end_cut(key, "silent")
        self.record(f"escalation:cut:{key}", f"{line} | {launched}")

    def cut_closed(self, key: str, clock: dict) -> str:
        """What closed a cut clock before its limit: the landing it holds settled, the root posted `--resolves` on the order that opened it, or another lane's `release` or `fix-live` record since it opened names one of its PRs; empty while none did."""
        if clock["pr"] and self.config.landing and (landing := self.book.load(LANDING).actions.get(f"release:{clock['pr']}")) and landing.status == "verified":
            return f"#{clock['pr']} settled"
        since, prs = actions.parse_stamp(clock["since"]), set(clock.get("prs", []))
        if key.startswith("go:"):
            seq = int(key.removeprefix("go:"))
            replies = cci.records(self.cci_tail, self.config.drive, "--lane", ROOT, since=seq)
            if resolved := next((record for record in replies if (record["lane"], record["drive"]) == (ROOT, self.config.drive) and record.get("resolves") == seq), None):
                return f"#{resolved['seq']} resolves"
        if not prs:
            return ""
        repo = self.config.landing["repo"] if self.config.landing else ""
        posted = cci.records(self.cci_tail, self.config.drive, *(f"--kind={kind}" for kind in SETTLING_KINDS))
        named = next(
            (
                record
                for record in posted
                if record["drive"] == self.config.drive
                and record["kind"] in SETTLING_KINDS
                and record["lane"] != RUNNER
                and actions.parse_stamp(record["at"]) >= since
                and any(names_pr(record, pr) or pr in dict(repo_prs(record, repo)) for pr in prs)
            ),
            None,
        )
        return f"#{named['seq']} {named['kind']}" if named else ""

    def release_command(self) -> str:
        """The release CLI as the landing config names it, run from the repo's root, or a placeholder when it names none."""
        return (self.config.landing or {}).get("release_cli") or "<release cli>"

    def end_cut(self, key: str, ended: str) -> None:
        self.book.edit(RUNNER, lambda incident: incident.facts[CUT_CLOCKS][key].update({"ended": ended}))

    def manual_release(self, key: str, lane: str, component: str, clock: dict, hand: str) -> str:
        """Launch the manual release lane for a silent cut from the manual-release brief; the clause returned tells the root what was done."""
        if refusal := self.launch_refusal(f"{CUT_LAUNCH}{key}", lane, "opus", "xhigh", MANUAL_TEMPLATE):
            return f"{lane} not launched: {refusal}"
        brief, failure = self.attach_brief(
            lane,
            lambda briefs: self.sliced(
                f"{CUT_LAUNCH}{key}",
                lane,
                MANUAL_TEMPLATE.read_text().format(lane=lane, component=component, release_lane=clock["lane"], trigger=clock["trigger"], hand=hand, commit=clock["commit"], drive=self.config.drive, repo=self.config.briefs_repo, log=briefs, release=self.release_command()),
            ),
            f"CUT-SILENT {clock['lane']}: manual release brief for {lane}",
        )
        if brief is None:
            return f"{lane} not launched: {failure}"
        self.accept_launch(f"{CUT_LAUNCH}{key}", lane, "opus", "xhigh", str(brief), True)
        return f"{lane} launching on opus xhigh, brief {brief}"

    def executor_backup(self, lane: str) -> str:
        """The Opus backup an incident executor holds for a fix lane it adopted, while that backup's launch is in flight or its worker is live, or empty."""
        store = actions.Store()
        for incident in map(store.load, store.ids()):
            backup = latest(incident, "dispatch:backup")
            if backup is None or incident.facts["adopted"].get("fix") != lane:
                continue
            if backup.status in actions.OPEN or (backup.status == "verified" and (shown := self.orca.show(backup.target, backup.dispatch_id)) and shown.status not in INACTIVE):
                return backup.target
        return ""

    def clock_line(self, clock: dict) -> str:
        launched = actions.parse_stamp(clock["launched"])
        marks = sorted((actions.parse_stamp(at), name) for name, at in clock["reached"].items())
        reached = ", ".join(f"{CLOCK_MILESTONES.get(name, name)} +{whole_minutes(launched, at)} min" for at, name in marks)
        status = f"stopped: {clock['ended']}" if clock["ended"] else f"{whole_minutes(launched, self.now())} min of {self.config.incident_ready_minutes}"
        return f"- incident clock [{status}] launched {pacific(launched)}; {reached or 'no milestone yet'}"

    def render(self) -> str:
        lines = [f"# desk-runner {pacific(self.now())}", self.binding_line(self.book.load(RUNNER).facts.get(BINDING))]
        if held := self.book.load(RUNNER).facts.get(DEFERRED):
            lines.append(f"deferred during {held['incident']}: {len(held['ids'])}")
            lines += [f"- {self.book.load(RUNNER).actions[key].target}" for key in held["ids"]]
        for lane, mechanism in ((self.book.load(RUNNER).facts.get(PR_SILENT) or {}).get("lanes") or {}).items():
            lines.append(f"pr-silent {lane}: {mechanism['mark']} #{mechanism['seq']} {whole_minutes(actions.parse_stamp(mechanism['at']), self.now())} min ago, no OPENED or BLOCKER; code state: `drive.py lane-status {lane}`")
        if policy := self.landing_policy():
            held = json.loads(policy.target)
            lines.append(f"landing policy: {held['rule']} at {held['revision']} ({policy.authority_ref})")
        for container in [*self.book.containers(LANE_PREFIX), LANDING]:
            incident = self.book.load(container)
            lines.append(f"## {container} owner {incident.owner} generation {incident.owner_generation}{f' pending {incident.pending_owner}' if incident.pending_owner else ''}")
            if clock := incident.facts.get(CLOCK):
                lines.append(self.clock_line(clock))
            for action in sorted(incident.actions.values(), key=lambda action: action.accepted_at):
                if action.kind in ("send", "policy"):
                    continue
                at = [f"accepted {pacific(actions.parse_stamp(action.accepted_at))}"]
                at += [f"started {pacific(actions.parse_stamp(action.started_at))}"] if action.started_at else []
                at += [f"{action.status} {pacific(actions.parse_stamp(receipt['at']))}"] if (receipt := action.verification_receipt or action.response) and "at" in receipt else []
                status = action.status
                if action.kind == "launch" and status == "accepted" and (held := self.load_refusal()):
                    status = f"HELD {held} for {int((self.now() - actions.parse_stamp(action.accepted_at)).total_seconds() // 60)}m"
                lines.append(f"- {action.action_id} {action.kind} [{status}] {' '.join(at)}")
        return "\n".join(lines) + "\n"

    def write_view(self) -> None:
        text = self.render()
        current = self.config.view.read_text() if self.config.view.is_file() else ""
        if current.split("\n", 1)[1:] != text.split("\n", 1)[1:]:
            self.config.view.parent.mkdir(parents=True, exist_ok=True)
            self.config.view.write_text(text)


def lost(incident: actions.Incident, key: str, response: dict) -> None:
    incident.lose(key)
    incident.action(key).response = response


def finish(incident: actions.Incident, key: str, response: dict, dispatch: str) -> None:
    if incident.action(key).status == "accepted":
        incident.start(key, actions.parse_stamp(response["at"]), dispatch_id=dispatch)
    incident.complete(key, response, dispatch_id=dispatch)


def verify_all(incident: actions.Incident, keys: list[str], receipt: dict) -> None:
    for key in keys:
        incident.verify(key, receipt)


def enqueue_key(prefix: list[str], verdicts: dict) -> str:
    return "enqueue:" + ",".join(f"{pr}@{verdicts[pr]['sha'] if pr in verdicts else ''}" for pr in prefix)


def enqueued_heads(key: str) -> dict[str, str]:
    return dict(entry.split("@", 1) for entry in key.split("#")[0].removeprefix("enqueue:").split(","))


REVIEWS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes requested", "REVIEW_REQUIRED": "waiting for review"}


def merge_card_block(repo: str, pr: str, waits: str, detail: str, view: dict) -> dict:
    """The board card that asks whether to merge `pr` at the head in `waits`: a short question, then why it waits and any rule it touches, the PR's title, author, size, and review state, the PR itself, and Merge recommended over Hold."""
    summary = f"{view['title']}, by {view['author']['login']}, +{view['additions']} −{view['deletions']} lines, review: {REVIEWS.get(view.get('reviewDecision') or '', 'none yet')}."
    context = f"{detail}\n\n{summary}"
    return {
        "id": waits,
        "type": "card",
        "title": f"Merge this PR at {waits.rsplit(':', 1)[1]}?",
        "children": [
            {"id": f"{waits}-context", "type": "markdown", "md": context},
            {"id": f"{waits}-pr", "type": "pr", "repo": repo, "number": int(pr)},
            {"id": f"{waits}-answer", "type": "choice", "options": [{"id": "merge", "label": "Merge", "recommended": True}, {"id": "hold", "label": "Hold"}]},
        ],
    }


def slack_ts(out: str) -> str | None:
    found = SLACK_MESSAGE.search(out)
    return f"{found[1]}.{found[2]}" if found else None


def numbered(prs: list[str]) -> str:
    return " ".join(f"#{pr}" for pr in prs)


def downstack(open_rows: dict[str, dict], pr: str) -> set[str]:
    """`pr` and every open row below it, walking each base to the open row whose branch it is."""
    by_branch = {row["branch"]: number for number, row in open_rows.items() if row.get("branch")}
    chain: set[str] = set()
    below: str | None = pr
    while below is not None and below not in chain and below in open_rows:
        chain.add(below)
        below = by_branch.get(open_rows[below].get("base"))
    return chain


def stacked(open_rows: dict[str, dict]) -> set[str]:
    """Every open row whose base is another open row's branch, so it cannot land before that parent does."""
    branches = {row["branch"] for row in open_rows.values() if row.get("branch")}
    return {pr for pr, row in open_rows.items() if row.get("base") in branches}


def under_hold(open_rows: dict[str, dict], held: list[str]) -> set[str]:
    by_branch = {row["branch"]: pr for pr, row in open_rows.items() if row.get("branch")}
    covered = set()
    for pr in open_rows:
        below = pr
        while below is not None and below not in held:
            below = by_branch.get(open_rows[below].get("base"))
        if below is not None:
            covered.add(pr)
    return covered


def names_head(named: str, sha: str) -> bool:
    return bool(sha) and (sha.startswith(named) or named.startswith(sha))


def graded_head(text: str) -> str:
    """The head a grade or finder line names: its first short sha, so no register key it lists after the head reads as one."""
    found = SHORT_SHA.search(text)
    return found[0] if found else ""


def newest_naming(grades: list[dict], sha: str) -> dict | None:
    return max((record for record in grades if (head := graded_head(record["text"])) and names_head(head, sha)), key=lambda record: record["seq"], default=None)


def cause(line: str) -> str:
    return line.split(" ", 1)[0]


def reserved_row(line: str) -> tuple[str, str]:
    """The row key and the first sentence of its words from a `RESERVED-NO-ANSWER <key><TAB><sentence>` line."""
    key, _, sentence = line.split(" ", 1)[1].partition("\t")
    return key.strip(), sentence.strip()


def repo_prs(record: dict, repo: str) -> list[tuple[str, bool]]:
    """Each PR `record` names that may be in `repo`, with whether its ref says so: `owner/repo#N` refs to `repo`, and bare numbers, which name no repo."""
    named = []
    for ref in record.get("refs", {}).get("prs", []):
        if isinstance(ref, int):
            named.append((str(ref), False))
        elif (where := str(ref).rpartition("#"))[0].lower() == repo.lower():
            named.append((where[2], True))
    return named


def names_key(text: str, key: str) -> bool:
    return re.search(rf"(?<![\w/]){re.escape(key)}(?![\w/])", text) is not None


def names_pr(record: dict, pr: str) -> bool:
    return pr in {str(ref).rsplit("#", 1)[-1] for ref in record.get("refs", {}).get("prs", [])} or re.search(rf"#{pr}\b", record["text"]) is not None


def pr_name(pr: int | str) -> str:
    """A cci PR reference in prose: a bare number as `#12`, `owner/repo#12` as written."""
    return str(pr) if "#" in str(pr) else f"#{pr}"


def prefix_tops(prefix: list[str], rows: dict[str, dict]) -> list[str]:
    bases = {rows[pr].get("base") for pr in prefix}
    return [pr for pr in prefix if rows[pr].get("branch") not in bases]


def heads_query(repo: str, shas: list[str]) -> str:
    owner, name = repo.split("/")
    fields = " ".join(f'h{index}: object(expression: "{sha}") {{ ... on Commit {{ status {{ contexts {{ context state description }} }} }} }}' for index, sha in enumerate(shas))
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'


def same_head(one: str, other: str) -> bool:
    return one.startswith(other) or other.startswith(one)



@dataclass(frozen=True)
class Grade:
    """A head's fidelity verdict: whether it counts as ok, the grade that decides it, and why it does not count."""

    ok: bool
    record: dict | None
    why: str


@dataclass(frozen=True)
class Graded:
    """What one run of the check registry grades: each PR's head, the ledger rows, when the enqueue was accepted, and whether a refusing check may post its blocker or ask."""

    heads: dict[str, str]
    rows: dict[str, dict]
    accepted_at: datetime
    act: bool


def release_posted(incident: actions.Incident, key: str, step: str, seq: int, at: datetime) -> None:
    if incident.action(key).status == "accepted":
        incident.start(key, at)
        incident.complete(key, {"posted": {}, "at": actions.stamp(at)})
    incident.action(key).response["posted"][step] = seq


class Landing:
    """The merge queue: gate every tracked stack read-only through the repository adapter, run the check registry over each ready prefix, enqueue it once per set of heads in priority order, classify each ejection, verify by squash, route the restack above it, and send a landed merge-fast PR's release to its release owner."""

    def __init__(self, runner: Runner, config: dict):
        self.runner = runner
        self.shell = runner.shell
        self.book = runner.book
        self.repo = config["repo"]
        self.ledger = config["ledger"]
        self.checkout = Path(config["checkout"]).expanduser()
        self.holds = Path(config["holds"]).expanduser()
        self.release_owner = config["release_owner"]
        self.release_cli = release_cli(self.checkout, config)
        self.status_card = config.get("status_card", False)
        self.card_drawn = ""
        self.landed: set[str] = set()
        self.patch_ids: dict[tuple[str, str], str] = {}
        self.risky: dict[tuple[str, str], list[str]] = {}
        self.viewer = ""
        self.reported: dict[tuple[str, str, bool], list[str]] = {}
        self.read: dict[str, tuple[datetime, list[dict]]] = {}
        self.unread: dict[str, dict[str, str]] = {}
        self.reading = threading.Lock()
        self.labels_read: dict[str, bool] = {}
        self.row_heads: dict[str, str] = {}
        self.started = self.book.stamp()
        self.adapter = merge_queue.adapter(self.shell, self.checkout, config["adapter"], self.ledger_py, self.repo, self.ledger)
        self.book.ensure(LANDING, LANDING)

    def ledger_py(self, *argv: str) -> Done:
        return self.shell.run([sys.executable, str(SCRIPTS / "ledger.py"), "-C", str(self.checkout), *argv], env={"CLAUDE_LONG_RUNNING_DRIVE": self.runner.config.drive})

    def rulings_py(self, verb: str, *argv: str) -> Done:
        return self.shell.run([sys.executable, str(SCRIPTS / "rulings.py"), verb, "--repo", str(self.checkout), "--drive", self.runner.config.drive, *argv])

    def rows(self) -> dict[str, dict] | None:
        """Every ledger PR row by number, or None when the ledger cannot be listed: an unread ledger is not an empty one."""
        done = self.ledger_py("list", "--ledger", self.ledger, "--json")
        if done.code != 0:
            return None
        rows = {row["pr"]: row for row in json.loads(done.out)}
        self.row_heads = {pr: row.get("head", "") for pr, row in rows.items()}
        return rows

    def held(self, rows: dict[str, dict]) -> list[str] | None:
        """Every PR the holds file, a rules-review block, an open cci hold, or GitHub's `hold` label holds, from one read of the file, so the gate routes no blocker for it or for the PRs stacked above it; None when that read carries a line the desk cannot hold by, and then the caller gates and enqueues nothing."""
        entries = holds.read(self.holds)
        if self.holds_unread(entries, rows):
            return None
        numbers = holds.held(entries, rows) | {pr for pr, row in rows.items() if row.get("rules_blocked") or HOLD_LABEL in row.get("labels", "").split(",")}
        numbers |= cci.held_prs(self.runner.cci_tail, self.runner.config.drive).keys()
        return sorted(numbers, key=int)

    def card(self, rows: dict[str, dict]) -> dict:
        """The drive root card's rollup, refs, progress, and facts from the ledger and the lane registry, for `cc-slack dm-status --root-only`."""
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        landed = [pr for pr, row in rows.items() if row.get("state") == "landed"]
        queued = sorted({pr for prs in self.believed_queued(rows).values() for pr in prs}, key=int)
        held = set(holds.held(holds.read(self.holds), rows)) | {pr for pr, row in open_rows.items() if row.get("hold_reason") or HOLD_LABEL in row.get("labels", "").split(",")}

        def url(pr: str) -> str:
            return f"https://github.com/{self.repo}/pull/{pr}"

        def status(pr: str, row: dict) -> str:
            if pr in held:
                return "held"
            if row.get("rules_blocked"):
                return "rules-blocked"
            if pr in queued:
                return "queued"
            return row.get("mergeable_state") or "open"

        by_lane: dict[str, list[str]] = {lane: [] for lane in self.runner.orca.lanes()}
        for pr, row in open_rows.items():
            by_lane.setdefault(row.get("lane") or "unowned", []).append(pr)
        def detail(prs: list[str]) -> dict:
            if not prs:
                return {}
            ordered = sorted(prs, key=int)
            for count in range(len(ordered), 0, -1):
                text = numbered(ordered[:count]) + (f" +{len(ordered) - count} more" if count < len(ordered) else "")
                if len(text) <= CARD_DETAIL:
                    return {"detail": text}
            return {"detail": f"{len(ordered)} PRs"}

        lanes = [
            {"name": lane[:CARD_NAME], "status": f"{len(prs)} open PR{'' if len(prs) == 1 else 's'}" if prs else "no open PR", **detail(prs)}
            for lane, prs in sorted(by_lane.items())
        ]
        card = {
            "drive": self.runner.config.drive,
            "rollup": {"lanes": lanes[:CARD_ROLLUP], "prs": [{"url": url(pr), "label": f"#{pr} {row.get('lane', '')}".strip()[:CARD_LABEL], "status": status(pr, row)} for pr, row in sorted(open_rows.items(), key=lambda item: int(item[0]))][:CARD_ROLLUP]},
            "refs": [{"url": url(pr), "label": f"#{pr}"} for pr in queued][:CARD_REFS],
            "facts": [{"label": "Open PRs", "value": str(len(open_rows))}, {"label": "Queued", "value": str(len(queued))}, {"label": "Held", "value": str(len(held & open_rows.keys()))}, {"label": "Landed", "value": str(len(landed))}],
        }
        if total := len(landed) + len(open_rows):
            scale = min(1.0, CARD_STEPS / total)
            card["progress"] = {"step": round(len(landed) * scale), "total": round(total * scale), "label": "PRs landed"}
        return card

    def draw_card(self, rows: dict[str, dict]) -> None:
        """Redraw the drive's root status card whenever the ledger's picture of it changes; a refusal is escalated once per error."""
        card = json.dumps(self.card(rows), sort_keys=True)
        if card == self.card_drawn:
            return
        try:
            done = self.shell.run(["cc-slack", "dm-status", "--root-only", "--card", card])
        except OSError as error:
            done = Done(127, "", f"cc-slack did not start: {error}")
        if done.code:
            why = (done.err or done.out).strip()
            self.runner.escalate(f"card:{hashlib.sha1(why.encode()).hexdigest()[:8]}", "CARD-FAILED", "landing", f"the drive root card was not redrawn: {why} | post one status card with slack_dm_status, and the next pass redraws its root")
            return
        self.card_drawn = card

    def holds_unread(self, entries: list[holds.Entry], rows: dict[str, dict]) -> bool:
        """Whether `entries` carry a line the desk cannot hold by, each escalated once: a line no form matches, or a lane line naming a lane no ledger row carries in any state. Such a line may hold something the desk cannot name, so nothing is gated or enqueued on that read."""
        faults = {
            entry.line: f"no form matches the holds-file line `{entry.line}`, so the desk enqueues nothing until it is fixed"
            f" | in {self.holds} write `#<pr> <why>`, `lane:<name> <why>`, `<lane> HOLD <why>`, `HOLD <lane> <why>`, `hold:all <why>`, or `release #<pr>`"
            for entry in entries
            if entry.unknown
        }
        faults |= {
            line: f"the holds-file line `{line}` names lane {', '.join(lanes)}, which no ledger row carries, so it holds nothing and the desk enqueues nothing until it is fixed"
            f" | in {self.holds} hold each PR as `#<pr> <why>`, or delete the line"
            for line, lanes in holds.rowless(entries, rows).items()
        }
        for line, text in faults.items():
            self.runner.escalate(f"holds:{hashlib.sha1(line.encode()).hexdigest()[:8]}", "HOLDS-UNREAD", "landing", text)
        return bool(faults)

    def whole(self) -> bool:
        policy = self.runner.landing_policy()
        return bool(policy) and json.loads(policy.target)["rule"] == "whole"

    def trees(self, rows: dict[str, dict]) -> list[list[str]]:
        """Every tracked stack's tips, grouped by the root of their stack, ordered by `(priority, root PR)`: a tree's priority is the lowest ledger `priority` among its PRs."""
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        by_branch = {row["branch"]: pr for pr, row in open_rows.items() if row.get("branch")}
        tracked = {pr: row for pr, row in open_rows.items() if row.get("ours")}
        bases = {row.get("base") for row in tracked.values()}
        grouped: dict[str, list[str]] = {}
        for tip in sorted((pr for pr, row in tracked.items() if row.get("branch") not in bases), key=int):
            root = tip
            while (below := by_branch.get(open_rows[root].get("base"))) is not None:
                root = below
            grouped.setdefault(root, []).append(tip)
        return [grouped[root] for root in sorted(grouped, key=lambda root: (min(self.priority(open_rows, pr) for tip in grouped[root] for pr in downstack(open_rows, tip)), int(root)))]

    def priority(self, rows: dict[str, dict], pr: str) -> int:
        return int(rows.get(pr, {}).get("priority", DEFAULT_PRIORITY))

    def gate(self, rows: dict[str, dict], held: list[str]) -> None:
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        bases = {row.get("base") for row in open_rows.values()}
        self.labels_read = {key: True for key, admitted in self.labels_read.items() if admitted}
        admitted = self.admit({pr: row.get("head", "") for pr, row in open_rows.items() if not row.get("ours") and not row.get("unadmitted")})
        for pr, row in open_rows.items():
            if not row.get("ours") and pr not in admitted and row.get("branch") not in bases:
                self.runner.escalate(f"not-ours:{pr}@{row.get('head', '')}", "NOT-OURS", f"#{pr}", f"skipped: {NOT_ADMITTED}, so the desk never gates or enqueues it; {ADMIT_HOW.format(pr=pr)}")
        trees = [[tip] for tree in self.trees(rows) for tip in tree] if self.whole() else self.trees(rows)
        covered = under_hold(open_rows, held)
        children = stacked(open_rows)
        waiting = under_hold(open_rows, self.incident_rows(open_rows))
        trees = [kept for tree in trees if (kept := [tip for tip in tree if tip not in waiting])]
        with ThreadPoolExecutor(GATE_WORKERS) as pool:
            gated = [pair for pairs in pool.map(self.proven_stacks, trees) for pair in pairs]
        for tips, done in gated:
            verdicts = done.verdicts
            if done.code and not verdicts:
                self.gate_failed(tips, done)
                continue
            if done.would is not None:
                self.accept(tips, done.would, verdicts, rows)
            for pr, match in verdicts.items():
                if match["verdict"] == "BLOCKED" and pr not in rows and self.adapter.rider(match):
                    if pr not in self.admit({pr: match["sha"]}):
                        self.runner.escalate(f"unnamed:{pr}@{match['sha']}", "FOREIGN", numbered(tips), f"#{pr} would ride the batch, and {NOT_ADMITTED}; nothing was enqueued; {ADMIT_HOW.format(pr=pr)}")
                elif match["verdict"] == "BLOCKED" and pr in rows and pr not in covered:
                    stack = set(verdicts) | downstack(open_rows, pr)
                    if self.adapter.failing_required(match) and self.red_by_design(pr):
                        continue
                    gated_red = pr in children and self.adapter.failing_required(match) and self.gated_red(pr, match["sha"])
                    if self.retrying(pr, match, stack):
                        continue
                    if blocker := self.adapter.blocker(match, stack, gated_red):
                        self.route_blocker(pr, match["sha"], blocker, rows[pr], tips, stack, gated_red, self.adapter.failing_required(match))

    def red_by_design(self, pr: str) -> bool:
        done = self.shell.run(["gh", "pr", "view", pr, "--repo", self.repo, "--json", "body"])
        return done.code == 0 and RED_BY_DESIGN.match(json.loads(done.out)["body"] or "") is not None

    def gate_failed(self, tips: list[str], done: merge_queue.Gate) -> None:
        """Escalate `GATE-FAILED` for a check that read no verdict, naming only the tips that have not landed since the pass read the ledger."""
        if left := [tip for tip in tips if tip not in self.landed_now(tips)]:
            self.runner.escalate(f"gate:{','.join(left)}:{hashlib.sha1(done.last.encode()).hexdigest()[:8]}", "GATE-FAILED", numbered(left), f"the {self.adapter.kind} check exited {done.code} with no verdict: {done.last}")

    def landed_now(self, tips: list[str]) -> set[str]:
        """The tips whose ledger row reads landed once `ledger.py landed` settles them by their squash on the freshly fetched trunk."""
        self.ledger_py("landed", "--repo", self.repo, "--ledger", self.ledger, "--checkout", str(self.checkout), *(f"--pr={tip}" for tip in tips))
        rows = self.rows() or {}
        return {tip for tip in tips if rows.get(tip, {}).get("state") == "landed"}

    def retrying(self, pr: str, verdict: dict, stack: set[str]) -> bool:
        """Whether the queue's ejection of a head this desk enqueued is a flaky check the desk retries itself, or whose retry the queue still holds, so the lane is not asked for a new head."""
        ejection = self.adapter.ejection(verdict, stack)
        if not ejection or ejection.kind != merge_queue.FLAKY_CHECK:
            return False
        sha = self.row_heads.get(pr) or verdict["sha"]
        enqueued_here = any(same_head(enqueued_heads(action.action_id).get(pr) or "-", sha) for action in self.book.actions(LANDING, kind="enqueue"))
        return enqueued_here and (not self.flake_retried(pr, sha) or self.flake_retried(pr, sha, BELIEVED_QUEUED | {"flake-refused", "stranded", "unsettled", "failed"}))

    def red_cause(self, pr: str, sha: str) -> str:
        """The red jobs on `pr`'s head outside the gates, each with its status description and run numbers and durations folded, so the same job failing the same way on the next head reads the same; empty when the statuses are unread or no such job is red."""
        statuses = self.head_statuses(pr, sha) or []
        red = sorted({(status["context"], RUN_NUMBERS.sub("#", status["description"])) for status in statuses if status["state"] in RED_STATES and status["context"] not in GATE_STATUSES})
        return "; ".join(f"{context}: {cause}" for context, cause in red)

    def gated_red(self, pr: str, sha: str) -> bool:
        """Whether a stacked PR's failing required check comes only from a gate status or from its unlanded base: no status but a gate's is red on its own head. A status read that fails counts as gated for this pass, and the next pass reads again."""
        statuses = self.head_statuses(pr, sha)
        return statuses is None or not any(status["state"] in RED_STATES and status["context"] not in GATE_STATUSES for status in statuses)

    def proven_stacks(self, tips: list[str]) -> list[tuple[list[str], Done]]:
        gated = []
        while tips:
            first, *rest = tips
            alone = self.checked([first])
            members = set(alone.verdicts)
            shared = [first, *(tip for tip in rest if tip in members)]
            joined = alone if len(shared) == 1 else self.checked(shared)
            if joined.code and not joined.verdicts:
                gated += [([first], alone), *(([tip], self.checked([tip])) for tip in shared[1:])]
            else:
                gated.append((shared, joined))
            tips = [tip for tip in rest if tip not in members]
        return gated

    def checked(self, tips: list[str]) -> merge_queue.Gate:
        return self.adapter.check(tips, self.whole(), self.row_heads)

    @property
    def fidelity_lanes(self) -> list[str]:
        configured = json.loads(self.runner.config.source.read_text())["landing"]["fidelity_lane"]
        return [configured] if isinstance(configured, str) else list(configured)

    @property
    def fidelity_names(self) -> str:
        return " or ".join(self.fidelity_lanes)

    def grades(self, pr: str) -> list[dict]:
        """Every grade a fidelity lane posted about `pr`, read by its `owner/repo#N` reference, which cci also matches to a post that named the bare number."""
        drive, lanes = self.runner.config.drive, self.fidelity_lanes
        reviews = [record for lane in lanes for record in cci.records(self.runner.cci_tail, drive, "--kind=review", "--lane", lane, "--pr", f"{self.repo}#{pr}")]
        return [record for record in reviews if record["kind"] == "review" and record["drive"] == drive and record["lane"] in lanes and record["text"].startswith(FIDELITY_GRADES) and pr in dict(repo_prs(record, self.repo))]

    def standing(self, grades: list[dict], sha: str) -> list[dict]:
        """Each fidelity lane's newest grade naming `sha`."""
        return [grade for lane in self.fidelity_lanes if (grade := newest_naming([record for record in grades if record["lane"] == lane], sha))]

    def fidelity(self, pr: str, sha: str, base: str) -> Grade:
        """Whether `pr` at `sha` counts as graded ok. Each fidelity lane's newest grade naming the head stands for that lane, so one lane's `missing` blocks until that lane grades the head ok, whatever another lane posts; an ok counts as posted (4236611). With no grade naming the head, the ok of an older head whose lanes all stand at ok carries when both patches against `base` match."""
        grades = self.grades(pr)
        newest = self.standing(grades, sha)
        if missing := max((grade for grade in newest if grade["text"].startswith(FIDELITY_MISSING)), key=lambda record: record["seq"], default=None):
            return Grade(False, missing, f"{missing['lane']} graded this head on cci #{missing['seq']}: {missing['text']}")
        if newest:
            return Grade(True, max(newest, key=lambda record: record["seq"]), "")
        none = Grade(False, None, f"no review from {self.fidelity_names} starting `{FIDELITY_OK}` names this head")
        if not (sha and base and any(grade["text"].startswith(FIDELITY_OK) for grade in grades)):
            return none
        carried = self.carried(pr, sha, base, grades)
        return Grade(True, carried[0], f"{FIDELITY_OK} at {sha}, patch-identical to {carried[1]}") if carried else none

    def carried(self, pr: str, sha: str, base: str, grades: list[dict]) -> tuple[dict, str] | None:
        """The newest ok of an older head whose fidelity lanes all stand at ok, with that head, when its patch against `base` is identical to `sha`'s."""
        graded = dict.fromkeys(
            head
            for record in sorted(grades, key=lambda record: record["seq"], reverse=True)
            if record["text"].startswith(FIDELITY_OK) and (head := graded_head(record["text"])) and not names_head(head, sha)
        )
        oks = {
            named: max(stand, key=lambda record: record["seq"])
            for named in graded
            if (stand := self.standing(grades, named)) and all(grade["text"].startswith(FIDELITY_OK) for grade in stand)
        }
        if not oks or not (ours := self.patch_id(pr, sha, base, escalate=True)):
            return None
        return next(((grade, named) for named, grade in oks.items() if self.patch_id(pr, named, base, escalate=False) == ours), None)

    def patch_id(self, pr: str, sha: str, base: str, escalate: bool) -> str:
        """`git patch-id --stable` of `sha`'s diff against its merge base with `base`, read from GitHub's compare; empty when GitHub refuses the read, which escalates once for the PR's own head and is silent for a short sha a grade names that is no commit."""
        if (base, sha) in self.patch_ids:
            return self.patch_ids[(base, sha)]
        diff = self.shell.run(["gh", "api", f"repos/{self.repo}/compare/{base}...{sha}", "-H", DIFF_MEDIA])
        if diff.code != 0:
            if escalate:
                last = (diff.err or diff.out).strip().splitlines()[-1:] or [f"exit {diff.code}"]
                self.runner.escalate(f"patch:{pr}:{sha}", "PATCH-READ-FAILED", f"#{pr}", f"GitHub refused the diff of {base}...{sha}: {last[0]}; no fidelity ok carries to this head until it reads, and the runner reads it again on the next pass")
            return ""
        done = self.shell.run(["git", "patch-id", "--stable"], stdin=diff.out)
        self.patch_ids[(base, sha)] = done.out.split()[0] if done.code == 0 and done.out.strip() else ""
        return self.patch_ids[(base, sha)]

    def head_statuses(self, pr: str, sha: str) -> list[dict] | None:
        return self.read_heads({sha: pr}).get(sha)

    def cached(self, sha: str) -> list[dict] | None:
        now = self.runner.now()
        with self.reading:
            return next((statuses for head, (at, statuses) in self.read.items() if same_head(head, sha) and now - at < HEAD_FLOOR), None)

    def read_heads(self, heads: dict[str, str]) -> dict[str, list[dict] | None]:
        """The commit statuses of each head in `heads`, a sha to the PR it heads: one GraphQL query per `HEADS_PER_QUERY` heads not read in the last `HEAD_FLOOR`. A head GitHub refuses or does not know reads None and joins `unread` under its cause, which `report_unread` logs at the end of the pass."""
        found = {sha: statuses for sha in heads if (statuses := self.cached(sha)) is not None}
        due = sorted(set(heads) - set(found))
        for start in range(0, len(due), HEADS_PER_QUERY):
            batch = due[start : start + HEADS_PER_QUERY]
            done = self.shell.run(["gh", "api", "graphql", "-f", f"query={heads_query(self.repo, batch)}"])
            last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
            commits = json.loads(done.out)["data"]["repository"] if done.code == 0 else {}
            now = self.runner.now()
            for index, sha in enumerate(batch):
                if (commit := commits.get(f"h{index}")) is None:
                    why = f"GitHub refused the statuses: {last[0]}" if done.code else f"GitHub knows no such commit in {self.repo}"
                    with self.reading:
                        self.unread.setdefault(why, {})[heads[sha]] = sha
                    continue
                statuses = [{"context": context["context"], "state": context["state"].lower(), "description": context["description"] or ""} for context in (commit["status"] or {"contexts": []})["contexts"]]
                with self.reading:
                    self.read[sha] = (now, statuses)
                found[sha] = statuses
        return found

    def report_unread(self) -> None:
        """Log one `STATUS-READ-FAILED` per cause for the heads this pass could not read, with their count and each PR and head, once per cause and set of heads, so one GitHub blip across many PRs reads as one event."""
        for why, prs in sorted(self.unread.items()):
            named = " ".join(f"#{pr} {prs[pr][:10]}" for pr in sorted(prs, key=int))
            key = f"status-read:{hashlib.sha1(f'{why} {named}'.encode()).hexdigest()[:12]}"
            self.runner.escalate(key, "STATUS-READ-FAILED", "landing", f"{why}, for {len(prs)} head(s): {named}; the runner reads them again on the next pass")
        self.unread = {}

    def quota_low(self) -> bool:
        """Whether the REST or GraphQL quota of the installation this desk reads GitHub through is under `QUOTA_FLOOR` of its limit, from one `rate_limit` read GitHub does not count. Each quota under the floor logs one `GITHUB-BACKOFF` per reset; a read that fails logs one `QUOTA-READ-FAILED` per hour and reads as not low."""
        done = self.shell.run(["ccx", "vcs", "gh", "--", "api", "rate_limit"], env={"GH_REPO": self.repo})
        if done.code != 0:
            last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
            self.runner.escalate(f"quota-read:{self.runner.now():%Y%m%d%H}", "QUOTA-READ-FAILED", "landing", f"the GitHub quota read failed: {last[0]}; the pass runs without it")
            return False
        resources = json.loads(done.out)["resources"]
        low = {name: resources[name] for name in QUOTA_RESOURCES if resources[name]["remaining"] < resources[name]["limit"] * QUOTA_FLOOR}
        for name, quota in low.items():
            reset = pacific(datetime.fromtimestamp(quota["reset"], timezone.utc))
            self.runner.escalate(
                f"github-backoff:{name}:{quota['reset']}",
                "GITHUB-BACKOFF",
                "landing",
                f"GitHub's {name} quota has {quota['remaining']} of {quota['limit']} calls left, under the {QUOTA_FLOOR:.0%} floor; the desk reads nothing from GitHub until it resets at {reset}, and accepted enqueues and root go records run after the reset",
            )
        return bool(low)

    def ungraded(self, heads: dict[str, str], rows: dict[str, dict]) -> dict[str, Grade]:
        grades = {pr: self.fidelity(pr, sha, rows[pr].get("base") or "") for pr, sha in heads.items()}
        return {pr: grade for pr, grade in grades.items() if not grade.ok}

    def refuse_ungraded(self, heads: dict[str, str], ungraded: dict[str, Grade], rows: dict[str, dict]) -> None:
        for pr, grade in ungraded.items():
            key = f"fidelity:{pr}:{heads[pr]}:{grade.record['seq'] if grade.record else 'ungraded'}"
            if self.book.known(LANDING, key):
                continue
            why = grade.why
            owner = self.owner(pr, rows[pr])
            to = ",".join([*(self.reach(owner) if owner else []), *self.fidelity_lanes])
            self.post_blocker(key, to, f"#{pr} {heads[pr]}: nothing in {numbered(list(heads))} was enqueued; {why}", pr)

    def accept(self, tips: list[str], prefix: list[str], verdicts: dict[str, re.Match], rows: dict[str, dict]) -> None:
        """One enqueue per exact set of prefix heads, every one a PR a drive lane opened and the fidelity lane graded ok at that head; a fresh attempt only after every earlier one enqueued nothing."""
        base = enqueue_key(prefix, verdicts)
        if foreign := [pr for pr in prefix if not rows.get(pr, {}).get("ours")]:
            admitted = self.admit({pr: rows.get(pr, {}).get("head") or (verdicts[pr]["sha"] if pr in verdicts else "") for pr in foreign if not rows.get(pr, {}).get("unadmitted")})
            if unclaimed := [pr for pr in foreign if pr not in admitted]:
                self.runner.escalate(f"foreign:{base}", "FOREIGN", numbered(tips), f"the {self.adapter.kind} check would also enqueue {', '.join('#' + pr for pr in unclaimed)}, which the drive did not admit; nothing was enqueued")
            return
        enqueues = self.book.actions(LANDING, kind="enqueue")
        ejected = {pr: sha for action in enqueues if action.response for pr, sha in action.response.get("ejected", {}).items()}
        if any(sha and ejected.get(pr) == sha for pr, sha in enqueued_heads(base).items()):
            return
        pulled = {(action.response["pulled"]["pr"], enqueued_heads(action.action_id).get(action.response["pulled"]["pr"])) for action in enqueues if (action.response or {}).get("outcome") == PULLED}
        if any((pr, sha) in pulled for pr, sha in enqueued_heads(base).items()):
            return
        if self.awaiting_go(enqueues, enqueued_heads(base)):
            return
        attempts = [action for action in enqueues if action.action_id.split("#")[0] == base]
        if any(action.status != "completed" or action.response["outcome"] not in RETRYABLE for action in attempts):
            return
        if self.failing(ACCEPT_CHECKS, Graded(enqueued_heads(base), rows, self.runner.now(), act=True)):
            return
        self.book.accept(LANDING, f"{base}#{len(attempts) + 1}", "enqueue", ",".join(prefix), self.authority(), self.runner.now() + timedelta(minutes=self.runner.config.enqueue_minutes))

    def authority(self) -> str:
        policy = self.runner.landing_policy()
        return f"{policy.authority_ref} {json.loads(policy.target)['revision']}" if policy else ""

    @property
    def landing_config(self) -> dict:
        return json.loads(self.runner.config.source.read_text())["landing"]

    @property
    def checks(self) -> list[str]:
        """The check registry in the order `landing.checks` names it, every built-in when it names none; re-read on every use."""
        return list(self.landing_config.get("checks", merge_queue.CHECKS))

    def failing(self, stage: tuple[str, ...], graded: Graded) -> tuple[str, dict[str, merge_queue.State]] | None:
        """The first configured check of `stage` that does not pass every head, with each head's state; a refusing check posts its own blocker or ask when `graded.act`."""
        for name in self.checks:
            if name in stage and (states := {pr: state for pr, state in self.check(name, graded).items() if state.state != merge_queue.PASS}):
                return name, states
        return None

    def check(self, name: str, graded: Graded) -> dict[str, merge_queue.State]:
        return {"hold": self.check_hold, "rules-review": self.check_rules_review, "rulings": self.check_rulings, "fidelity": self.check_fidelity, "finder": self.check_finder, "merge-mode": self.check_merge_mode}[name](graded)

    def check_hold(self, graded: Graded) -> dict[str, merge_queue.State]:
        return {pr: merge_queue.State(merge_queue.FAIL, reason) for pr, reason in self.holding(list(graded.heads), graded.rows, graded.accepted_at).items()}

    def check_rules_review(self, graded: Graded) -> dict[str, merge_queue.State]:
        return {
            pr: merge_queue.State(merge_queue.FAIL, f"rules-review holds {sha[:10]}: its review is pending or carries a finding no `rules-override` waived")
            for pr, sha in graded.heads.items()
            if graded.rows.get(pr, {}).get("rules_blocked")
        }

    def rulings_report(self, pr: str, sha: str, incident: bool = False) -> list[str]:
        """The cause lines `rulings.py check-pr` prints for `pr` at `sha`, run once per head a pass. A run that exits with a code other than 0 or 1, whose exit code disagrees with its cause lines, or that prints a line whose first word names no cause, reads as no cause; it and every `BLOCKER` line go to the root once as `RULINGS-UNREAD`. The check fails open on what it cannot read (0afd2f6), and a cause it did read stands beside a `BLOCKER` line."""
        key = (pr, sha, incident)
        if key not in self.reported:
            done = self.rulings_py("check-pr", "--pr", f"{self.repo}#{pr}", "--head", sha, *(("--incident",) if incident else ()))
            lines = [line.strip() for line in done.out.splitlines() if line.strip()]
            causes = [line for line in lines if cause(line) in CHECK_PR_CAUSES]
            unread = [line for line in lines if cause(line) == CHECK_PR_BLOCKER]
            if done.code not in (0, 1) or len(causes) + len(unread) < len(lines) or (done.code == 1) != bool(causes):
                last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
                causes, unread = [], [f"check-pr exited {done.code}: {last[0]}"]
            if unread:
                text = "; ".join(unread)
                self.runner.escalate(f"rulings-unread:{pr}:{sha[:10]}:{hashlib.sha1(PINNED_REF_PID.sub(r"\1", text).encode()).hexdigest()[:8]}", "RULINGS-UNREAD", f"#{pr}", f"`rulings.py check-pr` did not read everything for #{pr} at {sha[:10]}, so the head passes the rulings check: {text}")
            self.reported[key] = causes
        return self.reported[key]

    def refuse_head(self, key: str, pr: str, graded: Graded, why: str) -> None:
        """Post one blocker per `key` to the lane that owns `pr`, and the root when that lane has no live session or there is none."""
        if self.book.known(LANDING, key):
            return
        owner = self.owner(pr, graded.rows.get(pr, {}))
        self.post_blocker(key, ",".join(self.reach(owner) if owner else [ROOT]), f"#{pr} {graded.heads[pr]}: nothing in {numbered(list(graded.heads))} was enqueued; {why}", pr)

    def warn_head(self, key: str, pr: str, graded: Graded, why: str) -> None:
        """Post one `note` warning per `key` to the lane that owns `pr`, and the root when that lane has no live session or there is none."""
        if self.book.known(LANDING, key):
            return
        owner = self.owner(pr, graded.rows.get(pr, {}))
        self.post_record("note", key, ",".join(self.reach(owner) if owner else [ROOT]), f"#{pr} {graded.heads[pr]}: WARNING {why}", pr)

    def check_rulings(self, graded: Graded) -> dict[str, merge_queue.State]:
        """No head fails here (4236611): each cause `rulings.py check-pr` prints for a head is a warning, one `note` record to its lane per head and set of causes. A reserved touch is merge mode's to settle: it sends the card whose tap authorizes it, and holds the head until then."""
        if graded.act:
            for pr, sha in graded.heads.items():
                if warnings := [line for line in self.rulings_report(pr, sha) if cause(line) != RESERVED_TOUCH]:
                    text = f"`rulings.py check-pr` warns at {sha[:10]}, and the head is not held for it: {'; '.join(warnings)}"
                    self.warn_head(f"rulings:{pr}:{sha}:{hashlib.sha1(text.encode()).hexdigest()[:8]}", pr, graded, text)
        return {}

    def finder_records(self, pr: str) -> list[dict]:
        drive = self.runner.config.drive
        records = cci.records(self.runner.cci_tail, drive, "--kind=review", "--pr", f"{self.repo}#{pr}")
        return [record for record in records if record["kind"] == "review" and record["drive"] == drive and record["text"].startswith(FINDER_GRADES) and pr in dict(repo_prs(record, self.repo))]

    def finder(self, pr: str, sha: str) -> dict | None:
        """The newest `finder:` review record about `pr` naming `sha`, from whichever lane ran the pass (reference/finder-brief.md)."""
        return newest_naming(self.finder_records(pr), sha)

    def finder_carried(self, pr: str, sha: str, base: str) -> bool:
        records = self.finder_records(pr)
        heads = dict.fromkeys(head for record in records if (head := graded_head(record["text"])) and not names_head(head, sha))
        oks = [head for head in heads if (newest := newest_naming(records, head)) and newest["text"].startswith(FINDER_OK)]
        if not base or not oks or not (ours := self.patch_id(pr, sha, base, escalate=True)):
            return False
        return any(self.patch_id(pr, head, base, escalate=False) == ours for head in oks)

    def check_finder(self, graded: Graded) -> dict[str, merge_queue.State]:
        """Each head with no finder record, or whose newest names an open major, fails `FINDER-PENDING`, with one blocker to its lane per head (E4.1)."""
        states = {}
        for pr, sha in graded.heads.items():
            if (verdict := self.finder(pr, sha)) and verdict["text"].startswith(FINDER_OK):
                continue
            if not verdict and self.finder_carried(pr, sha, graded.rows[pr].get("base") or ""):
                continue
            why = f"{verdict['lane']} posted on cci #{verdict['seq']}: {verdict['text']}" if verdict else "no `finder:` review record names this head; run the sol finder reference/finder-brief.md names and post its record"
            states[pr] = merge_queue.State(merge_queue.FAIL, f"FINDER-PENDING #{pr}: {why}")
        if graded.act:
            for pr, state in states.items():
                self.refuse_head(f"finder:{pr}:{graded.heads[pr]}", pr, graded, state.reason)
        return states

    def check_fidelity(self, graded: Graded) -> dict[str, merge_queue.State]:
        ungraded = self.ungraded(graded.heads, graded.rows)
        if graded.act and ungraded:
            self.refuse_ungraded(graded.heads, ungraded, graded.rows)
        return {pr: merge_queue.State(merge_queue.FAIL, grade.why) for pr, grade in ungraded.items()}

    def check_merge_mode(self, graded: Graded) -> dict[str, merge_queue.State]:
        if (asks := self.owner_asks(graded.heads)) is None:
            return {pr: merge_queue.State(merge_queue.PENDING, "merge mode high-risk reads the changed files first, and they did not read") for pr in graded.heads}
        waiting = self.unanswered(graded.heads, asks)
        if graded.act and waiting:
            self.ask_owner(graded.heads, waiting)
        return {pr: merge_queue.State(merge_queue.PENDING, f"merge mode {self.merge_mode} waits for the owner's Merge answer at {graded.heads[pr][:7]}") for pr in waiting}

    @property
    def merge_mode(self) -> str:
        return self.landing_config["merge_mode"]

    def owner_asks(self, heads: dict[str, str]) -> dict[str, list[str]] | None:
        """The PRs of `heads` the merge mode leaves for the owner to answer, each with the high-risk paths that put it there: every one under `every`, under `high-risk` each whose changed files a `landing.high_risk_paths` glob names, and in every mode each whose head touches a reserved row no answer authorizes yet. None when a changed-files read failed, and then nothing in the prefix is enqueued that pass."""
        mode = self.merge_mode
        if mode == "auto" or self.lands_green:
            asks = {}
        elif mode == "every":
            asks = {pr: [] for pr in heads}
        else:
            risky = {pr: self.risky_paths(pr, sha) for pr, sha in heads.items()}
            if any(paths is None for paths in risky.values()):
                return None
            asks = {pr: paths for pr, paths in risky.items() if paths}
        return asks | {pr: [] for pr, sha in heads.items() if pr not in asks and self.reserved_touches(pr, sha)}

    @property
    def lands_green(self) -> bool:
        """Whether this repository's PRs land once green with no merge card: the GitHub account the runner acts as owns it (de6c88c). A login that does not read is read again on the next use, and the repository follows its merge mode meanwhile."""
        if not self.viewer:
            done = self.shell.run(["gh", "api", "user", "--jq", ".login"])
            self.viewer = done.out.strip() if done.code == 0 else ""
        return bool(self.viewer) and self.repo.split("/")[0].casefold() == self.viewer.casefold()

    def reserved_touches(self, pr: str, sha: str) -> list[tuple[str, str]]:
        """Each reserved row `pr` touches at `sha` that no answer authorizes yet, with the first sentence of its words: `check-pr`'s `RESERVED-NO-ANSWER` lines."""
        return [reserved_row(line) for line in self.rulings_report(pr, sha) if cause(line) == RESERVED_TOUCH]

    def risky_paths(self, pr: str, sha: str) -> list[str] | None:
        """The changed files of `pr` at `sha` that a `landing.high_risk_paths` glob names, read once per head; None when GitHub refuses the read, which escalates once per head."""
        if (pr, sha) not in self.risky:
            done = self.shell.run(["gh", "api", "--paginate", f"repos/{self.repo}/pulls/{pr}/files", "--jq", ".[].filename"])
            if done.code:
                last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
                self.runner.escalate(f"files:{pr}:{sha}", "FILES-READ-FAILED", f"#{pr}", f"GitHub refused the changed files of #{pr}: {last[0]}; merge mode high-risk enqueues nothing in its prefix until they read")
                return None
            globs = self.landing_config["high_risk_paths"]
            self.risky[(pr, sha)] = [name for name in done.out.splitlines() if any(fnmatch(name, glob) for glob in globs)]
        return self.risky[(pr, sha)]

    def head_gos(self, pr: str, head: str) -> list[dict]:
        drive = self.runner.config.drive
        gos = [record for record in cci.records(self.runner.cci_tail, drive, "--lane", ROOT, "--kind=go") if (record["kind"], record["lane"], record["drive"]) == ("go", ROOT, drive)]
        return [record for record in gos if names_pr(record, pr) and any(names_head(named, head) for named in SHORT_SHA.findall(record["text"]))]

    def merged_by_owner(self, heads: dict[str, str], prs: list[str]) -> dict[str, datetime]:
        """The PRs of `prs` with an owner Merge answer at their head, a root `go` record on this drive that names the PR and its head by a short sha, each with its newest such answer's time."""
        return {pr: max(actions.parse_stamp(record["at"]) for record in gos) for pr in prs if (gos := self.head_gos(pr, heads[pr]))}

    def carded(self, pr: str, head: str) -> dict[str, datetime]:
        """Each reserved row a merge card the runner logged for `pr` at `head` named, with the time of the first card naming it."""
        prefix = f"escalation:merge-ask:{pr}@{head}"
        named: dict[str, datetime] = {}
        for action in self.book.actions(RUNNER, kind="escalation"):
            if action.action_id.startswith(prefix):
                at = actions.parse_stamp(action.accepted_at)
                for key in action.action_id.removeprefix(prefix).split(":")[1:]:
                    named[key] = min(named.get(key, at), at)
        return named

    def unauthorized(self, pr: str, head: str, answered: datetime) -> list[tuple[str, str]]:
        """Each reserved row `pr` touches at `head` that no `authorizes:<key>` answer covers, no go at the head names by key, and no merge card named before the owner's Merge answer at `answered` (4236611)."""
        carded = self.carded(pr, head)
        named = " ".join(record["text"] for record in self.head_gos(pr, head))
        return [(key, sentence) for key, sentence in self.reserved_touches(pr, head) if not names_key(named, key) and (key not in carded or carded[key] > answered)]

    def unanswered(self, heads: dict[str, str], asks: dict[str, list[str]]) -> dict[str, list[str]]:
        """The PRs of `asks` still waiting on the owner: no Merge answer at their head yet, or a reserved row the head touches that the answer does not cover, because no card named the row before it."""
        if not asks:
            return {}
        merged = self.merged_by_owner(heads, list(asks))
        return {pr: paths for pr, paths in asks.items() if pr not in merged or self.unauthorized(pr, heads[pr], merged[pr])}

    def batch_posted(self) -> set[str] | None:
        """The PRs a recorded batch post lists: the `#<n>` numbers in the note of each ledger `progress` entry carrying an artifact, the post's permalink (8908f83). None when the ledger does not read, which escalates once per error."""
        done = self.ledger_py("show", "--ledger", self.ledger, "--json")
        if done.code:
            last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
            self.runner.escalate(f"batch-read:{hashlib.sha1(last[0].encode()).hexdigest()[:8]}", "BATCH-READ-FAILED", "landing", f"the ledger did not read for its batch posts: {last[0]}; no MERGE-ASK goes out until it reads")
            return None
        entries = [json.loads(value) for row in json.loads(done.out)["rows"] for name, value in row["fields"].items() if name.startswith("progress_")]
        return {pr for entry in entries if entry["artifacts"] for pr in re.findall(r"#(\d+)\b", entry["note"])}

    def merge_card(self, pr: str, head: str, detail: str) -> str | None:
        """Ask the merge question for `pr` at `head` through `cc-present ask` in the drive root's session, so it lands on the root's board and in the root's Slack status thread like every other card, waiting on `merge:<pr>:<short sha>` with Merge recommended and Hold; asking again under the same id edits the card in place. The card's Slack ts, or None when it did not post, which logs one `MERGE-CARD-FAILED` per error and tries again next pass."""
        waits = f"merge:{pr}:{head[:7]}"
        posted = self.book.load(LANDING).facts.get(MERGE_CARDS, {}).get(waits)
        if posted and posted["question"] == detail:
            return posted["ts"]
        entry = next((entry for entry in drive.drives() if drive.cci_drive(entry) == self.runner.config.drive and entry["sessions"]), None)
        if not entry:
            self.runner.escalate("merge-card:unset", "MERGE-CARD-FAILED", "landing", f"no drive registry entry for {self.runner.config.drive} names a root session, so no merge card posts | run `drive.py start --drive <drive> --ledger <ledger>` from the root session; the next pass posts the cards")
            return None
        view = self.shell.run(["gh", "pr", "view", pr, "--repo", self.repo, "--json", MERGE_CARD_FIELDS])
        done = view if view.code else self.ask_card(merge_card_block(self.repo, pr, waits, detail, json.loads(view.out)), entry["sessions"][-1])
        if done.code or not (ts := slack_ts(done.out)):
            why = ((done.err or done.out).strip().splitlines() or [f"exit {done.code}"])[-1]
            cause = VOLATILE.sub("", why)
            self.runner.escalate(f"merge-card:{pr}:{head[:7]}:{hashlib.sha1(cause.encode()).hexdigest()[:8]}", "MERGE-CARD-FAILED", f"#{pr}", f"the merge card for #{pr} at {head[:7]} did not post: {why}; the next pass tries again")
            return None
        self.book.edit(LANDING, lambda incident: incident.facts.setdefault(MERGE_CARDS, {}).update({waits: {"ts": ts, "question": detail}}))
        return ts

    def ask_card(self, card: dict, session: str) -> Done:
        try:
            return self.shell.run(["cc-present", "ask", "-", "--session", session], stdin=json.dumps(card))
        except OSError as error:
            return Done(127, "", f"cc-present did not start: {error}")

    def ask_owner(self, heads: dict[str, str], waiting: dict[str, list[str]]) -> None:
        """Ask one merge card per waiting PR and head in the drive root's session, and log one `MERGE-ASK` naming it for the root, who answers the tap; nothing in the prefix is enqueued until a root `go` names each waiting head. A head touching a reserved row gets the row's key and first sentence in its card's question, whose tap is the authorization. A PR no recorded batch post lists gets no card: the batch logs one `BATCH-POST-OWED` instead (8908f83)."""
        mode = self.merge_mode
        merged = self.merged_by_owner(heads, list(waiting))
        asking = [pr for pr in waiting if pr not in merged or self.unauthorized(pr, heads[pr], merged[pr])]
        if not asking or (posted := self.batch_posted()) is None:
            return
        if owed := [pr for pr in asking if pr not in posted]:
            batch = " ".join(f"{pr}@{heads[pr]}" for pr in owed)
            self.runner.escalate(
                f"batch-post:{hashlib.sha1(batch.encode()).hexdigest()[:12]}",
                "BATCH-POST-OWED",
                numbered(owed),
                f"{numbered(owed)} are ready, and no batch post recorded on the ledger lists them, so no MERGE-ASK goes out (8908f83)"
                f" | post the batch, then run `ledger.py progress --ledger {self.ledger} --ask <the ask the batch answers> --note \"{numbered(owed)}\" --artifact <permalink>`; the cards follow on the next pass",
            )
            asking = [pr for pr in asking if pr not in owed]
        for pr in asking:
            paths, short = waiting[pr], heads[pr][:7]
            waits = f"merge:{pr}:{short}"
            reserved = self.reserved_touches(pr, heads[pr])
            rows_named = "".join(f":{key}" for key, _ in reserved)
            why = f"It changes {', '.join(paths[:3])}{' and more' if len(paths) > 3 else ''}." if paths else "Every PR waits for your answer." if mode == "every" and not self.lands_green else "It touches a reserved rule."
            touched = "".join(f" It touches your rule \"{sentence}\" Merging also allows it." for _, sentence in reserved)
            if not (ts := self.merge_card(pr, heads[pr], f"{why}{touched}")):
                continue
            authorize = "".join(f"; the card quotes {key} and its words, and the Merge go authorizes it" for key, _ in reserved)
            self.runner.escalate(
                f"merge-ask:{pr}@{heads[pr]}{rows_named}",
                "MERGE-ASK",
                f"#{pr}",
                f"#{pr} {heads[pr]} is ready, and merge mode {mode} keeps {numbered(list(heads))} out of the queue until the owner answers the merge card at ts {ts} in the root's status thread or on its board, waiting on `{waits}`"
                f" | Merge posts `cci post --kind go --pr {pr} --text \"merge #{pr} at {short}\"`{authorize}, Hold runs `holds.py add --pr {pr}`, the text the owner types in Other's modal reaches you as the card's capture and merges nothing, so classify it as a capture,"
                f" and an Explain more tap resolves nothing: post `cci post --kind ask --to root --text \"explain more: {waits}\"` and answer in plain words with `cc-present reply --block {waits} --body <answer>`, which replies on the board and under the Slack card with the link back to it",
            )

    def admit(self, heads: dict[str, str]) -> set[str]:
        """The PRs in `heads`, a PR to its head, that carry the drive's label on GitHub, each registered under its head branch so the next refresh rows it as ours. GitHub is read once per PR and head in a pass, and again in the next pass for a PR it did not admit; record text never admits a PR."""
        for pr, head in heads.items():
            if f"{pr}@{head}" not in self.labels_read:
                self.read_label(pr, head)
        return {pr for pr, head in heads.items() if self.labels_read.get(f"{pr}@{head}")}

    def read_label(self, pr: str, head: str) -> None:
        """Register `pr` when it carries the drive's label and remember the answer for its head; a failed read is asked again on the next call."""
        label = f"{DRIVE_LABEL}{self.runner.config.drive}"
        done = self.shell.run(["gh", "pr", "view", pr, "--repo", self.repo, "--json", "labels,headRefName"])
        if done.code:
            return
        view = json.loads(done.out)
        if label not in {item["name"] for item in view["labels"]}:
            self.labels_read[f"{pr}@{head}"] = False
            return
        registered = self.ledger_py("register", "--ledger", self.ledger, "--repo", self.repo, "--lane", view["headRefName"], "--pr", pr)
        if registered.code:
            last = (registered.err or registered.out).strip().splitlines()[-1:] or [f"exit {registered.code}"]
            self.runner.escalate(f"admit:{pr}@{head}", "ADMIT-FAILED", f"#{pr}", f"ledger.py register refused #{pr}, which carries {label}: {last[0]}; the desk gates it once a lane registers it")
        self.labels_read[f"{pr}@{head}"] = not registered.code

    def awaiting_go(self, enqueues: list[actions.Action], heads: dict[str, str]) -> bool:
        """Whether a head in `heads` the queue dropped for a failure outside its stack still waits for a root `go` record naming its PR, posted after the newest such drop."""
        dropped: dict[str, datetime] = {}
        for action in enqueues:
            for pr, sha in (action.response or {}).get("awaiting_go", {}).items():
                if sha and heads.get(pr) == sha:
                    at = actions.parse_stamp(action.response["dropped_at"])
                    dropped[pr] = max(dropped.get(pr, at), at)
        if not dropped:
            return False
        drive = self.runner.config.drive
        gos = [record for record in cci.records(self.runner.cci_tail, drive, "--lane", ROOT, "--kind=go") if (record["kind"], record["lane"], record["drive"]) == ("go", ROOT, drive)]
        return any(not any(actions.parse_stamp(record["at"]) > at and names_pr(record, pr) for record in gos) for pr, at in dropped.items())

    def enqueue(self) -> None:
        """Run every accepted enqueue, the lowest priority value first: one parallel wave per priority, so a higher-priority prefix reaches the queue before a lower one."""
        accepted = self.book.actions(LANDING, kind="enqueue", status="accepted")
        if not accepted:
            return
        rows = self.rows() or {}
        waves: dict[int, list[actions.Action]] = {}
        for action in accepted:
            waves.setdefault(min(self.priority(rows, pr) for pr in action.target.split(",")), []).append(action)
        for priority in sorted(waves):
            with ThreadPoolExecutor(len(waves[priority])) as pool:
                list(pool.map(self.enqueue_one, waves[priority]))

    def enqueue_one(self, action: actions.Action) -> None:
        key = action.action_id
        if not self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now(), deadline=actions.parse_stamp(action.deadline))):
            return
        prefix = action.target.split(",")
        rows = self.rows() or {}
        if any(not rows.get(pr, {}).get("ours") for pr in prefix):
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "foreign", "out": "", "at": self.book.stamp()}))
            return
        if self.held(rows) is None:
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "blocked", "out": f"{self.holds} carries a line the desk cannot hold by", "at": self.book.stamp()}))
            return
        accepted_at = actions.parse_stamp(action.accepted_at)
        graded = Graded(enqueued_heads(key), rows, accepted_at, act=True)
        if failed := self.failing(HOLD_CHECKS, graded):
            self.refuse(key, prefix, {pr: state.reason for pr, state in failed[1].items()})
            return
        tops = prefix_tops(prefix, rows)
        fresh = self.checked(tops)
        current = enqueue_key(fresh.would, fresh.verdicts) if fresh.would is not None else ""
        if current != key.split("#")[0]:
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "superseded", "out": fresh.out[-800:], "at": self.book.stamp()}))
            return
        if failed := self.failing(ENQUEUE_CHECKS, graded):
            name, states = failed
            text = f"the {name} check refuses " + "; ".join(f"#{pr}: {state.reason}" for pr, state in states.items())
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "blocked", "out": text, "at": self.book.stamp()}))
            return
        outcome, out, last = self.adapter.enqueue(tops, self.whole(), enqueued_heads(key))
        if outcome in BELIEVED_QUEUED and "hold" in self.checks and (late := self.holding(prefix, rows, accepted_at)):
            outcome, out = self.dequeue(key, prefix, rows, late)
        self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": outcome, "out": out, "drive": self.runner.config.drive, "at": self.book.stamp()}))
        if outcome in ("stranded", "failed", "unsettled"):
            self.runner.escalate(key, f"ENQUEUE-{outcome.upper()}", numbered(tops), last)

    def holding(self, prefix: list[str], rows: dict[str, dict], accepted_at: datetime) -> dict[str, str]:
        """Why each PR of `prefix` may not be in the queue right now, read fresh: a holds-file line, the `hold` label on GitHub, an open cci `hold` record, a `hold` record posted after this enqueue was accepted, or a `hold` record newer than the PR's newest `ready` record. A label read GitHub refuses holds the PR too."""
        entries = holds.read(self.holds)
        held = holds.held(entries, rows)
        why = {}
        for pr in prefix:
            if pr in held:
                line = next(entry.line for entry in entries if pr in holds.held([entry], rows))
                why[pr] = f"the holds-file line `{line}`"
            elif reason := self.label_hold(pr) or self.record_hold(pr, accepted_at):
                why[pr] = reason
        return why

    def label_hold(self, pr: str) -> str:
        done = self.shell.run(["gh", "pr", "view", pr, "--repo", self.repo, "--json", "labels"])
        if done.code != 0:
            return f"GitHub refused the label read: {(done.err or done.out).strip()[:120]}"
        return f"the `{HOLD_LABEL}` label on GitHub" if any(label["name"] == HOLD_LABEL for label in json.loads(done.out)["labels"]) else ""

    def record_hold(self, pr: str, accepted_at: datetime) -> str:
        drive = self.runner.config.drive

        def about_pr(kind: str) -> list[dict]:
            found = cci.records(self.runner.cci_tail, drive, f"--kind={kind}", "--pr", f"{self.repo}#{pr}")
            return [record for record in found if (record["kind"], record["drive"]) == (kind, drive) and pr in dict(repo_prs(record, self.repo))]

        if not (posted := about_pr("hold")):
            return ""
        newest = posted[-1]
        if opened := [record for record in posted if record["status"] == "open"]:
            return f"open cci hold #{opened[-1]['seq']}"
        if actions.parse_stamp(newest["at"]) > accepted_at:
            return f"cci hold #{newest['seq']} was posted after this enqueue was accepted"
        if (readied := about_pr("ready")) and readied[-1]["seq"] < newest["seq"]:
            return f"cci ready #{readied[-1]['seq']} is older than cci hold #{newest['seq']}; a ready record after the hold lets it through"
        return ""

    def refuse(self, key: str, prefix: list[str], standing: dict[str, str]) -> None:
        """Complete an enqueue as `refused` and log one `REFUSED` line naming each hold, once per set of heads and holds."""
        why = "; ".join(f"#{pr} is held by {reason}" for pr, reason in standing.items())
        text = f"enqueue of {numbered(prefix)} refused: {why}; it goes through once nothing holds them and no hold is newer than their ready record"
        self.runner.escalate(f"refused:{key.split('#')[0]}:{hashlib.sha1(why.encode()).hexdigest()[:8]}", "REFUSED", numbered(prefix), text)
        self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "refused", "out": text, "at": self.book.stamp()}))

    def dequeue(self, key: str, prefix: list[str], rows: dict[str, dict], late: dict[str, str]) -> tuple[str, str]:
        """Take a just-enqueued prefix back out of the queue after a hold appeared during the call: every held PR and every PR stacked above one, top first, through the adapter's dequeue, telling every drive that names each one. Returns the outcome, `dequeued` once the queue let go of all of them and `held-queued` otherwise, and its text."""
        covered = under_hold({pr: rows[pr] for pr in prefix}, list(late))
        why = "; ".join(f"#{pr} is held by {reason}" for pr, reason in late.items())
        failed = []
        for pr in reversed(prefix):
            if pr not in covered:
                continue
            released, line = self.adapter.dequeue(pr)
            if not released:
                failed.append(f"#{pr}: {line}")
                continue
            self.tell_stopped(pr, rows.get(pr, {}), f"a hold appeared while {numbered(prefix)} was being enqueued ({why}): {line}")
        if failed:
            text = f"a hold appeared while {numbered(prefix)} was being enqueued ({why}); the dequeue failed for {'; '.join(failed)}"
            self.runner.escalate(f"dequeue-failed:{key}", "DEQUEUE-FAILED", numbered(prefix), text)
            return "held-queued", text
        text = f"a hold appeared while {numbered(prefix)} was being enqueued ({why}); {numbered([pr for pr in prefix if pr in covered])} taken back out of the queue"
        self.runner.escalate(f"dequeued:{key}", "DEQUEUED", numbered(prefix), text)
        return "dequeued", text

    def reconcile(self) -> None:
        """An enqueue whose response was lost: Graphite's own status decides; a partial queue is unverifiable and nothing is re-enqueued."""
        cutoff = self.runner.now() - timedelta(minutes=self.runner.config.enqueue_minutes)
        for action in self.book.actions(LANDING, kind="enqueue", status="started"):
            if actions.parse_stamp(action.started_at) < cutoff:
                self.book.attempt(LANDING, lambda incident, key=action.action_id: incident.lose(key))
        for action in self.book.actions(LANDING, kind="enqueue", status="unverifiable"):
            prefix = action.target.split(",")
            if (held := self.adapter.queued(prefix)) is None:
                continue
            queued = [pr for pr in prefix if held[pr]]
            key = action.action_id
            if len(queued) == len(prefix):
                self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "enqueued", "out": f"reconciled from the {self.adapter.kind} queue status", "drive": self.runner.config.drive, "at": self.book.stamp()}))
            else:
                self.runner.escalate(key, "UNVERIFIABLE", f"#{prefix[-1]}", f"the queue holds {', '.join('#' + pr for pr in queued) or 'none'} of {', '.join('#' + pr for pr in prefix)}; nothing was re-enqueued")

    def verify(self, rows: dict[str, dict]) -> None:
        for action in self.book.actions(LANDING, kind="enqueue", status="completed"):
            prefix = action.target.split(",")
            if action.response["outcome"] not in ADMITTED or not all(rows.get(pr, {}).get("state") == "landed" for pr in prefix):
                continue
            receipt = {"landed": {pr: rows[pr].get("landed_sha", "") for pr in prefix}, "at": self.book.stamp()}
            self.book.attempt(LANDING, lambda incident, key=action.action_id: incident.verify(key, receipt))
        for action in self.book.actions(LANDING, kind="enqueue", status="verified"):
            top = action.target.split(",")[-1]
            self.restack(top, rows)

    def is_incident(self, pr: str, row: dict) -> bool:
        """Whether a ledger row is an incident fix's: its lane is an `incident-` or `outage-` lane or a fix lane the runner launched on the incident clock, or an open incident in the executor's store names the PR as its repair."""
        lane = row.get("lane", "")
        if INCIDENT_NAME.match(lane) or (lane_container(lane) in self.book.store.ids() and fix_launch(self.book.load(lane_container(lane)))):
            return True
        store = actions.Store()
        return any(str(incident.facts.get("repair_pr")) == pr for incident in map(store.load, store.ids()) if "adopted" in incident.facts and incident.status not in EXECUTOR_SETTLED and not incident.facts.get("not_ours"))

    def incident_rows(self, open_rows: dict[str, dict]) -> list[str]:
        return [pr for pr, row in open_rows.items() if row.get("ours") and row.get("base") == TRUNK and self.is_incident(pr, row)]

    def squash_incidents(self, rows: dict[str, dict], held: list[str]) -> None:
        """Merge each unheld incident fix PR on the trunk by plain squash in the pass its head is review-approved and every required check passes, once per head, and post `landed`; the queue never takes it."""
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        covered = under_hold(open_rows, held)
        for pr in sorted(self.incident_rows(open_rows), key=int):
            if pr not in covered:
                self.squash(pr, open_rows[pr])

    def squash(self, pr: str, row: dict) -> None:
        done = self.shell.run(["gh", "pr", "view", pr, "--repo", self.repo, "--json", "reviewDecision,headRefOid,state"])
        if done.code != 0:
            return
        view = json.loads(done.out)
        key = f"land:{pr}@{view['headRefOid']}"
        if (squashed := self.book.known(LANDING, key)) and squashed.status == "completed" and "posted" not in squashed.response:
            self.announce(pr, row, key, view["headRefOid"], squashed.response["sha"])
        if view["state"] != "OPEN" or view["reviewDecision"] != "APPROVED" or squashed:
            return
        checks = self.shell.run(["gh", "pr", "checks", pr, "--repo", self.repo, "--required", "--json", "name,bucket"])
        try:
            required = json.loads(checks.out)
        except json.JSONDecodeError:
            return
        if not required or any(check["bucket"] != SQUASH_PASS for check in required):
            return
        if warnings := [line for line in self.rulings_report(pr, view["headRefOid"], incident=True) if cause(line) in INCIDENT_CHECKS]:
            text = f"#{pr} {view['headRefOid'][:10]}: WARNING `rulings.py check-pr --incident` names these on the approved, green incident fix, and the runner does not hold it for them (4236611): {'; '.join(warnings)}"
            warning = f"rulings:{pr}:{view['headRefOid']}:{hashlib.sha1(text.encode()).hexdigest()[:8]}"
            if not self.book.known(LANDING, warning):
                owner = self.owner(pr, row)
                self.post_record("note", warning, ",".join(self.reach(owner) if owner else [ROOT]), text, pr)
        if pr in holds.held(holds.read(self.holds), {pr: row}):
            return
        self.book.accept(LANDING, key, "squash", json.dumps({"pr": pr, "head": view["headRefOid"]}), f"#{pr} incident", None)
        self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now()))
        merged = self.shell.run(["gh", "pr", "merge", pr, "--repo", self.repo, "--squash", "--match-head-commit", view["headRefOid"]])
        if merged.code != 0:
            why = (merged.err or merged.out).strip()[:300] or f"exit {merged.code}"
            self.book.attempt(LANDING, lambda incident: incident.fail(key, why))
            self.runner.escalate(key, "SQUASH-FAILED", f"#{pr}", f"gh pr merge --squash refused the approved, green incident fix at {view['headRefOid'][:10]}: {why}")
            return
        read = self.shell.run(["gh", "pr", "view", pr, "--repo", self.repo, "--json", "mergeCommit"])
        commit = ((json.loads(read.out) if read.code == 0 else {}).get("mergeCommit") or {}).get("oid", "")
        self.book.attempt(LANDING, lambda incident: incident.complete(key, {"sha": commit, "at": self.book.stamp()}))
        self.announce(pr, row, key, view["headRefOid"], commit)

    def ungraded_after(self) -> None:
        """Escalate `UNGRADED-30` once for each incident fix this runner squashed whose head, 30 minutes after it landed, has no counted fidelity ok or no finder ok (d968e1d). Each landing is read once, on the first pass at or after the mark."""
        for action in self.book.actions(LANDING, kind="squash", status="completed"):
            age = self.runner.now() - actions.parse_stamp(action.response["at"])
            if UNGRADED_READ in action.response or age < UNGRADED_AFTER:
                continue
            landed = json.loads(action.target)
            pr, head = landed["pr"], landed["head"]
            verdict = self.finder(pr, head)
            owed = [name for name, ok in (("fidelity ok", self.fidelity(pr, head, TRUNK).ok), ("finder ok", bool(verdict and verdict["text"].startswith(FINDER_OK)))) if not ok]
            if owed:
                self.runner.escalate(
                    f"ungraded-30:{pr}@{head}",
                    "UNGRADED-30",
                    f"#{pr}",
                    f"#{pr} merged as an incident fix at {head[:10]} {int(age.total_seconds() // 60)} minutes ago and has no {' and no '.join(owed)} at that head; both grades are owed within 30 minutes of the merge (d968e1d)",
                )
            self.book.attempt(LANDING, lambda incident, key=action.action_id: incident.action(key).response.update({UNGRADED_READ: self.book.stamp()}))

    def announce(self, pr: str, row: dict, key: str, head: str, commit: str) -> None:
        """Post `landed` for a squash the runner made, under its key so a repost returns the stored record; a post that fails runs again on the next pass."""
        text = f"#{pr} {LANDED_BY_SQUASH} as {commit[:10] or 'an unread squash'}: an incident fix, approved and green at {head[:10]}, merged by the landing runner (ef905a6); its release is owed now"
        done = self.runner.cci_post("landed", text, ",".join(dict.fromkeys([self.owner(pr, row) or ROOT, ROOT])), pr, key, pr=pr, record_id=key)
        if done.code == 0 and (seq := POSTED.match(done.out)):
            self.book.attempt(LANDING, lambda incident: incident.action(key).response.update({"posted": int(seq[1])}))

    def releases(self, rows: dict[str, dict]) -> None:
        """A PR that lands carrying the merge-fast label and a `Release-With:` line sends the release owner and the root one `release` record in the pass that reads the landing, then one `blocker` each `deadlines.release_start_minutes` until a `release` record names the landing or the root replies. Each landing is judged once, by the first pass that reads it, and the landings read persist across restarts: a desk's first pass records every landing it reads as history, so a restart or an upgrade never judges an old landing again."""
        read = self.book.load(LANDING).facts.get(RELEASE_READ)
        landed = {pr for pr, row in rows.items() if row.get("state") == "landed" and row.get("landed_at")}
        if read is None:
            self.book.edit(LANDING, lambda incident: incident.facts.update({RELEASE_READ: sorted(landed, key=int)}))
            return
        judged = []
        for pr in sorted(landed - set(read), key=int):
            row = rows[pr]
            incident = self.squashed(pr) or self.is_incident(pr, row)
            if (incident or MERGE_FAST in row.get("labels", "").split(",")) and not self.accept_release(pr, row, incident, row["landed_at"] < self.started):
                continue
            judged.append(pr)
        if judged:
            self.book.edit(LANDING, lambda incident: incident.facts[RELEASE_READ].extend(judged))
        for action in self.book.actions(LANDING, kind="release"):
            if action.status in ("accepted", "completed"):
                self.press_release(action)
            elif action.status == "verified" and (action.verification_receipt or {}).get("builds") and not (action.verification_receipt or {}).get("live"):
                self.publish_line(action)

    def squashed(self, pr: str) -> bool:
        """Whether this runner merged the PR by plain squash, so its landing owes a release however the PR's incident reads now."""
        return any(action.action_id.startswith(f"land:{pr}@") and action.status == "completed" for action in self.book.actions(LANDING, kind="squash"))

    def accept_release(self, pr: str, row: dict, incident: bool, before_start: bool) -> bool:
        """Owe a landing's release, and say whether its body was read: a merge-fast landing owes the components its `Release-With:` lines name, and none when it names none; an incident fix's landing owes its release either way. A landing from before this runner started owes nothing once `<landing.release_cli> live --json` shows its squash or a later commit running, and closes without a record."""
        done = self.shell.run(["gh", "pr", "view", pr, "--repo", self.repo, "--json", "body"])
        if done.code != 0:
            return False
        components = list(dict.fromkeys(name for line in RELEASE_WITH.findall(json.loads(done.out)["body"]) for name in line.split()))
        key = f"release:{pr}"
        self.book.accept(LANDING, key, "release", json.dumps({"pr": pr, "sha": row["landed_sha"], "components": components, "landed_at": row["landed_at"], "incident": incident}), f"#{pr} {'incident' if incident else MERGE_FAST}", None)
        if not components and not incident:
            self.book.attempt(LANDING, lambda incident: incident.verify(key, {"at": self.book.stamp()}))
        elif before_start and (running := self.shipped(row["landed_sha"], components)):
            self.book.attempt(LANDING, lambda incident: incident.verify(key, {"shipped": running, "at": self.book.stamp()}))
        return True

    def shipped(self, sha: str, components: list[str]) -> str:
        """The deploy `<release_cli> live --json` shows running `sha` or a later commit, of one of `components` when it names any, or empty when none does, or the read fails."""
        done = self.shell.run([*self.release_cli, "live", "--json"])
        if done.code != 0:
            return ""
        contains: dict[str, bool] = {}
        for running in json.loads(done.out):
            if components and running["target"] not in components:
                continue
            if running["commit"] not in contains:
                contains[running["commit"]] = self.shell.run(["git", "-C", str(self.checkout), "merge-base", "--is-ancestor", sha, running["commit"]]).code == 0
            if contains[running["commit"]]:
                return f"{running['target']} on {running['env']} runs {running['commit'][:10]} ({running.get('release') or 'outside a release'})"
        return ""

    def press_release(self, action: actions.Action) -> None:
        """Post one record per step since the landing while no one has started or acknowledged its release: the `release` record first, then a `blocker` with the minutes elapsed. A step is `deadlines.incident_release_minutes` while an incident clock runs and `deadlines.release_start_minutes` otherwise. An incident fix's landing settles only on a `release` record with a build, a `fix-live` record, or a root `--resolves` on one of the runner's records about it, and a root record that holds it after it landed is answered once."""
        landed, key, posted = json.loads(action.target), action.action_id, (action.response or {}).get("posted", {})
        incident = landed.get("incident", False)
        closing = set(posted.values()) | self.held_lines(landed["pr"])
        if settled := self.release_started(landed) or (closing and self.acknowledged(closing, ("resolves",) if incident else ("re", "resolves"))):
            refs = settled.get("refs") or {}
            receipt = {"cci": settled["seq"], "builds": refs.get("builds", []), "targets": refs.get("targets", []), "release": next(iter(RELEASE_ID.findall(settled["text"])), ""), "at": self.book.stamp()}
            if settled["kind"] == "fix-live":
                receipt["live"] = settled["seq"]
            self.book.attempt(LANDING, lambda incident: incident.verify(key, receipt))
            return
        if incident:
            self.held_after_landing(landed)
        pr, every = landed["pr"], self.runner.config.incident_release_minutes if self.runner.live_clocks() else self.runner.config.release_start_minutes
        named = " ".join(landed["components"])
        how = ("as an incident fix" if incident else f"with {MERGE_FAST}") + (f" and Release-With: {named}" if named else "")
        start = f"Start the {named} release now" if named else "Start the release its squash needs now"
        targets = f"targets {named}" if named else "targets a component"
        settles = "post `release --build <url> --target <component>`" if incident else f"post that record with --pr {pr} or --target <component>; a root reply to this record with --re also stops the blocker"
        minutes = int((self.runner.now() - actions.parse_stamp(landed["landed_at"])).total_seconds() // 60)
        step = "0" if minutes < every else str(minutes // every) if every == self.runner.config.release_start_minutes else f"{minutes // every}@{every}m"
        if step in posted:
            return
        if posted:
            kind, text = "blocker", f"#{pr} {landed['sha'][:10]}: landed {minutes} minutes ago {how}, and no cci `release` record since {targets} or names #{pr}. {start} and {settles}, which repeats every {every} minutes."
        else:
            build = " --build <url>" if incident else ""
            kind, text = "release", f"#{pr} landed as {landed['sha'][:10]} {how}. {start}, then run `cci post --drive {self.runner.config.drive} --lane <lane> --kind release --pr {pr} --target <component>{build} --text <build>`. With no such record {every} minutes after the landing, the landing desk posts a blocker to you and the root every {every} minutes."
        seq = self.post_release(f"{key}:{step}", kind, pr, text)
        if seq is not None:
            self.book.attempt(LANDING, lambda incident: release_posted(incident, key, step, seq, self.runner.now()))

    def held_after_landing(self, landed: dict) -> None:
        """Answer each open root `hold` posted after an incident fix landed that names its PR or is addressed to the release owner with one RELEASE-HELD-AFTER-LANDING line: its cut is owed now. Only a `hold` record is a hold, and the root's `--resolves` on it ends it."""
        drive, pr, since = self.runner.config.drive, landed["pr"], actions.parse_stamp(landed["landed_at"])
        owner = self.release_owner.removeprefix("peer:")
        for record in cci.records(self.runner.cci_tail, drive, "--lane", ROOT, "--kind=hold"):
            if (record["lane"], record["drive"], record["kind"]) != (ROOT, drive, "hold") or record["status"] != "open" or actions.parse_stamp(record["at"]) < since:
                continue
            if names_pr(record, pr) or any(owner in lane for lane in record.get("to") or []):
                self.runner.record(
                    f"escalation:release-held:{pr}:{record['seq']}",
                    f"RELEASE-HELD-AFTER-LANDING #{pr}: landed {landed['sha'][:10]} at {pacific(since)}; its cut is owed now, the other PR gets its own cut when it lands (root #{record['seq']})",
                )

    def publish_line(self, action: actions.Action) -> None:
        """Once a landing's release names its build, read `<release_cli> live <id> --json` each pass and post one RELEASED fix-live record to the root alone when every component it ships is live; the root dispatches any post."""
        landed, receipt = json.loads(action.target), action.verification_receipt
        components = landed["components"] or receipt["targets"]
        release = receipt["release"] or BUILD_NUMBER.search(receipt["builds"][0])[1]
        if not components:
            return
        done = self.shell.run([*self.release_cli, "live", release, "--json"])
        if done.code != 0:
            return
        live = json.loads(done.out)
        if any(live.get(component, {}).get("live", NOT_LIVE) == NOT_LIVE for component in components):
            return
        build = receipt["builds"][0]
        versions = ", ".join(f"{component} {live[component]['live']}" for component in components)
        key = f"release:{landed['pr']}:live"
        flags = [arg for component in components for arg in ("--target", component)] + ["--build", build]
        done = self.runner.cci_post("fix-live", f"RELEASED {versions} ({build})", ROOT, landed["pr"], key, pr=landed["pr"], record_id=key, flags=flags)
        if done.code == 0 and POSTED.match(done.out):
            self.book.attempt(LANDING, lambda incident: incident.action(action.action_id).verification_receipt.update({"live": int(POSTED.match(done.out)[1])}))

    def post_release(self, key: str, kind: str, pr: str, text: str) -> int | None:
        """Post one record about a landing's release to the release owner and the root, with `key` as its cci id so a repost returns the stored record, and return its sequence. A record cci refuses, or a post that prints no sequence, is escalated to root once per `key`, and the next pass posts it again."""
        to = f"{self.release_owner},{ROOT}"
        done = self.runner.cci_post(kind, text, to, pr, key, pr=pr, record_id=key)
        seq = POSTED.match(done.out)
        if done.code == 0 and seq:
            return int(seq[1])
        why = "refused" if done.code else "printed no sequence for"
        self.runner.escalate(key, "RELEASE-FAILED", f"#{pr}", f"cci {why} the {kind} record to {to}: {(done.err or done.out).strip()[:200]}; {text}")
        return None

    def release_started(self, landed: dict) -> dict | None:
        """The drive's first cci `release` or `fix-live` record after the landing, from any lane but the runner's own, that names one of the landing's components as a target or its PR. An incident fix's landing needs a `fix-live` record, or a build on the record and a target: one of its components, or any when it names none."""
        drive, since, names = self.runner.config.drive, actions.parse_stamp(landed["landed_at"]), (int(landed["pr"]), f"{self.repo}#{landed['pr']}")
        releases = cci.records(self.runner.cci_tail, drive, *(f"--kind={kind}" for kind in SETTLING_KINDS))

        def names_landing(kind: str, refs: dict) -> bool:
            targets = set(refs.get("targets", []))
            if landed.get("incident") and kind == "release":
                return bool(refs.get("builds")) and bool(targets & set(landed["components"]) if landed["components"] else targets)
            return bool(targets & set(landed["components"]) or any(named in names for named in refs.get("prs", [])))

        return next(
            (
                record
                for record in releases
                if record["kind"] in SETTLING_KINDS
                and record["drive"] == drive
                and record["lane"] != RUNNER
                and actions.parse_stamp(record["at"]) >= since
                and names_landing(record["kind"], record["refs"])
            ),
            None,
        )

    def acknowledged(self, seqs: set[int], links: tuple[str, ...]) -> dict | None:
        """The drive root's first cci record that carries one of `links`, `--re` or `--resolves`, for one of the records the runner posted about a landing's release."""
        drive = self.runner.config.drive
        replies = cci.records(self.runner.cci_tail, drive, "--lane", ROOT, since=min(seqs))
        return next((record for record in replies if (record["lane"], record["drive"]) == (ROOT, drive) and seqs & {record.get(link) for link in links}), None)

    def held_lines(self, pr: str) -> set[int]:
        """The sequences of the RELEASE-HELD-AFTER-LANDING lines the runner posted for `pr`."""
        prefix = f"escalation:release-held:{pr}:"
        return {int(seq[1]) for action in self.book.actions(RUNNER, kind="escalation") if action.action_id.startswith(prefix) and (seq := POSTED.search((action.response or {}).get("posted", "")))}

    def believed_queued(self, rows: dict[str, dict]) -> dict[str, list[str]]:
        return {
            action.action_id: [pr for pr, sha in enqueued_heads(action.action_id).items() if sha and pr in rows and rows[pr].get("state", "open") == "open" and rows[pr].get("head", "").startswith(sha)]
            for action in self.book.actions(LANDING, kind="enqueue", status="completed")
            if action.response["outcome"] in BELIEVED_QUEUED
        }

    def requeue(self, rows: dict[str, dict]) -> None:
        """A believed-queued prefix the queue no longer holds at the heads it admitted is classified from the adapter's verdict on each dropped PR. A `conflict` stays out at that head, and the gate routes its ejection to the lane; an `own-red` is gated again, and the adapter blocks it until a new head; a `batch-mate-red` blaming a PR outside the PR's own stack tells the root once and re-enqueues the same head after the root's next `go` record naming the PR, while one the adapter itself retries is gated again. A `flaky-check` goes back in at the same head through the adapter once, on the desk's own policy; a second at the same head waits for a root `go` record naming the PR."""
        believed = self.believed_queued(rows)
        numbers = sorted({pr for prs in believed.values() for pr in prs}, key=int)
        if not numbers:
            return
        done = self.shell.run(["ccx", "vcs", "pr", "status", "-R", self.repo, "--json", *numbers])
        if done.code != 0:
            return
        queue = {str(entry["number"]): entry["queue"] for entry in json.loads(done.out)}
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        for key, prs in believed.items():
            dropped = [pr for pr in prs if queue[pr] in OUT_OF_QUEUE]
            if not dropped:
                continue
            heads = enqueued_heads(key)
            gated = self.checked(prefix_tops([pr for pr in heads if pr in rows], rows))
            if not gated.verdicts:
                continue
            ejections = {pr: self.adapter.ejection(gated.verdicts[pr], set(heads) | downstack(open_rows, pr)) if pr in gated.verdicts else None for pr in dropped}
            kinds = {pr: ejection.kind if ejection else "" for pr, ejection in ejections.items()}
            conflicts = {pr: heads[pr] for pr, kind in kinds.items() if kind == merge_queue.CONFLICT}
            blamed = {pr: ejection.blamed for pr, ejection in ejections.items() if ejection and ejection.blamed}
            flaky = {pr: heads[pr] for pr, kind in kinds.items() if kind == merge_queue.FLAKY_CHECK}
            for pr, named in blamed.items():
                self.runner.escalate(f"awaits-go:{pr}:{heads[pr]}", "AWAITS-GO", f"#{pr}", f"{heads[pr]}: the merge queue dropped it for downstack failures on #{named}, outside its stack; no new head is needed, and a root `go` record naming #{pr} re-enqueues this head")
            awaiting = {pr: heads[pr] for pr in blamed}
            due = {pr: sha for pr, sha in flaky.items() if not self.flake_retried(pr, sha)}
            for pr, sha in flaky.items():
                if pr not in due:
                    awaiting[pr] = sha
                    self.runner.escalate(f"awaits-go:{pr}:{sha}", "AWAITS-GO", f"#{pr}", f"{sha}: a flaky check ejected it again at the same head after the desk's one automatic retry ({ejections[pr].detail}); a root `go` record naming #{pr} puts this head back in, and a new head enqueues as usual")
            drop = {
                "outcome": "ejected" if conflicts else "dropped",
                "drop": {pr: {"kind": kinds[pr], "detail": gated.verdicts[pr]["detail"] if pr in gated.verdicts else ""} for pr in dropped},
                "ejected": conflicts,
                "awaiting_go": awaiting,
                "flaky": {pr: sha for pr, sha in flaky.items() if pr in awaiting},
                "flaky_due": due,
                "dropped_at": self.book.stamp(),
            }
            self.book.attempt(LANDING, lambda incident, key=key, drop=drop: setattr(incident.action(key), "response", {**incident.action(key).response, **drop}))

    def flake_retried(self, pr: str, sha: str, outcomes: frozenset[str] | None = None) -> bool:
        """Whether the desk already put `pr` back at `sha` through the adapter, and with `outcomes`, whether that retry still reads one of them."""
        return any(
            (response := action.response or {}).get(FLAKE_RETRY, {}).get("pr") == pr and same_head(response[FLAKE_RETRY]["head"], sha) and (outcomes is None or response["outcome"] in outcomes)
            for action in self.book.actions(LANDING, kind="enqueue")
        )

    def flake_retry(self, pr: str, sha: str, prefix: list[str], heads: dict[str, str], rows: dict[str, dict], why: str) -> str:
        """Put `pr` back in the queue at its unchanged head through the adapter once its prefix passes the `hold` check and every enqueue check, recorded before the call as one more enqueue of its prefix so it is verified and dropped like any other, naming `why` in the journal; the adapter comments on the PR. Returns `enqueued`, `refused` when the adapter refused it, which logs `FLAKE-RETRY-FAILED` and waits for a root `go` or `mq requeue`, `unsettled` when the queue left it stranded, unsettled, or failed, which logs `ENQUEUE-<OUTCOME>` like any enqueue, or `held` when a check refused it, which tries again on a later pass."""
        graded = Graded({member: heads.get(member, "") for member in prefix}, rows, self.runner.now(), act=True)
        if self.failing((*HOLD_CHECKS, *ENQUEUE_CHECKS), graded):
            return "held"
        base = "enqueue:" + ",".join(f"{member}@{heads.get(member, '')}" for member in prefix)
        attempt = len([action for action in self.book.actions(LANDING, kind="enqueue") if action.action_id.split("#")[0] == base]) + 1
        key = f"{base}#{attempt}"
        self.book.accept(LANDING, key, "enqueue", ",".join(prefix), self.authority(), None)
        self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now()))
        outcome, out, last = self.adapter.flake(pr)
        retry = {"pr": pr, "head": sha, "why": why}
        if outcome == "blocked":
            refused = {"outcome": "flake-refused", "out": out, "drive": self.runner.config.drive, FLAKE_RETRY: retry, "flaky": {pr: sha}, "dropped_at": self.book.stamp(), "at": self.book.stamp()}
            self.book.attempt(LANDING, lambda incident: incident.complete(key, refused))
            self.runner.escalate(f"flake:{pr}:{sha}", "FLAKE-RETRY-FAILED", f"#{pr}", f"{sha}: the {self.adapter.kind} queue refused the same-head retry of its flaky-check ejection: {last}; a root `go` naming #{pr} or `mq.py requeue {pr}` tries it again")
            return "refused"
        self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": outcome, "out": out, "drive": self.runner.config.drive, FLAKE_RETRY: retry, "at": self.book.stamp()}))
        if outcome not in BELIEVED_QUEUED:
            self.runner.escalate(key, f"ENQUEUE-{outcome.upper()}", f"#{pr}", last)
            return "unsettled"
        return "enqueued"

    def retry_flakes(self, rows: dict[str, dict]) -> None:
        """Put each flaky head back in the queue: once on the desk's policy right after its first drop, and again once a root `go` record naming its PR is posted after a later drop, citing that record. Runs only on a pass that read the holds and fast-forwarded the checkout."""
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        for action in self.book.actions(LANDING, kind="enqueue", status="completed"):
            for pr, sha in dict(action.response.get("flaky_due", {})).items():
                if not rows.get(pr, {}).get("head", "").startswith(sha):
                    continue
                heads = enqueued_heads(action.action_id)
                if self.flake_retry(pr, sha, [p for p in heads if p in downstack(open_rows, pr)] or [pr], heads, rows, "the desk's policy of one automatic same-head retry per flaky-check ejection") != "held":
                    self.book.attempt(LANDING, lambda incident, key=action.action_id, pr=pr: incident.action(key).response["flaky_due"].pop(pr))
        waiting = [(action, pr, sha) for action in self.book.actions(LANDING, kind="enqueue", status="completed") for pr, sha in action.response.get("flaky", {}).items() if rows.get(pr, {}).get("head", "").startswith(sha)]
        if not waiting:
            return
        drive = self.runner.config.drive
        gos = [record for record in cci.records(self.runner.cci_tail, drive, "--lane", ROOT, "--kind=go") if (record["kind"], record["lane"], record["drive"]) == ("go", ROOT, drive)]
        for action, pr, sha in waiting:
            at = actions.parse_stamp(action.response["dropped_at"])
            go = next((record for record in gos if actions.parse_stamp(record["at"]) > at and names_pr(record, pr)), None)
            if go is None:
                continue
            heads = enqueued_heads(action.action_id)
            if self.flake_retry(pr, sha, [p for p in heads if p in downstack(open_rows, pr)] or [pr], heads, rows, f"the root's go #{go['seq']}") != "held":
                self.book.attempt(LANDING, lambda incident, key=action.action_id, pr=pr: incident.action(key).response["flaky"].pop(pr))

    def owned(self, pr: str, rows: dict[str, dict]) -> bool:
        """Whether this drive's queue answers for `pr`: an enqueue this drive's store believes queued names it, or a drive lane of this drive opened it."""
        return any(pr in prs for prs in self.believed_queued(rows).values()) or bool(rows.get(pr, {}).get("ours"))

    def naming_drives(self, pr: str) -> list[str]:
        """Every drive `cci drive ls` lists whose own records name `pr` in this repository, this drive first."""
        done = self.shell.run(["cci", "drive", "ls", "--json"])
        if done.code != 0:
            raise CciTailFailed(f"cci drive ls exited {done.code}: {(done.err or done.out).strip()[:300]}")
        drive = self.runner.config.drive
        others = [entry["drive"] for entry in json.loads(done.out) if entry["drive"] != drive]
        return [drive, *(other for other in others if any(record["drive"] == other for record in cci.records(self.runner.cci_tail, other, "--pr", f"{self.repo}#{pr}")))]

    def tell_stopped(self, pr: str, row: dict, text: str) -> None:
        """Post one cci `stopped` record about a dequeued PR to each drive that names it: to the PR's lane on this drive, and to the root on every other."""
        for drive in self.naming_drives(pr):
            to = (self.owner(pr, row) or ROOT) if drive == self.runner.config.drive else ROOT
            self.runner.cci_post("stopped", f"DEQUEUED #{pr} by {self.runner.config.drive}: {text}", to, f"#{pr}", f"stopped:{pr}:{drive}:{self.book.stamp()}", pr=f"{self.repo}#{pr}", drive=drive)

    def dequeue_pr(self, pr: str, why: str) -> tuple[bool, str]:
        """Take `pr` out of the queue for `mq dequeue`: refused when this drive's queue does not answer for it, naming the drives whose records do; otherwise through the adapter, telling every drive that names it, and marking each enqueue that admitted it `pulled`, so the desk does not put the same head back without `mq requeue`."""
        rows = self.rows() or {}
        if not self.owned(pr, rows):
            others = self.naming_drives(pr)[1:]
            owners = ", ".join(f"root@{other}" for other in others) or "no drive's root"
            return False, f"#{pr} is not this drive's: no enqueue of {self.runner.config.drive} admitted it and no lane of it opened it; ask {owners} to dequeue it"
        released, line = self.adapter.dequeue(pr)
        if not released:
            return False, f"the {self.adapter.kind} queue did not let #{pr} go: {line}"
        for key, prs in self.believed_queued(rows).items():
            if pr in prs:
                self.book.attempt(LANDING, lambda incident, key=key: setattr(incident.action(key), "response", {**incident.action(key).response, "outcome": PULLED, "pulled": {"pr": pr, "why": why, "at": self.book.stamp()}}))
        self.tell_stopped(pr, rows.get(pr, {}), f"{why}: {line}")
        return True, f"dequeued #{pr}: {line}"

    def requeue_pr(self, pr: str) -> tuple[bool, str]:
        """Let `mq requeue` put `pr` back at its current head: a flaky head goes back in through the adapter at once, and a pulled, ejected, or go-awaiting head becomes retryable once the adapter releases whatever latch it keeps on that head, so the next pass enqueues it once every check passes."""
        rows = self.rows() or {}
        head = rows.get(pr, {}).get("head", "")
        cleared = []
        for action in self.book.actions(LANDING, kind="enqueue", status="completed"):
            response = action.response
            sha = enqueued_heads(action.action_id).get(pr)
            if not sha or not head.startswith(sha):
                continue
            if pr in response.get("flaky", {}):
                heads = enqueued_heads(action.action_id)
                open_rows = {number: row for number, row in rows.items() if row.get("state", "open") == "open"}
                result = self.flake_retry(pr, sha, [p for p in heads if p in downstack(open_rows, pr)] or [pr], heads, rows, "the root's mq requeue")
                if result != "held":
                    self.book.attempt(LANDING, lambda incident, key=action.action_id: incident.action(key).response["flaky"].pop(pr))
                return result == "enqueued", {"enqueued": f"#{pr} {sha} back in the queue", "refused": f"#{pr} {sha}: the {self.adapter.kind} queue refused the same-head retry", "unsettled": f"#{pr} {sha}: the same-head retry did not settle in the {self.adapter.kind} queue; read the ENQUEUE- escalation", "held": f"#{pr} {sha}: a check still refuses it; read mq.py why {pr}"}[result]
            if response["outcome"] in (PULLED, "ejected") or pr in response.get("awaiting_go", {}) or pr in response.get("ejected", {}):
                cleared.append(action.action_id)
        if not cleared:
            return True, f"#{pr} {head[:10] or 'has no row'}: nothing holds this head out of the queue"
        released, line = self.adapter.release(pr, head)
        if not released:
            return False, f"#{pr} {head[:10]}: the {self.adapter.kind} queue still refuses this head, so nothing was cleared: {line}"

        def clear(incident: actions.Incident, key: str) -> None:
            kept = incident.action(key).response
            incident.action(key).response = {**kept, "outcome": "dropped", "ejected": {n: h for n, h in kept.get("ejected", {}).items() if n != pr}, "awaiting_go": {n: h for n, h in kept.get("awaiting_go", {}).items() if n != pr}}

        for key in cleared:
            self.book.attempt(LANDING, lambda incident, key=key: clear(incident, key))
        return True, f"#{pr} {head[:10]} is retryable again; the next pass enqueues it once every check passes"

    def why(self, pr: str) -> list[str]:
        """One line per configured check of `pr` at its current head, read without posting anything, after the adapter's own verdict line."""
        rows = self.rows() or {}
        row = rows.get(pr)
        if row is None:
            return [f"#{pr} has no ledger row, so the queue never gates it"]
        head = row.get("head", "")
        verdict = self.checked([pr]).verdicts.get(pr)
        said = " ".join(filter(None, (verdict["verdict"], verdict["detail"]))) if verdict else "no verdict"
        lines = [f"#{pr} {head[:10]} {self.adapter.kind}: {said}"]
        if not row.get("ours"):
            lines.append(f"ours fail {NOT_ADMITTED}")
        adapter_states = self.adapter.classify(verdict) if verdict else {}
        graded = Graded({pr: head}, rows, self.runner.now(), act=False)
        for name in self.checks:
            state = adapter_states.get(name, merge_queue.State(merge_queue.PENDING, "no verdict")) if name in ("approvals", "required-ci") else self.check(name, graded).get(pr, merge_queue.State(merge_queue.PASS))
            lines.append(f"{name} {state.state} {state.reason}".rstrip())
        return lines

    def status_lines(self) -> list[str]:
        """The queue as this drive's store records it: each believed-queued enqueue, then each open tree in the order the desk gates and enqueues it."""
        rows = self.rows() or {}
        lines = []
        for action in self.book.actions(LANDING, kind="enqueue", status="completed"):
            outcome = action.response["outcome"]
            if outcome in BELIEVED_QUEUED or action.response.get("flaky") or action.response.get("awaiting_go"):
                lines.append(f"{action.action_id} {outcome} by {action.response.get('drive', self.runner.config.drive)}")
        for tree in self.trees(rows):
            open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
            members = sorted({pr for tip in tree for pr in downstack(open_rows, tip)}, key=int)
            lines.append(f"tree {numbered(tree)} priority {min(self.priority(open_rows, pr) for pr in members)}: {numbered(members)}")
        return lines or ["the queue holds nothing this drive enqueued, and no open tree waits"]

    def restack(self, landed_pr: str, rows: dict[str, dict]) -> None:
        landed = rows.get(landed_pr, {})
        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            if row.get("state", "open") != "open" or row.get("base") != landed.get("branch"):
                continue
            text = f"#{landed_pr} landed as {landed.get('landed_sha', '')[:10]}; #{pr} at {row.get('head', '')[:10]} sits on its deleted branch. Restack now: `ccx vcs stack submit` from your worktree."
            self.route(f"restack:{pr}:{landed_pr}", self.owner(pr, row), text, pr)

    def route_blocker(self, pr: str, sha: str, blocker: merge_queue.Blocker, row: dict, tips: list[str], stack: set[str], gated_red: bool, failing: bool) -> None:
        """Tell the lane once per head, re-reading the gate just before the send so an approval or fix that already arrived is never asked for. A failing required check posts one blocker per PR, refusal, red job and cause: a later head failing the same way adds one line under that blocker, and a changed cause or a new red job opens a new one. A conflict whose lane has no live dispatch and no live cci session routes nothing: the root gets one `PARKED` line per PR, and the head routes once the lane is back."""
        key = f"blocker:{pr}:{sha}"
        owner = self.owner(pr, row)
        if self.routed(key, owner) or self.has_landed(pr):
            return
        red = self.red_cause(pr, sha) if failing and not blocker.conflict else ""
        cause = hashlib.sha1(f"{blocker.text}\n{red}".encode()).hexdigest()[:12] if red else ""
        earlier = self.cause_blocker(pr, cause) if cause else None
        parked = bool(owner and blocker.conflict and not self.lane_live(owner))
        if parked and self.book.known(RUNNER, f"escalation:{PARKED}:{pr}"):
            return
        current = self.checked(tips).verdicts.get(pr)
        if not current or current["verdict"] != "BLOCKED" or self.adapter.blocker(current, stack, gated_red) != blocker:
            return
        if parked:
            self.runner.escalate(f"{PARKED}:{pr}", "PARKED", f"#{pr}", f"{sha}: {owner} has no live dispatch or cci session, so its conflict routes to no one until the lane is back: {blocker.text}")
        elif earlier and (seq := POSTED_SEQ.match(earlier.response["posted"])):
            to = ",".join(self.reach(owner)) if owner else json.loads(earlier.target)["lane"]
            self.post_record("note", key, to, f"#{pr} {sha}: the same failure again: {red}", pr, re_seq=seq[1])
        else:
            self.route(key, owner, f"#{pr} {sha}: {blocker.text}", pr, cause)

    def cause_blocker(self, pr: str, cause: str) -> actions.Action | None:
        """The blocker this desk already posted on `pr` for the same refusal, red jobs and causes, if any."""
        return next(
            (action for action in self.book.actions(LANDING, kind="bus", status="completed") if action.action_id.startswith(f"blocker:{pr}:") and json.loads(action.target).get("cause") == cause),
            None,
        )

    def lane_live(self, lane: str) -> bool:
        """The lane has a dispatch Orca shows active, or a session `cci who` lists."""
        dispatch = self.runner.orca.show(lane) if self.runner.orca.receipt(lane) else None
        return bool(dispatch and dispatch.status not in INACTIVE) or self.session_live(lane)

    def owner(self, pr: str, row: dict) -> str:
        """The lane that owns `pr` now: the poster of the newest `head`, `opened`, `ready`, or `claim` record naming it on this drive, or of a `ledger.py report` on it, else the lane that registered its ledger row."""
        drive = self.runner.config.drive
        posted = cci.records(self.runner.cci_tail, drive, "--pr", f"{self.repo}#{pr}", *(f"--kind={kind}" for kind in OWNER_KINDS))
        claims = [
            record
            for record in posted
            if record["drive"] == drive and record["kind"] in OWNER_KINDS and (record["kind"] != "report" or cci.lane_report(record)) and pr in dict(repo_prs(record, self.repo))
        ]
        return claims[-1]["lane"] if claims else row.get("lane", "")

    def reach(self, lane: str) -> list[str]:
        """Who a blocker for `lane` goes to: the lane, and the root too when `cci who` lists no live session for it."""
        return [lane] if self.session_live(lane) else [lane, ROOT]

    def session_live(self, lane: str) -> bool:
        done = self.shell.run(["cci", "who", "--drive", self.runner.config.drive, "--lane", lane, "--json"])
        if done.code != 0:
            raise CciTailFailed(f"cci who exited {done.code}: {(done.err or done.out).strip()[:300]}")
        return bool(done.out.strip())

    def has_landed(self, pr: str) -> bool:
        """A PR the merge queue already landed keeps its ledger row open until the branch is pruned, and the gate still reads its stale branch as conflicted; it gets no blocker."""
        if pr not in self.landed:
            done = self.shell.run(["ccx", "vcs", "pr", "status", "-R", self.repo, "--json", pr])
            if done.code == 0 and any(str(entry["number"]) == pr and entry["queue"] == "landed" for entry in json.loads(done.out)):
                self.landed.add(pr)
        return pr in self.landed

    def routed(self, key: str, lane: str) -> bool:
        container = lane_container(lane)
        return key in self.book.load(LANDING).actions or (container in self.book.store.ids() and key in self.book.load(container).actions)

    def route(self, key: str, lane: str, text: str, pr: str, cause: str = "") -> None:
        if self.routed(key, lane):
            return
        if not lane:
            self.runner.escalate(key, "UNOWNED", f"#{pr}", text)
        elif self.runner.orca.receipt(lane):
            relay, _ = self.runner.accept_relay(key, lane, text, "", self.runner.config.start_minutes)
            if (dispatch := self.runner.orca.show(lane)) and dispatch.status not in INACTIVE:
                self.runner.send(lane_container(lane), dispatch, relay)
        else:
            self.post_blocker(key, ",".join(self.reach(lane)), text, pr, cause)

    def post_blocker(self, key: str, to: str, text: str, pr: str, cause: str = "") -> None:
        self.post_record("blocker", key, to, text, pr, cause=cause)

    def post_record(self, kind: str, key: str, to: str, text: str, pr: str, cause: str = "", re_seq: str = "") -> None:
        self.book.accept(LANDING, key, "bus", json.dumps({"lane": to, "pr": pr, "text": text} | ({"cause": cause} if cause else {})), key, None)
        self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now()))
        done = self.runner.cci_post(kind, text, to, pr, key, flags=["--re", re_seq] if re_seq else None)
        if done.code == 0:
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"posted": done.out.strip()[:200], "at": self.book.stamp()}))
        else:
            self.book.attempt(LANDING, lambda incident: incident.fail(key, (done.err or done.out).strip()[:300]))

    def verify_restacks(self, rows: dict[str, dict]) -> None:
        """A restack route is verified once its PR's head moves or the PR lands."""
        routes = [(LANDING, action) for action in self.book.actions(LANDING, kind="bus")]
        routes += [(name, action) for name in self.book.containers(LANE_PREFIX) for action in self.book.actions(name, kind="relay")]
        for container, action in routes:
            if not action.action_id.startswith("restack:") or action.status in ("verified", "failed"):
                continue
            _, pr, landed_pr = action.action_id.split(":")
            row = rows.get(pr, {})
            if row and (row.get("state", "open") != "open" or row.get("base") != rows.get(landed_pr, {}).get("branch")):
                receipt = {"head": row.get("head", ""), "state": row.get("state", ""), "at": self.book.stamp()}
                self.book.attempt(container, lambda incident, key=action.action_id: incident.verify(key, receipt))

    def advance(self) -> bool:
        """Fast-forward the checkout to the remote trunk without writing a shared ref, so the pass gates with the trunk's adapter script; a checkout that cannot is escalated once per error."""
        git = ["git", "-C", str(self.checkout)]
        done = self.shell.run([*git, "fetch", "-q", "--no-tags", "--refmap=", "origin", f"refs/heads/{TRUNK}"])
        if not done.code:
            done = self.shell.run([*git, "merge", "-q", "--ff-only", "FETCH_HEAD"])
        if done.code:
            self.runner.escalate(
                f"checkout:{hashlib.sha1(done.err.encode()).hexdigest()[:8]}", "CHECKOUT-STALE", "landing",
                f"{self.checkout} did not fast-forward to origin/{TRUNK}, so this pass gated and enqueued nothing: {done.err.strip()}",
            )
        return not done.code

    def run(self) -> None:
        self.ask_rulings()
        try:
            self.gate_pass()
        finally:
            self.report_unread()

    def ask_rulings(self) -> None:
        """Log one `RULING-ASK` per board round while ruling cards `rulings.py classify` wrote queue under the drive's `cards/`; the root puts the round on the cc-present board with `rulings.py round`, never one card per ruling."""
        state = self.runner.config.source.parent
        if not (queued := list((state / RULING_CARDS).glob("*.json"))):
            return
        number = len(list((state / RULING_ROUNDS).glob("round-*.json"))) + 1
        self.runner.escalate(
            f"ruling-ask:round-{number}:{min(queued).stem}",
            "RULING-ASK",
            f"round-{number}",
            f"{len(queued)} ruling confirmations queue | when the owner can take a round, run `rulings.py round --drive {self.runner.config.drive}`:"
            f" it puts at most ten of them on the live cc-present board as round {number}, foundational rows first, then sends them to Slack in one `cc-present send`, never as an AskUserQuestion or a bare Slack card;"
            " a pick or a note on a ruling there is a capture: Right ticks every line with `rulings.py confirm <key> --tick all --capture <digest>`,"
            " and Some wrong waits for the owner's note naming the lines",
        )

    def gate_pass(self) -> None:
        self.reported = {}
        if self.quota_low():
            return
        advanced = self.advance()
        self.ledger_py("refresh", "--repo", self.repo, "--ledger", self.ledger)
        self.ledger_py("reconcile", "--repo", self.repo, "--ledger", self.ledger, "--checkout", str(self.checkout))
        rows = self.rows()
        if rows is None:
            return
        if self.status_card:
            self.draw_card(rows)
        self.read_heads({row["head"]: pr for pr, row in rows.items() if row.get("state", "open") == "open" and row.get("head")})
        entries = holds.read(self.holds)
        unread = self.holds_unread(entries, rows)
        if not rows:
            return
        self.reconcile()
        self.verify(rows)
        self.releases(rows)
        self.ungraded_after()
        self.verify_restacks(rows)
        self.requeue(rows)
        if advanced and not unread and (held := self.held(rows)) is not None:
            self.squash_incidents(rows, held)
            self.retry_flakes(rows)
            self.gate(rows, held)
            self.enqueue()
        self.runner.overdue(LANDING, "landing")


def seed_policy(runner: Runner) -> None:
    landing = runner.config.landing
    if landing and not runner.landing_policy():
        policy = landing["policy"]
        runner.accept_policy(f"policy:{policy['revision']}", policy["rule"], policy["revision"], policy["source"], "")


def run_orca(runner: Runner, once: bool) -> int:
    state = runner.binding()
    if not state["bound"] and runner.shell.env("ORCA_TERMINAL_HANDLE") and runner.rebind() == 0:
        state = runner.binding()
    if not state["bound"]:
        runner.escalate(f"unbound:{state['terminal']}:{state['generation']}", "UNBOUND", "runner", f"{runner.unbound(state)}; the runner refused to start")
        runner.flush()
        print(runner.unbound(state), file=sys.stderr)
        return 3
    if fault := applying_fault(runner.config.landing):
        why = f"{runner.config.source}: {fault}; the runner refused to start"
        runner.escalate(f"applying-config:{runner.config.source}", "APPLYING-CONFIG-INVALID", "runner", why)
        runner.flush()
        print(why, file=sys.stderr)
        return 3
    if failure := runner.resolve_briefs_log():
        runner.escalate(f"briefs-log:{runner.config.briefs_log or 'unset'}", "BRIEFS-LOG-FAILED", "runner", f"the briefs log did not resolve at start: {failure}; each brief attach retries it")
    swept = rotated = datetime.min.replace(tzinfo=timezone.utc)
    while True:
        if runner.now() - rotated >= ROTATE_EVERY:
            runner.rotate_inboxes()
            rotated = runner.now()
        runner.follow_coordinator()
        try:
            if runner.check():
                runner.reap()
                runner.read_inbox()
                runner.read_cci()
                runner.deliver()
                if runner.now() - swept >= SWEEP_EVERY:
                    runner.sweep()
                    swept = runner.now()
            runner.deadlines_orca()
            runner.incident_clock()
            runner.pr_silent()
            runner.class_unowned()
            runner.in_process()
            runner.cut_clock()
            runner.release_coupling()
        except OrcaUnreachable as down:
            runner.orca_down(down)
        else:
            runner.orca_back()
        runner.aged_holds()
        runner.stalled_launches()
        behind = runner.stale("orca")
        runner.flush()
        runner.write_view()
        runner.beat("orca")
        if once:
            return 0
        runner.idle(runner.pass_wait())
        if runner.stop_requested():
            runner.stop_marker().unlink()
            return 0
        if behind:
            runner.reexec("orca")


def mq_config_fault(landing: dict) -> str:
    """What `landing` lacks for the merge queue to run, or empty: an adapter kind it knows, a script for the stack-enqueue adapter, only built-in checks, the high-risk globs merge mode high-risk reads, and the release command line. Each fault ends with the exact `landing` members that fix it in one edit; none is ever assumed."""
    faults, members = [], []
    adapter = landing.get("adapter") or {}
    if adapter.get("kind") not in (merge_queue.GraphiteStackEnqueue.kind, merge_queue.LabelEnqueue.kind):
        faults.append(f"names no landing.adapter.kind of {merge_queue.GraphiteStackEnqueue.kind} or {merge_queue.LabelEnqueue.kind}, so the merge queue has no repository adapter")
        members.append(f'"adapter": {json.dumps(merge_queue.suggested_adapter(Path(landing["checkout"])))}')
    elif adapter["kind"] == merge_queue.GraphiteStackEnqueue.kind and not adapter.get("script"):
        faults.append("names no landing.adapter.script, the stack-enqueue script relative to landing.checkout")
        members.append(f'"adapter": {json.dumps({**adapter, "script": merge_queue.STACK_ENQUEUE})}')
    if unknown := [name for name in landing.get("checks", merge_queue.CHECKS) if name not in merge_queue.CHECKS]:
        faults.append(f"lists {', '.join(unknown)} under landing.checks, which is no built-in check of {', '.join(merge_queue.CHECKS)}")
        members.append(f'"checks": {json.dumps([name for name in landing["checks"] if name in merge_queue.CHECKS])}')
    if landing["merge_mode"] == "high-risk" and not landing.get("high_risk_paths"):
        faults.append("runs merge mode high-risk with no landing.high_risk_paths globs, so no PR would read as high-risk")
        members.append(f'"high_risk_paths": {json.dumps(list(HIGH_RISK_STARTER))}')
    if not landing.get("release_cli"):
        faults.append("names no landing.release_cli, the release command line relative to landing.checkout, so no deploy state could be read")
        members.append(f'"release_cli": {json.dumps(RELEASE_CLI if (Path(landing["checkout"]).expanduser() / RELEASE_CLI.split()[0]).is_file() else "<release command line>")}')
    if not faults:
        return ""
    return f"{'; '.join(faults)}; add to the \"landing\" object of runner.json, replacing any member of the same name: {', '.join(members)}"


def run_landing(runner: Runner, once: bool) -> int:
    if "fidelity_lane" not in runner.config.landing:
        why = f"{runner.config.source} names no landing.fidelity_lane, so no fidelity grade would count; the runner refused to start"
        runner.escalate(f"fidelity-lane-unset:{runner.config.source}", "FIDELITY-LANE-UNSET", "runner", why)
        runner.flush()
        print(why, file=sys.stderr)
        return 3
    if runner.config.landing.get("merge_mode") not in MERGE_MODES:
        why = f"{runner.config.source} names no landing.merge_mode of {', '.join(MERGE_MODES)}, so the owner's merge mode is unknown; the runner refused to start"
        runner.escalate(f"merge-mode-unset:{runner.config.source}", "MERGE-MODE-UNSET", "runner", why)
        runner.flush()
        print(why, file=sys.stderr)
        return 3
    if why := mq_config_fault(runner.config.landing):
        why = f"the runner refused to start: {runner.config.source} {why}"
        runner.escalate(f"mq-config:{runner.config.source}:{hashlib.sha1(why.encode()).hexdigest()[:8]}", "MQ-CONFIG-INVALID", "runner", why)
        runner.flush()
        print(why, file=sys.stderr)
        return 3
    seed_policy(runner)
    landing = Landing(runner, runner.config.landing)
    interval = runner.config.landing.get("interval_seconds", 180)
    while True:
        started = runner.now()
        try:
            landing.run()
        except CciTailFailed as failure:
            runner.tail_failed(failure)
        except OrcaUnreachable as down:
            runner.orca_down(down)
        behind = runner.stale("landing")
        runner.flush()
        runner.beat("landing")
        if once:
            return 0
        if runner.rest(max(0.0, interval - (runner.now() - started).total_seconds())):
            runner.stop_marker().unlink()
            return 0
        if behind:
            runner.reexec("landing")


def launch_model(model: str) -> str:
    if not MODELS.fullmatch(model):
        raise argparse.ArgumentTypeError(f"{model} is not a model orca-launch.sh starts: opus, sonnet, fable, claude-*, sol, codex, incident, astra, or gpt-*")
    return model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desk-runner.py", description=__doc__.split("\n", 1)[0])
    verbs = parser.add_subparsers(dest="verb", required=True)
    relay = verbs.add_parser("relay")
    relay.add_argument("--key", required=True)
    relay.add_argument("--lane", required=True)
    relay.add_argument("--text", required=True)
    relay.add_argument("--reply-to", default="")
    relay.add_argument("--deadline-minutes", type=int)
    launch = verbs.add_parser("launch")
    launch.add_argument("--key", required=True)
    launch.add_argument("--lane", required=True)
    launch.add_argument("--model", required=True, type=launch_model)
    launch.add_argument("--effort", required=True, choices=EFFORTS)
    launch.add_argument("--brief", required=True, type=Path)
    launch.add_argument("--owner-directed", action="store_true")
    launch.add_argument("--role", default="")
    policy = verbs.add_parser("policy")
    policy.add_argument("--key", required=True)
    policy.add_argument("--landing", choices=LANDING_POLICIES, required=True)
    policy.add_argument("--revision", required=True)
    policy.add_argument("--source", required=True)
    policy.add_argument("--supersedes", default="")
    loop = verbs.add_parser("run")
    loop.add_argument("--desk", choices=DESKS, required=True)
    loop.add_argument("--once", action="store_true")
    rebind = verbs.add_parser("rebind")
    handoff = verbs.add_parser("handoff")
    handoff.add_argument("--desk", choices=DESKS, required=True)
    show = verbs.add_parser("show")
    gc = verbs.add_parser("gc")
    gc.add_argument("--dry-run", action="store_true")
    for sub in (relay, launch, policy, loop, rebind, handoff, show, gc):
        sub.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load(args.config)
    runner = Runner(shell or Shell(), config, actions.Store(config.store))
    if args.verb == "run":
        runner.shell.adopt(config.desk_env[args.desk])
        return (run_orca if args.desk == "orca" else run_landing)(runner, args.once)
    if args.verb == "show":
        sys.stdout.write(runner.render())
        return 0
    if args.verb == "rebind":
        return runner.rebind()
    if args.verb == "handoff":
        return runner.handoff(args.desk)
    if args.verb == "gc":
        try:
            return runner.gc(args.dry_run)
        except OrcaUnreachable as down:
            print(f"orca unreachable: {down}; nothing more was reclaimed", file=sys.stderr)
            return 1
    if args.verb == "relay":
        action, created = runner.accept_relay(args.key, args.lane, args.text, args.reply_to, args.deadline_minutes or config.start_minutes)
    elif args.verb == "launch":
        brief = args.brief.expanduser().resolve()
        if not args.owner_directed and (holder := runner.duplicate_holder(args.lane, brief)):
            runner.record(f"escalation:launch:{args.key}", duplicate_line(args.key, args.lane, holder))
            runner.flush()
            print(duplicate_line(args.key, args.lane, holder), file=sys.stderr)
            return 1
        action, created = runner.accept_launch(args.key, args.lane, args.model, args.effort, str(brief), args.owner_directed, role=args.role)
    else:
        action, created = runner.accept_policy(args.key, args.landing, args.revision, args.source, args.supersedes)
    posted = runner.flush()
    print(f"{action.action_id} {action.kind} {action.status}{'' if created else ' (already accepted)'}")
    return 0 if posted else 1


if __name__ == "__main__":
    sys.exit(main())
