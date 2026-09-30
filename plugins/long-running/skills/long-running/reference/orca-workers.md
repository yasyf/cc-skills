# Orca workers: launch recipe

The orca-desk launches every worker through `scripts/orca-launch.sh` and owns the
foreground `scripts/orca-check.sh` loop. Spawn it beside the landing-desk before the
first Orca worker. The root sends briefs and rulings; R9 keeps worker mechanics in
the desk. Its spawn brief is [orca-desk-brief.md](orca-desk-brief.md).

## Launching

Orca launches `claude` with the default agent arguments in the user's settings, including
`--permission-mode plan`. A worker needs a terminal created with an explicit
command. Keep the interactive default in plan mode. Carry every other default
argument into `ORCA_LAUNCH_CLAUDE_ARGS`; `--channels plugin:cc-review@cc-review` below
stands for those arguments, not a fixed channel requirement.

`orca-launch.sh` creates the worktree and terminal, then attaches the worker with
these commands:

```sh
orca worktree create --name "<lane>" --repo "id:<repo id>" --base-branch "origin/<base>" \
  --parent-worktree "path:<coordinator worktree>" --setup run --json
orca terminal create --worktree "path:<wt>" --json \
  --command "claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --channels plugin:cc-review@cc-review --model <id> --effort <level>"
orca orchestration worker-start --run "<run>" --spec "<pointer>" \
  --worktree "path:<wt>" --terminal "<handle>" --json
```

`<handle>` is `result.terminal.handle` from `terminal create`. On `worker-start`,
`--model` and `--effort` cannot combine with `--terminal`; put both on the custom
`claude` command. Shift-tab is a fallback only before the worker opens a plan.
The launch script checks the terminal screen for `bypass permissions on`.

Every worktree is created with `--parent-worktree path:<coordinator worktree>`.
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

`send --type` accepts only
`status|dispatch|worker_done|merge_ready|escalation|handoff|decision_gate|question|heartbeat`.
Workers use the preamble's `orca orchestration ask` for a ruling. If it returns
`capacity reached`, they send `--type question` or `--type escalation` and keep
working on everything that does not depend on the answer. The desk treats either
message like an ask.

**R56. Reply to the original question id.** A worker blocked on
`orca orchestration ask` wakes only on
`orca orchestration reply --id <its question message id>`. A plain
`send --type dispatch` can sit unread in a background wait. Desks must reply to the
original question id and may add a send for context.

*Prevents a worker staying blocked after the desk sent its ruling as a dispatch.*

Finish with `send --type worker_done --outcome succeeded|failed`
and the preamble's `--task-id <taskId>` and `--dispatch-id <dispatchId>`.

Orca workers are separate sessions. They run `ledger.py register/report` themselves
to report to the landing desk and never `SendMessage` a subagent. Read a worker's
output by dispatch:

```sh
orca orchestration worker-read --dispatch "<id>"
```

A check from a worker terminal must name `--terminal <its handle>`. A `--run` check
from a non-coordinator terminal fails `consumer_fenced`. The desk consumes the run's
inbox from the coordinator terminal; workers consume their own terminal inboxes.

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
| `ORCA_LAUNCH_BOOT_SECONDS` | Wait for Claude to start and between screen checks; default `8`. |

A successful launch prints one receipt line in this shape:

```text
<lane> ready task=<id> dispatch=<id> terminal=<handle> worktree=<path>
```

Count the launch only after that line. The worker-start result must read `ready`,
and the screen must show bypass permissions on. A failed launch prints
`<lane> failed <step and reason>` and exits 1; invalid usage exits 2.

The receipt is `<receipt dir>/<lane>.json`; `<lane>.terminal` holds the handle that
`orca-check.sh` maps back to the lane, and `<lane>.worktree` the path Orca created.
A failed `worker-start` that returned a task and dispatch still writes the receipt,
so the next launch retries that dispatch instead of opening a second task. The
brief path is made absolute before it goes into the pointer. For a relaunch approved by the root, run the
same command with the same lane and receipt directory. The script reads
`result.taskId` and `result.dispatchId` from the recorded receipt and passes
`--task <taskId> --retry-of <dispatchId>` instead of creating a new task from `--spec`.
It creates a new terminal and keeps the worktree. A follow-up alone is not a
relaunch: edit the brief file, then send its pointer with `send --type dispatch`
to the current dispatch.

Worktree creation gets four attempts; terminal creation gets three, separated by
`ORCA_LAUNCH_RETRY_SECONDS`. The script waits `ORCA_LAUNCH_BOOT_SECONDS` for startup
and checks the screen up to five times. A failed `worker-start` returns immediately;
it is not one of those retry loops.

### `orca-check.sh`

The script runs one check, not the desk's whole loop:

```text
usage: orca-check.sh [--ack <delivery-id>] [--peek] [-- <orca check args>...]
```

Without `--peek`, it calls
`orca orchestration check --wait --types worker_done,escalation,question`.
The types select what wakes the wait; the returned delivery is the whole batch.
Each non-heartbeat message prints on one line, followed by the delivery to acknowledge:

```text
<msg id> <type> <lane> <subject>: <body on one line>
delivery <delivery id> heartbeats=<n>
```

The lane comes from the launch receipt's terminal handle; a sender with no matching
receipt prints as its handle. A wait that ends empty prints `timeout`.

Process every message, then pass the printed delivery id as `--ack <delivery-id>`
on the next call. This is
`result.deliveryId`, never a message id. A message id acknowledges nothing, and an
unacknowledged batch replays.

An escalated question can be recorded as awaiting a
root ruling before acknowledging its delivery; the question's message id remains
the reply address. A stale question from a stopped or superseded dispatch is
acknowledged without an answer.

`--peek` prints unread messages without waiting or marking them read. It does not
acknowledge a batch, even if `--ack` is supplied beside it. Arguments after `--`
pass through to `orca orchestration check`. Use `--run <id>` for the desk and
`--terminal <handle>` for a worker.

| Variable | Meaning and default |
|---|---|
| `ORCA_CHECK_TIMEOUT_MS` | Longest wait; default `60000`. Keep it at most 60000 so the desk reads its inbox file every minute. |
| `ORCA_CHECK_STATE` | Launch receipt directory; default `~/.claude/scratch/orca-launch/<run>`. |
| `ORCA_CHECK_RETRY_SECONDS` | Wait before a connection retry; default `30`. |

A lost connection or a `runtime_unavailable` error retries once, then prints
`connection-lost` and exits 1, so the desk is back at its inbox file within the
minute. Any other Orca error response prints `error <code>: <message>` and exits 1 without retrying.
On either failure, return to the inbox-file step before the next check; never restart
Orca. Read the append-only inbox with its saved cursor at the top of every iteration,
including after `timeout`; [orca-desk-brief.md](orca-desk-brief.md) gives the loop.

## Worked example: a v3 drive

From the coordinator terminal, the orca-desk fills these paths and ids. The spec
directory already holds `common.md` and `ci-fix.md`; the inbox file is append-only.
Replace the channel argument with the other arguments in Orca's defaults.

```sh
SCRIPTS='<plugin root>/skills/long-running/scripts'
SPEC_DIR='<spec dir>'
INBOX='<inbox file>'
export ORCA_LAUNCH_RUN='<run id>'
export ORCA_LAUNCH_REPO='<repo id>'
export ORCA_LAUNCH_PARENT='<coordinator worktree>'
export ORCA_LAUNCH_PREFIX='v3-'
export ORCA_LAUNCH_STATE='<receipt dir>'
export ORCA_LAUNCH_CLAUDE_ARGS='--channels plugin:cc-review@cc-review'
export ORCA_LAUNCH_RETRY_SECONDS=30 ORCA_LAUNCH_BOOT_SECONDS=8
export ORCA_CHECK_STATE="$ORCA_LAUNCH_STATE"
export ORCA_CHECK_TIMEOUT_MS=60000 ORCA_CHECK_RETRY_SECONDS=30
touch "$INBOX"
cat "$SPEC_DIR/common.md" "$SPEC_DIR/ci-fix.md" > "$SPEC_DIR/ci-fix.full.md"
"$SCRIPTS/orca-launch.sh" ci-fix opus xhigh "$SPEC_DIR/ci-fix.full.md"
"$SCRIPTS/orca-check.sh" -- --run "$ORCA_LAUNCH_RUN"
```

After processing that batch and reading new inbox-file lines, acknowledge its
printed delivery on the next wait:

```sh
"$SCRIPTS/orca-check.sh" --ack '<delivery id>' -- --run "$ORCA_LAUNCH_RUN"
```

The root appends each ruling as one numbered line. It never sends a ruling by
`SendMessage` to the looping desk:

```sh
printf '%s\n' 'R1 <msg id> ci-fix: <ruling>' >> '<inbox file>'
```
