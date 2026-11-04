# Orca workers: launch and mailbox adapters

## Contents

- [Choose the remote or local worker route](#choose-the-remote-or-local-worker-route)
- [Launch local Claude and Codex workers](#launch-local-claude-and-codex-workers)
- [Launch fast-tier incident workers](#launch-fast-tier-incident-workers)
- [Send and answer worker messages](#send-and-answer-worker-messages)
- [Protect sessions and throttle on load](#protect-sessions-and-throttle-on-load)
- [Run `lr launch start`](#run-lr-launch-start)
- [Run `orca-check.sh`](#run-orca-checksh)

## Choose the remote or local worker route

**Default ordinary implementation and test workers to a Sprite.** Under O16 in [reference/orca-desk-brief.md](orca-desk-brief.md), `lr launch worker`, described in [orca-sprite-workers.md](orca-sprite-workers.md), defaults to Sprite prepare+attach, with a local Mac route for the coordinator, Fable, incident lanes, incident clock fallbacks, and standing desks (`role=desk` or `role=watch`). Preserve the requested model, effort, and Codex service tier. Use the repository's complete VM brief and [source-return contract](orca-sprite-workers.md#return-source-for-root-review) so the remote worker returns a patch or report that the coordinator collects, reviews, and ships. Remote API keys stay in the worker process; local Fable uses the Mac's interactive authentication.

**Keep remote workers on their native Run and consumer.** Canonical remote workers use their own native Run, one home-inbox consumer, and home Dispatch IDs. Do not enroll remote workers in local `lr launch start` or `orca-check.sh`, apply raw-TTY wakes or unsupervised retries to them, or add a second consumer. Namespace runs on regular Compute through an explicit opt-in configuration; the recipe picker is separate from this direct CLI route.

**Drive local workers through the relay.** The rest of this file describes the local desktop adapters, `lr launch start` and `orca-check.sh`, that `lr relay run` calls. Only the relay consumes the Run mailbox, and the coordinator issues launches and relays through `lr launch` and `lr relay` as [reference/orca-desk-brief.md](orca-desk-brief.md) describes.

## Launch local Claude and Codex workers

**Build explicit Claude terminal commands that disallow prompts.** Orca's default user settings launch `claude` with `--permission-mode plan`. Keep the interactive default in plan mode, and create every worker terminal with an explicit command that sets `bypassPermissions` and `--disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode` so a worker never blocks on an unwatched terminal prompt. Pass any other default arguments through `ORCA_LAUNCH_CLAUDE_ARGS`; `--channels plugin:cc-review@cc-review` below stands for those arguments, not a fixed requirement.

**`lr launch start` creates the worktree, creates the terminal, and starts the worker.** By default `orca worktree create` makes a full checkout wherever Orca puts worktrees:

```sh
orca worktree create --name "<lane>-<run>-<n>" --repo "id:<repo id>" --base-branch "origin/<base>" \
  --parent-worktree "path:<coordinator worktree>" --setup run --json
orca terminal create --worktree "path:<wt>" --json \
  --command "env <claudeLaneEnv> claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools <laneDeniedTools> --strict-mcp-config --no-chrome --channels plugin:cc-review@cc-review --model <id> --effort <level> --settings '{<claudeLaneSettings>}'"
orca orchestration worker-start --run "<run>" --spec "<pointer>" \
  --worktree "path:<wt>" --terminal "<handle>" --json
```

No human reads a lane, so it starts stripped: no plugin or CLI auto-update, no `/rewind` backups, no native file watcher on `~/.claude` (`CHOKIDAR_USEPOLLING`), so no FSEvents client in `fseventsd`, no plugin-dir polling, no animation, title, mouse or status line, no telemetry, error reports or surveys, no bundled skills or cron tools, and no git status in its prompt. `lr/internal/orca/lane_env.go` holds the lists, and `lane_env`, `lane_settings` and `lane_denied_tools` in the orca plugin's `orca-worker-launch.sh` mirror them. Set `CLAUDE_LANE_SETTINGS` to a JSON object to add local settings, such as `enabledPlugins` entries that a machine's lanes never use.

`<handle>` is `result.terminal.handle` from `terminal create`. On `worker-start`, `--model` and `--effort` cannot combine with `--terminal`; put both on the `claude` command. Shift-tab is a fallback only before the worker opens a plan. The script checks the terminal screen for `bypass permissions on`. A launch that fails before `worker-start` removes the worktree its create made with `orca worktree rm --force`.

**Set `ORCA_LAUNCH_SPARSE=1` for sparse lanes in a large repo.** `orca worktree create` has no sparse option, and a `git worktree add` from the main checkout copies no sparse config either, so each monorepo lane held the whole tree, 34,196 entries and 0.56 GB. With `ORCA_LAUNCH_SPARSE=1` the script adds the worktree with git at `<root>/<lane>-<run>-<n>`. `<root>` is `ORCA_LAUNCH_ROOT`, else `<ccx.worktreeRoot>/<main checkout's name>` when the main checkout's git config sets `ccx.worktreeRoot`, else the coordinator worktree's directory.

When HEAD tracks more than `ORCA_LAUNCH_SPARSE_FILES` files, 5,000 by default, the script checks out only the coordinator's files, `.agents`, `.claude`, `tools`, `infra/ci/hooks/lib`, and the directories the brief's `ccx:` line names in `paths=`. The monorepo's `tools/ci` sources `infra/ci/hooks/lib`. That cone is about 1,800 entries in the monorepo, and a smaller repo keeps a full checkout. Orca lists the worktree once it reads git's registry, and `orca worktree set` gives it Orca's name and parent:

```sh
git -C "<main checkout>" worktree add --no-checkout --no-track -b "<user>/<lane>-<run>-<n>" "<root>/<lane>-<run>-<n>" "origin/<base>"
printf '%s\n' .agents .claude tools infra/ci/hooks/lib <paths> | git -C "<wt>" sparse-checkout set --cone --stdin
git -C "<wt>" reset --hard
orca worktree set --worktree "path:<wt>" --display-name "<lane>-<run>-<n>" \
  --parent-worktree "path:<coordinator worktree>" --json
```

Like `orca worktree create`, the script links the repo's Orca `symlinkPaths` from the main checkout and copies each existing, gitignored path `.worktreeinclude` names. It runs no Orca setup hook; put one in `ORCA_LAUNCH_PREPARE`, which runs in the sparse checkout. The script prints the cone and the widen command on stderr, `git -C <wt> sparse-checkout add <dir>`, and the worker's pointer says "This lane is sparse: widen with `git sparse-checkout add <dir>` before reading outside it." `git sparse-checkout disable` restores the full tree. A sparse launch that fails before `worker-start` hands its worktree to `ccx vcs worktree rm --path <wt> --force` and deletes the branch it created.

**Tag non-incident worktrees to the coordinator worktree.** Except for incident lanes, worktrees use `--parent-worktree path:<coordinator worktree>`. A relaunch resumes in the worktree its `<lane>.worktree` receipt names while that worktree is still a checkout; re-tag an existing worktree before launch when it lacks that parent:

```sh
orca worktree set --worktree "path:<wt>" --parent-worktree "path:<coordinator worktree>"
```

**Pass a brief pointer, never the brief text, in `--spec`.** Attach the combined shared contract and lane section as `<lane>.full.md` on the drive's briefs log according to [reference/orca-lane-brief.md](orca-lane-brief.md). `--spec` points to its resolved file path, symlinked to a short path when the pointer exceeds 500 characters, because Orca truncates pasted prompts near 3 KB.

**Select models and effort levels by role.** Effort is `low`, `medium`, `high`, `xhigh`, or `max`. The coordinator runs Fable 5.1 on the Mac. Ordinary Claude implementation workers default to Opus, and subdesks use Opus or Sonnet according to the routing table. Beyond the coordinator, Fable is for exceptional, highly sensitive local implementation only, and auth, migrations, concurrency, or error-prone code alone do not qualify; never use Fable as a general fallback or set `fallbackModel`. Visual design goes to gpt-6.1-sol through codex, with a Fable pass beside it on a page redesign; "A design lane's part" in [orca-lane-brief.md](orca-lane-brief.md#a-design-lanes-part) has the rule.

| Alias | Model id |
|---|---|
| `opus` | `claude-opus-5-5` |
| `sonnet` | `claude-sonnet-5-5` |
| `fable` | `claude-fable-5-1` |
| `sol`, `codex` | `gpt-6.1-sol` on Orca's codex agent, standard tier |
| `incident` | `gpt-6.1-sol` in its own terminal, fast tier |
| `incident-opus` | `claude-opus-5-5` in Claude fast mode, the incident fallback |
| `astra` | `gpt-6-astra` on Orca's codex agent, exceptional cases only |

**Launch standard Codex workers in their own terminal.** A lane routed to Codex runs Codex directly, never as a Claude worker calling the codex skill. `lr launch start <lane> sol xhigh <brief>`, where `codex` is the same alias, creates the worktree and a terminal running the command below, then passes `--terminal` to `worker-start`:

```sh
codex --dangerously-bypass-approvals-and-sandbox -c model=gpt-6.1-sol -c model_reasoning_effort=xhigh <codexLaneConfig> -c mcp_servers={} -c mcp_servers.<name>.enabled=false ...
```

`worker-start --agent codex` takes no launch flags, so it could not carry this list. `codexLaneConfig` in `lr/internal/orca/lane_env.go` turns off plugins, the remote plugin catalog, memories, apps, skill search, analytics, feedback, `notify`, history, and TUI notifications and animations. Every server in `config.toml` is off except `datadog`, `sentry`, and whatever `ORCA_LAUNCH_CODEX_MCP` names.

## Launch fast-tier incident workers

**Launch incident fix and evidence lanes with `lr launch start <lane> incident xhigh <brief>`.** Because `worker-start` has no service-tier flag, the script creates a top-level (`--no-parent`) worktree and a custom terminal running Codex on the fast tier, then starts the worker on that terminal:

```sh
orca terminal create --worktree "path:<wt>" --title "<name>" --json \
  --command "codex --dangerously-bypass-approvals-and-sandbox -c model=gpt-6.1-sol -c service_tier=fast -c model_reasoning_effort=xhigh -c mcp_servers={}"
orca orchestration worker-start --run "<run>" --spec "<pointer>" \
  --worktree "path:<wt>" --terminal "<handle>" --timeout-ms 90000 --json
```

Codex 0.160 resolves `service_tier=fast` to the catalog id `priority`, labeled Fast, and records `service_tier: "priority"` in the session settings. Do not pass `--skip-git-repo-check`; it is exec-only and breaks interactive Codex. Only `incident` lanes run on the fast tier. Keep Orca's codex runtime config at `~/Library/Application Support/orca/codex-runtime-home/home/config.toml` set to `service_tier = "default"` so `sol` and `codex` lanes stay on the standard tier. Codex lanes mount `datadog` and `sentry` Model Context Protocol servers from `~/.codex/config.toml`, and `codex-ask` mounts both by default, using the OAuth sign-in from `codex mcp login <server>`.

**Launch the Opus incident backup on `incident-opus`.** The relay's `<lane>-opus` fallback launches as `incident-opus xhigh`. The alias puts `--settings '{"fastMode":true}'` on the worker's own command line, so fast mode never depends on runtime config, and a session launched with it reports `fast_mode_state: on` even under a user-level `fastModePerSessionOptIn`. No other alias turns fast mode on.

**Handle Codex readiness timeouts as `unsupervised` launches.** Orca's readiness check does not recognize Codex, so `worker-start` can end with `state=failed`, `failedStage=agent_readiness`, `lastError=timeout` while Codex sits idle at its prompt with the spec undelivered. On that timeout with a live `codex` or `incident` terminal, `lr launch start` types the spec pointer into the terminal itself and prints `<lane> unsupervised task=... dispatch=... terminal=... worktree=...`. Count `unsupervised` as launched. The lane reports with `cci post --drive <drive> --lane <lane> --kind report --to root --text "<report>"` and reads deliveries with `cci tail --drive <drive> --cursor <lane> --reader <lane>`, without Orca `worker_done` or escalation plumbing.

## Send and answer worker messages

**Use valid Orca orchestration message types.** `orca orchestration send --type` accepts only `status|dispatch|worker_done|merge_ready|escalation|handoff|decision_gate|question|heartbeat`. Workers ask for a ruling, report, and raise blockers only with `cci post --kind ask|report|blocker`; the preamble's `send` and `ask` carry the dispatch lifecycle alone. The relay still carries any `question`, `escalation` or `status` a worker sends into Run home to the lane's desk, or the coordinator when no desk launched it, once per message id. Finish a worker with `send --type worker_done --outcome succeeded|failed` using the preamble's `--task-id <taskId>` and `--dispatch-id <dispatchId>`.

**R56. Reply to the original question id.** Post a cci `answer` with `--re` under O4 in [reference/orca-desk-brief.md](orca-desk-brief.md). The relay verifies the current dispatch and runs `orca orchestration reply --id` for the original question id. Only `reply --id` wakes an `orca orchestration ask` wait; a dispatch message does not. The relay wakes a terminal after a guidance relay, not after a question reply.

**Record PRs and read worker output.** Orca workers are separate sessions that register prefixes and report verdicts through `ledger.py` themselves and never `SendMessage` a subagent. In a Claude worker, `ccx vcs ship` records each opened PR in the ledger and as an `opened` record on cci by using its hook, so the worker posts no `opened` line by hand. A Codex worker runs without a PR hook or a bound cci lane, so it runs the `drive.py record --opened` command from its brief's `## PR record` section after each ship that opens a PR. Read a worker's output by dispatch:

```sh
orca orchestration worker-read --dispatch "<id>"
```

A mailbox check from a worker terminal must pass `--terminal <its handle>`; a `--run` check from a non-coordinator terminal fails with `consumer_fenced`.

## Protect sessions and throttle on load

**R195. Sessions are protected.** Claude and Codex sessions, Orca, terminal hosts, pseudo-terminal daemons, and their supervisors are never stopped, signalled, suspended, restarted, released, or closed, singly or in bulk, for cleanup or load, except through the two coordinator `gc` paths below:
1. Settled idle terminals: Because `lr launch start` creates every Claude terminal externally, `orca orchestration worker-release` returns `retained` (`external_terminal`) and never closes the tab. After a lane sends `worker_done` and the relay's sweep emits `RECLAIM`, the coordinator runs the `orca.gc` command, this skill's `lr gc --run <run> [--dispatch <ctx>]`, from the drive checkout. `lr gc` never touches a live or unsettled worker or a terminal outside the Run, except a retired, silent lane the supervisor names with `--stale` under the proof below. It closes a settled dispatch's terminal with `orca terminal close --terminal <handle> --tab` only when the terminal is idle; right before closing, it verifies an empty prompt, nothing running, no unanswered question, and the same agent process it planned on: pid, parent, process group, start time and full command, for a claude or codex under that terminal's login shell. A status-line repaint is not activity, so output timing plays no part. A tree holding unsaved work keeps its terminal open. It removes the worktree with `orca worktree rm` only when clean, fully pushed or landed, with no open PR, and without another attached terminal in `orca terminal list`.
   The lane supervisor runs `lr gc --new` every five minutes from the drive checkout, so a dispatch is reclaimed soon after it settles without anyone running gc. Each pass takes up to forty settled dispatches, those no pass has swept first, then those swept longest ago, so what a pass kept is read again on a later one. A Sprite whose Orca runtime is down is read through `sprite exec`: no claude or codex process on it and none working in its checkout, plus a clean, pushed checkout, destroys it. A dispatch on a server Orca no longer knows is skipped in silence. A lane the registry retired that has posted nothing for two hours, whose dispatch never settled and whose brief names no `Standing:`, goes to gc as `--stale`: gc ends it only when its screen sits at an empty prompt, one verified agent process names its terminal, and its tree is clean and pushed; anything it cannot read keeps the session and names why. Root and owner-relay never go. A pass that moved anything posts a `session-reap` state record, and `lr update` prints the newest six under Session reap with before and after counts. A dispatch whose lane the registry still wants (desired, held or launching) goes to gc as `--keep`, which treats it as live, its terminal and worktree included. `lr lanes --idle` prints the census and changes nothing: claude and codex processes, Orca worktrees and terminals, and what a whole-Run gc would close, remove, destroy and keep.
2. Done lanes (`--done`): Once the coordinator decides a lane of its own Run is done because it is stood down, superseded, or deliverable on disk, the coordinator runs `lr gc --run <run> --done <ctx>` or `lr launch withdraw`. That call runs `worker-stop`, closes the terminal without waiting for an idle read, and reclaims the worktree under the same cleanliness checks. Never message the lane to send `worker_done` first. The captain-hook sessions guard allows a bare `orca orchestration worker-stop --dispatch <ctx>` only from the coordinator coordinating that dispatch's Run.

A launch that fails before `worker-start` dispatches, such as `consumer_fenced` or a boot timeout, is not a session; `lr launch start` closes the tab it opened and removes any worktree it created.

**R210. Load is the only launch throttle.** Working workers have no count cap. Before each Mac launch, read `uptime`. Mac load holds launches under O14 in [orca-desk-brief.md](orca-desk-brief.md). If the Orca runtime drops connections under load, retry after 30 seconds; never restart Orca to recover a connection. After an Orca restart, re-list workers and terminals and update recorded handles.

## Run `lr launch start`

**Pass the lane, model, effort, and resolved brief attachment path.** `lr launch start` makes `<brief-file>` absolute before it goes into the pointer.

```sh
lr launch start <lane> <model> <effort> \
  "$(ccn -R <drive checkout> attachment path <briefs log> <lane>.full.md)"
```

```text
usage: lr launch start <lane> <model> <effort> <brief-file>
```

| Variable | Meaning and default |
|---|---|
| `ORCA_LAUNCH_RUN` | Orchestration run id; defaults to `drive.py orca-run` (recorded by `drive.py start --orca-run`). |
| `ORCA_LAUNCH_REPO` | Orca repo id; defaults to the repo Orca registers at the parent's main checkout (`git rev-parse --git-common-dir`). Its record names the main checkout the worktree is added from, the branch prefix, and the paths to link. |
| `ORCA_LAUNCH_PARENT` | Coordinator worktree path; defaults to `$PWD`. |
| `ORCA_LAUNCH_NO_PARENT` | `1` creates a top-level (`--no-parent`) worktree instead of a child of `ORCA_LAUNCH_PARENT` (always set for `incident`); defaults to unset. |
| `ORCA_LAUNCH_PREFIX` | Worktree name prefix; defaults to none. Each new worktree is `<prefix><lane>-<run>-<n>` on branch `<user>/<prefix><lane>-<run>-<n>`. `<run>` is the first 8 characters of the Run id, `<user>` is the repo's Orca `gitUsername`, and `<n>` counts past every name the lane's `<lane>.names` receipt records and every existing path or branch. A name is never cut twice, and the branch never blocks `<user>/<prefix><lane>/` branches. |
| `ORCA_LAUNCH_ROOT` | Directory a new worktree goes in. A sparse lane's defaults to `<ccx.worktreeRoot>/<main checkout's name>` when the main checkout's git config sets `ccx.worktreeRoot`, else to the coordinator worktree's directory. `orca worktree create` puts a default lane's worktree where Orca chooses, and the script finds it by name. |
| `ORCA_LAUNCH_BASE` | Base branch; defaults to the parent checkout's `origin/HEAD`. |
| `ORCA_LAUNCH_STATE` | Receipt directory; defaults to `$XDG_STATE_HOME/long-running/orca-launch/<run>`, with `$XDG_STATE_HOME` defaulting to `~/.local/state`. Leave unset on hand launches. |
| `ORCA_LAUNCH_CLAUDE_ARGS` | Extra arguments from Orca's agent defaults (excluding plan mode); defaults to none. |
| `ORCA_LAUNCH_BOOT_SECONDS` | Ceiling in seconds waiting for Orca to detect the terminal's idle agent; defaults to `180`. |
| `ORCA_LAUNCH_WORKTREE_SECONDS` | Ceiling in seconds on each worktree wait: a failed create registering, its checkout filling, and Orca listing a worktree the launch added or resumes; defaults to `180`. |
| `ORCA_LAUNCH_PREPARE` | Optional shell command run in the new worktree before worker start, such as code generators, or `tools/hooks/warm-lane.sh` in a repo that has it; never a JavaScript dependency install. |
| `ORCA_LAUNCH_PREPARE_SECONDS` | Timeout in seconds for `ORCA_LAUNCH_PREPARE`; defaults to `120`. |
| `ORCA_LAUNCH_SPARSE` | `1` adds each new worktree with `git worktree add`, sparse in a large repo, instead of `orca worktree create`; defaults to unset. |
| `ORCA_LAUNCH_SPARSE_FILES` | Tracked-file count above which a worktree `ORCA_LAUNCH_SPARSE` adds is sparse; defaults to `5000`. |

**Verify the launch output line and receipt files.** A successful launch prints one of:

```text
<lane> ready task=<id> dispatch=<id> terminal=<handle> worktree=<path>
<lane> unsupervised task=<id> dispatch=<id> terminal=<handle> worktree=<path>
```

A failed launch prints `<lane> failed <step and reason>` and exits 1; invalid usage exits 2. The script writes `<receipt dir>/<lane>.json`, `<lane>.terminal` holding the handle `orca-check.sh` maps back to the lane, and `<lane>.worktree`. Even when `worker-start` fails after allocating a task and dispatch, the script writes the receipt so a permitted retry addresses that dispatch instead of opening a second task.

A stalled launch retries once. One wait outlasting `ORCA_LAUNCH_STALL_SECONDS` fails a launch with `LAUNCH-FAILED: <wait> for <n>s`. When that happens before any `worker-start` call and the rollback left nothing behind, the same process starts the lane again under the next worktree name. An `incident` lane retries at once, and any other lane after 30 seconds. Stderr says `<lane> retries once after a stall: <wait>`, and only a second failure prints a failure line, which ends `; this was its one retry`. A refusal, a failure after `worker-start`, and a rollback that kept anything never retry.

A rollback deletes the branch its launch cut unless that branch holds commits not on the base, and the failure line names anything it keeps with the reason in parentheses.

**Retry failed or blocked tasks without duplicating sessions.** Retry a failed dispatch only after confirming its session has ended and getting the coordinator's ruling. Run the same command with the same lane and receipt directory. When `orca orchestration task-list --status failed` or `--status blocked` lists the receipt's `result.taskId`, the script passes `--task <taskId> --retry-of <dispatchId>` instead of `--spec`, creating a new terminal while keeping the worktree. If the recorded task completed, such as an incident lane relaunched for a recurring alert, it opens a fresh task from `--spec`. A follow-up alone is not a relaunch; edit the brief file, then send its pointer with `send --type dispatch` to the current dispatch.

**Understand internal boot and checkout retries.** Worktree creation gets four attempts and terminal creation gets three.
- Worktree registration and index: Before each attempt and for up to `ORCA_LAUNCH_WORKTREE_SECONDS` (180 s) after a failed create, the script polls `orca worktree show` to adopt a worktree that finished registering after a dropped connection. It then waits within the same ceiling until `git diff-index --cached HEAD` is empty so `git reset --hard` has finished populating the checkout. A sparse lane's `git worktree add` and `reset --hard` finish before the script moves on, so it waits only for Orca to list the new worktree.
- Terminal creation and agent readiness: If `terminal create` times out after 10 seconds without a handle, the script polls the worktree every four seconds for up to `ORCA_LAUNCH_BOOT_SECONDS` (180 s) to adopt the late terminal before retrying. Before calling `worker-start`, the script blocks on `orca terminal wait --for tui-idle` up to `ORCA_LAUNCH_BOOT_SECONDS`. If a third of that ceiling passes with no idle agent and the screen shows no command-line prompt and no agent UI, the script sends the startup command once with `orca terminal send`. If the agent is still not idle at the ceiling, the script fails with `boot terminal=<handle>: orca terminal wait --for tui-idle reads <timeout, status, or blocked prompt>` without calling `worker-start`. After a Claude start, it checks the screen for `bypass permissions on` up to 10 times, four seconds apart. A `worker-start` the Orca runtime did not answer (`runtime_timeout`, `runtime_unavailable`, `remote_runtime_unavailable`, or `invalid_runtime_response`, with an `orchestrationRequestId`) is reissued with `--retry-request <id>` every four seconds until `ORCA_LAUNCH_BOOT_SECONDS`, and the failure line names its tries. Any other failed `worker-start` returns immediately without retrying.

## Run `orca-check.sh`

**Check worker mailboxes.**

```text
usage: orca-check.sh [--peek] [-- <orca check args>...]
```

- Blocking or peek check for worker terminals only: Do not start a blocking Run check beside the relay. Workers pass `-- --terminal <handle>`. Without `--peek`, the script calls `orca orchestration check --wait --types worker_done,escalation,question`, prints each non-heartbeat message and the batch delivery id, or prints `timeout`:

```text
<msg id> <type> <lane> <subject>: <body on one line>
delivery <delivery id> heartbeats=<n>
```

The lane comes from the launch receipt's terminal handle; a sender with no matching receipt prints as its handle. Acknowledge the previous batch by passing `-- --ack <delivery-id>`, using `result.deliveryId` and never a message id. `--peek` prints unread messages without waiting or marking them read.

| Variable | Meaning and default |
|---|---|
| `ORCA_CHECK_TIMEOUT_MS` | Maximum mailbox wait in milliseconds; defaults to `60000` (keep at most `60000`). |
| `ORCA_CHECK_STATE` | Launch receipt directory; defaults to `$XDG_STATE_HOME/long-running/orca-launch/<run>`, with `$XDG_STATE_HOME` defaulting to `~/.local/state`. |
| `ORCA_CHECK_RETRY_SECONDS` | Wait in seconds before a connection retry; defaults to `30`. |

A lost connection or `runtime_unavailable` error retries once after `ORCA_CHECK_RETRY_SECONDS`, then prints `connection-lost` and exits 1. Any other Orca error prints `error <code>: <message>` and exits 1 without retrying.
