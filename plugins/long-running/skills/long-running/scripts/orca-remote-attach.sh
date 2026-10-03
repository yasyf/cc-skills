#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: orca-remote-attach.sh <lane> <prepared-task.json>

Attaches the idle worker that cc-remote orca prepare left on a remote machine to
the coordinator's Orca Run with one orca orchestration worker-start, and prints
one line:

  <lane> ready task=<id> dispatch=<id> terminal=<handle> worktree=<path>

Anything else is a failure, printed as one line beginning "<lane> failed ",
exit 1. The helper never prints unsupervised.

<prepared-task.json> is the flat task record prepare printed: schemaVersion 1,
prepared true, no prompt receipts, the brief's absolute path on the machine with
its SHA-256 and byte count, the checkout's baseCommit, and the worktree ID
<repoId>::<projectRoot>. Paths are printable ASCII without spaces. A Fable model
refuses, since Fable requires an explicit local choice, and nothing launches
locally instead. The spec is a pointer of at most 300 characters telling the
worker to read and verify that brief; a longer pointer refuses rather than
shortening the brief or linking its path.

Before worker-start, orca orchestration run-current must name ORCA_LAUNCH_RUN,
and orca status for the recorded environment must answer from the recorded
runtime, reachable, with orchestration.contract.v1 and orchestration.federation.v1.
worker-start reuses the recorded worktree and terminal and passes no agent,
model, effort, or service tier, so the prepared process keeps its own.

Each lane gets one attempt. worker-start's stdout and stderr stay in the state
directory as <lane>.json and <lane>.worker.err, whatever the outcome, and an
earlier <lane>.json, <lane>.worker.err, <lane>.terminal, or <lane>.worktree
refuses another attempt. Only a ready receipt naming the Run, the recorded
environment, the reused worktree and terminal, no setup, accepted dispatch input,
and no residual resources writes <lane>.terminal and <lane>.worktree. Nothing is
retried, closed, or cleaned up.

  ORCA_LAUNCH_RUN    orchestration Run id, required
  ORCA_LAUNCH_STATE  receipt directory, default ~/.claude/scratch/orca-launch/<run>
EOF
  exit 2
}

ID='\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\z'
WORD='\A[a-z][a-z0-9_]{0,63}\z'

matches() {
  jq -ne --arg value "$1" --arg pattern "$2" '$value | test($pattern)' >/dev/null 2>&1
}

pick() {
  jq -rs --arg pattern "$2" "if length == 1 then .[0] | $1 | select(type == \"string\" and test(\$pattern)) else empty end" 2>/dev/null || :
}

fail() {
  printf '%s failed %s\n' "$LANE" "$*"
  exit 1
}

[ $# -eq 2 ] || usage
LANE=$1 PREPARED=$2
matches "$LANE" '\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\z' || {
  echo "orca-remote-attach.sh: <lane> takes up to 64 letters, digits, '.', '_', and '-', starting with a letter or digit" >&2
  exit 2
}
RUN=${ORCA_LAUNCH_RUN:-}
[ -n "$RUN" ] || fail "ORCA_LAUNCH_RUN is required"
matches "$RUN" "$ID" || fail "ORCA_LAUNCH_RUN is not a plain Run id"
[ -f "$PREPARED" ] && [ -r "$PREPARED" ] || fail "prepared record: not a readable file"

FIELDS=$(jq -rs '
  def nonempty: type == "string" and length > 0;
  def ident: type == "string" and test("\\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\\z");
  def token: type == "string" and test("\\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\\z");
  def abspath: type == "string" and test("\\A/[!-~]{1,1023}\\z") and (.[1:] | split("/") | all(. != "" and . != "." and . != ".."));
  if length != 1 or (.[0] | type) != "object" then "refused not one JSON object"
  else .[0] as $t
    | if $t.schemaVersion != 1 then "refused schemaVersion is not 1"
      elif $t.prepared != true then "refused prepared is not true"
      elif ([$t.workspace, $t.provider, $t.profile, $t.machine] | all(nonempty) | not) then "refused workspace, provider, profile, and machine must be nonempty strings"
      elif ([$t.environment, $t.environmentId, $t.runtimeId, $t.repoId, $t.terminal] | all(ident) | not) then "refused environment, environmentId, runtimeId, repoId, and terminal must be plain identifiers"
      elif ($t.projectRoot | abspath | not) then "refused projectRoot is not a plain absolute path"
      elif $t.worktreeId != "\($t.repoId)::\($t.projectRoot)" then "refused worktreeId is not repoId::projectRoot"
      elif ($t.agent | type) != "object" or ($t.agent.kind | IN("claude", "codex") | not) or ([$t.agent.model, $t.agent.effort] | all(token) | not) then "refused agent needs kind claude or codex with a plain model and effort"
      elif ($t.agent.model | test("(^|[-_.])fable([-_.0-9]|$)"; "i")) then "fable"
      elif ($t.agent | has("serviceTier")) and ($t.agent.kind != "codex" or ($t.agent.serviceTier | token | not)) then "refused agent.serviceTier is only a plain codex tier"
      elif ($t | has("receipts")) and $t.receipts != [] then "refused the task already records prompt receipts"
      elif ($t | has("bootstrap")) and (($t.bootstrap | type) != "array" or ($t.bootstrap | all(nonempty) | not)) then "refused bootstrap is not a list of steps"
      elif ($t.brief | type) != "object" or ($t.brief.path | abspath | not) or ($t.brief.sha256 | type != "string" or (test("\\A[0-9a-f]{64}\\z") | not)) or ($t.brief.bytes | type != "number" or . != floor or . < 1 or . > 9007199254740991) then "refused brief needs a plain absolute path, a lowercase SHA-256, and a positive byte count"
      elif ($t.baseCommit | type != "string" or (test("\\A[0-9a-f]{40}([0-9a-f]{24})?\\z") | not)) then "refused baseCommit is not a full 40- or 64-character lowercase commit"
      else "ok \($t.environment) \($t.environmentId) \($t.runtimeId) \($t.worktreeId) \($t.projectRoot) \($t.terminal) \($t.brief.path) \($t.brief.sha256) \($t.brief.bytes | floor)"
      end
  end' "$PREPARED" 2>/dev/null) || fail "prepared record: not readable as JSON"
case $FIELDS in
  fable) fail "model: Fable requires an explicit local choice; a remote worker never runs it, and nothing launches locally instead" ;;
  "refused "*) fail "prepared record: ${FIELDS#refused }" ;;
esac
set -f
set -- $FIELDS
set +f
ENVIRONMENT=$2 ENVIRONMENT_ID=$3 RUNTIME=$4 WORKTREE_ID=$5 ROOT=$6 TERMINAL=$7 BRIEF_PATH=$8 BRIEF_SHA=$9 BRIEF_BYTES=${10}

SPEC="Lane $LANE: read the whole brief at $BRIEF_PATH, verify its SHA-256 is $BRIEF_SHA and its size is $BRIEF_BYTES bytes, then follow it exactly."
[ "${#SPEC}" -le 300 ] || fail "spec pointer is ${#SPEC} characters, over 300; the prepared worker stays idle"

STATE=${ORCA_LAUNCH_STATE:-$HOME/.claude/scratch/orca-launch/$RUN}
RECEIPT=$STATE/$LANE.json ERR=$STATE/$LANE.worker.err
for prior in "$RECEIPT" "$ERR" "$STATE/$LANE.terminal" "$STATE/$LANE.worktree"; do
  [ ! -e "$prior" ] && [ ! -L "$prior" ] ||
    fail "attempt: the state directory already holds ${prior##*/}; a lane gets one attachment attempt, so inspect it instead"
done

BINDING=$(orca orchestration run-current --json 2>/dev/null) && BOUND=0 || BOUND=$?
[ "$BOUND" = 0 ] && printf '%s' "$BINDING" | jq -se --arg run "$RUN" 'length == 1 and (.[0] | type == "object" and .ok == true and .error == null and .result.run.id == $run)' >/dev/null 2>&1 || {
  CODE=$(printf '%s' "$BINDING" | pick '.error.code' "$WORD")
  fail "coordinator binding: run-current exited $BOUND${CODE:+ code=$CODE} without naming Run $RUN; no attachment was attempted, so attach from the coordinator that already owns Run $RUN"
}

STATUS=$(orca status --environment "$ENVIRONMENT" --json 2>/dev/null) && ANSWERED=0 || ANSWERED=$?
CHECK=$(printf '%s' "$STATUS" | jq -rs --arg runtime "$RUNTIME" '
  if length != 1 or (.[0] | type) != "object" then "no single JSON object"
  else .[0]
    | if .ok != true or .error != null then "an error"
      elif ._meta.runtimeId != $runtime then "an answer from another runtime"
      elif .result.runtime.reachable != true then "an unreachable runtime"
      elif .result.runtime.runtimeId != $runtime then "another runtime ID"
      elif (.result.runtime.capabilities | type != "array" or (all(type == "string") | not)) then "no capability list"
      elif (.result.runtime.capabilities | any(. == "orchestration.contract.v1") | not) then "no orchestration.contract.v1"
      elif (.result.runtime.capabilities | any(. == "orchestration.federation.v1") | not) then "no orchestration.federation.v1"
      else "ok"
      end
  end' 2>/dev/null) || CHECK="an unreadable answer"
[ "$ANSWERED" = 0 ] && [ "$CHECK" = ok ] || {
  CODE=$(printf '%s' "$STATUS" | pick '.error.code' "$WORD")
  fail "status: environment $ENVIRONMENT exited $ANSWERED with $CHECK${CODE:+ code=$CODE}, not reachable runtime $RUNTIME with orchestration.contract.v1 and orchestration.federation.v1"
}

mkdir -p "$STATE" || fail "attempt: cannot create the state directory"
(set -C && : >"$RECEIPT" && : >"$ERR") 2>/dev/null ||
  fail "attempt: another attachment of $LANE claimed ${RECEIPT##*/} first; inspect it instead"

STARTED=0
orca orchestration worker-start --run "$RUN" --on "$ENVIRONMENT" --worktree "id:$WORKTREE_ID" \
  --terminal "$TERMINAL" --spec "$SPEC" --task-title "$LANE" --json >"$RECEIPT" 2>"$ERR" || STARTED=$?

TASK=$(pick '.result.taskId' "$ID" <"$RECEIPT")
DISPATCH=$(pick '.result.dispatchId' "$ID" <"$RECEIPT")
IDS="${TASK:+ task=$TASK}${DISPATCH:+ dispatch=$DISPATCH}"
KEPT="; kept ${RECEIPT##*/} and ${ERR##*/}, sent nothing more, and cleaned up nothing"
if [ "$STARTED" != 0 ]; then
  CODE=$(pick '.error.code' "$WORD" <"$RECEIPT")
  REPORTED=$(pick '.result.state' "$WORD" <"$RECEIPT")
  fail "worker-start exited $STARTED${CODE:+ code=$CODE}${REPORTED:+ state=$REPORTED}$IDS$KEPT"
fi
[ -s "$RECEIPT" ] || fail "worker-start returned nothing$KEPT"
VERDICT=$(jq -rs --arg run "$RUN" --arg environment "$ENVIRONMENT" --arg environmentId "$ENVIRONMENT_ID" \
  --arg worktree "$WORKTREE_ID" --arg terminal "$TERMINAL" '
  def ident: type == "string" and test("\\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\\z");
  [
    {kind: "worktree", action: "reused", id: $worktree},
    {kind: "setup", action: "not_applicable", state: "not_applicable"},
    {kind: "terminal", role: "agent", action: "reused", id: $terminal},
    {kind: "dispatch_input", role: "agent", id: $terminal, state: "accepted"}
  ] as $effects
  | if length != 1 or (.[0] | type) != "object" then "no single JSON object"
    else .[0] as $r | $r.result as $x
      | if $r.ok != true or $r.error != null then "an error envelope"
        elif ($x | type) != "object" then "no result"
        elif $x.state != "ready" then "state=\($x.state | if type == "string" and test("\\A[a-z][a-z0-9_]{0,63}\\z") then . else "unrecognized" end)"
        elif $x.runId != $run then "another Run"
        elif ($x.taskId | ident | not) or ($x.dispatchId | ident | not) then "no printable task and dispatch IDs"
        elif ($x.stage | type != "string" or length == 0) then "no stage"
        elif ($x.server | type) != "object" or $x.server.environmentId != $environmentId or $x.server.name != $environment then "another server"
        elif ($x.setup | type) != "object" or $x.setup.requested != "not_applicable" or $x.setup.effective != "not_applicable" or $x.setup.state != "not_applicable" or $x.setup.source != "existing_worktree" or $x.setup.hookFound != false or $x.setup.startupPolicy != "start-immediately" then "a setup other than none on the existing worktree"
        elif ($x.effects | type) != "array" or ($x.effects | sort) != ($effects | sort) then "effects other than the reused worktree and terminal with accepted dispatch input"
        elif $x.residualResources != [] then "residual resources"
        else "ok"
        end
    end' "$RECEIPT" 2>/dev/null) || VERDICT="no single JSON object"
[ "$VERDICT" = ok ] || fail "worker-start returned $VERDICT, not a matching ready receipt$IDS$KEPT"

printf '%s\n' "$TERMINAL" >"$STATE/$LANE.terminal"
printf '%s\n' "$ROOT" >"$STATE/$LANE.worktree"
printf '%s ready task=%s dispatch=%s terminal=%s worktree=%s\n' "$LANE" "$TASK" "$DISPATCH" "$TERMINAL" "$ROOT"
