# Orca workers: launch recipe

For implementation and test workers, prefer a repository's canonical remote
entrypoint. Forge-AI/monorepo's
[Orca skill](https://github.com/Forge-AI/monorepo/blob/dev/.agents/skills/orca/SKILL.md)
defaults to Sprite prepare+attach, with an explicit local route for the root and
for extremely sensitive Fable work. Preserve the requested model, effort, and Codex service tier.
Use its complete VM brief and
[source-return contract](https://github.com/Forge-AI/monorepo/blob/dev/.agents/skills/orca/SKILL.md#return-source-for-root-review);
the remote worker returns a patch/report and the root collects, reviews, and
ships it. Remote API keys stay in the worker process; local Fable uses existing
Mac interactive authentication.

Canonical remote workers use their already-owned native Run, one home-inbox
consumer, and home Dispatch IDs. Do not enroll them in desk-runner, common
`orca-launch.sh`, or `orca-check.sh`, or apply the raw-TTY wake, unsupervised
retry, local VCS/PR, or historical cleanup procedures below to them. Do not add
a second consumer to compensate for an adapter that assumes local terminals.
Namespace remains opt-in pending regular Compute acceptance; the recipe picker
is separate from this direct CLI route.

Current session retention also overrides every historical cleanup/rollback
instruction in this reference for local sessions. Keep workers and coordinators on success,
failure, completion, and idle. A `RECLAIM` line, load, or terminal count grants
no permission to stop, release, close, or remove a session or its workspace.

## Separate desktop adapter

The rest of this reference describes the existing desktop adapter.

`desk-runner.py run --config C --desk orca` owns launches and the
Run inbox cursor. Start it detached before the first worker, with the
config in [orca-desk-brief.md](orca-desk-brief.md). The root issues
`desk-runner.py launch --config C --key R<n> --lane L --model M --effort E --brief PATH`
and `desk-runner.py relay --config C --key R<n> --lane L --text T
[--reply-to <question msg id>]`. No model desk runs between the root and Orca;
the root reads the runner's escalation file. Only the orca runner consumes the Run,
through non-destructive `orchestration inbox --terminal run:<run> --limit N --json`
reads. No other loop may use `check --run` with `--wait` or `--ack`.
The commands below describe the adapters the runner calls, not a second root loop.

## Launching

Choose any optional remote workspace recipe through the repository's Orca
skill and its current verification contract. The canonical monorepo worker
route above uses direct prepare+attach and does not require the desktop composer.
Keep every resulting session and workspace; follow the current retention rule
above instead of historical cleanup advice.

Orca launches `claude` with the default agent arguments in the user's settings, including
`--permission-mode plan`. A worker needs a terminal created with an explicit
command. Keep the interactive default in plan mode. The explicit command also
disallows `AskUserQuestion`, `EnterPlanMode`, and `ExitPlanMode`: a worker's prompt
reaches only its own terminal, which no one watches, so the launch script puts
the flag on every command it builds. Carry every other default
argument into `ORCA_LAUNCH_CLAUDE_ARGS`; `--channels plugin:cc-review@cc-review` below
stands for those arguments, not a fixed channel requirement.

`orca-launch.sh` creates the worktree and terminal, then attaches the worker with
these commands:

```sh
orca worktree create --name "<lane>" --repo "id:<repo id>" --base-branch "origin/<base>" \
  --parent-worktree "path:<coordinator worktree>" --setup run --json
orca terminal create --worktree "path:<wt>" --json \
  --command "claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode --strict-mcp-config --channels plugin:cc-review@cc-review --model <id> --effort <level>"
orca orchestration worker-start --run "<run>" --spec "<pointer>" \
  --worktree "path:<wt>" --terminal "<handle>" --json
```

`<handle>` is `result.terminal.handle` from `terminal create`. On `worker-start`,
`--model` and `--effort` cannot combine with `--terminal`; put both on the custom
`claude` command. Shift-tab is a fallback only before the worker opens a plan.
The launch script checks the terminal screen for `bypass permissions on`.

Except for incident lanes below, worktrees use `--parent-worktree path:<coordinator worktree>`.
The script reuses an existing directory; re-tag an existing worktree before launch
when it does not carry that parent:

```sh
orca worktree set --worktree "path:<wt>" --parent-worktree "path:<coordinator worktree>"
```

The shared contract and lane section become one `<lane>.full.md` attachment on
the drive's briefs log, as [orca-lane-brief.md](orca-lane-brief.md) describes.
`--spec` points to its resolved file path, at most 500 characters. Orca truncates
the pasted prompt near 3 KB, so the brief itself never goes in `--spec`.

Roots run Opus 5.5. Fable is for exceptional cases only: the most sensitive local
implementation, using the Mac's existing interactive authentication. Ordinary
Claude workers and subdesks use Opus or Sonnet per the routing table; Opus is the
default for Claude implementation workers. Auth, migrations, concurrency, or error-prone code
alone does not qualify for Fable. Preserve explicitly requested models and
effort within these roles. Never use Fable as a general fallback or set
`fallbackModel`.

Use these model ids; the script also accepts the aliases in the first column.

| Alias | Model id |
|---|---|
| `opus` | `claude-opus-5-5` |
| `sonnet` | `claude-sonnet-5-5` |
| `fable` | `claude-fable-5-1` |
| `sol`, `codex` | `gpt-6.1-sol` on Orca's codex agent, standard tier |
| `incident` | `gpt-6.1-sol` in its own terminal, fast tier |
| `astra` | `gpt-6-astra` on Orca's codex agent, exceptional cases only |

Effort is `low`, `medium`, `high`, `xhigh`, or `max`.

A lane the routing table sends to codex launches on Orca's codex agent, never as a
claude worker calling the codex skill. `orca-launch.sh <lane> sol xhigh <brief>`
(`codex` is the same alias) creates the worktree the same way, then runs:

```sh
orca orchestration worker-start --run "<run>" --spec "<pointer>" --task-title "<prefix><lane>" \
  --worktree "path:<wt>" --agent codex --model gpt-6.1-sol --effort xhigh --timeout-ms 600000 --json
```

`--agent` makes Orca create the terminal, and Orca's `agentDefaultArgs.codex` carries
`--dangerously-bypass-approvals-and-sandbox`, so there is no custom command and no
screen check. The script reads the terminal handle from the receipt's agent-terminal
effect and counts a `ready` or `unsupervised` result as launched. `--terminal` refuses
`--agent`, so a custom codex terminal cannot name its agent.

On 2026-09-30, both launch shapes left codex idle
at its prompt until `worker-start` failed with `failedStage: agent_readiness` and
`lastError: timeout`: dispatch `ctx_43637c1c2b7b` through `--agent codex`, and
`ctx_4aa6ac245439` through a terminal created with
`--command "codex --dangerously-bypass-approvals-and-sandbox -m gpt-6-astra -c model_reasoning_effort=xhigh"`.
The script now delivers the spec after a readiness timeout; see [Incident lanes](#incident-lanes-gpt-61-sol-on-the-fast-tier).
Leave the terminal open, as R195 requires, and report the dispatch to the root.

### Incident lanes: gpt-6.1-sol on the fast tier

Launch both incident lanes with `scripts/orca-launch.sh <lane> incident xhigh <brief>`.
`worker-start` has no service-tier flag, so the script passes the tier on the codex
command line. It creates a top-level (`--no-parent`) worktree and a terminal running
codex on the fast tier, then starts the worker on that terminal. This is the recipe
verified on `ctx_e6b256d5c0c8`:

```sh
orca terminal create --worktree "path:<wt>" --title "<name>" --json \
  --command "codex --dangerously-bypass-approvals-and-sandbox -c model=gpt-6.1-sol -c service_tier=fast -c model_reasoning_effort=xhigh -c mcp_servers={}"
orca orchestration worker-start --run "<run>" --spec "<pointer>" \
  --worktree "path:<wt>" --terminal "<handle>" --timeout-ms 90000 --json
```

Codex lanes have Sentry and Datadog: `--agent codex` workers load `~/.codex/config.toml`, incident lanes keep `datadog` and `sentry` mounted, and `codex-ask` mounts both by default, all on the OAuth login from `codex mcp login <server>`.

Only `incident` lanes run fast. Orca's codex runtime config,
`~/Library/Application Support/orca/codex-runtime-home/home/config.toml`, stays
`service_tier = "default"`, so `sol` and `codex` lanes on `--agent codex` run on
the standard tier. No script or lane edits that file.

The Opus 5.5 backup of an incident runs in Claude fast mode. `incident.py`
launches it as `opus xhigh` with `ORCA_LAUNCH_CLAUDE_ARGS="--settings
'{"fastMode":true}'"`; a session launched with that setting reports
`fast_mode_state: on`, even under a user-level `fastModePerSessionOptIn`.

Orca's readiness check does not recognize codex. Dispatch `ctx_a1c0260ecb02` and the root's two hand-launched incident workers
ended `state=failed`, `failedStage=agent_readiness`, `lastError=timeout` with codex at its prompt and
the spec undelivered. On that timeout with a live codex or incident terminal, the script types the
spec pointer itself and prints `<lane> unsupervised task=... dispatch=... terminal=... worktree=...`.
Count it as launched; it reports with
`cci post --drive <drive> --lane <lane> --kind report --to root --text "<report>"`,
with no Orca `worker_done` or escalation plumbing.
A `desk-runner.py launch` action starts the script detached; the runner never
waits on readiness in its mailbox loop.

That earlier dispatch read `ready`. `--skip-git-repo-check` is exec-only and breaks interactive codex.
Codex 0.160 accepts `service_tier=fast` and resolves it to the catalog id `priority`, labeled Fast; the session config of a lane launched with `fast` records `service_tier: "priority"`.
`worker-release` returns `retained` (`external_terminal`) for a terminal you created, and `orca-launch.sh` creates every claude terminal, so Orca never closes a finished claude lane's terminal. The root's gc closes it under R195's settled-dispatch bar.

### Worker messages

`send --type` accepts only
`status|dispatch|worker_done|merge_ready|escalation|handoff|decision_gate|question|heartbeat`.
Workers use the preamble's `orca orchestration ask` for a ruling. If it returns
`capacity reached`, they send `--type question` or `--type escalation` and keep
working on everything that does not depend on the answer. The runner judges
either message from the brief; the root answers a `DECIDE` through
`desk-runner.py relay --config C --key R<n> --lane L --text T --reply-to <msg id>`.

**R56. Reply to the original question id.** Submit
`desk-runner.py relay --config C --key R<n> --lane L --text T --reply-to <msg id>`.
The runner checks the current dispatch and uses `orca orchestration reply --id`
for the original question. Only that reply wakes an `orca orchestration ask`
wait; a dispatch message does not.

The runner wakes a terminal after a guidance
relay, not after a question reply. Completed or failed dispatches receive no new
relay; request any authorized successor with `desk-runner.py launch`, never a
second mailbox loop. The runner records a missing launch outcome as
`UNVERIFIABLE` and never retries it blindly.

`orca orchestration worker-release` releases only a settled
worker's terminal; it never stops a live worker.

*Prevents a worker staying blocked after the desk sent its ruling as a dispatch, and b2-data / phase0b-aig sitting idle for about an hour with unread messages (2026-10-01).*

Finish with `send --type worker_done --outcome succeeded|failed`
and the preamble's `--task-id <taskId>` and `--dispatch-id <dispatchId>`.

Orca workers are separate sessions. They register their prefixes and report verdicts
through `ledger.py` themselves; the hook records opened PRs, with hand registration
as the fallback. They never `SendMessage` a subagent. Read a worker's output by dispatch:

```sh
orca orchestration worker-read --dispatch "<id>"
```

A check from a worker terminal must name `--terminal <its handle>`. A `--run` check
from a non-coordinator terminal fails `consumer_fenced`. Only
`desk-runner.py run --config C --desk orca` consumes the Run mailbox, using the
coordinator identity; workers consume their own terminal inboxes.

**R195. Sessions are protected.** Claude and Codex sessions, Orca, terminal hosts,
PTY daemons, and their supervisors are never stopped, signalled, suspended,
restarted, released, or closed, singly or in bulk, for cleanup or load. There
are two exceptions. The root's gc closes a settled dispatch's idle terminal
after the lane's `worker_done`. The root also ends a lane of its own Run once
it decides the lane is done (stood down, superseded, or its deliverable on
disk) with `orca-gc --run <run> --done <ctx>`. That call runs `worker-stop`,
closes the terminal without the idle read, and reclaims the worktree by the
rules below. The root does not message the lane to send `worker_done` first. Idle means an empty prompt, nothing running, no
unanswered question, and no output in 10 minutes, read again right before the
close. gc never touches a live or unsettled worker or a terminal outside the
Run. It closes a terminal with `orca terminal close --terminal <handle> --tab`
and removes a worktree with `orca worktree rm`, only when the worktree is clean,
fully pushed or landed, has no open PR, and no other terminal in
`orca terminal list` is attached to it. The runner never does either (O6); its
sweep names each newly settled dispatch in one `RECLAIM` line carrying the gc
command, and the root runs it. A launch that fails before `worker-start` is
not a session, and neither is one that `worker-start` refuses before it
dispatches, such as `consumer_fenced`: `orca-launch.sh` closes the tab it
opened and removes a worktree it created. Forge-AI/monorepo's gc is
`.agents/skills/orca/scripts/orca-gc --run <run> [--dispatch <ctx>] [--done <ctx>]`.
The captain-hook sessions guard allows a bare
`orca orchestration worker-stop --dispatch <ctx>` only from the root
coordinating that dispatch's Run.

The owner ruled this on 2026-10-02: "1 and improve our orca skill and scripts to
avoid this in the future" (answer bef3366, option 1 releasing every completed or
failed dispatch and closing its idle terminal), and answer 827a3f2, which named
76 such handles for the finished- and failed-dispatch class verified idle by a
screen read. On 2026-10-06 it added the `--done` route: "if a lane is done you
can just terminate it no need to do this dumb signal thing" (answer edfa70e).

*Prevents the 12:35Z kill that ended every session of a drive (release v3,
2026-09-30), and the 476 retained terminals and 361 worktrees of settled
dispatches that held a 32-core machine at load 100 to 600 (release v3, 2026-10-02).*

**R210. Load is the only launch throttle.** Working workers have no cap. Before each
launch, read `uptime`; while the 1-minute load average is above the core count
(`sysctl -n hw.ncpu`), launch nothing until two readings in a row are under it.
Incident and owner-directed launches are exempt and start at once.

The runtime drops connections under load. Retry after 30 seconds; never restart
Orca to recover a connection. Handles belong to one runtime. After a restart,
re-list the workers and terminals, update the recorded handles, and continue with
the replacements.

## Scripts

### `orca-launch.sh`

The script takes one lane and a file path for its complete brief. Store briefs as
attachments on the drive's `briefs: <slug>` log and resolve the path at launch:

```sh
orca-launch.sh <lane> <model> <effort> \
  "$(ccn -R <drive checkout> attachment path <briefs log> <lane>.full.md)"
```

```text
usage: orca-launch.sh <lane> <model> <effort> <brief-file>
```

Set the run and repo ids before calling it. The remaining variables have defaults.

| Variable | Meaning and default |
|---|---|
| `ORCA_LAUNCH_RUN` | Orchestration run id; required. |
| `ORCA_LAUNCH_REPO` | Orca repo id; required. |
| `ORCA_LAUNCH_PARENT` | Coordinator worktree path; default `$PWD`. |
| `ORCA_LAUNCH_NO_PARENT` | `1` creates a top-level (`--no-parent`) worktree instead of a child of the parent, as an incident lane always does; default unset. |
| `ORCA_LAUNCH_PREFIX` | Worktree name prefix; default none. The worktree is `<prefix><lane>-base`, so its branch `yasyf/<prefix><lane>-base` never blocks the lane's `yasyf/<prefix><lane>/` branches. |
| `ORCA_LAUNCH_ROOT` | Directory Orca creates worktrees in; default the parent's directory. |
| `ORCA_LAUNCH_BASE` | Base branch; default the parent checkout's `origin/HEAD`. |
| `ORCA_LAUNCH_STATE` | Receipt directory; default `~/.claude/scratch/orca-launch/<run>`. |
| `ORCA_LAUNCH_CLAUDE_ARGS` | Further arguments from Orca's agent defaults; default none. Leave out the plan-mode argument. |
| `ORCA_LAUNCH_RETRY_SECONDS` | Wait before a retry; default `30`. |
| `ORCA_LAUNCH_BOOT_SECONDS` | Ceiling on the wait for Orca to detect the terminal's agent; default `180`. |
| `ORCA_LAUNCH_WORKTREE_SECONDS` | Ceiling on the wait for a worktree whose create failed to register; default `180`. |

A successful launch prints one of these result lines:

```text
<lane> ready task=<id> dispatch=<id> terminal=<handle> worktree=<path>
<lane> unsupervised task=<id> dispatch=<id> terminal=<handle> worktree=<path>
```

Count the launch only after one of those lines. A supervised result reads `ready`;
Claude's screen must also show `bypass permissions on`. An `unsupervised` codex
result means the script sent the spec after the readiness timeout; report it to
the root through `cci post --drive <drive> --lane <lane> --kind report --to root --text "<report>"`.
Read deliveries with `cci tail --drive <drive> --cursor <lane> --reader <lane>`.
A failed launch prints
`<lane> failed <step and reason>` and exits 1; invalid usage exits 2.

The receipt is `<receipt dir>/<lane>.json`; `<lane>.terminal` holds the handle that
`orca-check.sh` maps back to the lane, and `<lane>.worktree` the path Orca created.
A failed `worker-start` that returned a task and dispatch still writes the receipt,
so a permitted retry addresses that dispatch instead of opening a second task. The
brief path is made absolute before it goes into the pointer.

Retry a failed dispatch only after confirming its session has ended, with the
root's ruling; run the same command with the same lane and receipt directory.
The script reads
`result.taskId` and `result.dispatchId` from the recorded receipt and, when
`orca orchestration task-list --status failed` or `--status blocked` lists that task,
passes `--task <taskId> --retry-of <dispatchId>` instead of creating a new task from `--spec`.
Orca retries only a failed or blocked task, so a lane whose recorded task completed,
such as an incident lane relaunched for a recurring alert, gets a fresh task from `--spec`.
It creates a new terminal and keeps the worktree. Never stop the old session or
duplicate its active work. A follow-up alone is not a relaunch: edit the brief file,
then send its pointer with `send --type dispatch` to the current dispatch.

Worktree creation gets four attempts; terminal creation gets three, separated by
`ORCA_LAUNCH_RETRY_SECONDS`. A worktree create that fails often created the worktree
anyway, since the runtime drops the connection but finishes the work. Before each
attempt and after a failed one, the script asks `orca worktree show` for the
worktree's path, polling up to `ORCA_LAUNCH_WORKTREE_SECONDS` (default 180) after a
failure, and creates again only when none registers. Before `worker-start`, which refuses
with `agent_unconfigured` a terminal where Orca sees no agent title or ready prompt,
the script blocks on `orca terminal wait --for tui-idle`, the readiness `worker-start`
waits for next, and fails with
`boot terminal=<handle>: orca terminal wait --for tui-idle reads <timeout, status, or blocked prompt>`
once `ORCA_LAUNCH_BOOT_SECONDS` passes. `orca terminal list` reports `agentIdentity`
from the agent's process or first hook, before its UI renders, so it reads `claude` while
`worker-start` still refuses. Orca drops a terminal's startup command under
load and leaves a shell prompt. Once a third of the ceiling has passed with no idle
agent, the script reads the terminal's screen; when it shows neither the command
line nor the agent's UI, the script types the command with `orca terminal send` once.
A launch whose agent is still not idle at the ceiling fails, and `worker-start` never
runs. After a claude start it checks the screen for
`bypass permissions on` up to ten times, 4 seconds apart. A failed `worker-start` returns immediately;
it is not one of those retry loops.

### `orca-check.sh`

The runner uses `--stale`; terminal callers can block or peek:

```text
usage: orca-check.sh [--peek] [-- <orca check args>...]
       orca-check.sh --stale [--inbox <inbox file>]
```

Without `--peek` or `--stale`, it calls
`orca orchestration check --wait --types worker_done,escalation,question`.
The types select what wakes the wait; the returned delivery is the whole batch.
Each non-heartbeat message prints on one line, followed by the delivery to acknowledge:

```text
<msg id> <type> <lane> <subject>: <body on one line>
delivery <delivery id> heartbeats=<n>
```

The lane comes from the launch receipt's terminal handle; a sender with no matching
receipt prints as its handle. A wait that ends empty prints `timeout`.
Terminal callers acknowledge the previous batch by passing `--ack <delivery-id>`
after `--`. Use `result.deliveryId`; a message id acknowledges nothing, and an
unacknowledged batch replays. Desk-runner reads the Run inbox by sequence and never
acknowledges deliveries.

An escalated question can be recorded as awaiting a
root ruling before acknowledging its delivery; the question's message id remains
the reply address. Never answer a stale question to a replacement dispatch;
[O10](../SKILL.md#the-orca-desk) records the current judge path's missing fence.

`--peek` prints unread messages without waiting or marking them read. Arguments
after `--` pass through to `orca orchestration check`; workers use
`--terminal <handle>`. Do not start a blocking Run check beside desk-runner.

`--stale` reads every launch receipt's dispatch with `worker-show` and peeks the
worker terminal's unread messages. It prints `STALE <lane> <age>m unread <msg id>`
for an in-progress dispatch's messages unread for at least `ORCA_CHECK_STALE_MINUTES`
minutes (default `10`), `STALE <lane> <age>m <completed|failed> <msg id>` for unread messages
on a completed or failed dispatch, and `STALE <lane> <age>m <completed|failed> R<n>`
for a line in `--inbox` past `<inbox file>.cursor` addressed to that lane. Message
age starts at creation; an inbox line's age starts at dispatch completion.

| Variable | Meaning and default |
|---|---|
| `ORCA_CHECK_STALE_MINUTES` | Unread age that flags an in-progress dispatch; default `10`. |
| `ORCA_CHECK_TIMEOUT_MS` | Longest mailbox wait; default `60000`. Keep it at most 60000; a retry can extend the wrapper call. |
| `ORCA_CHECK_STATE` | Launch receipt directory; default `~/.claude/scratch/orca-launch/<run>`. |
| `ORCA_CHECK_RETRY_SECONDS` | Wait before a connection retry; default `30`. |

A lost connection or a `runtime_unavailable` error retries once, then prints
`connection-lost` and exits 1. Any other Orca error prints
`error <code>: <message>` and exits 1 without retrying. The runner's separate Run
inbox read emits `ORCA-INBOX` on failure and keeps its saved cursor for the next
pass. It never restarts Orca. [orca-desk-brief.md](orca-desk-brief.md) gives the loop
and escalation contract.

## Worked example: a v3 drive

Fill the runner config from [the template](orca-desk-brief.md#config-and-startup)
and start both processes detached there. The drive's briefs log holds `common.md`,
`ci-fix.md`, and the complete `ci-fix.full.md` attachment. Resolve it and submit a launch:

```sh
DRIVE_REPO='/absolute/drive/checkout'
BRIEFS_LOG='<briefs log id>'
CONFIG='/absolute/drive/runner.json'
desk-runner.py launch --config "$CONFIG" --key R1 --lane ci-fix --model opus --effort xhigh \
  --brief "$(ccn -R "$DRIVE_REPO" attachment path "$BRIEFS_LOG" ci-fix.full.md)"
```

Submit a ruling with its existing R number. For a question, retain its message id:

```sh
desk-runner.py relay --config "$CONFIG" --key R2 --lane ci-fix --text '<ruling>' --reply-to '<question msg id>'
desk-runner.py show --config "$CONFIG"
```

The root issues runner commands and never `SendMessage`s a desk. It receives cci
records over the `cci` channel subscription R9 defines. The orca runner alone reads the Run inbox and
persists its sequence cursor.
