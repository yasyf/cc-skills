# Orca workers: launch recipe

`desk-runner.py run --config C --desk orca` owns launches and the
`scripts/orca-check.sh` loop. Start it detached before the first worker, with the
config in [orca-desk-brief.md](orca-desk-brief.md). The root issues
`desk-runner.py launch --config C --key R<n> --lane L --model M --effort E --brief PATH`
and `desk-runner.py relay --config C --key R<n> --lane L --text T
[--reply-to <question msg id>]`. No model desk runs between the root and Orca;
the root reads the runner's escalation file. Only the orca runner consumes
`check --run`; end any old desk loop after its current pass before starting it.
The commands below describe the adapters the runner calls, not a second root loop.

## Launching

When a lane needs its own machine for tests or builds off the owner's Mac, or the
running platform, it creates its own remote Orca workspace through the repository's
Orca skill if it ships one (Forge-AI/monorepo: `.agents/skills/orca`, "Remote workspaces").

- Use the skill's `orca computer` flow through the desktop New Workspace composer:
  `Run on` -> a per-workspace environment recipe. Orca owns sleep, wake, and delete.
- Default to Sprites over SSH (`sprites-agents-ssh`) for agents and tests. Use
  Namespace stack over SSH (`namespace-stack-ssh`) only for the running platform.
- Never create the machine with the provider CLI or recipe helper and register an
  SSH host by hand; Orca then never suspends or destroys it.
- Verify the workspace from Orca's own listings through the skill's verify step
  before starting work there. Leave a finished lane's workspace running, like its
  terminal (R195). Delete a workspace through the skill only on the user's explicit
  authorization for that workspace, and only after proving no protected session
  or terminal remains on it. A completed task is not that authorization.
- Treat tailnet access as pending. It depends on the helper's tailnet enrollment
  (not yet shipped) and the owner's one-time setup: an OAuth client, workspace tag,
  and SSH policy that admits only the owner as exact workspace OS users. After the
  repository skill's verify step passes, run its in-workspace Tailnet check
  (`tailscale status`, `.Self.DNSName`); only once it prints the node's DNS name,
  reach the workspace at that name, from an owner-permitted device.
  Never assume enrollment, reachability, or revocation on destroy without that check.

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
  --command "claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode --channels plugin:cc-review@cc-review --model <id> --effort <level>"
orca orchestration worker-start --run "<run>" --spec "<pointer>" \
  --worktree "path:<wt>" --terminal "<handle>" --json
```

`<handle>` is `result.terminal.handle` from `terminal create`. On `worker-start`,
`--model` and `--effort` cannot combine with `--terminal`; put both on the custom
`claude` command. Shift-tab is a fallback only before the worker opens a plan.
The launch script checks the terminal screen for `bypass permissions on`.

Except for sol incident lanes below, worktrees use `--parent-worktree path:<coordinator worktree>`.
The script reuses an existing directory; re-tag an existing worktree before launch
when it does not carry that parent:

```sh
orca worktree set --worktree "path:<wt>" --parent-worktree "path:<coordinator worktree>"
```

The shared contract and lane section become one `<spec dir>/<lane>.full.md` file,
as [orca-lane-brief.md](orca-lane-brief.md) describes. `--spec` is a pointer to that
file, at most 300 characters. Orca truncates the pasted prompt near 3 KB, so the
brief itself never goes in `--spec`.

Use these model ids; the script also accepts the aliases in the first column.

| Alias | Model id |
|---|---|
| `opus` | `claude-opus-5-5` |
| `sonnet` | `claude-sonnet-5-5` |
| `fable` | `claude-fable-5-1` |

Effort is `low`, `medium`, `high`, `xhigh`, or `max`.

A lane the routing table sends to codex launches on Orca's codex agent, never as a
claude worker calling the codex skill. `orca-launch.sh <lane> codex xhigh <brief>`
creates the worktree the same way, then runs:

```sh
orca orchestration worker-start --run "<run>" --spec "<pointer>" --task-title "<prefix><lane>" \
  --worktree "path:<wt>" --agent codex --model gpt-6-astra --effort xhigh --timeout-ms 600000 --json
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

Launch both incident lanes with `scripts/orca-launch.sh <lane> sol xhigh <brief>`.
`worker-start` has no service-tier flag, so the script passes the tier on the codex
command line. It creates a top-level (`--no-parent`) worktree and a terminal running
codex on the fast tier, then starts the worker on that terminal. This is the recipe
verified on `ctx_e6b256d5c0c8`:

```sh
orca terminal create --worktree "path:<wt>" --title "<name>" --json \
  --command "codex --dangerously-bypass-approvals-and-sandbox -c model=gpt-6.1-sol -c service_tier=fast -c model_reasoning_effort=xhigh"
orca orchestration worker-start --run "<run>" --spec "<pointer>" \
  --worktree "path:<wt>" --terminal "<handle>" --timeout-ms 90000 --json
```

Only sol lanes run fast. Orca's codex runtime config,
`~/Library/Application Support/orca/codex-runtime-home/home/config.toml`, stays
`service_tier = "default"`, so astra `codex` lanes on `--agent codex` run on the
default tier. No script or lane edits that file.

Orca's readiness check does not recognize codex. Dispatch `ctx_a1c0260ecb02` and the root's two hand-launched sol workers
ended `state=failed`, `failedStage=agent_readiness`, `lastError=timeout` with codex at its prompt and
the spec undelivered. On that timeout with a live codex or sol terminal, the script types the
spec pointer itself and prints `<lane> unsupervised task=... dispatch=... terminal=... worktree=...`.
Count it as launched; it reports through its inbox/bus file, with no Orca `worker_done` or escalation plumbing.
A `desk-runner.py launch` action starts the script detached; the runner never
waits on readiness in its mailbox loop.

That earlier dispatch read `ready`. `--skip-git-repo-check` is exec-only and breaks interactive codex.
Codex accepts `service_tier=fast`; the catalog id is `priority`, labeled Fast at twice the speed.
`worker-release` returns `retained` (`external_terminal`) for a terminal you created; closing it requires `orca terminal close --terminal <handle>`, subject to R195's session protection.

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
restarted, released, or closed, singly or in bulk, for cleanup, load, or a finished
lane. A finished lane's terminal stays open and idle. Remove a worktree only when
it is clean, fully pushed, and no terminal in `orca terminal list` is attached to it.

*Prevents the 12:35Z kill that ended every session of a drive (release v3, 2026-09-30).*

**R210. Load is the only launch throttle.** Working workers have no cap. Before each
launch, read `uptime`; while the 1-minute load average is above the core count
(`sysctl -n hw.ncpu`), launch nothing until two readings in a row are under it.

The runtime drops connections under load. Retry after 30 seconds; never restart
Orca to recover a connection. Handles belong to one runtime. After a restart,
re-list the workers and terminals, update the recorded handles, and continue with
the replacements.

## Scripts

### `orca-launch.sh`

The script takes one lane and its complete brief:

```text
usage: orca-launch.sh <lane> <model> <effort> <brief-file>
```

Set the run and repo ids before calling it. The remaining variables have defaults.

| Variable | Meaning and default |
|---|---|
| `ORCA_LAUNCH_RUN` | Orchestration run id; required. |
| `ORCA_LAUNCH_REPO` | Orca repo id; required. |
| `ORCA_LAUNCH_PARENT` | Coordinator worktree path; default `$PWD`. |
| `ORCA_LAUNCH_PREFIX` | Worktree name prefix; default none. The worktree is `<prefix><lane>-base`, so its branch `yasyf/<prefix><lane>-base` never blocks the lane's `yasyf/<prefix><lane>/` branches. |
| `ORCA_LAUNCH_ROOT` | Directory Orca creates worktrees in; default the parent's directory. |
| `ORCA_LAUNCH_BASE` | Base branch; default the parent checkout's `origin/HEAD`. |
| `ORCA_LAUNCH_STATE` | Receipt directory; default `~/.claude/scratch/orca-launch/<run>`. |
| `ORCA_LAUNCH_CLAUDE_ARGS` | Further arguments from Orca's agent defaults; default none. Leave out the plan-mode argument. |
| `ORCA_LAUNCH_RETRY_SECONDS` | Wait before a retry; default `30`. |
| `ORCA_LAUNCH_BOOT_SECONDS` | Ceiling on the wait for Orca to detect the terminal's agent; default `180`. |

A successful launch prints one of these result lines:

```text
<lane> ready task=<id> dispatch=<id> terminal=<handle> worktree=<path>
<lane> unsupervised task=<id> dispatch=<id> terminal=<handle> worktree=<path>
```

Count the launch only after one of those lines. A supervised result reads `ready`;
Claude's screen must also show `bypass permissions on`. An `unsupervised` codex
result means the script sent the spec after the readiness timeout; report it to
the root and use the lane's inbox/bus file. A failed launch prints
`<lane> failed <step and reason>` and exits 1; invalid usage exits 2.

The receipt is `<receipt dir>/<lane>.json`; `<lane>.terminal` holds the handle that
`orca-check.sh` maps back to the lane, and `<lane>.worktree` the path Orca created.
A failed `worker-start` that returned a task and dispatch still writes the receipt,
so a permitted retry addresses that dispatch instead of opening a second task. The
brief path is made absolute before it goes into the pointer.

Retry a failed dispatch only after confirming its session has ended, with the
root's ruling; run the same command with the same lane and receipt directory.
The script reads
`result.taskId` and `result.dispatchId` from the recorded receipt and passes
`--task <taskId> --retry-of <dispatchId>` instead of creating a new task from `--spec`.
It creates a new terminal and keeps the worktree. Never stop the old session or
duplicate its active work. A follow-up alone is not a relaunch: edit the brief file,
then send its pointer with `send --type dispatch` to the current dispatch.

Worktree creation gets four attempts; terminal creation gets three, separated by
`ORCA_LAUNCH_RETRY_SECONDS`. Before `worker-start`, which refuses a terminal whose
agent Orca has not detected with `agent_unconfigured`, the script polls
`orca terminal list` every 4 seconds until the terminal's `agentIdentity` reads
`claude`, or `codex` for sol, and fails with
`boot terminal=<handle>: orca terminal list shows agentIdentity=<seen>` once
`ORCA_LAUNCH_BOOT_SECONDS` passes. After a claude start it checks the screen for
`bypass permissions on` up to ten times, 4 seconds apart. A failed `worker-start` returns immediately;
it is not one of those retry loops.

### `orca-check.sh`

The runner calls this adapter for one mailbox check:

```text
usage: orca-check.sh [--ack <delivery-id>] [--peek] [--json] [-- <orca check args>...]
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
receipt prints as its handle. A wait that ends empty prints `timeout`. The
runner uses `--json`: one compact JSON object per non-heartbeat message, including
its lane, followed by the same delivery line.

The runner processes every message, then passes the printed delivery id as
`--ack <delivery-id>` on its next call. This is
`result.deliveryId`, never a message id. A message id acknowledges nothing, and an
unacknowledged batch replays.

An escalated question can be recorded as awaiting a
root ruling before acknowledging its delivery; the question's message id remains
the reply address. Never answer a stale question to a replacement dispatch;
[O10](../SKILL.md#the-orca-desk) records the current judge path's missing fence.

`--peek` prints unread messages without waiting or marking them read. It does not
acknowledge a batch, even if `--ack` is supplied beside it. Arguments after `--`
pass through to `orca orchestration check`. Only the orca runner uses `--run <id>`;
a worker uses `--terminal <handle>`.

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
`error <code>: <message>` and exits 1 without retrying. The runner emits
`ORCA-CHECK` and continues its loop; it never restarts Orca. It reads actions from
the store, never an inbox cursor. [orca-desk-brief.md](orca-desk-brief.md) gives the
loop and escalation contract.

## Worked example: a v3 drive

Fill the runner config from [the template](orca-desk-brief.md#config-and-startup)
and start both processes detached there. The spec directory holds `common.md`
and `ci-fix.md`. Build the brief and submit a launch:

```sh
SPEC_DIR='/absolute/drive/briefs'
CONFIG='/absolute/drive/runner.json'
cat "$SPEC_DIR/common.md" "$SPEC_DIR/ci-fix.md" > "$SPEC_DIR/ci-fix.full.md"
desk-runner.py launch --config "$CONFIG" --key R1 --lane ci-fix --model opus --effort xhigh --brief "$SPEC_DIR/ci-fix.full.md"
```

Submit a ruling with its existing R number. For a question, retain its message id:

```sh
desk-runner.py relay --config "$CONFIG" --key R2 --lane ci-fix --text '<ruling>' --reply-to '<question msg id>'
desk-runner.py show --config "$CONFIG"
```

The root never appends to an orca-desk inbox or `SendMessage`s a desk. It arms one
Monitor on `tail -n 0 -F <escalations file>` and re-arms on expiry. The orca runner
alone reads and acknowledges Run deliveries.
