#!/usr/bin/env bash
# Polls one pull request over the REST API and prints one line per new event,
# the watch behind open-pr's background watcher and pr-loop's Monitor.
#
#   pr-poll.sh <owner/repo> <pr-number> <state-file>
#
#   CHECK    <name> <bucket> <link>
#   REVIEW   <author> <state> <id>
#   COMMENT  <author> <id> <first-80-chars-of-body>
#   QUEUED   <actor> <label|comment-id>
#   UNQUEUED <actor>
#   GREEN    awaiting-review
#   RATELIMIT waiting <n>s for GitHub to reset
#   DONE     ready-to-merge | merged | queue-merged | closed | checks-failed |
#            conflicted | deadline-still-open | window-elapsed
#   DONE     ready-to-merge downstack #<n>
#   DONE     evicted <conflicts|failed-ci|downstack|head-moved|other|unknown> <detail>
#
# Exits 0 after DONE. QUEUED means the PR entered the merge queue, by the queue
# label or by a merge-activity enqueue entry; UNQUEUED means a human took it
# out, by removing the label or by dequeuing it in the queue's UI. Neither is
# terminal.
#
# conflicted: mergeable_state dirty, or mergeable false on two reads with no
# true between them. A null mergeable (GitHub still recomputing after the base
# or head moved) is no read at all. Fires once per head.
#
# evicted: the queue's merge-activity comment logs a drop as its latest queue
# entry, whatever the label and mergeable state read, or a bot takes the label
# off a stint no report has ended yet. Fires once per stint; re-enqueueing, by
# label or in the UI, re-arms it. A drop logged before the head was committed
# dropped an earlier head and evicts nothing.
#
# ready-to-merge needs every check passed, mergeable true, queue state read,
# the PR neither queued nor evicted and waiting to re-enter, and the review
# requirement met: no reviewer's latest review requests changes and
# mergeable_state is not blocked. The blocked state means a required approval
# is still missing: it is REST's stand-in for GraphQL reviewDecision, which
# this REST-only script cannot read. A queued PR is watched until it lands or
# the queue drops it.
# A stacked PR is one whose base is the head of open PR #<n> rather than the
# default branch. Graphite's mergeability check stays in progress on it until
# #<n> merges; it doesn't hold the verdict, which then reads ready-to-merge
# downstack #<n>: landing needs the stack enqueued together.
# Green but not yet approved prints GREEN awaiting-review once per head and
# keeps watching. An eviction holds only its head: pushing a new head clears
# it.
#
# One poller per state file: a second concurrent run exits 3 without polling.
#
# With cci on PATH, each round claims `watches pr:<n>` for the session, so
# `cci who --pr <n>` finds the watcher, and every DONE except window-elapsed and
# evicted drops the claim: those two re-arm the same watch.
#
# window-elapsed: the harness kills a Monitor after 30 minutes and announces it
# with one expiry notice a background agent can miss, so the script ends its own
# round after PR_POLL_WINDOW seconds (25 minutes) and the watcher re-arms it on
# the same state file. The deadline counts from the state file's started_at, so
# it spans windows; started_at restarts when the head moves and when a run
# follows one that ended on any other DONE.
#
# A fresh state file watches from now on: label events before its start and the
# merge-activity entries already posted are history, except that the latest
# posted queue entry decides whether the watch starts queued. Pre-seed
# .watermarks to replay them.
set -euo pipefail

STATE_SCHEMA=4
EMPTY_PASSES_BEFORE_GREEN=3
MERGEABLE_FALSE_READS=2
INTERVAL_MIN=120
INTERVAL_FLOOR_PER_PR=10
RATE_LIMIT_WAIT=60
QUEUE_LABEL="${PR_POLL_QUEUE_LABEL:-merge}"
QUEUE_PROVIDER="${PR_POLL_QUEUE_PROVIDER:-}"
DEADLINE="${PR_POLL_DEADLINE:-14400}"
[[ $DEADLINE =~ ^[0-9]+$ ]] || DEADLINE=14400
WINDOW="${PR_POLL_WINDOW:-1500}"
[[ $WINDOW =~ ^[0-9]+$ ]] || WINDOW=1500
STARTED=$(date +%s)

CHECK_RUNS='.check_runs[] | {
  id,
  name,
  bucket: (
    if .status != "completed" then "pending"
    elif .conclusion == "success" then "pass"
    elif .conclusion == "neutral" or .conclusion == "skipped" then "skipping"
    elif .conclusion == "cancelled" then "cancel"
    elif .conclusion | IN("failure", "timed_out", "action_required", "startup_failure") then "fail"
    else "pending" end),
  link: (.details_url // .html_url // ""),
  graphite_mergeability: (.app.slug == "graphite-app" and .name == "Graphite / mergeability_check")
}'

LATEST_RUNS='. as $runs | .[] | . as $r | select(.id == ([$runs[] | select(.name == $r.name) | .id] | max))'

STATUSES='.statuses[] | {
  name: .context,
  bucket: ({ success: "pass", pending: "pending" }[.state] // "fail"),
  link: (.target_url // "")
}'

LABEL_EVENTS='.[] | {
  id, event, created_at,
  actor: { login: (.actor.login // "-"), type: (.actor.type // "") },
  label: (.label.name // "")
}'

QUEUE_ENTRIES='(.body // "") | split("\n") | map(select(test("^ *[*-] +")) | gsub("^ *[*-] +"; ""))'

QUEUE_BULLETS='
  def logged_at($created):
    capture("^(?<mon>[A-Z][a-z]{2}) (?<day>[0-9]{1,2}), (?<hour>[0-9]{1,2}):(?<min>[0-9]{2}) (?<half>[AP])M UTC: ") as $t
    | (["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"] | index($t.mon) + 1) as $month
    | (($created[0:4] | tonumber) + (if $month < ($created[5:7] | tonumber) then 1 else 0 end)) as $year
    | (($t.hour | tonumber) % 12 + (if $t.half == "P" then 12 else 0 end)) as $hour
    | "\($year)-\("0\($month)"[-2:])-\("0\($t.day)"[-2:])T\("0\($hour)"[-2:]):\($t.min)";
  (.at // "") as $created
  | '"$QUEUE_ENTRIES"'
  | .[$seen:][]
  | (gsub("\\[(?<t>[^]]*)\\]\\([^)]*\\)"; "\(.t)") | gsub("[*`]"; "") | gsub("[\r\n\t]+"; " ")) as $line
  | (($line | logged_at($created)) // null) as $at
  | ($line | sub("^[A-Z][a-z]{2} [0-9]{1,2}, [0-9]{1,2}:[0-9]{2} [AP]M UTC: "; "")) as $text
  | if ($text | test("added this pull request to the .*merge queue"; "i"))
      then { kind: "queued", actor: ($text | capture("^(?<who>[^ ]+) +added this pull request").who // null) }
    elif ($text | test("^[^ ]+ +removed this pull request from the .*queue"; "i"))
      then { kind: "unqueued", actor: ($text | capture("^(?<who>[^ ]+) ").who) }
    elif ($text | test("downstack (failures? on (PR )?|PR )#[0-9]+"; "i"))
      then { kind: "drop", class: "downstack",
             detail: ("#" + ($text | capture("downstack (failures? on (PR )?|PR )#(?<n>[0-9]+)"; "i").n)) }
    elif ($text | test("merge conflict|try rebasing"; "i"))
      then { kind: "drop", class: "conflicts", detail: $text[0:80] }
    elif ($text | test("ci (failed|failure)|failed ci|failing (required )?check|failed (required )?check|failed for an unknown reason"; "i"))
      then { kind: "drop", class: "failed-ci",
             detail: ($text | capture("failed ci \\((?<check>[^)]+)\\)"; "i").check // $text[0:80]) }
    elif ($text | test("couldn.t merge this PR|can ?not be added to the|removed this pull request|removed .* from the .*queue|disabled \"merge when ready\""; "i"))
      then { kind: "drop", class: "other", detail: $text[0:80] }
    else empty end
  | .at = $at'

QUEUE_EVENTS='
  def bot: .actor.type == "Bot" or (.actor.login | endswith("[bot]"));
  sort_by(.id) as $events
  | ($events | map(select((.event == "labeled" or .event == "unlabeled") and .label == $label))) as $flips
  | ($flips | map(select(.event == "labeled")) | last) as $labeled
  | ($flips | last) as $last
  | {
      labeled_by: ($labeled.actor.login // "-"),
      stint: (if $labeled == null then "" else ($labeled.id | tostring) end),
      last: (
        if $last != null and [$last.created_at, $last.id] > [$since.at, $since.id]
        then { id: ($last.id | tostring), event: $last.event, actor: $last.actor.login, bot: ($last | bot) }
        else null end),
      pushed_in_queue: ($labeled != null and any($events[]; .event == "head_ref_force_pushed" and .id > $labeled.id))
    }'

usage() {
  cat >&2 <<'EOF'
usage: pr-poll.sh <owner/repo> <pr-number> <state-file>

  Polls every PR_POLL_INTERVAL seconds over REST, through ccx's GitHub
  read gateway (ccx vcs gh), and prints one CHECK /
  REVIEW / COMMENT / QUEUED / UNQUEUED / GREEN line per new event, then DONE
  and exit 0. Exits 3 when another poller holds the state file.

  PR_POLL_INTERVAL defaults to 120 and is floored there. Each pass spends
  ~7 REST calls, one more on a stacked PR, on a new head, and on a green
  pass, and every watcher on a stack spends them against one shared hourly
  budget, so the interval is also floored at 10 seconds per concurrently
  watched PR: set PR_POLL_STACK to the number of PRs being watched at once
  (default 1). PR_POLL_QUEUE_LABEL names the merge-queue label (default
  merge).

  Every read is conditional: the ETag of each response is kept beside the
  state file and sent back as If-None-Match, and GitHub does not count a 304
  against the hourly budget, so a pass over an idle PR spends almost none.
  On a GitHub rate limit the poller prints one RATELIMIT line, sleeps until
  the core limit resets (a minute for a secondary limit), and resumes.

  A green check list is ready only once every GitHub Actions check suite on
  the head has completed: a workflow run registers its suite before its
  jobs, so a partial list can read green while a run is still queued.
  Other apps' suites are not consulted, since an installed app that never
  runs leaves its suite queued with no check runs.

  A round ends with DONE window-elapsed after PR_POLL_WINDOW seconds (1500,
  under the harness's 30-minute Monitor cap); re-run on the same state file
  to continue; the rerun waits out the interval before its first pass. Set
  PR_POLL_WINDOW=40 for a foreground Bash call capped at 60 seconds.
  DONE deadline-still-open ends the whole watch PR_POLL_DEADLINE
  seconds (14400) after the state file's started_at, which restarts on
  a new head and on a run after any DONE but window-elapsed or evicted.
EOF
  exit 2
}

for dep in ccx jq; do
  command -v "$dep" >/dev/null 2>&1 || {
    echo "pr-poll.sh: $dep is required but not installed" >&2
    exit 1
  }
done

wait_out_rate_limit() {
  local core remaining="" reset="" now until
  core=$(CCX_CALLER=pr-poll ccx vcs gh -- api rate_limit --jq '.resources.core | "\(.remaining) \(.reset)"' 2>/dev/null) || core=""
  read -r remaining reset <<<"$core" || true
  now=$(date +%s)
  until=$((now + RATE_LIMIT_WAIT))
  if [ "$remaining" = 0 ] && [[ $reset =~ ^[0-9]+$ ]] && [ "$reset" -gt "$now" ]; then
    until=$((reset + 2))
  fi
  printf 'RATELIMIT waiting %ss for GitHub to reset\n' "$((until - now))" >&8
  sleep "$((until - now))"
}

gh() {
  local out err rc
  out=$(mktemp)
  err=$(mktemp)
  while :; do
    CCX_CALLER=pr-poll ccx vcs gh -- "$@" >"$out" 2>"$err" && rc=0 || rc=$?
    if [ "$rc" -ne 0 ] && grep -qiE 'rate limit exceeded|secondary rate limit' "$out" "$err"; then
      wait_out_rate_limit
      continue
    fi
    cat "$out"
    cat "$err" >&2
    rm -f "$out" "$err"
    return "$rc"
  done
}

get() {
  local url=$1 filter=${2:-.} key etag out status next
  local -a cond
  out=$(mktemp)
  while [ -n "$url" ]; do
    key=$(printf '%s' "$url" | cksum | cut -d' ' -f1)
    etag=$(cat "$ETAG_DIR/$key.etag" 2>/dev/null || true)
    cond=()
    [ -z "$etag" ] || cond=(-H "If-None-Match: $etag")
    gh api "$url" -i ${cond[@]+"${cond[@]}"} >"$out" 2>/dev/null || true
    status=$(awk 'NR == 1 { print $2 }' "$out")
    case $status in
    200)
      awk 'seen { print } /^\r?$/ { seen = 1 }' "$out" >"$ETAG_DIR/$key.body"
      etag=$(awk '/^\r?$/ { exit } tolower($1) == "etag:" { print $2; exit }' "$out" | tr -d '\r')
      next=$(awk '/^\r?$/ { exit } tolower($1) == "link:" { print; exit }' "$out" |
        sed -n 's/.*<\([^>]*\)>; rel="next".*/\1/p')
      printf '%s' "$next" >"$ETAG_DIR/$key.next"
      if [ -n "$etag" ]; then printf '%s' "$etag" >"$ETAG_DIR/$key.etag"; else rm -f "$ETAG_DIR/$key.etag"; fi
      ;;
    304) next=$(cat "$ETAG_DIR/$key.next") ;;
    *)
      rm -f "$out"
      return 1
      ;;
    esac
    jq -rc "$filter" "$ETAG_DIR/$key.body" || {
      rm -f "$out"
      return 1
    }
    url=${next#https://api.github.com/}
  done
  rm -f "$out"
}

[ $# -eq 3 ] || usage
REPO=$1
PR=$2
STATE_FILE=$3

[[ $REPO =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || usage
[[ $PR =~ ^[0-9]+$ ]] || usage

if [ -z "$QUEUE_PROVIDER" ]; then
  origin=$(git remote get-url origin 2>/dev/null || true)
  case "$origin" in
  "https://github.com/$REPO"|"https://github.com/$REPO.git"|"git@github.com:$REPO"|"git@github.com:$REPO.git"|"ssh://git@github.com/$REPO"|"ssh://git@github.com/$REPO.git")
    QUEUE_PROVIDER=$(git config --get ccx.queue 2>/dev/null || true)
    if [ -z "$QUEUE_PROVIDER" ] && [ "$(git config --get ccx.stack 2>/dev/null || true)" = github ]; then
      QUEUE_PROVIDER=github
    fi
    ;;
  esac
fi

STACK="${PR_POLL_STACK:-1}"
[[ $STACK =~ ^[1-9][0-9]*$ ]] || STACK=1
FLOOR=$((INTERVAL_FLOOR_PER_PR * STACK))

INTERVAL="${PR_POLL_INTERVAL:-$INTERVAL_MIN}"
[[ $INTERVAL =~ ^[0-9]+$ ]] || INTERVAL=$INTERVAL_MIN
[ "$INTERVAL" -ge "$INTERVAL_MIN" ] || INTERVAL=$INTERVAL_MIN
[ "$INTERVAL" -ge "$FLOOR" ] || INTERVAL=$FLOOR

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkdir -p "$(dirname "$STATE_FILE")"

ETAG_DIR="$STATE_FILE.etags"

exec 8>&1

exec 9>>"$STATE_FILE.lock"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 && locked=0 || locked=$?
else
  perl -MFcntl=:flock -e 'open(my $fd, ">>&=", 9) or die "pr-poll.sh: fd 9: $!\n"; flock($fd, LOCK_EX | LOCK_NB) or exit 1' &&
    locked=0 || locked=$?
fi
case $locked in
0) ;;
1)
  echo "pr-poll.sh: another poller holds $STATE_FILE; run one poller per state file" >&2
  exit 3
  ;;
*) exit 1 ;;
esac

rm -rf "$ETAG_DIR"
mkdir -p "$ETAG_DIR"

STATE='{}'
empty_passes=0
FRESH=1
if [ -f "$STATE_FILE" ] && jq -e '.watermarks' "$STATE_FILE" >/dev/null 2>&1; then FRESH=0; fi

normalize() {
  jq -c --argjson schema "$STATE_SCHEMA" --argjson pr "$PR" --arg repo "$REPO" --arg now "$NOW" \
    --argjson started "$STARTED" --arg provider "${QUEUE_PROVIDER:-graphite}" '
    { head_at_last_pass: null, head_committed_at: null, checks_seen: {}, merge_activity: {}, merge_state_seen: null,
      mergeable_false_reads: 0, conflicted_head: null, awaiting_review_head: null,
      queue: { provider: "graphite", queued: null, head: null, evicted: null, resolved_stint: null },
      attempts: {}, applied: [], escalated: [], watcher: null } * .
    | if .queue.provider != $provider then
        .queue = { provider: $provider, queued: null, head: null, evicted: null, resolved_stint: null }
        | .merge_activity = {}
      else . end
    | .schema = $schema | .pr = $pr | .repo = $repo
    | .started_at = (.started_at // $started)
    | .watermarks = ({ comments: $now, reviews: $now, events: { at: $now, id: 0 } } * (.watermarks // {}))
  ' <<<"$STATE"
}

# Re-read every pass: the watcher agent owns .attempts, .applied and .escalated
# and writes them between our passes, so we merge into whatever is on disk now
# instead of overwriting the file from a stale in-memory copy. A read that fails
# (a torn write, say) keeps the previous object rather than resetting watermarks.
load_state() {
  local raw
  if [ -f "$STATE_FILE" ] && raw=$(jq -c . "$STATE_FILE" 2>/dev/null); then
    STATE=$raw
  fi
  STATE=$(normalize)
}

write_state() {
  local tmp="$STATE_FILE.tmp.$$"
  printf '%s\n' "$STATE" >"$tmp"
  mv -f "$tmp" "$STATE_FILE"
}

state() {
  jq -r "$1" <<<"$STATE"
}

emit() {
  [ -n "$1" ] || return 0
  printf '%s\n' "$1"
}

cci_watch() {
  command -v cci >/dev/null 2>&1 || return 0
  cci "$1" --pr "$PR" --rel watches >/dev/null 9>&- || true
}

finish() {
  case $1 in
  window-elapsed | evicted*) ;;
  *)
    cci_watch unclaim
    load_state
    STATE=$(jq -c --arg v "$1" '.finished = $v' <<<"$STATE")
    write_state
    ;;
  esac
  printf 'DONE %s\n' "$1"
  exit 0
}

squash_on_base() {
  gh api "repos/$REPO/commits?sha=$1&per_page=100" \
    --jq ".[] | select((.commit.message | split(\"\\n\")[0]) | endswith(\"(#$PR)\")) | .sha" \
    2>/dev/null | head -1
}

closed_verdict() {
  local base=$1 comments rc
  if [ -n "$(squash_on_base "$base" || true)" ]; then printf 'queue-merged\n'; return 0; fi

  comments=$(gh api "repos/$REPO/issues/$PR/comments" --paginate \
    --jq '.[] | select((.body // "") | test("Merged by the \\[?Graphite merge queue"; "i")) | .id' \
    2>/dev/null) && rc=0 || rc=$?
  if [ -n "$comments" ]; then printf 'queue-merged\n'; return 0; fi

  [ "$rc" -eq 0 ] || return 0
  printf 'closed\n'
}

eviction_reason() {
  local merge_state=$1 mergeable=$2 head=$3 checks=$4 queue=$5 actor=$6 downstack=$7 failing
  if [ "$merge_state" = dirty ] || [ "$mergeable" = false ]; then
    printf 'conflicts mergeable_state=%s\n' "$merge_state"
    return
  fi
  if [ -n "$downstack" ]; then
    printf 'downstack #%s\n' "$downstack"
    return
  fi
  if [ "$(jq -r '.pushed_in_queue' <<<"$queue")" = true ] ||
    { [ "$(state '.queue.head // ""')" != "" ] && [ "$(state '.queue.head')" != "$head" ]; }; then
    printf 'head-moved %s\n' "${head:0:12}"
    return
  fi
  failing=$(jq -r '[.[] | select(.bucket == "fail") | .name] | first // empty' <<<"$checks")
  if [ -n "$failing" ]; then
    printf 'failed-ci %s\n' "${failing//[$'\r\n\t']/ }"
    return
  fi
  printf 'unknown label removed by %s\n' "$actor"
}

actions_completed() {
  local open
  open=$(get "repos/$REPO/commits/$1/check-suites?per_page=100" \
    '[.check_suites[] | select(.app.slug == "github-actions" and .status != "completed")] | length') || return 1
  [ "$(jq -s 'add // 0' <<<"$open")" = 0 ]
}

green() {
  local head=$1 merge_state=$2 reviews_ok=$3 changes_requested=$4 downstack=$5
  [ "$reviews_ok" = 1 ] || return 0
  actions_completed "$head" || return 0
  if [ "$merge_state" != blocked ] && [ "$changes_requested" = false ]; then
    finish "ready-to-merge${downstack:+ downstack #$downstack}"
  fi
  [ "$(state '.awaiting_review_head // ""')" != "$head" ] || return 0
  emit "GREEN awaiting-review"
  STATE=$(jq -c --arg h "$head" '.awaiting_review_head = $h' <<<"$STATE")
  write_state
}

native_queue() {
  [ "$QUEUE_PROVIDER" = trunk ] || [ "$QUEUE_PROVIDER" = github ]
}

poll() {
  local view head prev base default_branch stacked_on="" downstack="" pr_state merged mergeable merge_state present
  local checks checks_ok=1 events comments items reviews_ok=1 changes_requested seen wm_c wm_r next_c next_r queue
  local activity act_id act_author act_seen act_n bullets latest latest_kind requeued history reads
  local last_event last_bot last_actor stint committed
  local evicted="" conflicted=0 settled n_checks verdict closed_as authoritative="" authoritative_queue=""

  view=$(get "repos/$REPO/pulls/$PR" \
    '{state, merged, head: .head.sha, base: .base.ref, default_branch: .base.repo.default_branch, mergeable, mergeable_state, labels: [.labels[].name]}') || return 0
  jq -e . >/dev/null 2>&1 <<<"$view" || return 0
  head=$(jq -r '.head // ""' <<<"$view")
  [ -n "$head" ] || return 0
  base=$(jq -r '.base // ""' <<<"$view")
  default_branch=$(jq -r '.default_branch' <<<"$view")
  pr_state=$(jq -r '.state // ""' <<<"$view")
  merged=$(jq -r '.merged // false' <<<"$view")
  mergeable=$(jq -r '.mergeable' <<<"$view")
  merge_state=$(jq -r '.mergeable_state // ""' <<<"$view")
  present=$(jq -r --arg l "$QUEUE_LABEL" 'any(.labels[]; . == $l)' <<<"$view")

  checks=$(
    {
      get "repos/$REPO/commits/$head/check-runs?per_page=100" "$CHECK_RUNS" | jq -cs "$LATEST_RUNS" &&
        get "repos/$REPO/commits/$head/status?per_page=100" "$STATUSES"
    } 2>/dev/null | jq -cs .
  ) || checks_ok=0
  [ "$checks_ok" = 1 ] || checks='[]'

  if native_queue; then
    if ! authoritative=$(ccx vcs pr status "$PR" --json -R "$REPO" | jq -ce --argjson n "$PR" '.[] | select(.number == $n)'); then
      authoritative='{"queue":"unknown","verdict":"unread"}'
    fi
    if [ "$(jq -r '.head // ""' <<<"$authoritative")" != "$head" ] || [ "$(jq -r '.stale == null' <<<"$authoritative")" != true ]; then
      authoritative='{"queue":"unknown","verdict":"unread"}'
    fi
    authoritative_queue=$(jq -r '.queue' <<<"$authoritative")
    case "$authoritative_queue" in
    queued|landed|evicted|"not queued") ;;
    *) authoritative_queue=unknown; emit "QUEUE unknown" ;;
    esac
    checks=$(jq -c 'map(select(.graphite_mergeability != true))' <<<"$checks")
    downstack=$(jq -r '.downstack // empty' <<<"$authoritative")
  fi

  if [ "$base" != "$default_branch" ]; then
    stacked_on=$(get "repos/$REPO/pulls?state=open&head=$(jq -rn --arg h "${REPO%%/*}:$base" '$h | @uri')" \
      '.[0].number // empty') || return 0
  fi
  if [ -n "$stacked_on" ] && jq -e 'any(.[]; .graphite_mergeability == true and .bucket == "pending")' <<<"$checks" >/dev/null; then
    downstack=$stacked_on
  fi

  if native_queue; then
    events='[]'
  else
    events=$(get "repos/$REPO/issues/$PR/events?per_page=100" "$LABEL_EVENTS" | jq -cs .) || return 0
  fi

  load_state

  wm_c=$(state '.watermarks.comments')
  comments=$(get "repos/$REPO/issues/$PR/comments?per_page=100&since=$wm_c" \
    '.[] | { id, author: .user.login, at: .created_at, body }') || return 0
  comments+=$'\n'$(get "repos/$REPO/pulls/$PR/comments?per_page=100&since=$wm_c" \
    '.[] | { id, author: .user.login, at: .created_at, body }' || true)

  queue=$(jq -c --argjson since "$(jq -c '.watermarks.events' <<<"$STATE")" --arg label "$QUEUE_LABEL" \
    "$QUEUE_EVENTS" <<<"$events")
  last_event=$(jq -r '.last.event // ""' <<<"$queue")
  last_bot=$(jq -r '.last.bot // false' <<<"$queue")
  last_actor=$(jq -r '.last.actor // "-"' <<<"$queue")
  stint=$(jq -r '.stint' <<<"$queue")

  if ! native_queue && { [ "$FRESH" = 1 ] || [ "$(state '.queue.queued == null')" = true ]; }; then
    if history=$(get "repos/$REPO/issues/$PR/comments?per_page=100" \
      '.[] | select((.body // "") | test("Merge activity")) | { id, at: .created_at, body }' |
      jq -cs .); then
      if [ "$FRESH" = 1 ]; then
        STATE=$(jq -c --argjson h "$history" \
          ".merge_activity = (\$h | map({ key: (.id | tostring), value: ($QUEUE_ENTRIES | length) }) | from_entries) * .merge_activity" \
          <<<"$STATE")
        FRESH=0
      fi
      latest=$(jq -c --argjson seen 0 "sort_by(.at) | last // empty | [$QUEUE_BULLETS] | last // empty" <<<"$history")
      STATE=$(jq -c --argjson l "${latest:-null}" --arg h "$head" --arg s "$stint" '
        .queue.queued = ($l.kind == "queued")
        | if $l.kind == "queued" then .queue.head = $h else . end
        | if $l.kind == "unqueued" or $l.kind == "drop" then .queue.resolved_stint = $s else . end' <<<"$STATE")
    fi
  fi

  prev=$(state '.head_at_last_pass // ""')
  if [ "$head" != "$prev" ]; then
    STATE=$(jq -c --arg h "$head" --argjson t "$(date +%s)" '
      if .head_at_last_pass != null then .started_at = $t else . end
      | .head_at_last_pass = $h | .head_committed_at = null | .checks_seen = {} | .mergeable_false_reads = 0
      | if .queue.evicted != null then .queue.evicted = null | .queue.head = null else . end' <<<"$STATE")
  fi
  if [ "$(state '.head_committed_at == null')" = true ] &&
    committed=$(get "repos/$REPO/commits/$head" '.commit.committer.date'); then
    STATE=$(jq -c --arg c "$committed" '.head_committed_at = $c' <<<"$STATE")
  fi

  if [ "$checks_ok" = 1 ]; then
    seen=$(jq -c '.checks_seen' <<<"$STATE")
    emit "$(jq -r --argjson seen "$seen" '
      .[]
      | select(.bucket != "pending")
      | select(($seen[.name] // "") != .bucket)
      | "CHECK \(.name | gsub("[\r\n\t]+"; " ")) \(.bucket) \(if .link == "" then "-" else .link end)"
    ' <<<"$checks")"
    STATE=$(jq -c --argjson c "$checks" '.checks_seen = ($c | map({ (.name): .bucket }) | add // {})' <<<"$STATE")
  fi

  wm_r=$(state '.watermarks.reviews')
  items=$(get "repos/$REPO/pulls/$PR/reviews?per_page=100" \
    '.[] | select(.submitted_at != null) | { id, author: .user.login, state, at: .submitted_at }') ||
    reviews_ok=0
  emit "$(jq -rs --arg wm "$wm_r" '
    map(select(.at > $wm)) | sort_by(.at) | .[] | "REVIEW \(.author) \(.state) \(.id)"
  ' <<<"$items")"
  next_r=$(jq -rs --arg wm "$wm_r" '[.[].at] + [$wm] | max' <<<"$items" 2>/dev/null) || next_r=$wm_r
  changes_requested=$(jq -rs '
    map(select(.state != "COMMENTED")) | group_by(.author)
    | any(.[]; max_by(.at).state == "CHANGES_REQUESTED")' <<<"$items")

  emit "$(jq -rs --arg wm "$wm_c" '
    map(select(.at > $wm)) | sort_by(.at) | .[]
    | "COMMENT \(.author) \(.id) \((.body // "") | gsub("[\r\n]+"; " ") | .[0:80])"
  ' <<<"$comments")"
  next_c=$(jq -rs --arg wm "$wm_c" '[.[].at] + [$wm] | max' <<<"$comments" 2>/dev/null) || next_c=$wm_c

  latest=""
  requeued=false
  activity=$(jq -cs 'map(select((.body // "") | test("Merge activity"))) | sort_by(.at) | last // empty' <<<"$comments")
  if ! native_queue && [ -n "$activity" ]; then
    act_id=$(jq -r '.id' <<<"$activity")
    act_author=$(jq -r '.author' <<<"$activity")
    act_seen=$(state ".merge_activity[\"$act_id\"] // 0")
    act_n=$(jq -r "$QUEUE_ENTRIES | length" <<<"$activity")
    # A shorter log than last pass is a replaced comment, not a rewind.
    [ "$act_n" -ge "$act_seen" ] || act_seen=0
    bullets=$(jq -c --argjson seen "$act_seen" "$QUEUE_BULLETS" <<<"$activity")
    emit "$(jq -r --arg author "$act_author" --arg id "$act_id" '
      if .kind == "queued" then "QUEUED \(.actor // $author) \($id)"
      elif .kind == "unqueued" then "UNQUEUED \(.actor)"
      else empty end' <<<"$bullets")"
    latest=$(jq -cs 'last // empty' <<<"$bullets")
    requeued=$(jq -rs 'any(.[]; .kind == "queued")' <<<"$bullets")
    STATE=$(jq -c --arg id "$act_id" --argjson n "$act_n" '.merge_activity[$id] = $n' <<<"$STATE")
  fi

  latest_kind=$(jq -r '.kind // ""' <<<"${latest:-null}")

  if native_queue; then
    case "$authoritative_queue" in
    landed) finish queue-merged ;;
    queued)
      [ "$(state '.queue.queued')" = true ] || emit "QUEUED $QUEUE_PROVIDER api"
      STATE=$(jq -c --arg h "$(jq -r '.enqueued // ""' <<<"$authoritative")" '.queue.queued = true | .queue.head = $h | .queue.evicted = null' <<<"$STATE")
      present=true
      ;;
    evicted)
      if [ "$(state '.queue.evicted == null')" = true ]; then
        evicted="other $(jq -r '(.evicted // "queue entry failed") | gsub("[\r\n\t]+"; " ")' <<<"$authoritative")"
      fi
      STATE=$(jq -c '.queue.queued = false' <<<"$STATE")
      present=false
      ;;
    unknown)
      STATE=$(jq -c '.queue.queued = null' <<<"$STATE")
      present=false
      ;;
    "not queued")
      [ "$(state '.queue.queued')" != true ] || emit "UNQUEUED $QUEUE_PROVIDER"
      STATE=$(jq -c '.queue.queued = false' <<<"$STATE")
      present=false
      ;;
    esac
  else
  case "$latest_kind" in
  queued)
    STATE=$(jq -c --arg h "$head" '.queue.queued = true | .queue.head = $h | .queue.evicted = null' <<<"$STATE")
    if [ "$present" != true ] && [ "$last_event" = unlabeled ]; then
      STATE=$(jq -c --arg s "$stint" '.queue.resolved_stint = $s' <<<"$STATE")
    fi
    ;;
  unqueued)
    STATE=$(jq -c --arg s "$stint" '.queue.queued = false | .queue.resolved_stint = $s' <<<"$STATE")
    ;;
  drop)
    if [ "$(jq -r --arg c "$(state '.head_committed_at // ""')" '$c != "" and .at < $c[0:16]' <<<"$latest")" = true ]; then
      STATE=$(jq -c --arg s "$stint" '.queue.queued = false | .queue.resolved_stint = $s' <<<"$STATE")
    elif [ "$requeued" = true ] || [ "$(state '.queue.evicted == null')" = true ]; then
      evicted=$(jq -r '"\(.class) \(.detail)"' <<<"$latest")
    fi
    ;;
  *)
    if [ "$present" = true ]; then
      if [ "$(jq -c --arg s "$stint" '.queue.queued != true and .queue.resolved_stint != $s' <<<"$STATE")" = true ]; then
        emit "QUEUED $(jq -r '.labeled_by' <<<"$queue") label"
        STATE=$(jq -c --arg h "$head" '.queue.queued = true | .queue.head = $h | .queue.evicted = null' <<<"$STATE")
      fi
    elif [ "$last_event" = unlabeled ] && [ "$stint" != "$(state '.queue.resolved_stint // ""')" ]; then
      if [ "$last_bot" = true ]; then
        evicted=$(eviction_reason "$merge_state" "$mergeable" "$head" "$checks" "$queue" "$last_actor" "$downstack")
      elif [ "$(state '.queue.queued')" = true ]; then
        emit "UNQUEUED $last_actor"
        STATE=$(jq -c --arg s "$stint" '.queue.queued = false | .queue.resolved_stint = $s' <<<"$STATE")
      fi
    fi
    ;;
  esac
  fi
  if [ -n "$evicted" ]; then
    STATE=$(jq -c --arg r "$evicted" --arg h "$head" --arg s "$stint" '
      .queue.evicted = $r | .queue.queued = false | .queue.resolved_stint = $s
      | if ($r | startswith("conflicts ")) then .conflicted_head = $h else . end' <<<"$STATE")
  fi

  reads=$(state '.mergeable_false_reads')
  case "$mergeable" in
  false) reads=$((reads + 1)) ;;
  true) reads=0 ;;
  esac
  if [ "$merge_state" = dirty ] || [ "$reads" -ge "$MERGEABLE_FALSE_READS" ]; then conflicted=1; fi

  STATE=$(jq -c --arg c "$next_c" --arg r "$next_r" --arg m "$merge_state" --argjson n "$reads" '
    .watermarks.comments = $c | .watermarks.reviews = $r
    | .merge_state_seen = $m | .mergeable_false_reads = $n' <<<"$STATE")
  write_state

  if [ "$merged" = true ]; then finish merged; fi
  if [ "$pr_state" = closed ] && native_queue; then finish closed; fi
  if [ "$pr_state" = closed ]; then
    closed_as=$(closed_verdict "$base")
    if [ -n "$closed_as" ]; then finish "$closed_as"; fi
    return 0
  fi

  if [ -n "$evicted" ]; then
    if [ -n "$(squash_on_base "$base" || true)" ]; then finish queue-merged; fi
    finish "evicted $evicted"
  fi

  if [ "$conflicted" = 1 ]; then
    [ "$(state '.conflicted_head // ""')" != "$head" ] || return 0
    STATE=$(jq -c --arg h "$head" '.conflicted_head = $h' <<<"$STATE")
    write_state
    finish conflicted
  fi

  [ "$checks_ok" = 1 ] || return 0

  settled=0
  if [ "$mergeable" = true ] && [ "$present" = false ] &&
    [ "$(state '.queue.queued == false and .queue.evicted == null')" = true ]; then
    if ! native_queue || [ "$(jq -r '.verdict' <<<"$authoritative")" = landable ]; then settled=1; fi
  fi

  n_checks=$(jq 'length' <<<"$checks")
  if [ "$n_checks" = 0 ]; then
    # A just-pushed head carries no registered checks for a few seconds, so an
    # empty rollup only counts as green once it holds across several passes.
    empty_passes=$((empty_passes + 1))
    if [ "$empty_passes" -ge "$EMPTY_PASSES_BEFORE_GREEN" ] && [ "$settled" = 1 ]; then green "$head" "$merge_state" "$reviews_ok" "$changes_requested" "$downstack"; fi
    return 0
  fi
  empty_passes=0

  verdict=$(jq -r --arg downstack "$downstack" '
    if any(.[]; .bucket == "pending" and ($downstack == "" or .graphite_mergeability != true)) then "pending"
    elif any(.[]; .bucket == "fail" or .bucket == "cancel") then "checks-failed"
    else "green" end
  ' <<<"$checks")
  case "$verdict" in
  checks-failed) finish checks-failed ;;
  green) [ "$settled" = 0 ] || green "$head" "$merge_state" "$reviews_ok" "$changes_requested" "$downstack" ;;
  esac
}

rest_until() {
  local due=$1 now window_end=$((STARTED + WINDOW))
  now=$(date +%s)
  if [ "$WINDOW" -gt 0 ] && [ "$due" -ge "$window_end" ]; then
    [ "$window_end" -le "$now" ] || sleep $((window_end - now)) 9>&-
    load_state
    STATE=$(jq -c --argjson d "$due" '.next_poll_at = $d' <<<"$STATE")
    write_state
    finish window-elapsed
  fi
  [ "$due" -le "$now" ] || sleep $((due - now)) 9>&-
}

load_state
if [ "$(state '.finished // ""')" != "" ]; then
  STATE=$(jq -c --argjson s "$STARTED" '.started_at = $s | del(.finished)' <<<"$STATE")
  write_state
fi
cci_watch claim
rest_until "$(state '.next_poll_at // 0')"
while :; do
  polled_at=$(date +%s)
  poll
  now=$(date +%s)
  watch_started=$(jq -r --argjson s "$STARTED" '.started_at // $s' <<<"$STATE")
  if [ "$DEADLINE" -gt 0 ] && [ $((now - watch_started)) -ge "$DEADLINE" ]; then
    finish deadline-still-open
  fi
  rest_until $((polled_at + INTERVAL))
done
