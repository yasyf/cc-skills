# The orca desk runner

`desk-runner.py` owns Orca worker traffic and ready-prefix landing. Run two detached
processes against one config and store: `run --desk orca` and `run --desk landing`.
No model-operated orca-desk sits between the root and Orca. Where the checkout
carries `stack-enqueue`, the landing runner runs D3, D14, and D16; the
landing-desk lane keeps the duties in [its brief](landing-desk-brief.md).

*Prevents G130's `AmiBake` launch being lost behind a desk handoff, R620 going to a
superseded desk, and GO carrying no start deadline (2026-10-01 audit, Brief 3 and
ranked fix 3).*

## Root discipline

Issue commands with the drive's R/L numbering. Submit a key once per action;
submitting it again in the same lane returns the existing action. Never append
Orca rulings to `inbox/orca-desk.md`, and never `SendMessage` a desk.

```text
desk-runner.py relay --config C --key R<n> --lane L --text T [--reply-to <question msg id>]
desk-runner.py launch --config C --key R<n> --lane L --model M --effort E --brief PATH [--owner-directed]
desk-runner.py policy --config C --key L<n> --landing prefix|whole --revision REV --source TEXT --supersedes <current revision>
desk-runner.py show --config C
```

Use `--model sol --effort xhigh` for incident lanes. A `DECIDE` line carries the
question message id as its key; answer with `relay --reply-to <msg id>`. Other
relays carry guidance or a brief attachment pointer. Keep each brief complete as
an attachment on the drive's `briefs: <slug>` log.

Read runner traffic only from the config's `escalations` file, under the drive's
`inbox/`, through one Monitor on `tail -n 0 -F <file>`. Re-arm on expiry and after
compaction. `show` and the config's `view` file render action state. Runner-owned
`inbox/` files are views, never command authority. Keep existing history; do not
mirror runner actions into `TaskCreate`/`TaskUpdate` or complete shadow tasks for them.

Only the orca runner consumes the Run. To cut over from a running orca-desk,
let it finish its current pass and acknowledge its delivered batch, then end its
loop before starting the runner. Keep its session open. Carry unresolved work into
runner commands with its existing keys.

The runner reads `orchestration inbox --terminal run:<run>` without a waiter or
acknowledgement. No other loop may run `check --run` with `--wait` or `--ack`.

## Config and startup

Fill the paths and ids in this JSON and use the same file for both processes.
`store` is optional; omitting it uses `~/.claude/long-running/incidents`. The store
holds one JSON container per lane, plus `desk-landing` and `desk-runner`, with an
owner and `owner_generation`. Match `orca.run` and `orca.receipts` to the launch
environment. Set the accepted prefix policy's revision to #28601's revision.

```json
{
  "store": "/absolute/drive/actions",
  "escalations": "/absolute/drive/inbox/runner.md",
  "view": "/absolute/drive/inbox/runner-state.md",
  "orca": {
    "run": "<run id>",
    "receipts": "/absolute/drive/receipts",
    "briefs": {"repo": "/absolute/drive/checkout", "log": "<briefs log id>"},
    "launch_env": {
      "ORCA_LAUNCH_RUN": "<run id>",
      "ORCA_LAUNCH_REPO": "<Orca repo id>",
      "ORCA_LAUNCH_PARENT": "/absolute/coordinator/worktree",
      "ORCA_LAUNCH_PREFIX": "<drive prefix>",
      "ORCA_LAUNCH_ROOT": "/absolute/worktrees",
      "ORCA_LAUNCH_BASE": "origin/dev",
      "ORCA_LAUNCH_STATE": "/absolute/drive/receipts",
      "ORCA_LAUNCH_CLAUDE_ARGS": "",
      "ORCA_LAUNCH_RETRY_SECONDS": "30",
      "ORCA_LAUNCH_BOOT_SECONDS": "180"
    }
  },
  "deadlines": {
    "start_minutes": 10,
    "launch_minutes": 15,
    "load_hold_minutes": 5,
    "enqueue_minutes": 15
  },
  "judge_model": "claude-sonnet-5-5",
  "landing": {
    "repo": "Forge-AI/monorepo",
    "ledger": "<ledger id>",
    "checkout": "/absolute/monorepo/worktree",
    "holds": "/absolute/drive/holds.md",
    "bus": "<bus id>",
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
start both and arm the root's Monitor on the last command:

```sh
DRIVE='/absolute/drive'
CONFIG="$DRIVE/runner.json"
mkdir -p "$DRIVE/inbox"
touch "$DRIVE/inbox/runner.md"
nohup desk-runner.py run --config "$CONFIG" --desk orca > "$DRIVE/orca-runner.log" 2>&1 < /dev/null &
nohup desk-runner.py run --config "$CONFIG" --desk landing > "$DRIVE/landing-runner.log" 2>&1 < /dev/null &
tail -n 0 -F "$DRIVE/inbox/runner.md"
```

Restarting either process uses the same records and is idempotent. Keep one process
per desk. Restarting a runner never means relaunching its workers.

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
`--retry-request`, or the recipient's mailbox. Without either proof it remains
`unverifiable` and emits `UNVERIFIABLE`; never resend it blindly. An unparseable
CLI response currently becomes `failed` and enters the send retry path, so that
response shape lacks this guarantee. A launch with neither a launch line nor a
new receipt is `UNVERIFIABLE`, never relaunched automatically.

## Orca passes and standing rules

Each pass settles detached launches, delivers accepted relays and launches,
reconciles lost sends, reads new Run messages, checks start and launch deadlines,
and writes changed views and escalation lines. A quiet pass writes nothing.
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
replaying history; restarts resume there, including messages marked read elsewhere.

**O4. Judge questions from the brief.** A Sonnet-low `claude -p` call with no
tools reads the lane's brief for each question or escalation. It answers what
the brief settles; otherwise, it emits `DECIDE` with the question and options.

**O5. Address the current dispatch.** Relays use the current receipt and
`worker-show`; completed or failed dispatches receive no new relay. Replies
address the original question id through `--reply-to`.

**O6. Never end a session.** The runner never stops, signals, releases, or closes
Claude, Codex, Orca, a terminal, a PTY daemon, or a supervisor. Finished sessions
stay open. An `OUTCOME` line does not authorize cleanup.

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

**O13. A prompt is a desk bug.** The sweep reads `observation.agentWait` and
emits `PROMPT` in the pass that sees it. It never types a guessed answer.
Stale unread mail gets one terminal wake per message; completed or failed
dispatch mail emits `STALE-MAIL`.

**O14. Hold launches under load, for a bounded time.** Before each launch, the
runner reads the 1-minute load average. Sol and `--owner-directed` launches never
wait. Above the core count any other launch stays accepted and `show` marks it
`HELD`; after `deadlines.load_hold_minutes` it fails with a `LAUNCH-HELD`
escalation and a Run mailbox message. It never kills a worker to lower load.

**O15. Preserve codex and sol routes.** `codex` and `gpt-*` use Orca's codex
agent. `sol xhigh` uses `gpt-6.1-sol`, a `--no-parent` worktree, and a terminal
command with `--dangerously-bypass-approvals-and-sandbox` and
`-c service_tier=fast`. An `unsupervised` result escalates; it is not a failed
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

After a prefix lands, the runner routes `ccx vcs stack submit` to the owner of the
first PR above it. Orca lanes receive a relay; other lanes receive
`bus.py post --kind blocker`. It verifies a restack route when the child's head
moves or the child lands. The landing-desk lane never enqueues or duplicates
these restack routes beside the runner.

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
identify the cause; `DECIDE` uses the question id.

| Kind | Root action |
|---|---|
| `DECIDE` | Resolve the question the brief does not settle; answer with `relay --reply-to <msg id>`. |
| `DEADLINE` | Resolve the named action's missing delivery, start, launch, or enqueue proof. |
| `UNVERIFIABLE` | Reconcile the missing external receipt; never repeat the mutation blindly. |
| `OUTCOME` | Consume the worker's `worker_done` result. |
| `FIX-LIVE`, `MECHANISM` | Read the lane's status milestone by message id; the line includes its subject and up to 300 body characters. |
| `PROMPT` | Resolve the prompt on the named dispatch and terminal. |
| `LIVENESS` | Inspect the non-live dispatch; preserve its session. |
| `STALE-MAIL` | Resolve unread work on a completed or failed dispatch. |
| `STALE-POLICY` | Reconcile the rejected revision with the accepted predecessor. |
| `LAUNCH-FAILED` | Read the named launch failure before issuing any new action. |
| `UNSUPERVISED` | Arrange the launched lane's reporting without launching it again. |
| `SEND-FAILED` | Resolve a send that failed three explicit attempts. |
| `ENQUEUE-STRANDED`, `ENQUEUE-UNSETTLED`, `ENQUEUE-FAILED` | Resolve the queue result from current evidence. |
| `ROUTE`, `UNOWNED` | Supply the missing route or owner. |
| `DECISION_GATE`, `HANDOFF` | Act on the worker's gate or handoff. |
| `ORCA-INBOX` | Resolve the mailbox error without restarting Orca. |

The store deduplicates escalation keys. Liveness keys include the hour;
`ORCA-INBOX` keys include a ten-minute bucket, so an unchanged fault can recur. Missing
briefs and failed judge calls can emit `DECIDE` without options. Routine quiet
polls do not wake a model.

The runner never edits worker code, mutates production, writes Slack, invents
scope, or derives authority from a branch prefix or a display name. The root
owns decisions and the holds file; lanes own their worktrees.
