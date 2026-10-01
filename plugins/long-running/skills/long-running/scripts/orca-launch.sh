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
reaches only its own terminal, which no one watches. Claude runs under
CLAUDE_LONG_RUNNING_LANE=<lane> and, when drive.py places this session in a drive,
CLAUDE_LONG_RUNNING_DRIVE=<drive>, so the pack's PR hook records every PR the worker
opens in the drive's ledger under the lane's name. The spec is a
pointer to <brief-file>, because Orca truncates a pasted spec near 3 KB; the
pointer must stay within 300 characters, so a brief path that pushes it over is
replaced by a symlink ~/.claude/<8 hex of the path's sha> to the brief. Before
worker-start, which refuses a terminal with agent_unconfigured until Orca detects
its agent, the script polls orca terminal list every 4 seconds until the terminal's
agentIdentity reads claude, or codex for sol, up to ORCA_LAUNCH_BOOT_SECONDS. The
launch counts only once the receipt reads ready and the terminal's screen shows bypass permissions on.

A codex model launches on Orca's codex agent instead: worker-start creates the
terminal with --agent codex --model --effort, and Orca's codex default args
already bypass approvals, so there is no custom command and no screen check.
Its service tier comes from Orca's codex runtime config, since worker-start has
no tier flag.

sol is the incident lane: gpt-6.1-sol in a top-level worktree, launched in a
terminal running codex with -c service_tier=fast on its command line, so the
fast tier never depends on Orca's runtime config. The command prepends the
plugin bin to the terminal's own PATH, never the caller's expanded PATH. When Orca times out at
agent_readiness on a codex or sol worker whose terminal is up, the script types
the spec pointer into that terminal itself and prints the lane as unsupervised:
it runs, but Orca carries no worker_done for it.

<model> is opus, sonnet, fable, a claude-* model id, codex (gpt-6-astra), sol
(gpt-6.1-sol), or a gpt-* model id. <effort> is low, medium,
high, xhigh, or max. Worktree and terminal creation retry after
ORCA_LAUNCH_RETRY_SECONDS, because the runtime drops connections under load.

  ORCA_LAUNCH_RUN            orchestration Run id, required
  ORCA_LAUNCH_REPO           Orca repo id, required
  ORCA_LAUNCH_PARENT         coordinator worktree path, default $PWD
  ORCA_LAUNCH_NO_PARENT      1 creates a top-level worktree, as sol always does, default unset
  ORCA_LAUNCH_PREFIX         worktree name prefix, default none
  ORCA_LAUNCH_ROOT           directory Orca creates worktrees in, default the parent's directory
  ORCA_LAUNCH_BASE           base branch, default the parent checkout's origin/HEAD
  ORCA_LAUNCH_STATE          receipt directory, default ~/.claude/scratch/orca-launch/<run>
  ORCA_LAUNCH_CLAUDE_ARGS    further claude args from Orca's agent default args, default none
  ORCA_LAUNCH_RETRY_SECONDS  wait before a retry, default 30
  ORCA_LAUNCH_BOOT_SECONDS   ceiling on the wait for Orca to detect the terminal's agent, default 180
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
POLL=4
RECEIPT=$STATE/$LANE.json
WT=$(cat "$STATE/$LANE.worktree" 2>/dev/null || echo "$ROOT/$WORKTREE_NAME")

fail() {
  echo "$LANE failed $*"
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
COMMAND="env CLAUDE_LONG_RUNNING_LANE=$LANE${DRIVE:+ CLAUDE_LONG_RUNNING_DRIVE=$DRIVE} claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode${ORCA_LAUNCH_CLAUDE_ARGS:+ $ORCA_LAUNCH_CLAUDE_ARGS} --model $MODEL_ID --effort $EFFORT"
BIN=$(cd "$(dirname "$0")/../../../bin" && pwd)
[ "$AGENT" != sol ] || COMMAND="sh -c 'PATH=$BIN:\$PATH exec codex --dangerously-bypass-approvals-and-sandbox -c model=$MODEL_ID -c service_tier=fast -c model_reasoning_effort=$EFFORT'"
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

attempt=0
until [ -d "$WT" ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -le 4 ] || fail "worktree create: $(head -c 300 "$STATE/$LANE.worktree.json")"
  if orca worktree create --name "$WORKTREE_NAME" --repo "id:$REPO" --base-branch "$BASE" \
    "$@" --setup run --json >"$STATE/$LANE.worktree.json" 2>&1; then
    WT=$(jq -er '.result.worktree.path' "$STATE/$LANE.worktree.json")
  else
    sleep "$RETRY"
  fi
done
printf '%s\n' "$WT" >"$STATE/$LANE.worktree"
spec

attempt=0 TERMINAL=''
until [ "$AGENT" = codex ] || [ -n "$TERMINAL" ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -le 3 ] || fail "terminal create: $(head -c 300 "$STATE/$LANE.terminal.err")"
  TERMINAL=$(orca terminal create --worktree "path:$WT" --title "$NAME" --command "$COMMAND" --json \
    2>"$STATE/$LANE.terminal.err" | jq -r '.result.terminal.handle // empty') || TERMINAL=
  [ -n "$TERMINAL" ] || sleep "$RETRY"
done
IDENTITY=claude
[ "$AGENT" = claude ] || IDENTITY=codex
attempt=0 DETECTED=''
until [ "$AGENT" = codex ] || [ "$DETECTED" = "$IDENTITY" ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -le $(((BOOT + POLL - 1) / POLL)) ] ||
    fail "boot terminal=$TERMINAL: orca terminal list shows agentIdentity=${DETECTED:-none}, not $IDENTITY, after ${BOOT}s"
  sleep "$POLL"
  DETECTED=$(orca terminal list --json | jq -r --arg t "$TERMINAL" '.result.terminals[] | select(.handle == $t) | .agentIdentity // empty') || DETECTED=
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
orca orchestration worker-start --run "$RUN" "$@" --worktree "path:$WT" \
  --timeout-ms "$TIMEOUT" --json >"$RECEIPT.new" 2>"$STATE/$LANE.worker.err" || STARTED=$?
if jq -e '.result.taskId and .result.dispatchId' "$RECEIPT.new" >/dev/null 2>&1; then
  mv "$RECEIPT.new" "$RECEIPT"
  [ "$AGENT" != codex ] || TERMINAL=$(jq -r 'first(.result.effects[] | select(.kind == "terminal" and .role == "agent") | .id) // empty' "$RECEIPT")
  printf '%s\n' "$TERMINAL" >"$STATE/$LANE.terminal"
fi
if [ "$AGENT" != claude ] && [ -n "$TERMINAL" ] &&
  [ "$(jq -r '.result.failedStage // empty' "$RECEIPT")" = agent_readiness ]; then
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
