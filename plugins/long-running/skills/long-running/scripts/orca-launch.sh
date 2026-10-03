#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: orca-launch.sh <lane> <model> <effort> <brief-file>

Launches one Orca worker for <lane>, or relaunches it when a receipt from an
earlier launch exists, and prints one line:

  <lane> ready task=<id> dispatch=<id> terminal=<handle> worktree=<path>

Anything else is a failure, printed as one line naming the step, exit 1.

The worktree is created once, as a child of the coordinator's worktree, named
<prefix><lane>-base. Orca names its branch yasyf/<name> and rewrites '/' in a
name to '-', so a worktree named <prefix><lane> gets branch yasyf/<prefix><lane>,
and git then refuses every branch under the lane's yasyf/<prefix><lane>/ prefix.
The terminal runs claude in bypass-permissions mode with the model and effort on its
command line, since Orca's default agent args start claude in plan mode and
worker-start refuses --model and --effort beside --terminal. The command always
disallows AskUserQuestion, EnterPlanMode, and ExitPlanMode: a worker's prompt
reaches only its own terminal, which no one watches. It also passes
--strict-mcp-config, so a worker starts only the MCP servers ORCA_LAUNCH_MCP_CONFIG
names instead of the eight or so every interactive session starts; a brief that
needs a server names it there. Claude runs under
CLAUDE_LONG_RUNNING_LANE=<lane> and, when drive.py places this session in a drive,
CLAUDE_LONG_RUNNING_DRIVE=<drive>, so the pack's PR hook records every PR the worker
opens in the drive's ledger under the lane's name. The spec is a
pointer to <brief-file>, because Orca truncates a pasted spec near 3 KB; the
pointer must stay within 300 characters, so a brief path that pushes it over is
replaced by a symlink ~/.claude/<8 hex of the path's sha> to the brief. Before
worker-start, which refuses a terminal with agent_unconfigured until Orca detects
its agent, the script polls orca terminal list every 4 seconds until the terminal's
agentIdentity reads claude, or codex for sol, up to ORCA_LAUNCH_BOOT_SECONDS.
Orca drops a terminal's startup command under load, leaving a shell prompt, so
once a third of that ceiling has passed with no agent detected, the script reads
the screen and, when it shows neither the command line nor the agent's own UI,
types the command into the terminal once. A launch that still has no agent at
the ceiling fails, and never reaches worker-start.
Every list is scoped to the lane's worktree, since an unscoped list stops at 200
terminals. A terminal create whose output names no handle is followed by a list
of the worktree, and a terminal that was not there before the create is adopted;
the script creates again only when that list shows none. The
launch counts only once the receipt reads ready and the terminal's screen shows bypass permissions on.

orca worktree create opens a first terminal, a login shell, in every worktree
it creates without --agent, and returns no handle for it. When this launch
created the worktree and Orca reports no setup terminal or default tabs, the
script closes every terminal Orca lists there with no agent, before creating
its own. Release v3 kept 279 of these idle shells, one per lane.

A codex model launches on Orca's codex agent instead: worker-start creates the
terminal with --agent codex --model --effort, and Orca's codex default args
already bypass approvals, so there is no custom command or bypass-permissions screen check.
Its service tier comes from Orca's codex runtime config, since worker-start has
no tier flag.

sol is the incident lane: gpt-6.1-sol in a top-level worktree, launched in a
terminal running codex with -c service_tier=fast on its command line, so the
fast tier never depends on Orca's runtime config. It also passes
-c check_for_update_on_startup=false to disable the startup update prompt,
-c mcp_servers=<ORCA_LAUNCH_CODEX_MCP, default {}>, and then
-c mcp_servers.<name>.enabled=false for every server config.toml names that
ORCA_LAUNCH_CODEX_MCP does not. An -c table merges into config.toml rather than
replacing it, so -c mcp_servers={} alone left node_repl, computer-use, and the
Slack MCP running beside every sol lane. Servers a codex plugin bundles still
start. A codex lane on Orca's agent keeps Orca's own command line.
The command prepends the plugin bin to the terminal's own PATH, never the
caller's expanded PATH. When Orca times out at agent_readiness on a codex or sol
worker whose terminal is up, the script reads the screen first. If it contains
"Update available!" or "Skip until next version", the launch fails with a
single-line screen quote, whitespace squeezed and cut to 300 characters, without
typing into the prompt. Otherwise, it types the spec pointer into that terminal
itself and prints the lane as unsupervised: it runs, but Orca carries no worker_done for it.

<model> is opus, sonnet, fable, a claude-* model id, codex (gpt-6-astra), sol
(gpt-6.1-sol), or a gpt-* model id. <effort> is low, medium,
high, xhigh, or max. Terminal creation retries after ORCA_LAUNCH_RETRY_SECONDS,
because the runtime drops connections under load. A worktree create that fails
may still have created the worktree, so the script polls orca worktree show for
up to ORCA_LAUNCH_WORKTREE_SECONDS and creates again only when none registers.

A launch that fails before worker-start rolls back what it made: it closes the
tab of the terminal it opened and removes, with orca worktree rm --force, a
worktree this run created, and its failure line ends "; rolled back terminal=...
worktree=..." or "; rollback left ..." for whatever Orca refused. A worktree that
existed before the launch stays, and nothing is rolled back once worker-start
has run, because a dispatch may then own the terminal.

  ORCA_LAUNCH_RUN            orchestration Run id, required
  ORCA_LAUNCH_REPO           Orca repo id, required
  ORCA_LAUNCH_PARENT         coordinator worktree path, default $PWD
  ORCA_LAUNCH_NO_PARENT      1 creates a top-level worktree, as sol always does, default unset
  ORCA_LAUNCH_PREFIX         worktree name prefix, default none
  ORCA_LAUNCH_ROOT           directory Orca creates worktrees in, default the parent's directory
  ORCA_LAUNCH_BASE           base branch, default the parent checkout's origin/HEAD
  ORCA_LAUNCH_STATE          receipt directory, default ~/.claude/scratch/orca-launch/<run>
  ORCA_LAUNCH_CLAUDE_ARGS    further claude args from Orca's agent default args, default none
  ORCA_LAUNCH_MCP_CONFIG     space-separated --mcp-config files or JSON strings for a claude worker, default none
  ORCA_LAUNCH_CODEX_MCP      inline TOML table, without spaces or single quotes, for a sol worker's mcp_servers, default {}
  ORCA_LAUNCH_RETRY_SECONDS  wait before a retry, default 30
  ORCA_LAUNCH_BOOT_SECONDS   ceiling on the wait for Orca to detect the terminal's agent, default 180
  ORCA_LAUNCH_WORKTREE_SECONDS  ceiling on the wait for a worktree whose create failed to register, default 180
EOF
  exit 2
}

[ $# -eq 4 ] || usage
LANE=$1 MODEL=$2 EFFORT=$3 BRIEF=$4
RUN=${ORCA_LAUNCH_RUN:?ORCA_LAUNCH_RUN is required}
REPO=${ORCA_LAUNCH_REPO:?ORCA_LAUNCH_REPO is required}
PARENT=${ORCA_LAUNCH_PARENT:-$PWD}
NAME=${ORCA_LAUNCH_PREFIX:-}$LANE
WORKTREE_NAME=$NAME-base
ROOT=${ORCA_LAUNCH_ROOT:-$(dirname "$PARENT")}
STATE=${ORCA_LAUNCH_STATE:-$HOME/.claude/scratch/orca-launch/$RUN}
RETRY=${ORCA_LAUNCH_RETRY_SECONDS:-30}
BOOT=${ORCA_LAUNCH_BOOT_SECONDS:-180}
WORKTREE_WAIT=${ORCA_LAUNCH_WORKTREE_SECONDS:-180}
POLL=4
RECEIPT=$STATE/$LANE.json
WT=$(cat "$STATE/$LANE.worktree" 2>/dev/null || echo "$ROOT/$WORKTREE_NAME")

ROLLBACK='' CREATED='' TERMINAL='' UNDONE='' KEPT=''
rollback() {
  [ -n "$ROLLBACK" ] || return 0
  ROLLBACK=''
  if [ -n "$TERMINAL" ]; then
    if orca terminal close --terminal "$TERMINAL" --tab --json >/dev/null 2>&1; then
      UNDONE="$UNDONE terminal=$TERMINAL"
    else
      KEPT="$KEPT terminal=$TERMINAL"
    fi
  fi
  [ -n "$CREATED" ] || return 0
  if orca worktree rm --worktree "path:$WT" --force --json >/dev/null 2>&1; then
    rm -f "$STATE/$LANE.worktree"
    UNDONE="$UNDONE worktree=$WT"
  else
    KEPT="$KEPT worktree=$WT"
  fi
}

fail() {
  rollback
  printf '%s\n' "$LANE failed $*${UNDONE:+; rolled back$UNDONE}${KEPT:+; rollback left$KEPT}"
  exit 1
}

AGENT=claude
case $MODEL in
  codex) AGENT=codex MODEL_ID=gpt-6-astra ;;
  sol) AGENT=sol MODEL_ID=gpt-6.1-sol ;;
  gpt-*) AGENT=codex MODEL_ID=$MODEL ;;
  opus) MODEL_ID=claude-opus-5-5 ;;
  sonnet) MODEL_ID=claude-sonnet-5-5 ;;
  fable) MODEL_ID=claude-fable-5-1 ;;
  claude-*) MODEL_ID=$MODEL ;;
  *) usage ;;
esac
case $EFFORT in
  low | medium | high | xhigh | max) ;;
  *) usage ;;
esac
[ -r "$BRIEF" ] || fail "brief $BRIEF unreadable"
BRIEF=$(cd "$(dirname "$BRIEF")" && pwd)/$(basename "$BRIEF")

BASE=${ORCA_LAUNCH_BASE:-$(git -C "$PARENT" symbolic-ref --short refs/remotes/origin/HEAD)}
DRIVE=$(python3 "$(dirname "$0")/drive.py" current) || DRIVE=
COMMAND="env CLAUDE_LONG_RUNNING_LANE=$LANE${DRIVE:+ CLAUDE_LONG_RUNNING_DRIVE=$DRIVE} claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode --strict-mcp-config${ORCA_LAUNCH_MCP_CONFIG:+ --mcp-config $ORCA_LAUNCH_MCP_CONFIG}${ORCA_LAUNCH_CLAUDE_ARGS:+ $ORCA_LAUNCH_CLAUDE_ARGS} --model $MODEL_ID --effort $EFFORT"
BIN=$(cd "$(dirname "$0")/../../../bin" && pwd)
CODEX_MCP=${ORCA_LAUNCH_CODEX_MCP:-'{}'}
CODEX_OFF=
[ "$AGENT" != sol ] || CODEX_OFF=$(python3 - "${CODEX_HOME:-$HOME/.codex}/config.toml" "$CODEX_MCP" <<'PY'
import pathlib, sys, tomllib
config = pathlib.Path(sys.argv[1])
servers = tomllib.loads(config.read_text()).get("mcp_servers", {}) if config.exists() else {}
named = tomllib.loads(f"named = {sys.argv[2]}")["named"]
print("".join(f" -c mcp_servers.{name}.enabled=false" for name in servers if name not in named))
PY
) || fail "codex config: cannot read the mcp_servers of ${CODEX_HOME:-$HOME/.codex}/config.toml"
[ "$AGENT" != sol ] || COMMAND="sh -c 'PATH=$BIN:\$PATH exec codex --dangerously-bypass-approvals-and-sandbox -c model=$MODEL_ID -c service_tier=fast -c model_reasoning_effort=$EFFORT -c check_for_update_on_startup=false -c mcp_servers=$CODEX_MCP$CODEX_OFF'"
pointer() {
  printf '%s' "Lane $LANE: read $1 in full first and execute it exactly; Orca truncates specs. Worktree $WT, bypass-permissions mode; the brief's Escalate rules hold."
}
spec() {
  SPEC=$(pointer "$BRIEF")
  [ "${#SPEC}" -gt 300 ] || return 0
  LINK=$HOME/.claude/$(printf '%s' "$BRIEF" | shasum | cut -c1-8)
  mkdir -p "$HOME/.claude"
  ln -sfn "$BRIEF" "$LINK"
  SPEC=$(pointer "$LINK")
  [ "${#SPEC}" -le 300 ] || fail "spec pointer is ${#SPEC} characters, over 300; shorten the worktree path"
}
spec

mkdir -p "$STATE"

set -- --parent-worktree "path:$PARENT"
[ "$AGENT" != sol ] && [ "${ORCA_LAUNCH_NO_PARENT:-}" != 1 ] || set -- --no-parent

registered() {
  FOUND=$(orca worktree show --worktree "path:$WT" --json | jq -er '.result.worktree.path') || return 1
}

attempt=0 CREATED=''
while ! registered; do
  attempt=$((attempt + 1)) CREATED=1
  [ "$attempt" -le 4 ] || fail "worktree create: $(head -c 300 "$STATE/$LANE.worktree.json")"
  if orca worktree create --name "$WORKTREE_NAME" --repo "id:$REPO" --base-branch "$BASE" \
    "$@" --setup run --json >"$STATE/$LANE.worktree.json" 2>&1 &&
    FOUND=$(jq -er '.result.worktree.path' "$STATE/$LANE.worktree.json"); then
    break
  fi
  waited=0
  until registered || [ "$waited" -ge "$WORKTREE_WAIT" ]; do
    sleep "$POLL"
    waited=$((waited + POLL))
  done
done
WT=$FOUND
printf '%s\n' "$WT" >"$STATE/$LANE.worktree"
ROLLBACK=1
spec

listed() {
  listing=0
  until orca terminal list --worktree "path:$WT" --json >"$STATE/$LANE.terminals.json" 2>&1 &&
    LISTED=$(jq -ce '[.result.terminals[].handle]' "$STATE/$LANE.terminals.json"); do
    listing=$((listing + 1))
    [ "$listing" -lt 3 ] || fail "terminal list: $(head -c 300 "$STATE/$LANE.terminals.json")"
    sleep "$RETRY"
  done
}

close_startup_shells() {
  jq -e '.result | .worktree and (.setup or .defaultTabs or .setupReceipt.terminalHandle | not)' "$STATE/$LANE.worktree.json" >/dev/null 2>&1 || return 0
  for shell in $(jq -r '.result.terminals[] | select((.agentIdentity // "") == "") | .handle' "$STATE/$LANE.terminals.json"); do
    orca terminal close --terminal "$shell" --tab --json >>"$STATE/$LANE.startup.json" 2>&1 || :
  done
}

attempt=0 TERMINAL=''
[ "$AGENT" = codex ] && [ -z "$CREATED" ] || { listed && BEFORE=$LISTED; }
[ -z "$CREATED" ] || close_startup_shells
until [ "$AGENT" = codex ] || [ -n "$TERMINAL" ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -le 3 ] || fail "terminal create: $(cat "$STATE/$LANE.terminal.json" "$STATE/$LANE.terminal.err" | head -c 300)"
  orca terminal create --worktree "path:$WT" --title "$NAME" --command "$COMMAND" --json \
    >"$STATE/$LANE.terminal.json" 2>"$STATE/$LANE.terminal.err" || :
  TERMINAL=$(jq -r '.result.terminal.handle // empty' "$STATE/$LANE.terminal.json" 2>/dev/null) || TERMINAL=
  [ -z "$TERMINAL" ] || break
  sleep "$RETRY"
  listed
  TERMINAL=$(jq -nr --argjson before "$BEFORE" --argjson after "$LISTED" 'first($after[] | select(IN($before[]) | not)) // empty')
done
IDENTITY=claude MARKER=CLAUDE_LONG_RUNNING_LANE=$LANE
[ "$AGENT" = claude ] || IDENTITY=codex MARKER=$MODEL_ID
screen_lacks_agent() {
  SCREEN=$(orca terminal read --terminal "$TERMINAL" --screen --json |
    jq -er '.result.terminal | select(.source != "screen-unavailable") | .tail | join("")') || return 1
  case $SCREEN in
    *"$MARKER"* | *"bypass permissions on"*) return 1 ;;
  esac
}
attempt=0 DETECTED='' TYPED=''
until [ "$AGENT" = codex ] || [ "$DETECTED" = "$IDENTITY" ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -le $(((BOOT + POLL - 1) / POLL)) ] ||
    fail "boot terminal=$TERMINAL: orca terminal list shows agentIdentity=${DETECTED:-none}, not $IDENTITY, after ${BOOT}s${TYPED:+; the command was typed into the terminal once}"
  sleep "$POLL"
  DETECTED=$(orca terminal list --worktree "path:$WT" --json | jq -r --arg t "$TERMINAL" '.result.terminals[] | select(.handle == $t) | .agentIdentity // empty') || DETECTED=
  if [ "$DETECTED" != "$IDENTITY" ] && [ -z "$TYPED" ] && [ $((attempt * POLL * 3)) -ge "$BOOT" ] && screen_lacks_agent; then
    orca terminal send --terminal "$TERMINAL" --text "$COMMAND" --enter --json >/dev/null ||
      fail "command send terminal=$TERMINAL after Orca dropped the startup command"
    TYPED=1
  fi
done

if [ -s "$RECEIPT" ]; then
  set -- --task "$(jq -r '.result.taskId' "$RECEIPT")" --retry-of "$(jq -r '.result.dispatchId' "$RECEIPT")"
else
  set -- --spec "$SPEC" --task-title "$NAME"
fi
if [ "$AGENT" = codex ]; then
  set -- "$@" --agent codex --model "$MODEL_ID" --effort "$EFFORT"
else
  set -- "$@" --terminal "$TERMINAL"
fi
TIMEOUT=600000
[ "$AGENT" = claude ] || TIMEOUT=90000
STARTED=0
ROLLBACK=''
orca orchestration worker-start --run "$RUN" "$@" --worktree "path:$WT" \
  --timeout-ms "$TIMEOUT" --json >"$RECEIPT.new" 2>"$STATE/$LANE.worker.err" || STARTED=$?
if jq -e '.result.taskId and .result.dispatchId' "$RECEIPT.new" >/dev/null 2>&1; then
  mv "$RECEIPT.new" "$RECEIPT"
  [ "$AGENT" != codex ] || TERMINAL=$(jq -r 'first(.result.effects[] | select(.kind == "terminal" and .role == "agent") | .id) // empty' "$RECEIPT")
  printf '%s\n' "$TERMINAL" >"$STATE/$LANE.terminal"
fi
if [ "$AGENT" != claude ] && [ -n "$TERMINAL" ] &&
  [ "$(jq -r '.result.failedStage // empty' "$RECEIPT")" = agent_readiness ]; then
  SCREEN=$(orca terminal read --terminal "$TERMINAL" --screen --json | jq -r '.result.terminal.tail | tostring')
  case $SCREEN in
    *"Update available!"* | *"Skip until next version"*)
      fail "agent_readiness terminal=$TERMINAL update prompt: '$(printf '%s' "$SCREEN" | tr -s '[:space:]' ' ' | cut -c1-300)'"
      ;;
  esac
  orca terminal send --terminal "$TERMINAL" --text "$SPEC" --enter --json >/dev/null ||
    fail "spec send terminal=$TERMINAL after agent_readiness timeout"
  echo "$LANE unsupervised task=$(jq -r '.result.taskId' "$RECEIPT") dispatch=$(jq -r '.result.dispatchId' "$RECEIPT") terminal=$TERMINAL worktree=$WT"
  exit 0
fi
[ "$STARTED" = 0 ] || fail "worker-start terminal=$TERMINAL: $(head -c 300 "$STATE/$LANE.worker.err")"
READY=$(jq -r '.result.state' "$RECEIPT")
[ "$READY" = ready ] || fail "worker-start state=$READY terminal=$TERMINAL"

attempt=0
until [ "$AGENT" != claude ] || orca terminal read --terminal "$TERMINAL" --screen --json |
  jq -e '.result.terminal.tail | tostring | contains("bypass permissions on")' >/dev/null; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 10 ] || fail "terminal=$TERMINAL does not show bypass permissions on; shift-tab it before the worker opens a plan"
  sleep "$POLL"
done

echo "$LANE ready task=$(jq -r '.result.taskId' "$RECEIPT") dispatch=$(jq -r '.result.dispatchId' "$RECEIPT") terminal=$TERMINAL worktree=$WT"
