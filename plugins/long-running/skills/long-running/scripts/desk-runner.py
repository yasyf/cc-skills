#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
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
a delivered relay is never failed or reported again. A relay about a PR at a newer head,
while the lane has not answered the one it was sent about an earlier head, folds into
that one and sends nothing. A lane answers its mail only between turns, so a heartbeat
it sends after a relay counts as its acknowledgement, and a lane that sent one within
thirty minutes before the relay gets thirty minutes before a `DEADLINE`. A lane has no live dispatch only
when it has no receipt or Orca answers that its dispatch is gone. When Orca gives no
answer, a runtime or connection error, a timeout, or output that is not its JSON, the
pass stops there and changes no dispatch's liveness and no relay's state. The first
such pass logs `ORCA-UNREACHABLE` once for the streak, each failed pass doubles the
wait before the next, up to two minutes, and the first pass Orca answers again logs
`ORCA-BACK`. Every pass Orca answers, the first after `ORCA-BACK` included, reads the Run's
worker list once and logs one `LANE-GONE` for each lane with a receipt whose dispatch was
live on the last pass Orca answered, or is still listed as dispatched, and that Orca now
shows gone, completed or failed, or whose terminal Orca finds missing, unless the runner
withdrew it, relayed its `worker_done`, or the lane's newest record ends its work; it names
the dispatch, the lane's last record and its age, and the outage that just ended. `cci post` keeps only the first
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
`not-ours`, `stopped`, and `stand-down`, a `report` or `incident` that starts `READY`, a
record of any kind whose text says mechanism, and its `mechanism:`, `ready:`, and
`fix-live:` Run statuses. A lane's records
count under `<name>` and `<name>-fix` alike, since the desk launches `<name>-fix` and its
lane may post as `<name>`. With no mechanism at
`deadlines.incident_mechanism_minutes` (default 10), or no PR at
`deadlines.incident_pr_minutes` (default 15), the runner launches `<lane>-opus` once per
launch, urgent, on opus xhigh in Claude fast mode, from the lane's own brief under a
fallback header it attaches to the briefs log as `<lane>-opus.full.md` and writes to
`<state>/briefs/<lane>-opus.full.md`, the path the blocker names, and logs
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
stops at `ready`, `fix-live`, `not-ours`, `stopped`, or `stand-down`, at a root `decide`
addressed to the lane or naming it, or when the dispatch settles, and `show` prints each clocked lane's minutes and milestones. Any milestone a sibling lane on the same
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
`CLASS` or `NEW CLASS`, that declares a class, by its topic or an incident tag in its `CLASS` line or a leading `[TAG]`,
and has no later root `go`, `decide`, or `decision` naming one of them `deadlines.pr_silent_minutes` after it logs one
`CLASS-UNOWNED <class>` blocker per class. A tag elsewhere in the text, such as a step label or a hash in an evidence
line, declares no class.
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
incident launch is never held: it starts anyway and logs one `LOAD-INCIDENT` report naming
the load. An `--owner-directed` launch fails at once with `LAUNCH-FAILED`; any other launch
waits for at most `deadlines.load_hold_minutes`, then reports the failure to root through
cci and the Run mailbox. Those Mac launches are only a brief whose `ccx:` line says
`place=local`, `role=desk`, or `role=watch`, a Fable or incident launch, and an incident
clock fallback. Every other launch runs `orca.sprite.launcher`, the repository's
worker-launch.sh, on a Sprite and never waits on Mac load. The runner counts no Sprites
and sets no limit: the provider admits or refuses each one. A Sprite launch fails at once
with `LAUNCH-FAILED` when no `orca.sprite` is configured, when a Sprite refuses its lane
name, or when its receipts prove worker-start never ran, a provider refusal among them;
nothing starts locally in its place.
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
Each prefix passes the check registry, `landing.checks` in order, every built-in when it names none, and `hold` first whether it names it or not: `hold`, `precondition`,
`rules-review`, `rulings`, `fidelity`, `finder`, and `merge-mode` are the desk's own, and `approvals` and `required-ci` are the adapter's verdict;
`old-app` and `build-failed` run only when `landing.checks` names them. Trees gate in
`(priority, root PR)` order, a tree's priority the lowest ledger `priority` among its PRs, and accepted enqueues run one parallel
wave per priority, lower first. A check that reads no verdict first settles its tips through `ledger.py landed`, which
reads each tip's squash on the freshly fetched trunk, and drops every tip whose row then reads landed; only the tips left
log `GATE-FAILED`. A check killed at its 60-second cap has no verdict yet, logs nothing, and runs again next pass. `mq.py` reads and steers the same queue. Each landing pass first fast-forwards the landing checkout to the remote trunk, so every
adapter call runs the trunk's script; a checkout its repository no longer lists as a worktree, or one on a branch with no commit, is rebuilt at the fetched trunk head with a clean tree,
re-registered through `git worktree add --no-checkout` and `git worktree repair` when git lost it, logs `MIRROR-REBUILT` to root, and the pass goes on;
a checkout that still cannot fast-forward logs `CHECKOUT-STALE` to root and gates nothing that pass.
Right after the ledger and holds read, the pass reads the queue, then gates every tree holding a PR whose ledger head a root `go`
record names, sixteen trees at a time, so the only PRs that can land reach the queue before the ledger-wide head read and the gate
over every other tree. Every gate checks only a tree holding a PR the queue read calls landable on PRs below it that are landable
or queued, since a check of any other tree enqueues nothing and spends GitHub reads; each PR of the rest hears what its status
entry asks of its lane. Each tree runs in a stack worker of its own from its check through its settling to the enqueue it accepts, so no
stack waits on another stack's checks or enqueue. The workers outlive the pass: the pass queues them with the lanes taking turns, waits for
them at most 120 s, skips a tree whose worker still runs or waits, and its releases, announcements, worktree removal and restack checks run
on a side worker, never ahead of the gate. Each worker writes its step and when it began to `<config>.landing-stacks.json`, which
stall_watch reads to name a stack stuck past its step's budget. Every pass posts one `state` record to root on topic `landing-state`
counting the queued, waiting, held and refused PRs, the workers running and the overflow's enqueues, with the pass's seconds.
`GO-UNQUEUED` is raised only when the newest enqueue of a go'd head since its go ended blocked, failed, stranded or unsettled, or went in and the queue's merge activity then evicted it, naming that eviction's time and reason, never for
backlog, and `OVERFLOW-STALLED` when the overflow thread finished no run in five minutes. A go'd head whose queue draft closed unmerged is taken out with the adapter's dequeue once per head, posting `ORPHAN-RELEASED`, and enqueued afresh, never `--recover`ed. Beside the passes, an overflow thread reads afresh every minute and puts straight
into the queue, with none of the runner's checks, each stack that has stayed green, named at its heads by a root go, unheld, unqueued
and unrefused for five minutes, posting a QUEUED report that names the overflow (owner 9524276). The queue read is one `ccx vcs pr status` call over every open row a pass,
allowed 300 s, which the gate, the requeue and the landed check share. A PR body is read at most once per five minutes, a head's
`check-pr` that waits on an owner answer runs again at most once per five minutes, and the trunk's statuses are read only when
`git ls-remote` shows the trunk moved or its newest check was unfinished five minutes ago (root #146561).
A prefix `ccx vcs pr status` reads out of the queue at the heads it admitted is classified from the adapter's verdict on each dropped
PR, never from comment text. A `conflict` stays out at that head and its ejection goes to the lane as the gate's blocker. An `own-red`
is gated again, and the adapter blocks it until a new head. A `batch-mate-red` the adapter retries is gated again; one blaming a PR outside the
PR's own stack is the batch's failure: it routes no blocker, logs one `AWAITS-GO` line to root, and the same head re-enqueues once a root
`go` record naming the PR is posted after the drop, with no new head. A `flaky-check`, a red check that read green on the PR's own head and
failed only in a queue draft holding nothing but its stack, stays out at that head after its first ejection: one `blocker` naming the failing
job goes to the live lanes `landing.restack_lane` names, else the root, and the desk enqueues the PR again only at a new head. The lane a PR's blocker or restack goes to is the poster of the newest cci `head`, `opened`, `ready`,
or `claim` record naming the PR, else the lane that registered its ledger row; a blocker posted on cci also goes to the root
when `cci who` lists no live session for that lane, and to the live lanes `landing.restack_lane` names instead, else the root
alone, opening with `owner lane dead`, once that lane has no live dispatch or session and no cci record on the drive in the last 60 minutes. A conflict blocker for a lane with no dispatch Orca
shows active and no session `cci who` lists, but a record in that hour, routes nothing: the root gets one `PARKED` line per PR, and that head routes once the lane is back. A gate's `BLOCKED` verdict goes to the lane at most once per head, as the blocker its adapter names, and only for red CI,
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
names is held until a `lift --resolves <seq>` closes that record. No check or enqueue call names a held PR or a PR stacked above one: the gate names
the tips below it, and a prefix a check would sweep it into is cut below it, with one `HELD-OUT` line to root naming the hold. A `hold:all <reason>` line in
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
grade names carries the ok of an older head whose lanes all stand at ok, when `git patch-id --stable` of the PR's own
commits at both heads, GitHub's compare of `<head>~<n>...<head>` with n the PR's commit count, is equal, so a restack whose
parent also moved still matches (root #131054), cited as `fidelity: ok at <head>, patch-identical to <graded head>`;
a compare GitHub refuses for the PR's own head logs `PATCH-READ-FAILED` once and carries nothing. The compare is read with
`--allow-escape-sequences`, so a diff that carries terminal escape sequences reads as bytes for `git patch-id`.
Any other head gets one cci `blocker` per head and grade, to the PR's lane and the fidelity lane, and any accept check
that refuses a PR cuts the prefix below it: the PRs under the cut enqueue on their own with `--partial`, leaving the refused PR
and every PR above it out, and the refused PR's blocker names it as the cut. Graphite keeps one linear chain per batch, so a
prefix that forks enqueues one call per chain in the same pass, bottom chain first, each naming its top with `--partial`; a
prefix stacked on an open fork outside it logs `FORK-DEFERRED` and waits until that fork lands. The `finder` check reads every review record that names the finder, from any lane, as a verdict in plain
words: `finder: ok`, "no majors", "no findings" or "ok" pass, and `finder: major` or "N major" fail. The head is the record's first short
sha, or else the head the PR's newest `head` record named before it. The newest record naming the head must pass, unless a root `go` at
that head came after it or a root `decision` replies to it, directly or along a chain of `--re` replies (rule 2cc5a1f3); a head with none,
or whose newest fails, gets one `FINDER-PENDING` blocker per head and failing record to its lane and `finder-desk` (E4.1). A record the runner cannot read for a verdict or a head
gets one `note` to its lane naming the line it reads. The `old-app` check reads a fidelity lane's `old-app: unchanged`, `old-app: bug fix` or
`old-app: CHANGES` line, in a review or the body a fidelity grade spills to, at the head the finder rule reads; a head whose newest grade is
neither of the first two, carried along a chain of fidelity carries, gets one `OLD-APP-PENDING` warning to its lane and the fidelity lanes and is not held for it.
The `build-failed` check reads `landing.build_failures`: `tree`, a path prefix such as `go/sand/next/`; `context`, the Buildkite annotation
context whose list names each package a build failed to compile with its first compile error, such as `go-build-failed-sand-next-tests`; and
`annotations`, a command that prints one build's annotations, such as `tools/ci bk api /pipelines/{pipeline}/builds/{number}/annotations`.
A head whose PR changes a file under `tree` gets one `BUILD-FAILED` blocker per head and package to its lane while a finished build at it
lists a package the PR changes, or one whose Go files import a package the PR changes, by the head's tree in the landing checkout; it is
held until a new head's build no longer lists it. A package the same pipeline's finished build at the head's merge base with the trunk
lists with the same compile error is inherited, not the PR's: the head is not held for it and gets one `INHERITED-BUILD-FAILURE` `note` per head
and package to its lane instead. The colour of the step that posts the annotation never counts, since it may soft-fail, and a
head that changes nothing under `tree` is never held for a package another PR broke. A read that fails holds nothing (0afd2f6). A finder ok of an older head
carries to a head with no finder record when both heads' own patches match, as a fidelity ok does, or when a fidelity desk's `fidelity: ok <head> carried from <earlier head>` grade, read along its chain, reaches a head whose finder verdict is ok, unless the grade says the change adds new behavior. A PR whose body opens with "Red by design", or whose lane's newest
`contract` record with `--pr` naming it names its failing run ("red by design", "fails in run", "failing run"), draws no
blocker for its failing required checks, and a blocker relay already sent on it draws no `DEADLINE`.
The `rulings` check runs `rulings.py check-pr` once per head a pass and holds no head: each cause it prints but `RESERVED-NO-ANSWER`,
which merge mode settles, is a warning, one `note` record per head and set of causes to the PR's lane (4236611); a head warns again only for a cause
none of its earlier warnings named, and never once an `answer` replies to or resolves one of them. A run that exits with a code other than 0 or 1 or
prints a line naming no cause passes the head with nothing posted, and so does each `BLOCKER` line it prints: the check fails open.
Each pass reads the commit statuses of every open row's head in one GraphQL query, and reuses a
head's statuses for 60 s; a head read that fails counts as unread for that pass, and the pass ends with one
`STATUS-READ-FAILED` per cause naming the count and each PR and head, once per cause and set of heads. Every GitHub read goes through
`ccx vcs gh --max-age 5m`, which answers a read made in the last five minutes from ccx's machine-wide cache; the incident squash's
precondition reads pass `--max-age 0s`, and the trunk's tip is read with `git ls-remote`. Each pass starts with one `ccx vcs auth status --json`
read of every GitHub quota, the user's and the GitHub App installation's, REST and GraphQL. Any quota with under 1500 calls left posts
one `urgent` `GITHUB-QUOTA-LOW` to root per reset, naming the calls left, the reset and the five callers that spent the most. With a
quota of the identity the desk reads through under ccx's 1000-call floor, the pass reads nothing from GitHub and
logs one `GITHUB-BACKOFF` naming the calls left and the reset per quota window; accepted enqueues stay accepted and root `go` records
stay on cci, so both run on the first pass after the reset. A quota read that fails logs `QUOTA-READ-FAILED` once an hour, and the pass runs. A `cci tail` that fails in a landing pass
ends that pass before it gates or enqueues further; it logs `CCI-TAIL-FAILED` and the runner keeps running.
An incident fix PR on the trunk, one whose ledger lane is an `incident-` or `outage-` lane or a fix lane the runner clocks, or whose
number an open executor incident names as its `repair_pr`, and no root go, hold, lift, decide or release naming it says it is not an incident fix, takes it
off the incident path, or owes no release, which also settles any release its landing owes, which logs `INCIDENT-TAG-CLEARED` once and gates it as a normal row, is never enqueued: in the pass its head reads `APPROVED` and every
required check passes, `rulings.py check-pr --incident` names none of `RULINGS-UNCITED`, `RESERVED-NO-ANSWER`, or `TEST-REMOVED`,
and the holds file, read again just before, holds no line over it,
the runner runs `gh pr merge <n> --squash --match-head-commit <head>` once per head, recorded as
`land:<pr>@<head>`, and posts `landed` to the PR's lane and the root; a merge GitHub refuses logs `SQUASH-FAILED`. A head
those checks name is not merged: it draws one `RULINGS-REFUSED` blocker to the PR's lane and the root per head and set of causes,
and merges in the first pass they clear (45200f2). Every other cause `check-pr` names stays a warning (4236611).
In a pass whose ledger reads a PR newly landed, the runner reads `git worktree list --porcelain` of the checkout once and runs
`ccx vcs worktree rm --path <tree>` on each worktree whose branch is a landed PR's head branch and no open PR's; a refusal, such as a
dirty tree or one a live process works in, logs `WORKTREE-KEPT` once per tree, is never forced, and is tried again in the next pass that reads a new landing.
Neither grade
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
With a `finder` object in runner.json, read again every pass, each orca pass, and a thread of the orca desk every 15 seconds
between them, dispatches the finder (reference/finder-brief.md) on every drive head, beside the fidelity desks and never
after them, so a slot a finished pass frees refills in seconds. A head whose PR a root `go` or an owner's board tap names
starts first, then a head of a PR a finder pass already graded, which carries or reads only a delta, then the PR that has
waited longest, counted from the first of its heads a newer one superseded, so a PR whose head keeps moving keeps its place. Each `head` or `opened` record
past the saved sequence that names a PR of `finder.repo`, default `landing.repo`, and a full head, its `commit` field or
the first full sha of its text that no `was` precedes, queues one `finder:<pr>:<head>` action in the `desk-finder`
container, so a head two records name queues once; a first read starts at the newest record. Each pass also reads the ledger once
and queues the green head of every open drive row whose PR has no pass waiting or running, so a restack that posted no `head`
record is graded with no hand requeue. At most `finder.concurrent`
passes (default 8) run at once and the rest wait queued; each PR's newest queued head starts, a lower ledger `priority` first, then longest waiting, and an older
queued head of the same PR settles as superseded, and so does a running pass of it: the runner writes its job's `cancelled`
mark and runs `codex-ask --cancel` on each codex run the pass started. Before it starts any, one fresh GraphQL read of every
queued PR settles a head whose PR is closed, or no longer at that head, as skipped. A head some lane's `finder:` record already names starts nothing. A root
`ask` to `finder-desk` that names a PR and says `finder: whole` queues one `full` pass of that PR that
starts ahead of the rest and wins over its queued heads: it reads the PR's whole own change at its live head even when a record
names that head, unless a whole-range finder record about the PR is newer than its newest `head` record. A pass
writes `<config dir>/finder-desk/<pr>-<head12>/job.json` and starts `finder.py` on that directory detached, which runs
`codex-ask -m <finder.model, default sol> --incident --schema findings`, at the fast tier unless `finder.fast` is false, in `finder.checkout`, default `landing.checkout`, over
the head's own commits against the base branch's current tip, or over what changed since the head the PR's newest finder
record at another head read, else since the head its `head` record replaced when the PR has any finder review
(finder.py), and posts
`finder: ok <head> …` or `finder: major <head> <n> open: …` under lane `finder-desk` to the head's lane, root, the runner
and each `finder.to` address, since the landing runner reads finder records by address. A pass whose job wrote an error, whose
process (its pid in `pid` beside `job.json`) exited with no result, or that wrote no result within `finder.timeout_minutes` (default 60)
fails that pass, freeing its slot, and its job queues again as `<key>#<n>` in its own `-<n>` directory, up to three attempts; the
third failure logs `FINDER-FAILED` once. A head the landing check refuses `FINDER-PENDING` with no finder record queues one more
attempt when no pass on it waits or runs, so a job a pass skipped while its PR was closed, or one no record queued, runs again.
While passes wait queued and none has settled for 15 minutes, one `FINDER-DESK-STALLED` per streak names how many wait and each
running pass's age. `show` lists each queued and running pass.
With a `fidelity` object in runner.json, each orca pass also dispatches one fidelity grade per drive head the same
way: each `head` or `opened` record queues one `fidelity:<pr>:<head>:<seq>` action in the `desk-fidelity` container, and
the green head of each open drive row with no pass waiting or running queues one `fidelity:<pr>:<head>:ledger`,
the newest queued head of each PR starts, a lower ledger `priority` first, while fewer than `fidelity.concurrent` (default 8) run, and a head any
fidelity lane, `fidelity-pipeline` included, already graded or sent to a reader starts nothing. A pass writes
`<config dir>/fidelity-pipeline/<pr>-<head12>-<seq>/job.json` and starts `fidelity.py` on it detached, which carries
an earlier head's ok with no model call when its own commits are patch-identical, else runs `codex-ask -m
<fidelity.model, default sol> --incident`, at the fast tier unless `fidelity.fast` is false, over the head's own diff, its required register rows, the plan, the lane's newest
contract and the PR body, and posts the fidelity line and the `old-app:` line against `fidelity.baseline` under lane
`fidelity-pipeline` to the head's lane, root, the runner and each `fidelity.to` address; a head it cannot settle goes to
the other `landing.fidelity_lane` lanes as `fidelity: reader`. A pass whose job wrote an error, or no result within
`fidelity.timeout_minutes` (default 60), fails at once and logs `FIDELITY-FAILED` to those lanes; the next `head`
record for the PR queues it again. `show` lists each queued and running pass.
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
UNGRADED-30, and SLICE-UNREAD to blocker, any *-FAILED to defect, and all other labels to report.
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
With `landing.ci_build` set to a command that prints one Buildkite build's JSON, such as
`tools/ci bk api /pipelines/{pipeline}/builds/{number}`, a GREEN head whose passing build has a job named
"Redundant within its stack" reads BLOCKED `unproven: redundant skip, child #<n> <state>` and drops out of the prefix
that would enqueue, with everything above it. It is GREEN again once a passed build with no skip job runs on a commit that
contains the head: a build at the stack's tip, or one a `full run green <build>: #<pr>` note or report from the PR's lane
names over the last two days. With `landing.ci_grade` set to a command that starts one full build of `{commit}` on
`{branch}` in `{pipeline}` and prints its JSON, a head no tip build covers or is still building gets that build on a
`ci-grade/<pr>` branch, started once per head, and is GREEN once it passes; a failed start logs `CI-GRADE-FAILED` once
per head.
With `landing.trunk_check` set to the trunk's required status context, such as `buildkite/test`, every landing pass reads
that context over the trunk's last 50 commits in one GraphQL query and posts the trunk's state on the `dev-red` cci topic
to root and drive-watchdog. A red run, skipping commits whose check has not finished, names its first red commit, the PR
its squash subject names, the newest red build and, through `landing.ci_build`, its failed jobs. When a failed job is one
any open row's finished PR build of that pipeline runs, or no PR build reads, the record is
an `incident` reading `DEV-RED since <time>: … No PR can land until this is fixed; nothing lands until #<fix> fixes it`.
When every failed job is one no PR build runs, it is a `report` reading `POST-MERGE-RED since <time>: … Landing continues;
a fix is owed`. The runner holds no row for either: the queue's required checks hold what cannot land. Either posts
the pass it is first read and again every 30 minutes while red; `DEV-GREEN` posts once when the check passes again. The
fix is the PR the newest record on the topic by any other lane names, an incident excepted, since the last `DEV-GREEN`:
`cci post --topic dev-red --pr <n>`. With `landing.ci_build` set too, each newer finished trunk build is read once and
compared with the one before it: a job name that fails or soft-fails more often posts one `POST-MERGE-RED` to root, each
`landing.desk_lane` and the lanes of the PRs that landed between the two, so a build green only by soft-fail reads red.
A trunk that does not read gates and enqueues nothing that pass and reads again
the next; `TRUNK-READ-FAILED` posts once per run of such passes, and `TRUNK-READ-RECOVERED` once a read succeeds.
`landing.release_cli` is the repo's release command line, such as `tools/ci release`, its first word relative to the landing
checkout, which `run --desk landing` requires: without it the desk refuses to start with `MQ-CONFIG-INVALID`, whose line names it
among every missing member.
The config requires `drive` and `orca.desk_inbox`, and `run --desk landing` requires `landing.release_owner`,
a cci `--to` address such as `peer:<session>`, and `landing.merge_mode`, the owner's pick of `every`, `high-risk`, or `auto`,
re-read on every use. A repository the GitHub account the runner acts as owns, which `gh api user` names, lands once green under every mode (de6c88c). Otherwise,
under `every`, under `high-risk` for a PR whose changed files a `landing.high_risk_paths` glob names, and in
every mode for a head `check-pr` reads as touching a reserved row no answer authorizes, a prefix
that would enqueue posts one PR card per waiting PR and head instead, logs one `MERGE-ASK` naming it, which with a blocker to the PR's lane quotes a `title: stale` line from the PR's newest fidelity grade, and enqueues once a root `go` record names each
waiting PR and its head by a short sha in a `go` or `merge` clause, `go #<n> at <sha>`, wherever in the text that clause
starts, or carries one: a fidelity desk's `fidelity: ok <head>` grade naming the earlier head whose first sentence says patch-identical, tap carries, no new behavior, test-only, or pure replay, on a PR whose go names the earlier head, is a go at the new head, and a carried grade for added behavior carries nothing; a clause ending `with #<a> #<b>` binds its PRs and those into one batch that enqueues whole or not at all, one `BATCH-WAITS` line to root naming the PRs a partial batch keeps out; `show` names the `merge:<pr>:<short sha>` key each ready head still waits on. The card goes through `cc-present ask` with `--session` set to the drive root's newest session in the drive registry,
so it lands on the root's board and in the root's Slack status thread like every other card: the question, the PR's title, author, size, and review state, the
PR linked with its live checks, waiting on `merge:<pr>:<short sha>` with `Merge (Recommended)`, `Hold`, `Other`, and `Explain more` (93037c3); a card already
open for the head is edited in place, never re-posted, and a post that fails
logs `MERGE-CARD-FAILED` once per head and cause and tries again next pass. With `landing.merge_ask_surface` set to `board`
(default `slack`), the runner posts and edits no card, and the root asks every merge question on its own board. On that surface each pass
carries the owner's board taps: an approval on a `pr-groups` row of the root's board at an older head of an open PR, with no verdict at
its ledger head, moves to that head through `cc-present verdict carry` once `head` records reading `<new> (was <old>)` and fidelity carries
call each move between the two patch-identical, with one `TAP-CARRIED` note naming both heads to root and each lane `landing.board_lane`
names, one lane or a list, and, when no go names the new head, one `go` record of the runner's own for it, which `go_lane` counts. A new head a record names with no such chain, a fixed version, is never carried. A record saying it adds new
behavior draws one `TAP-NOT-CARRIED` note to the same lanes saying the board needs a new row; a fidelity ok grade naming none draws one
`TAP-KEPT` note saying his tap keeps over that review fix, which still waits for root's go naming the fix (rule 399b5fa, root #195224), and once a go
names the head the runner carries the tap there itself, citing the grade and the go; a root `go` or a
patch-identical fidelity carry grade naming that head otherwise draws neither. A board that does not read, or a carry cc-present refuses for any cause but an absent merge block, which leaves nothing to carry, logs `TAP-CARRY-FAILED` once per
cause and tries again next pass. An exempt change, one whose fidelity grade and whole diff
make it a pure port, test-only or CI-only (`EXEMPT-ADMITTED`), needs no go and no tap in any mode: its tree gates with the go'd ones, it asks no
merge question, and it goes straight to Graphite's queue once the hold, fidelity, finder, build and CI checks pass (root #194387). Under `every` the runner
then bars each other open PR no root `go` names at its head like a held one: no check or enqueue call names it or sweeps it in, and a PR
stacked on it waits. The pass's one `ccx vcs pr status` read routes it instead: a conflict, red CI, or requested changes is that head's
blocker, and a landable head runs the accept checks, whose merge-mode check logs its `MERGE-ASK`. `landing.checks_wait_for_go`, a cci sequence number, pauses the gate until a root `go` record comes after it:
no `stack-enqueue --check`, `MERGE-ASK`, or enqueue runs meanwhile, one `GATE-PAUSED` says so, and the rest of the pass goes on,
an incident fix's plain squash included. A reserved touch puts the first sentence of the row's words in the card's question, whose
tap is the authorization: only an answer `rulings.py authorize` labels `authorizes:<key>` from that captured tap covers the row, which `check-pr` then
stops naming, and a root `go` never does, whatever it names (C224). A quiet pass writes nothing.
The orca runner rotates every *.md inbox beside `orca.desk_inbox` once an hour,
using the default six-hour window. While `runner.json` holds a `stall` object, read every pass so an edit needs no
restart, the orca runner starts `stall_watch.py --config <config>` detached every `stall.every_minutes` (default 5)
once its last pass has exited, logging to `stall-watch.log` beside `orca.desk_inbox`.
Each pass of either desk writes its heartbeat beside `orca.desk_inbox`, naming its pid, config, restart command, and the
plugin version the runner runs. A runner behind the version `installed_plugins.json` lists posts nothing and re-executes into the installed version after
that pass's wait, keeping its pid, config, and desk; `--once` never re-executes. An `env` object under a desk's config
object, such as `"landing": {"env": {"AWS_PROFILE": "release-operator"}}`, is merged into that desk's process environment
each time `run` starts, a re-execution included, and is carried by its restart command and its `handoff` successor. `handoff` stops the live runner at the next
boundary between the steps of its pass, or between the PRs and stacks a landing step works through, and starts the next one with the same config and desk, appending to `<desk>-runner.log`. Each `run` holds
`<config>.<desk>.lock` for its life and refuses to start, naming the holder's pid, while another runner holds it; `handoff` stops
that holder and waits up to 20 minutes, as long as a slow pass takes, for it to exit, or for its heartbeat to show it
re-executed into the handoff's own version under the same pid, which withdraws the stop and starts nothing; past that it
starts nothing and logs `HANDOFF-STALLED` to root with the pid. `handoff` stops nothing while the install lacks a runtime brief or any file of
`reference/` or `scripts/` that the marketplace copy it was installed from holds. `drive.py digest`
prints each desk's running and installed versions.
While ruling cards `rulings.py classify` wrote queue under the drive's `cards/`, the landing runner logs one `RULING-ASK`
per board round, however many cards join it; the root runs `rulings.py round`, which puts at most ten of them on the live cc-present board as one
round, foundational rows first.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
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
import traceback
from collections import Counter
from contextlib import contextmanager
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as finished_by
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

import actions
import board_rows
import cci
import drive
import fidelity
import finder
import ghread
import holds
import incident
import inboxes
import lanes
import merge_queue
import prompt_screen
import prompt_watch
import stall_watch
import testfiles
from grades import ADDS_BEHAVIOR, FIDELITY_MISSING, FIDELITY_OK, FINDER_PASS, OLD_APP_PASSES, SHORT_SHA, about_pr, carried_go, closes, fidelity_carries, fidelity_grades_in, finder_records, finder_reviews_in, carried_gos, go_batch, go_heads, gone_at, goes, full_text, graded_head, head_moves, heads_since, missing_grounds, owner_grounded, names_head, names_pr, newest_finder, newest_naming, old_app_grades_in, patch_chain, precondition_sentences, repo_prs, standing
from grades import carry_of, delegated, keeps_tap, succeeds
from grades import carries as carry_grade
from grades import PORT_PHRASE, QUOTED_BLOCK, QUOTED_SPAN, TEST_ONLY_PHRASE, TEST_ONLY_PHRASES
from grades import WITHDRAWN as WITHDRAWN_OPENING
from grades import owes
from grades import REVIEW_BOT_CHECK, REVIEW_BOT_HELD, review_bot_wait, withdrawn
from incident import CLAUDE_FAST_MODE, CLOCK_FALLBACK, latest

SCRIPTS = Path(__file__).resolve().parent
RUNNER_BIN = SCRIPTS.parents[2] / "bin" / "desk-runner.py"
PACIFIC = ZoneInfo("America/Los_Angeles")
LANE_PREFIX = "desk-lane-"
LANDING = "desk-landing"
RULING_CARDS = "cards"
MERGE_CARDS = "merge_asks"
MERGE_WAITS = "merge_waits"
MERGE_ASK_SURFACES = ("slack", "board")
EXEMPT_GO = re.compile(r"\bgo\s+(?:port|test-only|ci-only)\s+#\d+|\bpure port\b|\b(?:test|ci)-only\b", re.IGNORECASE)
CLASS_WRONG = re.compile(r"(?:re #\d+:\s*)?class wrong\b", re.IGNORECASE)
TEST_ONLY_CLAIM = re.compile(r"\b(?:test|ci)-only\b", re.IGNORECASE)
TAP_CLAIM = re.compile(r"(?<![\w-])(?<!\bno )(?<!\bnot )(?<!\bwithout )(?<!\bwithout his )(?<!\bwithout your )(?:taps?|tapped|approved|(?:his|your|the owner's)\s+approval)(?![\w-])", re.IGNORECASE)
OLD_APP_LINE = re.compile(r"^old-app:\s*(?:unchanged|bug fix|changes)\b.*$", re.IGNORECASE | re.MULTILINE)
TEST_DIRS = frozenset({"__tests__", "testdata", "fixtures", "docs"})
CI_PREFIXES = (".buildkite/", ".github/", "tools/ci/", "infra/ci/")
TOOLING_GLOBS = (".agents/skills/*/scripts/*", ".agents/skills/*/tests/*")
PORT_PREFIXES = ("go/sand/next/",)
OWNER_CITE = re.compile(r"#\s*owner:\s*[0-9a-f]{7,40}\b")
MERGE_CARD_FIELDS = "title,author,additions,deletions,reviewDecision"
SLACK_MESSAGE = re.compile(r"^slack: \S*/p(\d{10})(\d{6})\b", re.MULTILINE)
RULING_ROUNDS = "cards/rounds"
RUNNER = "desk-runner"
ORCA_DESK = "orca-desk"
RUNNER_LANES = frozenset({RUNNER, ORCA_DESK, finder.LANE, fidelity.LANE, stall_watch.LANE, prompt_watch.LANE})
FINDER = "desk-finder"
FINDER_CURSOR = "cursor"
FINDER_CONCURRENT = 8
FINDER_MINUTES = 60
FINDER_MODEL = "sol"
FINDER_FAST = True
FINDER_ATTEMPTS = 3
FINDER_STALLED_AFTER = timedelta(minutes=15)
FINDER_SETTLED = "settled_at"
FINDER_SKIPS = {"CLOSED": "#{pr} is closed", "NOT_FOUND": "#{pr} is no pull request in {repo}"}
FINDER_STALLED = "stalled"
FIDELITY = "desk-fidelity"
FIDELITY_CONCURRENT = 8
FIDELITY_MINUTES = 60
FINDER_EVERY = timedelta(seconds=15)
FINDER_TAPS_EVERY = timedelta(minutes=5)
LAUNCH_STATE = Path(".claude/scratch/orca-launch")
CCI_TEXT = 400
ESCALATION_KINDS = {"DECIDE": "decide", "INCIDENT": "incident", "INCIDENT-OWNED": "state", "UNBOUND": "blocker", "UNOWNED": "blocker", "CLASS-UNOWNED": "blocker", "UNACKED": "blocker", "INCIDENT-CLOCK": "blocker", "HOLDS-UNREAD": "blocker", "LOAD-HIGH": "blocker", "CUT-SILENT": "blocker", "PR-SILENT": "blocker", "RELEASE-HELD-AFTER-LANDING": "blocker", "LAUNCH-STALLED": "blocker", "LAUNCH-DUPLICATE": "blocker", "UNGRADED-30": "blocker", "SLICE-UNREAD": "blocker", "GITHUB-QUOTA-LOW": "urgent", "FINDER-DESK-STALLED": "blocker", "GO-TAP-MISSING": "blocker"}
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
GO_UNQUEUED_OUTCOMES = frozenset({"blocked", "failed", "stranded", "unsettled"})
RETRYABLE = frozenset({"blocked", "superseded", "foreign", "dropped", "refused", "dequeued"})
BELIEVED_QUEUED = frozenset({"enqueued", "noop"})
ADMITTED = BELIEVED_QUEUED | {"dropped", "ejected"}
OUT_OF_QUEUE = frozenset({"not queued", "evicted"})
LANDING_REFUSALS = frozenset({"FOREIGN", "TAP-REQUIRED", "CONTRACT-MISSING", "PARKED", "UNOWNED", "GATE-FAILED", "RULINGS-REFUSED", "ENQUEUE-STRANDED", "ENQUEUE-FAILED", "ENQUEUE-UNSETTLED"})
OWNER_KINDS = ("head", "opened", "ready", "claim", "report")
CHECK_PR_CAUSES = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED", "FINDER-MAJOR", "MECHANISM-BANNED", "NAME-HIT", "RULINGS-PLAIN", "LANE-DECIDED", "CONTRACT-MISSING", "TESTS-CORPUS"})
INCIDENT_CHECKS = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED"})
RESERVED_TOUCH = "RESERVED-NO-ANSWER"
CHECK_PR_BLOCKER = "BLOCKER"
UNGRADED_AFTER = timedelta(minutes=30)
GO_TO_QUEUE = timedelta(minutes=5)
OVERFLOW = "overflow"
OVERFLOW_AFTER = timedelta(minutes=5)
OVERFLOW_EVERY = timedelta(minutes=1)
OVERFLOW_SEEN = "overflow-seen"
OVERFLOW_STALLED = timedelta(minutes=5)
OVERFLOW_RETRIES = frozenset({"superseded", "failed", "dropped"})
UNGRADED_READ = "ungraded_read"
DIFF_MEDIA = "Accept: application/vnd.github.diff"
ALLOW_ESCAPES = "--allow-escape-sequences"
HOLD_LABEL = "hold"
MERGE_MODES = ("every", "high-risk", "auto")
ACCEPT_CHECKS = ("precondition", "rules-review", "rulings", "fidelity", "finder", "old-app", "build-failed", "merge-mode")
HOLD_CHECKS = ("hold",)
ENQUEUE_CHECKS = ("precondition", "rules-review", "rulings", "fidelity", "finder", "old-app", "build-failed", "merge-mode")
CHECK_STAGES = {"hold": "enqueue", "precondition": "accept, enqueue", "rules-review": "accept, enqueue", "rulings": "accept, enqueue", "fidelity": "accept, enqueue", "finder": "accept, enqueue", "old-app": "accept, enqueue", "build-failed": "accept, enqueue", "merge-mode": "accept, enqueue", "approvals": "adapter check", "required-ci": "adapter check"}
DEFAULT_PRIORITY = 100
HIGH_RISK_STARTER = ("*migrations/*", "*.sql", "*backfill*", "*secret*", "*credential*", "*creds*")
PULLED = "pulled"
VOLATILE = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\d+")
RELAYED_PR = re.compile(r"^(?:blocker|restack):(\d+):")
RED_BY_DESIGN = re.compile(r"^\W*red by design\b", re.IGNORECASE)
TITLE_STALE = re.compile(r"\btitle: stale\b[^\n]*", re.IGNORECASE)
RED_CONTRACT = re.compile(r"red[- ]by[- ]design|\bfail(?:s|ed|ing)? (?:in )?(?:run|build)\b|\bfailing (?:ci )?runs?\b", re.IGNORECASE)
MERGE_FAST = "merge-fast"
RELEASE_WITH = re.compile(r"^Release-With:[ \t]*(\S.*)$", re.MULTILINE)
RELEASE_READ = "release-read"
LANDED_READ = "landed-read"
LANDED_TIDIED = "landed-tidied"
DEQUEUE_READ = "dequeue-read"
DEQUEUE_ORDERS = "dequeue-orders"
PULLED_HEADS = "pulled-heads"
DEQUEUE_ORDER = re.compile(r"(?:\A\s*|\b(?:landing-desk|desk-runner):\s*)dequeue\s+#(\d+)\b", re.IGNORECASE)
INCIDENT_NAME = re.compile(r"^(?:incident|outage)-")
INCIDENT_CLEARED = re.compile(r"not an incident fix|off the incident path|no release is owed", re.IGNORECASE)
INCIDENT_CLEARING_KINDS = frozenset({"go", "hold", "lift", "decide", "decision", "release"})
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
LIST_ITEM = re.compile(r"<li>(.*?)</li>", re.DOTALL)
HTML_TAG = re.compile(r"<[^>]+>")
CONFLICT_BLOCKER = "GitHub reads it as conflicting against its base; run ccx vcs stack submit from the branch's worktree"
REDUNDANT_SKIP = re.compile(r"redundant within its stack", re.IGNORECASE)
BUILDS_PATH = re.compile(r"/builds/\d+")
GRADE_BRANCH = "ci-grade/"
GRADE_KEY = "ci-grade:"
CLAIM_CLAUSE = re.compile(r";|\.\s")
CLAIMED_PR = re.compile(r"(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<pr>\d+)\b")
STATUS_PAGE = 25
STATUS_MAX_AGE = timedelta(minutes=5)
FULL_RUN_GREEN = re.compile(r"full run green (?P<build>\d+)(?P<sentence>[^.]*)")
FULL_RUN_WINDOW = timedelta(days=2)
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
TRUNK_DEPTH = 50
TRUNK_RED_EVERY = timedelta(minutes=30)
FINISHED_STATES = frozenset({"SUCCESS", "FAILURE", "ERROR"})
SQUASHED_PR = re.compile(r"\(#(\d+)\)$")
CARD_ROLLUP = 50
CARD_REFS = 10
CARD_DETAIL = 200
CARD_STEPS = 1000
CARD_NAME = 80
CARD_LABEL = 75
COMMAND_TIMEOUT_SECONDS = 1800
ENQUEUE_SECONDS = 300
LEDGER_SECONDS = 300
FETCH_SECONDS = 120
GRAPHITE_BASE = "graphite-base/"
QUICK_SECONDS = 60
CHECK_PR_SECONDS = 180
GATE_CHECK_SECONDS = 60
QUEUE_STATUS_SECONDS = 300
LANDABLE = "landable"
CHECK_PR_TIMEOUT = "CHECK-PR-TIMEOUT"
QUICK_PROGRAMS = frozenset({"gh", "cci", "ccx"})
TIMED_OUT = 124
WAKE_SECONDS = 0.25
STOP_CONFIRM = timedelta(seconds=30)
STOP_POLL_SECONDS = 2
SHOW_WORKERS = 8
GATE_WORKERS = 16
GATE_BUDGET = timedelta(seconds=120)
STEP_BUDGETS = {"check": 180, "settle": 300, "enqueue": 300}
STACKS_FILE = "landing-stacks.json"
CHECK_PR_WORKERS = 8
TELL_BUDGET = timedelta(seconds=60)
REFRESH_EVERY = timedelta(minutes=2)
GATE_STATUSES = frozenset({"release-pr-check", "emergency-head"})
RED_STATES = frozenset({"failure", "error"})
RUN_NUMBERS = re.compile(r"\d+")
POSTED_SEQ = re.compile(r"#(\d+)")
HEAD_FLOOR = timedelta(seconds=60)
BODY_MAX_AGE = timedelta(minutes=5)
CHECK_PR_REREAD = timedelta(minutes=5)
RULINGS_SOURCES = ("rulings", "board_rows", "testfiles")
TRUNK_REREAD = timedelta(minutes=5)
QUEUED_STATE = "queued"
QUEUED_VERDICT = "QUEUED"
ORPHANED_HEAD = "queue head orphaned"
STATUS_SETTLED = frozenset({QUEUED_STATE, "landed"})
HEADS_PER_QUERY = 100
QUOTA_FLOOR = 1000
QUOTA_RESOURCES = ("core", "graphql")
QUOTA_URGENT = 1500
TOP_CALLERS = 5
ROTATE_EVERY = timedelta(hours=1)
STALL_WATCH = SCRIPTS / "stall_watch.py"
STALL_EVERY_MINUTES = 5
HEARTBEATS = drive.HEARTBEATS
WATCHDOG = "drive-watchdog"
ALIVE_EVERY = timedelta(minutes=1)
DESKS = tuple(HEARTBEATS)
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
ORCA_LIVE = "orca-live"
TRUNK_UNREAD_FACT = "trunk-unread"
TRUNK_JOBS_FACT = "trunk-jobs"
UNREACHABLE_WAIT = timedelta(minutes=2)
DEAD_LANE = timedelta(minutes=60)
UNKNOWN_BLOCK = re.compile(r'\bunknown block "')
SUCCESSOR_NUMBER = re.compile(r"(?:-\d{1,2})+$")
LANE_ORDER_WINDOW = timedelta(hours=2)
LANE_SILENT = timedelta(minutes=15)
LANE_SPAWN_GRACE = timedelta(minutes=10)
LANE_GONE_REPORT = re.compile(r"\bLANE-GONE lane-gone:([^:\s]+)[:\s]")
LANE_ENDED_KINDS = frozenset({"done", "handoff", "stand-down", "stopped", "not-ours"})
HANDOFF_WAIT = timedelta(minutes=20)
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
INSTALL_PARTS = ("reference", "scripts")
BRIEF_FILES = (MANUAL_TEMPLATE, ALERT_TEMPLATE, UNFIXED_TEMPLATE, finder.TEMPLATE, finder.ROWS_STEP, fidelity.TEMPLATE, fidelity.RUBRIC, incident.TEMPLATE)
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
LANE_HEARTBEAT = "lane-heartbeat"
HEARTBEAT_GRACE = timedelta(minutes=30)
MILESTONE_SUBJECTS = ("fix-live:", "mechanism:", "ready:")
CLOCK_MILESTONES = {"mechanism": "mechanism", "opened": "PR", "ready": "ready"}
CLOCK_ENDS = ("fix-live", "not-ours", "stopped", "stand-down")
STAND_DOWN = "stand-down"
INCIDENT_ENDS = ("not-ours", STAND_DOWN)
STOPPED = "stopped"
TEXT_MARK = re.compile(r"^(?P<mark>MECHANISM|READY)\b")
TEXT_MARK_KINDS = ("report", "incident")
SAYS_MECHANISM = re.compile(r"\bmechanism\b", re.IGNORECASE)
CLOCK_DECIDE = "decide"
FIX_ALIAS = "-fix"
CASE = "-case"
LINEAGE = re.compile(rf"(?:{re.escape(CLOCK_FALLBACK)}|{re.escape(CASE)}\d+)+$")
INCIDENT_PART = re.compile(r"-[^-]+(?:-\d+)?$")
CLAUDE_ARGS = "ORCA_LAUNCH_CLAUDE_ARGS"
PR_SILENT = "pr-silent"
CLASS_UNOWNED = "class-unowned"
CLASS_TEXT = re.compile(r"^(?:NEW )?CLASS\b")
CLASS_HEADER = re.compile(r"^(?:(?:NEW )?CLASS\b[^\n]*|\[[^\]\n]+\])")
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
PLACE = re.compile(CCX_KEY.format("place=(remote|local)"), re.MULTILINE)
STANDING_DESK = re.compile(CCX_KEY.format("role=(?:desk|watch)"), re.MULTILINE)
SPRITE_LANE = re.compile(r"[a-z0-9][a-z0-9-]{0,54}")
SPRITE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
SPRITE_IDS = {"task": "taskId", "dispatch": "dispatchId"}
SPRITE_PREPARED = "prepare.status"
SPRITE_RECEIPT = "worker-start.json"
SPRITE_MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "sol": "gpt-6.1-sol", "codex": "gpt-6.1-sol", "astra": "gpt-6-astra"}
FABLE_MODEL = re.compile(r"(^|[-_.])fable([-_.0-9]|$)", re.IGNORECASE)
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


class StopRequested(Exception):
    pass


def quick(argv: list[str]) -> bool:
    return Path(argv[0]).name in QUICK_PROGRAMS


def time_bound(argv: list[str]) -> float:
    if len(argv) > 2 and Path(argv[1]).name == "rulings.py" and argv[2] == "check-pr":
        return CHECK_PR_SECONDS
    if "--check" in argv:
        return GATE_CHECK_SECONDS
    if argv[:4] == ["ccx", "vcs", "pr", "status"]:
        return QUEUE_STATUS_SECONDS
    if Path(argv[0]).name == "stack-enqueue" or argv[:3] == ["ccx", "vcs", "ship"]:
        return ENQUEUE_SECONDS
    if len(argv) > 1 and Path(argv[1]).name == "ledger.py":
        return LEDGER_SECONDS
    if argv[0] == "git" and "fetch" in argv:
        return FETCH_SECONDS
    return QUICK_SECONDS if quick(argv) else COMMAND_TIMEOUT_SECONDS


def rereadable(argv: list[str]) -> bool:
    """Whether `argv` only reads, so a run that timed out or failed on a transient network error may run once more."""
    return argv[:2] in (["cci", "tail"], ["cci", "who"]) or argv[:3] == ghread.GATEWAY or argv[:4] == ["ccx", "vcs", "pr", "status"] or (Path(argv[0]).name == "stack-enqueue" and bool({"--check", "--status"} & set(argv)))


def command_family(argv: list[str]) -> str:
    """The program, its verbs (up to three words that are no flag, path or number, read until a flag or path follows them), and `--check` when it carries one: what a pass's cost line groups by. A read through ccx's gateway groups as the `gh` command it runs."""
    words = argv[1:] if Path(argv[0]).name.startswith("python") else ghread.gh_args(argv)
    verbs: list[str] = []
    for word in words[1:]:
        if word.startswith("-") or "/" in word or word.isdigit():
            if verbs:
                break
            continue
        verbs.append(word)
        if len(verbs) == 3:
            break
    return " ".join([Path(words[0]).name, *verbs, *(["--check"] if "--check" in words else [])])


def reserved_fingerprint(rows: dict[str, dict]) -> str:
    reserved = {key: {name: value for name, value in fields.items() if name != "updated_at"} for key, fields in rows.items() if fields.get("kind") == "reserved"}
    return hashlib.sha256(json.dumps(reserved, sort_keys=True).encode()).hexdigest()[:16]


def rulings_digest() -> str:
    """What `rulings.py check-pr` reads its causes with: its source and the sources of the modules that decide a cause, the board rows it parses and the test-file rule, and none of the plumbing it reads through."""
    return hashlib.sha256(b"".join((SCRIPTS / f"{name}.py").read_bytes() for name in RULINGS_SOURCES)).hexdigest()[:16]


def command_name(argv: list[str]) -> str:
    words = argv[1:] if Path(argv[0]).name.startswith("python") else argv
    return " ".join(Path(word).name if word.startswith("/") else word for word in words)[:120]


class Shell:
    """The single side-effect boundary: every subprocess, the clock, and the load average pass through here.

    Input reaches a child from a file, never a pipe the runner writes while the child's output
    fills, and a child still running after COMMAND_TIMEOUT_SECONDS is killed and fails its pass.
    Output that is not UTF-8, such as a diff of a binary file, reads with each bad byte replaced.
    A GitHub, cci or ccx read gets QUICK_SECONDS, a `--check` GATE_CHECK_SECONDS, `rulings.py check-pr` CHECK_PR_SECONDS, any other
    `stack-enqueue` and `ccx vcs ship` ENQUEUE_SECONDS, `ledger.py` LEDGER_SECONDS and `git fetch` FETCH_SECONDS; one that times out is logged and
    reads as a failed command, never a fatal one. A read `rereadable` names runs once more when it timed out or failed on a transient
    network error. `in_progress` holds each thread's command in flight
    and when it started, for the heartbeat.
    """

    in_progress: dict[int, tuple[str, float]] = {}
    spent: Counter[str] = Counter()
    called: Counter[str] = Counter()
    waited: Counter[str] = Counter()
    spending = threading.Lock()

    def run(self, argv: list[str], stdin: str | None = None, env: dict[str, str] | None = None, cwd: str | None = None) -> Done:
        done = self.run_once(argv, stdin, env, cwd)
        if rereadable(argv) and done.code and (done.code == TIMED_OUT or merge_queue.TRANSIENT_FAILURE.search(done.err + done.out)):
            done = self.run_once(argv, stdin, env, cwd)
        return done

    def run_once(self, argv: list[str], stdin: str | None, env: dict[str, str] | None, cwd: str | None) -> Done:
        timeout = time_bound(argv)
        started = time.monotonic()
        self.in_progress[threading.get_ident()] = (command_name(argv), started)
        try:
            with tempfile.TemporaryFile("w+") as feed:
                feed.write(stdin or "")
                feed.seek(0)
                try:
                    proc = subprocess.run(argv, stdin=feed, capture_output=True, text=True, errors="replace", env={**os.environ, **env} if env else None, cwd=cwd, timeout=timeout)
                except subprocess.TimeoutExpired:
                    print(f"TIMED-OUT after {timeout}s, skipped: {command_name(argv)}", file=sys.stderr, flush=True)
                    return Done(TIMED_OUT, "", f"{' '.join(argv)} timed out after {timeout}s and was killed")
        finally:
            self.in_progress.pop(threading.get_ident(), None)
            with self.spending:
                self.spent[command_family(argv)] += time.monotonic() - started
                self.called[command_family(argv)] += 1
                if threading.current_thread() is threading.main_thread():
                    self.waited[command_family(argv)] += time.monotonic() - started
        return Done(proc.returncode, proc.stdout, proc.stderr)

    def pass_cost(self) -> str:
        """The seconds the pass itself waited on its own commands, the four costliest families first, then the seconds every thread's commands spent and how many calls spent them, the eight costliest first; the tally starts over."""
        with self.spending:
            waited, costliest, called, self.spent, self.called, self.waited = self.waited.most_common(4), self.spent.most_common(8), self.called, Counter(), Counter(), Counter()
        return f"the pass waited on {', '.join(f'{family} {seconds:.0f}s' for family, seconds in waited) or 'nothing'}; all threads: " + ", ".join(f"{family} {seconds:.0f}s in {called[family]}" for family, seconds in costliest)

    def in_flight(self) -> tuple[str, float] | None:
        """The pass's own command in flight and how many seconds it has run, else the longest-running command a worker has in flight."""
        now = time.monotonic()
        flights = dict(self.in_progress)
        if own := flights.get(threading.main_thread().ident):
            return own[0], now - own[1]
        return max(((name, now - started) for name, started in flights.values()), key=lambda item: item[1], default=None)

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

    def pid_alive(self, pid: int) -> bool:
        """Whether a process with id `pid` exists; a child that exited is reaped at the next spawn."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        return True

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
    """The incident clock milestone a lane's cci record marks, or empty: its kind, `ready` for a `report` or `incident` whose text starts READY, or `mechanism` for a record of any kind whose text says mechanism."""
    if record["kind"] in (*CLOCK_MILESTONES, *CLOCK_ENDS):
        return record["kind"]
    if record["kind"] in TEXT_MARK_KINDS and (mark := TEXT_MARK.match(record["text"])):
        return mark["mark"].lower()
    return "mechanism" if SAYS_MECHANISM.search(record.get("text") or "") else ""


def class_keys(record: dict) -> set[str]:
    """The class a record declares: its topic, and the incident tags in its header, the `CLASS` or `NEW CLASS` line or a leading `[TAG]`; tags anywhere else in its text are evidence, never a class."""
    header = CLASS_HEADER.match(record.get("text") or "")
    return cci.incident_keys({"topic": record.get("topic"), "text": header.group(0) if header else ""})


def names_lane(record: dict, names: set[str]) -> bool:
    """Whether a record is addressed to one of a lane's names or names one in its text."""
    return bool(names & set(record.get("to") or [])) or any(re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-]|\.\w)", record.get("text") or "") for name in names)


def lane_aliases(lane: str) -> tuple[str, str]:
    """The names one incident lane posts under: the desk launches `<name>-fix`, and its lane may post as `<name>`."""
    base = lane.removesuffix(FIX_ALIAS)
    return base, f"{base}{FIX_ALIAS}"


def successors(lane: str) -> re.Pattern[str]:
    """The names a lane and its numbered successors take after rotations: `fidelity-desk`, `fidelity-desk-3`, `register-seed-5-5`."""
    return re.compile(rf"{re.escape(SUCCESSOR_NUMBER.sub('', lane))}(?:-\d{{1,2}})*")


def names_resolved(sentence: str, resolved: set[int]) -> bool:
    """Whether a precondition sentence names a record root has resolved, such as the hold it waits on."""
    return any(int(seq) in resolved for seq in cci.NAMED_PR.findall(sentence))


def pr_family(lane: str) -> str:
    """The name one incident's fix lane and its Opus fallback share, so the first PR either opens settles both."""
    return lane.removesuffix(CLOCK_FALLBACK).removesuffix(FIX_ALIAS)


def is_test_or_ci_file(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        name.startswith(CI_PREFIXES)
        or testfiles.test_file(name)
        or any(fnmatch(name, glob) for glob in TOOLING_GLOBS)
        or not TEST_DIRS.isdisjoint(path.parts[:-1])
        or (path.suffix in (".yml", ".yaml") and "buildkite" in path.name)
    )


def queued_since(passes: list[actions.Action], action: actions.Action) -> tuple[str, int]:
    """When the PR of `action` began waiting for a finder pass, and the record that queued it: the oldest head in the unbroken run of its heads a newer one superseded just before `action`, so a PR whose head keeps moving keeps its place in line."""
    first = action
    for earlier in reversed(passes[: passes.index(action)]):
        if "superseded" not in (earlier.response or {}):
            break
        first = earlier
    return first.accepted_at, json.loads(first.target)["seq"]


def full_pass(action: actions.Action) -> bool:
    """Whether `action` is a requested full pass; an action queued before full passes existed carries no mark and is not."""
    return json.loads(action.target).get("full", False)


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
    text = brief.read_text() if brief.is_file() else ""
    if STANDING_DESK.search(text):
        return False
    placed = PLACE.search(text)
    return not placed or placed[1] == "remote"


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


def grade_argv(checkout: Path, template: str, values: dict[str, str]) -> list[str]:
    """`landing.ci_grade` for one head, its first word relative to the landing checkout, each `{name}` of `values` replaced in place so a JSON body in the template keeps its own braces."""
    words = shlex.split(template)
    for name, value in values.items():
        words = [word.replace(f"{{{name}}}", value) for word in words]
    return [str(checkout / words[0]), *words[1:]]


def started_build_url(out: str) -> str:
    """The `web_url` of the build a `landing.ci_grade` command printed, or empty when its output names no Buildkite build."""
    try:
        build = json.loads(out)
    except ValueError:
        return ""
    url = build.get("web_url") if isinstance(build, dict) else None
    return url if isinstance(url, str) and BUILD_URL.search(url) else ""


def root_claim(record: dict) -> bool:
    return record["kind"] == "claim" and record["lane"] == ROOT


def claimed_prs(record: dict, repo: str) -> set[str]:
    """The PRs of `repo` a claim takes: its refs, and each `#<n>` bare or qualified with `repo` in its text before the first `;` or sentence end, so a PR it mentions after that is not taken."""
    clause = CLAIM_CLAUSE.split(record["text"], maxsplit=1)[0]
    return {pr for pr, _ in repo_prs(record, repo)} | {match["pr"] for match in CLAIMED_PR.finditer(clause) if not match["repo"] or match["repo"].lower() == repo.lower()}


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
            try:
                with self.store.edit(container) as incident:
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
    missing: bool = False


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
            missing=observation.get("status") == "missing",
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

    def workers(self, *scope: str) -> list[dict]:
        rows: list[dict] = []
        cursor: list[str] = []
        while True:
            page = self.ask("orchestration", "worker-list", "--run", self.config.run, *scope, "--limit", "100", *cursor)["result"]
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


def install_gaps() -> list[str]:
    """Each file under `reference/` or `scripts/` of the marketplace copy this install came from that the install lacks; none when the runner runs from no cache install."""
    if not (source := drive.marketplace_copy(SCRIPTS.parent)):
        return []
    return sorted(str(path.relative_to(source)) for part in INSTALL_PARTS for path in (source / part).rglob("*") if path.is_file() and "__pycache__" not in path.parts and not (SCRIPTS.parent / path.relative_to(source)).exists())


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


def active_policy(incident: actions.Incident) -> actions.Action | None:
    verified = [action for action in incident.actions.values() if action.kind == "policy" and action.status == "verified"]
    superseded = {json.loads(action.target)["supersedes"] for action in verified}
    return next((action for action in verified if json.loads(action.target)["revision"] not in superseded), None)


def refused_members(routes: Iterable[actions.Action], members: set[str], open_rows: dict[str, dict]) -> Iterator[str]:
    found: set[str] = set()
    for action in routes:
        target = json.loads(action.target)
        if target.get("kind") == "blocker" and (pr := str(target["pr"])) in members and (sha := re.search(rf":{pr}:([0-9a-f]{{7,40}})", action.action_id)) and names_head(sha[1], open_rows[pr].get("head", "")):
            found.add(pr)
            yield pr
            if found == members:
                return


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
        self.passes: Counter[str] = Counter()
        self.raised: dict[str, tuple[str, int]] = {}
        self.alive_posted: dict[str, datetime] = {}
        self.stall_pass: subprocess.Popen | None = None
        self.stall_started = datetime.min.replace(tzinfo=timezone.utc)
        self.finding = threading.Lock()
        self.taps: tuple[datetime, set[str]] = (datetime.min.replace(tzinfo=timezone.utc), set())
        self.cci_tail = cci.Tail(self.cci_read)
        self.read_failed_at = float("-inf")
        self.book.ensure(RUNNER, RUNNER)

    def now(self) -> datetime:
        return self.shell.now()

    def escalate(self, key: str, kind: str, about: str, text: str, subject: str = "", to: str = "") -> None:
        """Record one escalation per key; `flush` posts it to root through cci with its subject, the key unless given, as its topic, so cci delivers only the newest report on a subject the root has not read yet. With `to`, it goes to those lanes instead, posted at once and recorded only once posted, so a failed post runs again on the next pass."""
        action_id, line = f"escalation:{key}", f"{kind} {subject or key} {about}: {' '.join(text.split())}"
        if not to:
            self.record(action_id, line)
            return
        if self.book.known(RUNNER, action_id):
            return
        done = self.post_escalation(line, to, action_id)
        if done.code != 0:
            print(f"escalation {key} to {to} left for the next pass; cci post exited {done.code}: {(done.err or done.out).strip()[:300]}", file=sys.stderr)
            return
        self.record(action_id, line)
        self.book.attempt(RUNNER, lambda incident: (incident.start(action_id, self.now()), incident.complete(action_id, {"at": self.book.stamp(), "posted": done.out.strip(), "to": to})))

    def raised_before(self, key: str, subject: str) -> bool:
        """Whether an escalation under another key already named `subject`."""
        return any(action.action_id != f"escalation:{key}" and action.target.split()[1] == subject for action in self.book.actions(RUNNER, kind="escalation"))

    def landing_lanes(self, member: str) -> list[str]:
        """The lanes `landing.<member>` names, one or a list, re-read on every use."""
        configured = json.loads(self.config.source.read_text()).get("landing", {}).get(member, [])
        return [configured] if isinstance(configured, str) else list(configured)

    def post_escalation(self, line: str, to: str, action_id: str) -> Done:
        label, topic = line.split()[:2]
        message = self.message_file(topic)
        return self.cci_post(ESCALATION_KINDS.get(label, "defect" if label.endswith("-FAILED") else "report"), line, to, topic, action_id, message.read_text() if message.is_file() else "")

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
        self.cci_tail.forget()
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
            label = action.target.split()[0]
            if clocks and label in DEFERRABLE and action.target.split()[2].rstrip(":") not in incident:
                if action.action_id not in held:
                    self.defer(action, clocks)
                continue
            done = self.post_escalation(action.target, ROOT, action.action_id)
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

    def pass_raised(self, desk: str, error: Exception) -> bool:
        """Log a pass's traceback and post one blocker to the root naming the desk and the exception; true when the pass before raised the same, and the runner then exits."""
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        raised = f"{type(error).__name__}: {error}"
        again = self.raised.get(desk) == (raised, self.passes[desk] - 1)
        self.raised[desk] = (raised, self.passes[desk])
        then = "it raised the same on the pass before, so the runner exited; restart it with its heartbeat's restart line" if again else "the runner goes on with its next pass"
        self.cci_post("blocker", f"{desk} runner pass raised {raised[:240]}; {then}", ROOT, desk, f"pass-raised:{desk}")
        return again

    @property
    def watchdog(self) -> str:
        """The drive's own watchdog, by drive, since cci reroutes a bare lane no live thread reads here to the one other drive that has it."""
        return f"{WATCHDOG}@{self.config.drive}"

    def post_alive(self, desk: str) -> None:
        """Post the desk's `<desk> alive <time> pass <n>` record to the drive watchdog at most once a minute, naming the command in flight and how long it has run when one is."""
        if self.now() - self.alive_posted.get(desk, datetime.min.replace(tzinfo=timezone.utc)) < ALIVE_EVERY:
            return
        self.alive_posted[desk] = self.now()
        flight = self.shell.in_flight()
        doing = f"{self.passes[desk] + 1} running `{flight[0]}` for {int(flight[1]) // 60}m{int(flight[1]) % 60:02d}s" if flight else str(self.passes[desk])
        self.cci_post("state", f"{desk} alive {self.now().astimezone(PACIFIC).strftime('%-I:%M %p')} pass {doing}", self.watchdog, desk, f"alive:{desk}")

    def stall_watch(self) -> None:
        stall = json.loads(self.config.source.read_text()).get("stall")
        if not stall or (self.stall_pass and self.stall_pass.poll() is None) or self.now() - self.stall_started < timedelta(minutes=stall.get("every_minutes", STALL_EVERY_MINUTES)):
            return
        self.stall_started = self.now()
        self.stall_pass = self.shell.spawn([sys.executable, str(STALL_WATCH), "--config", str(self.config.source)], self.config.desk_inbox.with_name("stall-watch.log"), {})

    def keep_alive(self, desk: str) -> threading.Event:
        """Write the heartbeat file now, then post the alive record and rewrite the file every minute from a daemon thread, whatever the pass is doing, until the returned event is set, so a long first pass never leaves a predecessor's pid in the file."""
        stop = threading.Event()
        self.write_heartbeat(desk)

        def tick() -> None:
            while not stop.wait(ALIVE_EVERY.total_seconds()):
                self.post_alive(desk)
                self.write_heartbeat(desk)

        threading.Thread(target=tick, name=f"{desk}-alive", daemon=True).start()
        return stop

    def beat(self, desk: str) -> None:
        """Write the desk's heartbeat file every pass, and post its alive record."""
        self.passes[desk] += 1
        self.post_alive(desk)
        self.write_heartbeat(desk)

    def write_heartbeat(self, desk: str) -> None:
        inbox = self.config.desk_inbox.parent
        beat = {"pid": os.getpid(), "at": self.now().isoformat(), "config": str(self.config.source), "terminal": self.shell.env("ORCA_TERMINAL_HANDLE"), "restart": self.restart_command(desk), "version": RUNNING_VERSION}
        inbox.mkdir(parents=True, exist_ok=True)
        staged = inbox / f".{HEARTBEATS[desk]}.{threading.get_ident()}"
        staged.write_text(json.dumps(beat) + "\n")
        staged.replace(inbox / HEARTBEATS[desk])

    def stale(self) -> bool:
        """Whether a newer plugin version is installed than this runner runs; the loop re-executes into it at the end of the pass."""
        return drive.behind(RUNNING_VERSION, drive.installed_version())

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

    def checkpoint(self) -> None:
        """End the pass at this boundary between steps once `handoff` has asked this runner to stop."""
        if self.stop_requested():
            raise StopRequested

    def steps(self, *steps: Callable[[], object]) -> None:
        for step in steps:
            self.checkpoint()
            step()

    @contextmanager
    def desk_lock(self, desk: str) -> Iterator[int | None]:
        """Hold the lock one runner of `desk` on this config holds for its life while the block runs, yielding None when this process took it and the holder's pid when another runner holds it."""
        with self.config.source.with_name(f"{self.config.source.name}.{desk}.lock").open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.seek(0)
                yield int(handle.read().strip() or 0)
                return
            handle.truncate(0)
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            yield None

    def desk_holder(self, desk: str) -> int | None:
        with self.desk_lock(desk) as holder:
            return holder

    def handoff(self, desk: str) -> int:
        """Stop the runner holding `desk`'s lock at its next step boundary, wait until it has exited, then start its successor; a runner whose heartbeat, written after the stop was asked, shows it re-executed into this version under the same pid has handed over in place, so its stop is withdrawn and no successor starts (root, Oct 29 11:46 AM); refuse, naming its pid, when neither happens within HANDOFF_WAIT (#140225). Refuse before stopping anything when the install lacks a brief the runner, finder, or incident job reads, or any reference or script file the marketplace copy it was installed from holds."""
        if missing := list(dict.fromkeys([*(f"{path.parent.name}/{path.name}" for path in BRIEF_FILES if not path.exists()), *install_gaps()])):
            print(f"desk-runner handoff: this install lacks {', '.join(missing)}, so no runner was stopped; reinstall the plugin and hand off again", file=sys.stderr)
            return 1
        pid = self.desk_holder(desk)
        if pid is not None:
            marker = self.stop_marker()
            marker.write_text(f"{pid}\n")
            asked = self.now()
            deadline = asked + HANDOFF_WAIT
            while self.desk_holder(desk) == pid:
                if self.reexecuted(desk, pid, asked):
                    marker.unlink(missing_ok=True)
                    self.shell.sleep(WAKE_SECONDS)
                    if self.desk_holder(desk) == pid:
                        print(f"desk-runner handoff: runner {pid} re-executed into {RUNNING_VERSION} in place, so no successor started")
                        return 0
                    break
                if self.now() >= deadline:
                    why = f"runner {pid} still runs the {desk} desk after {HANDOFF_WAIT}, so no successor started; {marker} stays, the runner exits at the end of its pass, and the next `desk-runner.py handoff --desk {desk}` starts the successor"
                    self.escalate(f"handoff:{desk}:{pid}", "HANDOFF-STALLED", "runner", why)
                    self.flush()
                    print(f"desk-runner handoff: {why}", file=sys.stderr)
                    return 1
                self.shell.sleep(WAKE_SECONDS)
        started = self.shell.spawn([str(RUNNER_BIN), "run", "--config", str(self.config.source), "--desk", desk], self.runner_log(desk), self.config.desk_env[desk], append=True)
        print(f"desk-runner handoff: {f'runner {pid} exited' if pid is not None else f'no live {desk} runner'}; started runner {started.pid}, logging to {self.runner_log(desk)}")
        return 0

    def reexecuted(self, desk: str, pid: int, since: datetime) -> bool:
        heartbeat = self.config.desk_inbox.parent / HEARTBEATS[desk]
        if not heartbeat.is_file():
            return False
        beat = json.loads(heartbeat.read_text())
        return beat["pid"] == pid and beat.get("version") == RUNNING_VERSION and datetime.fromisoformat(beat["at"]) >= since

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

    def cci_read(self, argv: list[str]) -> str:
        done = self.shell.run(argv)
        if done.code != 0:
            self.read_failed_at = time.monotonic()
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
        """Attach the brief `render` writes for the resolved briefs log to that log as `<lane>.full.md`, and write it to `<state>/briefs/<lane>.full.md`; that path, or why it has none."""
        if failure := self.resolve_briefs_log():
            return None, f"the briefs log did not resolve: {failure}"
        text = render(self.config.briefs_log)
        if failure := self.attach(f"{lane}.full.md", text, entry):
            return None, f"the brief did not attach: {failure}"
        brief = self.config.desk_inbox.parent.parent / "briefs" / f"{lane}.full.md"
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text(text)
        return brief, ""

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
        marks = [(record["at"], f"#{record['seq']}", record["text"]) for record in self.lane_records(lane) if clock_mark(record) == "mechanism"]
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
        """A lane's own cci records of these kinds, or of every kind when none is named, on this drive, under either of its `lane_aliases`; `cci tail` also returns the caller's peer mail, which is dropped."""
        names = lane_aliases(lane)
        posted = cci.records(self.cci_tail, self.config.drive, *(arg for name in names for arg in ("--lane", name)), *(f"--kind={kind}" for kind in kinds))
        return [record for record in posted if record["drive"] == self.config.drive and record["lane"] in names and (not kinds or record["kind"] in kinds)]

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
        return active_policy(self.book.ensure(LANDING, LANDING))

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
                if unread := self.unread_relay(container, action):
                    response = {"text": f"folded into {unread}, which the lane has not answered yet", "at": self.book.stamp()}
                    self.book.attempt(container, lambda incident, key=action.action_id: finish(incident, key, response, dispatch.id))
                    continue
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

    def unread_relay(self, container: str, action: actions.Action) -> str:
        """The relay about the same PR at an earlier head that the lane was sent and has not answered, which a relay not yet sent folds into instead of queuing a second message; empty when there is none."""
        sent = {send.authority_ref for send in self.book.actions(container, kind="send") if send.status != "failed"}
        if action.action_id in sent or not (relayed := RELAYED_PR.match(action.action_id)):
            return ""
        return next(
            (
                earlier.action_id
                for earlier in self.book.actions(container, kind=action.kind, status="accepted")
                if earlier.action_id != action.action_id and earlier.action_id in sent and (other := RELAYED_PR.match(earlier.action_id)) and other[0] == relayed[0]
            ),
            "",
        )

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
        if refusal:
            self.refuse_launch(container, lane, action, refusal, "the root submits it again under a new key once that is fixed")
            return
        if refusal is not None and (held := self.load_refusal()):
            self.move_load(held)
            if spec["model"] != INCIDENT_MODEL:
                if spec["urgent"]:
                    self.refuse_launch(container, lane, action, held, "the root launches it in-process or once the load is moved")
                elif self.now() - actions.parse_stamp(action.accepted_at) >= timedelta(minutes=self.config.load_hold_minutes):
                    self.expire_launch(container, lane, action, held)
                return
            self.escalate(f"{container}/{action.action_id}:load", "LOAD-INCIDENT", lane, f"{action.action_id} launches on {held}: an incident fix lane is never held for load")
        if not self.book.attempt(container, lambda incident: incident.start(action.action_id, self.now(), deadline=actions.parse_stamp(action.deadline))):
            return
        if refusal is None:
            self.launch_sprite(container, lane, action)
            return
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
        if FABLE_MODEL.search(spec["model"]) or spec["model"] == INCIDENT_MODEL or spec.get("fast") or not sprite_placed(Path(spec["brief"])):
            return ""
        if not self.config.sprite:
            return "a runner config with no orca.sprite"
        if not SPRITE_LANE.fullmatch(lane):
            return f"lane {lane}, not a Sprite name of up to 55 lowercase letters, digits, and dashes"
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
                self.refuse_launch(container, lane, action, f"a Sprite that never started a worker: {reason}", f"its receipts are in {attempt}; the root submits it again under a new key")
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
                self.beat_lane(message)
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

    def beat_lane(self, message: dict) -> None:
        """Keep the newest heartbeat a lane sent on its container, where `overdue` reads it as the lane being alive."""
        container = lane_container(self.orca.lane_of(message["from_handle"]))
        at = message["created_at"]
        if container in self.book.store.ids() and at > self.book.load(container).facts.get(LANE_HEARTBEAT, ""):
            self.book.edit(container, lambda incident: incident.facts.update({LANE_HEARTBEAT: at}))

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
            if action.kind in ("relay", "reply") and (action.status == "started" or self.beating(container, action, moment)) or self.lane_moved(about, action) or self.relayed_red(about, action):
                continue
            sent = [send for send in self.book.actions(container, kind="send") if send.authority_ref == action.action_id]
            missing = {
                "relay": f"delivered to {sent[-1].target}, no started reply" if sent and sent[-1].status == "completed" else "never delivered: no live dispatch" if not sent else f"send {sent[-1].status}",
                "reply": "never delivered: no live dispatch" if not sent else f"send {sent[-1].status}",
                "launch": "launch never finished" if action.status == "started" else "launch never started",
                "enqueue": "the adapter's enqueue never returned" if action.status == "started" else "enqueue never ran",
            }[action.kind]
            self.escalate(f"deadline:{container}/{action.action_id}:{action.status}", "DEADLINE", about, f"{action.authority_ref} {action.kind}: {missing}; accepted {pacific(actions.parse_stamp(action.accepted_at))}")

    def beating(self, container: str, action: actions.Action, moment: datetime) -> bool:
        """Whether the lane's heartbeats cover `action`, since a lane answers its mail only between turns: one arrived after `action` was accepted, or one arrived within `HEARTBEAT_GRACE` before it and `action` is not yet `HEARTBEAT_GRACE` old."""
        if not (beat := self.book.load(container).facts.get(LANE_HEARTBEAT)):
            return False
        beat, accepted = actions.parse_stamp(beat), actions.parse_stamp(action.accepted_at)
        return beat > accepted or accepted - beat < HEARTBEAT_GRACE and moment - accepted < HEARTBEAT_GRACE

    def relayed_red(self, lane: str, action: actions.Action) -> bool:
        """Whether `action` relays a blocker on a PR the lane's newest contract for it calls red by design, so no DEADLINE follows it."""
        if action.kind != "relay" or not (relayed := RELAYED_PR.match(action.action_id)):
            return False
        try:
            return red_contract(self.lane_records(lane, "contract"), relayed[1])
        except CciTailFailed as failure:
            self.tail_failed(failure)
            return True

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
                    decided = self.decided(lane, launched)
                    clock["reached"] = self.reached(container, lane, launched)
                except CciTailFailed as failure:
                    self.tail_failed(failure)
                    continue
                if stood_down:
                    clock["ended"] = f"{WITHDRAWN} by {stood_down}"
                elif decided:
                    clock["ended"] = f"decided by {decided}"
                else:
                    clock["ended"] = next((name for name in (*CLOCK_ENDS, "ready") if name in clock["reached"]), "")
                if not clock["ended"]:
                    self.overrun(lane, launch, launched, clock["reached"])
            if clock != kept:
                self.book.edit(container, lambda incident, clock=clock: incident.facts.update({CLOCK: clock}))

    def reached(self, container: str, lane: str, launched: datetime) -> dict[str, str]:
        """Each milestone a lane reached since `launched`, at its earliest stamp: the lane's own cci records by kind, and its milestone statuses from the Run. Any milestone a sibling lane on the same incident reached counts as this lane's mechanism, so the PR and ready thresholds stay the lane's own."""
        marks = [(mark, record["at"]) for record in self.lane_records(lane) if (mark := clock_mark(record))]
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

    def decided(self, lane: str, launched: datetime) -> str:
        """The root's `decide` since the lane's launch that is addressed to the lane or names it, as `#<seq>`, or empty."""
        names = set(lane_aliases(lane))
        posted = cci.records(self.cci_tail, self.config.drive, "--lane", ROOT, f"--kind={CLOCK_DECIDE}")
        return next((f"#{record['seq']}" for record in posted if record["drive"] == self.config.drive and record["lane"] == ROOT and record["kind"] == CLOCK_DECIDE and names_lane(record, names) and actions.parse_stamp(record["at"]) >= launched), "")

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
        posted = cci.records(self.cci_tail, self.config.drive, *(arg for name in sorted(names) for arg in ("--lane", name)))
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

    def lanes_gone(self) -> None:
        """Log one `LANE-GONE` per lane and newest order for each lane, other than the runner's own lanes and its child passes `stall-watch` and `prompt-watch`, that a root `ask` addressed, or a root `decide` or `correction` gave an order as `<lane>: ...`, in the last `LANE_ORDER_WINDOW`, whose oldest unanswered order is `LANE_SILENT` old, that has posted nothing since, neither it nor a numbered successor (`<lane>-<n>`), that no later root `stand-down` or `stopped` retired, whose newest record does not end its work (`done`, `handoff`, `stand-down`, `stopped`, `not-ours`), whose records are not all `defect` records, as a build script's are (root #152067), whose dispatch the runner did not withdraw and Orca does not show active, with no session `cci who` announced in the last `LANE_SPAWN_GRACE`, and no earlier LANE-GONE of it the root resolved or answered in the window, once its newest record and `cci who`'s last sight of it, by lane or agent name, are both `LANE_SILENT` old, naming the unanswered orders and its last record, so the root pings it and spawns a successor only when the ping goes unanswered (root #147167, drive-watchdog #147921)."""
        try:
            now, drive = self.now(), self.config.drive
            records = [record for record in cci.records(self.cci_tail, drive, since=actions.stamp(now - LANE_ORDER_WINDOW)) if record["drive"] == drive]
            orders: dict[str, list[dict]] = {}
            for record in records:
                if record["lane"] == ROOT:
                    for lane in record.get("to") or []:
                        if lane not in {ROOT, "main", "all", *RUNNER_LANES} and not lane.startswith("peer:") and owes(record, lane):
                            orders.setdefault(lane, []).append(record)
            sessions: list[dict] = []
            if orders:
                done = self.shell.run(["cci", "who", "--drive", drive, "--json"])
                if done.code != 0:
                    raise CciTailFailed(f"cci who exited {done.code}: {(done.err or done.out).strip()[:300]}")
                sessions = [json.loads(line) for line in done.out.splitlines() if line.strip()]
            reported = {record["seq"]: match[1] for record in records if record["lane"] == RUNNER and (match := LANE_GONE_REPORT.search(record.get("text") or ""))}
            settled = {reported[seq] for record in records if record["lane"] == ROOT for seq in closes(record) | {record.get("re")} if seq in reported}
            for lane, addressed in orders.items():
                if lane in settled:
                    continue
                family = {lane} | {record["lane"] for record in records if succeeds(record["lane"], lane)}
                retired = max((record["seq"] for record in records if record["lane"] == ROOT and record["kind"] in (STAND_DOWN, STOPPED) and family & set(record.get("to") or [])), default=0)
                posted = [record for record in records if record["lane"] in family]
                if posted and all(record["kind"] == "defect" for record in posted):
                    continue
                last = max(posted, key=lambda record: record["seq"], default=None)
                unanswered = [order for order in addressed if order["seq"] > retired and (last is None or order["seq"] > last["seq"])]
                if not unanswered or now - actions.parse_stamp(unanswered[0]["at"]) < LANE_SILENT or last and (last["kind"] in LANE_ENDED_KINDS or now - actions.parse_stamp(last["at"]) < LANE_SILENT) or self.withdrawn_by(lane) or self.dispatch_active(lane):
                    continue
                found = [entry for entry in sessions if {entry.get("lane"), entry.get("agent_name")} & family]
                if any(entry.get("announced_at") and now - actions.parse_stamp(entry["announced_at"]) < LANE_SPAWN_GRACE for entry in found):
                    continue
                sightings = [actions.parse_stamp(entry["seen_at"]) for entry in found if entry.get("seen_at")]
                seen = max(sightings, default=None)
                if seen and now - seen < LANE_SILENT:
                    continue
                said = f"its last record is #{last['seq']} {last['kind']} at {pacific(actions.parse_stamp(last['at']))}" if last else f"it posted nothing in the last {LANE_ORDER_WINDOW.seconds // 3600} h"
                sight = f"cci last saw it {whole_minutes(seen, now)} min ago" if seen else "cci lists no session for it"
                orders_named = ", ".join(f"#{order['seq']}" for order in unanswered)
                key, subject = f"lane-gone:{lane}:{unanswered[-1]['seq']}", f"lane-gone:{lane}"
                to = ",".join(dict.fromkeys([ROOT, *desks, *self.landing_lanes("board_lane"), lane])) if self.raised_before(key, subject) and (desks := self.landing_lanes("desk_lane")) else ""
                self.escalate(key, "LANE-GONE", lane, f"root {orders_named} to {lane} unanswered; {said}, and {sight}; a roster or cci miss is not absence: SendMessage it and spawn a successor only when it has not answered in 5 minutes", subject, to)
        except CciTailFailed as failure:
            self.tail_failed(failure)

    def dispatch_active(self, lane: str) -> bool:
        return bool(self.orca.receipt(lane) and (shown := self.orca.show(lane)) and shown.status not in INACTIVE)

    def dispatches_gone(self) -> None:
        """Read the Run's worker list once and log one `LANE-GONE` for each lane with a receipt whose dispatch was live on the last pass Orca answered, or is still listed as dispatched, and that Orca now shows gone, completed or failed, or whose terminal Orca finds missing, unless the runner withdrew it, relayed its `worker_done`, or the lane's newest record ends its work; the dispatch is the evidence, so no `LANE_SILENT` wait applies."""
        try:
            statuses = {row["dispatchId"]: row["dispatchStatus"] for row in self.orca.workers("--include-remote")}
            facts = self.book.load(RUNNER).facts
            known, live = facts.get(ORCA_LIVE, {}), {}
            for lane in self.orca.lanes():
                if not (dispatch := self.orca.receipt(lane)):
                    continue
                listed = statuses.get(dispatch)
                if listed and listed not in INACTIVE:
                    shown = self.orca.show(lane, dispatch)
                    if shown and shown.status not in INACTIVE and not shown.missing:
                        live[lane] = dispatch
                        continue
                    state = f"{shown.status} with its terminal {shown.terminal} missing" if shown and shown.status not in INACTIVE else shown.status if shown else "gone"
                elif known.get(lane) == dispatch:
                    state = listed or "gone"
                else:
                    continue
                self.lane_vanished(lane, dispatch, state, facts.get(UNREACHABLE_FACT))
            if live != known:
                self.book.edit(RUNNER, lambda incident: incident.facts.update({ORCA_LIVE: live}))
        except CciTailFailed as failure:
            self.tail_failed(failure)

    def lane_vanished(self, lane: str, dispatch: str, state: str, outage: dict | None) -> None:
        if self.withdrawn_by(lane) or any(action.target.startswith("OUTCOME ") and f" dispatch={dispatch}:" in action.target for action in self.book.actions(RUNNER, kind="escalation")):
            return
        drive = self.config.drive
        last = max((record for record in cci.records(self.cci_tail, drive, "--lane", lane) if (record["drive"], record["lane"]) == (drive, lane)), key=lambda record: record["seq"], default=None)
        if last and last["kind"] in LANE_ENDED_KINDS:
            return
        said = "it has posted no record"
        if last:
            at = actions.parse_stamp(last["at"])
            said = f"its last record is #{last['seq']} {last['kind']} at {pacific(at)}, {whole_minutes(at, self.now())} min ago"
        down = f"; Orca gave no answer from {pacific(actions.parse_stamp(outage['since']))} until this pass ({outage['error']})" if outage else ""
        self.escalate(f"lane-gone:{lane}:{dispatch}", "LANE-GONE", lane, f"Orca shows its dispatch {dispatch} {state}; {said}{down}; relaunch it from its brief and handoff", f"lane-gone:{lane}")

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
            if not (keys := class_keys(record)):
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
        """What code the lane holds, by `lanes.lane_status` over the runner's launch receipts, the lane's cci records of the last day, and the worktrees of every repository in the pool, never the landing checkout itself."""
        records = [record for record in cci.records(self.cci_tail, self.config.drive, "--lane", lane, since=actions.stamp(self.now() - LANE_RECORDS)) if record["drive"] == self.config.drive and record["lane"] == lane]
        landing = self.config.landing or {}
        excluded = frozenset({landing["checkout"]} if landing.get("checkout") else ())
        return lanes.lane_status(lane, records, [self.config.receipts], Path(self.config.briefs_repo), landing.get("repo", ""), TRUNK, self.now(), self.checked, excluded)

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
        self.cci_tail.forget()
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

    def finders(self) -> None:
        """Dispatch one finder pass per drive head while runner.json carries `finder` (reference/finder-brief.md): queue each new `head` record's PR and full head, settle the passes that wrote a result, then start the newest queued head of each PR, up to `finder.concurrent` at once. The orca pass and the `keep_finding` thread take turns under one lock."""
        if (spec := json.loads(self.config.source.read_text()).get("finder")) is None:
            return
        repo = spec.get("repo") or self.config.landing["repo"]
        with self.finding:
            self.book.ensure(FINDER, FINDER)
            try:
                rows = self.open_rows()
                self.queue_finders(repo, rows)
                self.settle_finders()
                self.start_finders(spec, repo, rows)
                self.watch_finder_desk(spec)
            except CciTailFailed as failure:
                self.tail_failed(failure)

    def keep_finding(self) -> threading.Event:
        """Run the finder desk every `FINDER_EVERY` from a daemon thread until the returned event is set, so a slot a finished pass frees refills in seconds rather than on the next orca pass, which runs for minutes. A loop that raises the same twice running stops, and the orca pass still runs the desk."""
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(FINDER_EVERY.total_seconds()):
                try:
                    self.finders()
                except Exception as error:
                    if self.pass_raised(FINDER, error):
                        return
                self.passes[FINDER] += 1

        threading.Thread(target=loop, name="finders", daemon=True).start()
        return stop

    def queue_finders(self, repo: str, rows: dict[str, dict]) -> None:
        """Accept one `finder:<pr>:<head>` action per PR and full head the drive's `head` and `opened` records past the saved sequence name, and per green head of an open ledger row, so a head two records name, or a restack that posted no record, queues once; a first read starts at the newest record."""
        cursor = self.book.load(FINDER).facts.get(FINDER_CURSOR)
        if cursor is None:
            newest = cci.records(self.cci_tail, self.config.drive, "--limit", "1")
            self.book.edit(FINDER, lambda incident: incident.facts.update({FINDER_CURSOR: max((record["seq"] for record in newest), default=0)}))
            return
        read = cci.records(self.cci_tail, self.config.drive, "--kind=head", "--kind=opened", "--kind=ask", since=cursor)
        for record in read:
            if record["drive"] != self.config.drive:
                continue
            if record["kind"] == "ask":
                for pr in finder.requested(record, repo) if record["lane"] == ROOT else ():
                    self.book.accept(FINDER, f"finder:{pr}:whole:{record['seq']}", "finder", json.dumps({"pr": pr, "head": "", "lane": record["lane"], "seq": record["seq"], "full": True}), f"cci#{record['seq']}", None)
                continue
            for pr, head in finder.heads(record, repo):
                self.book.accept(FINDER, f"finder:{pr}:{head}", "finder", json.dumps({"pr": pr, "head": head, "lane": record["lane"], "seq": record["seq"], "full": False}), f"cci#{record['seq']}", None)
        if read:
            cursor = read[-1]["seq"]
            self.book.edit(FINDER, lambda incident: incident.facts.update({FINDER_CURSOR: cursor}))
        for pr, row in self.green_heads(FINDER, rows):
            self.book.accept(FINDER, f"finder:{pr}:{row['head']}", "finder", json.dumps({"pr": pr, "head": row["head"], "lane": row.get("lane") or ROOT, "seq": cursor, "full": False}), "ledger", None)

    def settle_finders(self) -> None:
        """Settle each started pass from the result its job wrote; one that wrote an error, whose process exited with no result, or that wrote none by its deadline fails, so its slot frees this pass."""
        settled = False
        for action in self.book.actions(FINDER, kind="finder", status="started"):
            directory = Path(action.dispatch_id)
            pid = directory / finder.PID
            exited = pid.is_file() and not self.shell.pid_alive(int(pid.read_text()))
            result = directory / finder.RESULT
            ran = whole_minutes(actions.parse_stamp(action.started_at), self.now())
            if result.is_file():
                outcome = json.loads(result.read_text())
                if "error" in outcome:
                    self.finder_failed(action, outcome["error"])
                else:
                    self.finder_settled(action.action_id, outcome)
            elif exited:
                self.finder_failed(action, f"finder.py exited {ran} min after it started and wrote no result")
            elif self.now() >= actions.parse_stamp(action.deadline):
                self.finder_failed(action, f"no result {ran} min after it started")
            else:
                continue
            settled = True
        if settled:
            self.book.edit(FINDER, lambda incident: incident.facts.update({FINDER_SETTLED: self.book.stamp()}))

    def start_finders(self, spec: dict, repo: str, rows: dict[str, dict]) -> None:
        """Start the newest queued head of each PR, a requested full pass over any head, while fewer than `finder.concurrent` passes run: a lower ledger `priority` first, then full passes, then those a root `go` or an owner's board tap names, then heads of a PR a finder pass or a lane's finder record already graded, which carry or read only a delta, then the PR waiting longest, counted from the first of its heads a newer one superseded. An older queued head of the same PR settles as superseded, and so does a running pass of it, its codex run cancelled. A queued head whose PR is closed, or no longer at that head, or whose number names no PR, settles as skipped without a pass."""
        newest: dict[str, actions.Action] = {}
        for action in sorted(self.book.actions(FINDER, kind="finder", status="accepted"), key=lambda action: (full_pass(action), json.loads(action.target)["seq"])):
            if (older := newest.get(pr := json.loads(action.target)["pr"])) is not None:
                self.finder_settled(older.action_id, {"superseded": json.loads(action.target)["head"]})
            newest[pr] = action
        running = 0
        for action in self.book.actions(FINDER, kind="finder", status="started"):
            running += not self.cancel_finder(action, newest.get(json.loads(action.target)["pr"]))
        if not newest or running >= spec.get("concurrent", FINDER_CONCURRENT):
            return
        live = self.live_heads(repo, list(newest))
        for pr, action in list(newest.items()):
            job = json.loads(action.target)
            if (state := live.get(pr)) and (state["state"] in FINDER_SKIPS or job["head"] and state["headRefOid"] != job["head"]):
                self.finder_settled(action.action_id, {"skipped": FINDER_SKIPS.get(state["state"], "#{pr} moved to {head}").format(pr=pr, repo=repo, head=state["headRefOid"][:12])})
                del newest[pr]
        going = self.tapped() | {pr for record in cci.records(self.cci_tail, self.config.drive, "--lane", ROOT, "--kind=go") if (record["lane"], record["kind"]) == (ROOT, "go") for pr in go_heads(record)}
        history: dict[str, list[actions.Action]] = {}
        for action in sorted(self.book.actions(FINDER, kind="finder"), key=lambda action: action.accepted_at):
            history.setdefault(json.loads(action.target)["pr"], []).append(action)
        graded = {pr for pr, passes in history.items() if any({"verdict", "answered"} & set(action.response or {}) for action in passes)}
        ranks = {pr: int(row.get("priority", DEFAULT_PRIORITY)) for pr, row in rows.items()}
        for action in sorted(newest.values(), key=lambda action: (ranks.get(pr := json.loads(action.target)["pr"], DEFAULT_PRIORITY), not full_pass(action), pr not in going, pr not in graded, queued_since(history[pr], action))):
            if running >= spec.get("concurrent", FINDER_CONCURRENT):
                return
            running += self.start_finder(spec, repo, action, rows)

    def cancel_finder(self, action: actions.Action, queued: actions.Action | None) -> bool:
        """Settle a running pass as superseded when a newer head of its PR is queued, write its job's `cancelled` mark so it asks codex nothing, and cancel each codex run it started, since a head that moves while codex reads it posts nothing."""
        job = json.loads(action.target)
        if queued is None or full_pass(action) or full_pass(queued) or json.loads(queued.target)["seq"] <= job["seq"]:
            return False
        head = json.loads(queued.target)["head"]
        directory = Path(action.dispatch_id)
        (directory / finder.CANCELLED).write_text(f"{head}\n")
        for run in sorted(directory.glob("codex-*")):
            self.shell.run(["codex-ask", "--cancel", str(run)])
        self.finder_settled(action.action_id, {"superseded": head})
        return True

    def live_heads(self, repo: str, prs: list[str]) -> dict[str, dict]:
        """Each of `prs` GitHub answered for, to its `state` and `headRefOid`, read fresh in one GraphQL query, with state `NOT_FOUND` for a number that names no pull request in `repo`, such as a cci seq a record bound as one (#177997), which GitHub answers beside the rest; a read that answers for none answers none, and each pass then reads its PR itself."""
        owner, name = repo.split("/")
        fields = " ".join(f"p{pr}: pullRequest(number: {pr}) {{ state headRefOid }}" for pr in prs)
        done = self.shell.run(ghread.read("api", "graphql", "-f", f'query=query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}', max_age=ghread.FRESH), env={"GH_REPO": repo})
        read = json.loads(done.out) if done.out.lstrip().startswith("{") else {}
        answered = (read.get("data") or {}).get("repository") or {}
        if not answered:
            print(f"finder live heads did not read: {(done.err or done.out).strip()[:300]}", file=sys.stderr)
            return {}
        missing = {error["path"][-1] for error in read.get("errors") or [] if error.get("type") == "NOT_FOUND"}
        return {alias.removeprefix("p"): state or {"state": "NOT_FOUND", "headRefOid": ""} for alias, state in answered.items() if state or alias in missing}

    def green_heads(self, container: str, rows: dict[str, dict]) -> list[tuple[str, dict]]:
        """Each open drive row whose head is green while no pass of its PR waits or runs in `container`, so a ledger head that trails a newer queued one never supersedes it."""
        busy = {json.loads(action.target)["pr"] for status in ("accepted", "started") for action in self.book.actions(container, status=status)}
        return [(pr, row) for pr, row in rows.items() if pr not in busy and row.get("ours") and row.get("test_state") == "success" and finder.FULL_SHA.fullmatch(row.get("head", ""))]

    def open_rows(self) -> dict[str, dict]:
        """Each open ledger row by PR, whose `priority` ranks the graders' queues, lower first, so a row the root ranks or an open owner ask links is graded before the rest, and whose green head queues a pass no record named; an unread ledger names none."""
        landing = self.config.landing or {}
        done = self.shell.run([sys.executable, str(SCRIPTS / "ledger.py"), "-C", str(Path(landing["checkout"]).expanduser()), "list", "--ledger", landing["ledger"], "--json"], env={"CLAUDE_LONG_RUNNING_DRIVE": self.config.drive})
        if done.code != 0:
            print(f"the graders did not read the ledger: {(done.err or done.out).strip()[:300]}", file=sys.stderr)
            return {}
        return {row["pr"]: row for row in json.loads(done.out) if row.get("state", "open") == "open"}

    def tapped(self) -> set[str]:
        """The PRs an owner's approval on the root's board names, read at most every `FINDER_TAPS_EVERY`; a board that does not read names none."""
        read, taps = self.taps
        if self.now() - read < FINDER_TAPS_EVERY:
            return taps
        try:
            verdicts = board_rows.verdicts_of(self.shell.run(["cc-present", "outcomes", "--all", "--no-doc", "--session", session])) if (session := self.root_session()) else {}
        except board_rows.ReadFailed:
            verdicts = {}
        taps = {pr for rows in verdicts.values() for row, entry in rows.items() if entry["verdict"] == "approved" and (pr := board_rows.pr_of(row))}
        self.taps = (self.now(), taps)
        return taps

    def root_session(self) -> str | None:
        """The drive root's newest session in the drive registry, whose board holds the merge cards and rows the owner taps."""
        entry = next((entry for entry in drive.drives() if drive.cci_drive(entry) == self.config.drive and entry["sessions"]), None)
        return entry["sessions"][-1] if entry else None

    def start_finder(self, spec: dict, repo: str, action: actions.Action, rows: dict[str, dict]) -> bool:
        """Settle a head some lane's finder record already names, or a full pass a whole-range finder record posted after the PR's newest `head` record answered, or after the request when no `head` record names the PR, or write the pass's job and start finder.py on it detached, reading since the head the PR's newest finder record at another head read, or the whole change for a full pass."""
        job = json.loads(action.target)
        full = full_pass(action)
        records = cci.records(self.cci_tail, self.config.drive, "--kind=review", "--kind=head", "--pr", f"{repo}#{job['pr']}")
        named = finder_reviews_in(records, self.config.drive, repo, job["pr"])
        reviews = finder_records(named)
        moved = max((record["seq"] for record in records if record["kind"] == "head"), default=job["seq"])
        answered = next((review for review in reviews if review["seq"] > moved and finder.WHOLE_READ in review["text"]), None) if full else newest_finder(reviews, job["head"])
        if answered:
            self.finder_settled(action.action_id, {"answered": f"cci #{answered['seq']} from {answered['lane']}"})
            return False
        last, prior = ("", None) if full else finder.since(reviews, records, job["head"])
        attempt = action.action_id.partition("#")[2]
        directory = self.config.source.parent / finder.LANE / ((f"{job['pr']}-whole-{job['seq']}" if full else f"{job['pr']}-{job['head'][:12]}") + (f"-{attempt}" if attempt else ""))
        directory.mkdir(parents=True, exist_ok=True)
        landing = self.config.landing or {}
        (directory / finder.JOB).write_text(
            json.dumps(
                {
                    "drive": self.config.drive,
                    "repo": repo,
                    "pr": job["pr"],
                    "head": job["head"],
                    "since": last,
                    "prior": prior,
                    "previous": finder.previous(next((record for record in records if record["seq"] == job["seq"]), None)) if named and not full else "",
                    "history": [{"seq": record["seq"], "lane": record["lane"], "text": record["text"], "path": (record.get("refs") or {}).get("path", "")} for record in named],
                    "settled": self.root_settled(repo, job["pr"], named),
                    "lane": job["lane"],
                    "to": [RUNNER, *filter(None, [rows.get(job["pr"], {}).get("lane")]), *spec.get("to", [])],
                    "checkout": spec.get("checkout") or landing["checkout"],
                    "model": spec.get("model", FINDER_MODEL),
                    "fast": spec.get("fast", FINDER_FAST),
                    "full": full,
                }
            )
        )
        deadline = self.now() + timedelta(minutes=spec.get("timeout_minutes", FINDER_MINUTES))
        if not self.book.attempt(FINDER, lambda incident: incident.start(action.action_id, self.now(), deadline=deadline, dispatch_id=str(directory))):
            return False
        process = self.shell.spawn([sys.executable, str(SCRIPTS / "finder.py"), str(directory)], directory / finder.LOG, {})
        (directory / finder.PID).write_text(f"{process.pid}\n")
        return True

    def root_settled(self, repo: str, pr: str, named: list[dict]) -> dict[str, int]:
        """Each finder record of `named` a root `decide` settled, keyed by its seq, with the oldest such decide: one naming it by `--re` or `--resolves`, or one about `pr` that says settled and came after it (root #182456)."""
        if not (seqs := {record["seq"] for record in named}):
            return {}
        settled: dict[str, int] = {}
        for decide in cci.records(self.cci_tail, self.config.drive, "--kind=decide", "--lane", ROOT, since=min(seqs)):
            if (decide["kind"], decide["lane"], decide["drive"]) != ("decide", ROOT, self.config.drive):
                continue
            named_by = {decide.get("re"), decide.get("resolves")} & seqs
            said = {seq for seq in seqs if seq < decide["seq"]} if "settled" in decide["text"].casefold() and about_pr(decide, repo, pr) else set()
            settled |= {str(seq): decide["seq"] for seq in named_by | said if str(seq) not in settled}
        return settled

    def finder_settled(self, key: str, response: dict) -> None:
        def settle(incident: actions.Incident) -> None:
            if incident.action(key).status == "accepted":
                incident.start(key, self.now())
            incident.complete(key, response | {"at": self.book.stamp()})
            incident.verify(key, response)

        self.book.attempt(FINDER, settle)

    def finder_failed(self, action: actions.Action, why: str) -> None:
        """Fail a pass and queue its job again, so a job runs at most `FINDER_ATTEMPTS` times; the last failure logs `FINDER-FAILED` once."""
        job = json.loads(action.target)
        if not self.book.attempt(FINDER, lambda incident: incident.fail(action.action_id, why)) or self.requeue_finder(action.action_id.partition("#")[0], action.target, action.authority_ref):
            return
        log = Path(action.dispatch_id) / finder.LOG
        self.escalate(f"finder:{job['pr']}:{job['head']}", "FINDER-FAILED", job["lane"], f"#{job['pr']} {job['head'][:12]}: the finder desk's pass failed on the last of its {FINDER_ATTEMPTS} attempts: {why}; its log is {log}; the lane runs the pass by hand from {finder.BRIEF}")

    def requeue_finder(self, key: str, target: str, authority: str) -> bool:
        """Accept one more attempt at the finder job `key` names, `key` itself first and then `<key>#<n>`, while it has had fewer than `FINDER_ATTEMPTS`."""
        tried = sum(action.action_id.partition("#")[0] == key for action in self.book.actions(FINDER, kind="finder"))
        if tried >= FINDER_ATTEMPTS:
            return False
        self.book.accept(FINDER, f"{key}#{tried + 1}" if tried else key, "finder", target, authority, None)
        return True

    def requeue_pending_finder(self, repo: str, pr: str, head: str, lane: str) -> None:
        """Queue one more finder pass on a head the landing check refuses `FINDER-PENDING` with no finder record, while runner.json carries `finder` for `repo` and no pass that would answer it waits or runs, so a lost job, or a pass that skipped the PR while it was closed, runs again."""
        if (spec := json.loads(self.config.source.read_text()).get("finder")) is None or (spec.get("repo") or self.config.landing["repo"]) != repo:
            return
        self.book.ensure(FINDER, FINDER)
        jobs = [(action, job) for action in self.book.actions(FINDER, kind="finder") if (job := json.loads(action.target))["pr"] == pr]
        if any(action.status in ("accepted", "started") and (job["full"] or names_head(job["head"], head)) for action, job in jobs):
            return
        tried = [action for action, job in jobs if not job["full"] and names_head(job["head"], head)]
        key = tried[0].action_id.partition("#")[0] if tried else f"finder:{pr}:{head}"
        self.requeue_finder(key, tried[-1].target if tried else json.dumps({"pr": pr, "head": head, "lane": lane, "seq": 0, "full": False}), "FINDER-PENDING")

    def watch_finder_desk(self, spec: dict) -> None:
        """Log one `FINDER-DESK-STALLED` per streak while passes wait queued and none has settled for `FINDER_STALLED_AFTER`, naming how many wait and each running pass's age; the streak ends once a pass settles or none waits."""
        incident = self.book.load(FINDER)
        passes = [action for action in incident.actions.values() if action.kind == "finder"]
        queued = [action for action in passes if action.status == "accepted"]
        quiet = max(stamp for stamp in (incident.facts.get(FINDER_SETTLED), min((action.accepted_at for action in queued), default="")) if stamp) if queued else ""
        if not quiet or self.now() - (since := actions.parse_stamp(quiet)) < FINDER_STALLED_AFTER:
            if FINDER_STALLED in incident.facts:
                self.book.edit(FINDER, lambda incident: incident.facts.pop(FINDER_STALLED, None))
            return
        streak = self.book.edit(FINDER, lambda incident: dict(incident.facts.setdefault(FINDER_STALLED, {"since": actions.stamp(since)})))
        running = ", ".join(f"#{job['pr']} {job['head'][:12] or 'whole'} for {whole_minutes(actions.parse_stamp(action.started_at), self.now())} min" for action in passes if action.status == "started" for job in [json.loads(action.target)])
        self.escalate(f"finder-desk:{streak['since']}", "FINDER-DESK-STALLED", finder.LANE, f"{len(queued)} queued, and no finder pass has settled since {pacific(since)}; running (at most {spec.get('concurrent', FINDER_CONCURRENT)} at once): {running or 'none'}; each pass logs to job.log in its directory under {self.config.source.parent / finder.LANE}")

    def fidelities(self) -> None:
        """Dispatch one fidelity grade per drive head while runner.json carries `fidelity`: queue each new `head` or `opened` record's PR and full head, settle the passes that wrote a result, then start the newest queued head of each PR, up to `fidelity.concurrent` at once."""
        if (spec := json.loads(self.config.source.read_text()).get("fidelity")) is None:
            return
        repo = spec.get("repo") or self.config.landing["repo"]
        self.book.ensure(FIDELITY, FIDELITY)
        try:
            rows = self.open_rows()
            self.queue_fidelities(repo, rows)
            self.settle_fidelities()
            self.start_fidelities(spec, repo, rows)
        except CciTailFailed as failure:
            self.tail_failed(failure)

    def queue_fidelities(self, repo: str, rows: dict[str, dict]) -> None:
        """Accept one `fidelity:<pr>:<head>:<seq>` action per PR, full head and record past the saved sequence, so the next `head` record for a PR queues a pass again after one failed, and one `fidelity:<pr>:<head>:ledger` per green head of an open ledger row, so a restack that posted no record queues once; a first read starts at the newest record."""
        cursor = self.book.load(FIDELITY).facts.get(FINDER_CURSOR)
        if cursor is None:
            newest = cci.records(self.cci_tail, self.config.drive, "--limit", "1")
            self.book.edit(FIDELITY, lambda incident: incident.facts.update({FINDER_CURSOR: max((record["seq"] for record in newest), default=0)}))
            return
        read = cci.records(self.cci_tail, self.config.drive, "--kind=head", "--kind=opened", since=cursor)
        for record in read:
            if record["drive"] == self.config.drive:
                for pr, head in finder.heads(record, repo):
                    self.book.accept(FIDELITY, f"fidelity:{pr}:{head}:{record['seq']}", "fidelity", json.dumps({"pr": pr, "head": head, "lane": record["lane"], "seq": record["seq"]}), f"cci#{record['seq']}", None)
        if read:
            cursor = read[-1]["seq"]
            self.book.edit(FIDELITY, lambda incident: incident.facts.update({FINDER_CURSOR: cursor}))
        for pr, row in self.green_heads(FIDELITY, rows):
            self.book.accept(FIDELITY, f"fidelity:{pr}:{row['head']}:ledger", "fidelity", json.dumps({"pr": pr, "head": row["head"], "lane": row.get("lane") or ROOT, "seq": cursor}), "ledger", None)

    def settle_fidelities(self) -> None:
        for action in self.book.actions(FIDELITY, kind="fidelity", status="started"):
            result = Path(action.dispatch_id) / fidelity.RESULT
            if not result.is_file():
                if self.now() >= actions.parse_stamp(action.deadline):
                    self.fidelity_failed(action, f"no result {whole_minutes(actions.parse_stamp(action.started_at), self.now())} min after it started")
                continue
            outcome = json.loads(result.read_text())
            if "error" in outcome:
                self.fidelity_failed(action, outcome["error"])
            else:
                self.settle(FIDELITY, action.action_id, outcome)

    def start_fidelities(self, spec: dict, repo: str, rows: dict[str, dict]) -> None:
        """Start the newest queued head of each PR, a lower ledger `priority` first, then oldest PR first, while fewer than `fidelity.concurrent` passes run; an older queued head of the same PR settles as superseded, and a queued head whose PR is closed, no longer at that head, or whose number names no PR, such as a cci seq a record bound as one, settles as skipped without a pass."""
        newest: dict[str, actions.Action] = {}
        for action in sorted(self.book.actions(FIDELITY, kind="fidelity", status="accepted"), key=lambda action: json.loads(action.target)["seq"]):
            if (older := newest.get(pr := json.loads(action.target)["pr"])) is not None:
                self.settle(FIDELITY, older.action_id, {"superseded": json.loads(action.target)["head"]})
            newest[pr] = action
        running = len(self.book.actions(FIDELITY, kind="fidelity", status="started"))
        if not newest or running >= spec.get("concurrent", FIDELITY_CONCURRENT):
            return
        live = self.live_heads(repo, list(newest))
        for pr, action in list(newest.items()):
            if (state := live.get(pr)) and (state["state"] in FINDER_SKIPS or state["headRefOid"] != json.loads(action.target)["head"]):
                self.settle(FIDELITY, action.action_id, {"skipped": FINDER_SKIPS.get(state["state"], "#{pr} moved to {head}").format(pr=pr, repo=repo, head=state["headRefOid"][:12])})
                del newest[pr]
        ranks = {pr: int(row.get("priority", DEFAULT_PRIORITY)) for pr, row in rows.items()}
        for action in sorted(newest.values(), key=lambda action: ranks.get(json.loads(action.target)["pr"], DEFAULT_PRIORITY)):
            if running >= spec.get("concurrent", FIDELITY_CONCURRENT):
                return
            running += self.start_fidelity(spec, repo, action, rows)

    def start_fidelity(self, spec: dict, repo: str, action: actions.Action, rows: dict[str, dict]) -> bool:
        """Settle a head a fidelity lane already graded or sent to a reader, or write the pass's job, with every fidelity grade and `old-app:` line on the PR, the grades a root `class wrong` decision answers, and the lane's newest contract, and start fidelity.py on it detached."""
        job = json.loads(action.target)
        drive, landing = self.config.drive, self.config.landing or {}
        lanes = list(dict.fromkeys([*self.landing_lanes("fidelity_lane"), fidelity.LANE]))
        records = cci.records(self.cci_tail, drive, "--kind=review", "--kind=head", "--pr", f"{repo}#{job['pr']}")
        grades = [record | {"text": full_text(record)} for record in fidelity_grades_in(records, drive, lanes, repo, job["pr"])]
        sent = [record for record in records if (record["kind"], record["lane"]) == ("review", fidelity.LANE) and record["text"].startswith(fidelity.READER)]
        if answered := newest_naming(grades, job["head"]) or newest_naming(sent, job["head"]):
            self.settle(FIDELITY, action.action_id, {"answered": f"cci #{answered['seq']} from {answered['lane']}"})
            return False
        contracts = [record for record in cci.records(self.cci_tail, drive, "--kind=contract", "--lane", job["lane"]) if (record["kind"], record["lane"]) == ("contract", job["lane"])]
        contract = max((record for record in contracts if about_pr(record, repo, job["pr"])), key=lambda record: record["seq"], default=None) or max(contracts, key=lambda record: record["seq"], default=None)
        directory = self.config.source.parent / fidelity.LANE / f"{job['pr']}-{job['head'][:12]}-{job['seq']}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / fidelity.JOB).write_text(
            json.dumps(
                {
                    "drive": drive,
                    "repo": repo,
                    "pr": job["pr"],
                    "head": job["head"],
                    "lane": job["lane"],
                    "to": [RUNNER, *filter(None, [rows.get(job["pr"], {}).get("lane")]), *spec.get("to", [])],
                    "readers": [lane for lane in lanes if lane != fidelity.LANE],
                    "lanes": lanes,
                    "grades": [{key: grade[key] for key in ("seq", "lane", "text")} for grade in grades],
                    "old_apps": [{"seq": grade["seq"], "lane": grade["lane"], "head": grade["head"], "line": OLD_APP_LINE.search(full_text(grade))[0]} for grade in old_app_grades_in(records, drive, lanes, repo, job["pr"])],
                    "settled": self.root_settled(repo, job["pr"], grades),
                    "corrected": [record["re"] for record in cci.records(self.cci_tail, drive, "--kind=decide", "--lane", ROOT, "--pr", f"{repo}#{job['pr']}") if (record["kind"], record["lane"]) == ("decide", ROOT) and record.get("re") and CLASS_WRONG.match(record["text"])],
                    "contract": full_text(contract) if contract else "",
                    "exempt": {"port": landing.get("port_phrase", PORT_PHRASE), "test-only": TEST_ONLY_PHRASE},
                    "exempt_grades": landing.get("exempt_grades", [landing.get("port_phrase", PORT_PHRASE), TEST_ONLY_PHRASE]),
                    "baseline": spec.get("baseline", ""),
                    "checkout": spec.get("checkout") or landing["checkout"],
                    "model": spec.get("model", FINDER_MODEL),
                    "fast": spec.get("fast", FINDER_FAST),
                }
            )
        )
        deadline = self.now() + timedelta(minutes=spec.get("timeout_minutes", FIDELITY_MINUTES))
        if not self.book.attempt(FIDELITY, lambda incident: incident.start(action.action_id, self.now(), deadline=deadline, dispatch_id=str(directory))):
            return False
        self.shell.spawn([sys.executable, str(SCRIPTS / "fidelity.py"), str(directory)], directory / fidelity.LOG, {})
        return True

    def settle(self, container: str, key: str, response: dict) -> None:
        def settled(incident: actions.Incident) -> None:
            if incident.action(key).status == "accepted":
                incident.start(key, self.now())
            incident.complete(key, response | {"at": self.book.stamp()})
            incident.verify(key, response)

        self.book.attempt(container, settled)

    def fidelity_failed(self, action: actions.Action, why: str) -> None:
        """Tell the human fidelity desks, then fail the pass; a post that fails leaves the pass started, so the next orca pass tells them again."""
        job = json.loads(action.target)
        key = f"fidelity:{job['pr']}:{job['head']}:{job['seq']}"
        readers = [lane for lane in self.landing_lanes("fidelity_lane") if lane != fidelity.LANE]
        log = Path(action.dispatch_id) / fidelity.LOG
        self.escalate(key, "FIDELITY-FAILED", job["lane"], f"#{job['pr']} {job['head'][:12]}: the fidelity pass failed: {why}; its log is {log}; a fidelity desk grades it by hand, and the next head record for #{job['pr']} queues the pass again", to=",".join(readers))
        if self.book.known(RUNNER, f"escalation:{key}"):
            self.book.attempt(FIDELITY, lambda incident: incident.fail(action.action_id, why))

    def render(self) -> str:
        runner = self.book.load(RUNNER)
        landing = self.book.ensure(LANDING, LANDING)
        lines = [f"# desk-runner {pacific(self.now())}", self.binding_line(runner.facts.get(BINDING))]
        if held := runner.facts.get(DEFERRED):
            lines.append(f"deferred during {held['incident']}: {len(held['ids'])}")
            lines += [f"- {runner.actions[key].target}" for key in held["ids"]]
        for lane, mechanism in ((runner.facts.get(PR_SILENT) or {}).get("lanes") or {}).items():
            lines.append(f"pr-silent {lane}: {mechanism['mark']} #{mechanism['seq']} {whole_minutes(actions.parse_stamp(mechanism['at']), self.now())} min ago, no OPENED or BLOCKER; code state: `drive.py lane-status {lane}`")
        if policy := active_policy(landing):
            held = json.loads(policy.target)
            lines.append(f"landing policy: {held['rule']} at {held['revision']} ({policy.authority_ref})")
        if self.book.store.path(FINDER).is_file():
            for action in self.book.actions(FINDER, kind="finder"):
                if action.status in ("accepted", "started"):
                    job = json.loads(action.target)
                    lines.append(f"finder #{job['pr']} {job['head'][:12]}: {f'running since {pacific(actions.parse_stamp(action.started_at))}' if action.started_at else 'queued'}")
        if self.book.store.path(FIDELITY).is_file():
            for action in self.book.actions(FIDELITY, kind="fidelity"):
                if action.status in ("accepted", "started"):
                    job = json.loads(action.target)
                    lines.append(f"fidelity #{job['pr']} {job['head'][:12]}: {f'running since {pacific(actions.parse_stamp(action.started_at))}' if action.started_at else 'queued'}")
        for container in [*self.book.containers(LANE_PREFIX), LANDING]:
            incident = landing if container == LANDING else self.book.load(container)
            lines.append(f"## {container} owner {incident.owner} generation {incident.owner_generation}{f' pending {incident.pending_owner}' if incident.pending_owner else ''}")
            if clock := incident.facts.get(CLOCK):
                lines.append(self.clock_line(clock))
            for pr, wait in (incident.facts.get(MERGE_WAITS) or {}).items():
                authorize = f", and the owner's tap authorizing {', '.join(wait['rows'])} through `rulings.py authorize`" if wait["rows"] else ""
                lines.append(f"- #{pr} waits on `{wait['key']}`: a root go naming #{pr} at {wait['key'].rsplit(':', 1)[1]}{authorize}")
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


def stack_root(tree: list[str], open_rows: dict[str, dict]) -> str:
    return min(downstack(open_rows, tree[0]), key=lambda pr: len(downstack(open_rows, pr)))


def lane_turns(trees: list[list[str]], open_rows: dict[str, dict]) -> list[list[str]]:
    """`trees` with the lanes taking turns, each lane's trees in their given order, so one lane's many stacks never go ahead of every other lane's."""
    by_lane: dict[str, list[list[str]]] = {}
    for tree in trees:
        by_lane.setdefault(open_rows[stack_root(tree, open_rows)].get("lane", ""), []).append(tree)
    return [tree for turn in itertools.zip_longest(*by_lane.values()) for tree in turn if tree]


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


def cause(line: str) -> str:
    return line.split(" ", 1)[0]


def reserved_row(line: str) -> tuple[str, str]:
    """The row key and the first sentence of its words from a `RESERVED-NO-ANSWER <key><TAB><sentence>` line."""
    key, _, sentence = line.split(" ", 1)[1].partition("\t")
    return key.strip(), sentence.strip()


def red_contract(records: list[dict], pr: str) -> bool:
    """Whether the newest `contract` record whose `--pr` refs name `pr` names its failing run as expected."""
    named = [record for record in records if record["kind"] == "contract" and pr in {str(ref).rsplit("#", 1)[-1] for ref in record.get("refs", {}).get("prs", [])}]
    return bool(named) and RED_CONTRACT.search(max(named, key=lambda record: record["seq"])["text"]) is not None


def pr_name(pr: int | str) -> str:
    """A cci PR reference in prose: a bare number as `#12`, `owner/repo#12` as written."""
    return str(pr) if "#" in str(pr) else f"#{pr}"


def cut_at(prefix: list[str], refused: Iterable[str], whole: bool, open_rows: dict[str, dict]) -> str:
    """What a refusal of `refused` leaves of `prefix`: every PR not stacked on a refused one goes on without the refused PRs and those above them, and nothing goes on under the whole-stack policy."""
    out = cut_out(prefix, set(refused), open_rows)
    kept = [pr for pr in prefix if pr not in out]
    if whole or not kept:
        return f"nothing in {numbered(prefix)} was enqueued"
    return f"the prefix is cut at #{out[0]}, and only {numbered(kept)} may enqueue, without {numbered(out)}"


def cut_out(prefix: list[str], refused: set[str], open_rows: dict[str, dict]) -> list[str]:
    """The PRs of `prefix` that are refused or stacked above a refused one."""
    return [pr for pr in prefix if pr in refused or downstack(open_rows, pr) & refused]


def unlanded_fork(prefix: list[str], open_rows: dict[str, dict]) -> str:
    """The open PR outside `prefix` that a PR of `prefix` is stacked on and that has another open child, or empty: a queued fork the prefix may not join until it lands."""
    by_branch = {row["branch"]: pr for pr, row in open_rows.items() if row.get("branch")}
    for pr in prefix:
        parent = by_branch.get(open_rows[pr].get("base"))
        if parent and parent not in prefix and sum(row.get("base") == open_rows[parent]["branch"] for row in open_rows.values()) > 1:
            return parent
    return ""


def holds_any(open_rows: dict[str, dict], tree: list[str], prs: set[str]) -> bool:
    return any(downstack(open_rows, tip) & prs for tip in tree)


def blocker_key(pr: str, head: str) -> str:
    """One blocker per PR and head, whether the gate's 10-character sha or the ledger's full one names it, so a red build and the Graphite `FAILING_REQUIRED` it causes route once (root #142221)."""
    return f"blocker:{pr}:{head[:10]}"


def status_blocker(entry: dict, red: bool = True) -> str:
    """What a `ccx vcs pr status` entry asks its lane to fix, or empty while the PR only waits: a conflict, a restack Graphite asks for, red CI with its failing checks unless `red` is False, or requested changes."""
    causes = entry["verdict"].removeprefix("blocked:").split(",")
    said = []
    if "conflict" in causes:
        said.append(CONFLICT_BLOCKER)
    if restack := next((cause for cause in causes if cause.startswith("needs-restack")), ""):
        said.append(f"Graphite reads it as {restack}; run ccx vcs stack submit from the branch's worktree")
    if "ci-red" in causes and red:
        said.append(f"CI is red: {', '.join(entry['ci'].get('failing') or [entry['ci'].get('rollup', 'red')])}")
    if "changes-requested" in causes:
        said.append(f"changes requested by {', '.join(entry['approval']['changes_requested_by'])}")
    return "; ".join(said)


def prefix_tops(prefix: list[str], rows: dict[str, dict]) -> list[str]:
    bases = {rows[pr].get("base") for pr in prefix}
    return [pr for pr in prefix if rows[pr].get("branch") not in bases]


def heads_query(repo: str, shas: list[str]) -> str:
    owner, name = repo.split("/")
    review = f'checkSuites(last: 10, filterBy: {{checkName: "{REVIEW_BOT_CHECK}"}}) {{ nodes {{ checkRuns(last: 1, filterBy: {{checkName: "{REVIEW_BOT_CHECK}"}}) {{ nodes {{ title startedAt }} }} }} }}'
    fields = " ".join(f'h{index}: object(expression: "{sha}") {{ ... on Commit {{ status {{ contexts {{ context state description }} }} {review} }} }}' for index, sha in enumerate(shas))
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'


@dataclass(frozen=True)
class Pool:
    name: str
    limit: int
    remaining: int
    reset: datetime
    callers: list[dict]

    @property
    def key(self) -> str:
        return f"{self.name.replace(' ', '-')}:{int(self.reset.timestamp())}"


def quota_pools(*identities: dict) -> list[Pool]:
    """Each REST and GraphQL quota of `identities` once, named by its identity and resource, its callers heaviest first; the user's identity reads and writes alike when no app is installed. The search quota, 30 calls a minute, is no budget the drive spends."""
    pools = {
        f"{pool['identity']} {pool['resource']}": Pool(f"{pool['identity']} {pool['resource']}", pool["limit"], pool["remaining"], datetime.fromisoformat(pool["resetAt"]), pool["callers"])
        for identity in identities
        for pool in identity["pools"]
        if pool["resource"] in QUOTA_RESOURCES
    }
    return list(pools.values())


def kitchen(moment: datetime) -> str:
    return moment.astimezone(PACIFIC).strftime("%-I:%M %p")


def trunk_query(repo: str, tip: str) -> str:
    owner, name = repo.split("/")
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ object(expression: "{tip}") {{ ... on Commit {{ history(first: {TRUNK_DEPTH}) {{ nodes {{ oid committedDate message status {{ contexts {{ context state targetUrl }} }} }} }} }} }} }} }}'


@dataclass(frozen=True)
class TrunkRed:
    since: str
    at: datetime
    pr: str
    build: str


def trunk_red(history: list[dict], check: str) -> TrunkRed | None | bool:
    """The trunk's newest unbroken run of failed `check` statuses, newest first, skipping commits whose `check` has not finished: its oldest commit, when that commit landed, the PR its squash subject names, and the newest red build; None when the newest finished `check` passed, and False when no commit read has finished it."""
    finished = [(commit, context) for commit in history for context in (commit["status"] or {"contexts": []})["contexts"] if context["context"] == check and context["state"] in FINISHED_STATES]
    if not finished:
        return False
    run = list(itertools.takewhile(lambda pair: pair[1]["state"] != "SUCCESS", finished))
    if not run:
        return None
    first = run[-1][0]
    landed = SQUASHED_PR.search(first["message"].splitlines()[0])
    return TrunkRed(first["oid"], datetime.fromisoformat(first["committedDate"]), landed[1] if landed else "", run[0][1]["targetUrl"] or "")


def build_failures_in(body: str) -> dict[str, str]:
    """Each package a build-failure annotation's list names, with the compile error after it."""
    items = (html.unescape(HTML_TAG.sub("", item)).split(None, 1) for item in LIST_ITEM.findall(body))
    return {item[0]: item[1].strip() for item in items if len(item) == 2}


def import_path(package: str, directory: str, other: str) -> str | None:
    """The import path of the package in `other`, by the module root that `package`, whose directory is `directory`, shares with it; None when `other` is outside that module, or when `directory` is not `package`'s own, as for a missing import Go reports under its importer's header."""
    parts, dirs = package.split("/"), directory.split("/")
    if not (shared := len(os.path.commonprefix([parts[::-1], dirs[::-1]]))):
        return None
    root, module = "/".join(dirs[: len(dirs) - shared]), "/".join(parts[: len(parts) - shared])
    if (relative := os.path.relpath(other, root or ".")).startswith(".."):
        return None
    return module if relative == "." else "/".join(filter(None, (module, relative)))


def failed_steps(build: dict) -> list[str]:
    return sorted({job.get("step_key") or job["name"] for job in build.get("jobs", []) if job.get("type") == "script" and job.get("state") == "failed" and not job.get("soft_failed")})


def job_failures(build: dict) -> Counter[tuple[str, bool]]:
    """How many script jobs of each name failed in `build`, counted apart by whether they soft-failed."""
    return Counter((job.get("name") or job.get("step_key") or "", bool(job.get("soft_failed"))) for job in build.get("jobs", []) if job.get("type") == "script" and job.get("state") == "failed")


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


def pull_action(incident: actions.Incident, key: str, pr: str, why: str, at: str) -> None:
    kept = incident.action(key).response
    incident.action(key).response = {**kept, "outcome": PULLED, "pulled": {"pr": pr, "why": why, "at": at}}


def verdictless(gate: merge_queue.Gate) -> bool:
    return bool(gate.code) and not gate.verdicts


class Landing:
    """The merge queue: gate every tracked stack read-only through the repository adapter, run the check registry over each ready prefix, enqueue it once per set of heads in priority order, classify each ejection, verify by squash, route the restack above it, and send a landed merge-fast PR's release to its release owner."""

    def __init__(self, runner: Runner, config: dict, cci_tail: cci.Tail | None = None):
        self.runner = runner
        self.shell = runner.shell
        self.book = runner.book
        self.cci_tail = cci_tail or runner.cci_tail
        self.repo = config["repo"]
        self.ledger = config["ledger"]
        self.checkout = Path(config["checkout"]).expanduser()
        self.holds = Path(config["holds"]).expanduser()
        self.release_owner = config["release_owner"]
        self.release_cli = release_cli(self.checkout, config)
        self.status_card = config.get("status_card", False)
        self.card_drawn = ""
        self.landed: set[str] = set()
        self.patch_ids: dict[tuple[int, str], str] = {}
        self.commit_counts: dict[tuple[str, str], int] = {}
        self.files: dict[tuple[str, str], list[str]] = {}
        self.exempts: dict[tuple[str, str], tuple[str, str]] = {}
        self.annotated: dict[str, dict[str, str]] = {}
        self.imported: dict[tuple[str, str, frozenset[str]], bool] = {}
        self.bases: dict[str, str] = {}
        self.read_only = False
        self.viewer = ""
        self.reports_file = runner.config.source.with_name(f"{runner.config.source.stem}.check-pr.json")
        self.reported: dict[tuple[str, str, bool], tuple[datetime | None, list[str]]] = {}
        self.kept: dict[str, list[str]] | None = None
        self.kept_under = ""
        self.reporting = threading.Lock()
        self.checking = threading.Semaphore(CHECK_PR_WORKERS)
        self.told: dict[str, tuple[str, datetime]] = {}
        self.refresher: threading.Thread | None = None
        self.overflowing: threading.Thread | None = None
        self.overflowed_at = time.monotonic()
        self.overflower: Landing | None = None
        self.held_now: list[str] = []
        self.workers = ThreadPoolExecutor(GATE_WORKERS)
        self.inflight: dict[str, tuple[set[str], Future]] = {}
        self.dispatching = threading.Lock()
        self.steps: dict[str, dict] = {}
        self.stacks_file = runner.config.source.with_name(f"{runner.config.source.stem}.{STACKS_FILE}")
        self.gate_deadline = time.monotonic()
        self.pass_began = self.trusted_from = time.monotonic()
        self.side = ThreadPoolExecutor(1)
        self.side_work: Future | None = None
        self.bodies: dict[str, tuple[datetime, str]] = {}
        self.trunk_read: tuple[str, datetime, bool, dict] | None = None
        self.unchecked: set[str] = set()
        self.unproven_why: dict[str, str] = {}
        self.sessions: dict[str, bool] = {}
        self.live: list[dict] | None = None
        self.dead: dict[str, bool] = {}
        self.statuses: dict[str, dict] | None = None
        self.queues_read = False
        self.statuses_floor = datetime.min.replace(tzinfo=timezone.utc)
        self.reviews: list[dict] | None = None
        self.claims_read: list[dict] | None = None
        self.scanning = threading.Lock()
        self.designed_red: dict[str, bool] = {}
        self.body_preconditions: dict[tuple[str, str], list[str]] = {}
        self.said_preconditions: dict[str, list[tuple[str, str]]] = {}
        self.resolved_preconditions: dict[str, set[int]] = {}
        self.gos: list[dict] | None = None
        self.cleared: dict[str, int] = {}
        self.going = threading.Lock()
        self.read: dict[str, tuple[datetime, list[dict]]] = {}
        self.review_titles: dict[str, str] = {}
        self.unread: dict[str, dict[str, str]] = {}
        self.reading = threading.Lock()
        self.labels_read: dict[str, bool] = {}
        self.row_heads: dict[str, str] = {}
        self.ledger_rows: dict[str, dict] = {}
        self.builds_read: dict[str, tuple[datetime, list[tuple[str, str, bool | None]]]] = {}
        self.builds: dict[str, dict] = {}
        self.full_runs_read: dict[str, list[dict]] = {}
        self.grading = False
        self.grade_tried: set[str] = set()
        self.stacks: dict[str, set[str]] = {}
        self.no_go: set[str] = set()
        self.below_blocking: set[str] = set()
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
        self.ledger_rows = rows
        return rows

    def held(self, rows: dict[str, dict]) -> list[str] | None:
        """Every PR the holds file, a rules-review block, an open cci hold, or GitHub's `hold` label holds, from one read of the file, so the gate routes no blocker for it or for the PRs stacked above it; None when that read carries a line the desk cannot hold by, and then the caller gates and enqueues nothing."""
        entries = holds.read(self.holds)
        if self.holds_unread(entries, rows):
            return None
        numbers = holds.held(entries, rows) | {pr for pr, row in rows.items() if row.get("rules_blocked") or HOLD_LABEL in row.get("labels", "").split(",")}
        numbers |= cci.held_prs(self.cci_tail, self.runner.config.drive).keys()
        return sorted(numbers, key=int)

    def hold_sources(self, prs: list[str], rows: dict[str, dict]) -> str:
        """Each of `prs` with where its hold stands: the holds file, an open cci `hold` record by seq, a rules-review block, or GitHub's `hold` label, so a reader finds the hold the runner obeys."""
        filed = holds.held(holds.read(self.holds), rows)
        posted = cci.held_prs(self.cci_tail, self.runner.config.drive)

        def where(pr: str) -> str:
            row = rows.get(pr, {})
            found = ["the holds file" if pr in filed else "", f"cci hold #{posted[pr]}" if pr in posted else "", "rules-review" if row.get("rules_blocked") else "", "the hold label" if HOLD_LABEL in row.get("labels", "").split(",") else ""]
            return "; ".join(filter(None, found))

        return " ".join(f"#{pr} ({where(pr)})" for pr in prs)

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

    def gate(self, rows: dict[str, dict], held: list[str], gone: set[str]) -> None:
        """Admit the drive's rows, then gate every tree but those holding a PR of `gone`, which `land_gone` gated earlier in the pass."""
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        bases = {row.get("base") for row in open_rows.values()}
        self.labels_read = {key: True for key, admitted in self.labels_read.items() if admitted}
        admitted = self.admit({pr: row.get("head", "") for pr, row in open_rows.items() if not row.get("ours") and not row.get("unadmitted")})
        for pr, row in open_rows.items():
            if not row.get("ours") and pr not in admitted and row.get("branch") not in bases:
                self.runner.escalate(f"not-ours:{pr}@{row.get('head', '')}", "NOT-OURS", f"#{pr}", f"skipped: {NOT_ADMITTED}, so the desk never gates or enqueues it; {ADMIT_HOW.format(pr=pr)}")
        self.gate_trees(rows, held, [tree for tree in self.stack_trees(rows, held) if not holds_any(open_rows, tree, gone)])

    def land_gone(self, rows: dict[str, dict], held: list[str]) -> set[str]:
        """Gate every tree holding a PR the root's `go` names at its ledger head, or whose change at that head is exempt, `GATE_WORKERS` checks at a time, and enqueue what they accept, before the ledger-wide head read and the gate over every other tree: only those PRs can land, so they reach the queue first. Returns those PRs."""
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        gos = self.root_gos()
        gone = {pr for pr, row in open_rows.items() if row.get("ours") and self.free_to_land(gos, pr, row.get("head", ""))}
        if trees := [tree for tree in self.stack_trees(rows, held) if holds_any(open_rows, tree, gone)]:
            self.gate_trees(rows, held, trees)
        return gone

    def pull_ordered(self, rows: dict[str, dict]) -> set[str]:
        """Take out of the queue each PR a root record on this drive orders out, one that opens with `dequeue #<n>` or says `landing-desk: dequeue #<n>` outside quotes and code, or a `withdraw` of a root `go` naming a PR this desk enqueued at that go's head, so no order waits on a lane reading the bus (root #146459). It runs before any enqueue of the pass. Each order is kept in the desk store until it settles, its progress saved one order at a time, and says `DEQUEUED` only from a status read: one taken after the dequeue that finds the PR out of the queue, when the PR's lane and every drive naming it hear it stopped, or one that finds it already out. Either way the PR's head is latched out until `mq.py requeue`. A read that still finds it queued or landed, a refused dequeue, or no open ledger row says `DEQUEUE-FAILED`. Returns the PRs this pass keeps out of every check and enqueue: each with an open order, and each whose ledger head is latched out."""
        if DEQUEUE_READ not in self.book.load(LANDING).facts:
            self.book.edit(LANDING, lambda incident: incident.facts.setdefault(DEQUEUE_READ, self.started))
        facts = self.book.load(LANDING).facts
        statuses = self.pr_statuses()
        if statuses is not None:
            found, read = self.dequeue_orders(facts[DEQUEUE_READ])

            def remember(incident: actions.Incident) -> None:
                incident.facts.setdefault(DEQUEUE_ORDERS, {}).update({key: order for key, order in found.items() if key not in incident.facts[DEQUEUE_ORDERS]})
                incident.facts[DEQUEUE_READ] = read

            self.book.edit(LANDING, remember)
            for key, order in self.book.load(LANDING).facts.get(DEQUEUE_ORDERS, {}).items():
                self.settle_order(key, order, rows, statuses)
        facts = self.book.load(LANDING).facts
        latched = facts.get(PULLED_HEADS, {})
        return {order["pr"] for order in facts.get(DEQUEUE_ORDERS, {}).values()} | {pr for pr, head in latched.items() if rows.get(pr, {}).get("head", "").startswith(head)}

    def settle_order(self, key: str, order: dict, rows: dict[str, dict], statuses: dict[str, dict]) -> None:
        pr, why = order["pr"], order["why"]
        row, queue = rows.get(pr, {}), statuses.get(pr, {}).get("queue")
        head = row.get("head", "")

        def said(label: str, text: str) -> None:
            self.runner.escalate(f"dequeue:{key}", label, f"#{pr}", f"#{pr} at {head[:10] or 'no open row'}, {why}: {text}")
            self.book.edit(LANDING, lambda incident: incident.facts[DEQUEUE_ORDERS].pop(key))

        if row.get("state") == "landed" or queue == "landed":
            return said("DEQUEUE-FAILED", "it landed before the dequeue took")
        if "sent" in order:
            if queue is None or actions.parse_stamp(order["sent"]) >= self.statuses_floor:
                return None
            if queue == QUEUED_STATE:
                return said("DEQUEUE-FAILED", "the status read after the dequeue still finds it queued")
            if queue in OUT_OF_QUEUE:
                self.tell_stopped(pr, row, f"{why}: {order['line']}")
                return said("DEQUEUED", f"the status read after the dequeue finds it {queue}; its head stays out until mq.py requeue")
            return None
        if queue is None:
            return said("DEQUEUE-FAILED", "it has no open row in this drive's ledger, so its queue state was not read and nothing was taken out")
        if queue in OUT_OF_QUEUE:
            self.latch_pulled(pr, head, why)
            return said("DEQUEUED", f"the status read finds it {queue}, so nothing was taken out; its head stays out until mq.py requeue")
        if queue != QUEUED_STATE:
            return None
        released, line = self.release_pr(pr, why)
        if not released:
            return said("DEQUEUE-FAILED", line)
        self.latch_pulled(pr, head, why)
        self.book.edit(LANDING, lambda incident: incident.facts[DEQUEUE_ORDERS][key].update({"sent": self.book.stamp(), "line": line}))
        return None

    def dequeue_orders(self, read: int | str) -> tuple[dict[str, dict], int | str]:
        """Each order a root record on this drive posted after `read` gives, keyed by record and PR, and the read to start from next time: the newest seq read, else `read`. A root `withdraw` that resolves or answers a root `go` orders out each PR of that go an enqueue of this desk admitted at the head the go names (root #162011)."""
        drive = self.runner.config.drive
        records = [record for record in cci.records(self.cci_tail, drive, "--lane", ROOT, since=read) if record["lane"] == ROOT and record["drive"] == drive]
        orders = {
            f"{record['seq']}:{pr}": {"pr": pr, "why": f"root #{record['seq']} ordered it out"}
            for record in records
            for pr in DEQUEUE_ORDER.findall(QUOTED_SPAN.sub("", QUOTED_BLOCK.sub("", full_text(record))))
        }
        if withdrawn := {seq: record["seq"] for record in records if record["kind"] == "withdraw" for seq in closes(record)}:
            admitted = [(pr, sha) for action in self.book.actions(LANDING, kind="enqueue", status="completed") if action.response["outcome"] in {*BELIEVED_QUEUED, PULLED} for pr, sha in enqueued_heads(action.action_id).items()]
            for go in self.go_records():
                if go["seq"] in withdrawn:
                    orders |= {
                        f"{withdrawn[go['seq']]}:{pr}": {"pr": pr, "why": f"root #{withdrawn[go['seq']]} withdrew go #{go['seq']}"}
                        for pr, head in go_heads(go).items()
                        if any(pr == queued and names_head(head, sha) for queued, sha in admitted)
                    }
        return orders, max((record["seq"] for record in records), default=read)

    def latch_pulled(self, pr: str, head: str, why: str) -> None:
        """Keep `pr` at `head` out of every check and enqueue until `mq.py requeue`, and mark each finished enqueue that admitted it at that head `pulled`."""
        self.book.edit(LANDING, lambda incident: incident.facts.setdefault(PULLED_HEADS, {}).update({pr: head}))
        for action in self.book.actions(LANDING, kind="enqueue", status="completed"):
            if (sha := enqueued_heads(action.action_id).get(pr)) and head.startswith(sha):
                self.book.attempt(LANDING, lambda incident, key=action.action_id: pull_action(incident, key, pr, why, self.book.stamp()))

    def unpassed(self, rows: dict[str, dict]) -> set[str]:
        """The open rows no root `go` names at their ledger head and whose change there is not exempt, when merge mode `every` leaves every ask to the root's board: the pass bars each like a held PR from every `--check` and enqueue call, never a top and never swept in (root #131119, #131287), and `tell_unpassed` routes them from the status read instead. Empty in every other mode, where the gate's check is what asks the owner."""
        if self.merge_mode != "every" or self.landing_config.get("merge_ask_surface", "slack") != "board" or self.lands_green:
            return set()
        gos = self.root_gos()
        return {pr for pr, row in rows.items() if row.get("state", "open") == "open" and not self.free_to_land(gos, pr, row.get("head", ""))}

    def free_to_land(self, gos: list[dict], pr: str, head: str) -> bool:
        """Whether a go of `gos` names `pr` at `head`, or its change there is exempt and goes straight to Graphite's queue with no go or tap (root #194387)."""
        return bool(head) and bool(gone_at(gos, pr, head) or self.exempt_admitted(pr, head)[0])

    def refuse_ungone_parents(self, rows: dict[str, dict], held: list[str]) -> None:
        """Post one note to the root per lowest such parent, naming the rows a root `go` names at their heads above a parent nothing can land past, and saying the stack below it lands without them (root #152548): one `unpassed` keeps out, one a hold covers, one red on CI, or one that conflicts with its base. It posts again only when that stack's set of such parents changes, never for a head that moves: the whole stack waits, `unqueued_gos` never reads it as an unqueued go, and the root hears which parents need the owner's tap, which wait on a hold, and which need their lane. A stack whose every such parent is held reads `BELOW-HELD` (root #142036, #142931, #145011)."""
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open" and row.get("head")}
        red = {pr for pr, row in open_rows.items() if row.get("test_state") == "failure" or row.get("mergeable_state") == "dirty"}
        holding = set(held)
        self.below_blocking = self.no_go | red | holding
        gos = self.root_gos()
        stacks: dict[str, tuple[list[str], set[str]]] = {}
        for pr in sorted(open_rows, key=int):
            if pr in self.below_blocking or not gone_at(gos, pr, open_rows[pr]["head"]) or not (parents := downstack(open_rows, pr) & self.below_blocking):
                continue
            lowest = min(parents, key=lambda below: (len(downstack(open_rows, below)), int(below)))
            gone, waiting = stacks.setdefault(lowest, ([], set()))
            gone.append(pr)
            waiting.update(parents)

        def named(prs: list[str]) -> str:
            return " and ".join(f"#{pr} at {open_rows[pr]['head'][:10]}" for pr in prs)

        for lowest, (gone, waiting) in stacks.items():
            parents = sorted(waiting, key=int)
            key = f"go-parents:{lowest}:" + ",".join(parents)
            if self.book.known(LANDING, key):
                continue
            on_hold = [parent for parent in parents if parent in holding]
            untapped = [parent for parent in parents if parent in self.no_go and parent not in holding]
            broken = [parent for parent in parents if parent not in self.no_go and parent not in holding]
            on = [f"{named(untapped)} with no go at that head"] * bool(untapped) + [f"{named(on_hold)}, which a hold covers"] * bool(on_hold) + [f"{named(broken)}, red on CI or conflicting with its base"] * bool(broken)
            asks = ["the owner taps each parent's merge row and root posts `go #<parent> at <head>`"] * bool(untapped) + ["the stack waits for each hold to lift"] * bool(on_hold) + ["each red or conflicting parent's lane makes it green and mergeable"] * bool(broken)
            label = "BELOW-HELD" if len(on_hold) == len(parents) else "BELOW-BLOCKED"
            self.post_record("note", key, ROOT if label == "BELOW-HELD" else self.landing_audience([*parents, *gone], open_rows, ROOT), f"{label} above #{lowest}: root gos name {named(gone)}, stacked on {' and '.join(on)}, so none of them is enqueued, and the stack below #{lowest} lands without them; {'; '.join(asks)}", lowest)

    def stack_trees(self, rows: dict[str, dict], held: list[str]) -> list[list[str]]:
        """The tips to gate, as `trees` groups them or one tip each under the whole-stack policy, over the rows no hold covers, so no call names a held PR or one stacked above it; every tip at or above an incident fix is left out."""
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        covered = under_hold(open_rows, held)
        free = {pr: row for pr, row in rows.items() if pr not in covered}
        trees = [[tip] for tree in self.trees(free) for tip in tree] if self.whole() else self.trees(free)
        waiting = under_hold(open_rows, self.incident_rows(open_rows))
        return [kept for tree in trees if (kept := [tip for tip in tree if tip not in waiting])]

    def gate_trees(self, rows: dict[str, dict], held: list[str], trees: list[list[str]]) -> None:
        """Hand each tree of `trees` that `landable_trees` keeps to a stack worker that outlives the pass and runs its check, its settling and the enqueue of what it accepts, so no stack's checks or enqueue wait on another's (landing-desk-3 #185922, owner 9524276). A tree whose worker still runs or waits from an earlier pass is skipped, the drive runs `GATE_WORKERS` at once with the lanes taking turns in its queue, and the pass waits for its workers no longer than its `GATE_BUDGET`. One wave per priority value, the lowest first, so a higher-priority stack reaches the queue first. Each enqueue still runs its stack's chains bottom-up."""
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        waves: dict[int, list[list[str]]] = {}
        for tree in self.landable_trees(rows, trees):
            waves.setdefault(min(self.priority(open_rows, pr) for tip in tree for pr in downstack(open_rows, tip)), []).append(tree)
        for priority in sorted(waves):
            self.await_workers([future for tree in lane_turns(waves[priority], open_rows) if (future := self.dispatch(",".join(tree), {pr for tip in tree for pr in downstack(open_rows, tip)}, lambda tree=tree: self.land_tree(rows, held, tree)))])

    def resubmit_graphite_base(self, rows: dict[str, dict]) -> None:
        """Resubmit the lowest open drive PR whose base is a `graphite-base/` branch, where a landed parent leaves it reading conflicted or stuck, on a stack worker beside the landing work: one at a time, once per head."""
        with self.dispatching:
            if any(key.startswith(GRAPHITE_BASE) and not future.done() for key, (_, future) in self.inflight.items()):
                return
        stale = [pr for pr, row in rows.items() if row.get("state", "open") == "open" and row.get("ours") and row.get("base", "").startswith(GRAPHITE_BASE) and not self.book.known(LANDING, f"{GRAPHITE_BASE}{pr}@{row['head']}")]
        if stale:
            pr = min(stale, key=int)
            self.dispatch(f"{GRAPHITE_BASE}{pr}@{rows[pr]['head']}", {pr}, lambda: self.resubmit(pr, rows[pr]))

    def resubmit(self, pr: str, row: dict) -> None:
        """Ship `pr`'s branch as it stands from the landing checkout with `ccx vcs ship --branch <branch> --tip-only --no-commit`, which moves it off its `graphite-base/` base, and post the outcome to the desk lane as a cci note. A ship that fails or is killed at its bound runs `ccx vcs stack abort --stack <branch>`, so its stopped rebase never blocks the next stack write, and the note carries the abort's outcome (#192681); a ship refused because another run already holds the branch leaves that run alone."""
        argv = ["ccx", "vcs", "ship", "--branch", row["branch"], "--tip-only", "--no-commit", "--no-watch"]
        done = self.shell.run(argv, cwd=str(self.checkout))
        last = ((done.err or done.out).strip().splitlines() or [f"exit {done.code}"])[-1][:300]
        outcome = "resubmitted" if done.code == 0 else f"resubmit failed with exit {done.code}"
        text = f"#{pr} {row['head'][:10]} sat on {row['base']} after its parent landed; {outcome} with {shlex.join(argv)}: {last}"
        if done.code and "already in progress" not in done.err + done.out:
            abort = ["ccx", "vcs", "stack", "abort", "--stack", row["branch"]]
            dropped = self.shell.run(abort, cwd=str(self.checkout))
            text += f"; {shlex.join(abort)} exited {dropped.code}: {((dropped.err or dropped.out).strip().splitlines() or ['no output'])[-1][:300]}"
        self.post_record("note", f"{GRAPHITE_BASE}{pr}@{row['head']}", ",".join(self.runner.landing_lanes("desk_lane")) or ROOT, text, pr)

    def dispatch(self, key: str, members: set[str], work: Callable[[], None]) -> Future | None:
        """Queue `work` on a stack worker under `key`, unless a worker that still runs or waits holds any PR of `members`, so two workers never enqueue one PR."""
        with self.dispatching:
            if any(not future.done() and members & others for others, future in self.inflight.values()):
                return None
            future = self.workers.submit(work)
            self.inflight[key] = (members, future)
            return future

    def await_workers(self, futures: list[Future]) -> None:
        """Wait for `futures` until the pass's gate budget runs out, then raise what a finished one raised, dropping each finished one so no later pass raises it again; the rest run on into later passes."""
        finished_by(futures, timeout=max(0.0, self.gate_deadline - time.monotonic()))
        self.surface()

    def close(self) -> None:
        """Let every running stack worker and the side work finish and start nothing queued, so no worker of this runner acts after it hands off."""
        self.workers.shutdown(cancel_futures=True)
        self.side.shutdown(cancel_futures=True)

    def surface(self) -> None:
        """Raise the first error a stack worker or the side work raised after an earlier pass stopped waiting for it, dropping each finished one, so the pass counts it as its own."""
        with self.dispatching:
            finished = {key: future for key, (_, future) in self.inflight.items() if future.done()}
            for key in finished:
                del self.inflight[key]
        side = self.side_work if self.side_work and self.side_work.done() else None
        if side:
            self.side_work = None
        for future in [*finished.values(), *([side] if side else [])]:
            if error := future.exception():
                raise error

    def drain(self) -> None:
        """Wait for every stack worker and the side work, raising what any raised, for a `--once` pass whose process ends with it."""
        for future in [future for _, future in list(self.inflight.values())] + ([self.side_work] if self.side_work else []):
            future.result()

    def land_tree(self, rows: dict[str, dict], held: list[str], tree: list[str]) -> None:
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        key = ",".join(tree)
        try:
            self.runner.checkpoint()
            self.track(key, tree, open_rows, "check")
            gated = self.proven_stacks(tree)
            self.track(key, tree, open_rows, "settle")
            self.settle_tree(rows, held, gated)
            members = {pr for tip in tree for pr in downstack(open_rows, tip)}
            if accepted := [action for action in self.book.actions(LANDING, kind="enqueue", status="accepted") if set(action.target.split(",")) <= members]:
                self.reread()
                self.rescanned_reviews()
                for action in accepted:
                    self.track(key, tree, open_rows, "enqueue")
                    self.enqueue_one(action)
        finally:
            self.track(key, tree, open_rows, "")

    def track(self, key: str, tree: list[str], open_rows: dict[str, dict], step: str) -> None:
        """Record the step a stack worker is on, or that it ended, and rewrite `landing-stacks.json` beside the config, which stall_watch reads to name a stack stuck past its step's budget."""
        root = stack_root(tree, open_rows)
        with self.dispatching:
            if step:
                members = {pr: open_rows[pr].get("head", "") for tip in tree for pr in downstack(open_rows, tip)}
                self.steps[key] = {"root": root, "lane": open_rows[root].get("lane", ""), "members": members, "step": step, "since": self.book.stamp(), "budget": STEP_BUDGETS[step], "thread": threading.get_ident()}
            else:
                self.steps.pop(key, None)
            stacks = {key: {name: value for name, value in entry.items() if name != "thread"} | ({"command": command[0], "command_seconds": int(time.monotonic() - command[1])} if (command := Shell.in_progress.get(entry["thread"])) else {}) for key, entry in self.steps.items()}
            staged = self.stacks_file.with_name(f".{self.stacks_file.name}.{threading.get_ident()}")
            staged.write_text(json.dumps({"at": self.book.stamp(), "stacks": stacks}))
            staged.replace(self.stacks_file)

    def landable_trees(self, rows: dict[str, dict], trees: list[list[str]]) -> list[list[str]]:
        """The trees of `trees` the pass is about to enqueue from, by its one status read: those holding a landable PR on PRs below it that are landable, queued, or landed. `stack-enqueue --check`, and the `rulings.py check-pr` an accepted prefix runs, run for no other tree (root #146561, #147019). A verdict counts as landable once a `queue-unknown` cause is set aside, since a label queue's state is not Graphite's to read, and once a lone `ci-unproven` cause is set aside for a head `unproven` finds proven, since only the runner reads the full builds that prove a redundant skip; `unproven` also starts the full build a head still needs. The PRs of each tree left out join `unchecked`, and `tell_unpassed` routes what each one's status entry asks of its lane. None are kept when the status read failed."""
        if (statuses := self.pr_statuses()) is None:
            return []
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}

        def landable(pr: str) -> bool:
            verdict = statuses.get(pr, {}).get("verdict", "")
            causes = set(verdict.removeprefix("blocked:").split(",")) - {"queue-unknown"} if verdict.startswith("blocked:") else set() if verdict == LANDABLE else {verdict}
            if causes == {"ci-unproven"} and not (why := self.unproven(pr, open_rows.get(pr, {}).get("head", ""))):
                causes.discard("ci-unproven")
            elif causes == {"ci-unproven"}:
                self.unproven_why[pr] = why
            return not causes

        def ready(pr: str) -> bool:
            return landable(pr) and all(landable(below) or statuses.get(below, {}).get("verdict") in STATUS_SETTLED for below in downstack(open_rows, pr) - {pr})

        kept = []
        for tree in trees:
            prs = {pr for tip in tree for pr in downstack(open_rows, tip)}
            if any(ready(pr) for pr in prs):
                kept.append(tree)
            else:
                self.unchecked |= prs
        return kept

    def settle_tree(self, rows: dict[str, dict], held: list[str], gated: list[tuple[list[str], merge_queue.Gate]]) -> None:
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        covered = under_hold(open_rows, held)
        children = stacked(open_rows)
        self.read_reports({pr: sha for _, done in gated if done.would is not None for pr, sha in enqueued_heads(enqueue_key(done.would, done.verdicts)).items()})
        for tips, done in gated:
            verdicts = done.verdicts
            if verdictless(done):
                if done.code != TIMED_OUT:
                    self.gate_failed(tips, done)
                continue
            if done.would is not None:
                self.accept(tips, done.would, verdicts, rows, held)
            for pr, match in verdicts.items():
                if match["verdict"] == "BLOCKED" and pr not in rows and self.adapter.rider(match):
                    if pr not in self.admit({pr: match["sha"]}):
                        self.runner.escalate(f"unnamed:{pr}@{match['sha']}", "FOREIGN", numbered(tips), f"#{pr} would ride the batch, and {NOT_ADMITTED}; nothing was enqueued; {ADMIT_HOW.format(pr=pr)}", "", self.routine_audience(tips, rows))
                elif match["verdict"] == "BLOCKED" and pr in rows and pr not in covered:
                    stack = set(verdicts) | downstack(open_rows, pr)
                    if self.adapter.failing_required(match) and self.red_by_design(pr):
                        continue
                    gated_red = pr in children and self.adapter.failing_required(match) and self.gated_red(pr, match["sha"])
                    if self.restacking(match, stack):
                        continue
                    if ORPHANED_HEAD in match["detail"] and self.head_gos(pr, rows[pr].get("head", "")):
                        self.release_orphan(pr, rows[pr], match["detail"])
                        continue
                    if blocker := self.adapter.blocker(match, stack, gated_red):
                        self.route_blocker(pr, match["sha"], blocker, rows[pr], tips, stack, gated_red, self.adapter.failing_required(match))

    def release_orphan(self, pr: str, row: dict, detail: str) -> None:
        """Take a go'd PR whose queue draft closed unmerged out of the queue once per head, through the adapter's dequeue, so the next pass enqueues its approved head afresh. `--recover` is never run: it can release the closed draft, whose content may lack the approved head's commits (root #195775)."""
        key = f"orphan:{pr}@{row['head']}"
        if self.book.known(LANDING, key):
            return
        released, line = self.adapter.dequeue(pr)
        label = "ORPHAN-RELEASED" if released else "ORPHAN-RELEASE-FAILED"
        then = "the next pass enqueues its approved head afresh" if released else "it stays out of the queue until the root dequeues it"
        self.post_record("note", key, ",".join(dict.fromkeys([ROOT, self.owner(pr, row) or ROOT])), f"{label} #{pr} at {row['head'][:10]}: {detail}; the landing runner ran the adapter's dequeue instead of --recover: {line}; {then}", pr)

    def red_by_design(self, pr: str) -> bool:
        """Whether `pr`'s body opens with "red by design", or its lane's newest contract for it names its failing run as expected."""
        if pr not in self.designed_red:
            lane = self.ledger_rows.get(pr, {}).get("lane")
            if lane and red_contract(self.runner.lane_records(lane, "contract"), pr):
                self.designed_red[pr] = True
            else:
                self.designed_red[pr] = (body := self.body(pr)) is not None and RED_BY_DESIGN.match(body) is not None
        return self.designed_red[pr]

    def body(self, pr: str, max_age: timedelta = BODY_MAX_AGE) -> str | None:
        """`pr`'s body, read again once the copy kept across passes is `max_age` old; None when GitHub refuses it, and the next caller reads again (root #146561)."""
        now = self.runner.now()
        if (kept := self.bodies.get(pr)) and now - kept[0] < max_age:
            return kept[1]
        done = self.shell.run(ghread.read("pr", "view", pr, "--repo", self.repo, "--json", "body", max_age=ghread.CACHED if max_age else ghread.FRESH))
        if done.code != 0:
            return None
        self.bodies[pr] = (now, json.loads(done.out)["body"] or "")
        return self.bodies[pr][1]

    def gate_failed(self, tips: list[str], done: merge_queue.Gate) -> None:
        """Escalate `GATE-FAILED` for a check that read no verdict, naming only the tips that have not landed since the pass read the ledger."""
        if left := [tip for tip in tips if tip not in self.landed_now(tips)]:
            self.runner.escalate(f"gate:{','.join(left)}:{hashlib.sha1(done.last.encode()).hexdigest()[:8]}", "GATE-FAILED", numbered(left), f"the {self.adapter.kind} check exited {done.code} with no verdict: {done.last}")

    def landed_now(self, tips: list[str]) -> set[str]:
        """The tips whose ledger row reads landed once `ledger.py landed` settles them by their squash on the freshly fetched trunk."""
        self.ledger_py("landed", "--repo", self.repo, "--ledger", self.ledger, "--checkout", str(self.checkout), *(f"--pr={tip}" for tip in tips))
        rows = self.rows() or {}
        return {tip for tip in tips if rows.get(tip, {}).get("state") == "landed"}

    def restacking(self, verdict: dict, stack: set[str]) -> bool:
        """Whether the queue ejected this head for a flaky check, which `requeue` sends to the restack lane, so the owner lane is not asked as well."""
        ejection = self.adapter.ejection(verdict, stack)
        return bool(ejection) and ejection.kind == merge_queue.FLAKY_CHECK

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
        """Each tip's gate, joined with every other tip its own check names as one stack. When the last pass's check of the first tip named every other tip, one joined check over every tip gates the tree, and the solo checks run only when stack-enqueue refuses it."""
        gated = []
        while tips:
            self.runner.checkpoint()
            first, *rest = tips
            tried = self.checked(tips) if rest and set(rest) <= self.stacks.get(first, set()) else None
            if tried and not verdictless(tried):
                self.stacks[first] = set(tried.verdicts)
                return [*gated, (tips, tried)]
            alone = self.checked([first])
            members = self.stacks[first] = set(alone.verdicts)
            shared = [first, *(tip for tip in rest if tip in members)]
            joined = alone if len(shared) == 1 else tried if tried and shared == tips else self.checked(shared)
            if verdictless(joined):
                gated += [([first], alone), *(([tip], self.checked([tip])) for tip in shared[1:])]
            else:
                gated.append((shared, joined))
            tips = [tip for tip in rest if tip not in members]
        return gated

    def checked(self, tips: list[str]) -> merge_queue.Gate:
        return self.proven(self.adapter.check(tips, self.whole(), self.row_heads, False))

    def left(self, prefix: list[str], refused: Iterable[str], rows: dict[str, dict]) -> str:
        return cut_at(prefix, refused, self.whole(), {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"})

    def exact(self, tops: list[str]) -> merge_queue.Gate:
        """The gate of exactly `tops` and the PRs below them, leaving out every PR stacked beside or above, as the enqueue of an accepted prefix names them."""
        return self.proven(self.adapter.check(tops, self.whole(), self.row_heads, not self.whole()))

    def proven(self, gate: merge_queue.Gate) -> merge_queue.Gate:
        """`gate` with each GREEN head whose passing build is the Graphite optimizer's redundant-within-its-stack skip read as BLOCKED, and the prefix that would enqueue cut below it, until a full build passes at that head or at the open child it defers to."""
        unproven = {pr: why for pr, verdict in gate.verdicts.items() if verdict["verdict"] == "GREEN" and (why := self.unproven(pr, self.row_heads.get(pr) or verdict["sha"]))}
        if not unproven:
            return gate
        verdicts = {pr: dict(verdict, verdict="BLOCKED", detail=unproven[pr]) if pr in unproven else verdict for pr, verdict in gate.verdicts.items()}
        would = list(itertools.takewhile(lambda pr: pr not in unproven, gate.would or []))
        return replace(gate, verdicts=verdicts, would=would or None)

    def unproven(self, pr: str, sha: str) -> str:
        """Why `sha` is not proven green, or empty. A failed Buildkite build at the head itself always wins. A redundant skip is proven only by a passed full build of the skip's own pipeline on a commit that contains the head: one at the stack's tip, or one a `full run green <build>` record from the PR's lane names for it. A read that fails reads as no skip, and the next pass reads it again (4236611)."""
        if not self.landing_config.get("ci_build"):
            return ""
        builds = self.ci_builds(sha)
        if builds is None:
            return ""
        if red := [url for url, state, _ in builds if state in ("failure", "error")]:
            return f"unproven: its own build {red[0]} failed at {sha[:10]}, and no covering build overrides it. Fix and push, or retrigger it."
        if not (skipped := [url for url, _, skip in builds if skip]):
            return ""
        pipeline = BUILD_URL.search(skipped[0])["pipeline"]
        child = self.deferred_child(pr)
        tip = [(url, state) for url, state, skip in (self.ci_builds(child["head"]) or []) if skip is False and BUILD_URL.search(url)["pipeline"] == pipeline] if child and child.get("head") else []
        reported = [BUILDS_PATH.sub(f"/builds/{number}", skipped[0]) for number in self.reported_full_runs(pr)]
        graded = [started.target] if (started := self.book.known(LANDING, f"{GRADE_KEY}{sha}")) else []
        if any(self.passes_over(url, sha) for url in [*(url for url, state in tip if state == "success"), *reported, *graded]):
            return ""
        named = f"child #{child['pr']} {child.get('state', 'open')}" if child else "no child in the ledger"
        if not graded and self.grading and not any(state == "pending" for _, state in tip) and (url := self.grade(pr, sha, pipeline)):
            graded = [url]
        if graded:
            return f"unproven: redundant skip, {named}, so full build {graded[0]} grades {sha[:10]}; it is GREEN once that build passes."
        return f"unproven: redundant skip, {named}, so no full test build proves {sha[:10]}. Run a full build on the stack's tip or post `full run green <build>: #{pr}` once one containing this head passes."

    def grade(self, pr: str, sha: str, pipeline: str) -> str:
        """Start one full `pipeline` build at `sha` through `landing.ci_grade` on a `ci-grade/<pr>` branch, which has no open PR for the Graphite optimizer to call redundant, and book its URL; empty when `ci_grade` is unset, the head was tried this pass, or the start fails, which escalates once per head."""
        if not (template := self.landing_config.get("ci_grade")) or sha in self.grade_tried:
            return ""
        self.grade_tried.add(sha)
        branch = f"{GRADE_BRANCH}{pr}"
        done = self.shell.run(["gh", "api", "-X", "PATCH", f"repos/{self.repo}/git/refs/heads/{branch}", "-f", f"sha={sha}", "-F", "force=true"])
        if done.code != 0:
            done = self.shell.run(["gh", "api", "-X", "POST", f"repos/{self.repo}/git/refs", "-f", f"ref=refs/heads/{branch}", "-f", f"sha={sha}"])
        if done.code == 0:
            done = self.shell.run(grade_argv(self.checkout, template, {"commit": sha, "branch": branch, "pipeline": pipeline, "pr": pr}))
        url = started_build_url(done.out) if done.code == 0 else ""
        if not url:
            self.runner.escalate(f"ci-grade:{pr}@{sha}", "CI-GRADE-FAILED", f"#{pr}", f"could not start a full {pipeline} build at {sha[:10]} on {branch}: {(done.err or done.out).strip()[:200]}")
            return ""
        self.book.accept(LANDING, f"{GRADE_KEY}{sha}", "ci-grade", url, "runner", None)
        return url

    def ci_builds(self, sha: str) -> list[tuple[str, str, bool | None]] | None:
        """Each Buildkite build GitHub's combined status names at `sha`, read again once the copy kept across passes is `BODY_MAX_AGE` old, so a head waiting on its proof costs one status read per interval, not per pass (root #146561): its URL, its state, and whether it is a redundant-within-its-stack skip; None when the status does not read, and None for a build that does not."""
        now = self.runner.now()
        if not (kept := self.builds_read.get(sha)) or now - kept[0] >= BODY_MAX_AGE:
            done = self.shell.run(ghread.read("api", f"repos/{self.repo}/commits/{sha}/status"))
            if done.code != 0:
                return None
            statuses = json.loads(done.out)["statuses"]
            self.builds_read[sha] = (now, [(status["target_url"], status["state"], self.redundant(status["target_url"]) if status["state"] == "success" else False) for status in statuses if "buildkite.com/" in (status.get("target_url") or "") and BUILD_URL.search(status["target_url"])])
        return self.builds_read[sha][1]

    def watch_trunk(self, rows: dict[str, dict]) -> bool:
        """Read `landing.trunk_check` over the trunk's last `TRUNK_DEPTH` commits and post its state on the `dev-red` topic to root and the watchdog. A red run whose failed jobs include one a PR build also runs is DEV-RED, posted as an incident that says no PR can land; one whose failed jobs no PR build runs is POST-MERGE-RED, a report that says landing continues and a fix is owed; each posts the pass it is first read and again every 30 minutes while red, and DEV-GREEN posts once when the check passes again. It bars nothing: the queue's own required checks hold what cannot land, and a hold here would freeze the queue. False when the trunk does not read, so the pass gates nothing: `TRUNK-READ-FAILED` posts once per run of unread passes and `TRUNK-READ-RECOVERED` once a read succeeds."""
        if not (check := self.landing_config.get("trunk_check")):
            return True
        target, why = self.trunk_target(check)
        if not target:
            streak = self.book.edit(LANDING, lambda incident: dict(incident.facts.setdefault(TRUNK_UNREAD_FACT, {"since": self.book.stamp(), "error": why})))
            self.runner.escalate(f"trunk-read:{streak['since']}", "TRUNK-READ-FAILED", "landing", f"{TRUNK}'s {check} statuses did not read: {why}; until they read, each pass gates and enqueues nothing and reads again the next")
            return False
        if TRUNK_UNREAD_FACT in self.book.load(LANDING).facts and (streak := self.book.edit(LANDING, lambda incident: incident.facts.pop(TRUNK_UNREAD_FACT, None))):
            since = actions.parse_stamp(streak["since"])
            self.runner.escalate(f"trunk-read-recovered:{streak['since']}", "TRUNK-READ-RECOVERED", "landing", f"{TRUNK}'s {check} statuses read again; from {pacific(since)} for {whole_minutes(since, self.runner.now())} min no pass gated ({streak['error']})")
        self.compare_trunk_builds(target["history"]["nodes"], check, rows)
        posted = cci.trunk(cci.records(self.cci_tail, self.runner.config.drive, f"--topic={cci.TRUNK_TOPIC}"))
        red = trunk_red(target["history"]["nodes"], check)
        if red is False:
            return True
        if red is None:
            if posted.since:
                self.runner.cci_post("report", f"DEV-GREEN {TRUNK}'s {check} passes again; it was red since {posted.since}.", f"{ROOT},{self.runner.watchdog}", cci.TRUNK_TOPIC, f"dev-green:{self.runner.now():%Y%m%d%H%M}")
            return True
        built = BUILD_URL.search(red.build) if "buildkite.com/" in red.build else None
        build = self.build(red.build) if built and self.landing_config.get("ci_build") else None
        jobs = failed_steps(build) if build else []
        landing = self.landing_steps(built["pipeline"], rows) if jobs else None
        blocking = landing is None or bool(set(jobs) & landing)
        since = red.at.astimezone(PACIFIC).strftime("%-I:%M %p")
        failing = f"fails {', '.join(jobs)} on {red.build}" if jobs else f"is red on {red.build}"
        origin = f"first red at {red.since[:10]}, landed by #{red.pr}" if red.pr else f"first red at {red.since[:10]}"
        bucket = int((self.runner.now() - red.at) / TRUNK_RED_EVERY)
        if blocking:
            fix = f"nothing lands until #{posted.fix} fixes it" if posted.fix else "nothing lands until a fix lands; name it with `cci post --topic dev-red --pr <n>`"
            text = f"DEV-RED since {since}: {TRUNK}'s {check} {failing}, {origin}. No PR can land until this is fixed; {fix}."
            self.runner.cci_post("incident", text, f"{ROOT},{self.runner.watchdog}", cci.TRUNK_TOPIC, f"dev-red:{red.since}:{bucket}", record_id=f"dev-red:{red.since}:{bucket}")
            return True
        owed = f"#{posted.fix} fixes it" if posted.fix else "name the fix with `cci post --topic dev-red --pr <n>`"
        text = f"POST-MERGE-RED since {since}: {TRUNK}'s {check} {failing}, {origin}; no PR build runs these jobs. Landing continues; a fix is owed: {owed}."
        self.runner.cci_post("report", text, f"{ROOT},{self.runner.watchdog}", cci.TRUNK_TOPIC, f"post-merge-red:{red.since}:{bucket}", record_id=f"post-merge-red:{red.since}:{bucket}")
        return True

    def compare_trunk_builds(self, history: list[dict], check: str, rows: dict[str, dict]) -> None:
        """Compare each finished `check` build on the trunk newer than the last one compared with the finished build before it, oldest first, each build read once through `landing.ci_build`: every job name that fails or soft-fails more often in the newer build posts one `POST-MERGE-RED` to root, each `landing.desk_lane`, and the lanes of the PRs whose squashes landed between the two, naming the newer build, those jobs, and those PRs. A build that passes because its failing jobs soft-fail is read here, since its status alone reads green. The walk stops at a build that does not read or whose record did not post, and starts there again the next pass."""
        if not self.landing_config.get("ci_build"):
            return
        finished = [(index, url) for index, commit in enumerate(history) for context in (commit["status"] or {"contexts": []})["contexts"] if context["context"] == check and context["state"] in FINISHED_STATES and "buildkite.com/" in (url := context["targetUrl"] or "") and BUILD_URL.search(url)]
        urls = [url for _, url in finished]
        compared = self.book.load(LANDING).facts.get(TRUNK_JOBS_FACT)
        for position in reversed(range(min(urls.index(compared) if compared in urls else 1, len(finished) - 1))):
            if not self.compare_builds(history, finished[position], finished[position + 1], rows):
                return
            self.book.edit(LANDING, lambda incident, url=urls[position]: incident.facts.update({TRUNK_JOBS_FACT: url}))

    def compare_builds(self, history: list[dict], newer: tuple[int, str], older: tuple[int, str], rows: dict[str, dict]) -> bool:
        """Post one `POST-MERGE-RED` for the jobs that fail more often in the newer trunk build than in the older one; False while either build does not read finished or the record did not post."""
        (since, newest), (until, previous) = newer, older
        for url in (newest, previous):
            self.build(url)
        if newest not in self.builds or previous not in self.builds:
            return False
        if not (new := job_failures(self.builds[newest]) - job_failures(self.builds[previous])):
            return True
        number = BUILD_URL.search(newest)["number"]
        prs = [found[1] for commit in history[since:until] if (found := SQUASHED_PR.search(commit["message"].splitlines()[0]))]
        owners = [lane for pr in prs if (lane := rows.get(pr, {}).get("lane"))]
        jobs = ", ".join(f"{name} {'soft-failed' if soft else 'failed'}{f' in {count} jobs' if count > 1 else ''}" for (name, soft), count in sorted(new.items()))
        landed = ", ".join(f"#{pr}" for pr in prs) or "no PR a squash subject names"
        text = f"{TRUNK}'s build {newest} has jobs failing that {previous} did not: {jobs}. Landed between the two: {landed}. A soft-failed job leaves the build green, so nothing else reads it; a fix is owed"
        self.runner.escalate(f"trunk-jobs:{number}", "POST-MERGE-RED", f"{TRUNK} build {number}", text, to=",".join(dict.fromkeys([ROOT, *map(self.successor, [*self.runner.landing_lanes("desk_lane"), *owners])])))
        return bool(self.book.known(RUNNER, f"escalation:trunk-jobs:{number}"))

    def trunk_target(self, check: str) -> tuple[dict | None, str]:
        """The trunk's last `TRUNK_DEPTH` commits with their statuses, read from GitHub only when `git ls-remote` shows the trunk moved since the last read, or when the head's `check` had not passed at that read and `TRUNK_REREAD` has passed, so a rerun that turns a red head green is read within that interval, so a settled trunk costs no API read a pass (root #146561); with the cause when it did not read."""
        listed = self.shell.run(["git", "-C", str(self.checkout), "ls-remote", "origin", f"refs/heads/{TRUNK}"])
        head = listed.out.split()[0] if listed.code == 0 and listed.out.strip() else ""
        now = self.runner.now()
        if not head:
            return None, (((listed.err or listed.out).strip().splitlines()[-1:] or [f"exit {listed.code}"])[0] if listed.code else f"GitHub knows no {TRUNK} branch in {self.repo}")
        if (kept := self.trunk_read) and kept[0] == head and (kept[2] or now - kept[1] < TRUNK_REREAD):
            return kept[3], ""
        done = self.shell.run(ghread.read("api", "graphql", "-f", f"query={trunk_query(self.repo, head)}"), env={"GH_REPO": self.repo})
        target = json.loads(done.out)["data"]["repository"]["object"] if done.code == 0 else None
        if not target:
            return None, (((done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"])[0] if done.code else f"GitHub knows no {TRUNK} branch in {self.repo}")
        newest = (target["history"]["nodes"] or [{}])[0]
        settled = any(context["context"] == check and context["state"] == "SUCCESS" for context in (newest.get("status") or {"contexts": []})["contexts"])
        self.trunk_read = (head, now, settled, target)
        return target, ""

    def landing_steps(self, pipeline: str, rows: dict[str, dict]) -> set[str] | None:
        """Every job any open row's finished full build of `pipeline` runs: a PR build leaves out each unit it did not touch, so one build alone reads a landing job as post-merge. None when no such build reads."""
        builds = [build for row in rows.values() if row.get("state", "open") == "open" and row.get("head") for url, state, skip in self.ci_builds(row["head"]) or [] if skip is False and state in ("success", "failure") and BUILD_URL.search(url)["pipeline"] == pipeline and (build := self.build(url))]
        return {job.get("step_key") or job["name"] for build in builds for job in build.get("jobs", []) if job.get("type") == "script"} if builds else None

    def build(self, url: str) -> dict | None:
        """The Buildkite build at `url`, read through `landing.ci_build` and kept once it has finished."""
        if url not in self.builds:
            done = self.shell.run(release_build_argv(self.checkout, self.landing_config["ci_build"], url))
            if done.code != 0:
                return None
            build = json.loads(done.out)
            if build.get("finished_at") is None and build.get("state") not in ("passed", "failed", "canceled"):
                return build
            self.builds[url] = build
        return self.builds[url]

    def redundant(self, url: str) -> bool | None:
        build = self.build(url)
        return None if build is None else any(REDUNDANT_SKIP.search(job.get("name") or "") for job in build.get("jobs", []))

    def passes_over(self, url: str, sha: str) -> bool:
        """Whether the build at `url` passed in full, no job of it a redundant skip, on a commit that contains `sha`."""
        build = self.build(url)
        return bool(build) and build.get("state") == "passed" and self.redundant(url) is False and bool(build.get("commit")) and self.contains(build["commit"], sha)

    def reported_full_runs(self, pr: str) -> list[str]:
        """The build numbers that `full run green <build>` records from `pr`'s lane name for it, by `--pr` or by `#<pr>` in that sentence, over the last two days."""
        lane = self.ledger_rows.get(pr, {}).get("lane")
        if not lane:
            return []
        if lane not in self.full_runs_read:
            drive = self.runner.config.drive
            since = actions.stamp(self.runner.now() - FULL_RUN_WINDOW)
            self.full_runs_read[lane] = [record for record in cci.records(self.cci_tail, drive, "--kind=note", "--kind=report", "--lane", lane, since=since) if record["drive"] == drive and FULL_RUN_GREEN.search(record["text"])]
        found = []
        for record in self.full_runs_read[lane]:
            named = {number for number, _ in repo_prs(record, self.repo)}
            for match in FULL_RUN_GREEN.finditer(record["text"]):
                if pr in named or pr in re.findall(r"#(\d+)", match["sentence"]):
                    found.append(match["build"])
        return found

    def contains(self, head: str, sha: str) -> bool:
        done = self.shell.run(ghread.read("api", f"repos/{self.repo}/compare/{sha}...{head}", "--jq", ".status"))
        return done.code == 0 and done.out.strip() in ("ahead", "identical")

    def deferred_child(self, pr: str) -> dict | None:
        """The topmost PR the ledger stacks on `pr`, whose build the Graphite optimizer runs for the stack."""
        row = self.ledger_rows.get(pr, {})
        while kids := sorted((kid for kid in self.ledger_rows.values() if row.get("branch") and kid.get("base") == row["branch"]), key=lambda kid: int(kid["pr"])):
            row = kids[-1]
        return row if row.get("pr", pr) != pr else None

    @property
    def fidelity_lanes(self) -> list[str]:
        configured = json.loads(self.runner.config.source.read_text())["landing"]["fidelity_lane"]
        return [configured] if isinstance(configured, str) else list(configured)

    @property
    def fidelity_to(self) -> list[str]:
        return list(dict.fromkeys(map(self.successor, self.fidelity_lanes)))

    @property
    def fidelity_names(self) -> str:
        return " or ".join(self.fidelity_lanes)

    def grades(self, pr: str) -> list[dict]:
        return fidelity_grades_in(self.scanned_reviews(), self.runner.config.drive, self.fidelity_lanes, self.repo, pr)

    def standing(self, grades: list[dict], sha: str) -> list[dict]:
        return standing(grades, self.fidelity_lanes, sha)

    def fidelity(self, pr: str, sha: str) -> Grade:
        """Whether `pr` at `sha` counts as graded ok, read again with every grade posted since the pass scanned reviews before it says no, so a grade that lands while a long pass runs counts (#190787)."""
        grades = self.grades(pr)
        grade = self.fidelity_in(grades, pr, sha)
        if grade.ok:
            return grade
        fresh = fidelity_grades_in(self.rescanned_reviews(), self.runner.config.drive, self.fidelity_lanes, self.repo, pr)
        return grade if len(fresh) == len(grades) else self.fidelity_in(fresh, pr, sha)

    def fidelity_in(self, grades: list[dict], pr: str, sha: str) -> Grade:
        """Whether `pr` at `sha` counts as graded ok. Each fidelity lane's newest grade naming the head stands for that lane, so one lane's `missing` blocks until that lane grades the head ok or a root record resolves that `missing`, which accepts it, whatever another lane posts, except that a fidelity desk's ok at the head stands over a `fidelity-pipeline` missing (root #177596, #176536); an ok counts as posted (4236611, root #144646). With no grade naming the head, the ok of an older head whose lanes all stand at ok carries when the PR's own commits at both heads have one patch (root #131054)."""
        newest = self.standing(grades, sha)
        desk_ok = any(grade["lane"] != fidelity.LANE and grade["text"].startswith(FIDELITY_OK) for grade in newest)
        misses = [grade for grade in newest if grade["text"].startswith(FIDELITY_MISSING) and not (desk_ok and grade["lane"] == fidelity.LANE)]
        accepted = {seq for record in cci.records(self.cci_tail, self.runner.config.drive, "--lane", ROOT, "--pr", f"{self.repo}#{pr}") if record["lane"] == ROOT for seq in closes(record)} if misses else set()
        if missing := max((grade for grade in misses if grade["seq"] not in accepted), key=lambda record: record["seq"], default=None):
            return Grade(False, missing, f"{missing['lane']} graded this head on cci #{missing['seq']}: {missing['text']}")
        if newest:
            oks = [grade for grade in newest if grade["text"].startswith(FIDELITY_OK)] or newest
            return Grade(True, max(oks, key=lambda record: record["seq"]), "")
        none = Grade(False, None, f"no review from {self.fidelity_names} starting `{FIDELITY_OK}` names this head")
        if not (sha and any(grade["text"].startswith(FIDELITY_OK) for grade in grades)):
            return none
        carried = self.carried(pr, sha, grades)
        return Grade(True, carried[0], f"{FIDELITY_OK} at {sha}, patch-identical to {carried[1]}") if carried else none

    def carried(self, pr: str, sha: str, grades: list[dict]) -> tuple[dict, str] | None:
        """The newest ok of an older head whose fidelity lanes all stand at ok, with that head, when its patch is identical to `sha`'s."""
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
        if not oks or not (commits := self.own_commits(pr, sha)) or not (ours := self.patch_id(pr, sha, commits, escalate=True)):
            return None
        return next(((grade, named) for named, grade in oks.items() if self.patch_id(pr, named, commits, escalate=False) == ours), None)

    def own_commits(self, pr: str, sha: str) -> int:
        """How many commits `pr` carries at its head `sha`, from GitHub's read of the PR, kept per head once it reads; 0 when GitHub refuses it, and the next use reads again."""
        if (pr, sha) not in self.commit_counts:
            done = self.shell.run(ghread.read("api", f"repos/{self.repo}/pulls/{pr}", "--jq", ".commits"))
            if done.code != 0 or not done.out.strip().isdigit():
                return 0
            self.commit_counts[(pr, sha)] = int(done.out)
        return self.commit_counts[(pr, sha)]

    def patch_id(self, pr: str, sha: str, commits: int, escalate: bool) -> str:
        """`git patch-id --stable` of the last `commits` commits up to `sha`, the PR's own patch whatever its parent was restacked onto, read from GitHub's compare; empty when GitHub refuses the read, which escalates once for the PR's own head and is silent for a short sha a grade names that is no commit."""
        if (commits, sha) in self.patch_ids:
            return self.patch_ids[(commits, sha)]
        span = f"{sha}~{commits}...{sha}"
        diff = self.shell.run(ghread.read("api", f"repos/{self.repo}/compare/{span}", "-H", DIFF_MEDIA, ALLOW_ESCAPES))
        if diff.code != 0:
            if escalate and not self.read_only:
                last = (diff.err or diff.out).strip().splitlines()[-1:] or [f"exit {diff.code}"]
                self.runner.escalate(f"patch:{pr}:{sha}", "PATCH-READ-FAILED", f"#{pr}", f"GitHub refused the diff of {span}: {last[0]}; no fidelity ok carries to this head until it reads, and the runner reads it again on the next pass")
            return ""
        done = self.shell.run(["git", "patch-id", "--stable"], stdin=diff.out)
        self.patch_ids[(commits, sha)] = done.out.split()[0] if done.code == 0 and done.out.strip() else ""
        return self.patch_ids[(commits, sha)]

    def head_statuses(self, pr: str, sha: str) -> list[dict] | None:
        return self.read_heads({sha: pr}).get(sha)

    def cached(self, sha: str) -> list[dict] | None:
        now = self.runner.now()
        with self.reading:
            return next((statuses for head, (at, statuses) in self.read.items() if same_head(head, sha) and now - at < HEAD_FLOOR), None)

    def read_heads(self, heads: dict[str, str]) -> dict[str, list[dict] | None]:
        """The commit statuses of each head in `heads`, a sha to the PR it heads, and into `review_titles` the title of the review bot's newest `ai-review` run on it: one GraphQL query per `HEADS_PER_QUERY` heads not read in the last `HEAD_FLOOR`. A head GitHub refuses or does not know reads None and joins `unread` under its cause, which `report_unread` logs at the end of the pass."""
        found = {sha: statuses for sha in heads if (statuses := self.cached(sha)) is not None}
        due = sorted(set(heads) - set(found))
        for start in range(0, len(due), HEADS_PER_QUERY):
            batch = due[start : start + HEADS_PER_QUERY]
            done = self.shell.run(ghread.read("api", "graphql", "-f", f"query={heads_query(self.repo, batch)}"), env={"GH_REPO": self.repo})
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
                runs = [run for suite in commit["checkSuites"]["nodes"] for run in suite["checkRuns"]["nodes"]]
                with self.reading:
                    self.read[sha] = (now, statuses)
                    self.review_titles[sha] = max(runs, key=lambda run: run["startedAt"] or "", default={"title": ""})["title"] or ""
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
        """Whether a quota of the identity this desk reads GitHub through is under ccx's `QUOTA_FLOOR`, from one `ccx vcs auth status --json` read. Every quota, the user's or the app's, REST or GraphQL, under `QUOTA_URGENT` calls left posts one urgent `GITHUB-QUOTA-LOW` per reset naming its `TOP_CALLERS` heaviest callers; each read quota under the floor logs one `GITHUB-BACKOFF` per reset; a read that fails logs one `QUOTA-READ-FAILED` per hour and reads as not low."""
        done = self.shell.run(["ccx", "vcs", "auth", "status", self.repo, "--json"])
        if done.code != 0:
            last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
            self.runner.escalate(f"quota-read:{self.runner.now():%Y%m%d%H}", "QUOTA-READ-FAILED", "landing", f"the GitHub quota read failed: {last[0]}; the pass runs without it")
            return False
        [auth] = json.loads(done.out)
        for pool in quota_pools(auth["reads"], auth["writes"]):
            if pool.remaining < QUOTA_URGENT:
                callers = ", ".join(f"{caller['caller']} {caller['requests']}" for caller in pool.callers[:TOP_CALLERS]) or "none recorded"
                self.runner.escalate(f"github-quota:{pool.key}", "GITHUB-QUOTA-LOW", "github", f"the {pool.name} quota has {pool.remaining} of {pool.limit} calls left and resets at {kitchen(pool.reset)}; top callers: {callers}")
        low = [pool for pool in quota_pools(auth["reads"]) if pool.remaining < QUOTA_FLOOR]
        for pool in low:
            self.runner.escalate(
                f"github-backoff:{pool.key}",
                "GITHUB-BACKOFF",
                "landing",
                f"GitHub's {pool.name} quota has {pool.remaining} of {pool.limit} calls left, under ccx's {QUOTA_FLOOR}-call floor; the desk reads nothing from GitHub until it resets at {kitchen(pool.reset)}, and accepted enqueues and root go records run after the reset",
            )
        return bool(low)

    def ungraded(self, heads: dict[str, str]) -> dict[str, Grade]:
        grades = {pr: self.fidelity(pr, sha) for pr, sha in heads.items()}
        return {pr: grade for pr, grade in grades.items() if not grade.ok}

    def refuse_ungraded(self, heads: dict[str, str], ungraded: dict[str, Grade], rows: dict[str, dict]) -> None:
        for pr, grade in ungraded.items():
            key = f"fidelity:{pr}:{heads[pr]}:{grade.record['seq'] if grade.record else 'ungraded'}"
            if self.book.known(LANDING, key):
                continue
            why = grade.why
            owner = self.owner(pr, rows[pr])
            if grade.record and grade.record["text"].startswith(FIDELITY_MISSING) and not owner_grounded(grade.record["text"]):
                grounds = missing_grounds(grade.record["text"]) or "nothing"
                self.post_blocker(key, ",".join([ROOT, *self.fidelity_to]), f"#{pr} {heads[pr]}: {self.left(list(heads), ungraded, rows)}; {why} | it rests on `{grounds}`, no plan line and no owner row, so it changes no code: the grader re-posts it from `rulings.py grade`, or root puts the gap on the owner's board", pr)
                continue
            to = ",".join([*(self.reach(owner) if owner else []), *self.fidelity_to])
            self.post_blocker(key, to, f"{self.dead_note(owner)}#{pr} {heads[pr]}: {self.left(list(heads), ungraded, rows)}; {why}", pr)

    def accept(self, tips: list[str], prefix: list[str], verdicts: dict[str, re.Match], rows: dict[str, dict], held: list[str]) -> None:
        """One enqueue per exact set of prefix heads, every one a PR a drive lane opened and the fidelity lane graded ok at that head. An accept check that refuses a PR cuts the prefix below it, and the PRs under the cut are tried again on their own, so they land now."""
        base = enqueue_key(prefix, verdicts)
        if foreign := [pr for pr in prefix if not rows.get(pr, {}).get("ours")]:
            admitted = self.admit({pr: rows.get(pr, {}).get("head") or (verdicts[pr]["sha"] if pr in verdicts else "") for pr in foreign if not rows.get(pr, {}).get("unadmitted")})
            if unclaimed := [pr for pr in foreign if pr not in admitted]:
                self.runner.escalate(f"foreign:{base}", "FOREIGN", numbered(tips), f"the {self.adapter.kind} check would also enqueue {', '.join('#' + pr for pr in unclaimed)}, which the drive did not admit; nothing was enqueued", "", self.routine_audience(tips, rows))
            return
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        if out := cut_out(prefix, set(held), open_rows):
            prefix = [pr for pr in prefix if pr not in out]
            if named := [pr for pr in out if pr in held and pr not in self.no_go]:
                left = f"the enqueue names only {numbered(prefix)}, with `--partial`" if prefix else "nothing in it is enqueued"
                self.runner.escalate(f"held-out:{base}", "HELD-OUT", numbered(tips), f"a hold covers {self.hold_sources(named, rows)}, so {numbered(out)} stay out of the batch and {left}")
        batched = self.whole_batches(prefix, tips, rows, open_rows)
        if not batched or self.whole() and batched != prefix:
            return
        prefix = batched
        if fork := unlanded_fork(prefix, open_rows):
            self.runner.escalate(f"fork:{base}", "FORK-DEFERRED", numbered(tips), f"{numbered(prefix)} fork off #{fork}, which has not landed; Graphite keeps one linear chain per batch, so they enqueue once #{fork} lands", "", self.landing_audience(prefix, rows, ""))
            return
        while prefix and (refused := self.accept_prefix(prefix, verdicts, rows)) and not self.whole():
            out = cut_out(prefix, refused, open_rows)
            prefix = self.whole_batches([pr for pr in prefix if pr not in out], tips, rows, open_rows)

    def whole_batches(self, prefix: list[str], tips: list[str], rows: dict[str, dict], open_rows: dict[str, dict]) -> list[str]:
        """`prefix` without each PR of a root `go` batch that `prefix` does not hold whole, and the PRs stacked above them, until every batch left is whole: the PRs a `go ... with #a #b` binds enqueue in one call or not at all, so a hold, a red check, a PR with no ledger row, or a batch spread over two chains of a fork keeps them all out. A PR the ledger reads landed or closed leaves its batch."""
        closed = {pr for pr, row in rows.items() if row.get("state", "open") != "open"}
        batches = [go_batch(go) - closed for go in self.root_gos() if any(pr in open_rows and names_head(sha, open_rows[pr].get("head", "")) for pr, sha in go_heads(go).items())]
        split = {pr for batch in batches if not any(batch <= downstack(open_rows, top) for top in batch & open_rows.keys()) for pr in batch}
        kept = prefix
        while out := cut_out(kept, split | {pr for batch in batches if batch & set(kept) and not batch <= set(kept) for pr in batch}, open_rows):
            kept = [pr for pr in kept if pr not in out]
        if waiting := [pr for pr in prefix if pr not in kept]:
            self.runner.escalate(f"batch-waits:{','.join(waiting)}", "BATCH-WAITS", numbered(tips), f"a root go binds {numbered(waiting)} into a batch the queue cannot take whole in one call yet, so they stay out of this enqueue")
        return kept

    def accept_prefix(self, prefix: list[str], verdicts: dict[str, re.Match], rows: dict[str, dict]) -> set[str]:
        """Accept one enqueue of `prefix` at its heads unless an earlier attempt, ejection, pull, or awaited `go` already decides it; a fresh attempt only after every earlier one enqueued nothing. Returns the PRs the first refusing accept check holds, empty when nothing refused."""
        base = enqueue_key(prefix, verdicts)
        enqueues = self.book.actions(LANDING, kind="enqueue")
        ejected = {pr: sha for action in enqueues if action.response for pr, sha in action.response.get("ejected", {}).items()}
        if any(sha and ejected.get(pr) == sha for pr, sha in enqueued_heads(base).items()):
            return set()
        pulled = {(action.response["pulled"]["pr"], enqueued_heads(action.action_id).get(action.response["pulled"]["pr"])) for action in enqueues if (action.response or {}).get("outcome") == PULLED}
        if any((pr, sha) in pulled for pr, sha in enqueued_heads(base).items()):
            return set()
        if self.awaiting_go(enqueues, enqueued_heads(base)):
            return set()
        attempts = [action for action in enqueues if action.action_id.split("#")[0] == base]
        if any(action.status != "completed" or action.response["outcome"] not in RETRYABLE for action in attempts):
            return set()
        if failed := self.failing(ACCEPT_CHECKS, Graded(enqueued_heads(base), rows, self.runner.now(), act=True)):
            return set(failed[1])
        self.book.accept(LANDING, f"{base}#{len(attempts) + 1}", "enqueue", ",".join(prefix), self.authority(), self.runner.now() + timedelta(minutes=self.runner.config.enqueue_minutes))
        return set()

    def authority(self) -> str:
        policy = self.runner.landing_policy()
        return f"{policy.authority_ref} {json.loads(policy.target)['revision']}" if policy else ""

    @property
    def landing_config(self) -> dict:
        return json.loads(self.runner.config.source.read_text())["landing"]

    @property
    def checks(self) -> list[str]:
        """The check registry in the order `landing.checks` names it, every built-in when it names none, with the `hold` check first whether it names it or not, so no enqueue path skips an open hold; re-read on every use."""
        return [*HOLD_CHECKS, *(name for name in self.landing_config.get("checks", merge_queue.CHECKS) if name not in HOLD_CHECKS)]

    def failing(self, stage: tuple[str, ...], graded: Graded) -> tuple[str, dict[str, merge_queue.State]] | None:
        """The first configured check of `stage` that does not pass every head, with each head's state; a refusing check posts its own blocker or ask when `graded.act`. The merge-mode check runs first, so a head's merge card is asked before the slower checks run, and its verdict still counts in its configured place."""
        early = {"merge-mode": self.check("merge-mode", graded)} if "merge-mode" in stage and "merge-mode" in self.checks else {}
        for name in self.checks:
            if name in stage and (states := {pr: state for pr, state in (early[name] if name in early else self.check(name, graded)).items() if state.state != merge_queue.PASS}):
                return name, states
        return None

    def check(self, name: str, graded: Graded) -> dict[str, merge_queue.State]:
        return {"hold": self.check_hold, "precondition": self.check_precondition, "rules-review": self.check_rules_review, "rulings": self.check_rulings, "fidelity": self.check_fidelity, "finder": self.check_finder, "old-app": self.check_old_app, "build-failed": self.check_build_failed, "merge-mode": self.check_merge_mode}[name](graded)

    def check_hold(self, graded: Graded) -> dict[str, merge_queue.State]:
        return {pr: merge_queue.State(merge_queue.FAIL, reason) for pr, reason in self.holding(list(graded.heads), graded.rows, graded.accepted_at).items()}

    def check_rules_review(self, graded: Graded) -> dict[str, merge_queue.State]:
        return {
            pr: merge_queue.State(merge_queue.FAIL, f"rules-review holds {sha[:10]}: its review is pending or carries a finding no `rules-override` waived")
            for pr, sha in graded.heads.items()
            if graded.rows.get(pr, {}).get("rules_blocked")
        }

    def read_reports(self, heads: dict[str, str]) -> None:
        """Run `check-pr` for every head of `heads` this pass has not read yet, `CHECK_PR_WORKERS` at a time: each one diffs on this machine."""
        with ThreadPoolExecutor(CHECK_PR_WORKERS) as pool:
            list(pool.map(lambda item: self.rulings_report(*item), heads.items()))

    def rulings_report(self, pr: str, sha: str, incident: bool = False) -> list[str]:
        """The cause lines `rulings.py check-pr` prints for `pr` at `sha`, run once per head and kept across passes, except an incident fix's, a timeout, a run that does not read, and a reserved-row cause an owner answer can clear, which run again once `CHECK_PR_REREAD` has passed, so a head waiting on an answer costs GitHub one read per interval rather than one per pass (root #146561). A run killed at CHECK_PR_SECONDS reads as one `CHECK-PR-TIMEOUT` warning that the head's rulings are unproven this pass. Any other run that exits with a code other than 0 or 1, whose exit code disagrees with its cause lines, or that prints a line whose first word names no cause, reads as no cause. The check fails open on what it cannot read (0afd2f6, 4236611) and posts nothing about it; a cause it did read stands beside a `BLOCKER` line."""
        key = (pr, sha[:7], incident)
        now = self.runner.now()
        if (kept := self.reported.get(key)) and (kept[0] is None or now - kept[0] < CHECK_PR_REREAD):
            return kept[1]
        if not incident and (causes := self.kept_reports().get(f"{pr}@{sha[:7]}")) is not None:
            self.reported[key] = (None, causes)
            return causes
        with self.checking:
            done = self.rulings_py("check-pr", "--pr", f"{self.repo}#{pr}", "--head", sha, *(("--incident",) if incident else ()))
        lines = [line.strip() for line in done.out.splitlines() if line.strip()]
        causes = [line for line in lines if cause(line) in CHECK_PR_CAUSES]
        unread = [line for line in lines if cause(line) == CHECK_PR_BLOCKER]
        if done.code == TIMED_OUT:
            causes = [f"{CHECK_PR_TIMEOUT} it ran past {CHECK_PR_SECONDS}s and was killed, so the rulings at {sha[:10]} are unproven this pass"]
        elif done.code not in (0, 1) or len(causes) + len(unread) < len(lines) or (done.code == 1) != bool(causes):
            causes = []
        final = not incident and done.code in (0, 1) and not unread and not any(cause(line) == RESERVED_TOUCH for line in causes)
        self.reported[key] = (None if final else now, causes)
        if final and self.kept_under:
            self.keep_reports()
        return causes

    def kept_reports(self) -> dict[str, list[str]]:
        """The final `check-pr` causes per `<pr>@<head>` an earlier runner wrote, read once per runner, while `rulings.py` and the modules it imports and the register's reserved rows read as they did then, since a new or changed reserved row can hold a head an earlier check passed; none when the register does not read, so a handoff or re-exec runs no kept head's check again."""
        with self.reporting:
            if self.kept is None:
                done = self.rulings_py("rows")
                self.kept_under = f"{rulings_digest()}:{reserved_fingerprint(json.loads(done.out)['rows'])}" if done.code == 0 else ""
                try:
                    kept = json.loads(self.reports_file.read_text())
                except FileNotFoundError:
                    kept = {}
                self.kept = kept.get("reports", {}) if self.kept_under and kept.get("under") == self.kept_under else {}
            return self.kept

    def keep_reports(self) -> None:
        with self.reporting:
            reports = {f"{pr}@{sha}": causes for (pr, sha, incident), (at, causes) in list(self.reported.items()) if at is None and not incident}
            staged = self.reports_file.with_name(f".{self.reports_file.name}.{threading.get_ident()}")
            staged.write_text(json.dumps({"under": self.kept_under, "reports": (self.kept or {}) | reports}))
            staged.replace(self.reports_file)

    def refuse_head(self, key: str, pr: str, graded: Graded, why: str, refused: Iterable[str], also: Iterable[str] = ()) -> None:
        """Post one blocker per `key` to the lane that owns `pr`, and the root when that lane has no live session or there is none, and to each lane of `also`."""
        if self.book.known(LANDING, key):
            return
        owner = self.owner(pr, graded.rows.get(pr, {}))
        self.post_blocker(key, ",".join(dict.fromkeys([*(self.reach(owner) if owner else [ROOT]), *also])), f"{self.dead_note(owner)}#{pr} {graded.heads[pr]}: {self.left(list(graded.heads), refused, graded.rows)}; {why}", pr)

    def warn_head(self, key: str, pr: str, graded: Graded, why: str, also: Iterable[str] = ()) -> None:
        """Post one `note` warning per `key` to the lane that owns `pr` and to each lane of `also`. The lanes of `also` act on it, so it skips a dead owner and never reaches the root; with no `also`, the root stands in for an owner lane with no live session, or for none (root #142931)."""
        if self.book.known(LANDING, key):
            return
        owner = self.owner(pr, graded.rows.get(pr, {}))
        if also:
            self.post_record("note", key, ",".join([*([owner] if owner and not self.lane_dead(owner) else []), *also]), f"#{pr} {graded.heads[pr]}: WARNING {why}", pr)
            return
        self.post_record("note", key, ",".join(self.reach(owner) if owner else [ROOT]), f"{self.dead_note(owner)}#{pr} {graded.heads[pr]}: WARNING {why}", pr)

    def check_rulings(self, graded: Graded) -> dict[str, merge_queue.State]:
        """No head fails here (4236611): each cause `rulings.py check-pr` prints for a head is a warning, one `note` record to its lane per head and set of causes. A head warns again only for a cause none of its earlier warnings named, naming that cause alone, and never once an `answer` replies to or resolves one of them (cci #182230). A reserved touch is merge mode's to settle: it sends the card whose tap authorizes it, and holds the head until then."""
        if graded.act:
            self.read_reports(graded.heads)
            for pr, sha in graded.heads.items():
                if fresh := self.unwarned(pr, sha, [line for line in self.rulings_report(pr, sha) if cause(line) != RESERVED_TOUCH]):
                    text = f"`rulings.py check-pr` warns at {sha[:10]}, and the head is not held for it: {'; '.join(fresh)}"
                    self.warn_head(f"rulings:{pr}:{sha[:7]}:{hashlib.sha1(text.encode()).hexdigest()[:8]}", pr, graded, text)
        return {}

    def unwarned(self, pr: str, sha: str, warnings: list[str]) -> list[str]:
        """The lines of `warnings` no warning `check_rulings` posted for `pr` at `sha` named yet, or none once an `answer` replies to or resolves one of those warnings."""
        posted = [action for action in self.book.actions(LANDING, kind="bus") if action.action_id.startswith(f"rulings:{pr}:{sha[:7]}:")]
        named = {line for action in posted for line in json.loads(action.target)["text"].partition("the head is not held for it: ")[2].split("; ")}
        fresh = [line for line in "; ".join(warnings).split("; ") if line and line not in named]
        seqs = {int(seq[1]) for action in posted if (seq := POSTED.search((action.response or {}).get("posted", "")))}
        if not fresh or seqs and any(seqs & {record.get("re"), record.get("resolves")} for record in cci.records(self.cci_tail, self.runner.config.drive, "--kind=answer", since=min(seqs))):
            return []
        return fresh

    def scanned_reviews(self) -> list[dict]:
        """The drive's `review` and `head` records, scanned once a pass, so a review whose text names the PR but whose refs do not still counts."""
        with self.scanning:
            if (reviews := self.reviews) is None:
                reviews = self.reviews = cci.records(self.cci_tail, self.runner.config.drive, "--kind=review", "--kind=head")
        return reviews

    def rescanned_reviews(self) -> list[dict]:
        """The pass's scanned reviews plus every `review` and `head` record posted since that scan, so a grade that lands while a long pass runs is read before the pass warns that it is missing."""
        with self.scanning:
            scanned = self.reviews if self.reviews is not None else cci.records(self.cci_tail, self.runner.config.drive, "--kind=review", "--kind=head")
            reviews = self.reviews = [*scanned, *cci.records(self.cci_tail.run, self.runner.config.drive, "--kind=review", "--kind=head", since=max((record["seq"] for record in scanned), default=0))]
        return reviews

    def finder_reviews(self, pr: str) -> list[dict]:
        """Every finder review about `pr`."""
        return finder_reviews_in(self.scanned_reviews(), self.runner.config.drive, self.repo, pr)

    def finder_records(self, pr: str) -> list[dict]:
        return finder_records(self.finder_reviews(pr))

    def finder(self, pr: str, sha: str) -> dict | None:
        """The newest finder review record about `pr` naming `sha`, from whichever lane ran the pass (reference/finder-brief.md)."""
        return newest_finder(self.finder_records(pr), sha)

    def finder_carried(self, pr: str, sha: str) -> bool:
        records = self.finder_records(pr)
        if self.carried_by_fidelity(pr, sha, lambda old: (newest := newest_finder(records, old)) is not None and newest["grade"] == FINDER_PASS):
            return True
        heads = dict.fromkeys(record["head"] for record in records if not names_head(record["head"], sha))
        oks = [head for head in heads if (newest := newest_finder(records, head)) and newest["grade"] == FINDER_PASS]
        if not oks or not (commits := self.own_commits(pr, sha)) or not (ours := self.patch_id(pr, sha, commits, escalate=True)):
            return False
        return any(self.patch_id(pr, head, commits, escalate=False) == ours for head in oks)

    def carried_by_fidelity(self, pr: str, sha: str, passes: Callable[[str], bool]) -> bool:
        """Whether a chain of fidelity carries runs from `sha` back to a head that `passes`, such as one whose newest finder verdict is ok (root #139077)."""
        drive = self.runner.config.drive
        withdrawn = {seq for lane in self.fidelity_lanes for record in cci.records(self.cci_tail, drive, "--kind=withdraw", "--lane", lane) for seq in closes(record)}
        carries = fidelity_carries([grade for grade in self.grades(pr) if grade["seq"] not in withdrawn])
        seen: set[str] = set()
        frontier = [sha]
        while frontier:
            head = frontier.pop()
            for new, old in carries:
                if not names_head(new, head) or old in seen:
                    continue
                if passes(old):
                    return True
                seen.add(old)
                frontier.append(old)
        return False

    def check_finder(self, graded: Graded) -> dict[str, merge_queue.State]:
        """Each head with no finder record, or whose newest names an open major that root has not settled, fails `FINDER-PENDING`, with one blocker to its lane and `finder-desk` per head and failing record (E4.1); a head with no finder record queues a finder pass when none waits or runs on it."""
        states, failed = {}, {}
        for pr, sha in graded.heads.items():
            if graded.act:
                for record in self.finder_reviews(pr):
                    if not (record["grade"] and record["head"]):
                        self.ask_finder_line(pr, record)
            if self.finder_clear(pr, sha):
                continue
            verdict = self.finder(pr, sha)
            why = f"{verdict['lane']} posted on cci #{verdict['seq']}: {verdict['text']}" if verdict else f"no `finder:` review record names this head; run the sol finder {finder.BRIEF} names and post its record"
            states[pr] = merge_queue.State(merge_queue.FAIL, f"FINDER-PENDING #{pr}: {why}")
            failed[pr] = f":{verdict['seq']}" if verdict else ""
        if graded.act:
            for pr, state in states.items():
                self.refuse_head(f"finder:{pr}:{graded.heads[pr]}{failed[pr]}", pr, graded, state.reason, states, also=[finder.LANE])
                if not failed[pr]:
                    self.runner.requeue_pending_finder(self.repo, pr, graded.heads[pr], self.owner(pr, graded.rows.get(pr, {})) or ROOT)
        return states

    def finder_clear(self, pr: str, sha: str) -> bool:
        """Whether the newest finder record naming `sha` passes or root settled it, or none names it and a passing one carries to it."""
        if verdict := self.finder(pr, sha):
            return verdict["grade"] == FINDER_PASS or self.root_answered(pr, sha, verdict)
        return self.finder_carried(pr, sha)

    def root_answered(self, pr: str, sha: str, verdict: dict) -> bool:
        """Whether root settled `verdict`, a failing finder record at `sha`: a root `go` naming `pr` at `sha` posted after it, a root `decide` or `decision` that resolves it, or a root `decision` replying to it directly or along a chain of `--re` replies, since root refutes an edge-case finding in one line (rule 2cc5a1f3, root #152572, #194669)."""
        if any(go["seq"] > verdict["seq"] for go in self.head_gos(pr, sha)):
            return True
        answered = {verdict["seq"]}
        for record in cci.records(self.cci_tail, self.runner.config.drive, since=verdict["seq"]):
            if record["lane"] == ROOT and record["kind"] in cci.ROOT_RULINGS and verdict["seq"] in closes(record):
                return True
            if record.get("re") not in answered:
                continue
            if record["lane"] == ROOT and record["kind"] == "decision":
                return True
            answered.add(record["seq"])
        return False

    def ask_finder_line(self, pr: str, record: dict) -> None:
        key = f"finder-line:{record['seq']}"
        if self.book.known(LANDING, key):
            return
        unread = "verdict" if not record["grade"] else "head"
        text = f"#{pr}: cci #{record['seq']} names the finder, but the runner reads no {unread} in it. Post `finder: ok <head>` or `finder: major <head> <what is open>`, naming the head the finder read."
        self.post_record("note", key, record["lane"], text, pr)

    def check_old_app(self, graded: Graded) -> dict[str, merge_queue.State]:
        """No head fails here (4236611, root #142277): each head whose newest `old-app:` grade from a fidelity lane, at that head or carried to it along a chain of fidelity carries, is not `unchanged` or `bug fix` gets one `OLD-APP-PENDING` warning per head and grade to its lane and the fidelity lanes (root #140437)."""
        if not graded.act:
            return {}
        for pr, sha in graded.heads.items():
            passes, newest = self.old_app(pr, sha, self.scanned_reviews())
            if not passes:
                passes, newest = self.old_app(pr, sha, self.rescanned_reviews())
            if passes:
                continue
            why = f"{newest['lane']} graded this head on cci #{newest['seq']}: {newest['text']}" if newest else f"no `old-app:` grade from {self.fidelity_names} names this head; grade it `old-app: unchanged {sha[:7]}`, `old-app: bug fix {sha[:7]}` or `old-app: CHANGES {sha[:7]} <what changes>`"
            text = f"OLD-APP-PENDING #{pr}, and the head is not held for it: {why}"
            self.warn_head(f"old-app:{pr}:{sha[:7]}:{hashlib.sha1(text.encode()).hexdigest()[:8]}", pr, graded, text, also=self.fidelity_to)
        return {}

    def old_app(self, pr: str, sha: str, reviews: list[dict]) -> tuple[bool, dict | None]:
        """Whether `pr` at `sha` stands at `unchanged` or `bug fix` in `reviews`, by its newest `old-app:` grade or, with none, along a chain of fidelity carries, and that newest grade."""
        grades = old_app_grades_in(reviews, self.runner.config.drive, self.fidelity_lanes, self.repo, pr)
        if newest := newest_finder(grades, sha):
            return newest["grade"] in OLD_APP_PASSES, newest
        return self.carried_by_fidelity(pr, sha, lambda old: (graded_old := newest_finder(grades, old)) is not None and graded_old["grade"] in OLD_APP_PASSES), None

    def check_build_failed(self, graded: Graded) -> dict[str, merge_queue.State]:
        """Each head that `build_failure` finds breaking a package's build fails `BUILD-FAILED`, with one blocker to its lane per head and package; each failure it finds inherited from the head's base gets one `note` per head and package instead."""
        failures = {pr: self.build_failure(pr, sha) for pr, sha in graded.heads.items()}
        states = {pr: merge_queue.State(merge_queue.FAIL, f"BUILD-FAILED #{pr}: {failure}") for pr, (failure, _) in failures.items() if failure}
        if graded.act:
            for pr, state in states.items():
                self.refuse_head(f"build-failed:{pr}:{graded.heads[pr]}:{state.reason.split()[2]}", pr, graded, state.reason, states)
            for pr, (_, inherited) in failures.items():
                for failure in inherited:
                    self.warn_head(f"build-inherited:{pr}:{graded.heads[pr]}:{failure.split()[0]}", pr, graded, f"INHERITED-BUILD-FAILURE #{pr}, and the head is not held for it: {failure}")
        return states

    def build_failure(self, pr: str, sha: str) -> tuple[str, list[str]]:
        """The first package a finished build at `sha` failed to build that `pr` changes, or that imports a package `pr` changes, with its first compile error, or empty; and each such failure that the same pipeline's finished build at the head's merge base with the trunk lists with the same error, which the head inherits and is not held for. Only a PR that changes a file under `landing.build_failures.tree` is read."""
        spec = self.landing_config["build_failures"]
        files, _ = self.changed_files(pr, sha)
        if not (touched := {os.path.dirname(name) for name in files or [] if name.startswith(spec["tree"])}):
            return "", []
        inherited = []
        for url, state, _ in self.ci_builds(sha) or []:
            if state not in ("success", "failure"):
                continue
            for package, error in self.build_failures(url).items():
                directory = os.path.dirname(error.split(":", 1)[0])
                if not (directory in touched or self.imports(sha, directory, frozenset(filter(None, (import_path(package, directory, other) for other in touched))))):
                    continue
                failure = f"{package} fails to build at {sha[:7]} on {url}: {error}"
                base, base_url = self.base_build(sha, url)
                if not base_url or self.build_failures(base_url).get(package) != error:
                    return failure, inherited
                inherited.append(f"{failure}, as it does at its base {base[:7]} on {base_url}")
        return "", inherited

    def base_build(self, sha: str, url: str) -> tuple[str, str]:
        """The merge base of `sha` with the trunk the landing checkout stands at, and the URL of the finished build there of the pipeline `url` ran; an empty URL when either does not read."""
        if not (base := self.merge_base(sha)):
            return "", ""
        pipeline = BUILD_URL.search(url)["pipeline"]
        return base, next((built for built, state, _ in self.ci_builds(base) or [] if state in ("success", "failure") and BUILD_URL.search(built)["pipeline"] == pipeline), "")

    def merge_base(self, sha: str) -> str:
        """The commit where `sha` left the trunk the landing checkout stands at, read once per head; empty, and read again next pass, when the checkout cannot read it."""
        if sha not in self.bases:
            if not self.local_commit(sha) or (done := self.shell.run(["git", "-C", str(self.checkout), "merge-base", "HEAD", sha])).code:
                return ""
            self.bases[sha] = done.out.strip()
        return self.bases[sha]

    def local_commit(self, sha: str) -> bool:
        """Whether the landing checkout holds `sha`, fetching it when it lacks it."""
        git = ["git", "-C", str(self.checkout)]
        return not (self.shell.run([*git, "cat-file", "-e", f"{sha}^{{commit}}"]).code and self.shell.run([*git, "fetch", "-q", "--no-tags", "origin", sha]).code)

    def build_failures(self, url: str) -> dict[str, str]:
        """Each package the `landing.build_failures.context` annotation of the finished build at `url` names, with its first compile error, read once per build; empty, and read again next pass, when the read fails."""
        if url not in self.annotated:
            spec = self.landing_config["build_failures"]
            if not (argv := release_build_argv(self.checkout, spec["annotations"], url)) or (done := self.shell.run(argv)).code:
                return {}
            self.annotated[url] = {package: error for annotation in json.loads(done.out) if annotation.get("context") == spec["context"] for package, error in build_failures_in(annotation.get("body_html") or "").items()}
        return self.annotated[url]

    def imports(self, sha: str, directory: str, paths: frozenset[str]) -> bool:
        """Whether a Go file directly in `directory` at `sha` imports one of `paths`, read in the landing checkout, which fetches `sha` when it lacks it; False when neither reads."""
        key = (sha, directory, paths)
        if key not in self.imported:
            if not paths or not self.local_commit(sha):
                return False
            self.imported[key] = self.shell.run(["git", "-C", str(self.checkout), "grep", "-q", "-F", *(arg for path in sorted(paths) for arg in ("-e", f'"{path}"')), sha, "--", f":(glob){directory}/*.go"]).code == 0
        return self.imported[key]

    def check_fidelity(self, graded: Graded) -> dict[str, merge_queue.State]:
        ungraded = self.ungraded(graded.heads)
        if graded.act and ungraded:
            self.refuse_ungraded(graded.heads, ungraded, graded.rows)
        return {pr: merge_queue.State(merge_queue.FAIL, grade.why) for pr, grade in ungraded.items()}

    def check_merge_mode(self, graded: Graded) -> dict[str, merge_queue.State]:
        if (asks := self.owner_asks(graded.heads)) is None:
            return {pr: merge_queue.State(merge_queue.PENDING, "merge mode high-risk reads the changed files first, and they did not read") for pr in graded.heads}
        waiting = self.unanswered(graded.heads, asks)
        if graded.act:
            self.record_waits(graded.heads, waiting)
        if graded.act and waiting:
            self.ask_owner(graded.heads, waiting, graded.rows)
        untapped, unread, exempt = self.untapped({pr: sha for pr, sha in graded.heads.items() if pr not in waiting})
        if graded.act:
            for pr, why in exempt.items():
                if not self.book.known(LANDING, key := f"port:{pr}:{graded.heads[pr]}"):
                    self.post_record("note", key, ",".join(dict.fromkeys([*self.routine_audience([pr], graded.rows).split(","), *self.board_lanes])), why, pr)
            for pr, why in untapped.items():
                self.refuse_head(f"tap-rule:{pr}:{graded.heads[pr]}", pr, graded, why, untapped, also=[ROOT, *self.board_lanes])
            for pr, why in unread.items():
                self.warn_head(f"tap-unread:{pr}:{graded.heads[pr]}:{why}", pr, graded, why, also=[ROOT, *self.runner.landing_lanes("desk_lane")])
        return {pr: merge_queue.State(merge_queue.PENDING, f"merge mode {self.merge_mode} waits for the owner's Merge answer at {graded.heads[pr][:7]}") for pr in waiting} | {pr: merge_queue.State(merge_queue.PENDING, why) for pr, why in (untapped | unread).items()}

    def untapped(self, heads: dict[str, str]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        """Each PR of `heads` whose changed files a glob of a `landing.tap_rules` row names, while that row is in the register in force, and no approval on the root's board names its head, with why it waits, since a root `go` never stands in for the owner's tap (rule/82317b0); each PR whose changed files, register, or board did not read, with what did not, which the runner leaves unqueued for the landing desk rather than guess a merge (4236611); and each such PR the owner's port and test-only rulings admit with no tap, with its `EXEMPT-ADMITTED` note (06288326)."""
        if not (rules := self.landing_config.get("tap_rules")):
            return {}, {}, {}
        touching, unread = {}, {}
        for pr, sha in heads.items():
            files, _ = self.changed_files(pr, sha)
            if files is None:
                unread[pr] = f"TAP-UNREAD #{pr}: its changed files did not read, so the runner leaves it unqueued for the landing desk"
            elif found := [rule for rule, globs in rules.items() if any(fnmatch(name, glob) for name in files for glob in globs)]:
                touching[pr] = found
        exempt, mismatched = {}, {}
        for pr in touching:
            exempt[pr], mismatched[pr] = self.exempt_admitted(pr, heads[pr])
        exempt = {pr: why for pr, why in exempt.items() if why}
        touching = {pr: found for pr, found in touching.items() if pr not in exempt}
        if not touching:
            return {}, unread, exempt
        done = self.rulings_py("rows")
        register = json.loads(done.out)["rows"] if done.code == 0 else None
        session = self.runner.root_session()
        try:
            verdicts = board_rows.verdicts_of(self.present(["cc-present", "outcomes", "--all", "--no-doc", "--session", session])) if session else None
        except board_rows.ReadFailed:
            verdicts = None
        waits = {}
        for pr, found in touching.items():
            if register is not None and not (found := [rule for rule in found if register.get(rule, {}).get("status") == "in-force"]):
                continue
            if register is None or verdicts is None:
                unread[pr] = f"TAP-UNREAD #{pr}: {'the register' if register is None else 'the root board'} did not read, so the runner leaves it unqueued for the landing desk"
                continue
            if self.tap_kept(pr, heads[pr]) or any(entry["verdict"] == "approved" and names_head(heads[pr], entry.get("head") or "") for by_row in verdicts.values() for row, entry in by_row.items() if board_rows.pr_of(row) == pr):
                continue
            waits[pr] = f"{mismatched[pr]}TAP-REQUIRED {','.join(found)}: it lands only on the owner's tap at this head, and a root go is no tap; no approval on the root's board names {heads[pr][:10]}"
        return waits, unread, exempt

    def tap_kept(self, pr: str, head: str) -> bool:
        """Whether this runner noted `TAP-KEPT` for `pr` at `head` and the standing fidelity grade there still says no new behavior since that tap: the owner's tap then carries without a board row or a go at the new head (root #192094, #194387, #195066)."""
        tapped = [parts[-2] for action in self.book.actions(LANDING, kind="bus") if (parts := action.action_id.split(":"))[0] == "tap-kept" and parts[2] == pr and names_head(parts[-1], head)]
        if not tapped:
            return False
        standing = self.fidelity(pr, head)
        return bool(standing.ok and standing.record) and any(keeps_tap(full_text(standing.record), sha) for sha in tapped)

    def exempt_admitted(self, pr: str, sha: str) -> tuple[str, str]:
        """The `EXEMPT-ADMITTED` note for `pr` at `sha` when its change is exempt and goes straight to Graphite's queue with no go and no board tap, and why its whole diff breaks the class its grade claims, read once per pass (root #194387, 06288326, owner Oct 28 8:45 PM). The head grades ok by `fidelity`'s rule and `exempt_class` finds one of `landing.exempt_grades` (default `pure port, no interaction`, `test-only or CI-only, no non-test code`, `test-only, no non-test code` and `CI-only, no non-test code`; the legacy `landing.port_phrase` replaces the first) in its grades. Both classes are checked against every file the PR changes against its base, never the grade's words alone (root #182391): a test-only or CI-only class fails on a file outside the test, CI, skill-script and docs set, and a port class on a file outside `PORT_PREFIXES` unless a `Rulings` comment clears its interaction; changed files that do not read admit nothing that pass."""
        if (pr, sha) not in self.exempts:
            self.exempts[(pr, sha)] = self.exemption(pr, sha)
        return self.exempts[(pr, sha)]

    def exemption(self, pr: str, sha: str) -> tuple[str, str]:
        if not (found := self.exempt_class(pr, sha, ())) or not self.fidelity(pr, sha).ok:
            return "", ""
        grade, said = found
        files, _ = self.changed_files(pr, sha)
        if files is None:
            return "", ""
        if TEST_ONLY_CLAIM.search(said):
            if offender := next((name for name in files if not is_test_or_ci_file(name)), ""):
                return "", f"TEST-ONLY-MISMATCH {offender} is neither a test nor CI configuration, so the test-only claim does not hold; "
        elif (offender := next((name for name in files if not name.startswith(PORT_PREFIXES)), "")) and not self.interaction_cleared(pr):
            return "", f"PORT-MISMATCH {offender} is outside {','.join(PORT_PREFIXES)} and no Rulings comment clears its interaction, so the port claim does not hold; "
        return f"EXEMPT-ADMITTED #{pr} at {sha[:10]}: fidelity grade cci #{grade['seq']} says \"{said}\", so it goes straight to the merge queue with no go or board tap (root #194387)", ""

    def interaction_cleared(self, pr: str) -> bool:
        """Whether a comment on `pr` under a `Rulings` heading has a line that names the change's interaction and cites the owner's answer as `# owner: <answer id>`."""
        done = self.shell.run(ghread.read("pr", "view", pr, "--repo", self.repo, "--json", "comments"))
        return done.code == 0 and any("interaction" in line.casefold() and OWNER_CITE.search(line) for comment in json.loads(done.out)["comments"] if finder.RULINGS_HEADING.search(comment["body"]) for line in comment["body"].splitlines())

    def exempt_class(self, pr: str, sha: str, seen: tuple[str, ...]) -> tuple[dict, str] | None:
        """The standing ok grade at `sha`, a desk's before the pipeline's, newest first, that says one of `landing.exempt_grades`, with that phrase; a patch-identical carry grade with none is read back along the heads it carries from."""
        phrases = self.landing_config.get("exempt_grades", [self.landing_config.get("port_phrase", PORT_PHRASE), *TEST_ONLY_PHRASES])
        oks = sorted((grade for grade in self.standing(self.grades(pr), sha) if grade["text"].startswith(FIDELITY_OK)), key=lambda record: (record["lane"] != fidelity.LANE, record["seq"]), reverse=True)
        if found := next(((grade, phrase) for grade in oks for phrase in phrases if phrase.casefold() in grade["text"].casefold()), None):
            return found
        earlier = [old for grade in oks if carry_grade(grade["text"]) and (carry := carry_of(grade["text"])) for old in carry[1] if old not in seen]
        return next((found for old in earlier if (found := self.exempt_class(pr, old, (*seen, sha)))), None)

    def record_waits(self, heads: dict[str, str], waiting: dict[str, list[str]]) -> None:
        """Keep, for `show`, what each graded head still waits on: its merge-ask key and the reserved rows only the owner's tap authorizes. A head that stopped waiting drops out, and the heads of other stacks keep theirs."""
        fresh = {pr: {"key": f"merge:{pr}:{heads[pr][:7]}", "rows": [key for key, _ in self.reserved_touches(pr, heads[pr])]} for pr in waiting}

        def merged(kept: dict) -> dict:
            return {pr: wait for pr, wait in kept.items() if pr not in heads} | fresh

        kept = self.book.load(LANDING).facts.get(MERGE_WAITS) or {}
        if merged(kept) != kept:
            self.book.edit(LANDING, lambda incident: incident.facts.update({MERGE_WAITS: merged(incident.facts.get(MERGE_WAITS) or {})}))

    def check_precondition(self, graded: Graded) -> dict[str, merge_queue.State]:
        """No head fails here (4236611): a head whose PR body or one of whose cci records holds its landing on something outside the PR gets one `note` warning to the root per head, quoting each sentence and where it was said. The head stays landable; the root posts a `hold` record when a sentence is a real dependency."""
        if graded.act:
            for pr, sha in graded.heads.items():
                key = f"precondition:{pr}:{sha}"
                if not self.book.known(LANDING, key) and (sentences := self.preconditions(pr, sha)):
                    said = "; ".join(f'{where} says "{sentence}"' for where, sentence in sentences)
                    self.post_record("note", key, self.routine_audience([pr], graded.rows), f"#{pr} {sha}: WARNING landing precondition: {said}. The head stays landable; post a `hold` record on #{pr} if this is a real dependency", pr)
        return {}

    def preconditions(self, pr: str, sha: str) -> list[tuple[str, str]]:
        """Every landing precondition sentence about `pr`, each with where it was said: the PR body, read once per head and read again next pass when GitHub refuses it, then each cci record about the PR that a lane other than the runner posted, read once per pass; a record a root record about the PR resolves, and a sentence naming one, is no longer a precondition (root #194669)."""
        if (pr, sha) not in self.body_preconditions and (body := self.body(pr, timedelta(0))) is not None:
            self.body_preconditions[(pr, sha)] = precondition_sentences(body)
        if pr not in self.said_preconditions:
            drive = self.runner.config.drive
            records = [record for record in cci.records(self.cci_tail, drive, "--pr", f"{self.repo}#{pr}") if record["drive"] == drive]
            resolved = {seq for record in records if record["lane"] == ROOT for seq in closes(record)}
            self.resolved_preconditions[pr] = resolved
            self.said_preconditions[pr] = [
                (f"cci #{record['seq']}", sentence)
                for record in records
                if record["lane"] != RUNNER and record["seq"] not in resolved and about_pr(record, self.repo, pr)
                for sentence in precondition_sentences(full_text(record))
                if not names_resolved(sentence, resolved)
            ]
        resolved = self.resolved_preconditions[pr]
        return [("the PR body", sentence) for sentence in self.body_preconditions.get((pr, sha), []) if not names_resolved(sentence, resolved)] + self.said_preconditions[pr]

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
        self.read_reports({pr: sha for pr, sha in heads.items() if pr not in asks})
        return asks | {pr: [] for pr, sha in heads.items() if pr not in asks and self.reserved_touches(pr, sha)}

    @property
    def lands_green(self) -> bool:
        """Whether this repository's PRs land once green with no merge card: the GitHub account the runner acts as owns it (de6c88c). A login that does not read is read again on the next use, and the repository follows its merge mode meanwhile."""
        if not self.viewer:
            done = self.shell.run(ghread.read("api", "user", "--jq", ".login"))
            self.viewer = done.out.strip() if done.code == 0 else ""
        return bool(self.viewer) and self.repo.split("/")[0].casefold() == self.viewer.casefold()

    def reserved_touches(self, pr: str, sha: str) -> list[tuple[str, str]]:
        """Each reserved row `pr` touches at `sha` that no answer authorizes yet, with the first sentence of its words: `check-pr`'s `RESERVED-NO-ANSWER` lines."""
        return [reserved_row(line) for line in self.rulings_report(pr, sha) if cause(line) == RESERVED_TOUCH]

    def risky_paths(self, pr: str, sha: str) -> list[str] | None:
        """The changed files of `pr` at `sha` that a `landing.high_risk_paths` glob names; None when GitHub refuses the read, which escalates once per head."""
        files, refused = self.changed_files(pr, sha)
        if files is None:
            if not self.read_only:
                self.runner.escalate(f"files:{pr}:{sha}", "FILES-READ-FAILED", f"#{pr}", f"GitHub refused the changed files of #{pr}: {refused}; merge mode high-risk enqueues nothing in its prefix until they read")
            return None
        globs = self.landing_config["high_risk_paths"]
        return [name for name in files if any(fnmatch(name, glob) for glob in globs)]

    def changed_files(self, pr: str, sha: str) -> tuple[list[str] | None, str]:
        """The changed files of `pr` at `sha`, read once per head, or None with the last line GitHub's refusal printed."""
        if (pr, sha) not in self.files:
            done = self.shell.run(ghread.read("api", "--paginate", f"repos/{self.repo}/pulls/{pr}/files", "--jq", ".[].filename"))
            if done.code:
                return None, ((done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"])[0]
            self.files[(pr, sha)] = done.out.splitlines()
        return self.files[(pr, sha)], ""

    def checks_paused(self) -> bool:
        """Whether `landing.checks_wait_for_go` names a cci sequence no root `go` record on this drive has come after yet: until one does, the pass runs no gate, so no `stack-enqueue --check`, MERGE-ASK, or enqueue, and logs one `GATE-PAUSED` per sequence; an approved, green incident fix still lands by plain squash."""
        if not (after := self.landing_config.get("checks_wait_for_go")):
            return False
        drive = self.runner.config.drive
        if any((record["kind"], record["lane"], record["drive"]) == ("go", ROOT, drive) for record in cci.records(self.cci_tail, drive, "--lane", ROOT, "--kind=go", since=after)):
            return False
        self.runner.escalate(f"gate-paused:{after}", "GATE-PAUSED", "landing", f"landing.checks_wait_for_go is {after}, so the gate runs no stack-enqueue --check until a root go record comes after cci #{after}; ledger refresh, reconcile, head reads and restack routes go on")
        return True

    def go_lane(self, lane: str) -> bool:
        return lane in (ROOT, RUNNER) or delegated(lane, self.landing_config.get("go_lanes", []))

    def go_records(self) -> list[dict]:
        """Every `go` record on this drive from the root or a `landing.go_lanes` lane or its numbered successor, read by lane when no lane is delegated."""
        drive = self.runner.config.drive
        lanes = () if self.landing_config.get("go_lanes") else ("--lane", ROOT, "--lane", RUNNER)
        return [record for record in cci.records(self.cci_tail, drive, *lanes, "--kind=go") if (record["drive"], record["kind"]) == (drive, "go") and self.go_lane(record["lane"])]

    def root_gos(self) -> list[dict]:
        """The `go` records of the root and of each `landing.go_lanes` lane or its numbered successor on this drive, read once per pass and again before each enqueue wave, without any that a later root record resolves, answers with a `withdraw`, or names by `#<seq>` or `--re` with the word "withdrawn", plus the gos a fidelity desk's `ok H carried from G` grade carries from G to H. Each carries `voided`, the PRs of its clause that a later root `withdraw` record, or one opening `WITHDRAWN`, names, which it no longer lets merge nor carries to a later head."""
        with self.going:
            if self.gos is None:
                drive = self.runner.config.drive
                gos = self.go_records()
                lanes = sorted({ROOT, *self.landing_config.get("go_lanes", []), *(go["lane"] for go in gos)})
                later = [record | {"text": full_text(record)} for record in cci.records(self.cci_tail, drive, *(flag for lane in lanes for flag in ("--lane", lane)), since=min(go["seq"] for go in gos)) if record["lane"] in lanes and record["drive"] == drive] if gos else []
                posted_seqs = {go["seq"] for go in gos}
                closed = {seq for record in later for seq in closes(record)} | {seq for record in later for seq in withdrawn(record) & posted_seqs if record["seq"] > seq}
                withdrawals = [record for record in later if record["kind"] == "withdraw" or WITHDRAWN_OPENING.match(record["text"])]
                posted = [go | {"voided": {pr for pr in go_heads(go) if any(record["seq"] > go["seq"] and about_pr(record, self.repo, pr) for record in withdrawals)}} for go in gos if go["seq"] not in closed]
                graders = [record for lane in self.fidelity_lanes for record in cci.records(self.cci_tail, drive, "--kind=review", "--kind=withdraw", "--lane", lane) if record["drive"] == drive and record["lane"] == lane] if posted else []
                unsaid = closed | {seq for record in graders for seq in closes(record)}
                lands = self.old_app_lands()
                gos = posted + carried_gos(posted, [record for record in graders if record["kind"] == "review" and record["seq"] not in unsaid], self.repo, lands)
                self.gos = gos = gos + self.patch_carried(gos, lands)
                return gos
            return self.gos

    def patch_carried(self, gos: list[dict], lands: Callable[[str, str], bool]) -> list[dict]:
        """The go each open row's head takes from a go of `gos` at an older head of its PR, newest first, whose own commits are patch-identical to the head's, once the head's fidelity grades stand ok, the human desks' over the pipeline's, and `lands` lets it, so a restack that moved the head needs no second go (root #178202)."""
        carried = []
        for pr, row in self.ledger_rows.items():
            head = row.get("head", "")
            if row.get("state", "open") != "open" or not head or gone_at(gos, pr, head):
                continue
            older = sorted((go for go in gos if pr in go_heads(go) and pr not in go.get("voided", ())), key=lambda go: go["seq"], reverse=True)
            if not older or not lands(pr, head) or not (grade := self.fidelity(pr, head)).ok:
                continue
            if not (commits := self.own_commits(pr, head)) or not (ours := self.patch_id(pr, head, commits, escalate=False)):
                continue
            if source := next((go for go in older if self.patch_id(pr, go_heads(go)[pr], commits, escalate=False) == ours), None):
                carried.append(carried_go(source, pr, head, grade.record["at"] if grade.record else source["at"]))
        return carried

    def old_app_lands(self) -> Callable[[str, str], bool]:
        """Whether a carried go reaches `pr` at `head`: always, unless the drive runs the old-app check, and then once a fidelity lane's newest `old-app:` grade naming that head, as `check_old_app` reads it, says `unchanged` or `bug fix` (root #143450, #143861)."""
        if "old-app" not in self.checks:
            return lambda pr, head: True
        graded: dict[str, list[dict]] = {}

        def lands(pr: str, head: str) -> bool:
            if pr not in graded:
                graded[pr] = old_app_grades_in(self.scanned_reviews(), self.runner.config.drive, self.fidelity_lanes, self.repo, pr)
            newest = newest_finder(graded[pr], head)
            return newest is not None and newest["grade"] in OLD_APP_PASSES

        return lands

    def head_gos(self, pr: str, head: str) -> list[dict]:
        return gone_at(self.root_gos(), pr, head)

    def merged_by_owner(self, heads: dict[str, str], prs: list[str]) -> set[str]:
        """The PRs of `prs` with an owner Merge answer at their head, a root `go` record on this drive that names the PR and its head by a short sha, or whose change at their head is exempt and needs none (root #194387)."""
        return {pr for pr in prs if self.free_to_land(self.root_gos(), pr, heads[pr])}

    def waiting_on_owner(self, heads: dict[str, str], prs: list[str]) -> list[str]:
        """The PRs of `prs` with no Merge answer at their head yet, or touching a reserved row no `authorizes:<key>` answer covers: only the owner's captured tap on the card quoting the row authorizes it, through `rulings.py authorize`, and a `go` never does (C224)."""
        merged = self.merged_by_owner(heads, prs)
        return [pr for pr in prs if pr not in merged or self.reserved_touches(pr, heads[pr])]

    def unanswered(self, heads: dict[str, str], asks: dict[str, list[str]]) -> dict[str, list[str]]:
        """The PRs of `asks` still waiting on the owner's Merge answer or on the authorization of a reserved row their head touches."""
        if not asks:
            return {}
        return {pr: asks[pr] for pr in self.waiting_on_owner(heads, list(asks))}

    def merge_card(self, pr: str, head: str, detail: str) -> str | None:
        """Ask the merge question for `pr` at `head` through `cc-present ask` in the drive root's session, so it lands on the root's board and in the root's Slack status thread like every other card, waiting on `merge:<pr>:<short sha>` with Merge recommended and Hold; asking again under the same id edits the card in place. The card's Slack ts, or None when it did not post, which logs one `MERGE-CARD-FAILED` per error and tries again next pass."""
        waits = f"merge:{pr}:{head[:7]}"
        posted = self.book.load(LANDING).facts.get(MERGE_CARDS, {}).get(waits)
        if posted and posted["question"] == detail:
            return posted["ts"]
        if not (session := self.runner.root_session()):
            self.runner.escalate("merge-card:unset", "MERGE-CARD-FAILED", "landing", f"no drive registry entry for {self.runner.config.drive} names a root session, so no merge card posts | run `drive.py start --drive <drive> --ledger <ledger>` from the root session; the next pass posts the cards")
            return None
        view = self.shell.run(ghread.read("pr", "view", pr, "--repo", self.repo, "--json", MERGE_CARD_FIELDS))
        done = view if view.code else self.present(["cc-present", "ask", "-", "--session", session], stdin=json.dumps(merge_card_block(self.repo, pr, waits, detail, json.loads(view.out))))
        if done.code or not (ts := slack_ts(done.out)):
            why = ((done.err or done.out).strip().splitlines() or [f"exit {done.code}"])[-1]
            cause = VOLATILE.sub("", why)
            self.runner.escalate(f"merge-card:{pr}:{head[:7]}:{hashlib.sha1(cause.encode()).hexdigest()[:8]}", "MERGE-CARD-FAILED", f"#{pr}", f"the merge card for #{pr} at {head[:7]} did not post: {why}; the next pass tries again")
            return None
        self.book.edit(LANDING, lambda incident: incident.facts.setdefault(MERGE_CARDS, {}).update({waits: {"ts": ts, "question": detail}}))
        return ts

    def present(self, argv: list[str], stdin: str | None = None) -> Done:
        try:
            return self.shell.run(argv, stdin=stdin)
        except OSError as error:
            return Done(127, "", f"cc-present did not start: {error}")

    @property
    def board_lanes(self) -> list[str]:
        return self.runner.landing_lanes("board_lane")

    def go_audience(self, prs: list[str], rows: dict[str, dict]) -> str:
        """Who hears that a go is stuck or unproven: each `landing.go_lanes` lane as its live successor, then the `routine_audience` (owner, Oct 29 2:06 PM)."""
        return ",".join(dict.fromkeys([*map(self.successor, self.landing_config.get("go_lanes", [])), *self.routine_audience(prs, rows).split(",")]))

    def routine_audience(self, prs: list[str], rows: dict[str, dict]) -> str:
        """Who hears a routine landing record: each `landing.desk_lane` and each PR's owning lane as its live successor, and the root only for a PR whose owning lane is dead or unnamed (root, Oct 29 2:10 PM)."""
        owners = [rows.get(pr, {}).get("lane", "") for pr in prs]
        return ",".join(dict.fromkeys([*map(self.successor, self.runner.landing_lanes("desk_lane")), *(ROOT if not owner or self.lane_dead(owner) else self.successor(owner) for owner in owners)]))

    def landing_audience(self, prs: list[str], rows: dict[str, dict], unstaffed: str) -> str:
        """Who hears a landing record in root's place: each `landing.desk_lane`, each `landing.board_lane`, and each PR's owning lane, as its live successor; `unstaffed` while no desk lane is named."""
        if not (desks := self.runner.landing_lanes("desk_lane")):
            return unstaffed
        owners = filter(None, (rows.get(pr, {}).get("lane") for pr in prs))
        return ",".join(dict.fromkeys(map(self.successor, [*desks, *self.board_lanes, *owners])))

    def carry_taps(self, rows: dict[str, dict]) -> None:
        """With `landing.merge_ask_surface` set to `board`, carry each approval the owner tapped on a `pr-groups` row of the root's board at an older head of an open PR, which no verdict names at its ledger head, to that head when a chain of `head` records reading `<new> (was <old>)` and fidelity carries calls each move patch-identical: `cc-present verdict carry` records it, so the row shows his tap and asks nothing, and one `TAP-CARRIED` note to root and each `landing.board_lane` lane names both heads. A tap whose new head a record names with no such chain, a fixed version, is never carried: a record saying the head adds new behavior draws one `TAP-NOT-CARRIED` note to the same lanes saying the board needs a new row, with the three facts its row opens with: his tap's Pacific time, the heads since it, and that record's sentence naming the new behavior (root #194315), and a fidelity ok grade of the head that names none draws one `TAP-KEPT` note saying his tap keeps over that review fix, so root posts the go naming the fix and the row lists it (rule 399b5fa, root #151306). Once root posts that go, the runner carries the tap to the fix itself the same way, citing the grade and the go. A head a root `go` or a patch-identical fidelity carry grade already names is otherwise settled and gets no such note, and a carry onto a merge block the board no longer holds has nothing to carry, so it logs no `TAP-CARRY-FAILED` (root #151003, #151839)."""
        if self.landing_config.get("merge_ask_surface", "slack") != "board":
            return
        if not (session := self.runner.root_session()):
            self.runner.escalate("tap-carry:unset", "TAP-CARRY-FAILED", "landing", f"no drive registry entry for {self.runner.config.drive} names a root session, so no board tap carries to a restacked head | run `drive.py start --drive <drive> --ledger <ledger>` from the root session; the next pass carries them")
            return
        try:
            verdicts = board_rows.verdicts_of(self.present(["cc-present", "outcomes", "--all", "--no-doc", "--session", session]))
        except board_rows.ReadFailed as failed:
            self.runner.escalate(f"tap-carry:unread:{hashlib.sha1(VOLATILE.sub('', str(failed)).encode()).hexdigest()[:8]}", "TAP-CARRY-FAILED", "landing", f"the root's board did not read ({failed}), so no tap carries to a restacked head this pass; the next pass reads it again")
            return
        heads = {pr: row["head"] for pr, row in rows.items() if row.get("state", "open") == "open" and row.get("head")}
        self.question_tap_claims(heads, verdicts, rows)
        entries = [(block, pr, entry) for block, by_row in verdicts.items() for pr, entry in by_row.items() if pr in heads and entry.get("head")]
        decided = {pr for _, pr, entry in entries if names_head(entry["head"], heads[pr])}
        to = ",".join([ROOT, *self.board_lanes])
        for _, pr, entry in entries:
            if entry["verdict"] == "approved" and entry.get("carriedFrom") and names_head(entry["head"], heads[pr]):
                self.go_carried_tap(pr, heads[pr], entry["carriedFrom"], rows, to)
        moved = [(block, pr, entry) for block, pr, entry in entries if entry["verdict"] == "approved" and pr not in decided]
        if not moved:
            return
        drive = self.runner.config.drive
        withdrawn = {seq for record in cci.records(self.cci_tail, drive, "--kind=withdraw") if record["kind"] == "withdraw" and record["drive"] == drive for seq in closes(record)}
        reviews = [record for record in self.scanned_reviews() if record["seq"] not in withdrawn]
        for block, pr, entry in moved:
            head, tapped = heads[pr], entry.get("carriedFrom") or entry["head"]
            graded = newest_naming(fidelity_grades_in(reviews, drive, self.fidelity_lanes, self.repo, pr), head)
            moves = head_moves(reviews, drive, self.fidelity_lanes, self.repo, pr)
            if chain := patch_chain(moves, head, entry["head"]):
                if self.carry_tap(session, block, pr, tapped, head, f"patch-identical restack, per {', '.join(f'cci #{seq}' for seq in chain)}", self.landing_audience([pr], rows, to)):
                    self.go_carried_tap(pr, head, tapped, rows, to)
                continue
            said = [record for record in reviews if record["kind"] == "head" and about_pr(record, self.repo, pr) and names_head(graded_head(record["text"]), head)]
            adds = [record for record in [*said, *([graded] if graded else [])] if ADDS_BEHAVIOR.search(full_text(record))]
            standing = self.fidelity(pr, head)
            graded_ok = bool(standing.ok and standing.record)
            says_kept = graded_ok and keeps_tap(full_text(standing.record), tapped)
            kept = not adds and says_kept and self.finder_clear(pr, head)
            if kept and not self.book.known(LANDING, key := f"tap-kept:{block}:{pr}:{tapped}:{head}"):
                self.post_record("note", key, to, f"TAP-KEPT #{pr}: the owner's tap at {tapped[:10]} on board block {block} keeps at {head[:10]}, a review fix with no new behavior per fidelity grade cci #{standing.record['seq']} (rule 399b5fa), so it goes straight to the merge queue with no go (root #194387), and the row lists the fix", pr)
            if kept and (gos := self.head_gos(pr, head)):
                self.carry_tap(session, block, pr, tapped, head, f"review fix with no new behavior, per fidelity grade cci #{standing.record['seq']} and root go cci #{gos[-1]['seq']} (rule 399b5fa)", self.landing_audience([pr], rows, to))
                continue
            if self.head_gos(pr, head) or graded and any(seq == graded["seq"] and identical for _, _, identical, seq in moves):
                continue
            if (adds or graded_ok and not says_kept) and not self.book.known(LANDING, key := f"tap-new-row:{block}:{pr}:{tapped}:{head}"):
                record = adds[-1] if adds else standing.record
                text = full_text(record)
                why = f"says that head adds new behavior: \"{board_rows.sentence_at(text, ADDS_BEHAVIOR.search(text).start()).strip()}\"" if adds else f"grades it ok without saying no new behavior since the tap at {tapped[:10]}"
                when = board_rows.tapped_at(entry)
                since = " -> ".join(sha[:10] for sha in heads_since(moves, head, entry["head"]))
                self.post_record("note", key, to, f"TAP-NOT-CARRIED #{pr}: the owner's tap at {tapped[:10]} ({when} Pacific) on board block {block} does not carry to {head[:10]}, since cci #{record['seq']} {why}. Heads since his tap: {since}. The board needs a new row for #{pr} at {head[:10]} opening 'You approved this at {when}. Since then: <what changed>. Shown again because: <the new behavior>.'", pr)

    def question_tap_claims(self, heads: dict[str, str], verdicts: dict[str, dict[str, dict]], rows: dict[str, dict]) -> None:
        """One `GO-TAP-MISSING` to root per root `go` and PR when the go says the owner tapped or approved an open PR at its ledger head, claims no port, test-only or CI-only exemption, and no approval on the root's board names that head or a head the go names, saying what the board holds for the PR instead: a go rests on a board verdict, never on a lane's line that says tapped (rule/82317b0, root #172682)."""
        for pr, head in heads.items():
            for go in self.head_gos(pr, head):
                text = full_text(go)
                if EXEMPT_GO.search(text) or not TAP_CLAIM.search(text):
                    continue
                held = [(block, entry) for block, by_row in verdicts.items() for row, entry in by_row.items() if board_rows.pr_of(row) == pr]
                named = [head, *SHORT_SHA.findall(text)]
                if any(entry["verdict"] == "approved" and any(names_head(sha, entry.get(at) or "") for sha in named for at in ("head", "carriedFrom")) for _, entry in held):
                    continue
                board = "; ".join(f"{entry['verdict']} at {(entry.get('head') or '')[:10]} on block {block}" for block, entry in held) or f"no verdict for #{pr}"
                self.runner.escalate(f"go-tap-missing:{go['seq']}:{pr}", "GO-TAP-MISSING", f"#{pr}", f"root go cci #{go['seq']} says the owner tapped #{pr} at {head[:10]}, but no approval on the root's board names {head[:10]} or a head the go names; the board holds {board}. Withdraw the go, or post it again citing the board verdict at this head or the carry to it", "", self.go_audience([pr], rows))

    def go_carried_tap(self, pr: str, head: str, tapped: str, rows: dict[str, dict], to: str) -> None:
        """Post the runner's own `go` for `pr` at `head`, where the owner's tap carried from `tapped`, unless a go names that head or this go was posted, so no root hop stands between a patch-identical restack and the queue (owner, Oct 29 2:06 PM); a post that failed is posted again next pass."""
        key = f"tap-go:{pr}:{head}"
        if self.head_gos(pr, head) or ((posted := self.book.known(LANDING, key)) and posted.status == "completed"):
            return
        self.post_record("go", key, self.landing_audience([pr], rows, to), f"go #{pr} at {head[:7]} TAP-CARRIED: the owner's tap at {tapped[:10]} carries to {head[:10]}, a patch-identical restack; the runner posts this go itself, so no root hop (owner, Oct 29 2:06 PM)", pr)

    def carry_tap(self, session: str, block: str, pr: str, tapped: str, head: str, why: str, to: str) -> bool:
        """Carry the owner's tap on `pr` from `tapped` to `head` on the board and post `TAP-CARRIED` once; whether the board took it."""
        done = self.present(["cc-present", "verdict", "carry", "--session", session, "--block", block, "--row", pr, "--head", head, "--note", why])
        if done.code:
            why = ((done.err or done.out).strip().splitlines() or [f"exit {done.code}"])[-1]
            if UNKNOWN_BLOCK.search(why):
                return False
            self.runner.escalate(f"tap-carry:{pr}:{head[:7]}:{hashlib.sha1(VOLATILE.sub('', why).encode()).hexdigest()[:8]}", "TAP-CARRY-FAILED", f"#{pr}", f"cc-present verdict carry refused the owner's tap on #{pr} from {tapped[:10]} to {head[:10]}: {why}; the next pass tries again")
            return False
        if not self.book.known(LANDING, key := f"tap-carried:{block}:{pr}:{head}"):
            self.post_record("note", key, to, f"TAP-CARRIED #{pr}: the owner's tap at {tapped[:10]} on board block {block} now stands at {head[:10]}, a {why}, recorded with cc-present verdict carry; the row asks no new tap", pr)
        return True

    def ask_owner(self, heads: dict[str, str], waiting: dict[str, list[str]], rows: dict[str, dict]) -> None:
        """Ask one merge card per waiting PR and head in the drive root's session, and log one `MERGE-ASK` naming it for the root, who answers the tap; nothing in the prefix is enqueued until a root `go` names each waiting head. A head touching a reserved row gets the row's key and first sentence in its card's question, whose tap is the authorization. A PR whose newest fidelity grade says `title: stale` still gets its card, and its `MERGE-ASK` and a blocker to its lane carry that line, since root posts no go until the lane's title fix (root #137006)."""
        mode = self.merge_mode
        asking = self.waiting_on_owner(heads, list(waiting))
        if not asking:
            return
        if self.landing_config.get("merge_ask_surface", "slack") == "board":
            asking = self.askable(heads, asking, rows)
        for pr in asking:
            paths, short = waiting[pr], heads[pr][:7]
            waits = f"merge:{pr}:{short}"
            reserved = self.reserved_touches(pr, heads[pr])
            rows_named = "".join(f":{key}" for key, _ in reserved)
            why = f"It changes {', '.join(paths[:3])}{' and more' if len(paths) > 3 else ''}." if paths else "Every PR waits for your answer." if mode == "every" and not self.lands_green else "It touches a reserved rule."
            touched = "".join(f" It touches your rule \"{sentence}\" Merging also allows it." for _, sentence in reserved)
            if self.landing_config.get("merge_ask_surface", "slack") == "board":
                where = "on the root's board"
            elif ts := self.merge_card(pr, heads[pr], f"{why}{touched}"):
                where = f"on merge card ts {ts}"
            else:
                continue
            stale = self.stale_title(pr)
            if stale:
                self.warn_stale_title(pr, heads[pr], rows.get(pr, {}), *stale)
            authorize = "".join(f"; only the tap authorizes {key}: `rulings.py authorize --drive {self.runner.config.drive} --row {key} --action <tap ts>`" for key, _ in reserved)
            self.runner.escalate(
                f"merge-ask:{pr}@{heads[pr]}{rows_named}" + (f":title-stale:{stale[0]['seq']}" if stale else ""),
                "MERGE-ASK",
                f"#{pr}",
                f"#{pr} {short} waits on the owner's tap on `{waits}` {where} (merge mode {mode})"
                + (f"; no go until the lane's title fix: cci #{stale[0]['seq']} says `{stale[1]}`" if stale else "")
                + authorize,
                f"merge-ask:{pr}",
                self.landing_audience([pr], rows, ""),
            )

    def askable(self, heads: dict[str, str], asking: list[str], rows: dict[str, dict]) -> list[str]:
        """The PRs of `asking` the owner can tap now: a fidelity ok stands at the head, the finder is clear there, and the root's board holds a row at that head or the runner noted `TAP-KEPT` there (root #191653). A graded head with no row is told to the board lanes once, so the row goes up in their next pass; an ungraded head already has its grade blocker."""
        graded = [pr for pr in asking if self.fidelity(pr, heads[pr]).ok and self.finder_clear(pr, heads[pr])]
        if not graded or (board := self.board_heads()) is None:
            return []
        askable = [pr for pr in graded if (pr, heads[pr][:7]) in board or self.tap_kept(pr, heads[pr])]
        for pr in sorted(set(graded) - set(askable), key=int):
            if not self.book.known(LANDING, key := f"board-row-missing:{pr}:{heads[pr]}"):
                self.post_record("note", key, ",".join(self.board_lanes) or ROOT, f"BOARD-ROW-MISSING #{pr} at {heads[pr][:10]}: graded ok and waiting on the owner's tap, but the root's board holds no row at this head; put its row up, and the runner asks for the tap once it is there (root #191653)", pr)
        return askable

    def board_heads(self) -> set[tuple[str, str]] | None:
        """Each PR and seven-character head the root board's rows name, or None when the board did not read."""
        if not (session := self.runner.root_session()):
            return None
        done = self.present(["cc-present", "outcomes", "--all", "--session", session])
        if done.code or (start := done.out.find("{")) < 0:
            return None
        return {(row.pr, row.head[:7]) for row in board_rows.rows_of(json.loads(done.out[start:]).get("doc") or {}) if row.head}

    def stale_title(self, pr: str) -> tuple[dict, str] | None:
        """The newest fidelity grade on `pr` with its `title: stale, <why>` line, when that grade judges the title against the head's commits and finds it stale."""
        newest = max(self.grades(pr), key=lambda record: record["seq"], default=None)
        return (newest, found[0]) if newest and (found := TITLE_STALE.search(full_text(newest))) else None

    def warn_stale_title(self, pr: str, head: str, row: dict, grade: dict, line: str) -> None:
        key = f"title-stale:{pr}@{head}:{grade['seq']}"
        if self.book.known(LANDING, key):
            return
        owner = self.owner(pr, row)
        self.post_blocker(key, ",".join(self.reach(owner) if owner else [ROOT]), f"{self.dead_note(owner)}#{pr} {head}: the newest fidelity grade, cci #{grade['seq']}, says `{line}`; root posts no go until your title fix (root #137006) posts as an APPLIED record", pr)

    def admit(self, heads: dict[str, str]) -> set[str]:
        """The PRs in `heads`, a PR to its head, that carry the drive's label on GitHub, each registered under its head branch so the next refresh rows it as ours. GitHub is read once per PR and head in a pass, and again in the next pass for a PR it did not admit; record text never admits a PR."""
        for pr, head in heads.items():
            if f"{pr}@{head}" not in self.labels_read:
                self.read_label(pr, head)
        return {pr for pr, head in heads.items() if self.labels_read.get(f"{pr}@{head}")}

    def read_label(self, pr: str, head: str) -> None:
        """Register `pr` when it carries the drive's label and remember the answer for its head; a failed read is asked again on the next call."""
        label = f"{DRIVE_LABEL}{self.runner.config.drive}"
        done = self.shell.run(ghread.read("pr", "view", pr, "--repo", self.repo, "--json", "labels,headRefName"))
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
        gos = self.root_gos()
        return any(not any(actions.parse_stamp(record["at"]) > at and goes(record, pr) for record in gos) for pr, at in dropped.items())

    def enqueue(self) -> None:
        """Run every accepted enqueue no stack worker has started, on the stack workers, the lowest priority value first: one parallel wave per priority, so a higher-priority prefix reaches the queue before a lower one, each waited for no longer than the pass's gate budget."""
        accepted = self.book.actions(LANDING, kind="enqueue", status="accepted")
        if not accepted:
            return
        rows = self.rows() or {}
        waves: dict[int, list[actions.Action]] = {}
        for action in accepted:
            waves.setdefault(min(self.priority(rows, pr) for pr in action.target.split(",")), []).append(action)
        for priority in sorted(waves):
            self.runner.checkpoint()
            self.reread()
            self.rescanned_reviews()
            self.await_workers([future for action in waves[priority] if (future := self.dispatch(action.action_id, set(action.target.split(",")), lambda action=action: self.enqueue_one(action)))])

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
        fresh = self.exact(tops)
        current = enqueue_key(fresh.would, fresh.verdicts) if fresh.would is not None else ""
        if current != key.split("#")[0]:
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "superseded", "out": fresh.out[-800:], "at": self.book.stamp()}))
            return
        if failed := self.failing(ENQUEUE_CHECKS, graded):
            name, states = failed
            text = f"the {name} check refuses " + "; ".join(f"#{pr}: {state.reason}" for pr, state in states.items())
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "blocked", "out": text, "at": self.book.stamp()}))
            return
        outcome, out, last = self.enqueue_chains(tops, enqueued_heads(key), self.whole())
        if outcome in BELIEVED_QUEUED:
            self.moved_in_queue(enqueued_heads(key))
        if outcome in BELIEVED_QUEUED and (late := self.holding(prefix, rows, accepted_at)):
            outcome, out = self.dequeue(key, prefix, rows, late)
        self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": outcome, "out": out, "drive": self.runner.config.drive, "at": self.book.stamp()}))
        if outcome in BELIEVED_QUEUED:
            self.post_record("report", f"queued:{key}", self.landing_audience(prefix, rows, self.queue_audience(prefix, rows)), f"QUEUED {self.queue_line(enqueued_heads(key))}: the landing runner put it in the merge queue at {pacific(self.runner.now())}", prefix[-1])
        if outcome in ("stranded", "failed", "unsettled"):
            self.runner.escalate(key, f"ENQUEUE-{outcome.upper()}", numbered(tops), last)

    def queue_line(self, heads: dict[str, str]) -> str:
        """Each PR the runner enqueued, at the head it enqueued, with the root `go` that names that head or `no go at this head`."""
        return ", ".join(f"#{pr} at {sha[:10]} ({self.go_named(pr, sha)})" for pr, sha in heads.items())

    def go_named(self, pr: str, head: str) -> str:
        if gos := self.head_gos(pr, head):
            return f"go #{gos[0]['seq']}"
        return "exempt, no go needed" if self.exempt_admitted(pr, head)[0] else "no go at this head"

    def queue_audience(self, prefix: list[str], rows: dict[str, dict]) -> str:
        return ",".join(dict.fromkeys([ROOT, *filter(None, (rows.get(pr, {}).get("lane") for pr in prefix))]))

    def moved_in_queue(self, heads: dict[str, str]) -> list[str]:
        """Once the queue accepts an enqueue, read each member's head again and post one note to the root and the fidelity lanes for each that moved since the gate graded it, naming the head the queue took, so that head is graded where it sits (root #144463). Returns the notes."""
        moved = []
        for pr, sha in heads.items():
            if (now := self.current_head(pr)) and not now.startswith(sha):
                text = f"QUEUED-AT-NEW-HEAD #{pr}: the gate graded {sha[:10]} and the queue took {now[:10]}, pushed while the enqueue ran; grade {now[:10]}"
                self.post_record("note", f"queued-head:{pr}:{now[:10]}", ",".join([ROOT, *self.fidelity_to]), text, pr)
                moved.append(text)
        return moved

    def enqueue_chains(self, tops: list[str], heads: dict[str, str], whole: bool) -> tuple[str, str, str]:
        """One adapter enqueue per top, bottom chain first, since Graphite keeps one linear chain per batch: a sibling chain after the first goes in as its own batch on the queued fork. The first call that does not enqueue ends the run and names its outcome."""
        outcome, outs, last = "noop", [], ""
        for top in tops:
            done, out, last = self.adapter.enqueue([top], whole, heads)
            outs.append(out)
            if done not in BELIEVED_QUEUED:
                return done, "".join(outs)[-1500:], last
            outcome = "enqueued" if "enqueued" in (outcome, done) else done
        return outcome, "".join(outs)[-1500:], last

    def holding(self, prefix: list[str], rows: dict[str, dict], accepted_at: datetime) -> dict[str, str]:
        """Why each PR of `prefix` may not be in the queue right now, read fresh: a holds-file line, an open cci `incident` record that names it, whose lane squash-merges the fix directly (ef905a6), the `hold` label on GitHub, an open cci `hold` record, a `hold` record posted after this enqueue was accepted, or a `hold` record newer than the PR's newest `ready` record. A label read GitHub refuses holds the PR too."""
        entries = holds.read(self.holds)
        held = holds.held(entries, rows)
        drive = self.runner.config.drive
        incidents = [record for record in cci.records(self.cci_tail.run, drive, "--kind=incident") if (record["kind"], record["drive"], record["status"]) == ("incident", drive, "open")]
        why = {}
        for pr in prefix:
            if pr in held:
                line = next(entry.line for entry in entries if pr in holds.held([entry], rows))
                why[pr] = f"the holds-file line `{line}`"
            elif named := [record for record in incidents if names_pr({**record, "text": full_text(record)}, pr)]:
                why[pr] = f"open INCIDENT #{named[-1]['seq']}, whose lane squash-merges it directly, never through the queue"
            elif reason := self.label_hold(pr) or self.record_hold(pr, accepted_at):
                why[pr] = reason
        return why

    def label_hold(self, pr: str) -> str:
        done = self.shell.run(ghread.read("pr", "view", pr, "--repo", self.repo, "--json", "labels"))
        if done.code != 0:
            return f"GitHub refused the label read: {(done.err or done.out).strip()[:120]}"
        return f"the `{HOLD_LABEL}` label on GitHub" if any(label["name"] == HOLD_LABEL for label in json.loads(done.out)["labels"]) else ""

    def record_hold(self, pr: str, accepted_at: datetime) -> str:
        drive = self.runner.config.drive

        def about_pr(kind: str) -> list[dict]:
            found = cci.records(self.cci_tail.run, drive, f"--kind={kind}", "--pr", f"{self.repo}#{pr}")
            return [record for record in found if (record["kind"], record["drive"]) == (kind, drive) and pr in dict(repo_prs(record, self.repo))]

        if not (posted := [record for record in about_pr("hold") if pr in cci.held_by(record)]):
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
            landed = ", ".join(f"#{pr} as {sha[:10]}" for pr, sha in receipt["landed"].items())
            text = f"LANDED {landed}: through the merge queue, which the landing runner entered at {pacific(actions.parse_stamp(action.started_at or action.accepted_at))} with {self.queue_line(enqueued_heads(action.action_id))}"
            self.post_record("landed", f"landed:{action.action_id}", self.landing_audience(prefix, rows, self.queue_audience(prefix, rows)), text, prefix[-1])
        for action in self.book.actions(LANDING, kind="enqueue", status="verified"):
            top = action.target.split(",")[-1]
            self.restack(top, rows)

    def announce_landings(self, rows: dict[str, dict]) -> None:
        """Post one `landed` record for each ledger row that reads landed with no landing record of its own: none from the runner, a verified enqueue or a squash, and no `landed` record on the bus naming it, as a lane posts for a merge it made. That is a PR queued by hand, landed in a batch the runner did not enqueue, or merged outside the queue, so its lane and the desks hear it the pass the ledger settles it. A PR of an admitted enqueue the runner has not verified waits for `verify`. A desk's first pass records every landed row as history, so a restart never announces an old landing; a post that fails runs again on the next pass."""
        read = self.book.load(LANDING).facts.get(LANDED_READ)
        landed = {pr for pr, row in rows.items() if row.get("state") == "landed"}
        if read is None:
            self.book.edit(LANDING, lambda incident: incident.facts.update({LANDED_READ: sorted(landed, key=int)}))
            return
        announced = {pr for action in self.book.actions(LANDING, kind="enqueue", status="verified") for pr in action.target.split(",")}
        pending = {pr for action in self.book.actions(LANDING, kind="enqueue", status="completed") if action.response["outcome"] in ADMITTED for pr in action.target.split(",")}
        if not (unread := sorted(landed - set(read) - pending, key=int)):
            return
        stamps = [rows[pr]["landed_at"] for pr in unread if rows[pr].get("landed_at")]
        posted = {cci.named_pr(record) for record in cci.records(self.cci_tail, self.runner.config.drive, "--kind=landed", since=min(stamps) if len(stamps) == len(unread) else 0)}
        settled = []
        for pr in unread:
            if pr in announced or pr in posted or self.squashed(pr):
                settled.append(pr)
                continue
            key = f"landed:{pr}"
            text = f"LANDED #{pr} as {rows[pr].get('landed_sha', '')[:10] or 'an unread squash'}: merged into {TRUNK} with no enqueue of the landing runner's own, so queued by hand or landed in a batch it did not enqueue"
            done = self.runner.cci_post("landed", text, self.landing_audience([pr], rows, self.queue_audience([pr], rows)), pr, key, pr=pr, record_id=key)
            if done.code == 0:
                settled.append(pr)
        if settled:
            self.book.edit(LANDING, lambda incident: incident.facts[LANDED_READ].extend(settled))

    def remove_landed_worktrees(self, rows: dict[str, dict]) -> None:
        """In a pass whose ledger reads a PR landed that no earlier pass tidied, read the checkout's worktrees once and remove each one whose branch is a landed PR's head branch and no open PR's through `ccx vcs worktree rm --path`, which refuses a dirty tree or one a live process works in; a refusal logs `WORKTREE-KEPT` once per tree, is never forced, and is tried again in the next pass that reads a new landing."""
        landed = {pr for pr, row in rows.items() if row.get("state") == "landed"}
        if landed <= set(self.book.load(LANDING).facts.get(LANDED_TIDIED, [])):
            return
        live = {row["branch"] for row in rows.values() if row.get("state", "open") == "open" and row.get("branch")}
        branches = {f"refs/heads/{rows[pr]['branch']}": pr for pr in landed if rows[pr].get("branch") and rows[pr]["branch"] not in live}
        listed = self.shell.run(["git", "-C", str(self.checkout), "worktree", "list", "--porcelain"])
        if listed.code:
            print(f"landed worktrees did not list: {(listed.err or listed.out).strip()[:300]}", file=sys.stderr)
            return
        for entry in listed.out.strip().split("\n\n"):
            tree = dict(line.partition(" ")[::2] for line in entry.splitlines())
            if (pr := branches.get(tree.get("branch", ""))) is None:
                continue
            self.runner.checkpoint()
            done = self.shell.run(["ccx", "vcs", "worktree", "rm", "--path", tree["worktree"]])
            if done.code:
                self.runner.escalate(f"worktree-kept:{tree['worktree']}", "WORKTREE-KEPT", f"#{pr}", f"{tree['worktree']} stays on landed #{pr}'s branch: {(done.err or done.out).strip()[:300]}")
        self.book.edit(LANDING, lambda incident: incident.facts.update({LANDED_TIDIED: sorted(landed, key=int)}))

    def is_incident(self, pr: str, row: dict) -> bool:
        """Whether a ledger row is an incident fix's: its lane is an `incident-` or `outage-` lane or a fix lane the runner launched on the incident clock, or an open incident in the executor's store names the PR as its repair, and no root go, hold, lift, decide or release naming it took it off the incident path."""
        lane = row.get("lane", "")
        store = actions.Store()
        tagged = INCIDENT_NAME.match(lane) or (lane_container(lane) in self.book.store.ids() and fix_launch(self.book.load(lane_container(lane)))) or any(str(incident.facts.get("repair_pr")) == pr for incident in map(store.load, store.ids()) if "adopted" in incident.facts and incident.status not in EXECUTOR_SETTLED and not incident.facts.get("not_ours"))
        return bool(tagged) and not self.incident_cleared(pr)

    def incident_cleared(self, pr: str) -> bool:
        """Whether a root go, hold, lift, decide or release naming `pr` says it is not an incident fix, takes it off the incident path, or owes no release, read once a pass; the runner then gates it as a normal row and logs `INCIDENT-TAG-CLEARED` once per record."""
        cleared = self.cleared
        if pr not in cleared:
            drive = self.runner.config.drive
            cleared[pr] = next((record["seq"] for record in cci.records(self.cci_tail, drive, "--lane", ROOT, "--pr", f"{self.repo}#{pr}") if record["lane"] == ROOT and record["drive"] == drive and record["kind"] in INCIDENT_CLEARING_KINDS and INCIDENT_CLEARED.search(full_text(record))), 0)
            if seq := cleared[pr]:
                self.runner.escalate(f"incident-tag-cleared:{pr}:{seq}", "INCIDENT-TAG-CLEARED", f"#{pr}", f"root record #{seq} took #{pr} off the incident path, so the runner never squashes it and gates it as a normal row, landing it only through the queue once its go and the owner's tap name its head")
        return bool(cleared[pr])

    def incident_rows(self, open_rows: dict[str, dict]) -> list[str]:
        return [pr for pr, row in open_rows.items() if row.get("ours") and row.get("base") == TRUNK and self.is_incident(pr, row)]

    def squash_incidents(self, rows: dict[str, dict], held: list[str]) -> None:
        """Merge each unheld incident fix PR on the trunk by plain squash in the pass its head is review-approved, every required check passes and `check-pr --incident` names none of its three ruling checks, once per head, warning the root of any landing precondition its words state, and post `landed`; the queue never takes it."""
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        covered = under_hold(open_rows, held)
        for pr in sorted(self.incident_rows(open_rows), key=int):
            if pr not in covered:
                self.squash(pr, open_rows[pr])

    def squash(self, pr: str, row: dict) -> None:
        done = self.shell.run(ghread.read("pr", "view", pr, "--repo", self.repo, "--json", "reviewDecision,headRefOid,state", max_age=ghread.FRESH))
        if done.code != 0:
            return
        view = json.loads(done.out)
        key = f"land:{pr}@{view['headRefOid']}"
        if (squashed := self.book.known(LANDING, key)) and squashed.status == "completed" and "posted" not in squashed.response:
            self.announce(pr, row, key, view["headRefOid"], squashed.response["sha"])
        if view["state"] != "OPEN" or view["reviewDecision"] != "APPROVED" or squashed:
            return
        checks = self.shell.run(ghread.read("pr", "checks", pr, "--repo", self.repo, "--required", "--json", "name,bucket", max_age=ghread.FRESH))
        try:
            required = json.loads(checks.out)
        except json.JSONDecodeError:
            return
        if not required or any(check["bucket"] != SQUASH_PASS for check in required):
            return
        if refusals := [line for line in self.rulings_report(pr, view["headRefOid"], incident=True) if cause(line) in INCIDENT_CHECKS]:
            text = f"#{pr} {view['headRefOid'][:10]}: RULINGS-REFUSED `rulings.py check-pr --incident` names these on the approved, green incident fix, and the runner does not merge it until they clear (45200f2): {'; '.join(refusals)}"
            refusal = f"rulings:{pr}:{view['headRefOid']}:{hashlib.sha1(text.encode()).hexdigest()[:8]}"
            if not self.book.known(LANDING, refusal):
                owner = self.owner(pr, row)
                self.post_blocker(refusal, ",".join(dict.fromkeys([*(self.reach(owner) if owner else []), ROOT])), self.dead_note(owner) + text, pr)
            return
        if pr in holds.held(holds.read(self.holds), {pr: row}):
            return
        self.check_precondition(Graded({pr: view["headRefOid"]}, {pr: row}, self.runner.now(), act=True))
        self.book.accept(LANDING, key, "squash", json.dumps({"pr": pr, "head": view["headRefOid"]}), f"#{pr} incident", None)
        self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now()))
        merged = self.shell.run(["gh", "pr", "merge", pr, "--repo", self.repo, "--squash", "--match-head-commit", view["headRefOid"]])
        if merged.code != 0:
            why = (merged.err or merged.out).strip()[:300] or f"exit {merged.code}"
            self.book.attempt(LANDING, lambda incident: incident.fail(key, why))
            self.runner.escalate(key, "SQUASH-FAILED", f"#{pr}", f"gh pr merge --squash refused the approved, green incident fix at {view['headRefOid'][:10]}: {why}")
            return
        read = self.shell.run(ghread.read("pr", "view", pr, "--repo", self.repo, "--json", "mergeCommit", max_age=ghread.FRESH))
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
            owed = [name for name, ok in (("fidelity ok", self.fidelity(pr, head).ok), ("finder ok", bool(verdict and verdict["grade"] == FINDER_PASS))) if not ok]
            if owed:
                self.runner.escalate(
                    f"ungraded-30:{pr}@{head}",
                    "UNGRADED-30",
                    f"#{pr}",
                    f"#{pr} merged as an incident fix at {head[:10]} {int(age.total_seconds() // 60)} minutes ago and has no {' and no '.join(owed)} at that head; both grades are owed within 30 minutes of the merge (d968e1d)",
                )
            self.book.attempt(LANDING, lambda incident, key=action.action_id: incident.action(key).response.update({UNGRADED_READ: self.book.stamp()}))

    def unqueued_gos(self, rows: dict[str, dict], held: list[str]) -> None:
        """Escalate `GO-UNQUEUED` once per head and stack for the lowest open, green, unheld PR of each stack a root `go` names at its ledger head that the pass's queue read still finds out of the queue five minutes after that go, and whose newest enqueue since the go does not show it QUEUED at that head (#185418), or after the fidelity grade that carried it to that head, naming the stack's bottom and the gone PRs above that wait on it (root #151213), whoever enqueued it, unless the adapter's own queue status reads it queued, such as Graphite's `WAITING_TO_MERGE` on a child whose base is still `graphite-base/<n>` (#194272), naming what the review bot's newest `ai-review` run on that head says when it holds the PR or waits for a restack push (root #154137), else the reserved rows below or at it that this runner's check-pr reads still wait on his tap, else the outcome of the newest enqueue at that head accepted since the go, so an attempt that ran before the go never reads as the reason. A head stacked on a parent no go names, one a hold covers, or one red on CI or conflicting with its base, is left to `refuse_ungone_parents`' one `BELOW-BLOCKED` or `BELOW-HELD` note per stack (root #142036, #142931, #145011)."""
        queued = {pr for prs in self.believed_queued(rows).values() for pr in prs}
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        now = self.runner.now()
        stuck: dict[str, list[tuple[int, str, str, list[dict], datetime, str]]] = {}
        for pr, row in rows.items():
            head = row.get("head", "")
            if row.get("state", "open") != "open" or row.get("test_state") != "success" or pr in queued or pr in held or not head or (stack := downstack(open_rows, pr)) - {pr} & self.below_blocking:
                continue
            if not (gos := self.head_gos(pr, head)) or now - (at := min(max(actions.parse_stamp(go["at"]), actions.parse_stamp(go.get("graded_at") or go["at"])) for go in gos)) < GO_TO_QUEUE:
                continue
            if (queue := self.queue_states()) is None or queue.get(pr) not in OUT_OF_QUEUE or self.refused_at_head(stack, open_rows):
                continue
            tried = sorted((action for action in self.book.actions(LANDING, kind="enqueue") if head.startswith(enqueued_heads(action.action_id).get(pr) or "-") and actions.parse_stamp(action.accepted_at) >= at), key=lambda action: action.accepted_at)
            bot = self.review_bot(pr, head, row)
            evicted = (self.pr_statuses() or {}).get(pr, {}) if queue.get(pr) == "evicted" else {}
            if not tried or (tried[-1].response or {}).get("outcome") not in GO_UNQUEUED_OUTCOMES and not evicted:
                continue
            if not evicted and any(match["pr"] == pr and match["verdict"] == QUEUED_VERDICT and head.startswith(match["sha"]) for match in merge_queue.VERDICT_LINE.finditer(tried[-1].response.get("out", ""))):
                continue
            reserved = [f"{reserved_row(line)[0]} on #{below}" for below in sorted(downstack(open_rows, pr), key=int) if (kept := self.reported.get((below, open_rows[below].get("head", "")[:7], False))) for line in kept[1] if cause(line) == RESERVED_TOUCH]
            if bot:
                why = bot
            elif reserved:
                why = f"it waits on his tap authorizing {', '.join(reserved)}, as MERGE-ASK says; a go never authorizes a reserved row (C224)"
            elif evicted:
                why = f"the queue let it go at {evicted.get('evicted_at') or 'an unstamped time'} after its newest enqueue since the go ({tried[-1].response['outcome']}): {evicted.get('evicted') or 'no reason given'}"
            else:
                why = f"its newest enqueue since the go ended {tried[-1].response['outcome']}: {' '.join(tried[-1].response.get('out', '').split())[-300:]}"
            bottom = min(stack, key=lambda member: len(downstack(open_rows, member)))
            stuck.setdefault(bottom, []).append((len(stack), pr, head, gos, at, why))
        unsure = sorted({member[1] for group in stuck.values() for member in group if (self.queue_states() or {}).get(member[1]) != "evicted"}, key=int)
        graphite = (self.adapter.queued(unsure) if unsure else None) or {}
        for bottom, group in stuck.items():
            if not (group := [member for member in group if not graphite.get(member[1])]):
                continue
            _, pr, head, gos, at, why = min(group)
            owner = self.owner(pr, open_rows[pr]) or ROOT
            above = [f"#{other}" for _, other, *_ in sorted(group) if other != pr]
            waits = f"; {', '.join(above)} above it in the stack from #{bottom} wait on it too" if above else ""
            key, subject = f"go-unqueued:{pr}@{head}", f"go-unqueued:{pr}"
            to = self.go_audience([pr], rows)
            self.runner.escalate(key, "GO-UNQUEUED", f"#{pr}", f"#{pr} is green at {head[:10]} and root go #{gos[0]['seq']} at {pacific(actions.parse_stamp(gos[0]['at']))} named it, yet it is not in the queue after {whole_minutes(at, now)} minutes; {why}{waits}; owner lane {owner}", subject, to)

    def keep_overflowing(self) -> None:
        """Run `overflow` every `OVERFLOW_EVERY` from a daemon thread, on a Landing of its own that reads cci afresh, so no pass, however slow, holds a landable stack out of the queue (owner 9524276). An overflow that raises prints its traceback and the next one runs."""
        mine = self.overflower = Landing(self.runner, self.runner.config.landing, cci.Tail(self.runner.cci_read))

        def loop() -> None:
            while True:
                started = time.monotonic()
                try:
                    mine.overflow()
                except Exception:
                    traceback.print_exc()
                time.sleep(max(0.0, OVERFLOW_EVERY.total_seconds() - (time.monotonic() - started)))

        self.overflowing = threading.Thread(target=loop, name="landing-overflow", daemon=True)
        self.overflowing.start()

    def post_state(self, took: float) -> None:
        """Post the pass's one `state` record to root on topic `landing-state`, which cci delivers newest-only, so root never asks what the runner is doing (owner 9524276): the open rows the queue holds, the green ones a root go names at their heads that are out of it and neither held nor refused, with the oldest stack step in flight, the held, the refused at their heads, the stack workers running, the overflow's enqueues this hour, and the pass's seconds. It logs `OVERFLOW-STALLED` once an hour while the overflow thread has finished no run in `OVERFLOW_STALLED`."""
        open_rows = {pr: row for pr, row in self.ledger_rows.items() if row.get("state", "open") == "open"}
        statuses, gos, held = self.statuses or {}, self.gos or [], set(self.held_now)
        landing = self.book.load(LANDING)
        refused = set(refused_members(landing.of_kind("bus"), set(open_rows), open_rows)) if open_rows else set()
        queued = {pr for pr in open_rows if statuses.get(pr, {}).get("queue") == QUEUED_STATE}
        waiting = [pr for pr, row in open_rows.items() if pr not in queued | held | refused and row.get("test_state") == "success" and row.get("head") and gone_at(gos, pr, row["head"])]
        now = self.runner.now()
        with self.dispatching:
            oldest = min(self.steps.values(), key=lambda entry: entry["since"], default=None)
            running = sum(1 for _, future in self.inflight.values() if not future.done())
        hour = now - timedelta(hours=1)
        overflowed = sum(1 for action in landing.of_kind("enqueue") if f"#{OVERFLOW}-" in action.action_id and actions.parse_stamp(action.accepted_at) >= hour)
        step = f" (oldest #{oldest['root']} {oldest['step']} {whole_minutes(actions.parse_stamp(oldest['since']), now)}m)" if oldest else ""
        text = f"LANDING queued {len(queued)}, waiting {len(waiting)}{step}, held {len(held)}, refused {len(refused)}, in flight {running}, overflowed {overflowed} this hour; pass {took:.0f}s"
        self.runner.cci_post("state", text, ROOT, "landing-state", f"landing-state:{self.book.stamp()}")
        if self.overflower and (quiet := time.monotonic() - self.overflower.overflowed_at) > OVERFLOW_STALLED.total_seconds():
            alive = self.overflowing is not None and self.overflowing.is_alive()
            self.runner.escalate(f"overflow-stalled:{now:%Y%m%d%H}", "OVERFLOW-STALLED", "landing", f"the overflow thread has finished no run in {int(quiet // 60)} minutes{'' if alive else ' and is dead'}, so a stack the passes leave out waits on them alone; `desk-runner.py handoff` restarts the runner")

    def overflow(self) -> None:
        """Enqueue straight into the merge queue, through the adapter and with none of the runner's own checks, each stack that has stayed landable for `OVERFLOW_AFTER` since an overflow first found it so: every unqueued PR of it green at its ledger head, named at that head by a root go, under no hold, pull, or incident fix, with no enqueue of it running and nothing the runner decided against it at that head, which is a refusal `last_refusal` finds since the go, an enqueue outcome outside `OVERFLOW_RETRIES` other than a `blocked` one five minutes old, a drop still waiting on a go, or a reserved row no answer authorizes (C224). A stack is due once any PR of it is, so a restack that restarted an upper PR's clock never sends the bottom in alone for Graphite to hold the rest on. Each goes in as its stack's top, recorded as an enqueue and posted as a QUEUED report that names the overflow."""
        self.cci_tail.remember()
        try:
            self.statuses, self.queues_read, self.gos, self.cleared = None, False, None, {}
            if self.quota_low() or (rows := self.rows()) is None or (held := self.held(rows)) is None or self.checks_paused() or (queue := self.queue_states()) is None:
                return
            open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
            landable = self.overflow_landable(open_rows, held, queue)

            def seen(incident: actions.Incident) -> dict[str, list[str]]:
                kept = incident.facts.get(OVERFLOW_SEEN, {})
                incident.facts[OVERFLOW_SEEN] = {pr: kept[pr] if kept.get(pr, [""])[0] == open_rows[pr]["head"] else [open_rows[pr]["head"], self.book.stamp()] for pr in landable}
                return incident.facts[OVERFLOW_SEEN]

            since = self.book.edit(LANDING, seen)
            now = self.runner.now()
            due = {pr for pr in landable if any(now - actions.parse_stamp(since[member][1]) >= OVERFLOW_AFTER for member in downstack(open_rows, pr) & landable)}
            with ThreadPoolExecutor(max(1, len(due))) as pool:
                touched = dict(zip(due, pool.map(lambda pr: bool(self.reserved_touches(pr, open_rows[pr]["head"])), due)))
            due = {pr for pr in due if not any(touched.get(member, False) for member in downstack(open_rows, pr) & due)}
            tops = [pr for pr in due if not any(pr in downstack(open_rows, other) for other in due - {pr})]
            with ThreadPoolExecutor(max(1, len(tops))) as pool:
                list(pool.map(lambda top: self.overflow_one(top, open_rows, queue, min(actions.parse_stamp(since[member][1]) for member in downstack(open_rows, top) & landable)), tops))
        finally:
            self.cci_tail.stop()
            self.overflowed_at = time.monotonic()

    def overflow_landable(self, open_rows: dict[str, dict], held: list[str], queue: dict[str, str]) -> set[str]:
        covered = under_hold(open_rows, [*held, *self.incident_rows(open_rows)])
        facts = self.book.load(LANDING).facts
        pulled = {order["pr"] for order in facts.get(DEQUEUE_ORDERS, {}).values()} | {pr for pr, head in facts.get(PULLED_HEADS, {}).items() if open_rows.get(pr, {}).get("head", "").startswith(head)}
        gos = self.root_gos()
        ready = {
            pr
            for pr, row in open_rows.items()
            if row.get("ours") and row.get("head") and row.get("test_state") == "success" and pr not in covered and pr not in pulled and queue.get(pr) in OUT_OF_QUEUE and gone_at(gos, pr, row["head"])
        }
        landable = {pr for pr in ready if all(below in ready or queue.get(below) in STATUS_SETTLED for below in downstack(open_rows, pr) - {pr})}
        enqueues = self.book.actions(LANDING, kind="enqueue")
        return {pr for pr in landable if not self.overflow_decided(downstack(open_rows, pr) & ready, open_rows, enqueues, gos)}

    def overflow_decided(self, members: set[str], open_rows: dict[str, dict], enqueues: list[actions.Action], gos: list[dict]) -> bool:
        heads = {pr: open_rows[pr]["head"] for pr in members}
        for action in enqueues:
            named = {pr: sha for pr, sha in enqueued_heads(action.action_id).items() if pr in heads and sha and heads[pr].startswith(sha)}
            if not named:
                continue
            response = action.response or {}
            if action.status == "completed" and response.get("outcome") == "blocked" and self.runner.now() - actions.parse_stamp(response["at"]) >= OVERFLOW_AFTER:
                continue
            if action.status == "started" or (action.status == "completed" and response.get("outcome") not in OVERFLOW_RETRIES):
                return True
            if any(response.get("ejected", {}).get(pr) == sha for pr, sha in named.items()):
                return True
        if self.awaiting_go(enqueues, heads) or self.refused_at_head(members, open_rows):
            return True
        since = min(max(actions.parse_stamp(go["at"]), actions.parse_stamp(go.get("graded_at") or go["at"])) for pr in members for go in gone_at(gos, pr, heads[pr]))
        return self.last_refusal(members, open_rows, since) is not None

    def overflow_one(self, top: str, open_rows: dict[str, dict], queue: dict[str, str], since: datetime) -> None:
        prefix = sorted((pr for pr in downstack(open_rows, top) if queue.get(pr) not in STATUS_SETTLED), key=lambda pr: len(downstack(open_rows, pr)))
        heads = {pr: open_rows[pr]["head"] for pr in prefix}
        base = "enqueue:" + ",".join(f"{pr}@{heads[pr][:10]}" for pr in prefix)
        key = f"{base}#{OVERFLOW}-{len([action for action in self.book.actions(LANDING, kind='enqueue') if action.action_id.split('#')[0] == base]) + 1}"
        deadline = self.runner.now() + timedelta(minutes=self.runner.config.enqueue_minutes)
        self.book.accept(LANDING, key, "enqueue", ",".join(prefix), OVERFLOW, deadline)
        if not self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now(), deadline=deadline)):
            return
        outcome, out, last = self.adapter.enqueue([top], self.whole(), heads)
        self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": outcome, "out": out, "drive": self.runner.config.drive, "at": self.book.stamp(), OVERFLOW: True}))
        rows = self.ledger_rows
        if outcome in BELIEVED_QUEUED:
            waited = whole_minutes(since, self.runner.now())
            self.post_record("report", f"queued:{key}", self.landing_audience(prefix, rows, self.queue_audience(prefix, rows)), f"QUEUED {self.queue_line(enqueued_heads(key))}: the landing runner's overflow put it straight in the merge queue at {pacific(self.runner.now())}, {waited} minutes after it found it landable and unqueued, without its own checks (owner 9524276)", top)
        elif outcome in ("stranded", "failed", "unsettled"):
            self.runner.escalate(key, f"ENQUEUE-{outcome.upper()}", numbered([top]), f"the overflow enqueue of {numbered(prefix)} ended {outcome}: {last}")

    def refused_at_head(self, members: set[str], open_rows: dict[str, dict]) -> bool:
        """Whether this runner posted a `blocker` for a PR of `members` at its current head: that refusal is the record, so no `GO-UNQUEUED` repeats it (root, Oct 29 10:35 AM)."""
        return next(refused_members(self.book.actions(LANDING, kind="bus"), members, open_rows), None) is not None

    def last_refusal(self, members: set[str], open_rows: dict[str, dict], since: datetime) -> tuple[str, str] | None:
        """The newest refusal any PR of `members` stands under, with the lane that owns that PR: a blocker this runner routed for its current head or base, a refusal it escalated since `since`, or a `blocker` record a `landing.desk_lane` lane posted naming it since then (root, Oct 29 2:37 AM)."""
        found: list[tuple[datetime, str, str]] = []
        routes = [action for action in self.book.actions(LANDING, kind="bus")] + [action for name in self.book.containers(LANE_PREFIX) for action in self.book.actions(name, kind="relay")]
        for member in members:
            row = open_rows[member]
            current = (f"blocker:{member}:{row.get('head', '')[:10]}", f"bot-held:{member}:{row.get('head', '')[:10]}", f"conflict:{member}:{row.get('base', '')}")
            found += [(actions.parse_stamp(action.accepted_at), member, f"the blocker routed at {pacific(actions.parse_stamp(action.accepted_at))}: {json.loads(action.target)['text']}") for action in routes if action.action_id in current]
            found += [
                (when, member, f"{action.target.split()[0]} at {pacific(when)}: {action.target}")
                for action in self.book.actions(RUNNER, kind="escalation")
                if action.target.split()[0] in LANDING_REFUSALS and re.search(rf"#{member}\b", action.target) and (when := actions.parse_stamp(action.accepted_at)) >= since
            ]
        if desks := self.runner.landing_lanes("desk_lane"):
            for record in cci.records(self.cci_tail, self.runner.config.drive, "--kind=blocker", *(arg for desk in desks for arg in ("--lane", desk)), since=actions.stamp(since)):
                if record["drive"] != self.runner.config.drive or record["kind"] != "blocker" or record["lane"] not in desks or record.get("status") == "closed":
                    continue
                found += [(actions.parse_stamp(record["at"]), member, f"{record['lane']}'s blocker #{record['seq']} at {pacific(actions.parse_stamp(record['at']))}: {full_text(record)}") for member in members if re.search(rf"#{member}\b", full_text(record))]
        if not found:
            return None
        _, member, said = max(found)
        return f"its newest refusal is {' '.join(said.split())[:400]}", self.owner(member, open_rows[member]) or ROOT

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
            incident = (self.squashed(pr) or self.is_incident(pr, row)) and not self.incident_cleared(pr)
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
        """Whether this runner merged the PR by plain squash, so its landing owes a release however the PR's incident reads now, unless a root record clears its incident tag."""
        return any(action.action_id.startswith(f"land:{pr}@") and action.status == "completed" for action in self.book.actions(LANDING, kind="squash"))

    def accept_release(self, pr: str, row: dict, incident: bool, before_start: bool) -> bool:
        """Owe a landing's release, and say whether its body was read: a merge-fast landing owes the components its `Release-With:` lines name, and none when it names none; an incident fix's landing owes its release either way. A landing from before this runner started owes nothing once `<landing.release_cli> live --json` shows its squash or a later commit running, and closes without a record."""
        if (body := self.body(pr, timedelta(0))) is None:
            return False
        components = list(dict.fromkeys(name for line in RELEASE_WITH.findall(body) for name in line.split()))
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
        """Post one record per step since the landing while no one has started or acknowledged its release: the `release` record first, then a `blocker` with the minutes elapsed. A step is `deadlines.incident_release_minutes` while an incident clock runs and `deadlines.release_start_minutes` otherwise. An incident fix's landing settles only on a `release` record with a build, a `fix-live` record, or a root `--resolves` on one of the runner's records about it, and a root record that holds it after it landed is answered once. A root record clearing its incident tag settles it, before or after the landing."""
        landed, key, posted = json.loads(action.target), action.action_id, (action.response or {}).get("posted", {})
        incident = landed.get("incident", False)
        if incident and self.incident_cleared(landed["pr"]):
            self.book.attempt(LANDING, lambda incident: incident.verify(key, {"cci": self.cleared[landed["pr"]], "cleared": True, "at": self.book.stamp()}))
            return
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
        for record in cci.records(self.cci_tail, drive, "--lane", ROOT, "--kind=hold"):
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
        """The drive's first cci `release` or `fix-live` record after the landing, from any lane but the runner's own, that names one of the landing's components as a target or its PR. An incident fix's landing needs a `fix-live` record, a root `release` record naming its PR, which says the release that carries it (root #143767), or a build on the record and a target: one of its components, or any when it names none."""
        drive, since, names = self.runner.config.drive, actions.parse_stamp(landed["landed_at"]), (int(landed["pr"]), f"{self.repo}#{landed['pr']}")
        releases = cci.records(self.cci_tail, drive, *(f"--kind={kind}" for kind in SETTLING_KINDS))

        def names_landing(record: dict) -> bool:
            refs = record["refs"]
            targets, named = set(refs.get("targets", [])), any(pr in names for pr in refs.get("prs", []))
            if landed.get("incident") and record["kind"] == "release":
                return (record["lane"] == ROOT and names_pr(record, landed["pr"])) or bool(refs.get("builds")) and bool(targets & set(landed["components"]) if landed["components"] else targets)
            return bool(targets & set(landed["components"])) or named

        return next(
            (
                record
                for record in releases
                if record["kind"] in SETTLING_KINDS
                and record["drive"] == drive
                and record["lane"] != RUNNER
                and actions.parse_stamp(record["at"]) >= since
                and names_landing(record)
            ),
            None,
        )

    def acknowledged(self, seqs: set[int], links: tuple[str, ...]) -> dict | None:
        """The drive root's first cci record that carries one of `links`, `--re` or `--resolves`, for one of the records the runner posted about a landing's release."""
        drive = self.runner.config.drive
        replies = cci.records(self.cci_tail, drive, "--lane", ROOT, since=min(seqs))
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
        """A believed-queued prefix the queue no longer holds at the heads it admitted is classified from the adapter's verdict on each dropped PR. A `conflict` stays out at that head, and the gate routes its ejection to the lane; an `own-red` is gated again, and the adapter blocks it until a new head; a `batch-mate-red` blaming a PR outside the PR's own stack tells the root once and re-enqueues the same head after the root's next `go` record naming the PR, while one the adapter itself retries is gated again. A `flaky-check` stays out at that head after its first ejection, and one `blocker` naming the failing job goes to the restack lanes, so the desk enqueues it again only at a new head. An enqueue is judged only once its whole-second completion stamp is before `statuses_floor`, since the status read may answer from a poll made before it."""
        believed = self.believed_queued(rows)
        if not any(believed.values()) or (queue := self.queue_states()) is None:
            return
        polled = {action.action_id for action in self.book.actions(LANDING, kind="enqueue", status="completed") if actions.parse_stamp(action.response["at"]) < self.statuses_floor}
        believed = {key: prs for key, prs in believed.items() if key in polled}
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
            for pr, sha in flaky.items():
                if not self.book.known(LANDING, restack := f"restack:{pr}:{sha}"):
                    self.post_blocker(restack, ",".join(self.restack_lanes()), f"#{pr} {sha}: the merge queue ejected it, failing in a queue draft holding only its stack though its own head reads green ({ejections[pr].detail}); restack it onto trunk and post the new head, since the desk never enqueues {sha} again", pr)
            drop = {
                "outcome": "ejected" if conflicts or flaky else "dropped",
                "drop": {pr: {"kind": kinds[pr], "detail": gated.verdicts[pr]["detail"] if pr in gated.verdicts else ""} for pr in dropped},
                "ejected": conflicts | flaky,
                "awaiting_go": awaiting,
                "dropped_at": self.book.stamp(),
            }
            self.book.attempt(LANDING, lambda incident, key=key, drop=drop: setattr(incident.action(key), "response", {**incident.action(key).response, **drop}))

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
        return [drive, *(other for other in others if any(record["drive"] == other for record in cci.records(self.cci_tail, other, "--pr", f"{self.repo}#{pr}")))]

    def tell_stopped(self, pr: str, row: dict, text: str) -> None:
        """Post one cci `stopped` record about a dequeued PR to each drive that names it: to the PR's lane on this drive, and to the root on every other."""
        for drive in self.naming_drives(pr):
            to = (self.owner(pr, row) or ROOT) if drive == self.runner.config.drive else ROOT
            self.runner.cci_post("stopped", f"DEQUEUED #{pr} by {self.runner.config.drive}: {text}", to, f"#{pr}", f"stopped:{pr}:{drive}:{self.book.stamp()}", pr=f"{self.repo}#{pr}", drive=drive)

    def dequeue_pr(self, pr: str, why: str) -> tuple[bool, str]:
        """Take `pr` out of the queue for `mq dequeue` through `release_pr`, telling every drive that names it."""
        released, line = self.release_pr(pr, why)
        if not released:
            return False, line
        self.tell_stopped(pr, self.ledger_rows.get(pr, {}), f"{why}: {line}")
        return True, f"dequeued #{pr}: {line}"

    def release_pr(self, pr: str, why: str) -> tuple[bool, str]:
        """Take `pr` out of the queue through the adapter, refused when this drive's queue does not answer for it, naming the drives whose records do, and mark each enqueue that admitted it `pulled`, so the desk does not put the same head back without `mq requeue`. Returns the adapter's line, or the refusal."""
        rows = self.rows() or {}
        if not self.owned(pr, rows):
            others = self.naming_drives(pr)[1:]
            owners = ", ".join(f"root@{other}" for other in others) or "no drive's root"
            return False, f"#{pr} is not this drive's: no enqueue of {self.runner.config.drive} admitted it and no lane of it opened it; ask {owners} to dequeue it"
        released, line = self.adapter.dequeue(pr)
        self.cci_tail.forget()
        if not released:
            return False, f"the {self.adapter.kind} queue did not let #{pr} go: {line}"
        for key, prs in self.believed_queued(rows).items():
            if pr in prs:
                self.book.attempt(LANDING, lambda incident, key=key: pull_action(incident, key, pr, why, self.book.stamp()))
        return True, line

    def requeue_pr(self, pr: str) -> tuple[bool, str]:
        """Let `mq requeue` put `pr` back at its current head: a pulled, ejected, or go-awaiting head becomes retryable once the adapter releases whatever latch it keeps on that head, so the next pass enqueues it once every check passes."""
        rows = self.rows() or {}
        head = rows.get(pr, {}).get("head", "")
        latched = pr in self.book.load(LANDING).facts.get(PULLED_HEADS, {})
        if latched:
            self.book.edit(LANDING, lambda incident: incident.facts[PULLED_HEADS].pop(pr))
        cleared = []
        for action in self.book.actions(LANDING, kind="enqueue", status="completed"):
            response = action.response
            sha = enqueued_heads(action.action_id).get(pr)
            if not sha or not head.startswith(sha):
                continue
            if response["outcome"] in (PULLED, "ejected") or pr in response.get("awaiting_go", {}) or pr in response.get("ejected", {}):
                cleared.append(action.action_id)
        if not cleared:
            return True, f"#{pr} {head[:10] or 'has no row'}: " + ("its dequeue latch is cleared; the next pass enqueues it once every check passes" if latched else "nothing holds this head out of the queue")
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
        self.read_only = True
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

    def hand_enqueue(self, prs: list[str], partial: bool) -> tuple[bool, list[str]]:
        """Enqueue `prs` by hand only when `hand_enqueue_gate` passes every head the queue would take, then name each head the queue took that moved since (root #144463, #145481). Returns whether the queue took them, and the lines to print."""
        heads, refused, held = self.hand_enqueue_gate(prs, partial)
        if held:
            return False, [*refused, f"HELD: {numbered(held)} held as named above; the hand enqueue puts nothing in the queue until each hold clears, an open cci hold record by the root's `--resolves`"]
        if refused:
            return False, [*refused, "REFUSED: a check above does not pass a head this enqueue puts in the queue"]
        self.read_only = False
        outcome, _, last = self.enqueue_chains(prefix_tops(list(heads), self.rows() or {}), heads, not partial)
        if outcome not in BELIEVED_QUEUED:
            return False, [f"ENQUEUE-{outcome.upper()}: {last}"]
        return True, [f"{outcome} {numbered(sorted(heads, key=int))} at " + " ".join(f"#{pr} {sha[:10]}" for pr, sha in heads.items()), *self.moved_in_queue(heads)]

    def hand_enqueue_gate(self, prs: list[str], partial: bool) -> tuple[dict[str, str], list[str], list[str]]:
        """The heads a hand `stack-enqueue` of `prs` puts in the queue, read at GitHub's current head, one line per hold or enqueue check that does not pass one, graded as this desk grades its own enqueue, posting and escalating nothing, and the PRs the `hold` check holds: each named PR and the open rows below it, and without `partial` every open row stacked beside or above them."""
        self.read_only = True
        rows = self.rows() or {}
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        below = {pr for named in prs for pr in downstack(open_rows, named)}
        taken = below if partial else {pr for pr in open_rows if downstack(open_rows, pr) & below}
        heads = {pr: self.current_head(pr) or open_rows[pr].get("head", "") for pr in sorted(taken, key=int)}
        graded = Graded(heads, rows, self.runner.now(), act=False)
        failed = [(name, pr, state) for name in self.checks if name in HOLD_CHECKS + ENQUEUE_CHECKS for pr, state in self.check(name, graded).items() if state.state != merge_queue.PASS]
        return heads, [f"#{pr} {heads[pr][:10]} {name} {state.state} {state.reason}".rstrip() for name, pr, state in failed], [pr for name, pr, _ in failed if name in HOLD_CHECKS]

    def current_head(self, pr: str) -> str:
        done = self.shell.run(ghread.read("pr", "view", pr, "--repo", self.repo, "--json", "headRefOid", "--jq", ".headRefOid"))
        return done.out.strip() if done.code == 0 else ""

    def status_lines(self) -> list[str]:
        """The queue as this drive's store records it: each believed-queued enqueue, then each open tree in the order the desk gates and enqueues it."""
        rows = self.rows() or {}
        lines = []
        for action in self.book.actions(LANDING, kind="enqueue", status="completed"):
            outcome = action.response["outcome"]
            if outcome in BELIEVED_QUEUED or action.response.get("awaiting_go"):
                lines.append(f"{action.action_id} {outcome} by {action.response.get('drive', self.runner.config.drive)}")
        for tree in self.trees(rows):
            open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
            members = sorted({pr for tip in tree for pr in downstack(open_rows, tip)}, key=int)
            lines.append(f"tree {numbered(tree)} priority {min(self.priority(open_rows, pr) for pr in members)}: {numbered(members)}")
        return lines or ["the queue holds nothing this drive enqueued, and no open tree waits"]

    def standing_go_lines(self) -> list[str]:
        """Each open ledger PR a standing root `go` names that the queue does not hold: the newest go at its ledger head, else the newest at an older head, with the PR's queue state and verdict from the runner's one batched status read and any hold on it, so a desk reads what waits on a go without a GitHub read per PR."""
        if (rows := self.rows()) is None:
            return ["the ledger could not be listed"]
        if (statuses := self.pr_statuses()) is None:
            return ["the status read failed; see the STATUS-READ-FAILED escalation"]
        held = set(self.held(rows) or [])
        gos = self.root_gos()
        lines = []
        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            head, entry = row.get("head", ""), statuses.get(pr, {})
            named = sorted((go for go in gos if goes(go, pr)), key=lambda go: go["seq"])
            if row.get("state", "open") != "open" or not named or entry.get("queue") not in OUT_OF_QUEUE:
                continue
            current = gone_at(named, pr, head)
            go = (current or named)[-1]
            at = f"at its head {head[:10]}" if current else f"at {go_heads(go)[pr][:10]}, but its head is {head[:10]}"
            lines.append(f"#{pr} go #{go['seq']} {at}; {entry['queue']}, {entry.get('verdict', '')}" + ("; held" if pr in held else ""))
        return lines or ["every open PR a standing go names is in the queue"]

    def restack(self, landed_pr: str, rows: dict[str, dict]) -> None:
        landed = rows.get(landed_pr, {})
        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            if row.get("state", "open") != "open" or row.get("base") != landed.get("branch"):
                continue
            text = f"#{landed_pr} landed as {landed.get('landed_sha', '')[:10]}; #{pr} at {row.get('head', '')[:10]} sits on its deleted branch. Restack now: `ccx vcs stack submit` from your worktree."
            self.route(f"restack:{pr}:{landed_pr}", self.owner(pr, row), text, pr)

    def route_blocker(self, pr: str, sha: str, blocker: merge_queue.Blocker, row: dict, tips: list[str], stack: set[str], gated_red: bool, failing: bool) -> None:
        """Tell the lane once per head, re-reading the gate just before the send so an approval or fix that already arrived is never asked for. A failing required check posts one blocker per PR, refusal, red job and cause: a later head failing the same way adds one line under that blocker, and a changed cause or a new red job opens a new one. A conflict whose lane has no live dispatch and no live cci session routes nothing: the root gets one `PARKED` line per PR, and the head routes once the lane is back."""
        key = blocker_key(pr, sha)
        owner = self.owner(pr, row)
        if self.routed(key, owner) or self.has_landed(pr) or blocker.conflict and self.routed(f"conflict:{pr}:{row.get('base', '')}", owner):
            return
        red = self.red_cause(pr, sha) if failing and not blocker.conflict else ""
        cause = hashlib.sha1(f"{blocker.text}\n{red}".encode()).hexdigest()[:12] if red else ""
        earlier = self.cause_blocker(pr, cause) if cause else None
        parked = bool(owner and blocker.conflict and not self.lane_dead(owner) and not self.lane_live(owner))
        if parked and self.book.known(RUNNER, f"escalation:{PARKED}:{pr}"):
            return
        current = self.checked(tips).verdicts.get(pr)
        if not current or current["verdict"] != "BLOCKED" or self.adapter.blocker(current, stack, gated_red) != blocker:
            return
        if parked:
            self.runner.escalate(f"{PARKED}:{pr}", "PARKED", f"#{pr}", f"{sha}: {owner} has no live dispatch or cci session, so its conflict routes to no one until the lane is back: {blocker.text}")
        elif earlier and (seq := POSTED_SEQ.match(earlier.response["posted"])):
            to = ",".join(self.reach(owner)) if owner else json.loads(earlier.target)["lane"]
            self.post_record("note", key, to, f"{self.dead_note(owner)}#{pr} {sha}: the same failure again: {red}", pr, re_seq=seq[1])
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
        """The lane that owns `pr` now, read as its live `successor`: the poster of the newest `head`, `opened`, `ready`, or `claim` record naming it on this drive, a lane's claim that lists it in its text alone included and the root's and the desks' claims left out, since a fidelity desk claims the heads it grades and never owns them (fidelity-desk-3 #158865), or of a `ledger.py report` on it, else the lane that registered its ledger row."""
        drive = self.runner.config.drive
        desks = [*self.fidelity_lanes, finder.LANE]
        posted = cci.records(self.cci_tail, drive, "--pr", f"{self.repo}#{pr}", *(f"--kind={kind}" for kind in OWNER_KINDS))
        claims = [
            record
            for record in posted
            if record["drive"] == drive and record["kind"] in OWNER_KINDS and (record["kind"] != "report" or cci.lane_report(record)) and not root_claim(record) and pr in dict(repo_prs(record, self.repo))
        ]
        claims += [record for record in self.claims() if record["drive"] == drive and record["kind"] == "claim" and not root_claim(record) and pr in claimed_prs(record, self.repo)]
        newest = max((record for record in claims if not delegated(record["lane"], desks)), key=lambda record: record["seq"], default=None)
        lane = newest["lane"] if newest else row.get("lane", "")
        return self.successor(lane) if lane else lane

    def claims(self) -> list[dict]:
        """The drive's `claim` records, from one scan a pass, so a claim that names its PRs only in its text still moves them."""
        with self.scanning:
            if (claims := self.claims_read) is None:
                claims = self.claims_read = cci.records(self.cci_tail, self.runner.config.drive, "--kind=claim")
        return claims

    def reach(self, lane: str) -> list[str]:
        """Who a blocker for `lane` goes to, read as its live `successor`: the lane while `cci who` lists a live session for it, the lane and the root while it is otherwise alive, and the `restack_lanes` once `lane_dead` reads it dead, since nothing reads a dead lane's mail (root #131190)."""
        lane = self.successor(lane)
        if self.session_live(lane):
            return [lane]
        return self.restack_lanes() if self.lane_dead(lane) else [lane, ROOT]

    def restack_lanes(self) -> list[str]:
        """The live successors of the lanes `landing.restack_lane` names, which take a dead owner lane's PRs, or the root while none is named or every one named is dead, so a dead restack worker never swallows them."""
        return [lane for lane in map(self.successor, self.runner.landing_lanes("restack_lane")) if not self.lane_dead(lane)] or [ROOT]

    def lane_dead(self, lane: str) -> bool:
        """Whether `lane` has no live dispatch or session and posted nothing on this drive under either of its `lane_aliases` in the last `DEAD_LANE`, read once per lane a pass."""
        dead = self.dead
        if lane not in dead:
            dead[lane] = not self.lane_live(lane) and not self.posted_lately(lane)
        return dead[lane]

    def posted_lately(self, lane: str) -> bool:
        drive, names = self.runner.config.drive, lane_aliases(lane)
        posted = cci.records(self.cci_tail, drive, *(arg for name in names for arg in ("--lane", name)), since=actions.stamp(self.runner.now() - DEAD_LANE))
        return any(record["drive"] == drive and record["lane"] in names for record in posted)

    def dead_note(self, lane: str) -> str:
        """The words that open a blocker `reach` sends to the root alone because its owner lane is dead."""
        return f"owner lane dead: {lane} has no live session and no record in {DEAD_LANE.seconds // 60} min, so this comes to {', '.join(self.restack_lanes())}; " if lane and self.lane_dead(lane) else ""

    def session_live(self, lane: str) -> bool:
        """Whether `cci who` lists a live session for `lane`, read once per lane a pass; a read that times out reads as no session."""
        sessions = self.sessions
        if lane not in sessions:
            done = self.shell.run(["cci", "who", "--drive", self.runner.config.drive, "--lane", lane, "--json"])
            if done.code not in (0, TIMED_OUT):
                raise CciTailFailed(f"cci who exited {done.code}: {(done.err or done.out).strip()[:300]}")
            sessions[lane] = done.code == 0 and bool(done.out.strip())
        return sessions[lane]

    def successor(self, lane: str) -> str:
        """`lane` while `cci who` lists a live session for it, else the newest live lane of its `successors`, so mail for a rotated lane reaches the one holding its work; `lane` when none is live."""
        if self.session_live(lane):
            return lane
        if (live := self.live) is None:
            done = self.shell.run(["cci", "who", "--drive", self.runner.config.drive, "--json"])
            if done.code not in (0, TIMED_OUT):
                raise CciTailFailed(f"cci who exited {done.code}: {(done.err or done.out).strip()[:300]}")
            live = self.live = [entry for line in done.out.splitlines() if line.strip() and (entry := json.loads(line)).get("live")] if done.code == 0 else []
        family = successors(lane)
        found = [entry for entry in live if family.fullmatch(entry.get("lane") or "")]
        return max(found, key=lambda entry: entry["announced_at"])["lane"] if found else lane

    def has_landed(self, pr: str) -> bool:
        """A PR the merge queue already landed keeps its ledger row open until the branch is pruned, and the gate still reads its stale branch as conflicted; it gets no blocker."""
        if pr not in self.landed and (self.queue_states() or {}).get(pr) == "landed":
            self.landed.add(pr)
        return pr in self.landed

    def pr_statuses(self) -> dict[str, dict] | None:
        """Every open ledger row's `ccx vcs pr status` entry, from one read a pass in parallel calls of `STATUS_PAGE` PRs, each with its own deadline, served from the poll the pass's ledger refresh made when it is under `STATUS_MAX_AGE` old, so no entry is older than `statuses_floor`, that the requeue read, the landed check, and the routing of PRs no go names share; None when a call fails, which logs one `STATUS-READ-FAILED` per cause an hour, and then none of them acts that pass."""
        if not self.queues_read:
            self.statuses_floor = actions.parse_stamp(actions.stamp(self.runner.now() - STATUS_MAX_AGE))
            numbers = sorted((pr for pr, row in self.ledger_rows.items() if row.get("state", "open") == "open"), key=int)
            pages = [numbers[start : start + STATUS_PAGE] for start in range(0, len(numbers), STATUS_PAGE)]
            with ThreadPoolExecutor(max(1, len(pages))) as pool:
                reads = list(pool.map(lambda page: self.shell.run(["ccx", "vcs", "pr", "status", "-R", self.repo, "--json", "--max-age", f"{int(STATUS_MAX_AGE.total_seconds())}s", *page]), pages))
            done = next((read for read in reads if read.code != 0), Done(0, "", ""))
            statuses = self.statuses = None if done.code else {str(entry["number"]): entry for read in reads for entry in json.loads(read.out)}
            self.queues_read = True
            if statuses is None:
                last = (done.err or done.out).strip().splitlines()[-1:] or [f"exit {done.code}"]
                self.runner.escalate(
                    f"pr-status:{self.runner.now():%Y%m%d%H}:{hashlib.sha1(last[0].encode()).hexdigest()[:8]}",
                    "STATUS-READ-FAILED",
                    "landing",
                    f"`ccx vcs pr status` over {len(numbers)} open rows failed: {last[0]}; this pass requeues nothing, settles no landing from the queue, and routes no PR without a go, and the next pass reads again",
                )
            return statuses
        return self.statuses

    def queue_states(self) -> dict[str, str] | None:
        statuses = self.pr_statuses()
        return None if statuses is None else {pr: entry["queue"] for pr, entry in statuses.items()}

    def tell_unpassed(self, rows: dict[str, dict], held: list[str]) -> None:
        """Tell each PR `unpassed` keeps out of every check, and each PR of a tree `landable_trees` left unchecked, what the pass's one status read says, after the go'd trees enqueue: red CI or requested changes goes to its lane as that head's blocker, a conflict alone once per base, since a restack that keeps its base on purpose stays conflicting at every head, and a landable head runs the accept checks, whose merge-mode check logs its `MERGE-ASK` for the root's board (root, Oct 26 1:38 PM), `GATE_WORKERS` heads at a time, heads not yet told at their head first and then the longest untold, starting none once `TELL_BUDGET` has passed, so a later pass tells the rest. It reads the root's gos again first, so a go posted while the pass ran asks nothing (root #143360)."""
        if not (self.no_go or self.unchecked) or (statuses := self.pr_statuses()) is None:
            return
        self.reread()
        open_rows = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open"}
        covered = under_hold(open_rows, held)
        told = {pr: (open_rows[pr]["head"], statuses[pr]) for pr in sorted((self.no_go | self.unchecked) - covered, key=int) if pr in statuses and pr in open_rows and open_rows[pr].get("head")}
        landable = {pr: head for pr, (head, entry) in told.items() if entry["verdict"] == LANDABLE and pr in self.no_go}
        deadline = self.runner.now() + TELL_BUDGET

        def tell(pr: str, head: str) -> None:
            if self.runner.now() < deadline:
                self.runner.checkpoint()
                self.failing(ACCEPT_CHECKS, Graded({pr: head}, rows, self.runner.now(), act=True))
                self.told[pr] = (head, self.runner.now())

        never = datetime.min.replace(tzinfo=timezone.utc)
        due = sorted(landable, key=lambda pr: (self.told.get(pr, ("", never))[0] == landable[pr], self.told.get(pr, ("", never))[1]))
        with ThreadPoolExecutor(GATE_WORKERS) as pool:
            list(pool.map(lambda pr: tell(pr, landable[pr]), due))
        for pr, (head, entry) in told.items():
            self.runner.checkpoint()
            self.review_bot(pr, head, open_rows[pr])
            if why := status_blocker(entry, "ci-red" not in entry["verdict"] or not self.red_by_design(pr)) or self.unproven_why.get(pr, ""):
                key = f"conflict:{pr}:{open_rows[pr].get('base', '')}" if why == CONFLICT_BLOCKER else blocker_key(pr, head)
                self.route(key, self.owner(pr, open_rows[pr]), f"#{pr} {head}: {why}", pr)

    def review_bot(self, pr: str, head: str, row: dict) -> str:
        """What the review bot's newest `ai-review` run on `head`, from the pass's head read, says `pr` waits on, or empty; a Held verdict tells the owning lane once per head (root #154137)."""
        title = self.review_titles.get(head, "")
        if not REVIEW_BOT_HELD.search(title):
            return review_bot_wait(title, "")
        lane = self.owner(pr, row)
        why = review_bot_wait(title, lane)
        self.route(f"bot-held:{pr}:{head[:10]}", lane, f"#{pr} {head}: {why}", pr)
        return why

    def routed(self, key: str, lane: str) -> bool:
        container = lane_container(lane)
        return key in self.book.load(LANDING).actions or (container in self.book.store.ids() and key in self.book.load(container).actions)

    def route(self, key: str, lane: str, text: str, pr: str, cause: str = "") -> None:
        if self.routed(key, lane):
            return
        self.trust_reads()
        if not lane:
            self.runner.escalate(key, "UNOWNED", f"#{pr}", text)
        elif self.lane_dead(lane):
            self.post_blocker(key, ",".join(self.restack_lanes()), self.dead_note(lane) + text, pr, cause)
        elif self.runner.orca.receipt(lane):
            relay, _ = self.runner.accept_relay(key, lane, text, "", self.runner.config.start_minutes)
            if (dispatch := self.runner.orca.show(lane)) and dispatch.status not in INACTIVE:
                self.runner.send(lane_container(lane), dispatch, relay)
        else:
            self.post_blocker(key, ",".join(self.reach(lane)), text, pr, cause)

    def post_blocker(self, key: str, to: str, text: str, pr: str, cause: str = "") -> None:
        self.trust_reads()
        self.post_record("blocker", key, to, text, pr, cause=cause)

    def trust_reads(self) -> None:
        """Raise, so the stack worker stops and the pass logs `CCI-TAIL-FAILED`, while a cci read failed since the previous pass began: a refusal decided on what a failed or stalled read left out is never posted, and the next clean pass decides again (root, Oct 29 4:43 PM)."""
        if self.runner.read_failed_at >= self.trusted_from:
            raise CciTailFailed("a cci read failed since the previous landing pass began, so this runner refuses nothing until a pass reads cleanly")

    def post_record(self, kind: str, key: str, to: str, text: str, pr: str, cause: str = "", re_seq: str = "") -> None:
        self.book.accept(LANDING, key, "bus", json.dumps({"lane": to, "pr": pr, "text": text, "kind": kind} | ({"cause": cause} if cause else {})), key, None)
        self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now()))
        done = self.runner.cci_post(kind, text, to, pr, key, pr=pr, flags=["--re", re_seq] if re_seq else None)
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

    def refresh(self) -> None:
        self.ledger_py("refresh", "--repo", self.repo, "--ledger", self.ledger)
        self.ledger_py("reconcile", "--repo", self.repo, "--ledger", self.ledger, "--checkout", str(self.checkout))

    def keep_fresh(self) -> None:
        """Refresh and reconcile the ledger every `REFRESH_EVERY` from a daemon thread, so a pass reads the rows the last refresh wrote instead of waiting on ccx's GitHub reads; a pass refreshes itself while no such thread runs."""

        def loop() -> None:
            while True:
                started = time.monotonic()
                self.refresh()
                time.sleep(max(0.0, REFRESH_EVERY.total_seconds() - (time.monotonic() - started)))

        self.refresher = threading.Thread(target=loop, name="landing-refresh", daemon=True)
        self.refresher.start()

    def advance(self) -> bool:
        """Fast-forward the checkout to the remote trunk without writing a shared ref, so the pass gates with the trunk's adapter script; a checkout its repository no longer lists as a worktree, or one on a branch with no commit, is rebuilt at the fetched trunk head in the same pass, and one that still cannot advance is escalated once per error."""
        git = ["git", "-C", str(self.checkout)]
        done = self.shell.run([*git, "fetch", "-q", "--no-tags", "--refmap=", "origin", f"refs/heads/{TRUNK}"])
        if not done.code:
            done = self.shell.run([*git, "worktree", "list", "--porcelain"])
        if not done.code:
            trees = [dict(line.partition(" ")[::2] for line in entry.splitlines()) for entry in done.out.strip().split("\n\n")]
            mine = [tree for tree in trees if os.path.realpath(path := tree.get("worktree", "")) == os.path.realpath(self.checkout) or (os.path.exists(path) and self.checkout.exists() and os.path.samefile(path, self.checkout))]
            tree = mine[0] if mine else None
            if tree is None or not tree.get("HEAD", "").strip("0"):
                done = self.rebuild(registered=tree is not None)
            else:
                done = self.shell.run([*git, "merge", "-q", "--ff-only", "FETCH_HEAD"])
        if done.code:
            self.runner.escalate(
                f"checkout:{hashlib.sha1(done.err.encode()).hexdigest()[:8]}", "CHECKOUT-STALE", "landing",
                f"{self.checkout} did not fast-forward to origin/{TRUNK}, so this pass gated and enqueued nothing: {done.err.strip()}",
            )
        return not done.code

    def rebuild(self, registered: bool) -> Done:
        """Reset the checkout to the fetched trunk head with a clean tree, first registering it afresh through `git worktree add --no-checkout` and `git worktree repair` when its repository lists it no more, carrying its old index over so the reset removes files the trunk no longer tracks, and log `MIRROR-REBUILT` to root naming what it found."""
        git = ["git", "-C", str(self.checkout)]
        if (head := self.shell.run([*git, "rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}"])).code:
            return head
        sha = head.out.strip()
        if not registered:
            branch = self.shell.run([*git, "symbolic-ref", "-q", "--short", "HEAD"])
            if (index := self.shell.run([*git, "rev-parse", "--path-format=absolute", "--git-path", "index"])).code:
                return index
            with tempfile.TemporaryDirectory(dir=self.checkout.parent) as scratch:
                staged = Path(scratch) / self.checkout.name
                for argv in (
                    [*git, "worktree", "add", "-q", "--no-checkout", *(["--detach"] if branch.code else ["-B", branch.out.strip()]), str(staged), sha],
                    ["cp", str(staged / ".git"), str(self.checkout / ".git")],
                    [*git, "worktree", "repair", str(self.checkout)],
                ):
                    if (done := self.shell.run(argv)).code:
                        return done
            if (fresh := self.shell.run([*git, "rev-parse", "--path-format=absolute", "--git-path", "index"])).code:
                return fresh
            if (done := self.shell.run(["cp", index.out.strip(), fresh.out.strip()])).code:
                return done
        if (done := self.shell.run([*git, "reset", "-q", "--hard", sha])).code:
            return done
        found = "was on a branch with no commit" if registered else "was not a registered worktree of its repository"
        self.runner.escalate(f"mirror:{sha[:12]}", "MIRROR-REBUILT", "landing", f"{self.checkout} {found}, so this pass rebuilt it at origin/{TRUNK} {sha[:12]} with a clean tree and gated as usual")
        return done

    def run(self) -> None:
        self.ask_rulings()
        self.cci_tail.remember()
        try:
            self.gate_pass()
        finally:
            self.cci_tail.stop()
            self.report_unread()

    def ask_rulings(self) -> None:
        """Log one `RULING-ASK` per board round while ruling cards `rulings.py classify` wrote queue under the drive's `cards/`; the root puts the round on the cc-present board with `rulings.py round`, never one card per ruling."""
        state = self.runner.config.source.parent
        if not (queued := list((state / RULING_CARDS).glob("*.json"))):
            return
        number = len(list((state / RULING_ROUNDS).glob("round-*.json"))) + 1
        self.runner.escalate(
            f"ruling-ask:round-{number}",
            "RULING-ASK",
            f"round-{number}",
            f"{len(queued)} ruling confirmations queue | when the owner can take a round, run `rulings.py round --drive {self.runner.config.drive}`:"
            f" it puts at most ten of them on the live cc-present board as round {number}, foundational rows first, never as an AskUserQuestion or a Slack card;"
            " a pick or a note on a ruling there is a capture: Right ticks every line with `rulings.py confirm <key> --tick all --capture <digest>`,"
            " and Some wrong waits for the owner's note naming the lines",
        )

    def reread(self) -> None:
        """Drop the pass's root gos and cci reads, so the next read sees records posted since."""
        self.gos = None
        self.cci_tail.forget()

    def gate_pass(self) -> None:
        self.sessions, self.live, self.dead, self.designed_red, self.full_runs_read, self.said_preconditions, self.resolved_preconditions = {}, None, {}, {}, {}, {}, {}
        self.grading, self.grade_tried = True, set()
        self.gate_deadline = time.monotonic() + GATE_BUDGET.total_seconds()
        self.trusted_from, self.pass_began = self.pass_began, time.monotonic()
        self.surface()
        self.statuses, self.queues_read, self.reviews, self.claims_read, self.gos, self.unchecked, self.unproven_why, self.cleared, self.exempts = None, False, None, None, None, set(), {}, {}, {}
        if self.quota_low():
            return
        with ThreadPoolExecutor(1) as pool:
            advancing = pool.submit(self.advance)
            if not (self.refresher and self.refresher.is_alive()):
                self.refresh()
            advanced = advancing.result()
        self.runner.checkpoint()
        rows = self.rows()
        read = self.watch_trunk(rows or {})
        if rows is None:
            return
        if self.status_card:
            self.draw_card(rows)
        entries = holds.read(self.holds)
        unread = self.holds_unread(entries, rows)
        if not rows:
            return
        self.carry_taps(rows)
        held = self.held(rows) if advanced and read and not unread else None
        self.held_now = held or []
        gating = held is not None and not self.checks_paused()
        self.no_go = self.unpassed(rows) if gating else set()
        self.refuse_ungone_parents(rows, held or [])
        barred = sorted({*(held or []), *self.no_go, *self.pull_ordered(rows)}, key=int)
        gone = self.land_gone(rows, barred) if gating else set()
        self.runner.steps(lambda: self.read_heads({row["head"]: pr for pr, row in rows.items() if row.get("state", "open") == "open" and row.get("head")}), self.reconcile, lambda: self.requeue(rows))
        if not self.side_work:
            self.side_work = self.side.submit(self.runner.steps, lambda: self.verify(rows), lambda: self.announce_landings(rows), lambda: self.remove_landed_worktrees(rows), lambda: self.releases(rows), self.ungraded_after, lambda: self.verify_restacks(rows))
        self.runner.steps(lambda: self.resubmit_graphite_base(rows))
        if held is not None:
            self.runner.steps(lambda: self.squash_incidents(rows, held))
            if gating:
                self.runner.steps(lambda: self.gate(rows, barred, gone), self.enqueue, lambda: self.unqueued_gos(rows, held), lambda: self.tell_unpassed(rows, held))
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
    runner.keep_alive("orca")
    if not once:
        runner.keep_finding()
    while True:
        if runner.now() - rotated >= ROTATE_EVERY:
            runner.rotate_inboxes()
            rotated = runner.now()
        runner.follow_coordinator()
        try:
            if runner.check():
                runner.steps(runner.reap, runner.read_inbox, runner.read_cci, runner.deliver)
                if runner.now() - swept >= SWEEP_EVERY:
                    runner.steps(runner.sweep)
                    swept = runner.now()
            runner.steps(runner.deadlines_orca, runner.incident_clock, runner.pr_silent, runner.lanes_gone, runner.dispatches_gone, runner.class_unowned, runner.in_process, runner.cut_clock, runner.release_coupling, runner.finders, runner.fidelities)
        except StopRequested:
            runner.flush()
            runner.stop_marker().unlink()
            return 0
        except OrcaUnreachable as down:
            runner.orca_down(down)
        except Exception as error:
            if runner.pass_raised("orca", error):
                return 1
        else:
            runner.orca_back()
        runner.aged_holds()
        runner.stalled_launches()
        runner.stall_watch()
        behind = runner.stale()
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
    known = merge_queue.CHECKS + merge_queue.OPT_IN_CHECKS
    if unknown := [name for name in landing.get("checks", merge_queue.CHECKS) if name not in known]:
        faults.append(f"lists {', '.join(unknown)} under landing.checks, which is no built-in check of {', '.join(known)}")
        members.append(f'"checks": {json.dumps([name for name in landing["checks"] if name in known])}')
    if "build-failed" in landing.get("checks", merge_queue.CHECKS) and not (landing.get("build_failures") and landing.get("ci_build")):
        faults.append("runs the build-failed check with no landing.build_failures or no landing.ci_build, so no build at a head could be read")
        members.append('"build_failures": {"tree": "<path prefix>", "context": "<annotation context>", "annotations": "tools/ci bk api /pipelines/{pipeline}/builds/{number}/annotations"}')
    if landing["merge_mode"] == "high-risk" and not landing.get("high_risk_paths"):
        faults.append("runs merge mode high-risk with no landing.high_risk_paths globs, so no PR would read as high-risk")
        members.append(f'"high_risk_paths": {json.dumps(list(HIGH_RISK_STARTER))}')
    if (after := landing.get("checks_wait_for_go")) is not None and not (isinstance(after, int) and after > 0):
        faults.append(f"names landing.checks_wait_for_go {json.dumps(after)}, which is no cci sequence number")
        members.append('"checks_wait_for_go": <the cci sequence the pause starts after>')
    if landing.get("merge_ask_surface", "slack") not in MERGE_ASK_SURFACES:
        faults.append(f"names landing.merge_ask_surface {json.dumps(landing['merge_ask_surface'])}, which is neither of {', '.join(MERGE_ASK_SURFACES)}")
        members.append('"merge_ask_surface": "slack"')
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
    if not once:
        landing.keep_fresh()
        landing.keep_overflowing()
    interval = runner.config.landing.get("interval_seconds", 180)
    runner.keep_alive("landing")
    while True:
        started = runner.now()
        try:
            landing.run()
            if once:
                landing.drain()
        except StopRequested:
            landing.close()
            runner.flush()
            runner.stop_marker().unlink()
            return 0
        except CciTailFailed as failure:
            runner.tail_failed(failure)
        except OrcaUnreachable as down:
            runner.orca_down(down)
        except Exception as error:
            if runner.pass_raised("landing", error):
                return 1
        behind = runner.stale()
        took = (runner.now() - started).total_seconds()
        landing.post_state(took)
        runner.flush()
        print(f"PASS landing {runner.passes['landing'] + 1} on {RUNNING_VERSION} took {took:.0f}s: {runner.shell.pass_cost()}", file=sys.stderr, flush=True)
        runner.beat("landing")
        if once:
            return 0
        if runner.rest(max(0.0, interval - (runner.now() - started).total_seconds())):
            landing.close()
            runner.stop_marker().unlink()
            return 0
        if behind:
            landing.close()
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
        with runner.desk_lock(args.desk) as holder:
            if holder is not None:
                print(f"desk-runner run: runner {holder} already runs the {args.desk} desk on {config.source}; `desk-runner.py handoff --config {config.source} --desk {args.desk}` replaces it", file=sys.stderr)
                return 3
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
    drive.run_installed(__file__)
    sys.exit(main())
