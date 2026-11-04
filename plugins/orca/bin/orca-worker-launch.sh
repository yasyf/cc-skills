#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 4 ]; then
  echo "usage: WORKER_RUN=<run_id> $0 <lane> <model> <effort> <brief-file>" >&2
  exit 2
fi

lane=$1 model=$2 effort=$3 brief=$4
run=${WORKER_RUN:?set WORKER_RUN to the Orca Run id}
ref=${WORKER_REF:-}
expect=${WORKER_EXPECT_BASE:-}
tier=${WORKER_SERVICE_TIER:-}
lane_env='DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING=1 CHOKIDAR_USEPOLLING=1 CHOKIDAR_INTERVAL=300000 CLAUDE_CODE_DISABLE_MOUSE=1 CLAUDE_CODE_SYNTAX_HIGHLIGHT=false CLAUDE_CODE_DISABLE_AGENT_PROGRESS_SUMMARIES=1 CLAUDE_CODE_ENABLE_AWAY_SUMMARY=false CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1 DISABLE_COST_WARNINGS=1 DISABLE_INSTALLATION_CHECKS=1 CLAUDE_CODE_AUTO_CONNECT_IDE=false CLAUDE_CODE_IDE_SKIP_AUTO_INSTALL=1 ENABLE_CLAUDEAI_MCP_SERVERS=false DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF=1 CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1 CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1 CLAUDE_CODE_PLUGIN_DIR_WATCH=false'
lane_settings='{"spinnerTipsEnabled":false,"prefersReducedMotion":true,"verbose":false,"showThinkingSummaries":false,"feedbackDrafts":"off","awaySummaryEnabled":false,"promptSuggestionEnabled":false,"tui":"fullscreen","disableDeepLinkRegistration":"disable","statusLine":{"type":"command","command":"exit 0"}}'
lane_denied_tools=AskUserQuestion,EnterPlanMode,ExitPlanMode,PushNotification,DesignSync

die() {
  echo "$*" >&2
  exit 1
}

matches() {
  jq -ne --arg value "$1" --arg pattern "$2" '$value | test($pattern)' >/dev/null
}

repo_value() {
  [ ! -f .orca.json ] || jq -r --arg key "$1" '.[$key] // empty | if type == "array" then join(" ") else . end' .orca.json
}

[ -f "$brief" ] || die "no brief at $brief"
dir=$(cd "$(dirname "$brief")" && pwd) name=$(basename "$brief")
brief=$dir/$name
attempt=$dir/${name%.*}.worker

case $model in
  opus) model_id="claude-opus-5-5" ;;
  sonnet) model_id="claude-sonnet-5-5" ;;
  fable) model_id="claude-fable-5-1" ;;
  *) model_id=$model ;;
esac
case $model_id in
  claude-*) kind=claude ;;
  gpt-*) kind=codex ;;
  *) die "model $model_id is neither a claude- nor a gpt- model, so neither claude nor codex runs it; nothing launched" ;;
esac
[ -z "$tier" ] || [ "$kind" = codex ] || die "WORKER_SERVICE_TIER is a codex setting, and claude runs $model_id; nothing launched"
[ "$tier" != fast ] || [ "${WORKER_INCIDENT:-}" = 1 ] || die "WORKER_SERVICE_TIER=fast is for incident lanes only; set WORKER_INCIDENT=1 for an incident lane; nothing launched"

case ${WORKER_LOCAL:-} in
  "") route=sprite ;;
  1) route=local ;;
  *) die "WORKER_LOCAL is 1 for the explicit local route or unset for a Sprite; nothing launched" ;;
esac

if [ "$route" = sprite ]; then
  matches "$lane" '\A[a-z0-9][a-z0-9-]{0,54}\z' ||
    die "a Sprite lane takes up to 55 lowercase letters, digits, and dashes, starting with a letter or digit; nothing was prepared"
  if grep -qiE '(^|[-_.])fable([-_.0-9]|$)' <<<"$model_id"; then
    die "$model_id is Fable, which runs only on the explicit local route, WORKER_LOCAL=1; nothing was prepared, and nothing launched locally instead"
  fi
  [ -z "${WORKER_REPO:-}" ] ||
    die "WORKER_REPO picks a local Orca repository; a Sprite checks out the repository its cc-remote config names, chosen with CC_REMOTE_CONFIG; nothing was prepared"
  [ -z "${WORKER_BASE_BRANCH:-}" ] ||
    die "WORKER_BASE_BRANCH picks a local worktree's base; a Sprite checks out WORKER_REF, a branch or tag, or the cc-remote config's ref; nothing was prepared"
  [ -z "$expect" ] || matches "$expect" '\A[0-9a-f]{40}([0-9a-f]{24})?\z' ||
    die "WORKER_EXPECT_BASE is a full 40- or 64-character lowercase commit; nothing was prepared"
  cc_remote=${WORKER_CC_REMOTE:-$(repo_value ccRemote)}
  cc_remote=${cc_remote:-cc-remote}
  command -v "$cc_remote" >/dev/null ||
    die "WORKER_CC_REMOTE names no cc-remote command at $cc_remote; nothing was prepared"
  config=${CC_REMOTE_CONFIG:-$(repo_value ccRemoteConfig)}
  [ -n "$config" ] ||
    die "set CC_REMOTE_CONFIG, or ccRemoteConfig in .orca.json, to the cc-remote config that names the repository a Sprite checks out; nothing was prepared"
  [[ $config == /* ]] || config=$PWD/$config
  export CC_REMOTE_CONFIG=$config
  payload=$(dirname "$(readlink -f "$config")")/agents-payload.json
  if [ -e "$payload" ]; then
    pin=$(jq -er '.bootstrapHelper.version' "$payload") ||
      die "$payload pins no bootstrap helper version; nothing was prepared"
    printed=$("$cc_remote" version 2>&1) || die "$cc_remote version failed: $printed; nothing was prepared"
    version=${printed##*$'\n'}
    [ "$version" = "$pin" ] ||
      die "$cc_remote version printed ${version:-nothing}, and $CC_REMOTE_CONFIG pins bootstrap helper $pin; a Sprite needs the CLI at exactly its helper's release; nothing was prepared"
  fi
else
  [ "$kind" = claude ] || die "the local route starts only claude, and codex runs $model_id; nothing launched"
  [ -z "$ref$expect" ] || die "WORKER_REF and WORKER_EXPECT_BASE pick a Sprite's checkout; the local route takes WORKER_BASE_BRANCH; nothing launched"
  repo=${WORKER_REPO:-$(repo_value repo)} base=${WORKER_BASE_BRANCH:-$(repo_value baseBranch)}
  [ -n "$repo" ] || die "set WORKER_REPO, or repo in .orca.json, to the Orca repository the local route adds its worktree to; nothing launched"
  [ -n "$base" ] || die "set WORKER_BASE_BRANCH, or baseBranch in .orca.json, to the branch a local worktree starts from; nothing launched"
  worktree=$lane-$(printf '%s' "$run" | shasum -a 256 | cut -c1-8)
fi

orchestration() {
  orca orchestration "$@" --full --json
}
bound=$(orchestration run-current |
  jq -rs 'select(length == 1) | .[0] | select(.ok == true and .error == null) | .result.run.id // empty' 2>/dev/null) || bound=
[ "$bound" = "$run" ] ||
  die "terminal ${ORCA_TERMINAL_HANDLE:-unset} coordinates ${bound:-no Run}, not $run, so nothing launched; launch from the coordinator that already owns Run $run"

legacy=$dir/${name%.*}.dispatch.json
[ ! -e "$legacy" ] && [ ! -L "$legacy" ] ||
  die "$legacy holds an earlier launch of this brief, and a brief gets one attempt; inspect it instead"
[ ! -e "$attempt" ] && [ ! -L "$attempt" ] ||
  die "$attempt holds an earlier attempt of this brief, and a brief gets one attempt; inspect it instead"
mkdir "$attempt"
sha=$(shasum -a 256 "$brief" | cut -d' ' -f1)
bytes=$(wc -c <"$brief" | tr -d ' ')
jq -n --arg lane "$lane" --arg run "$run" --arg route "$route" --arg kind "$kind" --arg model "$model_id" --arg effort "$effort" \
  --arg tier "$tier" --arg ref "$ref" --arg expect "$expect" --arg path "$brief" --arg sha "$sha" --argjson bytes "$bytes" \
  --arg worktree "${worktree:-}" '
  def given: if . == "" then null else . end;
  {lane: $lane, run: $run, route: $route, worktree: ($worktree | given),
    agent: {kind: $kind, model: $model, effort: $effort, serviceTier: ($tier | given)},
    ref: ($ref | given), expectBase: ($expect | given),
    brief: {path: $path, sha256: $sha, bytes: $bytes}}' >"$attempt/request.json"

start_worker() {
  local spec=$1 status=0 error
  shift
  orchestration worker-start --run "$run" --spec "$spec" --task-title "$lane" "$@" \
    --timeout-ms 600000 >"$attempt/worker-start.json" 2>"$attempt/worker-start.err" || status=$?
  echo "$status" >"$attempt/worker-start.status"
  if [ "$status" -ne 0 ]; then
    error=$(jq -r 'select(.ok == false) | "\(.error.code): \(.error.message)"' "$attempt/worker-start.json" 2>/dev/null) || error=
    die "orca orchestration worker-start exited $status${error:+: $error}; kept terminal $terminal, worktree $wt, and the receipt in $attempt"
  fi
  jq -se --arg run "$run" '
    def ident: type == "string" and test("\\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\\z");
    length == 1 and .[0].ok == true and .[0].error == null and .[0].result.state == "ready" and .[0].result.runId == $run
      and (.[0].result.taskId | ident) and (.[0].result.dispatchId | ident)' "$attempt/worker-start.json" >/dev/null 2>&1 ||
    die "orca orchestration worker-start exited 0 without a ready receipt for Run $run; kept terminal $terminal, worktree $wt, and the receipt in $attempt"
}

if [ "$route" = sprite ]; then
  prepare=(orca prepare "$lane" --warm --provider sprites --profile agents --agent "$kind" --model "$model_id" --effort "$effort" --brief-file "$brief")
  [ -z "$ref" ] || prepare+=(--ref "$ref")
  [ -z "$tier" ] || prepare+=(--service-tier "$tier")
  [ -z "${WORKER_MCP_CONFIG:-}" ] || prepare+=(--mcp-config "$WORKER_MCP_CONFIG")
  status=0
  "$cc_remote" "${prepare[@]}" >"$attempt/allocation.json" 2>"$attempt/prepare.err" || status=$?
  echo "$status" >"$attempt/prepare.status"
  [ "$status" -eq 0 ] || die "cc-remote orca prepare exited $status; $attempt keeps its output, and nothing was attached or retried"

  verdict=$(jq -rs --arg lane "$lane" --arg ref "$ref" --arg kind "$kind" --arg model "$model_id" --arg effort "$effort" --arg tier "$tier" \
    --arg sha "$sha" --argjson bytes "$bytes" '
    if length != 1 or (.[0] | type) != "object" then "not one JSON object"
    else .[0]
      | if .schemaVersion != 1 or .lane != $lane then "an allocation other than schema 1 for lane \($lane)"
        elif (.workspace | type) != "string" or (.workspace | test("\\A[a-z0-9][a-z0-9-]{0,54}\\z") | not) then "an invalid allocated workspace"
        elif (.allocation != "warm" and .allocation != "created") or (.allocation == "created" and .workspace != $lane) or (.allocation == "warm" and .workspace == $lane) then "an allocation kind inconsistent with its workspace"
        elif (.ref | type) != "string" or .ref == "" or ($ref != "" and .ref != $ref) then "a ref other than the requested branch or tag"
        elif (.task | type) != "object" or .task.workspace != .workspace or .task.provider != "sprites" or .task.profile != "agents" then "a task other than the allocated Sprite agents workspace"
        elif .task.prepared != true then "prepared is not true"
        elif (.task.managedServer | type) != "string" or .task.managedServer == "" then "an Orca runtime serving on Electron, not its managed server"
        elif .head != .task.baseCommit then "an allocation HEAD inconsistent with its task"
        elif .task.agent.kind != $kind or .task.agent.model != $model or .task.agent.effort != $effort or (.task.agent.serviceTier // "") != $tier then "an agent other than \($kind) \($model) at \($effort) effort"
        elif .task.brief.sha256 != $sha or .task.brief.bytes != $bytes then "a brief other than \($bytes) bytes with SHA-256 \($sha)"
        elif ([.task.environment, .task.terminal, .task.projectRoot, .task.brief.path] | all(type == "string" and length > 0) | not) or .task.worktreeId != "\(.task.repoId)::\(.task.projectRoot)" then "a task without its environment, terminal, repoId::projectRoot worktree, and brief path"
        else "ok \(.task.baseCommit)"
        end
    end' "$attempt/allocation.json" 2>/dev/null) || verdict="not readable as JSON"
  [[ $verdict == "ok "* ]] || die "$attempt/allocation.json holds $verdict, so the prepared worker stays idle and unattached"
  jq '.task' "$attempt/allocation.json" >"$attempt/prepared.json"
  base=${verdict#ok }
  [ -z "$expect" ] || [ "$base" = "$expect" ] ||
    die "the Sprite checked out $base, not WORKER_EXPECT_BASE $expect, so the prepared worker stays idle and unattached, and nothing is prepared again"

  terminal=$(jq -r .terminal "$attempt/prepared.json")
  wt=$(jq -r .projectRoot "$attempt/prepared.json")
  start_worker "Lane $lane: read the whole brief at $(jq -r .brief.path "$attempt/prepared.json"), verify its SHA-256 is $sha and its size is $bytes bytes, then follow it exactly." \
    --on "$(jq -r .environment "$attempt/prepared.json")" --worktree "id:$(jq -r .worktreeId "$attempt/prepared.json")" --terminal "$terminal"
  jq -c --arg terminal "$terminal" --arg worktree "$wt" --slurpfile allocation "$attempt/allocation.json" \
    '{state: .result.state, task: .result.taskId, dispatch: .result.dispatchId, $terminal, $worktree,
      lane: $allocation[0].lane, workspace: $allocation[0].workspace, allocation: $allocation[0].allocation}' "$attempt/worker-start.json"
  exit 0
fi

orca_json() {
  local out
  out=$(orca "$@" --json) || { echo "orca $1 $2 failed: $out" >&2; return 1; }
  echo "$out"
}

worktree_path() {
  orca_json worktree list --repo "id:$repo" --limit 10000 |
    jq -r --arg name "$worktree" '.result.worktrees[] | select(.displayName == $name) | .path'
}

existing=$(worktree_path)
if [ -z "$existing" ]; then
  for cut in "$dir"/*.worker/worktree; do
    name=
    { read -r name <"$cut"; } 2>/dev/null || true
    [ "$name" != "$worktree" ] ||
      die "worktree $worktree was cut for ${cut%/worktree} and is gone, and a name is never cut twice; launch under a new lane name; nothing launched"
  done
fi
created=
# worktree create can drop its RPC at ~30 s while setup runs and still create the worktree.
[ -n "$existing" ] ||
  created=$(orca_json worktree create --name "$worktree" --repo "id:$repo" --base-branch "$base" --setup run |
    jq -r '.result.worktree.path // empty') ||
  true

wt=${created:-$existing}
[ -n "$wt" ] || wt=$(worktree_path)
[ -n "$wt" ] || die "no worktree named $worktree"
printf '%s\n' "$worktree" >"$attempt/worktree"
sparse=
read -ra cone <<<"$(repo_value sparse) ${WORKER_SPARSE:-}"
if [ -z "$existing" ] && [ ${#cone[@]} -gt 0 ]; then
  git -C "$wt" sparse-checkout set --cone "${cone[@]}"
  widen="git -C $wt sparse-checkout add <dir>"
  echo "$wt is sparse: widen with $widen" >&2
  sparse=" This lane is sparse: widen with \`$widen\` before reading outside it."
fi

terminal=
# worktree create opens a login shell in the new worktree without returning its handle; claude runs in that shell.
[ -n "$existing" ] || terminal=$(orca_json terminal list --worktree "path:$wt" |
  jq -r 'first(.result.terminals[] | select((.agentIdentity // "") == "") | .handle) // empty')
# terminal create --command times out waiting for the handle while claude boots in a fresh worktree.
[ -n "$terminal" ] || terminal=$(orca_json terminal create --worktree "path:$wt" --title "$lane" | jq -er '.result.terminal.handle')
printf -v claude 'env %s claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools %s --strict-mcp-config --no-chrome --model %q --effort %q' "$lane_env" "$lane_denied_tools" "$model_id" "$effort"
[ -z "${WORKER_MCP_CONFIG:-}" ] || printf -v claude '%s --mcp-config %q' "$claude" "$WORKER_MCP_CONFIG"
[ -z "${CLAUDE_LANE_SETTINGS:-}" ] || lane_settings=$(jq -c --argjson local "$CLAUDE_LANE_SETTINGS" '. + $local' <<<"$lane_settings")
printf -v claude "%s --settings '%s'" "$claude" "$lane_settings"
orca_json terminal send --terminal "$terminal" --enter --text "$claude" >/dev/null

for _ in $(seq 120); do
  agent=$(orca_json terminal show --terminal "$terminal" | jq -r '.result.terminal.agentIdentity')
  [ "$agent" = claude ] && break
  sleep 1
done
[ "$agent" = claude ] || die "claude never started in $terminal; kept it and worktree $wt"
orca_json terminal wait --terminal "$terminal" --for tui-idle --timeout-ms 120000 >/dev/null ||
  die "claude in $terminal never went idle; kept it and worktree $wt"

start_worker "Lane '$lane'. Your full brief is the file $brief: read it in full with the Read tool before anything else and execute it exactly (Orca truncates pasted specs). Your Orca worktree is $wt.$sparse You run in bypass-permissions mode; the brief's Escalate rules still hold." \
  --worktree "path:$wt" --terminal "$terminal"
jq -c --arg terminal "$terminal" --arg worktree "$wt" \
  '{state: .result.state, task: .result.taskId, dispatch: .result.dispatchId, $terminal, $worktree}' "$attempt/worker-start.json"
