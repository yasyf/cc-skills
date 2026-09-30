#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: orca-launch.sh <lane> <model> <effort> <brief-file>

Launches one Orca worker for <lane>, or relaunches it when a receipt from an
earlier launch exists, and prints one line:

  <lane> ready task=<id> dispatch=<id> terminal=<handle> worktree=<path>

Anything else is a failure, printed as one line naming the step, exit 1.

The worktree is created once, as a child of the coordinator's worktree. The
terminal runs claude in bypass-permissions mode with the model and effort on its
command line, since Orca's default agent args start claude in plan mode and
worker-start refuses --model and --effort beside --terminal. The spec is a
pointer to <brief-file>, because Orca truncates a pasted spec near 3 KB; the
pointer must stay within 300 characters. The launch counts only once the
receipt reads ready and the terminal's screen shows bypass permissions on.

<model> is opus, sonnet, fable, or a full model id. <effort> is low, medium,
high, xhigh, or max. Worktree and terminal creation retry after
ORCA_LAUNCH_RETRY_SECONDS, because the runtime drops connections under load.

  ORCA_LAUNCH_RUN            orchestration Run id, required
  ORCA_LAUNCH_REPO           Orca repo id, required
  ORCA_LAUNCH_PARENT         coordinator worktree path, default $PWD
  ORCA_LAUNCH_PREFIX         worktree name prefix, default none
  ORCA_LAUNCH_ROOT           directory Orca creates worktrees in, default the parent's directory
  ORCA_LAUNCH_BASE           base branch, default the parent checkout's origin/HEAD
  ORCA_LAUNCH_STATE          receipt directory, default ~/.claude/scratch/orca-launch/<run>
  ORCA_LAUNCH_CLAUDE_ARGS    further claude args from Orca's agent default args, default none
  ORCA_LAUNCH_RETRY_SECONDS  wait before a retry, default 30
  ORCA_LAUNCH_BOOT_SECONDS   wait for claude to start, default 8
EOF
  exit 2
}

[ $# -eq 4 ] || usage
LANE=$1 MODEL=$2 EFFORT=$3 BRIEF=$4
RUN=${ORCA_LAUNCH_RUN:?ORCA_LAUNCH_RUN is required}
REPO=${ORCA_LAUNCH_REPO:?ORCA_LAUNCH_REPO is required}
PARENT=${ORCA_LAUNCH_PARENT:-$PWD}
NAME=${ORCA_LAUNCH_PREFIX:-}$LANE
ROOT=${ORCA_LAUNCH_ROOT:-$(dirname "$PARENT")}
STATE=${ORCA_LAUNCH_STATE:-$HOME/.claude/scratch/orca-launch/$RUN}
RETRY=${ORCA_LAUNCH_RETRY_SECONDS:-30}
BOOT=${ORCA_LAUNCH_BOOT_SECONDS:-8}
RECEIPT=$STATE/$LANE.json
WT=$(cat "$STATE/$LANE.worktree" 2>/dev/null || echo "$ROOT/$NAME")

fail() {
  echo "$LANE failed $*"
  exit 1
}

case $MODEL in
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
COMMAND="claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions${ORCA_LAUNCH_CLAUDE_ARGS:+ $ORCA_LAUNCH_CLAUDE_ARGS} --model $MODEL_ID --effort $EFFORT"
spec() {
  printf '%s' "Lane $LANE: read $BRIEF in full first and execute it exactly; Orca truncates specs. Worktree $WT, bypass-permissions mode; the brief's Escalate rules hold."
}
SPEC=$(spec)
[ "${#SPEC}" -le 300 ] || fail "spec pointer is ${#SPEC} characters, over 300; shorten the brief path"

mkdir -p "$STATE"

attempt=0
until [ -d "$WT" ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -le 4 ] || fail "worktree create: $(head -c 300 "$STATE/$LANE.worktree.json")"
  if orca worktree create --name "$NAME" --repo "id:$REPO" --base-branch "$BASE" \
    --parent-worktree "path:$PARENT" --setup run --json >"$STATE/$LANE.worktree.json" 2>&1; then
    WT=$(jq -er '.result.worktree.path' "$STATE/$LANE.worktree.json")
  else
    sleep "$RETRY"
  fi
done
printf '%s\n' "$WT" >"$STATE/$LANE.worktree"
SPEC=$(spec)
[ "${#SPEC}" -le 300 ] || fail "spec pointer is ${#SPEC} characters, over 300; shorten the brief path"

attempt=0 TERMINAL=''
until [ -n "$TERMINAL" ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -le 3 ] || fail "terminal create: $(head -c 300 "$STATE/$LANE.terminal.err")"
  TERMINAL=$(orca terminal create --worktree "path:$WT" --title "$NAME" --command "$COMMAND" --json \
    2>"$STATE/$LANE.terminal.err" | jq -r '.result.terminal.handle // empty') || TERMINAL=
  [ -n "$TERMINAL" ] || sleep "$RETRY"
done
sleep "$BOOT"

if [ -s "$RECEIPT" ]; then
  set -- --task "$(jq -r '.result.taskId' "$RECEIPT")" --retry-of "$(jq -r '.result.dispatchId' "$RECEIPT")"
else
  set -- --spec "$SPEC" --task-title "$NAME"
fi
STARTED=0
orca orchestration worker-start --run "$RUN" "$@" --worktree "path:$WT" --terminal "$TERMINAL" \
  --timeout-ms 600000 --json >"$RECEIPT.new" 2>"$STATE/$LANE.worker.err" || STARTED=$?
if jq -e '.result.taskId and .result.dispatchId' "$RECEIPT.new" >/dev/null 2>&1; then
  mv "$RECEIPT.new" "$RECEIPT"
  printf '%s\n' "$TERMINAL" >"$STATE/$LANE.terminal"
fi
[ "$STARTED" = 0 ] || fail "worker-start terminal=$TERMINAL: $(head -c 300 "$STATE/$LANE.worker.err")"
READY=$(jq -r '.result.state' "$RECEIPT")
[ "$READY" = ready ] || fail "worker-start state=$READY terminal=$TERMINAL"

attempt=0
until orca terminal read --terminal "$TERMINAL" --screen --json |
  jq -e '.result.terminal.tail | tostring | contains("bypass permissions on")' >/dev/null; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 5 ] || fail "terminal=$TERMINAL does not show bypass permissions on; shift-tab it before the worker opens a plan"
  sleep "$BOOT"
done

echo "$LANE ready task=$(jq -r '.result.taskId' "$RECEIPT") dispatch=$(jq -r '.result.dispatchId' "$RECEIPT") terminal=$TERMINAL worktree=$WT"
