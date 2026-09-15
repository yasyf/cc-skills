#!/usr/bin/env bash
# Polls one pull request and prints one line per new event — the Monitor command
# behind open-pr's background watcher.
#
#   pr-poll.sh <owner/repo> <pr-number> <state-file>
#
#   CHECK   <name> <bucket> <link>
#   REVIEW  <author> <state> <id>
#   COMMENT <author> <id> <first-80-chars-of-body>
#   QUEUED  <author> <id>
#   QUEUE-DROPPED <conflicts|failed-ci|other> <first-80-chars-of-entry>
#   CONFLICT <merge-state>
#   DONE    all-green | merged | queue-merged | closed | checks-failed |
#           deadline-still-open
#
# Exits 0 after DONE. QUEUED and QUEUE-DROPPED are not terminal: the PR is
# still open and the watch continues.
#
# Both read Graphite's one "Merge activity" comment, which it edits in place
# under whoever enqueued the PR, so a queue event is an edit of an old
# comment and the bullets already reported are what fire each event once.
#
# A merge queue that squash-merges leaves the PR CLOSED with mergedAt null, and
# closes abandoned PRs the same way, so the closed path asks for the landing
# itself: the squash naming the PR on the base branch, or the queue's own
# "Merged by" line. Neither means closed.
#
# A fresh state file watches from now on; pre-seed .watermarks to replay a PR's
# existing comments and reviews.
set -euo pipefail

STATE_SCHEMA=1
EMPTY_PASSES_BEFORE_GREEN=3
INTERVAL_FLOOR_PER_PR=10
DEADLINE="${PR_POLL_DEADLINE:-14400}"
[[ $DEADLINE =~ ^[0-9]+$ ]] || DEADLINE=14400
STARTED=$(date +%s)

# The bullets of Graphite's merge-activity log, oldest first.
QUEUE_ENTRIES='(.body // "") | split("\n") | map(select(test("^ *[*-] +")) | gsub("^ *[*-] +"; ""))'

# Each bullet past $seen as one event line; a bullet naming no queue event emits nothing.
QUEUE_EVENTS="$QUEUE_ENTRIES"'
  | .[$seen:][]
  | (gsub("\\[(?<t>[^]]*)\\]\\([^)]*\\)"; "\(.t)") | gsub("[*`]"; "") | gsub("[\r\n\t]+"; " ")) as $text
  | if ($text | test("added this pull request to the .*merge queue"; "i"))
      then "QUEUED \($author) \($id)"
    elif ($text | test("merge conflict|try rebasing"; "i"))
      then "QUEUE-DROPPED conflicts \($text[0:80])"
    elif ($text | test("ci (failed|failure)|failing (required )?check|failed (required )?check|failed for an unknown reason"; "i"))
      then "QUEUE-DROPPED failed-ci \($text[0:80])"
    elif ($text | test("couldn.t merge this PR|can ?not be added to the|removed this pull request|removed .* from the .*queue|disabled \"merge when ready\"|downstack failure"; "i"))
      then "QUEUE-DROPPED other \($text[0:80])"
    else empty end'

usage() {
  cat >&2 <<'EOF'
usage: pr-poll.sh <owner/repo> <pr-number> <state-file>

  Polls every PR_POLL_INTERVAL seconds and prints one CHECK / REVIEW /
  COMMENT / QUEUED line per new event, then DONE and exit 0.

  PR_POLL_INTERVAL defaults to 30. Each pass spends ~5 API calls, and every
  watcher on a stack spends them against one shared hourly budget, so the
  interval is floored at 10 seconds per concurrently watched PR: set
  PR_POLL_STACK to the number of PRs being watched at once (default 1).
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

INTERVAL="${PR_POLL_INTERVAL:-30}"
[[ $INTERVAL =~ ^[0-9]+$ ]] || INTERVAL=30
[ "$INTERVAL" -ge "$FLOOR" ] || INTERVAL=$FLOOR

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkdir -p "$(dirname "$STATE_FILE")"

STATE='{}'
empty_passes=0

normalize() {
  jq -c --argjson schema "$STATE_SCHEMA" --argjson pr "$PR" --arg repo "$REPO" --arg now "$NOW" '
    { head_at_last_pass: null, checks_seen: {}, merge_activity: {}, merge_state_seen: null, attempts: {},
      applied: [], escalated: [], watcher: null } * .
    | .schema = $schema | .pr = $pr | .repo = $repo
    | .watermarks = ({ comments: $now, reviews: $now } * (.watermarks // {}))
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

emit() {
  [ -n "$1" ] || return 0
  printf '%s\n' "$1"
}

finish() {
  printf 'DONE %s\n' "$1"
  exit 0
}

closed_verdict() {
  local base=$1 sha comments rc
  sha=$(gh api "repos/$REPO/commits?sha=$base&per_page=100" \
    --jq ".[] | select((.commit.message | split(\"\\n\")[0]) | endswith(\"(#$PR)\")) | .sha" \
    2>/dev/null | head -1) || sha=""
  if [ -n "$sha" ]; then printf 'queue-merged\n'; return 0; fi

  comments=$(gh api "repos/$REPO/issues/$PR/comments" --paginate \
    --jq '.[] | select((.body // "") | test("Merged by the \\[?Graphite merge queue"; "i")) | .id' \
    2>/dev/null) && rc=0 || rc=$?
  if [ -n "$comments" ]; then printf 'queue-merged\n'; return 0; fi

  # Both lookups failing leaves no evidence either way; a verdict would be a guess.
  [ "$rc" -eq 0 ] || return 0
  printf 'closed\n'
}

poll() {
  local view checks head prev seen items wm_c wm_r next_c next_r pr_state merged closed_as n_checks verdict
  local base merge_state
  local activity act_id act_author act_seen act_n

  view=$(gh pr view "$PR" --repo "$REPO" \
    --json state,mergedAt,headRefOid,baseRefName,mergeStateStatus,statusCheckRollup \
    --jq '{state, mergedAt, headRefOid, baseRefName, mergeStateStatus, n_checks: (.statusCheckRollup | length)}' \
    2>/dev/null || true)
  jq -e . >/dev/null 2>&1 <<<"$view" || view='{}'

  checks=$(gh pr checks "$PR" --repo "$REPO" \
    --json name,state,bucket,link,description,workflow 2>/dev/null || true)
  jq -e . >/dev/null 2>&1 <<<"$checks" || checks='[]'

  load_state

  head=$(jq -r '.headRefOid // ""' <<<"$view")
  prev=$(jq -r '.head_at_last_pass // ""' <<<"$STATE")
  if [ -n "$head" ] && [ "$head" != "$prev" ]; then
    STATE=$(jq -c --arg h "$head" '.head_at_last_pass = $h | .checks_seen = {}' <<<"$STATE")
  fi

  seen=$(jq -c '.checks_seen' <<<"$STATE")
  emit "$(jq -r --argjson seen "$seen" '
    .[]
    | select(.bucket != "pending")
    | select(($seen[.name] // "") != .bucket)
    | "CHECK \(.name | gsub("[\r\n\t]+"; " ")) \(.bucket) \(if (.link // "") == "" then "-" else .link end)"
  ' <<<"$checks")"
  STATE=$(jq -c --argjson c "$checks" '.checks_seen = ($c | map({ (.name): .bucket }) | add // {})' <<<"$STATE")

  wm_r=$(jq -r '.watermarks.reviews' <<<"$STATE")
  items=$(gh api --paginate "repos/$REPO/pulls/$PR/reviews" \
    --jq '.[] | select(.submitted_at != null) | { id, author: .user.login, state, at: .submitted_at }' 2>/dev/null || true)
  emit "$(jq -rs --arg wm "$wm_r" '
    map(select(.at > $wm)) | sort_by(.at) | .[] | "REVIEW \(.author) \(.state) \(.id)"
  ' <<<"$items")"
  next_r=$(jq -rs --arg wm "$wm_r" '[.[].at] + [$wm] | max' <<<"$items" 2>/dev/null) || next_r=$wm_r

  wm_c=$(jq -r '.watermarks.comments' <<<"$STATE")
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
  activity=$(jq -cs 'map(select((.body // "") | test("Merge activity"))) | sort_by(.at) | last // empty' <<<"$items")
  if [ -n "$activity" ]; then
    act_id=$(jq -r '.id' <<<"$activity")
    act_author=$(jq -r '.author' <<<"$activity")
    act_seen=$(jq -r --arg id "$act_id" '.merge_activity[$id] // 0' <<<"$STATE")
    act_n=$(jq -r "$QUEUE_ENTRIES | length" <<<"$activity")
    # A shorter log than last pass is a replaced comment, not a rewind.
    [ "$act_n" -ge "$act_seen" ] || act_seen=0
    emit "$(jq -r --argjson seen "$act_seen" --arg author "$act_author" --arg id "$act_id" \
      "$QUEUE_EVENTS" <<<"$activity")"
    STATE=$(jq -c --arg id "$act_id" --argjson n "$act_n" '.merge_activity[$id] = $n' <<<"$STATE")
  fi
  next_c=$(jq -rs --arg wm "$wm_c" '[.[].at] + [$wm] | max' <<<"$items" 2>/dev/null) || next_c=$wm_c

  STATE=$(jq -c --arg c "$next_c" --arg r "$next_r" \
    '.watermarks.comments = $c | .watermarks.reviews = $r' <<<"$STATE")
  write_state

  pr_state=$(jq -r '.state // ""' <<<"$view")
  merged=$(jq -r '.mergedAt // ""' <<<"$view")
  base=$(jq -r '.baseRefName // ""' <<<"$view")
  if [ -n "$merged" ] || [ "$pr_state" = MERGED ]; then finish merged; fi
  if [ "$pr_state" = CLOSED ]; then
    closed_as=$(closed_verdict "$base")
    if [ -n "$closed_as" ]; then finish "$closed_as"; fi
    return 0
  fi

  merge_state=$(jq -r '.mergeStateStatus // ""' <<<"$view")
  if [ -n "$merge_state" ] && [ "$merge_state" != "$(jq -r '.merge_state_seen // ""' <<<"$STATE")" ]; then
    if [ "$merge_state" = DIRTY ]; then emit "CONFLICT $merge_state"; fi
    STATE=$(jq -c --arg m "$merge_state" '.merge_state_seen = $m' <<<"$STATE")
    write_state
  fi

  n_checks=$(jq -r '.n_checks // -1' <<<"$view")
  if [ "$n_checks" = 0 ]; then
    # A just-pushed head carries no registered checks for a few seconds, so an
    # empty rollup only counts as green once it holds across several passes.
    empty_passes=$((empty_passes + 1))
    if [ "$empty_passes" -ge "$EMPTY_PASSES_BEFORE_GREEN" ]; then finish all-green; fi
    return 0
  fi
  empty_passes=0

  # A failed fetch on either call leaves nothing to judge; a verdict would be a guess.
  [ "$n_checks" -gt 0 ] || return 0
  [ "$(jq 'length' <<<"$checks")" -gt 0 ] || return 0

  verdict=$(jq -r '
    if any(.[]; .bucket == "pending") then "pending"
    elif any(.[]; .bucket == "fail" or .bucket == "cancel") then "checks-failed"
    else "all-green" end
  ' <<<"$checks")
  if [ "$verdict" != pending ]; then finish "$verdict"; fi
}

while :; do
  poll
  if [ "$DEADLINE" -gt 0 ] && [ $(($(date +%s) - STARTED)) -ge "$DEADLINE" ]; then
    finish deadline-still-open
  fi
  sleep "$INTERVAL"
done
