#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: orca-launch.sh <lane> <model> <effort> <brief-file>

Launches one Orca worker for <lane> and prints one line:

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
pointer to <brief-file>, because Orca truncates a pasted spec near 3 KB. It names
the brief as the authority over Orca's preamble, which forbids GitHub posts, and
over any standing leave-uncommitted default. A brief whose ccx: header line
says role=desk or role=watch is a standing desk, and its pointer adds the desk
contract: loop until rotation, and send worker_done only at rotation, naming the
handoff doc, since Orca's preamble otherwise reads setup or one quiet cycle as
the finished task. The pointer must stay within 500
characters, so a brief path that pushes it over is replaced by a symlink
~/.claude/<8 hex of the path's sha> to the brief. Before
worker-start, which refuses a terminal with agent_unconfigured until Orca sees the
agent's own UI (an agent title or ready prompt), the script blocks on orca terminal
wait --for tui-idle, the readiness worker-start itself waits for next, up to
ORCA_LAUNCH_BOOT_SECONDS. terminal list's agentIdentity is no substitute: it reads
claude from the agent's process or first hook, before its UI renders.
Orca drops a terminal's startup command under load, leaving a shell prompt, so
once a third of that ceiling has passed with no idle agent, the script reads
the screen and, when it shows neither the command line nor the agent's own UI,
types the command into the terminal once. A launch that still has no idle agent at
the ceiling, or whose agent is blocked on a startup prompt, fails, and never
reaches worker-start. Orca can still refuse an idle agent with agent_unconfigured
for a few seconds after tui-idle, so that refusal, which dispatches nothing, is
retried every 4 seconds for up to the same ceiling.
Every list is scoped to the lane's worktree, since an unscoped list stops at 200
terminals. A terminal create whose output names no handle is followed by a list
of the worktree every 4 seconds, for up to ORCA_LAUNCH_BOOT_SECONDS, and the first
terminal that was not there before the create is adopted; the script creates again
only when no list in that window shows one. Under load a create that timed out can
open its terminal well over a minute later, and creating again then starts a
second agent in the same worktree. The
launch counts only once the receipt reads ready and the terminal's screen shows bypass permissions on.
worker-start exits 1 with state outcome_unknown when it wrote the prompt but saw no
turn start within its 30-second observation; the dispatch still exists and Orca
still supervises it. The script then polls orca orchestration worker-show every 4
seconds, up to ORCA_LAUNCH_BOOT_SECONDS, and counts the launch as ready once the
dispatch's projected activity reads working or a worker report settles it as ready
or succeeded. Otherwise it fails naming the dispatch and the worker-abandon command
a relaunch needs first.

orca worktree create opens a first terminal, a login shell, in every worktree
it creates without --agent, and returns no handle for it. When this launch
created the worktree and Orca reports no setup terminal or default tabs, the
script closes every terminal Orca lists there with no agent, before creating
its own. Release v3 kept 279 of these idle shells, one per lane.

A codex model launches on Orca's codex agent instead: worker-start creates the
terminal with --agent codex --model --effort, and Orca's codex default args
already bypass approvals, so there is no custom command or bypass-permissions screen check.
Its service tier comes from Orca's codex runtime config, the standard tier,
since worker-start has no tier flag.

incident is the incident lane: gpt-6.1-sol in a top-level worktree, launched in a
terminal running codex with -c service_tier=fast on its command line, so the
fast tier never depends on Orca's runtime config. No other alias runs the fast
tier. It also passes
-c check_for_update_on_startup=false to disable the startup update prompt,
-c mcp_servers=<ORCA_LAUNCH_CODEX_MCP, default {}>, and then
-c mcp_servers.<name>.enabled=false for every server config.toml names other than
datadog, sentry, and the servers ORCA_LAUNCH_CODEX_MCP names, so every incident
lane can query Datadog and Sentry. An -c table merges into config.toml rather than
replacing it, so -c mcp_servers={} alone left node_repl, computer-use, and the
Slack MCP running beside every incident lane. Servers a codex plugin bundles still
start. A codex lane on Orca's agent keeps Orca's own command line.
The command prepends the plugin bin to the terminal's own PATH, never the
caller's expanded PATH. When Orca times out at agent_readiness on a codex or incident
worker whose terminal is up, the script reads the screen first. If it contains
"Update available!" or "Skip until next version", the launch fails with a
single-line screen quote, whitespace squeezed and cut to 300 characters, without
typing into the prompt. Otherwise, it types the spec pointer into that terminal
itself and prints the lane as unsupervised: it runs, but Orca carries no worker_done for it.

<model> is opus, sonnet, fable, a claude-* model id, sol or codex (gpt-6.1-sol on
the standard tier), incident (gpt-6.1-sol on the fast tier), astra (gpt-6-astra,
for exceptional cases only), or a gpt-* model id. <effort> is low, medium,
high, xhigh, or max. Orca fails a terminal create with "Terminal creation timed
out" when its renderer has not answered within a fixed 10 seconds, and the create
may still open the terminal. A worktree create that fails
may still have created the worktree, so the script polls orca worktree show for
up to ORCA_LAUNCH_WORKTREE_SECONDS and creates again only when none registers.
Orca registers a worktree at git worktree add --no-checkout and fills its index
with a reset --hard that can run past a minute under load, so a worktree found
that way waits, for the same ceiling, until its index matches HEAD; one that
never gets there is never adopted. A worktree Orca already lists before the
launch must be a git checkout of its own, or the launch fails at once: Orca can
serve a stale record for a worktree whose git add timed out and was undone.

A receipt from an earlier launch names the lane's last task and dispatch. When
orca orchestration task-list shows that task failed or blocked, worker-start
retries it with --task and --retry-of; any other task, such as one whose
dispatch completed, gets a fresh task from --spec, so a lane name can launch
again after its earlier worker finished.

Only the terminal bound to the Run may call worker-start, and Orca reads the
caller from ORCA_TERMINAL_HANDLE. Before it creates anything, the script asks
orca orchestration run-current which Run this terminal coordinates, and fails
with the rebind command when it is not ORCA_LAUNCH_RUN.

A launch that fails before worker-start, or that worker-start refuses before it
dispatches anything (consumer_fenced, invalid_argument, task_not_found,
worker_prompt_too_large, runtime_unavailable, agent_unconfigured), rolls back what it made: it
deletes the lane's <lane>.worktree receipt, closes the tab of the terminal whose
handle its own terminal create returned, and removes the worktree its own
worktree create made, whether or not that create answered: a registered one with
orca worktree rm --force, and a directory left at the path with no checkout,
which a timed-out git add leaves behind, with rm -rf. Its failure line ends
"; rolled back terminal=... worktree=..." and "; rollback left ..." names what it
kept: a terminal it adopted from a listing, which a concurrent launch of the same
lane may own, whatever Orca refused, and, after any other worker-start failure,
the terminal and worktree a dispatch may own. A worktree that existed before the
launch stays. Every failure line is one line, and an Orca error in it reads
"<code>: <message>".

  ORCA_LAUNCH_RUN            orchestration Run id, default the Orca run of the drive this session belongs to, from drive.py orca-run
  ORCA_LAUNCH_REPO           Orca repo id, default the repo orca repo list names at the parent's main checkout, the directory holding its git common dir
  ORCA_LAUNCH_PARENT         coordinator worktree path, default $PWD
  ORCA_LAUNCH_NO_PARENT      1 creates a top-level worktree, as incident always does, default unset
  ORCA_LAUNCH_PREFIX         worktree name prefix, default none
  ORCA_LAUNCH_ROOT           directory Orca creates worktrees in, default the parent's directory
  ORCA_LAUNCH_BASE           base branch, default the parent checkout's origin/HEAD
  ORCA_LAUNCH_STATE          receipt directory, default ~/.claude/scratch/orca-launch/<run>
  ORCA_LAUNCH_CLAUDE_ARGS    further claude args from Orca's agent default args, default none
  ORCA_LAUNCH_MCP_CONFIG     space-separated --mcp-config files or JSON strings for a claude worker, default none
  ORCA_LAUNCH_CODEX_MCP      inline TOML table, without spaces or single quotes, for an incident worker's mcp_servers beside datadog and sentry, default {}
  ORCA_LAUNCH_BOOT_SECONDS   ceiling on the poll for a terminal whose create named no handle, on the wait for the terminal's agent to reach its idle prompt, and on the wait for an outcome_unknown worker's first turn, default 180
  ORCA_LAUNCH_WORKTREE_SECONDS  ceiling on the wait for a worktree whose create failed to register, and then for its checkout, default 180
EOF
  exit 2
}

[ $# -eq 4 ] || usage
LANE=$1 MODEL=$2 EFFORT=$3 BRIEF=$4
PARENT=${ORCA_LAUNCH_PARENT:-$PWD}
NAME=${ORCA_LAUNCH_PREFIX:-}$LANE
WORKTREE_NAME=$NAME-base
ROOT=${ORCA_LAUNCH_ROOT:-$(dirname "$PARENT")}
BOOT=${ORCA_LAUNCH_BOOT_SECONDS:-180}
WORKTREE_WAIT=${ORCA_LAUNCH_WORKTREE_SECONDS:-180}
POLL=4

ROLLBACK='' CREATED='' MADE='' LOST='' OWNED='' TERMINAL='' UNDONE='' KEPT=''
rollback() {
  [ -n "$ROLLBACK" ] || return 0
  ROLLBACK=''
  if [ -n "$TERMINAL" ] && [ "$TERMINAL" = "$OWNED" ] &&
    orca terminal close --terminal "$TERMINAL" --tab --json >/dev/null 2>&1; then
    UNDONE="$UNDONE terminal=$TERMINAL"
  elif [ -n "$TERMINAL" ]; then
    KEPT="$KEPT terminal=$TERMINAL"
  fi
  if [ -n "$MADE" ] || { [ -n "$LOST" ] && registered; }; then
    if orca worktree rm --worktree "path:$WT" --force --json >/dev/null 2>&1; then
      rm -f "$STATE/$LANE.worktree"
      UNDONE="$UNDONE worktree=$WT"
    else
      KEPT="$KEPT worktree=$WT"
    fi
  elif [ -n "$LOST" ] && [ -d "$WT" ] && ! is_checkout; then
    rm -rf "$WT" && UNDONE="$UNDONE worktree=$WT" || KEPT="$KEPT worktree=$WT"
  fi
  is_checkout || rm -f "$STATE/$LANE.worktree"
}

fail() {
  rollback
  printf '%s\n' "$LANE failed $(printf '%s' "$*" | tr -s '[:space:]' ' ')${UNDONE:+; rolled back$UNDONE}${KEPT:+; rollback left$KEPT}"
  exit 1
}

orca_error() {
  jq -er 'select(.ok == false) | "\(.error.code): \(.error.message // "no message")"' "$1" 2>/dev/null ||
    cat "$@" | tr -s '[:space:]' ' ' | cut -c1-300
}

RUN=${ORCA_LAUNCH_RUN:-$(python3 "$(dirname "$0")/drive.py" orca-run)} ||
  fail "run: this session's drive binds no Orca run; set ORCA_LAUNCH_RUN or run drive.py start --orca-run <id>"
REPO=${ORCA_LAUNCH_REPO:-}
if [ -z "$REPO" ]; then
  MAIN=$(git -C "$PARENT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) ||
    fail "repo: $PARENT is not a git checkout; set ORCA_LAUNCH_REPO"
  MAIN=$(dirname "$MAIN")
  REPO=$(orca repo list --json | jq -er --arg path "$MAIN" 'first(.result.repos[] | select(.path == $path) | .id)') ||
    fail "repo: orca repo list names no repo at $MAIN; set ORCA_LAUNCH_REPO or run orca repo add --path $MAIN"
fi
STATE=${ORCA_LAUNCH_STATE:-$HOME/.claude/scratch/orca-launch/$RUN}
RECEIPT=$STATE/$LANE.json
WT=$(cat "$STATE/$LANE.worktree" 2>/dev/null || echo "$ROOT/$WORKTREE_NAME")

AGENT=claude
case $MODEL in
  codex | sol) AGENT=codex MODEL_ID=gpt-6.1-sol ;;
  astra) AGENT=codex MODEL_ID=gpt-6-astra ;;
  incident) AGENT=incident MODEL_ID=gpt-6.1-sol ;;
  gpt-*) AGENT=codex MODEL_ID=$MODEL ;;
  opus) MODEL_ID=claude-opus-5-5 ;;
  sonnet) MODEL_ID=claude-sonnet-5-5 ;;
  fable) MODEL_ID=claude-fable-5-1 ;;
  claude-*) MODEL_ID=$MODEL ;;
  *) fail "model $MODEL unknown: use opus, sonnet, fable, claude-*, sol, codex, incident, astra, or gpt-*" ;;
esac
case $EFFORT in
  low | medium | high | xhigh | max) ;;
  *) fail "effort $EFFORT unknown: use low, medium, high, xhigh, or max" ;;
esac
[ -r "$BRIEF" ] || fail "brief $BRIEF unreadable"
BRIEF=$(cd "$(dirname "$BRIEF")" && pwd)/$(basename "$BRIEF")

BASE=${ORCA_LAUNCH_BASE:-$(git -C "$PARENT" symbolic-ref --short refs/remotes/origin/HEAD)}
DRIVE=$(python3 "$(dirname "$0")/drive.py" current) || DRIVE=
COMMAND="env CLAUDE_LONG_RUNNING_LANE=$LANE${DRIVE:+ CLAUDE_LONG_RUNNING_DRIVE=$DRIVE} claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode --strict-mcp-config${ORCA_LAUNCH_MCP_CONFIG:+ --mcp-config $ORCA_LAUNCH_MCP_CONFIG}${ORCA_LAUNCH_CLAUDE_ARGS:+ $ORCA_LAUNCH_CLAUDE_ARGS} --model $MODEL_ID --effort $EFFORT"
BIN=$(cd "$(dirname "$0")/../../../bin" && pwd)
CODEX_MCP=${ORCA_LAUNCH_CODEX_MCP:-'{}'}
CODEX_OFF=
[ "$AGENT" != incident ] || CODEX_OFF=$(python3 - "${CODEX_HOME:-$HOME/.codex}/config.toml" "$CODEX_MCP" <<'PY'
import pathlib, sys, tomllib
config = pathlib.Path(sys.argv[1])
servers = tomllib.loads(config.read_text()).get("mcp_servers", {}) if config.exists() else {}
named = {"datadog", "sentry", *tomllib.loads(f"named = {sys.argv[2]}")["named"]}
print("".join(f" -c mcp_servers.{name}.enabled=false" for name in servers if name not in named))
PY
) || fail "codex config: cannot read the mcp_servers of ${CODEX_HOME:-$HOME/.codex}/config.toml"
[ "$AGENT" != incident ] || COMMAND="sh -c 'PATH=$BIN:\$PATH exec codex --dangerously-bypass-approvals-and-sandbox -c model=$MODEL_ID -c service_tier=fast -c model_reasoning_effort=$EFFORT -c check_for_update_on_startup=false -c mcp_servers=$CODEX_MCP$CODEX_OFF'"
DESK=
! grep -qE '^ccx:(.* )?role=(desk|watch)([[:space:]]|$)' "$BRIEF" ||
  DESK=" Standing desk: loop until rotation; setup and quiet cycles are not done. Send worker_done only at rotation, naming the handoff doc."
pointer() {
  printf '%s' "Lane $LANE: read $1 in full first and execute it exactly; it outranks Orca's preamble and any leave-uncommitted default, so commit, push, open PRs and post as it says. Worktree $WT, bypass-permissions mode; its Escalate rules hold.$DESK"
}
spec() {
  SPEC=$(pointer "$BRIEF")
  [ "${#SPEC}" -gt 500 ] || return 0
  LINK=$HOME/.claude/$(printf '%s' "$BRIEF" | shasum | cut -c1-8)
  mkdir -p "$HOME/.claude"
  ln -sfn "$BRIEF" "$LINK"
  SPEC=$(pointer "$LINK")
  [ "${#SPEC}" -le 500 ] || fail "spec pointer is ${#SPEC} characters, over 500; shorten the worktree path"
}
spec

mkdir -p "$STATE"

orca orchestration run-current --json >"$STATE/$LANE.binding.json" 2>&1 || :
BOUND=$(jq -r '.result.run.id // empty' "$STATE/$LANE.binding.json" 2>/dev/null) || BOUND=
[ "$BOUND" = "$RUN" ] ||
  fail "coordinator binding: terminal ${ORCA_TERMINAL_HANDLE:-unset} coordinates ${BOUND:-no Run}, not $RUN ($(orca_error "$STATE/$LANE.binding.json")); from the coordinator's Orca terminal run: orca orchestration run-use --id $RUN"

set -- --parent-worktree "path:$PARENT"
[ "$AGENT" != incident ] && [ "${ORCA_LAUNCH_NO_PARENT:-}" != 1 ] || set -- --no-parent

registered() {
  FOUND=$(orca worktree show --worktree "path:$WT" --json | jq -er '.result.worktree.path') || return 1
}
is_checkout() {
  [ "$(git -C "$WT" rev-parse --show-toplevel 2>/dev/null)" = "$(cd "$WT" 2>/dev/null && pwd -P)" ]
}
checked_out() {
  is_checkout && git -C "$WT" diff-index --cached --quiet HEAD --
}

ROLLBACK=1
attempt=0
while ! registered; do
  attempt=$((attempt + 1)) CREATED=1
  [ "$attempt" -le 4 ] || fail "worktree create: $(orca_error "$STATE/$LANE.worktree.json")"
  if orca worktree create --name "$WORKTREE_NAME" --repo "id:$REPO" --base-branch "$BASE" \
    "$@" --setup run --json >"$STATE/$LANE.worktree.json" 2>&1 &&
    FOUND=$(jq -er '.result.worktree.path' "$STATE/$LANE.worktree.json"); then
    MADE=1
    break
  fi
  jq -e '.error.code == "worktree_exists"' "$STATE/$LANE.worktree.json" >/dev/null 2>&1 || LOST=1
  waited=0
  until registered || [ "$waited" -ge "$WORKTREE_WAIT" ]; do
    sleep "$POLL"
    waited=$((waited + POLL))
  done
done
WT=$FOUND
[ -n "$CREATED" ] || is_checkout ||
  fail "worktree $WT: Orca lists it, but it is not a git checkout; remove the directory before relaunching"
waited=0
until [ -z "$CREATED" ] || [ -n "$MADE" ] || checked_out; do
  [ "$waited" -lt "$WORKTREE_WAIT" ] ||
    fail "worktree checkout: $WT registered, but its index still differs from HEAD after ${WORKTREE_WAIT}s"
  sleep "$POLL"
  waited=$((waited + POLL))
done
printf '%s\n' "$WT" >"$STATE/$LANE.worktree"
spec

listed() {
  listing=0
  until orca terminal list --worktree "path:$WT" --json >"$STATE/$LANE.terminals.json" 2>&1 &&
    LISTED=$(jq -ce '[.result.terminals[].handle]' "$STATE/$LANE.terminals.json"); do
    listing=$((listing + 1))
    [ "$listing" -lt 3 ] || fail "terminal list: $(orca_error "$STATE/$LANE.terminals.json")"
    sleep "$POLL"
  done
}

adopt() {
  waited=0
  while [ "$waited" -lt "$BOOT" ]; do
    sleep "$POLL"
    waited=$((waited + POLL))
    listed
    TERMINAL=$(jq -nr --argjson before "$BEFORE" --argjson after "$LISTED" 'first($after[] | select(IN($before[]) | not)) // empty')
    [ -z "$TERMINAL" ] || return 0
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
  [ "$attempt" -le 3 ] || fail "terminal create: $(orca_error "$STATE/$LANE.terminal.json" "$STATE/$LANE.terminal.err")"
  orca terminal create --worktree "path:$WT" --title "$NAME" --command "$COMMAND" --json \
    >"$STATE/$LANE.terminal.json" 2>"$STATE/$LANE.terminal.err" || :
  TERMINAL=$(jq -r '.result.terminal.handle // empty' "$STATE/$LANE.terminal.json" 2>/dev/null) || TERMINAL=
  OWNED=$TERMINAL
  [ -z "$TERMINAL" ] || break
  adopt
done
MARKER=CLAUDE_LONG_RUNNING_LANE=$LANE
[ "$AGENT" = claude ] || MARKER=$MODEL_ID
screen_lacks_agent() {
  SCREEN=$(orca terminal read --terminal "$TERMINAL" --screen --json |
    jq -er '.result.terminal | select(.source != "screen-unavailable") | .tail | join("")') || return 1
  case $SCREEN in
    *"$MARKER"* | *"bypass permissions on"*) return 1 ;;
  esac
}
booted() {
  orca terminal wait --terminal "$TERMINAL" --for tui-idle --timeout-ms "$1" --json >"$STATE/$LANE.boot.json" 2>&1 &&
    jq -e '.result.wait | .satisfied and .status == "running"' "$STATE/$LANE.boot.json" >/dev/null
}
TYPED=''
if [ "$AGENT" != codex ] && ! booted $((BOOT * 1000 / 3)); then
  if screen_lacks_agent; then
    orca terminal send --terminal "$TERMINAL" --text "$COMMAND" --enter --json >/dev/null ||
      fail "command send terminal=$TERMINAL after Orca dropped the startup command"
    TYPED=1
  fi
  booted $((BOOT * 1000 - BOOT * 1000 / 3)) ||
    fail "boot terminal=$TERMINAL: orca terminal wait --for tui-idle reads $(jq -er '.result.wait // empty | .blockedReason // "status=\(.status)"' "$STATE/$LANE.boot.json" 2>/dev/null || orca_error "$STATE/$LANE.boot.json") after ${BOOT}s${TYPED:+; the command was typed into the terminal once}"
fi

retryable() {
  for status in failed blocked; do
    orca orchestration task-list --run "$RUN" --status "$status" --brief --json |
      jq -e --arg task "$1" 'any(.result.tasks[]; .id == $task)' >/dev/null && return 0
  done
  return 1
}
TASK=$(jq -r '.result.taskId // empty' "$RECEIPT" 2>/dev/null) || TASK=
if [ -n "$TASK" ] && retryable "$TASK"; then
  set -- --task "$TASK" --retry-of "$(jq -r '.result.dispatchId' "$RECEIPT")"
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
refusal() {
  jq -rs 'if length == 1 and .[0].ok == false then .[0].error.code else empty end' "$RECEIPT.new" 2>/dev/null || :
}
waited=0
while :; do
  STARTED=0
  orca orchestration worker-start --run "$RUN" "$@" --worktree "path:$WT" \
    --timeout-ms "$TIMEOUT" --json >"$RECEIPT.new" 2>"$STATE/$LANE.worker.err" || STARTED=$?
  [ "$STARTED" != 0 ] && [ "$(refusal)" = agent_unconfigured ] && [ "$waited" -lt "$BOOT" ] || break
  sleep "$POLL"
  waited=$((waited + POLL))
done
ROLLBACK=''
if jq -e '.result.taskId and .result.dispatchId' "$RECEIPT.new" >/dev/null 2>&1; then
  mv "$RECEIPT.new" "$RECEIPT"
  [ "$AGENT" != codex ] || TERMINAL=$(jq -r 'first(.result.effects[] | select(.kind == "terminal" and .role == "agent") | .id) // empty' "$RECEIPT")
  printf '%s\n' "$TERMINAL" >"$STATE/$LANE.terminal"
elif [ "$STARTED" != 0 ]; then
  REFUSED=$(refusal)
  case $REFUSED in
    consumer_fenced | invalid_argument | task_not_found | worker_prompt_too_large | runtime_unavailable | agent_unconfigured) ROLLBACK=1 ;;
    *)
      KEPT=${TERMINAL:+ terminal=$TERMINAL}
      [ -z "$MADE$LOST" ] || KEPT="$KEPT worktree=$WT"
      ;;
  esac
  REBIND=
  [ "$REFUSED" != consumer_fenced ] ||
    REBIND="; terminal ${ORCA_TERMINAL_HANDLE:-unset} is not the Run's coordinator: from the coordinator's Orca terminal run orca orchestration run-use --id $RUN"
  fail "worker-start${TERMINAL:+ terminal=$TERMINAL}: $(orca_error "$RECEIPT.new" "$STATE/$LANE.worker.err")$REBIND"
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
turn_started() {
  waited=0
  until orca orchestration worker-show --dispatch "$1" --json |
    jq -e '.result | .projection.stage.activity == "working" or (.worker.state | IN("ready", "succeeded"))' >/dev/null 2>&1; do
    [ "$waited" -lt "$BOOT" ] || return 1
    sleep "$POLL"
    waited=$((waited + POLL))
  done
}
READY=$(jq -r '.result.state' "$RECEIPT")
case $READY in
  ready) ;;
  outcome_unknown)
    DISPATCH=$(jq -r '.result.dispatchId' "$RECEIPT")
    turn_started "$DISPATCH" ||
      fail "worker-start outcome_unknown dispatch=$DISPATCH terminal=$TERMINAL worktree=$WT: worker-show shows no turn started ${BOOT}s after worker-start; Orca still supervises the dispatch, so run orca orchestration worker-abandon --dispatch $DISPATCH --json before relaunching"
    ;;
  *)
    [ "$STARTED" = 0 ] || fail "worker-start terminal=$TERMINAL worktree=$WT: $(orca_error "$RECEIPT" "$STATE/$LANE.worker.err")"
    fail "worker-start state=$READY terminal=$TERMINAL worktree=$WT"
    ;;
esac

attempt=0
until [ "$AGENT" != claude ] || orca terminal read --terminal "$TERMINAL" --screen --json |
  jq -e '.result.terminal.tail | tostring | contains("bypass permissions on")' >/dev/null; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 10 ] || fail "terminal=$TERMINAL does not show bypass permissions on; shift-tab it before the worker opens a plan"
  sleep "$POLL"
done

echo "$LANE ready task=$(jq -r '.result.taskId' "$RECEIPT") dispatch=$(jq -r '.result.dispatchId' "$RECEIPT") terminal=$TERMINAL worktree=$WT"
