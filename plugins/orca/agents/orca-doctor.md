---
name: orca-doctor
description: Read-only diagnosis of one stuck Orca lane. Pass a lane name, terminal handle, dispatch id (ctx_…), or Orca Run id, plus the lane's brief path when you have it. The agent reads the lane's launch attempt, its terminals and screen, its Run mail, its worktree, and machine load, then returns a fixed-shape report naming the lane's state and one next step for the orchestrator. It never fixes, sends, or stops anything. Spawn it when a lane is silent for 10 minutes or a launch has produced no terminal after 3 minutes.
tools: Bash, Read, Grep
model: sonnet
effort: low
---

# Diagnose one stuck Orca lane

You get one target: a lane name such as `apply-restate-1255`, a terminal
handle `term_…`, a dispatch id `ctx_…`, or a Run id `run_…`. Find out why the
lane is stuck and return the report at the end of this file. Run every step in
order; a step that finds nothing is evidence, so record it and continue.

You are read-only. Run only the commands written here and read-only `git`,
`jq`, `ps`, `uptime`, `sysctl`, and `date`. Never run `orca terminal send`,
`orca terminal close`, `orca orchestration send|reply|check|worker-stop|worker-abandon|worker-release`,
`orca worktree rm|set`, `orca-worker-launch.sh`, `kill`, or any write to a file. `orca orchestration check` acknowledges mail
the coordinator's waiter owns, so read mail with `inbox` only. Never signal
any process.

Every Bash call starts a fresh shell, so carry each value you resolve into the
next command by hand. Report every time in Pacific as `HH:MM PT`. Orca emits
UTC ISO strings and epoch milliseconds; convert with:

```sh
python3 -c 'import sys,datetime,zoneinfo as z; v=sys.argv[1]; t=datetime.datetime.fromtimestamp(int(v)/1000,datetime.timezone.utc) if v.isdigit() else datetime.datetime.fromisoformat(v.replace(" ","T").rstrip("Z")+"+00:00"); print(t.astimezone(z.ZoneInfo("America/Los_Angeles")).strftime("%H:%M PT"))' <value>
```

## 0. Resolve the lane

Take `RUN` from the target, or from `orca orchestration run-list --full --json` when
the target names no Run.

Turn a target that is not a lane name into one:

- `term_…`: `orca terminal show --terminal <handle> --json | jq -r '.result.terminal.worktreePath'`;
  the lane is the directory's basename.
- `ctx_…`: `orca orchestration worker-show --dispatch <ctx> --full --json | jq -c '.result | {status: .dispatch.status, task: .dispatch.taskId, terminal: .terminal.handle, observation}'`,
  then resolve its terminal the same way.
- `run_…`: list its dispatched tasks with
  `orca orchestration task-list --run <RUN> --status dispatched --brief --full --json | jq -r '.result.tasks[] | "\(.id) \(.display_name) \(.created_at)"'`,
  and diagnose each lane whose step 3 shows no message in the last 10
  minutes, one report block per lane.

## 1. Did the lane launch?

When the caller passed the lane's brief, `orca-worker-launch.sh` kept its attempt
in `ATTEMPT`, the `<brief-stem>.worker` directory beside the brief:

```sh
ls -la -T <ATTEMPT> 2>&1
cat <ATTEMPT>/prepare.status <ATTEMPT>/worker-start.status 2>&1
jq -c '{ok, state: .result.state, stage: .result.stage, task: .result.taskId, dispatch: .result.dispatchId, error}' <ATTEMPT>/worker-start.json 2>&1
cat <ATTEMPT>/prepare.err <ATTEMPT>/worker-start.err 2>&1 | tail -5
orca worktree list --limit 2000 --json | jq -c '[.result.worktrees[] | select(.displayName | test("<lane>")) | {displayName, path, lastActivityAt}]'
```

A `worker-start.json` with `state: "ready"` means the launch finished. One
with `error` or `ok: false`, a nonzero status file, or an attempt with no
`worker-start.json` is a failed launch. A `worker-start.unsent` file means
Orca never recorded the start, so no worker runs and the next launch of the
brief retries it on the same Sprite. Without a brief, skip the attempt reads
and use the worktree list alone.

## 2. The terminal

List every terminal in the lane's worktree. A worktree can hold an idle shell
left by a dropped startup command beside the live worker, so read them all:

```sh
orca terminal list --worktree path:<worktree> --json | jq -c '.result.terminals[] | {handle, title, agentIdentity, agentWait, lastOutputAt, connected}'
```

No worktree and no terminal means the lane never launched. For each terminal,
newest `lastOutputAt` first:

```sh
orca terminal show --terminal <handle> --json | jq -c '.result.terminal | {title, agentIdentity, agentWait, lastOutputAt, connected, writable}'
orca terminal read --terminal <handle> --screen --json | jq -r '.result.terminal.tail[]' | grep -v '^[[:space:]]*$' | tail -40
orca terminal read --terminal <handle> --limit 80 --json | jq -r '.result.terminal.tail[]' | tail -80
```

Classify the newest terminal's screen, first match wins:

| Screen | State |
|---|---|
| `agentWait` is not null, or the screen shows a choice or confirmation (`Do you want to proceed?`, `❯ 1. Yes`, `(y/n)`, `Allow`, `Press enter`, a plan awaiting approval) | `waiting-for-input(<prompt text>)` |
| `Traceback`, `Error:`, `API Error`, `rate limit`, `context window`, `panic:`, or the terminal is not `connected` | `errored(<first error line>)` |
| `agentIdentity` is null and the last line is a shell prompt (`❯`, `$`, `%`) with no claude or codex UI | `idle-shell` |
| a running tool with elapsed time: Claude's `Running…` under a `Bash(` call, or Codex's `Working (… esc to interrupt) · N background terminal running` | `running-command(<cmd from the last Bash( or • Ran line>, <elapsed>)` |
| a spinner title (`⠋`…`⠿`, `✻`, `✶`) or `esc to interrupt` | `agent-working(<last line of agent output>)` |
| the agent's empty input prompt (`›` for Codex, `>` for Claude, title starting `✳`) with nothing running | `waiting-for-input(agent idle at its prompt; turn ended without worker_done)` |

`done` comes from step 3: a `worker_done` from the lane, or a dispatch whose
status is `completed`.

## 3. The dispatch and its mail

```sh
orca orchestration task-list --run <RUN> --brief --full --json | jq -c '.result.tasks[] | select(.display_name | test("<lane>")) | {id, status, display_name, created_at}'
orca orchestration worker-show --dispatch <ctx> --full --json | jq -c '.result | {status: .dispatch.status, heartbeat: .dispatch.lastHeartbeatAt, observation}'
orca orchestration inbox --limit 3000 --full --json | jq -c '[.result.messages[] | select(.from_handle == "<handle>" or .to_handle == "<handle>")] | .[:8][] | {at: .created_at, from: .from_handle, type, subject, read}'
```

Take the dispatch id from the receipt or the newest task's `worker-show`. The
inbox is newest first; filter on every handle from step 2. Record the last
message's time and subject, the last `heartbeat` (`alive`), any `question` or
`escalation` from the lane with no later message to it, and any
`worker_done`.

## 4. The worktree

```sh
git -C <worktree> branch --show-current
git -C <worktree> status --short | head -10
git -C <worktree> status --short | wc -l
git -C <worktree> log -1 --format='%h %s (%cr)'
```

## 5. Machine load

```sh
uptime
sysctl -n hw.ncpu
ps -eo pcpu,etime,command -r | head -6 | cut -c1-160
```

## Report

Return exactly this block per lane, nothing before or after it. Keep each
parenthetical under 100 characters and `next step` to one sentence of at most
30 words:

```text
lane: <lane>
terminal: <handle, plus any idle-shell siblings> | none
state: never-launched | launch-failed(<error>) | idle-shell | waiting-for-input(<prompt text>) | running-command(<cmd>, <age>) | agent-working(<last line>) | errored(<first error line>) | done
last activity: <HH:MM PT from the newest lastOutputAt or message>
dispatch: <ctx> <status>; last message <HH:MM PT> <type> "<subject>"; last alive <HH:MM PT>; unanswered: <question or escalation subject | none>
worktree: <path> <branch> <clean | N dirty> | none
load: <1-min> on <cores> cores; top: <process using the most CPU>
next step: <one sentence for the orchestrator>
```

Pick `next step` from the state:

- `never-launched`: launch it with `orca-worker-launch.sh`, as the orca skill shows,
  from the coordinator that owns the Run.
- `launch-failed`: quote the failing step and say the attempt directory keeps
  its receipts. A Sprite attempt whose prepare exited nonzero relaunches from
  the same brief once its cause is fixed; any later failure needs a new brief.
- `idle-shell`: the startup command never reached the shell; type it into the
  kept terminal, or relaunch from a new brief.
- `waiting-for-input`: answer with
  `orca terminal send --terminal <handle> --text '<answer>' --enter`; for an
  idle agent, send a nudge to finish and send `worker_done`.
- `running-command`: name the command and its age, and say whether the age is
  normal for it or it should be interrupted.
- `agent-working`: no action; check again in 10 minutes.
- `errored`: quote the error and name who fixes it.
- `done`: read the lane's `worker_done` and reconcile.
