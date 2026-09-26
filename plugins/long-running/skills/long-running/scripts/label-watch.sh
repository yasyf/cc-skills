#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: label-watch.sh once <pr>...
       label-watch.sh watch <list-file>

Adds the queue label to each pull request that passes the gate and prints one
line per pull request:

  <pr> SKIP <queue>               Graphite reads it queued or landed
  <pr> HELD                       it carries the hold label
  <pr> CONFLICT <sha> <files>     its head conflicts with the fresh trunk
  <pr> NOT-READY <sha> <reason>   base, mergeability, checks, or approval not there
                                  yet, or conflicts-with #<queued pr> <files>
  <pr> API-FAIL <read>            a GitHub, Graphite, or git fetch failed
  <pr> LABELLED <sha>             the queue label went on

watch re-gates every number in <list-file>, one per line, each interval until
the file is empty. It deletes LABELLED and SKIP entries from the file, keeps
the rest, and prints a timestamped line only when a result changes. The PRs it
saw queued or labelled stay conflict bases on every sweep until they close.

  LABEL_WATCH_APPROVERS  comma-separated logins that must approve the head sha
  LABEL_WATCH_REPO       owner/name, default the checkout's origin
  LABEL_WATCH_TRUNK      default the checkout's origin/HEAD
  LABEL_WATCH_CHECKOUT   local clone for the conflict check, default $PWD
  LABEL_WATCH_LABEL      queue label, default merge
  LABEL_WATCH_INTERVAL   seconds between watch sweeps, default 240
EOF
  exit 2
}

[ $# -ge 2 ] || usage
MODE=$1
shift

APPROVERS=${LABEL_WATCH_APPROVERS:?set LABEL_WATCH_APPROVERS to the logins that must approve the head sha}
CHECKOUT=${LABEL_WATCH_CHECKOUT:-$PWD}
REPO=${LABEL_WATCH_REPO:-$(git -C "$CHECKOUT" remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')}
TRUNK=${LABEL_WATCH_TRUNK:-$(git -C "$CHECKOUT" symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##')}
LABEL=${LABEL_WATCH_LABEL:-merge}
INTERVAL=${LABEL_WATCH_INTERVAL:-240}
TRUNK_REF=refs/label-watch/$TRUNK
QUEUED=
TRACKED=

onto_trunk() {
  git -C "$CHECKOUT" -c user.name=label-watch -c user.email=label-watch@localhost \
    commit-tree "$1" -p "$TRUNK_REF" -p "$2" -m "trunk with #$3"
}

downstack() {
  branch=$1
  while [ "$branch" != "$TRUNK" ]; do
    parent=$(gh api "repos/$REPO/pulls?state=open&head=${REPO%%/*}:$branch" --jq '.[:1][] | "\(.number) \(.base.ref)"') || return 1
    [ -n "$parent" ] || return 0
    echo "${parent%% *}"
    branch=${parent#* }
  done
}

gate() {
  n=$1
  queue=$2
  if [ "$queue" != "not queued" ]; then
    echo "$n SKIP $queue"
    return
  fi

  pull=$(gh api "repos/$REPO/pulls/$n" --jq '"\(.head.sha) \(.base.ref) \(.mergeable) \(.mergeable_state) \(any(.labels[]; .name == "hold"))"') || {
    echo "$n API-FAIL pull"
    return
  }
  read -r sha base mergeable state held <<EOF
$pull
EOF
  if [ "$held" = true ]; then
    echo "$n HELD"
    return
  fi
  short=$(printf %.10s "$sha")

  git -C "$CHECKOUT" cat-file -e "$sha^{commit}" 2>/dev/null \
    || git -C "$CHECKOUT" fetch -q --no-prune --no-write-fetch-head origin "$sha" 2>/dev/null || {
    echo "$n API-FAIL head-fetch"
    return
  }
  rc=0
  merge=$(git -C "$CHECKOUT" merge-tree --write-tree --name-only --no-messages "$TRUNK_REF" "$sha") || rc=$?
  case $rc in
    0) ;;
    1)
      echo "$n CONFLICT $short $(printf '%s\n' "$merge" | sed 1d | paste -sd ' ' -)"
      return
      ;;
    *) exit "$rc" ;;
  esac

  if [ -n "$QUEUED" ]; then
    below=$(downstack "$base") || {
      echo "$n API-FAIL downstack"
      return
    }
    while read -r q onto; do
      case " $n $below " in *" $q "*) continue ;; esac
      rc=0
      ahead=$(git -C "$CHECKOUT" merge-tree --write-tree --name-only --no-messages "$onto" "$sha") || rc=$?
      case $rc in
        0) ;;
        1)
          echo "$n NOT-READY $short conflicts-with #$q $(printf '%s\n' "$ahead" | sed 1d | paste -sd ' ' -)"
          return
          ;;
        *) exit "$rc" ;;
      esac
    done <<EOF
$QUEUED
EOF
  fi

  case $base in
    graphite-base/*)
      echo "$n NOT-READY $short base $base"
      return
      ;;
  esac
  if [ "$mergeable" != true ]; then
    echo "$n NOT-READY $short mergeable $mergeable"
    return
  fi
  if [ "$base" = "$TRUNK" ] && [ "$state" != clean ]; then
    echo "$n NOT-READY $short $state"
    return
  fi

  statuses=$(gh api "repos/$REPO/commits/$sha/status" --jq '.statuses[] | "\(.state) \(.context)"') \
    && runs=$(gh api "repos/$REPO/commits/$sha/check-runs?per_page=100" --jq '.check_runs[] | "\(if .status == "completed" then .conclusion else "pending" end) \(.name)"') || {
    echo "$n API-FAIL checks"
    return
  }
  checks=$(printf '%s\n%s\n' "$statuses" "$runs" | awk '
    $0 == "" { next }
    { state = $1; sub(/^[^ ]+ /, "") }
    /^Graphite/ { next }
    state ~ /^(failure|error|cancelled|timed_out|action_required|startup_failure|stale)$/ { red = red (red ? "," : "") $0; next }
    state == "pending" { wait = wait (wait ? "," : "") $0 }
    END { if (red) print "red " red; else if (wait) print "pending " wait }')
  if [ -n "$checks" ]; then
    echo "$n NOT-READY $short $checks"
    return
  fi

  reviews=$(gh api "repos/$REPO/pulls/$n/reviews?per_page=100") || {
    echo "$n API-FAIL reviews"
    return
  }
  missing=$(printf '%s' "$reviews" | jq -r --arg sha "$sha" --arg need "$APPROVERS" '
    [.[] | select(.commit_id == $sha and .state == "APPROVED") | .user.login] as $have
    | [$need | split(",")[] | select(IN($have[]) | not)] | join(",")')
  if [ -n "$missing" ]; then
    echo "$n NOT-READY $short awaiting $missing"
    return
  fi

  gh api -X POST "repos/$REPO/issues/$n/labels" -f "labels[]=$LABEL" --silent || {
    echo "$n API-FAIL label"
    return
  }
  echo "$n LABELLED $short"
  QUEUED=$(printf '%s\n%s %s' "$QUEUED" "$n" "$(onto_trunk "$merge" "$sha" "$n")" | sed '/^$/d')
}

fetch_trunk() {
  attempt=1
  until git -C "$CHECKOUT" fetch -q --no-prune --no-write-fetch-head --refmap= origin "+$TRUNK:$TRUNK_REF" 2>/dev/null; do
    [ "$attempt" -lt 3 ] || return 1
    sleep "$((attempt * 2))"
    attempt=$((attempt + 1))
  done
}

sweep() {
  for n; do
    case $n in
      '' | *[!0-9]*)
        echo "not a pull request number: $n" >&2
        exit 2
        ;;
    esac
  done
  if ! fetch_trunk; then
    for n; do echo "$n API-FAIL trunk-fetch"; done
    return
  fi
  tracked=
  for t in $TRACKED; do
    case " $* " in *" $t "*) ;; *) tracked="$tracked $t" ;; esac
  done
  if ! queues=$(ccx vcs pr status --json -R "$REPO" "$@" $tracked); then
    for n; do echo "$n API-FAIL queue"; done
    return
  fi
  enqueued=$(printf '%s' "$queues" | jq -r '.[] | select(.queue == "queued") | "\(.number) \(.enqueued)"')
  missing=$(printf '%s\n' "$enqueued" | while read -r q sha; do
    [ -z "$sha" ] || git -C "$CHECKOUT" cat-file -e "$sha^{commit}" 2>/dev/null || echo "$sha"
  done)
  if [ -n "$missing" ] && ! git -C "$CHECKOUT" fetch -q --no-prune --no-write-fetch-head origin $missing 2>/dev/null; then
    for n; do echo "$n API-FAIL queued-fetch"; done
    return
  fi
  QUEUED=$(printf '%s\n' "$enqueued" | while read -r q sha; do
    [ -n "$sha" ] || continue
    tree=$(git -C "$CHECKOUT" merge-tree --write-tree --no-messages "$TRUNK_REF" "$sha") || continue
    echo "$q $(onto_trunk "$tree" "$sha" "$q")"
  done)
  printf '%s' "$queues" | jq -r --arg tracked "$tracked" '.[]
    | if (.number | tostring | IN($tracked | split(" ")[])) then
        if .state == "OPEN" then "\(.number) TRACKED" else empty end
      else "\(.number) \(.queue)" end' | while read -r n queue; do
    if [ "$queue" = TRACKED ]; then
      echo "$n TRACKED"
      continue
    fi
    gate "$n" "$queue"
    sleep 1
  done
}

watch() {
  list=$1
  while [ -s "$list" ]; do
    set -- $(cat "$list")
    [ $# -gt 0 ] || break
    results=$(sweep "$@")
    TRACKED=
    while read -r line; do
      n=${line%% *}
      rest=${line#* }
      case $rest in
        TRACKED | LABELLED* | "SKIP queued") TRACKED="$TRACKED $n" ;;
      esac
      [ "$rest" != TRACKED ] || continue
      eval "last=\${last_$n-}"
      [ "$line" = "$last" ] || echo "$(date -u +%H:%M) $line"
      eval "last_$n=\$line"
      case ${rest%% *} in
        LABELLED | SKIP) awk -v n="$n" '$1 != n' "$list" >"$list.tmp" && mv "$list.tmp" "$list" ;;
      esac
    done <<EOF
$results
EOF
    [ ! -s "$list" ] || sleep "$INTERVAL"
  done
}

case $MODE in
  once) sweep "$@" ;;
  watch)
    [ $# -eq 1 ] || usage
    watch "$1"
    ;;
  *) usage ;;
esac
