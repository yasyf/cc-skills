# The orca desk runner

`desk-runner.py` owns Orca worker traffic and ready-prefix landing. Run two detached
processes against one config and store: `run --desk orca` and `run --desk landing`.
No model-operated orca-desk sits between the root and Orca. The landing runner is
the drive's merge queue and runs D3, D14, D16, and D20; the landing-desk lane keeps
the duties in [its brief](landing-desk-brief.md).

*Prevents G130's `AmiBake` launch being lost behind a desk handoff, R620 going to a
superseded desk, and GO carrying no start deadline (2026-10-01 audit, Brief 3 and
ranked fix 3).*

## Root discipline

Issue commands with the drive's R/L numbering. Submit a key once per action;
submitting it again in the same lane returns the existing action. Never `SendMessage`
a desk.

The root never types an `R<n>` or a clock. It cannot read the desk inbox, so a key it
picks can collide with one a line there already holds. `desk-launch.py` mints every
keyed line the root sends. It takes one past the highest `R<n>` on a cci record
addressed to `orca-desk` and in `inbox/orca-desk.md`, its archive included, stamps the
Pacific clock, posts the line to `orca-desk` on cci, and prints the key and the line.

```text
desk-launch.py incident <name> <fix-brief> [<evidence-brief>]
desk-launch.py lane <lane> <model> <effort> <brief> [--now]
desk-launch.py relay <lane>[,<lane>...] <text>
desk-launch.py hold <slug> <owner> <what>
desk-launch.py unhold <slug>
desk-launch.py withdraw <R<n> | unfixed:<msg>:<n> | lane>
```

A relay or a reply posted on cci can be any length and span lines; post it whole, as one record, and the runner relays all of it.
Keep every other inbox line under 400 characters; put evidence in a file or cc-notes and leave a pointer in the line.

A desk keys its own commands to the runner:

```text
desk-runner.py relay --config C --key R<n> --lane L --text T [--reply-to <question msg id>]
desk-runner.py launch --config C --key R<n> --lane L --model M --effort E --brief PATH [--owner-directed]
desk-runner.py policy --config C --key L<n> --landing prefix|whole --revision REV --source TEXT --supersedes <current revision>
desk-runner.py rebind --config C
desk-runner.py show --config C
```

A relay can also be one line appended to the desk inbox, which the runner still reads
while the root moves to cci records. Set the required
`orca.desk_inbox` config field to the drive's `inbox/orca-desk.md`. The orca runner
reads each complete line appended since its saved byte offset; its first read starts
at the file's end, so history never replays. It reads each cci record addressed to
`orca-desk` the same way, from a saved sequence that starts at the drive's newest
record, skipping records `cci import` took from the desk inbox; a record that is no
directive logs `DIRECTIVE-FAILED`. A line is a relay only in this form, one
relay per line, its text running to the end of the line:

```text
R<n> (<time>) orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>
```

In a cci record the first line is the directive and the text runs to the end of
the record, starting on that line or the next, so a relay posted on cci keeps its
paragraphs and lists. One record is one relay, so a later line is relay text,
never a second directive.

For each named lane with a live dispatch, the runner does what `relay --key R<n>`
does. When the dispatch's newest question to the Run has no answer on its terminal,
the relay is a reply to that question, which releases a worker blocked in
`orca orchestration ask`; otherwise, it is a plain relay that wakes the terminal.
A line without `R<n>` takes the key `inbox@<byte offset>`. Each lane logs once to the
cci drive as `RELAYED <key> <lane>` or `RELAY-FAILED <key> <lane>: <reason>`,
addressed to root.
A dead or missing dispatch, a key that already holds different text, and a line that
says `orca-desk: relay` outside this form each fail visibly; nothing is relayed for
them. A `relay` command accepted for a lane with no live dispatch and no launch in
flight fails the same way on the next pass, never later as a `DEADLINE`, unless a send
already delivered it; a delivered relay is never failed or reported again. A dispatch
is dead only when Orca answers that it is. When Orca gives no answer, a runtime or
connection error, a timeout, or output that is not its JSON, the runner stops that pass
without reading any dispatch as gone or failing any relay, logs `ORCA-UNREACHABLE` once
for the streak, backs off up to two minutes between passes, and logs `ORCA-BACK` on the
first pass Orca answers.

A launch can also be one line in the same inbox, in this form only, one launch per
line, with nothing after the brief path:

```text
R<n> (<time>) orca-desk: launch <lane> [NOW] <model> <effort> [role=<role>] brief=<absolute path>
```

The runner does what `launch --key R<n>` does with the same model, effort, and
brief; `NOW` is `--owner-directed`, so under load the launch fails at once instead of
waiting out the hold, and `role=<role>` is `--role`, the launch's role for the incident
clock. An incident
fix lane is `R<n> (<time>) orca-desk: launch <lane> NOW incident xhigh brief=<path>`;
only `incident` runs on the fast tier, and plain `sol` is the standard tier. A line without `R<n>` takes the key
`inbox@<byte offset>`. A verified launch logs `LAUNCHED <key> <lane>: dispatch
<ctx> terminal <handle>`. The runner logs `LAUNCH-FAILED <key> <lane>: <reason>`
and launches nothing for a lane with a live dispatch or a launch still in flight,
a key that already holds an action, a model or effort `launch` refuses, a brief
that is not a file, or a brief with no `Register slice: lane=<lane> step=<n>` line
unless the launch is an incident fix's. Before it launches, the runner refreshes the
slice under that line with `rulings.py slice --refresh <brief>`, writing
`Register slice: lane=<lane> step=incident` into an incident brief that has none, and
it writes the slice into every alert, case, fallback and manual release brief it
attaches. A slice that does not read logs `SLICE-UNREAD` and the launch goes ahead.
A line that says `orca-desk: launch` outside the form logs
`LAUNCH-FAILED <key> inbox`.

`desk-launch.py incident <name> <fix-brief> [<evidence-brief>]` and
`desk-launch.py lane <lane> <model> <effort> <brief> [--now]` post these lines for the
root; `incident` posts `role=fix` and `role=evidence` on its two lines. Each takes the next `R<n>`
past every key on an `orca-desk` cci record and in the desk inbox, posts
with `--to orca-desk`, waits for `LAUNCHED`, `LAUNCH-FAILED`, `LAUNCH-HELD`, or
`UNVERIFIABLE`, and prints the runner's line with the dispatch and terminal.

A launch or a lane is withdrawn with one line naming its launch key, `R<m>` or a case
launch's `unfixed:<msg>:<n>`, or the lane itself, which `desk-launch.py withdraw` posts:

```text
R<n> (<time>) orca-desk: withdraw R<m>
R<n> (<time>) orca-desk: withdraw <lane>
```

The runner fails a launch it accepted and has not started, such as one held on load,
and stops a launched dispatch with worker-stop, as `orca-gc --done` does. It logs
`WITHDRAWN` and marks the lane withdrawn, so the lane's `UNFIXED` lines launch no case
lane and Orca's offer to dispatch it again logs `WITHDRAWN`, not `DECIDE`. A launch
still starting, a settled launch with no dispatch, a key or lane that holds nothing, or
a worker-stop Orca refuses logs `WITHDRAW-FAILED`. A worker-stop Orca takes but never
shows settled, with its terminal gone or at an idle prompt, within 30 seconds, such as one
answering `stop_unknown`, logs `WITHDRAW-UNCONFIRMED` with the dispatch and terminal
instead of `WITHDRAWN`.
A root `cci post --kind withdraw --re <seq>` whose record is a launch line addressed
to `orca-desk` withdraws that launch the same way. Any other record carrying `--re`
is a reply, never a directive, so it logs no `DIRECTIVE-FAILED`.

Each root cci record carrying `--re` is read as a reply. One to the runner's
`DECIDE` on a worker question is relayed as the reply to that question. One to a
lane's own record, such as its cci ask, is relayed as the reply to that lane's open
Orca question, which the cci answer superseded. When another reply, an inbox relay
line included, already claims that question, or every open question, it goes to the
lane as a plain relay, since Orca refuses a second answer to one question. A reply
Orca still refuses with `answer_conflict`, answered outside the runner, goes once to
the dispatch as a message on the relay's thread. A lane with no open question gets
nothing, since it reads the answer on cci.

A monitor transition and an incident launch are each one line in the same inbox:

```text
orca-desk: alert <slug> <link> :: <what fired>
orca-desk: incident <slug> <link> reported=<HH:MM> :: <what fired>
```

`reported=` takes the first report's Pacific time as `18:41` or `6:41 PM`, and a slug may
carry a Slack channel name's underscores. `monitor-watch.py --alert-inbox` writes `alert` lines. The runner records the
transition as `ALERT` and launches nothing; a repeat relays to a live fix lane.
The alerts desk writes an `incident` line only when it judges a lane necessary.
For an `incident` line the runner fills `reference/alert-fix-brief.md` with `alert.facts`, attaches
it to the briefs log as `<slug>-fix.full.md` with `ccn log append --attach
--replace`, and launches `<slug>-fix` on incident xhigh at once on that
attachment's `ccn attachment path`. Nothing goes under `~/.claude/scratch`. A brief that fails
to attach logs `INCIDENT` with the ccn error and launches nothing. A repeat relays
to the live lane and keeps its brief. An incident another owner holds launches no lane: the root's `done` that resolves a
record naming the slug, its `stopped` addressed to `<slug>-fix`, or an `answer` from another
drive's root addressed to `root@<this drive>` that names the slug. The runner reads cci for
these at intake and again just before the launch starts, fails a pending launch, and posts one
`state` record, `INCIDENT-OWNED <key> <lane>: incident <slug> owned by <who>; no launch`.
The root ratifies from `INCIDENT`: evidence
and incident-doc lanes, target fence, and comms in the affected account channels
and, for a platform-wide incident, `#outage` under R16. The evidence lane attaches
`<slug>-evidence.md` to the same briefs log, where the fix brief tells the fix
lane to read it.
The incident executor can adopt the launched lane with `--adopt fix=<lane>`.
A malformed line logs `ALERT-FAILED` or `INCIDENT-FAILED` and launches nothing.

Every incident fix lane the runner launched is on a clock from its verified launch.
A lane is one when it launched on the `incident` alias, or when its brief's `ccx:`
line says `role=incident` or names an `incident=`. A launch whose line or brief names
another role, such as `role=evidence`, is never clocked, and neither is `incident=none`
or a lane whose name has an `evidence`, `proof`, `closeout`, `desk`, or `audit` part.
`desk-launch.py incident` launches `<name>-evidence` with `role=evidence`, so an
evidence lane gets no mechanism, PR, or ready threshold and no Opus fallback. On each
pass the runner reads the lane's own cci records, posted as `<name>` or `<name>-fix`,
and its `mechanism:`, `ready:`, and `fix-live:` Run statuses:

| Minutes since launch | Missing | The runner |
|---|---|---|
| `deadlines.incident_mechanism_minutes`, default 10 | a `mechanism` record or status, or a record of any kind whose text says mechanism | launches the Opus fallback and logs `INCIDENT-CLOCK <lane> mechanism` |
| `deadlines.incident_pr_minutes`, default 15 | an `opened` record | launches the fallback unless it already did, and logs `INCIDENT-CLOCK <lane> pr` |
| `deadlines.incident_ready_minutes`, default 20 | a `ready` record or status, or a `report` or `incident` that starts `READY` | logs `INCIDENT-CLOCK <lane> ready` with the missing milestones and the minutes each phase took |

A fallback whose Orca launch fails is launched once more under `<key>:retry`. After a
second failure, while the lane's clock runs, the runner logs
`INCIDENT-CLOCK <lane> in-process: the Orca launch of the Opus fallback <lane>-opus failed twice: <reasons> | root: start <lane>-opus in-process now, ...`
with the fallback brief's path, and launches nothing more for it.

A later milestone counts for the earlier ones, so an opened PR also settles the
mechanism threshold. Each threshold logs once per launch. The clock stops at `ready`,
at a `fix-live`, `not-ours`, `stopped`, or `stand-down` record, at a `fix-live:`
status, at a root `decide` addressed to the lane or naming it, or when the dispatch
settles. A record posted before the launch does not count.

```text
INCIDENT-CLOCK <lane> ready: not ready 20 min after its launch at 11:02, limit 20 min; missing PR, ready; launch to mechanism 7 min, mechanism to now 13 min
```

The fallback is `<lane>-opus` on opus xhigh in Claude fast mode, launched on the Mac
as an `--owner-directed` launch. Its brief is the lane's own brief, unchanged, under
a header that says why the clock started it, that it works in parallel and the first
PR wins, and that it posts on cci as `<lane>-opus` and addresses `<lane>` and root.
The runner attaches that brief to the briefs log as `<lane>-opus.full.md` and writes
it to `<state>/briefs/<lane>-opus.full.md`, the path its lines name. A `-opus`
lane is clocked and gets no fallback of its own. A lane whose `<lane>-opus` already
has a live dispatch or a launch in flight gets no second, and the line says so.
`show` prints each clocked lane's minutes and milestones.

A fix lane gets one Opus fallback. The incident executor, `incident.py`, launches
its own Opus backup for a fix lane it adopted after 15 quiet minutes. While that
backup is launching or live the clock launches none and the line names it, and
the executor launches none while the clock's `<lane>-opus` is launching or live.

*Prevents the seven incidents of 2026-10-13, which ran from 57 minutes to over four
hours. Lanes went more than 15 minutes with no mechanism and nobody was told, and
the Opus fallback was started by hand an hour late.*

A mechanism-to-PR clock also covers lanes the root started in-process. Any lane
named `inc-`, `incident-`, or `outage-` that is not an `-evidence` lane counts, as
does every fix lane the runner launched. A `mechanism` record, or a `report`,
`incident`, or `state` that starts `MECHANISM` or `CANDIDATE`, starts it. With no
`opened`, `head`, `ready`, `blocker`, `blocked`, or closing record, and no text
record starting `OPENED`, `BLOCKER`, `READY`, or `NOT-OURS`, from the lane or its
`-opus` pair within `deadlines.pr_silent_minutes` (default 10), the runner logs a
`PR-SILENT` blocker, then again every `deadlines.pr_silent_every_minutes` (default 5).
It fires only for a lane that owns a worktree or owes a PR, as a fix lane the
runner launched or one named `-fix`, `-fix-<n>`, `-opus`, or `-case<n>` does. A lane whose name or brief, `<state>/briefs/<lane>.md`
or `<state>/<lane>/brief.md`, names an evidence, proof, closeout, desk, or audit
role never fires it. Any lane's `opened`, `head`, `ready`, or `fix-live` answers the
mechanism when it names the seq with `--re`, shares the topic, or names one of its
incident tags. A root `go` or `decision` answers it the same way. `SAND-5A`,
`[SAND-5A]`, and `SAND5A` read as one tag, and an answer posted before the
mechanism counts too. A root `decide` or `decision` addressed to the lane, and the
lane's own `done` or a text record starting `DONE` or `STOPPED`, settle it as well.

The line carries `lanes.lane_status`, the code behind `drive.py lane-status <lane>`.
It names every worktree the lane owns, found from the runner's launch receipts, the
lane's cci records of the last day, the briefs checkout's `git worktree list`, and
its PRs' head branches. Each worktree reads as branch, dirty files, commits ahead of
`origin/dev`, last commit, push state from `git ls-remote`, and PR with CI state, or
`no worktree found for lane <lane>`. Incident clocks never defer it. A root
`stand-down` or `stopped` to the lane, or a root `--re` or `--resolves` on one of
its lines, ends the repeats.

```text
PR-SILENT <lane> #<seq>: MECHANISM at 21:11, 10 min with no OPENED or BLOCKER record; <path> (git worktree list): HEAD (no branch), 0 dirty files, 0 ahead of trunk, last commit 130 min ago <sha> <subject>, not pushed, no PR | <lane>: commit and push the fix, open its PR with the submit skill and post OPENED, or post BLOCKER
```

*Prevents the Oct 20 stall in which `inc-liam-load-opus` posted its mechanism at 9:11 PM and its worktree was still clean on a detached HEAD at 9:16 PM, which the root found only by checking by hand.*

A case a fix does not cover is an incident lane, never backlog. The alert fix brief
and the executor's fix brief make every incident fix lane reproduce the reporter's
own case before it reports ready. The lane closes its `ready:` and `fix-live:` Run
statuses with one `UNFIXED <lane>: <case>` line per known case the fix does not cover,
or `UNFIXED <lane>: none`. Only those two partial-fix reports launch case lanes; a
`worker_done`, a `NOT-OURS`, or a `STOPPED` launches nothing, whatever its body says.

For each case the runner launches `<first>-case<n>` on incident xhigh in the same pass,
with no root turn. `<first>` is the lane's name without its trailing `-opus` and
`-case<n>` parts, so every case lane of one incident sits beside the first fix lane,
and `<n>` is the lowest no lane container holds. The runner fills
`reference/unfixed-case-brief.md` with the case verbatim, the reporting lane, the path
of that lane's brief, and the PRs its cci `opened` records name. It attaches that brief
to the briefs log as `<first>-case<n>.full.md` and logs:

```text
INCIDENT <lane> <time>: its fix does not cover: <case> | case lane <first>-case<n> launching on incident xhigh, brief <path>; root: ratify, and keep the reporter told that this case is still open
```

Only a line that names the reporting lane counts, and `UNFIXED <lane>: none`
launches nothing. A case launches once across a lane, its `-opus` fallback, and its
case lanes, however often and by whichever of them it is reported. A case whose
launch is refused, or whose brief does not attach, logs `not launched` with the
reason and launches on the next report that names it. The clock above covers each
case lane.

An incident launches at most `incident.case_lanes` case lanes, two by default. The
runner launches nothing for a report's remaining new cases and puts them to the root
in one line, with a brief attached and a launch line ready for each:

```text
DECIDE unfixed:<msg> <lane>: <reason>: <case> | to launch its lane, post `orca-desk: launch <first>-case<n> NOW incident xhigh brief=<path>`; <case> | …
```

The reason is one of three. The case is past the cap. A case lane reported it, and
case lanes are one generation. Or the report's own text, or the lane's newest
`mechanism` record or status, says `UNKNOWN`. A lane without a named cause offers a
case lane nothing to extend, so each case waits for the root to post its line.

An incident ends at a `not-ours` or `stand-down` record its fix lane or `-opus`
fallback posts, or at a root `stand-down` or `stopped` addressed to either or to the case lane. A
`stopped` the reporting lane posted before its report ends it for that report. A report whose
subject or first body line opens with `NOT-OURS`, `stood down`, or `Stopped` launches no case
lane and files no `DECIDE`. A report after the end, or from a lane the root withdrew, launches nothing and logs one
line:

```text
UNFIXED <lane> <time>: <case>; <case> | no case lane launched: the incident ended with #<seq> <KIND> <lane>
```

A case launch the runner accepted and has not started fails once its incident ends,
and logs `WITHDRAWN unfixed:<msg>:<n> <first>-case<n>: the incident ended with …`.

*Prevents the resume fix of 2026-10-13. It was reported fixed after a test on a
neighbouring case while the reporter's own thread still failed, and two edge cases
the fix lane had named sat as backlog for two hours until a customer hit them.*

*The cap, the end, and the `UNKNOWN` gate come from 3:43 AM Oct 16. Lane
`alerts-api-0335-fix` posted `NOT-OURS` (cci #81930) and `STOPPED` (#81931) after the
root's stand-down (#81925), and its `worker_done` listed the ten original 524s as
`UNFIXED`. The runner launched a case lane for each Cloudflare `RayID` on incident xhigh (#81941 to
#81950), and five started after the root stood them down (#81955).*

An urgent release hold is one line in the same inbox. The holding lane names
itself or a dedicated decision-owner lane and states both the release and its
blocker:

```text
orca-desk: hold <slug> owner=<lane> :: <what is held, and on what>
orca-desk: unhold <slug>
```

Use `hold` for a release the plan marks urgent or any blocked catch-up of a
coupled control-plane set. A held or blocked coupled release gets its own
decision-owner lane. The holding lane writes `unhold` when the release moves.
Repeating an active hold keeps its first clock; a hold after `unhold` starts a
new one. A malformed hold or unhold logs `HOLD-FAILED`.

After `deadlines.hold_minutes` (default 15), the runner writes once per hold:

```text
DECIDE hold:<slug> <owner>: held <n> min: <what> | the root decides it now with <owner>, ahead of any open owner question on another subject
```

The root resolves it with the named lane ahead of any open owner card on another
subject; that card keeps waiting for its tap. The line reaches the root over the `cci` channel as
soon as the runner writes it. The hold verb records
a decision clock; it does not change the landing holds file or authorize a release.

*Prevents the api/runtime-v2/restate-worker catch-up waiting behind the Demeter
questions on 2026-10-03 while executor shipped alone. cc-notes answer `5548bb2`,
option 3; retro `849e294`.*

Use `--model incident --effort xhigh` for fix and evidence lanes. A worker-question
`DECIDE` carries the question message id as its key; answer with
`relay --reply-to <msg id>`. A `hold:<slug>` key names a release decision, not a
question message id. Other relays carry guidance or a brief attachment pointer.
Keep each brief complete as an attachment on the drive's `briefs: <slug>` log.

Read runner traffic from the cci drive named by the config's `drive` field.
Each escalation posts with `--lane desk-runner --to root`; its topic is the line's
key, the second token. `DECIDE` uses kind `decide`, `INCIDENT` uses `incident`,
`UNBOUND`, `UNOWNED`, `UNACKED`, `INCIDENT-CLOCK`, `PR-SILENT`, `HOLDS-UNREAD`, and `LOAD-HIGH` use `blocker`, any `*-FAILED` uses
`defect`, and all other labels use `report`. Text over 400 characters is clipped, with the full line saved
to `<receipts>/cci/<hash>.txt` and attached with `--path`. Landing restack
routes sent through cci use `--kind blocker --to <lane> --topic <pr>` with the same
text limit and attachment rule. A landing's release record and its blockers use
`--kind release` and `--kind blocker` with `--to <landing.release_owner>,root --topic <pr> --pr <pr>`.

`monitor-watch.py --alert-inbox` keeps writing alert lines to `orca-desk.md`.
The runner posts each Datadog alert's `INCIDENT` to root through cci with topic
`dd-<id>`; a `fix-live` record with `--topic dd-<id>` closes it.

The root receives cci records over the `cci` channel subscription R9 defines.

`show` and the config's `view` file render action state. Runner-owned `inbox/`
files are views, never command authority. Keep existing history; do not mirror
runner actions into `TaskCreate`/`TaskUpdate` or complete shadow tasks for them.

Only the orca runner consumes the Run. To cut over from a running orca-desk,
let it finish its current pass and acknowledge its delivered batch, then end its
loop before starting the runner. Keep its session open. Carry unresolved work into
runner commands with its existing keys.

The runner reads `orchestration inbox --terminal run:<run>` without a waiter or
acknowledgement. No other loop may run `check --run` with `--wait` or `--ack`.

## Config and startup

Fill the paths and ids in this JSON and use the same file for both processes.
Set `drive` to the same cci drive used by the root's Monitor. `orca.desk_inbox` is
required.

`store` is optional; omitting it uses `~/.claude/long-running/incidents`. The store
holds one JSON container per lane, plus `desk-landing` and `desk-runner`, with an
owner and `owner_generation`. Match `orca.run` to the launch environment. The runner
reads each lane's dispatch and terminal from `~/.claude/scratch/orca-launch/<orca.run>`,
the receipt directory orca-launch.sh writes by default, and launches with
`ORCA_LAUNCH_STATE` set to it. A lane launched by hand with orca-launch.sh is therefore
relayed to like one the runner launched, with no receipts to copy; never set
`ORCA_LAUNCH_STATE` on a hand launch. `orca.gc` names the repo's gc command, which every `RECLAIM` line
carries after `desk-runner.py gc`. Set the accepted prefix policy's revision to #28601's revision.

`landing.release_owner` is required by `run --desk landing`, which exits on a config
without it. It is the cci `--to` address of the lane that starts releases, such as
`peer:<session name>` for a session bound to another drive.
`landing.release_cli` is the repo's release command line, its first word relative to
`landing.checkout`; the landing desk reads `<release_cli> live --json` and
`<release_cli> live <id> --json` through it, and a CUT-SILENT hand path and the manual
release brief name it. On a config without it, `run --desk landing` exits with
`MQ-CONFIG-INVALID`, whose line lists it with every other missing member, as
`"release_cli": "tools/ci release"` on a checkout that carries `tools/ci`.
`landing.applying`, optional, turns on the orca runner's apply announcements: `build` is
the command line, first word relative to `landing.checkout`, that prints a release
build as Buildkite's REST build JSON, with `{build}`, `{pipeline}`, and `{number}`
filled from the build URL a lane's `release` record names; `step` is a regular
expression with `component` and `env` named groups that a job's step key must match
whole; and `components` lists the fnmatch globs of the components whose applies peer
drives watch. `run --desk orca` posts `APPLYING-CONFIG-INVALID` and exits on a partial one.
`reference/release-lanes.md` has what the runner claims, announces, and asks.
`landing.merge_mode` is required too: the owner's pick of `every`, `high-risk`, or
`auto`, which the root asks once at execution start and records as a cc-notes answer.
Without one of those values the landing runner exits and posts `MERGE-MODE-UNSET`. It
re-reads the value on every use, so a change needs no restart. Under `high-risk`,
`landing.high_risk_paths` lists the fnmatch globs of the repository's high-risk files.

`landing.status_card`, optional, makes the landing desk redraw the root's status card
from the ledger each pass the picture changes, as SKILL.md's status-card section
describes. Start the landing desk from the root's shell after its first status card.

`landing.adapter` names the merge queue's repository adapter, and the root writes it at
drive start along with `landing.high_risk_paths`. Use
`{"kind": "graphite-stack-enqueue", "script": ".agents/skills/submit-pr/scripts/stack-enqueue"}`
when the checkout carries that script, and `{"kind": "label"}` otherwise, for a queue that
takes the `merge` label through `ledger.py label`. The runner never picks one itself. `landing.checks`, optional, lists the
check registry in order, from `hold`, `precondition`, `rules-review`, `rulings`, `fidelity`, `finder`,
`merge-mode`, `approvals`, and `required-ci`; without it every check runs. On an adapter, a check,
or a high-risk setting it cannot run, the landing runner exits and posts `MQ-CONFIG-INVALID`,
ending with the exact `landing` members that fix every fault at once.

`deadlines.release_start_minutes` is the step between the blockers D20 posts, 10 by default.

`finder`, optional, turns on the orca desk's finder dispatch, O17. `model` is the
`codex-ask -m` model, `sol` by default; `concurrent` caps the passes running at once,
2 by default; `checkout` is the repo checkout codex reads in, `landing.checkout` by
default; `to` lists cci addresses that get every finder record besides the head's lane;
`repo` defaults to `landing.repo`; `timeout_minutes`, 60 by default, fails a pass with no
result. The runner reads the object every pass, so adding or changing it needs no restart.

Ordinary launches require `orca.sprite` and run `orca.sprite.launcher`, the
repository's `worker-launch.sh`, on a Sprite, with `orca.sprite.env` and `WORKER_RUN`
set by the runner. Neither `place=remote` nor `cpu=high` is required. The runner sets
no Sprite limit: the provider admits or refuses each one. `place=local` keeps a
launch on the Mac. Fable, incident, an incident clock fallback, and a `role=desk` or
`role=watch` brief launch locally whatever the line says.

Set `CC_REMOTE_CONFIG` in `orca.sprite.env`: without it cc-remote reads
`~/.config/cc-remote/config.yaml`. The runner reads its config only at start.
Missing `orca.sprite`, a lane name a Sprite refuses, and a Sprite launch whose
receipts prove worker-start never ran each fail with `LAUNCH-FAILED`; nothing
starts locally in its place. A Sprite launch with no ready line and no such proof
logs `SPRITE-UNVERIFIABLE` and launches nothing locally.
A Sprite launch that prints nothing is `UNVERIFIABLE` and is never relaunched.

Generated fix briefs are attachments on `orca.briefs.log`. The runner checks that
log at start and before every brief it attaches. When the log is unset or no longer
resolves, the runner switches to the drive's `briefs: <drive>` log, creating it if
missing, and logs `BRIEFS-LOG` naming the new id for the config. `alert.facts` names
the file whose contents go into each brief: drive checkout, apply authority,
target facts, and the drive's deploy inbox for `MECHANISM`, `FIX-LIVE`, and
`NOT-OURS` lines.

`orca.launch_env.ORCA_LAUNCH_PREPARE` is one shell command the launch script runs
in each worktree it creates, before the worker starts. Set it to the repo's code
generators, so a worker's first build finds the generated code in place. A command
that fails, or runs past `ORCA_LAUNCH_PREPARE_SECONDS` (default 120), never fails
the launch. The worker's brief then ends with the command and the path of its log.

```json
{
  "store": "/absolute/drive/actions",
  "drive": "<cci drive>",
  "alert": {"facts": "/absolute/drive/alert-facts.md"},
  "incident": {"case_lanes": 2},
  "orca": {
    "run": "<run id>",
    "desk_inbox": "/absolute/drive/inbox/orca-desk.md",
    "briefs": {"repo": "/absolute/drive/checkout", "log": "<briefs log id>"},
    "gc": "/absolute/drive/checkout/.agents/skills/orca/scripts/orca-gc",
    "launch_env": {
      "ORCA_LAUNCH_RUN": "<run id>",
      "ORCA_LAUNCH_REPO": "<Orca repo id>",
      "ORCA_LAUNCH_PARENT": "/absolute/coordinator/worktree",
      "ORCA_LAUNCH_PREFIX": "<drive prefix>",
      "ORCA_LAUNCH_ROOT": "/absolute/worktrees",
      "ORCA_LAUNCH_BASE": "origin/dev",
      "ORCA_LAUNCH_CLAUDE_ARGS": "",
      "ORCA_LAUNCH_BOOT_SECONDS": "180",
      "ORCA_LAUNCH_PREPARE": "<the repo's code generators, one shell command>"
    },
    "sprite": {
      "launcher": "/absolute/drive/checkout/.agents/skills/orca/scripts/worker-launch.sh",
      "env": {
        "WORKER_CC_REMOTE": "/absolute/drive/checkout/tools/cc-remote/bin/cc-remote",
        "CC_REMOTE_CONFIG": "/absolute/drive/checkout/tools/cc-remote/config.yaml",
        "WORKER_REF": "dev"
      }
    }
  },
  "stall": {"every_minutes": 5},
  "deadlines": {
    "start_minutes": 10,
    "launch_minutes": 15,
    "load_hold_minutes": 5,
    "hold_minutes": 15,
    "incident_mechanism_minutes": 10,
    "incident_pr_minutes": 15,
    "incident_ready_minutes": 20,
    "enqueue_minutes": 15,
    "release_start_minutes": 10
  },
  "finder": {"model": "sol", "concurrent": 2},
  "landing": {
    "repo": "Forge-AI/monorepo",
    "ledger": "<ledger id>",
    "checkout": "/absolute/monorepo/worktree",
    "holds": "/absolute/drive/holds.md",
    "fidelity_lane": "fidelity-desk",
    "merge_mode": "high-risk",
    "high_risk_paths": ["*migrations/*", "*.sql", "*backfill*", "*secret*", "*credential*", "infra/rows/*"],
    "adapter": {"kind": "graphite-stack-enqueue", "script": ".agents/skills/submit-pr/scripts/stack-enqueue"},
    "release_owner": "peer:<release session>",
    "release_cli": "tools/ci release",
    "applying": {"build": "tools/ci bk api /pipelines/{pipeline}/builds/{number}", "step": "apply-(?P<component>.+?)--(?P<env>.+)", "components": ["sandsql", "api", "executor", "rsw-*"]},
    "interval_seconds": 180,
    "status_card": true,
    "policy": {
      "rule": "prefix",
      "revision": "<#28601 revision>",
      "source": "Forge-AI/monorepo #28601, accepted prefix rule"
    }
  }
}
```

Start each process detached so it survives the root's compaction, in a dedicated
Orca terminal with coordinator identity or with `nohup`. After filling the config,
start both:

```sh
DRIVE='/absolute/drive'
CONFIG="$DRIVE/runner.json"
mkdir -p "$DRIVE/inbox"
nohup desk-runner.py run --config "$CONFIG" --desk orca > "$DRIVE/orca-runner.log" 2>&1 < /dev/null &
nohup desk-runner.py run --config "$CONFIG" --desk landing > "$DRIVE/landing-runner.log" 2>&1 < /dev/null &
```

Restarting either process uses the same records and is idempotent. Keep one process
per desk. Restarting a runner never means relaunching its workers.

Each process posts a `<desk> alive` cci `state` record to the drive watchdog when it
starts and every minute after, and its `restart` attr is the command to run from the
bound terminal when the `desk_liveness` hook or `desk-launch.py` reports
`LAUNCH-FAILED`. Every reader reads that record from cci, so neither runner writes a
heartbeat file and either may run on another machine.

For the cci cutover, the root adds `drive` to the runner config, confirms
`orca.desk_inbox`, and restarts both runners. Its records keep arriving over the
`cci` channel. The root owns that cutover.

To upgrade a running desk without a signal, run `desk-runner.py handoff --desk orca
--config "$CONFIG"`, or `--desk landing` for the landing desk, from the Run's bound
terminal. Every runner holds `$CONFIG.<desk>.lock` for its life, and a second
`run` on the same config and desk refuses to start. `handoff` reads the lock's holder
and writes its pid to `$CONFIG.stop`. That runner finishes its pass, removes
`$CONFIG.stop`, and exits 0. Only once it has exited does `handoff`
start the next runner itself, on the newest installed plugin with the same config and
desk, its output appended to `<desk>-runner.log` in the drive's state directory, so one
process per desk runs at every moment and a rotation is one command.

Only the terminal bound to the Run may call `worker-start`. Every pass the orca desk
reads the Run's coordinator from Orca and calls Orca, and starts each launcher, as that
terminal, whatever terminal the desk itself was started from. When the Run has no
coordinator or Orca finds that terminal gone, a desk started inside an Orca terminal rebinds
the Run to that terminal; one started outside any refuses to start and prints the rebind
command. When the coordinator disappears while it runs, it writes one `UNBOUND` line and
fails every launch at once with `LAUNCH-FAILED`, which no incident clock defers, so the
root launches it by hand with `orca-launch.sh`. After the root moves to a new terminal, run
`desk-runner.py rebind --config "$CONFIG"` there; the running desk follows on its next
pass and needs no restart. `show` prints the binding first.

## Actions and acknowledgements

Actions move from `accepted` to `started`, `completed`, and `verified`; `failed`
and `unverifiable` record failure or missing proof. For guidance relays, a send is
delivery evidence only. Question-reply actions currently complete on send and
carry no started/done instructions. Before acting on a guidance relay, the worker
sends a status on the thread named in the message; after acting it sends the
result, using its own dispatch:

```text
orca orchestration send --type status --thread-id <thread> --dispatch-id <its dispatch> --subject "started <key>"
orca orchestration send --type status --thread-id <thread> --dispatch-id <its dispatch> --subject "done <key>: <result>"
```

Carry the addressing and capability arguments from the worker's Orca preamble.
A relaunch offers the lane's container to the new dispatch. Its first ack takes
ownership at the next generation. Until that ack the old owner still owns the
container; after it, another dispatch's ack gets a stand-down reply and cannot
move the original action. A transfer changes logical ownership, never a session.
A done reply records completion; verification needs the action's external receipt.

The runner settles an `unverifiable` send through Orca `request-show` and
`--retry-request`, or the recipient's mailbox. An unparseable CLI response is
`unverifiable` too. A send to a dispatch that is no longer the lane's live one
stays `unverifiable`, emits `UNVERIFIABLE`, and is never resent. A launch with
neither a launch line nor a new receipt is `UNVERIFIABLE`, never relaunched
automatically.

With neither proof thirty seconds after a send to the lane's live dispatch, the
runner sends the relay again on the same thread, under the same key. It repeats
that once every thirty seconds until Orca holds a receipt or the message shows on
the thread, and a delivered resend emits nothing. A lane that already read its
mailbox leaves no message there, so a status the lane posts on the Run naming the
relay's R-number or cci sequence also counts. It acknowledges the relay as `started`
when its subject starts so and `done` otherwise, settles every send, and ends the
resends. After two resends all lost the same way the runner emits `UNACKED` once,
with the count, and its record's path holds the relay text; it sends nothing more.
A resend Orca rejects emits `SEND-FAILED`.

A relay record or a root reply can be any length. `cci post` keeps only the first
line of a text over 400 characters in the record, clipped, and writes the whole
text to a file the record names by path. The runner reads that file and relays
the whole text with its line breaks. A file whose first line is not the record's
text is an attachment, and the runner relays the record's own text. A record that
names a file the runner cannot read, such as one replicated from another machine
without its file, is refused as `RELAY-FAILED` naming the path, and nothing is
relayed. Post it again on the runner's machine.

## Orca passes and standing rules

Each pass settles detached launches, delivers accepted relays and launches,
reconciles lost sends, reads new Run messages, checks start and launch deadlines
and the incident clock, and writes changed views and escalation lines. A quiet pass writes nothing.
Stale mail, prompts, and liveness are swept every five minutes.

**O1. Launch through the runner.** It starts `orca-launch.sh` detached; the root
submits `launch` and never runs lifecycle helpers inline.

**O2. Settle launches from receipts.** Count a `ready` or `unsupervised` launch
line, or a new dispatch receipt when the log is empty. Other output fails the
launch. No output and no new receipt leaves it unverifiable.

**O3. Keep one Run reader.** The orca runner calls
`orca orchestration inbox --terminal run:<run> --limit N --json`. It processes
sequences after the saved cursor oldest first, skips heartbeats, and saves each
sequence in the `desk-runner` container's `facts` under `inbox:<run>`. Full pages expand
until the cursor is covered. A first read saves the newest sequence without
replaying read history; restarts resume there, including messages marked read elsewhere.

Each pass takes the Run's oldest unacknowledged Delivery with
`orca orchestration check --run <run>` before the read and acknowledges it with
`--ack <delivery id> --peek` once the read is relayed. Relayed message ids are saved
under `relayed:<run>`. A Delivery message with no saved id is relayed before the
acknowledgement, or logged `UNRELAYED` when its type has no relay.

**O4. Only the root answers a worker's question.** Each question or escalation
goes to root as `DECIDE` with the worker's question and options. The runner never
answers one; only this drive's root answers, through `relay --reply-to` or a cci
reply with `--re`.

**O5. Address the current dispatch.** Relays use the current receipt and
`worker-show`; completed or failed dispatches receive no new relay. Replies
address the original question id through `--reply-to`.

**O6. Never end a session.** The runner never stops, signals, releases, or closes
Claude, Codex, Orca, a terminal, a PTY daemon, or a supervisor. Finished sessions
stay open. An `OUTCOME` line does not authorize cleanup. The sweep names each
newly settled dispatch once in a `RECLAIM` line with `desk-runner.py gc` and then
the `orca.gc` command, and the root runs both under R195. `gc` closes only the
terminal a settled launch's `<lane>.terminal.json` receipt names, idle at an empty
prompt, and keeps and names every other.

**O7. Keep action records.** The action record carries acceptance,
delivery, start, result, and verification. Neither a sent message nor a moved
inbox cursor proves the lane acted.

**O8. A stale heartbeat never authorizes a relaunch.** The sweep escalates a
non-live dispatch as `LIVENESS`; resume its existing session in place. The
runner does not infer that missing liveness means the session ended.

**O9. Never re-brief.** Update the attachment with
`ccn log append <briefs log> --entry "<what changed>" --attach <lane>.full.md --replace`,
then submit its pointer from `ccn attachment path <briefs log> <lane>.full.md`
through `relay`.

**O10. Never answer a stale dispatch's question to its replacement.** Preserve
the original question id. The runner does not fence stale senders; do not treat
it as proof that this rule is enforced.

**O11. Treat a capacity fallback like an ask.** Workers send `question` or
`escalation` when `ask` returns `capacity reached`; both escalate as `DECIDE`.

**O12. Pause ten seconds between Run reads.** The orca runner sleeps ten seconds
between passes; the landing process sleeps for its configured interval.

**O13. A prompt is a bug, and the prompt watch finds it.** `prompt_watch.py` is the
one reader of `agentWait` and posts each prompt to the root itself. The sweep reads its
last poll and emits `PROMPT-WATCH-DOWN` while that poll is over two minutes old. It
never types a guessed answer. Stale unread mail gets one terminal wake per message,
typed only at the lane's plain empty prompt; completed or failed dispatch mail emits
`STALE-MAIL`.

**O14. Refuse launches under load and tell the root to move it.** Before each Mac
launch, the runner reads the 1-minute load average. Above the core count no launch
starts, and each ten minutes of it posts one `LOAD-HIGH` blocker to the root.
Incident and `--owner-directed` launches fail at once with `LAUNCH-FAILED`. Any other
launch stays accepted and `show` marks it `HELD`; after `deadlines.load_hold_minutes`
it fails with a `LAUNCH-HELD` escalation and a Run mailbox message. It never kills a
worker to lower load.

**O15. Preserve the codex and incident routes.** `sol`, `codex`, and `gpt-*` use
Orca's codex agent on the standard tier. `incident xhigh` uses `gpt-6.1-sol`, a
`--no-parent` worktree, and a terminal command with
`--dangerously-bypass-approvals-and-sandbox` and `-c service_tier=fast`. An `unsupervised` result escalates; it is not a failed
launch to repeat. Leave Orca's runtime defaults unchanged.

**O16. Run the stall watch on its cadence.** While `runner.json` holds a `stall` object,
the orca runner starts `stall_watch.py --config <runner.json>` detached every
`stall.every_minutes` (default 5), never while the last pass still runs, and logs it to
`stall-watch.log` beside `orca.desk_inbox`. It reads the object on every pass, so adding
it needs no restart. Each stall the pass finds reaches root once as an `urgent`
`STALL` record naming its owner and next step; SKILL.md "The stall watch" lists the kinds.

**O17. Dispatch a finder on every head.** With `finder` in the config, each pass reads
the drive's `head` records past its saved sequence and queues one finder pass per PR and
full head; two records naming one head queue it once. The newest queued head of each PR
starts at once, beside the fidelity desks, while fewer than `finder.concurrent` run, and
an older queued head of the same PR settles as superseded. A head some lane's `finder:`
record already names starts nothing. Each pass runs `finder.py` detached, which runs
`codex-ask --schema findings` from `reference/finder-desk-brief.md` over the head's own
commits against the base branch's current tip, or only since the head the PR's last finder
record graded, skipping a patch-identical restack and a head the PR no longer carries, and posts `finder: ok <head> …` or
`finder: major <head> <n> open: …` under lane `finder-desk` to the head's lane, root and the
runner, whose landing check reads finder records by address. A pass
that fails, whose process exits with no result, or that writes no result within
`finder.timeout_minutes` frees its slot that pass and runs again, up to three attempts;
the third failure posts one `FINDER-FAILED` to the root, and the lane then runs that head's
pass by hand. A head the landing check refuses `FINDER-PENDING` with no finder record and
no pass waiting or running queues one again. While passes wait and none settles for 15
minutes, the runner posts one `FINDER-DESK-STALLED` to the root, naming how many wait and
each running pass's age. `show` lists each queued and running pass.

## Landing passes

Every pass runs `ledger.py refresh` and `reconcile`, then reads the rows. It
reconciles uncertain enqueues, verifies prefixes whose rows are `landed` by squash
on the base, and routes restacks. It gates every tracked open stack tip in
parallel through the adapter's check, `stack-enqueue <tip> --check [--whole]` under
`graphite-stack-enqueue`, in `(priority, root PR)` order.
Under `graphite-stack-enqueue`, a check or enqueue that exits 5 with no verdict and a transient network error (TLS handshake
timeout, connection reset, DNS failure, HTTP 5xx) runs again, three attempts 20 seconds apart,
and only the third failure posts its one `GATE-FAILED` or `ENQUEUE-FAILED`.

Each exact set of prefix heads gets one enqueue action. Only attempts proven to
have enqueued nothing permit another attempt for those heads. Accepted enqueues
run in one parallel wave per priority, lowest first, each through the adapter's
enqueue after the check registry's `hold` check reads the holds file fresh, and
none runs while the file carries a line the runner cannot read. The holds file stays the root's interface. A `hold` label on a PR holds it too,
and the runner never adds or removes that label.

A prefix is accepted only when every PR in it has a fidelity ok at its current
head. The runner reads `cci tail --kind=review --lane <fidelity lane> --pr <n>`,
keeps the records that start `fidelity: ok` or `fidelity: missing` and name
that head by a short sha, and takes the newest; only `fidelity: ok` passes. An
ok that names an older head, or no head, does not count, so the fidelity lane
names the head it graded in every record, as in `fidelity: ok F307 at 9c0b891`.
Without an ok the runner enqueues nothing from the prefix and posts one cci
`blocker` per head and grade to the PR's lane and the fidelity lane. It checks
again just before the enqueue call and completes the action as `blocked` when
the ok is gone. `landing.fidelity_lane` names the lane, with no default: the landing desk
refuses to start without it and posts `FIDELITY-LANE-UNSET`. The
runner re-reads it from `runner.json` on every use, so an edit needs no restart. It
also takes a list of lane names, `"fidelity_lane": ["fidelity-desk-6", "fidelity-desk-7"]`,
for a drive that shards grading: a grade from any listed lane counts, the newest
across them decides, and a blocker goes to the PR's lane and every listed lane.
A head no grade names takes
an ok carried from an older head an ok names, whose own newest grade is ok, when
`git patch-id --stable` of both heads' diffs against the PR's base, read from
GitHub's compare, is equal. The carried grade reads `fidelity: ok at <head>,
patch-identical to <graded head>` under the graded record's seq. A compare GitHub
refuses for the PR's own head logs `PATCH-READ-FAILED` once and carries nothing.

The runner posts no `fidelity` status. Its check registry alone enforces holds,
rules-review, and fidelity, so a hand `gt merge`, a web-UI enqueue, or a hand adapter
call bypasses all three. `mq.py --config <runner config> why <n>` prints each check's
state at the PR's head. The fidelity lane keeps naming the head in each `review` record.

`landing.merge_mode` decides which ready prefixes wait for the owner. Under `auto`
none waits. Under `every` each PR waits, and under `high-risk` each PR whose changed
files, read once per head from `gh api --paginate repos/<repo>/pulls/<n>/files`, match a
`landing.high_risk_paths` glob, such as migrations and SQL, backfills, credentials and
secrets, the release pipeline, storage formats, and infra rows. A
waiting PR's prefix enqueues nothing, and the runner asks one merge card per PR and head
on the root's board and in the root's Slack status thread and logs one `MERGE-ASK` naming the card and the record that answers it.
With `landing.merge_ask_surface` set to `board` (default `slack`), the runner posts and edits no card, and the root asks every merge
question on its own board. On that surface the runner carries the owner's board taps itself. An approval on a `pr-groups` row
at an older head of an open PR moves to the PR's head through `cc-present verdict carry` once records call each move between the two
heads patch-identical, `head` records reading `<new> (was <old>)` and fidelity carries alike. One `TAP-CARRIED` note naming both heads
goes to root and each lane `landing.board_lane` names. A new head that a record names but no such chain reaches keeps the tap at the old
head, and one `TAP-NOT-CARRIED` note to the same lanes says the board needs a new row. A board that does not read, or a carry
cc-present refuses, logs `TAP-CARRY-FAILED`.
Under `every` the runner then checks only heads a root `go` names: no check or enqueue call names any
other PR or sweeps it in, and a PR stacked on it waits. Its blockers and `MERGE-ASK` come from the pass's `ccx vcs pr status` read instead. `landing.checks_wait_for_go`, a cci sequence number,
pauses the whole gate until a root `go` record comes after it: no `stack-enqueue --check`, `MERGE-ASK`, or
enqueue runs, one `GATE-PAUSED` says so, and refresh, reconcile, head reads, restack routes, and an approved, green incident fix's plain squash go on. The prefix enqueues on
the pass after a root `go` record names every waiting PR and its current head by a
short sha. A changed-files read that fails logs `FILES-READ-FAILED` once per head and
enqueues nothing from that prefix until it reads.

After a prefix lands, the runner routes `ccx vcs stack submit` to the owner of the
first PR above it. Orca lanes receive a relay; other lanes receive
`cci post --drive <drive> --lane landing-desk --kind blocker --topic <pr> --to <lane> --text "<restack route>"`.
It verifies a restack route when the child's head moves or the child lands. The
landing-desk lane never enqueues or duplicates these restack routes beside the runner.

A row that lands carrying the `merge-fast` label, with a `Release-With:` line in the
PR body, gets one cci record in the pass that reads the landing:
`cci post --drive <drive> --lane desk-runner --kind release --to <landing.release_owner>,root --topic <pr> --pr <pr> --id release:<pr>:<step> --text "<squash, components, start the release now>"`.
The runner reads the label from the row's `labels` and the body with one
`gh pr view <pr> --json body`. From then on each pass reads `cci tail --kind=release`
and the root's records. A `release` record on this drive from any lane but
`desk-runner`, stamped at or after the landing, that names one of the components with
`--target` or the PR with `--pr` settles the landing. `cci tail` also returns peer
mail from other drives, and a `release` record on another drive settles nothing. A
record this drive's root posts with `--re` or `--resolves` naming the release record
or one of its blockers settles the landing too.

Until then the runner posts one `blocker` to the same two addresses at each
`deadlines.release_start_minutes` step after the landing, with the minutes elapsed.
A landing first read after a step has passed gets the `release` record then and its
first blocker at the next step. Each record's `--id` names its step, so a runner that
stops between the post and its own save reposts under the same id, cci returns the
stored record, and no step is posted twice.

Each landing is judged once, by the first pass that reads it, and the landing desk's
store keeps the set it has read across restarts and upgrades. The desk's first pass
records every landing it reads as history and sends nothing, and a landed row with no
`landed_at` is not read until it has one. A `merge-fast` landing whose body has no
`Release-With:` line is read once and sends nothing.

An incident fix PR, one from an `incident-` or `outage-` lane, a fix lane on the
incident clock, or named as an open incident's repair, is merged by the runner with
`gh pr merge <n> --squash --match-head-commit <head>` in the pass its head is
approved and green, and never enqueued. Its landing owes a release whatever its
label, posts a blocker every `deadlines.incident_release_minutes` while an incident
clock runs, and settles only on a `release` record with `--build` and `--target`.
Once the settling record names a build, each pass reads
`tools/ci release live <id> --json` and posts one `RELEASED` `fix-live` record to the
root alone when every component reads live.

A `BLOCKED` head routes at most once per head, and only for red CI, a merge
conflict, a changes-requested review or fidelity grade, or a queue ejection. A
pending check, a missing approval, an ejection blaming a PR outside the stack,
and a PR held, labelled `hold`, or stacked above such a PR never route. A PR
based on another open PR's branch gets no failing-required route unless a
status other than a gate's, such as `buildkite/test`, is red on its own head. A queue drop that blames a PR outside the stack
re-enqueues the same head after the root's next `go` record naming the PR. The runner
re-runs the gate immediately before creating the route, suppressing a blocker
that has already cleared. Orca delivery can occur later; the gate is not re-read
at that later send. The landing desk retains `ledger.py route` for red CI and
conflicts, but does not repeat the runner's per-head gate messages.

The first landing run seeds the policy from config. Each later `policy` command
must name the current revision with `--supersedes`; another predecessor fails
with `STALE-POLICY`. A stale-checkout ruling such as L260 cannot replace #28601's
accepted prefix rule by omitting that edge. The root changes policy by command,
never by editing a rendered inbox view.

## Escalations

Each line uses `<HH:MM Pacific> <KIND> <key> <lane>: <text>`, with Pacific `HH:MM`
and no zone label. The lane slot can name `landing`, `runner`, or a PR. Keys
identify the cause; `DECIDE` uses a question id or `hold:<slug>`.

| Kind | Root action |
|---|---|
| `DECIDE` | For `hold:<slug>`, decide with the named lane ahead of unrelated owner questions. For a worker question, answer with `cci post --kind answer --to <lane> --re <DECIDE seq>`, or `relay --reply-to <msg id>`. |
| `INCIDENT` | Ratify the fix launch, start evidence and incident-doc lanes, fence the target, and start account comms plus `#outage` for a platform-wide incident; adopt the fix lane into the incident executor. For a line that says a fix does not cover a case, ratify the case lane it launched and keep the reporter told that the case is still open. A `DECIDE unfixed:<msg>` line lists cases the runner did not launch; post the launch line of each case worth a lane. |
| `INCIDENT-OWNED` | None. Another owner holds the incident the line names, so the runner launched nothing; check the named record agrees. |
| `INCIDENT-CLOCK` | Tell the owner the line in one plain line of your next reply, ahead of any other work in that turn, never as a Slack post. Ratify the `<lane>-opus` fallback the line names. When the line says none was launched, launch one unless it names an Opus backup already on the lane. For `in-process`, start `<lane>-opus` in that turn as an `Agent` of type `long-running:lane-ship` on opus in fast mode with the brief the line names. For `ready`, clear what holds the missing milestone. The key is the lane. |
| `MERGE-ASK` | One line: `#<n> <head> waits on the owner's tap on `merge:<n>:<head>``, where the card is, and the merge mode. The runner already asked that merge card on the root's board and in the root's Slack status thread, with `Merge (Recommended)`, `Hold`, `Other`, and `Explain more` (93037c3); never send a second card or an `AskUserQuestion`. On a `Merge` tap, post `cci post --kind go --pr <n> --text "merge #<n> at <head>"`, and for each reserved row the line names, run the `rulings.py authorize` command it gives, since a go never authorizes a row; on a `Hold` tap, run `holds.py add --pr <n>`; the text the owner types in `Other`'s modal arrives as the card's capture, which merges nothing and is a capture to classify; on an `Explain more` tap, post `cci post --kind ask --to root --text "explain more: merge:<n>:<head>"` and answer in plain words with `cc-present reply --block <the card's waits id> --body <answer>`, which replies on the board and under the Slack card with the link back to it and resolves nothing. The key is the PR and head. |
| `MERGE-CARD-FAILED` | A merge card did not post: register the root session with `drive.py start --drive <drive> --ledger <ledger>` from that session when the line says it is missing, otherwise read the error; the next pass tries again. |
| `RULING-ASK` | Ruling confirmations queue for the next board round, never one card each (standing rule 23: 5 to 10 cards a round, foundational picks first). When the owner can take a round, run `rulings.py round --drive <id>`: it opens a new round on the root's cc-present board with at most ten queued rulings, foundational rows first, and leaves out a row the owner's own pick, tap or reply on a card made. Never as an `AskUserQuestion` or a bare Slack card. An `Explain more` tap resolves nothing: answer in plain words with `cc-present reply --block <card id> --body <answer>`, which also replies under the Slack card with a link back to it. A pick or a note on a ruling is a capture: `Right` runs `rulings.py confirm <key> --tick all --capture <digest>`, and `Some wrong` waits for the owner's note naming the lines. Silence decides nothing. The key is the round. |
| `SLICE-UNREAD` | Read the named `rulings.py slice --refresh` failure, fix the register or the brief's `Register slice:` line, run the refresh by hand, and relay the fresh slice to the lane, which launched without it. The key is the launch. |
| `MERGE-MODE-UNSET` | Ask the owner which merge mode the drive runs in, record the answer, write `landing.merge_mode` to `runner.json`, and start the landing runner again. |
| `MQ-CONFIG-INVALID` | Add the members the line ends with to the `landing` object of `runner.json`, replacing any of the same name, adjust the suggested `high_risk_paths` globs to the repository, and start the landing runner again. |
| `FILES-READ-FAILED` | None while it is brief; the runner reads the PR's changed files again on its next pass. |
| `LOAD-HIGH` | Move the load off the Mac in that turn: stop or move each local pass, backfill, dry run, or bulk rewrite to in-region compute, and launch a refused incident lane in-process meanwhile, as an Agent named for the refused lane, which the pack's Agent hook lets through for 30 minutes. Never wait the load out. The key is the ten-minute window. |
| `ALERT-FAILED` | Correct the alert line's slug, link, or form and append it again. |
| `HOLD-FAILED` | Correct the hold or unhold grammar and append the line again. |
| `RELAYED` | None; an inbox relay line was accepted for that lane and is delivered in the same pass. |
| `RELAY-FAILED` | Fix the named relay's lane, key, or form, or launch the lane, and send a corrected relay under a new key. When the line names a file that is not readable, post the relay again on the runner's machine. |
| `LAUNCHED` | None; the launch is verified and the line names its dispatch and terminal. |
| `DEADLINE` | Resolve the named action's missing delivery, start, launch, or enqueue proof. A lane that posted a `head` or `review` record for the relayed PR since the relay, or sent a heartbeat after it, counts as started and draws none; a lane that heartbeat within thirty minutes before the relay draws none for thirty minutes. |
| `UNACKED` | Read the lane's Run messages first; if it never acted, deliver the relay text at the record's path by hand. The send and two resends lost their response, the lane never named the relay, and the runner sends nothing more. |
| `UNVERIFIABLE` | Reconcile the missing external receipt; never repeat the mutation blindly. |
| `SPRITE-UNVERIFIABLE` | Inspect the named receipt directory and any Task or Dispatch before relaunching; a remote worker may be running, and the runner launched nothing locally. |
| `OUTCOME` | Consume the worker's `worker_done` result. |
| `FIX-LIVE`, `MECHANISM`, `READY` | Read the lane's status milestone by message id; the line includes its subject and up to 300 body characters. |
| `PROMPT-WATCH-DOWN` | Run the line's `prompt_watch.py start` command; until it polls again no prompt on this drive is being read. |
| `LIVENESS` | Inspect the non-live dispatch; preserve its session. |
| `STALE-MAIL` | Resolve unread work on a completed or failed dispatch. |
| `RECLAIM` | Run the line's two commands: `desk-runner.py gc` closes each idle terminal a settled launch's receipt names, and the `orca.gc` command closes the rest and removes each finished worktree under R195. |
| `STALE-POLICY` | Reconcile the rejected revision with the accepted predecessor. |
| `LAUNCH-FAILED` | Read the named launch failure before issuing any new action; for an inbox launch line, relay to the live dispatch or append a corrected line under a new key. |
| `UNSUPERVISED` | Arrange the launched lane's reporting without launching it again. |
| `SEND-FAILED` | Resolve a send that failed three explicit attempts, or a resend Orca rejected. |
| `ENQUEUE-STRANDED`, `ENQUEUE-UNSETTLED`, `ENQUEUE-FAILED` | Resolve the queue result from current evidence. |
| `ROUTE`, `UNOWNED` | Supply the missing route or owner. |
| `GITHUB-BACKOFF` | None while it lasts; the landing desk reads nothing from GitHub until the reset the line names, and accepted enqueues and root `go` records run on the first pass after it. A line every quota window means another consumer is spending the installation's quota; find it before the next window. |
| `QUOTA-READ-FAILED` | Read why `ccx vcs gh -- api rate_limit` failed; until it reads again the desk runs without its back-off. |
| `HOLDS-UNREAD` | Rewrite the quoted holds-file line as `#<pr> <why>`, `lane:<name> <why>`, `<lane> HOLD <why>`, `HOLD <lane> <why>`, `hold:all <why>`, or `release #<pr>`, or delete it. When the line names a lane no ledger row carries, hold each PR as `#<pr> <why>` instead. The landing runner gates and enqueues nothing while the line stands. |
| `RELEASED` | Forward the line to the owner verbatim; it is the only publish line the root sends. |
| `RELEASE-HELD-AFTER-LANDING` | Lift the hold the line names and start the landed fix's cut now; the other PR gets its own cut when it lands. |
| `PR-SILENT` | Read the code state in the line, or run `drive.py lane-status <lane>` for it now. With dirty files or a fresh commit, order the lane to push and open its PR now; with a clean, detached, or missing worktree, start `<lane>-opus` on the lane's brief and tell the owner. Resolve the line once the PR is open or the lane is stood down. |
| `CUT-SILENT` | Ratify the `manual-release-<component>` lane the line names, or run its hand command in a lane when none launched; never order the silent release lane again. |
| `PARKED` | None while the PR stays parked; its lane has no live dispatch or session, so its conflict routes to no one. Relaunch or reassign the lane to have the conflict routed. A lane silent on cci for 60 minutes is dead instead, and its blockers reach root as `owner lane dead`. |
| `DEFERRED` | Work each held escalation the line names, now that the incident clock has ended. |
| `SQUASH-FAILED` | Read GitHub's refusal of the incident fix's plain squash; fix what it names, and the runner merges the next approved, green head. |
| `RELEASE-FAILED` | Start the release the line names, or post its `release` record when it is already running. When the line says cci refused the record, the `peer:` session has most often ended, so correct `landing.release_owner` and restart the landing runner. When it says cci printed no sequence, read what cci printed; the runner posts the record again under the same `--id` on its next pass. |
| `DECISION_GATE`, `HANDOFF` | Act on the worker's gate or handoff. |
| `ORCA-INBOX` | Resolve the mailbox error without restarting Orca. |
| `ORCA-UNREACHABLE` | None while it is brief; the runner retries with backoff and changes no dispatch or relay meanwhile. With no `ORCA-BACK` after ten minutes, check `orca status` and tell the owner; never restart Orca. |
| `ORCA-BACK` | None; Orca answers again and the runner resumes every relay and launch where it stopped. |
| `FINDER-DESK-STALLED` | Read each running pass's `job.log` the line names; a pass hung past its use, or a slot cap too low for the queue, is why nothing settles. Raise `finder.concurrent` in runner.json, or fix what the logs name; the runner reads runner.json again each pass. |

The store deduplicates escalation keys. Liveness keys include the hour;
`ORCA-INBOX` keys include a ten-minute bucket, so an unchanged fault can recur. Routine
quiet polls do not wake a model.

The runner never edits worker code, mutates production, writes Slack, invents
scope, or derives authority from a branch prefix or a display name. The root
owns decisions and the holds file; lanes own their worktrees.
