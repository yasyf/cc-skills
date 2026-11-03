#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: orca-launch.sh <lane> <model> <effort> <brief-file>

Launches one Orca worker for <lane> and prints one line:

  <lane> ready task=<id> dispatch=<id> terminal=<handle> worktree=<path>

Anything else is a failure, printed as one line naming the step, exit 1.

A relaunch whose receipt names a worktree that is still a checkout resumes in it
once Orca lists it. Every other launch makes a worktree under a name no launch has
used: <prefix><lane>-<run>-<n> on branch <user>/<prefix><lane>-<run>-<n>, where
<run> is the first 8 characters of the Run id after run_, <n> counts up past every
name the lane's <lane>.names receipt records and every <root>/<name> path or
branch that already exists, '/' in the name becomes '-', and <user> is the
gitUsername Orca records for the repo. The owner ruled that a name is never cut
twice, and a branch <user>/<prefix><lane> would also make git refuse every branch
under the lane's <user>/<prefix><lane>/ prefix.

By default orca worktree create makes it, a full checkout and a child of the
coordinator's worktree, wherever Orca puts worktrees; <root> is where the script
expects that, ORCA_LAUNCH_ROOT or the parent's directory. A create that fails
may still have created the worktree, so the script polls orca worktree show for
up to ORCA_LAUNCH_WORKTREE_SECONDS and creates again only when none registers. It
looks the worktree up by its name, since Orca chooses where it puts it. Orca
registers a worktree at git worktree add --no-checkout and fills its index with a
reset --hard that can run past a minute under load, so a worktree found that way
waits, for the same ceiling, until its index matches HEAD; one that never gets
there is never adopted. orca worktree create also opens a first terminal, a login
shell, and returns no handle for it. When Orca reports no setup terminal or
default tabs for the worktree it made, the script closes every terminal Orca
lists there with no agent, before creating its own. Release v3 kept 279 of these
idle shells, one per lane.

ORCA_LAUNCH_SPARSE=1 adds the worktree by git worktree add from the repo's main
checkout instead, at <root>/<name>, since orca worktree create cannot make a
sparse checkout and git copies sparse config only from the checkout that runs
worktree add. <root> is then ORCA_LAUNCH_ROOT, else <ccx.worktreeRoot>/<main
checkout's name> when the main checkout's git config sets ccx.worktreeRoot, else
the parent's directory. The branch starts at the base, fetched from its remote
first as Orca does, and tracks nothing. A checkout whose HEAD tracks more
than ORCA_LAUNCH_SPARSE_FILES files is sparse in cone mode: the root's files,
.agents, .claude, tools, infra/ci/hooks/lib, which the monorepo's tools/ci
sources, and each directory a paths=<dir>,<dir> key on the brief's ccx: header
line names. A smaller repo keeps a full checkout. The script prints
the cone and the widen command, git -C <wt> sparse-checkout add <dir>, on stderr,
and the spec tells the worker to widen before reading outside it. Orca lists the
worktree a few seconds after it reads git's registry; the script waits up to
ORCA_LAUNCH_WORKTREE_SECONDS for that, then gives it its name as Orca's display
name and tags it as a child of
the coordinator's worktree with orca worktree set. Like Orca's own create, it links
the repo's symlinkPaths from the main checkout and copies each existing,
gitignored path .worktreeinclude names. It runs no Orca setup hook; a repo that
needs one sets ORCA_LAUNCH_PREPARE.
The terminal runs claude in bypass-permissions mode with the model and effort on its
command line, since Orca's default agent args start claude in plan mode and
worker-start refuses --model and --effort beside --terminal. The command always
disallows AskUserQuestion, EnterPlanMode, and ExitPlanMode: a worker's prompt
reaches only its own terminal, which no one watches. It also passes
--strict-mcp-config, so a worker starts only the MCP servers ORCA_LAUNCH_MCP_CONFIG
names instead of the eight or so every interactive session starts; a brief that
needs a server names it there. Claude, and an incident lane's codex, run under
CLAUDE_LONG_RUNNING_LANE=<lane> and, when drive.py places this session in a drive,
CLAUDE_LONG_RUNNING_DRIVE=<drive>, so the pack's PR hook records every PR the worker
opens in the drive's ledger under the lane's name, drive.py finds the drive with no
Claude session id, and a ready launch posts the drive's
SPAWNED record (drive.py spawned) that adds the lane to the supervisor's registry. Lane root, the
coordinator, also gets the 1-hour prompt cache, since its one wait can pass the 5-minute one, and a
compaction window of 280k, so it compacts near 250k and every call re-reads less. The spec is a
pointer to <brief-file>, because Orca truncates a pasted spec near 3 KB. It names
the brief as the authority over Orca's preamble, which forbids GitHub posts, and
over any standing leave-uncommitted default. A sparse worktree's pointer adds
"This lane is sparse: widen with `git sparse-checkout add <dir>` before reading
outside it." In a worktree that carries tools/hooks/warm-lane.sh, the pointer
also says "node_modules is a shared symlink farm; never run yarn install, ask
warm-lane to rebuild for a changed lockfile." A brief whose ccx: header line
says role=desk or role=watch is a standing desk, and its pointer adds the desk
contract: loop until rotation, and send worker_done only at rotation, naming the
handoff doc, since Orca's preamble otherwise reads setup or one quiet cycle as
the finished task. A brief path that pushes the pointer over 500 characters is
replaced by a shorter symlink $XDG_STATE_HOME/long-running/orca-launch/<8 hex of the path's sha> to the brief; the
lane and worktree path go in as they are, since Orca truncates only near 3 KB. Before
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

A codex, incident, astra, or gpt-* lane reads a copy of its brief at
$STATE/<lane>.brief.md, ending in a PR prose section: the lane writes no prose,
so it runs the plugin's pr-prose.py, a headless Claude Opus 5.5 writer, for its PR
title, body, and commit message, and never asks the root for them. An incident
lane's section puts the push first: it starts the writer in the background, opens
the PR within 10 minutes of its candidate with a short body of its own when the
writer is not back, and never waits on prose. In a drive the
copy then carries a PR record section: Codex runs no PR hook and its cci posts bind
no lane, so ship neither registers the PR in the ledger nor posts its opened record,
and the lane runs drive.py record --opened after each ship that opens a PR, which
the incident clock and the landing desk read. In a drive the copy also opens with a
First command section: a codex lane's first command posts its registration record, so
cci who lists it before any claim for it, as a Claude lane's first-command hook does.

A codex model launches on Orca's codex agent instead: worker-start creates the
terminal with --agent codex --model --effort, and Orca's codex default args
already bypass approvals, so there is no custom command or bypass-permissions screen check.
Its service tier comes from Orca's codex runtime config, the standard tier,
since worker-start has no tier flag.

incident is the incident lane: gpt-6.1-sol in a top-level worktree, launched in a
terminal running codex with -c service_tier=fast on its command line, so the
fast tier never depends on Orca's runtime config. No other alias runs the fast
tier. incident-opus is the incident fallback when a sol fix lane misses: Claude
Opus 5.5 with --settings '{"fastMode":true}' on its command line, which turns
Claude Code's fast mode on even under a fastModePerSessionOptIn setting. No other
alias enables fast mode. The incident alias also passes
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
the standard tier), incident (gpt-6.1-sol on the fast tier), incident-opus
(claude-opus-5-5 in fast mode), astra (gpt-6-astra,
for exceptional cases only), or a gpt-* model id. <effort> is low, medium,
high, xhigh, or max. Orca fails a terminal create with "Terminal creation timed
out" when its renderer has not answered within a fixed 10 seconds, and the create
may still open the terminal.

With ORCA_LAUNCH_PREPARE set, the script runs that shell command in each worktree
this launch creates, once its checkout is whole and before the worker starts, for
up to ORCA_LAUNCH_PREPARE_SECONDS. It is where a drive runs the repo's code
generators, so a worker's first build finds the generated code in place. A
relaunch into an existing worktree runs nothing. The command's output goes to
<lane>.prepare.log in the receipt directory. A command that fails or passes the
ceiling never fails the launch: the worker reads a copy of the brief that ends
with the command, the log's path, and the instruction to run it before building.

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
handle its own terminal create returned, and removes the worktree it made. A
worktree its own orca worktree create made, whether or not that create answered,
goes with orca worktree rm --force once Orca lists it, and a directory left at the
path with no checkout, which a timed-out git add leaves behind, with rm -rf. A
worktree its own git worktree add made goes to ccx vcs worktree rm --path --force,
the throttled deletion queue, and the branch that add created is deleted. Its
failure line ends "; rolled back terminal=... worktree=... branch=..." and
"; rollback left ..." names what it kept: a terminal it adopted from a listing,
which a concurrent launch of the same lane may own, whatever Orca or ccx refused,
a branch git still holds because the queue has not yet released its worktree, and, after any other
worker-start failure, the terminal and worktree a dispatch may own. A worktree
and branch that existed before the launch stay. Every failure line is one line, and an Orca error in it reads
"<code>: <message>".

  ORCA_LAUNCH_RUN            orchestration Run id, default the Orca run of the drive this session belongs to, from drive.py orca-run
  ORCA_LAUNCH_STALL_SECONDS  give up with LAUNCH-FAILED once one wait lasts this long, default 180; each wait prints its reason to stderr every 30 s
  ORCA_LAUNCH_REPO           Orca repo id, default the repo Orca registers at the parent's main checkout, the directory holding its git common dir; its record names the main checkout the worktree is added from
  ORCA_LAUNCH_PARENT         coordinator worktree path, default $PWD
  ORCA_LAUNCH_NO_PARENT      1 creates a top-level worktree, as incident always does, default unset
  ORCA_LAUNCH_PREFIX         worktree name prefix, default none
  ORCA_LAUNCH_ROOT           directory a new worktree goes in, default the parent's directory, or <ccx.worktreeRoot>/<main checkout's name> for a sparse lane whose main checkout's git config sets ccx.worktreeRoot
  ORCA_LAUNCH_BASE           base branch, default the parent checkout's origin/HEAD
  ORCA_LAUNCH_STATE          receipt directory, default $XDG_STATE_HOME/long-running/orca-launch/<run>, $XDG_STATE_HOME defaulting to ~/.local/state
  ORCA_LAUNCH_CLAUDE_ARGS    further claude args from Orca's agent default args, default none
  ORCA_LAUNCH_MCP_CONFIG     space-separated --mcp-config files or JSON strings for a claude worker, default none
  ORCA_LAUNCH_CODEX_MCP      inline TOML table, without spaces or single quotes, for an incident worker's mcp_servers beside datadog and sentry, default {}
  ORCA_LAUNCH_BOOT_SECONDS   ceiling on the poll for a terminal whose create named no handle, on the wait for the terminal's agent to reach its idle prompt, and on the wait for an outcome_unknown worker's first turn, default 180
  ORCA_LAUNCH_WORKTREE_SECONDS  ceiling on the wait for a worktree whose create failed to register, then for its checkout, and on the wait for Orca to list a worktree this launch added or resumes, default 180
  ORCA_LAUNCH_PREPARE        shell command run in each worktree this launch creates, before its worker starts, default none
  ORCA_LAUNCH_PREPARE_SECONDS  ceiling on that command, default 120
  ORCA_LAUNCH_SPARSE         1 adds each new worktree by git worktree add, sparse in a large repo, instead of orca worktree create, default unset
  ORCA_LAUNCH_SPARSE_FILES   tracked-file count above which a worktree ORCA_LAUNCH_SPARSE adds is sparse, default 5000
EOF
  exit 2
}

[ $# -eq 4 ] || usage
LANE=$1 MODEL=$2 EFFORT=$3 BRIEF=$4
PARENT=${ORCA_LAUNCH_PARENT:-$PWD}
NAME=${ORCA_LAUNCH_PREFIX:-}$LANE
BOOT=${ORCA_LAUNCH_BOOT_SECONDS:-180}
WORKTREE_WAIT=${ORCA_LAUNCH_WORKTREE_SECONDS:-180}
PREPARE=${ORCA_LAUNCH_PREPARE:-}
PREPARE_WAIT=${ORCA_LAUNCH_PREPARE_SECONDS:-120}
SPARSE_LANES=${ORCA_LAUNCH_SPARSE:-}
SPARSE_FILES=${ORCA_LAUNCH_SPARSE_FILES:-5000}
POLL=4
COORDINATOR_COMPACT_WINDOW=280000
STALL=${ORCA_LAUNCH_STALL_SECONDS:-180}

ROLLBACK='' CREATED='' MADE='' LOST='' STARTUP='' BRANCHED='' OWNED='' TERMINAL='' UNDONE='' KEPT=''
removed() {
  if [ "$SPARSE_LANES" = 1 ]; then
    (cd "$MAIN" && exec ccx vcs worktree rm --path "$WT" --force) >"$STATE/$LANE.rm.log" 2>&1
  else
    orca worktree rm --worktree "path:$WT" --force --json >"$STATE/$LANE.rm.log" 2>&1
  fi
}
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
    if removed; then
      rm -f "$STATE/$LANE.worktree"
      UNDONE="$UNDONE worktree=$WT"
    else
      KEPT="$KEPT worktree=$WT"
    fi
  elif [ -n "$LOST" ] && [ -d "$WT" ] && ! is_checkout; then
    rm -rf "$WT" && UNDONE="$UNDONE worktree=$WT" || KEPT="$KEPT worktree=$WT"
  fi
  if [ -n "$BRANCHED" ] && git -C "$MAIN" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    if git -C "$MAIN" branch -D "$BRANCH" >/dev/null 2>&1; then
      UNDONE="$UNDONE branch=$BRANCH"
    else
      KEPT="$KEPT branch=$BRANCH"
    fi
  fi
  is_checkout || rm -f "$STATE/$LANE.worktree"
}

fail() {
  rollback
  printf '%s\n' "$LANE failed $(printf '%s' "$*" | tr -s '[:space:]' ' ')${UNDONE:+; rolled back$UNDONE}${KEPT:+; rollback left$KEPT}"
  exit 1
}

WAIT_FOR='' WAIT_START=0 SAID=0
pause() {
  now=$(date +%s)
  if [ "$1" != "$WAIT_FOR" ]; then
    WAIT_FOR=$1 WAIT_START=$now SAID=$now
  fi
  [ $((now - WAIT_START)) -lt "$STALL" ] || fail "LAUNCH-FAILED: $1 for $((now - WAIT_START))s"
  if [ $((now - SAID)) -ge 30 ]; then
    printf '%s waiting %ss: %s\n' "$LANE" $((now - WAIT_START)) "$1" >&2
    SAID=$now
  fi
  sleep "$POLL"
}

orca_error() {
  jq -er 'select(.ok == false) | "\(.error.code): \(.error.message // "no message")"' "$1" 2>/dev/null ||
    cat "$@" | tr -s '[:space:]' ' ' | cut -c1-300
}

RUN=${ORCA_LAUNCH_RUN:-$(python3 "$(dirname "$0")/drive.py" orca-run)} ||
  fail "run: this session's drive binds no Orca run; set ORCA_LAUNCH_RUN or run drive.py start --orca-run <id>"
RECEIPTS=${XDG_STATE_HOME:-$HOME/.local/state}/long-running/orca-launch
STATE=${ORCA_LAUNCH_STATE:-$RECEIPTS/$RUN}
RECEIPT=$STATE/$LANE.json
mkdir -p "$STATE"

AGENT=claude FAST=
case $MODEL in
  codex | sol) AGENT=codex MODEL_ID=gpt-6.1-sol ;;
  astra) AGENT=codex MODEL_ID=gpt-6-astra ;;
  incident) AGENT=incident MODEL_ID=gpt-6.1-sol ;;
  gpt-*) AGENT=codex MODEL_ID=$MODEL ;;
  opus) MODEL_ID=claude-opus-5-5 ;;
  incident-opus) MODEL_ID=claude-opus-5-5 FAST=" --settings '{\"fastMode\":true}'" ;;
  sonnet) MODEL_ID=claude-sonnet-5-5 ;;
  fable) MODEL_ID=claude-fable-5-1 ;;
  claude-*) MODEL_ID=$MODEL ;;
  *) fail "model $MODEL unknown: use opus, sonnet, fable, claude-*, sol, codex, incident, incident-opus, astra, or gpt-*" ;;
esac
case $EFFORT in
  low | medium | high | xhigh | max) ;;
  *) fail "effort $EFFORT unknown: use low, medium, high, xhigh, or max" ;;
esac
[ -r "$BRIEF" ] || fail "brief $BRIEF unreadable"
BRIEF=$(cd "$(dirname "$BRIEF")" && pwd)/$(basename "$BRIEF")
if [ -n "${ORCA_LAUNCH_REPO:-}" ]; then
  REPO=id:$ORCA_LAUNCH_REPO
else
  REPO=$(git -C "$PARENT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) ||
    fail "repo: $PARENT is not a git checkout; set ORCA_LAUNCH_REPO"
  REPO=path:$(dirname "$REPO")
fi
orca repo show --repo "$REPO" --json >"$STATE/$LANE.repo.json" 2>&1 || :
MAIN=$(jq -er '.result.repo.path' "$STATE/$LANE.repo.json" 2>/dev/null) ||
  fail "repo: orca repo show finds no repo at $REPO ($(orca_error "$STATE/$LANE.repo.json")); set ORCA_LAUNCH_REPO, or register the main checkout with orca repo add --path"
ROOT=${ORCA_LAUNCH_ROOT:-}
if [ -z "$ROOT" ] && [ "$SPARSE_LANES" = 1 ] && POOL=$(git -C "$MAIN" config ccx.worktreeRoot); then
  ROOT=$POOL/$(basename "$MAIN")
fi
ROOT=${ROOT:-$(dirname "$PARENT")}
GIT_USER=$(jq -r '.result.repo.gitUsername // empty' "$STATE/$LANE.repo.json")
RUN_ID=$(printf '%s' "${RUN#run_}" | cut -c1-8)
WT=$(cat "$STATE/$LANE.worktree" 2>/dev/null) || WT=

BASE=${ORCA_LAUNCH_BASE:-$(git -C "$PARENT" symbolic-ref --short refs/remotes/origin/HEAD)}
DRIVE=$(python3 "$(dirname "$0")/drive.py" current) || DRIVE=
COORDINATOR_ENV=
[ "$LANE" != root ] || COORDINATOR_ENV=" CLAUDE_CODE_PROMPT_CACHE_TTL=1h CLAUDE_CODE_AUTO_COMPACT_WINDOW=$COORDINATOR_COMPACT_WINDOW"
COMMAND="env CLAUDE_LONG_RUNNING_LANE=$LANE${DRIVE:+ CLAUDE_LONG_RUNNING_DRIVE=$DRIVE}$COORDINATOR_ENV claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools AskUserQuestion,EnterPlanMode,ExitPlanMode --strict-mcp-config${ORCA_LAUNCH_MCP_CONFIG:+ --mcp-config $ORCA_LAUNCH_MCP_CONFIG}${ORCA_LAUNCH_CLAUDE_ARGS:+ $ORCA_LAUNCH_CLAUDE_ARGS} --model $MODEL_ID --effort $EFFORT$FAST"
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
[ "$AGENT" != incident ] || COMMAND="sh -c 'PATH=$BIN:\$PATH exec env CLAUDE_LONG_RUNNING_LANE=$LANE${DRIVE:+ CLAUDE_LONG_RUNNING_DRIVE=$DRIVE} codex --dangerously-bypass-approvals-and-sandbox -c model=$MODEL_ID -c service_tier=fast -c model_reasoning_effort=$EFFORT -c check_for_update_on_startup=false -c mcp_servers=$CODEX_MCP$CODEX_OFF'"
DESK='' SPARSE='' NODE=''
! grep -qE '^ccx:(.* )?role=(desk|watch)([[:space:]]|$)' "$BRIEF" ||
  DESK=" Standing desk: loop until rotation; setup and quiet cycles are not done. Send worker_done only at rotation, naming the handoff doc."
pointer() {
  printf '%s' "Lane $LANE: read $1 in full first and execute it exactly; it outranks Orca's preamble and any leave-uncommitted default, so commit, push, open PRs and post as it says. Worktree $WT, bypass-permissions mode; its Escalate rules hold.$NODE$SPARSE$DESK"
}
LANE_BRIEF=$BRIEF
if [ "$AGENT" != claude ]; then
  LANE_BRIEF=$STATE/$LANE.brief.md
  {
    [ -z "$DRIVE" ] || printf '## First command\n\n%s\n\n' "Codex runs no session hook, so \`cci who\` lists you only once you post. Before anything else, run \`cci post --drive '$DRIVE' --lane '$LANE' --kind state --stamp '$LANE: registered at its first command'\`, so a claim made for you and the lane supervisor's launch check both find your session."
    cat "$BRIEF"
    if [ "$AGENT" = incident ]; then
      printf '\n\n## PR prose\n\n%s\n' "The fix outranks its prose. The moment the candidate is committed, start \`'$BIN/pr-prose.py' --brief '$BRIEF'\` in the background from your worktree, then push the branch and open the PR within 10 minutes of the candidate. Ship with the writer's title, body, and commit message if they are back; otherwise open with a short body you write yourself in plain words: what broke, what the change does, the test that proves it, the risk. Never wait for the writer, a red-baseline build, the root, or an Orca answer. The failing-first CI link and any scope change on a later push go in one PR comment, in your own plain words; the body stays as opened."
    else
      printf '\n\n## PR prose\n\n%s\n' "You run on a model that writes no prose: Claude Opus 5.5 writes every PR title, PR body, and commit message. Once the change is committed, run \`'$BIN/pr-prose.py' --brief '$BRIEF'\` from your worktree, adding \`--notes <file>\` for each findings or evidence file the PR must carry. It prints the paths of the title, body, and commit message it wrote; ship with them verbatim, amending your commit to that message. Never ask the root, another lane, or an Orca question for prose."
    fi
    [ -z "$DRIVE" ] || printf '\n\n## PR record\n\n%s\n' "Codex runs no PR hook, so \`ccx vcs ship\` neither registers your PR in the drive's ledger nor posts its \`opened\` record, and the incident clock and the landing desk read both. Right after each ship that opens a PR, run \`'$BIN/drive.py' record --opened --drive '$DRIVE' --session \"\$CODEX_THREAD_ID\" --lane '$LANE' --cwd \"\$PWD\" --pr <number>=<head>\` from your worktree."
  } >"$LANE_BRIEF.tmp"
  mv "$LANE_BRIEF.tmp" "$LANE_BRIEF"
fi
spec() {
  SPEC=$(pointer "$LANE_BRIEF")
  LINK=$RECEIPTS/$(printf '%s' "$LANE_BRIEF" | shasum | cut -c1-8)
  if [ "${#SPEC}" -le 500 ] || [ "${#LINK}" -ge "${#LANE_BRIEF}" ]; then
    return 0
  fi
  mkdir -p "$RECEIPTS"
  ln -sfn "$LANE_BRIEF" "$LINK"
  SPEC=$(pointer "$LINK")
}

REBIND="post the launch to the drive's orca runner, whose terminal coordinates the Run: desk-launch.py lane $LANE $MODEL $EFFORT $BRIEF; a drive with no runner rebinds from its coordinator's Orca terminal: orca orchestration run-use --id $RUN"
orca orchestration run-current --full --json >"$STATE/$LANE.binding.json" 2>&1 || :
BOUND=$(jq -r '.result.run.id // empty' "$STATE/$LANE.binding.json" 2>/dev/null) || BOUND=
[ "$BOUND" = "$RUN" ] ||
  fail "coordinator binding: terminal ${ORCA_TERMINAL_HANDLE:-unset} coordinates ${BOUND:-no Run}, not $RUN ($(orca_error "$STATE/$LANE.binding.json")); $REBIND"

set -- --parent-worktree "path:$PARENT"
[ "$AGENT" != incident ] && [ "${ORCA_LAUNCH_NO_PARENT:-}" != 1 ] || set -- --no-parent

is_checkout() {
  TOP=$(git -C "$WT" rev-parse --show-toplevel 2>/dev/null) && [ "$TOP" = "$(cd "$WT" && pwd -P)" ]
}
registered() {
  FOUND=$(orca worktree show --worktree "name:$WORKTREE_NAME" --json | jq -er '.result.worktree.path') || return 1
  WT=$FOUND
}
checked_out() {
  is_checkout && git -C "$WT" diff-index --cached --quiet HEAD --
}
listed_worktree() {
  waited=0
  until orca worktree show --worktree "path:$WT" --json >"$STATE/$LANE.worktree.json" 2>&1; do
    [ "$waited" -lt "$WORKTREE_WAIT" ] ||
      fail "worktree register: Orca does not list $WT after ${WORKTREE_WAIT}s ($(orca_error "$STATE/$LANE.worktree.json"))"
    pause "Orca does not list worktree $WT yet"
    waited=$((waited + POLL))
  done
}
fresh() {
  n=0
  until
    n=$((n + 1))
    WORKTREE_NAME=$(printf '%s-%s-%s' "$NAME" "$RUN_ID" "$n" | tr / -)
    BRANCH=${GIT_USER:+$GIT_USER/}$WORKTREE_NAME WT=$ROOT/$WORKTREE_NAME
    ! grep -qxF "$WORKTREE_NAME" "$STATE/$LANE.names" 2>/dev/null && [ ! -e "$WT" ] &&
      ! git -C "$MAIN" show-ref --verify --quiet "refs/heads/$BRANCH"
  do :; done
  printf '%s\n' "$WORKTREE_NAME" >>"$STATE/$LANE.names"
}
added() {
  remote=${BASE%%/*}
  ! git -C "$MAIN" remote get-url "$remote" >/dev/null 2>&1 || git -C "$MAIN" fetch --quiet "$remote" "${BASE#*/}" || :
  BRANCHED=1
  git -C "$MAIN" worktree add --no-checkout --no-track -b "$BRANCH" "$WT" "$BASE"
}
shared() {
  jq -r '.result.repo.symlinkPaths[]?' "$STATE/$LANE.repo.json" | while IFS= read -r path; do
    [ -e "$MAIN/$path" ] && [ ! -e "$WT/$path" ] || continue
    mkdir -p "$(dirname "$WT/$path")" && ln -s "$MAIN/$path" "$WT/$path"
  done
  [ -f "$MAIN/.worktreeinclude" ] || return 0
  sed -E -e 's/^[[:space:]]+|[[:space:]]+$//g' -e 's|^\./||' -e 's|/+$||' "$MAIN/.worktreeinclude" | grep -Ev '^(#|$)' | while IFS= read -r path; do
    [ -e "$MAIN/$path" ] && [ ! -e "$WT/$path" ] || continue
    git -C "$MAIN" check-ignore -q -- "$path" || continue
    mkdir -p "$(dirname "$WT/$path")" && cp -R "$MAIN/$path" "$WT/$path"
  done
}
checked() {
  [ -z "$CONE" ] || printf '%s\n' "$CONE" | git -C "$WT" sparse-checkout set --cone --stdin || return 1
  git -C "$WT" reset --quiet --hard && shared
}

ROLLBACK=1
if [ -n "$WT" ] && is_checkout; then
  listed_worktree
elif [ "$SPARSE_LANES" = 1 ]; then
  fresh
  CREATED=1
  added >"$STATE/$LANE.worktree.log" 2>&1 || fail "worktree add $WT: $(tail -n 3 "$STATE/$LANE.worktree.log")"
  MADE=1
  CONE=$(printf '%s\n' .agents .claude tools infra/ci/hooks/lib; sed -En 's/^ccx:(.* )?paths=([^[:space:]]+).*/\2/p' "$BRIEF" | tr , '\n')
  FILES=$(git -C "$WT" ls-tree -r --name-only HEAD | wc -l)
  [ $((FILES)) -gt "$SPARSE_FILES" ] || CONE=''
  checked >>"$STATE/$LANE.worktree.log" 2>&1 || fail "worktree checkout $WT: $(tail -n 3 "$STATE/$LANE.worktree.log")"
  [ -z "$CONE" ] || printf '%s sparse: %s; widen with git -C %s sparse-checkout add <dir>\n' "$LANE" "$(printf '%s' "$CONE" | tr '\n' ' ')" "$WT" >&2
  listed_worktree
  orca worktree set --worktree "path:$WT" --display-name "$WORKTREE_NAME" "$@" --json >"$STATE/$LANE.worktree.json" 2>&1 ||
    fail "worktree set $WT: $(orca_error "$STATE/$LANE.worktree.json")"
else
  fresh
  CREATED=1 STARTUP=1
  attempt=0
  while ! registered; do
    attempt=$((attempt + 1))
    [ "$attempt" -le 4 ] || fail "worktree create: $(orca_error "$STATE/$LANE.worktree.json")"
    if orca worktree create --name "$WORKTREE_NAME" --repo "$REPO" --base-branch "$BASE" \
      "$@" --setup run --json >"$STATE/$LANE.worktree.json" 2>&1 &&
      FOUND=$(jq -er '.result.worktree.path' "$STATE/$LANE.worktree.json"); then
      MADE=1 WT=$FOUND
      break
    fi
    jq -e '.error.code == "worktree_exists"' "$STATE/$LANE.worktree.json" >/dev/null 2>&1 || LOST=1
    waited=0
    until registered || [ "$waited" -ge "$WORKTREE_WAIT" ]; do
      pause "Orca has not registered worktree $WORKTREE_NAME"
      waited=$((waited + POLL))
    done
  done
  waited=0
  until [ -n "$MADE" ] || checked_out; do
    [ "$waited" -lt "$WORKTREE_WAIT" ] ||
      fail "worktree checkout: $WT registered, but its index still differs from HEAD after ${WORKTREE_WAIT}s"
    pause "worktree $WT is registered, and its index still differs from HEAD"
    waited=$((waited + POLL))
  done
fi
printf '%s\n' "$WT" >"$STATE/$LANE.worktree"
prepared() {
  (cd "$WT" && exec perl -e 'alarm shift; exec @ARGV' "$PREPARE_WAIT" sh -c "$PREPARE") >"$STATE/$LANE.prepare.log" 2>&1
}
if [ -n "$CREATED" ] && [ -n "$PREPARE" ] && ! prepared; then
  [ "$LANE_BRIEF" != "$BRIEF" ] || cp "$BRIEF" "$STATE/$LANE.brief.md"
  LANE_BRIEF=$STATE/$LANE.brief.md
  printf '\n\n## Worktree preparation\n\n%s\n' "The launch ran \`$PREPARE\` in this worktree and it failed; its output is in $STATE/$LANE.prepare.log. Run it yourself before you build." >>"$LANE_BRIEF"
fi
[ ! -f "$WT/tools/hooks/warm-lane.sh" ] ||
  NODE=" node_modules is a shared symlink farm; never run yarn install, ask warm-lane to rebuild for a changed lockfile."
[ "$(git -C "$WT" config --get --bool core.sparseCheckout)" != true ] ||
  SPARSE=" This lane is sparse: widen with \`git sparse-checkout add <dir>\` before reading outside it."
spec

listed() {
  listing=0
  until orca terminal list --worktree "path:$WT" --json >"$STATE/$LANE.terminals.json" 2>&1 &&
    LISTED=$(jq -ce '[.result.terminals[].handle]' "$STATE/$LANE.terminals.json"); do
    listing=$((listing + 1))
    [ "$listing" -lt 3 ] || fail "terminal list: $(orca_error "$STATE/$LANE.terminals.json")"
    pause "orca terminal list fails for $WT"
  done
}

adopt() {
  waited=0
  while [ "$waited" -lt "$BOOT" ]; do
    pause "no terminal has appeared in $WT after orca terminal create"
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
[ "$AGENT" = codex ] && [ -z "$STARTUP" ] || { listed && BEFORE=$LISTED; }
[ -z "$STARTUP" ] || close_startup_shells
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
    orca orchestration task-list --run "$RUN" --status "$status" --brief --full --json |
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
    --timeout-ms "$TIMEOUT" --full --json >"$RECEIPT.new" 2>"$STATE/$LANE.worker.err" || STARTED=$?
  [ "$STARTED" != 0 ] && [ "$(refusal)" = agent_unconfigured ] && [ "$waited" -lt "$BOOT" ] || break
  pause "worker-start refuses terminal $TERMINAL as agent_unconfigured"
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
  FENCED=
  [ "$REFUSED" != consumer_fenced ] ||
    FENCED="; terminal ${ORCA_TERMINAL_HANDLE:-unset} is not the Run's coordinator: $REBIND"
  fail "worker-start${TERMINAL:+ terminal=$TERMINAL}: $(orca_error "$RECEIPT.new" "$STATE/$LANE.worker.err")$FENCED"
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
  until orca orchestration worker-show --dispatch "$1" --full --json |
    jq -e '.result | .projection.stage.activity == "working" or (.worker.state | IN("ready", "succeeded"))' >/dev/null 2>&1; do
    [ "$waited" -lt "$BOOT" ] || return 1
    pause "dispatch $1 shows no turn started"
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
  pause "terminal $TERMINAL does not show bypass permissions on yet"
done

[ -z "$DRIVE" ] || python3 "$(dirname "$0")/drive.py" spawned --drive "$DRIVE" --lane "$LANE" --brief "$BRIEF" --model "$MODEL" --effort "$EFFORT" --placement orca ${LAUNCH_ROLE:+--role "$LAUNCH_ROLE"} --by "${CLAUDE_LONG_RUNNING_LANE:-root}" >&2 ||
  echo "$LANE: the SPAWNED record did not post, so the lane supervisor will not keep this lane alive" >&2
echo "$LANE ready task=$(jq -r '.result.taskId' "$RECEIPT") dispatch=$(jq -r '.result.dispatchId' "$RECEIPT") terminal=$TERMINAL worktree=$WT"
