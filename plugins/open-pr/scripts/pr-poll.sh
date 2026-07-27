#!/usr/bin/env bash
# Polls one pull request and prints one line per new event — the Monitor command
# behind open-pr's background watcher.
#
#   pr-poll.sh <owner/repo> <pr-number> <state-file>
#
#   CHECK   <name> <bucket> <link>
#   REVIEW  <author> <state> <id>
#   COMMENT <author> <id> <first-80-chars-of-body>
#   DONE    all-green | merged | closed | checks-failed
#
# Exits 0 after DONE. A fresh state file watches from now on; pre-seed
# .watermarks to replay a PR's existing comments and reviews.
set -euo pipefail

STATE_SCHEMA=1
EMPTY_PASSES_BEFORE_GREEN=3

usage() {
  cat >&2 <<'EOF'
usage: pr-poll.sh <owner/repo> <pr-number> <state-file>

  Polls every PR_POLL_INTERVAL seconds (default 30, floor 10) and prints one
  CHECK / REVIEW / COMMENT line per new event, then DONE and exit 0.
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

INTERVAL="${PR_POLL_INTERVAL:-30}"
[[ $INTERVAL =~ ^[0-9]+$ ]] || INTERVAL=30
[ "$INTERVAL" -ge 10 ] || INTERVAL=10

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
mkdir -p "$(dirname "$STATE_FILE")"

STATE='{}'
empty_passes=0

normalize() {
  jq -c --argjson schema "$STATE_SCHEMA" --argjson pr "$PR" --arg repo "$REPO" --arg now "$NOW" '
    { head_at_last_pass: null, checks_seen: {}, attempts: {},
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

poll() {
  local view checks head prev seen items wm_c wm_r next_c next_r pr_state merged n_checks verdict

  view=$(gh pr view "$PR" --repo "$REPO" --json state,mergedAt,headRefOid,statusCheckRollup \
    --jq '{state, mergedAt, headRefOid, n_checks: (.statusCheckRollup | length)}' 2>/dev/null || true)
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
  next_c=$(jq -rs --arg wm "$wm_c" '[.[].at] + [$wm] | max' <<<"$items" 2>/dev/null) || next_c=$wm_c

  STATE=$(jq -c --arg c "$next_c" --arg r "$next_r" \
    '.watermarks.comments = $c | .watermarks.reviews = $r' <<<"$STATE")
  write_state

  pr_state=$(jq -r '.state // ""' <<<"$view")
  merged=$(jq -r '.mergedAt // ""' <<<"$view")
  if [ -n "$merged" ] || [ "$pr_state" = MERGED ]; then finish merged; fi
  if [ "$pr_state" = CLOSED ]; then finish closed; fi

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
  sleep "$INTERVAL"
done
