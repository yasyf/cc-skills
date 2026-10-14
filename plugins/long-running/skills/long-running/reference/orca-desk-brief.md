# The orca desk runner

`desk-runner.py` owns Orca worker traffic and ready-prefix landing. Run two detached
processes against one config and store: `run --desk orca` and `run --desk landing`.
No model-operated orca-desk sits between the root and Orca. Where the checkout
carries `stack-enqueue`, the landing runner runs D3, D14, D16, and D20; the
landing-desk lane keeps the duties in [its brief](landing-desk-brief.md).

*Prevents G130's `AmiBake` launch being lost behind a desk handoff, R620 going to a
superseded desk, and GO carrying no start deadline (2026-10-01 audit, Brief 3 and
ranked fix 3).*

## Root discipline

Issue commands with the drive's R/L numbering. Submit a key once per action;
submitting it again in the same lane returns the existing action. Never `SendMessage`
a desk.
A relay or a reply posted on cci can be any length and span lines; post it whole, as one record, and the runner relays all of it.
Keep every other inbox line under 400 characters; put evidence in a file or cc-notes and leave a pointer in the line.

```text
desk-runner.py relay --config C --key R<n> --lane L --text T [--reply-to <question msg id>]
desk-runner.py launch --config C --key R<n> --lane L --model M --effort E --brief PATH [--owner-directed]
desk-runner.py policy --config C --key L<n> --landing prefix|whole --revision REV --source TEXT --supersedes <current revision>
desk-runner.py rebind --config C
desk-runner.py show --config C
```

A relay can also be one line appended to the desk inbox. Set the required
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
flight fails the same way on the next pass, never later as a `DEADLINE`.

A launch can also be one line in the same inbox, in this form only, one launch per
line, with nothing after the brief path:

```text
R<n> (<time>) orca-desk: launch <lane> [NOW] <model> <effort> brief=<absolute path>
```

The runner does what `launch --key R<n>` does with the same model, effort, and
brief; `NOW` is `--owner-directed`, so under load the launch fails at once instead of
waiting out the hold. An incident
fix lane is `R<n> (<time>) orca-desk: launch <lane> NOW incident xhigh brief=<path>`;
only `incident` runs on the fast tier, and plain `sol` is the standard tier. A line without `R<n>` takes the key
`inbox@<byte offset>`. A verified launch logs `LAUNCHED <key> <lane>: dispatch
<ctx> terminal <handle>`. The runner logs `LAUNCH-FAILED <key> <lane>: <reason>`
and launches nothing for a lane with a live dispatch or a launch still in flight,
a key that already holds an action, a model or effort `launch` refuses, or a brief
that is not a file. A line that says `orca-desk: launch` outside the form logs
`LAUNCH-FAILED <key> inbox`.

`desk-launch.py incident <name> <fix-brief> [<evidence-brief>]` and
`desk-launch.py lane <lane> <model> <effort> <brief> [--now]` post these lines for the
root: each takes the next `R<n>` past the root's newest `orca-desk:` record, posts
with `--to orca-desk`, waits for `LAUNCHED`, `LAUNCH-FAILED`, `LAUNCH-HELD`, or
`UNVERIFIABLE`, and prints the runner's line with the dispatch and terminal.

A launch or a lane is withdrawn with one line naming its launch key, `R<m>` or a case
launch's `unfixed:<msg>:<n>`, or the lane itself:

```text
R<n> (<time>) orca-desk: withdraw R<m>
R<n> (<time>) orca-desk: withdraw <lane>
```

The runner fails a launch it accepted and has not started, such as one held on load,
and stops a launched dispatch with worker-stop, as `orca-gc --done` does. It logs
`WITHDRAWN` and marks the lane withdrawn, so the lane's `UNFIXED` lines launch no case
lane and Orca's offer to dispatch it again logs `WITHDRAWN`, not `DECIDE`. A launch
still starting, a settled launch with no dispatch, a key or lane that holds nothing, or
a worker-stop Orca refuses logs `WITHDRAW-FAILED`.
A root `cci post --kind withdraw --re <seq>` whose record is a launch line addressed
to `orca-desk` withdraws that launch the same way. Any other record carrying `--re`
is a reply, never a directive, so it logs no `DIRECTIVE-FAILED`.

Each root cci record carrying `--re` is read as a reply. One to the runner's
`DECIDE` on a worker question is relayed as the reply to that question. One to a
lane's own record, such as its cci ask, is relayed as the reply to that lane's open
Orca question, which the cci answer superseded. A lane with no open question gets
nothing, since it reads the answer on cci.

A monitor transition and an incident launch are each one line in the same inbox:

```text
orca-desk: alert <slug> <link> :: <what fired>
orca-desk: incident <slug> <link> reported=<HH:MM> :: <what fired>
```

`monitor-watch.py --alert-inbox` writes `alert` lines. The runner records the
transition as `ALERT` and launches nothing; a repeat relays to a live fix lane.
The alerts desk writes an `incident` line only when it judges a lane necessary,
and the Slack watch lane writes one for each incident report. For an `incident`
line the runner fills `reference/alert-fix-brief.md` with `alert.facts`, attaches
it to the briefs log as `<slug>-fix.full.md` with `ccn log append --attach
--replace`, and launches `<slug>-fix` on incident xhigh at once on that
attachment's `ccn attachment path`. Nothing goes under `~/.claude/scratch`. A brief that fails
to attach logs `INCIDENT` with the ccn error and launches nothing. A repeat relays
to the live lane and keeps its brief. The root ratifies from `INCIDENT`: evidence
and incident-doc lanes, target fence, and comms in the affected account channels
and, for a platform-wide incident, `#outage` under R16. The evidence lane attaches
`<slug>-evidence.md` to the same briefs log, where the fix brief tells the fix
lane to read it.
The incident executor can adopt the launched lane with `--adopt fix=<lane>`.
A malformed line logs `ALERT-FAILED` or `INCIDENT-FAILED` and launches nothing.

Every incident fix lane the runner launched is on a clock from its verified launch.
A lane is one when it launched on the `incident` alias, or when its brief's `ccx:`
line says `role=incident` or names an `incident=`. A brief that names another role,
such as `role=evidence`, is never clocked, and neither is `incident=none`. On each
pass the runner reads the lane's own cci records, posted as `<name>` or `<name>-fix`,
and its `mechanism:`, `ready:`, and `fix-live:` Run statuses:

| Minutes since launch | Missing | The runner |
|---|---|---|
| `deadlines.incident_mechanism_minutes`, default 10 | a `mechanism` record or status, or a `report` or `incident` that starts `MECHANISM` | launches the Opus fallback and logs `INCIDENT-CLOCK <lane> mechanism` |
| `deadlines.incident_pr_minutes`, default 15 | an `opened` record | launches the fallback unless it already did, and logs `INCIDENT-CLOCK <lane> pr` |
| `deadlines.incident_ready_minutes`, default 20 | a `ready` record or status, or a `report` or `incident` that starts `READY` | logs `INCIDENT-CLOCK <lane> ready` with the missing milestones and the minutes each phase took |

A fallback whose Orca launch fails is launched once more under `<key>:retry`. After a
second failure, while the lane's clock runs, the runner logs
`INCIDENT-CLOCK <lane> in-process: the Orca launch of the Opus fallback <lane>-opus failed twice: <reasons> | root: start <lane>-opus in-process now, ...`
with the fallback brief's path, and launches nothing more for it.

A later milestone counts for the earlier ones, so an opened PR also settles the
mechanism threshold. Each threshold logs once per launch. The clock stops at `ready`,
at a `fix-live`, `not-ours`, `stopped`, or `stand-down` record, at a `fix-live:`
status, or when the dispatch settles. A record posted before the launch does not count.

```text
INCIDENT-CLOCK <lane> ready: not ready 20 min after its launch at 11:02, limit 20 min; missing PR, ready; launch to mechanism 7 min, mechanism to now 13 min
```

The fallback is `<lane>-opus` on opus xhigh in Claude fast mode, launched on the Mac
as an `--owner-directed` launch. Its brief is the lane's own brief, unchanged, under
a header that says why the clock started it, that it works in parallel and the first
PR wins, and that it posts on cci as `<lane>-opus` and addresses `<lane>` and root.
The runner attaches that brief to the briefs log as `<lane>-opus.full.md`. A `-opus`
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

A case a fix does not cover is an incident lane, never backlog. The alert fix brief
and the executor's fix brief make every incident fix lane reproduce the reporter's
own case before it reports ready. The lane closes its ready report and its
`worker_done` with one `UNFIXED <lane>: <case>` line per known case the fix does not
cover, or `UNFIXED <lane>: none`. When an incident fix lane's `worker_done` message, or its
`ready:` or `fix-live:` Run status, carries such lines in its body, the runner
launches `<first>-case<n>` on incident xhigh for each case in the same pass, with no
root turn. `<first>` is the lane's name without its trailing `-opus` and `-case<n>`
parts, so every case lane of one incident sits beside the first fix lane. The runner
fills `reference/unfixed-case-brief.md` with the case verbatim, the reporting lane,
the path of that lane's brief, and the PRs its cci `opened` records name. It attaches
that brief to the briefs log as `<first>-case<n>.full.md` and logs:

```text
INCIDENT <lane> <time>: its fix does not cover: <case> | case lane <first>-case<n> launching on incident xhigh, brief <path>; root: ratify, and keep the reporter told that this case is still open
```

Only a line that names the reporting lane counts, and `UNFIXED <lane>: none`
launches nothing. A case launches once across a lane, its `-opus` fallback, and its
case lanes, however often and by whichever of them it is reported. A case whose
launch is refused, or whose brief does not attach, logs `not launched` with the
reason and launches on the next report that names it. The case brief carries the
same reproduce step and `UNFIXED` field, so a case lane's own uncovered cases launch
further case lanes, and the clock above covers each one. A lane that is not an
incident fix lane launches nothing this way.

*Prevents the resume fix of 2026-10-13. It was reported fixed after a test on a
neighbouring case while the reporter's own thread still failed, and two edge cases
the fix lane had named sat as backlog for two hours until a customer hit them.*

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

The root resolves it with the named lane before or instead of any open
`AskUserQuestion` on another subject. That unrelated question goes to a
non-blocking board or waits. The line reaches the root over the `cci` channel as
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
`UNBOUND`, `UNOWNED`, `LOST`, `INCIDENT-CLOCK`, `HOLDS-UNREAD`, and `LOAD-HIGH` use `blocker`, any `*-FAILED` uses
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
carries. Set the accepted prefix policy's revision to #28601's revision.

`landing.release_owner` is required by `run --desk landing`, which exits on a config
without it. It is the cci `--to` address of the lane that starts releases, such as
`peer:<session name>` for a session bound to another drive.
`deadlines.release_start_minutes` is the step between the blockers D20 posts, 10 by default.

`orca.sprite` is optional. With it, a launch whose brief's `ccx:` line says
`place=remote`, or `cpu=high` with no `place=`, runs `orca.sprite.launcher`, the
repository's `worker-launch.sh`, on a Sprite, with `orca.sprite.env` and `WORKER_RUN`
and `WORKER_REMOTE_ATTACH` set by the runner. The runner sets no Sprite limit: the
provider admits or refuses each one. `place=local` keeps a `cpu=high` launch on the Mac.
Fable, incident, an incident clock fallback, and a `role=desk` or `role=watch` brief
launch locally whatever the line says, and so does every launch when `orca.sprite` is
absent.

Set `CC_REMOTE_CONFIG` in `orca.sprite.env`: without it cc-remote reads
`~/.config/cc-remote/config.yaml`, and every Sprite launch falls back to local. The
runner reads its config only at start. A lane name a Sprite refuses, and a Sprite
launch whose receipts prove worker-start never ran, a provider capacity refusal among
them, each start the same launch locally and log `SPRITE-FALLBACK` with the reason. A Sprite
launch with no ready line and no such proof logs `SPRITE-UNVERIFIABLE` and launches
nothing locally.
A Sprite launch that prints nothing is `UNVERIFIABLE` and is never relaunched.

Generated fix briefs are attachments on `orca.briefs.log`. `alert.facts` names
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
  "view": "/absolute/drive/inbox/runner-state.md",
  "alert": {"facts": "/absolute/drive/alert-facts.md"},
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
  "deadlines": {
    "start_minutes": 10,
    "launch_minutes": 15,
    "load_hold_minutes": 5,
    "hold_minutes": 15,
    "incident_mechanism_minutes": 10,
    "incident_pr_minutes": 15,
    "incident_ready_minutes": 20,
    "status_minutes": 60,
    "enqueue_minutes": 15,
    "release_start_minutes": 10
  },
  "judge_model": "claude-sonnet-5-5",
  "status": {"channel": "<the drive's updates channel id>"},
  "landing": {
    "repo": "Forge-AI/monorepo",
    "ledger": "<ledger id>",
    "checkout": "/absolute/monorepo/worktree",
    "holds": "/absolute/drive/holds.md",
    "fidelity_lane": "fidelity-desk",
    "release_owner": "peer:<release session>",
    "interval_seconds": 180,
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

The orca process rewrites `orca-runner.heartbeat.json` beside the desk inbox every
pass, and its `restart` field is the command to run from the bound terminal when the
`desk_liveness` hook reports `LAUNCH-FAILED`.

For the cci cutover, the root adds `drive` to the runner config, confirms
`orca.desk_inbox`, and restarts both runners. Its records keep arriving over the
`cci` channel. The root owns that cutover.

The orca desk calls Orca as the terminal it was started from, named by the
`ORCA_TERMINAL_HANDLE` it inherits, and only the terminal bound to the Run may call
`worker-start`. It refuses to start, printing the rebind command, unless that
terminal coordinates the Run. When the binding moves while it runs, it holds every
launch and writes one `UNBOUND` line. After the root moves to a new terminal, run
`desk-runner.py rebind --config "$CONFIG"` there, then restart the orca desk from
that terminal. `show` prints the binding first.

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
the thread, and a delivered resend emits nothing. After ten resends all lost the
same way the runner emits `LOST` once, with the count, and its record's path holds
the relay text; it sends nothing more. A resend Orca rejects emits `SEND-FAILED`.

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

**O4. Judge questions from the brief.** A Sonnet-low `claude -p` call with no
tools reads the lane's brief for each question or escalation. It answers what
the brief settles; otherwise, it emits `DECIDE` with the question and options.

**O5. Address the current dispatch.** Relays use the current receipt and
`worker-show`; completed or failed dispatches receive no new relay. Replies
address the original question id through `--reply-to`.

**O6. Never end a session.** The runner never stops, signals, releases, or closes
Claude, Codex, Orca, a terminal, a PTY daemon, or a supervisor. Finished sessions
stay open. An `OUTCOME` line does not authorize cleanup. The sweep names each
newly settled dispatch once in a `RECLAIM` line with the `orca.gc` command, and
the root runs that command under R195.

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
the original question id. The current judge path does not fence stale senders;
do not treat it as proof that this rule is enforced.

**O11. Treat a capacity fallback like an ask.** Workers send `question` or
`escalation` when `ask` returns `capacity reached`; both reach the same judge.

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

## Landing passes

Every pass runs `ledger.py refresh` and `reconcile`, then reads the rows. It
reconciles uncertain enqueues, verifies prefixes whose rows are `landed` by squash
on the base, and routes restacks. It gates every tracked open stack tip in
parallel with `stack-enqueue <tip> --check [--whole] [--hold <n>...]`.

Each exact set of prefix heads gets one enqueue action. Only attempts proven to
have enqueued nothing permit another attempt for those heads. Accepted enqueues
run in parallel, each with a fresh read of the holds file and held lanes' open
rows. Held PR numbers follow the tip:
`stack-enqueue <prefix top> --hold $(cat <held file>)`. Drop `--hold` when the
numeric file is empty. `--hold` takes one or more numbers, never a filename;
argparse's exit 2 otherwise reads as `unsettled`.

A prefix is accepted only when every PR in it has a fidelity ok at its current
head. The runner reads `cci tail --kind=review --lane <fidelity lane> --pr <n>`,
keeps the records that start `fidelity: ok` or `fidelity: missing` and name
that head by a short sha, and takes the newest; only `fidelity: ok` passes. An
ok that names an older head, or no head, does not count, so the fidelity lane
names the head it graded in every record, as in `fidelity: ok F307 at 9c0b891`.
Without an ok the runner enqueues nothing from the prefix and posts one cci
`blocker` per head and grade to the PR's lane and the fidelity lane. It checks
again just before the enqueue call and completes the action as `blocked` when
the ok is gone. `landing.fidelity_lane` names the lane, default `fidelity-desk`.

Each pass also mirrors the newest grade of every open row's head into that
head's `fidelity` commit status: `success` for `fidelity: ok`, `failure` for
`fidelity: missing`, with the grade's first line and cci seq as the description.
It posts once per grade record and keeps the action in the landing container
as `status:<pr>:<head>:<seq>`, so a restart does not post a completed status
again. A post GitHub took whose completion was never written is sent once more,
identical. A head with no grade gets no status. A `cci tail` that fails skips
the mirror for that pass and logs `CCI-TAIL-FAILED`. stack-enqueue reads the status on every call and refuses
a head whose `fidelity` is not `success`, which is how a grade binds a caller
that reads no cci. The fidelity lane posts no status itself; it keeps naming
the head in each `review` record, and it grades a head whatever its `fidelity`
check reads.

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

A landing older than the landing runner's first pass sends nothing, and neither does
a landed row with no `landed_at`. A `merge-fast` landing whose body has no
`Release-With:` line is read once and sends nothing.

A `BLOCKED` head other than `held` routes once per head and blocker. The runner
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
| `INCIDENT` | Ratify the fix launch, start evidence and incident-doc lanes, fence the target, and start account comms plus `#outage` for a platform-wide incident; adopt the fix lane into the incident executor. For a line that says a fix does not cover a case, ratify the case lane it launched and keep the reporter told that the case is still open. |
| `INCIDENT-CLOCK` | Send the line to the owner as a direct message in the turn it arrives, with `cc-slack dm-status`, ahead of any other work in that turn. Ratify the `<lane>-opus` fallback the line names. When the line says none was launched, launch one unless it names an Opus backup already on the lane. For `in-process`, start `<lane>-opus` in that turn as an `Agent` of type `long-running:lane-ship` on opus in fast mode with the brief the line names. For `ready`, clear what holds the missing milestone. The key is the lane. |
| `STATUS-DUE` | The drive's updates channel has had no status post for `deadlines.status_minutes`. The comms desk posts it under the standing grant; the root answers its fact asks and never asks the owner whether to post. The record goes to `root` and `comms-desk` on topic `status`, once per window. |
| `LOAD-HIGH` | Move the load off the Mac in that turn: stop or move each local pass, backfill, dry run, or bulk rewrite to in-region compute, and launch a refused incident lane in-process meanwhile, as an Agent named for the refused lane, which the pack's Agent hook lets through for 30 minutes. Never wait the load out. The key is the ten-minute window. |
| `ALERT-FAILED` | Correct the alert line's slug, link, or form and append it again. |
| `HOLD-FAILED` | Correct the hold or unhold grammar and append the line again. |
| `RELAYED` | None; an inbox relay line was accepted for that lane and is delivered in the same pass. |
| `RELAY-FAILED` | Fix the named relay's lane, key, or form, or launch the lane, and send a corrected relay under a new key. When the line names a file that is not readable, post the relay again on the runner's machine. |
| `LAUNCHED` | None; the launch is verified and the line names its dispatch and terminal. |
| `DEADLINE` | Resolve the named action's missing delivery, start, launch, or enqueue proof. |
| `LOST` | Deliver the relay text at the record's path to the lane by hand; the send and ten resends were all lost, and the runner sends nothing more. |
| `UNVERIFIABLE` | Reconcile the missing external receipt; never repeat the mutation blindly. |
| `SPRITE-UNVERIFIABLE` | Inspect the named receipt directory and any Task or Dispatch before relaunching; a remote worker may be running, and the runner launched nothing locally. |
| `OUTCOME` | Consume the worker's `worker_done` result. |
| `FIX-LIVE`, `MECHANISM`, `READY` | Read the lane's status milestone by message id; the line includes its subject and up to 300 body characters. |
| `PROMPT-WATCH-DOWN` | Run the line's `prompt_watch.py start` command; until it polls again no prompt on this drive is being read. |
| `LIVENESS` | Inspect the non-live dispatch; preserve its session. |
| `STALE-MAIL` | Resolve unread work on a completed or failed dispatch. |
| `RECLAIM` | Run the line's gc command; it closes each idle settled terminal's tab and removes each finished worktree under R195. |
| `STALE-POLICY` | Reconcile the rejected revision with the accepted predecessor. |
| `LAUNCH-FAILED` | Read the named launch failure before issuing any new action; for an inbox launch line, relay to the live dispatch or append a corrected line under a new key. |
| `UNSUPERVISED` | Arrange the launched lane's reporting without launching it again. |
| `SEND-FAILED` | Resolve a send that failed three explicit attempts, or a resend Orca rejected. |
| `ENQUEUE-STRANDED`, `ENQUEUE-UNSETTLED`, `ENQUEUE-FAILED` | Resolve the queue result from current evidence. |
| `ROUTE`, `UNOWNED` | Supply the missing route or owner. |
| `STATUS-FAILED` | Read GitHub's refusal of the `fidelity` commit status the line names; a token or rate limit is the usual cause. The runner posts the status again on every pass until GitHub takes it, and until then stack-enqueue cannot see that grade. |
| `HOLDS-UNREAD` | Rewrite the quoted holds-file line as `#<pr> <why>`, `lane:<name> <why>`, `<lane> HOLD <why>`, `HOLD <lane> <why>`, `hold:all <why>`, or `release #<pr>`, or delete it. When the line names a lane no ledger row carries, hold each PR as `#<pr> <why>` instead. The landing runner gates and enqueues nothing while the line stands. |
| `RELEASE-FAILED` | Start the release the line names, or post its `release` record when it is already running. When the line says cci refused the record, the `peer:` session has most often ended, so correct `landing.release_owner` and restart the landing runner. When it says cci printed no sequence, read what cci printed; the runner posts the record again under the same `--id` on its next pass. |
| `DECISION_GATE`, `HANDOFF` | Act on the worker's gate or handoff. |
| `ORCA-INBOX` | Resolve the mailbox error without restarting Orca. |

The store deduplicates escalation keys. Liveness keys include the hour;
`ORCA-INBOX` keys include a ten-minute bucket, so an unchanged fault can recur. Missing
briefs and failed judge calls can emit `DECIDE` without options. Routine quiet
polls do not wake a model.

The runner never edits worker code, mutates production, writes Slack, invents
scope, or derives authority from a branch prefix or a display name. The root
owns decisions and the holds file; lanes own their worktrees.
