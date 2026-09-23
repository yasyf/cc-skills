#!/usr/bin/env bash
# Polls one pull request over the REST API and prints one line per new event,
# the Monitor command behind open-pr's background watcher.
#
#   pr-poll.sh <owner/repo> <pr-number> <state-file>
#
#   CHECK    <name> <bucket> <link>
#   REVIEW   <author> <state> <id>
#   COMMENT  <author> <id> <first-80-chars-of-body>
#   QUEUED   <actor> <label|comment-id>
#   UNQUEUED <actor>
#   DONE     all-green | merged | queue-merged | closed | checks-failed |
#            conflicted | deadline-still-open
#   DONE     evicted <conflicts|failed-ci|downstack|head-moved|other|unknown> <detail>
#
# Exits 0 after DONE. QUEUED means the queue label is on the PR; UNQUEUED
# means a human took it off. Neither is terminal.
#
# conflicted: mergeable_state dirty, or mergeable false on two reads with no
# true between them. A null mergeable (GitHub still recomputing after the base
# or head moved) is no read at all. Fires once per head.
#
# evicted: the latest queue-label event is a bot taking the label off, or the
# queue's merge-activity comment logs a drop as its latest queue entry. Fires
# once per removal event; relabelling re-arms it.
#
# all-green needs every check passed, mergeable true, and the PR neither
# carrying the queue label nor evicted and waiting for a relabel: a queued PR
# is watched until it lands or the queue drops it.
#
# A fresh state file watches from now on: label events before its start and the
# merge-activity entries already posted are history. Pre-seed .watermarks to
# replay them.
set -euo pipefail

STATE_SCHEMA=2
EMPTY_PASSES_BEFORE_GREEN=3
MERGEABLE_FALSE_READS=2
INTERVAL_MIN=120
INTERVAL_FLOOR_PER_PR=10
QUEUE_LABEL="${PR_POLL_QUEUE_LABEL:-merge}"
DEADLINE="${PR_POLL_DEADLINE:-14400}"
[[ $DEADLINE =~ ^[0-9]+$ ]] || DEADLINE=14400
STARTED=$(date +%s)

CHECK_RUNS='.check_runs[] | {
  name,
  bucket: (
    if .status != "completed" then "pending"
    elif .conclusion == "success" then "pass"
    elif .conclusion == "neutral" or .conclusion == "skipped" then "skipping"
    elif .conclusion == "cancelled" then "cancel"
    elif .conclusion | IN("failure", "timed_out", "action_required", "startup_failure") then "fail"
    else "pending" end),
  link: (.details_url // .html_url // ""),
  detail: ([.output.title, .output.summary] | map(select(. != null)) | join("\n"))
}'

STATUSES='.statuses[] | {
  name: .context,
  bucket: ({ success: "pass", pending: "pending" }[.state] // "fail"),
  link: (.target_url // ""),
  detail: (.description // "")
}'

LABEL_EVENTS='.[] | {
  id, event, created_at,
  actor: { login: (.actor.login // "-"), type: (.actor.type // "") },
  label: (.label.name // "")
}'

QUEUE_ENTRIES='(.body // "") | split("\n") | map(select(test("^ *[*-] +")) | gsub("^ *[*-] +"; ""))'

QUEUE_BULLETS="$QUEUE_ENTRIES"'
  | .[$seen:][]
  | (gsub("\\[(?<t>[^]]*)\\]\\([^)]*\\)"; "\(.t)") | gsub("[*`]"; "") | gsub("[\r\n\t]+"; " ") | sub("^[A-Z][a-z]{2} [0-9]{1,2}, [0-9]{1,2}:[0-9]{2} [AP]M UTC: "; "")) as $text
  | if ($text | test("added this pull request to the .*merge queue"; "i"))
      then { kind: "queued" }
    elif ($text | test("downstack failures? on (PR )?#[0-9]+"; "i"))
      then { kind: "drop", class: "downstack",
             detail: ("#" + ($text | capture("downstack failures? on (PR )?#(?<n>[0-9]+)"; "i").n)) }
    elif ($text | test("merge conflict|try rebasing"; "i"))
      then { kind: "drop", class: "conflicts", detail: $text[0:80] }
    elif ($text | test("ci (failed|failure)|failing (required )?check|failed (required )?check|failed for an unknown reason"; "i"))
      then { kind: "drop", class: "failed-ci", detail: $text[0:80] }
    elif ($text | test("couldn.t merge this PR|can ?not be added to the|removed this pull request|removed .* from the .*queue|disabled \"merge when ready\""; "i"))
      then { kind: "drop", class: "other", detail: $text[0:80] }
    else empty end'

QUEUE_EVENTS='
  def bot: .actor.type == "Bot" or (.actor.login | endswith("[bot]"));
  sort_by(.id) as $events
  | ($events | map(select((.event == "labeled" or .event == "unlabeled") and .label == $label))) as $flips
  | ($flips | map(select(.event == "labeled")) | last) as $labeled
  | ($flips | last) as $last
  | {
      labeled_by: ($labeled.actor.login // "-"),
      last: (
        if $last != null and [$last.created_at, $last.id] > [$since.at, $since.id]
        then { id: ($last.id | tostring), event: $last.event, actor: $last.actor.login, bot: ($last | bot) }
        else null end),
      pushed_in_queue: ($labeled != null and any($events[]; .event == "head_ref_force_pushed" and .id > $labeled.id))
    }'

usage() {
  cat >&2 <<'EOF'
usage: pr-poll.sh <owner/repo> <pr-number> <state-file>

  Polls every PR_POLL_INTERVAL seconds over REST and prints one CHECK /
  REVIEW / COMMENT / QUEUED / UNQUEUED line per new event, then DONE and
  exit 0.

  PR_POLL_INTERVAL defaults to 120 and is floored there. Each pass spends
  ~7 REST calls, and every watcher on a stack spends them against one shared
  hourly budget, so the interval is also floored at 10 seconds per
  concurrently watched PR: set PR_POLL_STACK to the number of PRs being
  watched at once (default 1). PR_POLL_QUEUE_LABEL names the merge-queue
  label (default merge).
EOF
  exit 2
}

for dep in gh jq; do
  command -v "$dep" >/dev/null 2>&1 || {
    echo "pr-poll.sh: $dep is required but not installed" >&2
    exit 1
  }
done

[ $# -eq 3 ] || usage
REPO=$1
PR=$2
STATE_FILE=$3

[[ $REPO =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || usage
[[ $PR =~ ^[0-9]+$ ]] || usage

STACK="${PR_POLL_STACK:-1}"
[[ $STACK =~ ^[1-9][0-9]*$ ]] || STACK=1
FLOOR=$((INTERVAL_FLOOR_PER_PR * STACK))

INTERVAL="${PR_POLL_INTERVAL:-$INTERVAL_MIN}"
[[ $INTERVAL =~ ^[0-9]+$ ]] || INTERVAL=$INTERVAL_MIN
[ "$INTERVAL" -ge "$INTERVAL_MIN" ] || INTERVAL=$INTERVAL_MIN
[ "$INTERVAL" -ge "$FLOOR" ] || INTERVAL=$FLOOR

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkdir -p "$(dirname "$STATE_FILE")"

STATE='{}'
empty_passes=0
FRESH=1
if [ -f "$STATE_FILE" ] && jq -e '.watermarks' "$STATE_FILE" >/dev/null 2>&1; then FRESH=0; fi

normalize() {
  jq -c --argjson schema "$STATE_SCHEMA" --argjson pr "$PR" --arg repo "$REPO" --arg now "$NOW" '
    { head_at_last_pass: null, checks_seen: {}, merge_activity: {}, merge_state_seen: null,
      mergeable_false_reads: 0, conflicted_head: null,
      queue: { label: null, head: null, evicted: null, evicted_event: null },
      attempts: {}, applied: [], escalated: [], watcher: null } * .
    | .schema = $schema | .pr = $pr | .repo = $repo
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

finish() {
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
  local merge_state=$1 mergeable=$2 head=$3 checks=$4 queue=$5 actor=$6 downstack failing
  if [ "$merge_state" = dirty ] || [ "$mergeable" = false ]; then
    printf 'conflicts mergeable_state=%s\n' "$merge_state"
    return
  fi
  downstack=$(jq -r '
    [.[] | select(.name | test("graphite"; "i")) | .detail
      | capture("#(?<n>[0-9]+) needs to be merged").n] | first // empty' <<<"$checks")
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

poll() {
  local view head prev base pr_state merged mergeable merge_state present
  local checks checks_ok=1 events events_ok=1 items seen wm_c wm_r next_c next_r queue baseline
  local activity act_id act_author act_seen act_n bullets drop reads was_queued
  local last_event last_id last_bot last_actor
  local evicted="" conflicted=0 settled n_checks verdict closed_as

  view=$(gh api "repos/$REPO/pulls/$PR" \
    --jq '{state, merged, head: .head.sha, base: .base.ref, mergeable, mergeable_state, labels: [.labels[].name]}' \
    2>/dev/null) || return 0
  jq -e . >/dev/null 2>&1 <<<"$view" || return 0
  head=$(jq -r '.head // ""' <<<"$view")
  [ -n "$head" ] || return 0
  base=$(jq -r '.base // ""' <<<"$view")
  pr_state=$(jq -r '.state // ""' <<<"$view")
  merged=$(jq -r '.merged // false' <<<"$view")
  mergeable=$(jq -r '.mergeable' <<<"$view")
  merge_state=$(jq -r '.mergeable_state // ""' <<<"$view")
  present=$(jq -r --arg l "$QUEUE_LABEL" 'any(.labels[]; . == $l)' <<<"$view")

  checks=$(
    {
      gh api --paginate "repos/$REPO/commits/$head/check-runs?per_page=100" --jq "$CHECK_RUNS" &&
        gh api --paginate "repos/$REPO/commits/$head/status?per_page=100" --jq "$STATUSES"
    } 2>/dev/null | jq -cs .
  ) || checks_ok=0
  [ "$checks_ok" = 1 ] || checks='[]'

  events=$(gh api --paginate "repos/$REPO/issues/$PR/events?per_page=100" --jq "$LABEL_EVENTS" \
    2>/dev/null | jq -cs .) || events_ok=0
  [ "$events_ok" = 1 ] || events='[]'

  load_state

  if [ "$FRESH" = 1 ]; then
    if baseline=$(gh api --paginate "repos/$REPO/issues/$PR/comments" \
      --jq '.[] | select((.body // "") | test("Merge activity")) | { id, body }' 2>/dev/null |
      jq -cs "map({ key: (.id | tostring), value: ($QUEUE_ENTRIES | length) }) | from_entries"); then
      STATE=$(jq -c --argjson b "$baseline" '.merge_activity = $b * .merge_activity' <<<"$STATE")
      FRESH=0
    fi
  fi

  prev=$(state '.head_at_last_pass // ""')
  if [ "$head" != "$prev" ]; then
    STATE=$(jq -c --arg h "$head" '.head_at_last_pass = $h | .checks_seen = {} | .mergeable_false_reads = 0' <<<"$STATE")
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
  items=$(gh api --paginate "repos/$REPO/pulls/$PR/reviews" \
    --jq '.[] | select(.submitted_at != null) | { id, author: .user.login, state, at: .submitted_at }' 2>/dev/null || true)
  emit "$(jq -rs --arg wm "$wm_r" '
    map(select(.at > $wm)) | sort_by(.at) | .[] | "REVIEW \(.author) \(.state) \(.id)"
  ' <<<"$items")"
  next_r=$(jq -rs --arg wm "$wm_r" '[.[].at] + [$wm] | max' <<<"$items" 2>/dev/null) || next_r=$wm_r

  wm_c=$(state '.watermarks.comments')
  items=$(
    gh api --paginate "repos/$REPO/issues/$PR/comments?since=$wm_c" \
      --jq '.[] | { id, author: .user.login, at: .created_at, body }' 2>/dev/null || true
    gh api --paginate "repos/$REPO/pulls/$PR/comments?since=$wm_c" \
      --jq '.[] | { id, author: .user.login, at: .created_at, body }' 2>/dev/null || true
  )
  emit "$(jq -rs --arg wm "$wm_c" '
    map(select(.at > $wm)) | sort_by(.at) | .[]
    | "COMMENT \(.author) \(.id) \((.body // "") | gsub("[\r\n]+"; " ") | .[0:80])"
  ' <<<"$items")"
  next_c=$(jq -rs --arg wm "$wm_c" '[.[].at] + [$wm] | max' <<<"$items" 2>/dev/null) || next_c=$wm_c

  drop=""
  activity=$(jq -cs 'map(select((.body // "") | test("Merge activity"))) | sort_by(.at) | last // empty' <<<"$items")
  if [ -n "$activity" ]; then
    act_id=$(jq -r '.id' <<<"$activity")
    act_author=$(jq -r '.author' <<<"$activity")
    act_seen=$(state ".merge_activity[\"$act_id\"] // 0")
    act_n=$(jq -r "$QUEUE_ENTRIES | length" <<<"$activity")
    # A shorter log than last pass is a replaced comment, not a rewind.
    [ "$act_n" -ge "$act_seen" ] || act_seen=0
    bullets=$(jq -c --argjson seen "$act_seen" "$QUEUE_BULLETS" <<<"$activity")
    emit "$(jq -r --arg author "$act_author" --arg id "$act_id" \
      'select(.kind == "queued") | "QUEUED \($author) \($id)"' <<<"$bullets")"
    drop=$(jq -rs 'last // empty | select(.kind == "drop") | "\(.class) \(.detail)"' <<<"$bullets")
    STATE=$(jq -c --arg id "$act_id" --argjson n "$act_n" '.merge_activity[$id] = $n' <<<"$STATE")
  fi

  queue=$(jq -c --argjson since "$(jq -c '.watermarks.events' <<<"$STATE")" --arg label "$QUEUE_LABEL" \
    "$QUEUE_EVENTS" <<<"$events")
  last_event=$(jq -r '.last.event // ""' <<<"$queue")
  last_id=$(jq -r '.last.id // ""' <<<"$queue")
  last_bot=$(jq -r '.last.bot // false' <<<"$queue")
  last_actor=$(jq -r '.last.actor // "-"' <<<"$queue")
  was_queued=$(state '.queue.label == true')

  if [ "$present" = true ]; then
    if [ "$was_queued" != true ]; then
      emit "QUEUED $(jq -r '.labeled_by' <<<"$queue") label"
      STATE=$(jq -c --arg h "$head" '.queue.label = true | .queue.head = $h | .queue.evicted = null' <<<"$STATE")
    fi
  elif [ "$last_event" = unlabeled ] && [ "$last_bot" = true ] && [ "$last_id" != "$(state '.queue.evicted_event // ""')" ]; then
    evicted=${drop:-$(eviction_reason "$merge_state" "$mergeable" "$head" "$checks" "$queue" "$last_actor")}
  elif [ -n "$drop" ] && [ "$(state '.queue.evicted == null')" = true ]; then
    evicted=$drop
  elif [ "$was_queued" = true ] && [ "$last_event" = unlabeled ]; then
    emit "UNQUEUED $last_actor"
    STATE=$(jq -c '.queue.label = false' <<<"$STATE")
  elif [ "$was_queued" != true ]; then
    STATE=$(jq -c '.queue.label = false' <<<"$STATE")
  fi
  if [ -n "$evicted" ]; then
    STATE=$(jq -c --arg r "$evicted" --arg h "$head" --arg e "$last_id" --arg ev "$last_event" '
      .queue.evicted = $r | .queue.label = false
      | if $ev == "unlabeled" then .queue.evicted_event = $e else . end
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
  if [ "$mergeable" = true ] && [ "$present" = false ] && [ "$events_ok" = 1 ] &&
    [ "$(state '.queue.label != true and .queue.evicted == null')" = true ]; then
    settled=1
  fi

  n_checks=$(jq 'length' <<<"$checks")
  if [ "$n_checks" = 0 ]; then
    # A just-pushed head carries no registered checks for a few seconds, so an
    # empty rollup only counts as green once it holds across several passes.
    empty_passes=$((empty_passes + 1))
    if [ "$empty_passes" -ge "$EMPTY_PASSES_BEFORE_GREEN" ] && [ "$settled" = 1 ]; then finish all-green; fi
    return 0
  fi
  empty_passes=0

  verdict=$(jq -r '
    if any(.[]; .bucket == "pending") then "pending"
    elif any(.[]; .bucket == "fail" or .bucket == "cancel") then "checks-failed"
    else "all-green" end
  ' <<<"$checks")
  case "$verdict" in
  checks-failed) finish checks-failed ;;
  all-green) [ "$settled" = 0 ] || finish all-green ;;
  esac
}

while :; do
  poll
  if [ "$DEADLINE" -gt 0 ] && [ $(($(date +%s) - STARTED)) -ge "$DEADLINE" ]; then
    finish deadline-still-open
  fi
  sleep "$INTERVAL"
done
