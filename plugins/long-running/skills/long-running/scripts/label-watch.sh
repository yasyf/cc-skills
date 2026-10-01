#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: label-watch.sh once <pr>...
       label-watch.sh watch <list-file>

Enqueues each stack whose every pull request passes the gate and prints one
line per pull request. A listed pull request brings in its stack: every open PR
below it down to the trunk and every open PR stacked above it. The largest bottom
prefix of a stack whose every PR passes goes into the Graphite merge queue as one
batch, through the API call gt merge makes; the PRs above it wait for their own
gates and are restacked by their lane once the prefix lands. A stack that forks
never goes in.

  <pr> SKIP <queue>               Graphite reads it queued or landed, the trunk carries
                                  its squash, or it closed
  <pr> SKIP labelled              it already carries the queue label
  <pr> SKIP covered-by #<top>     enqueueing #<top>, or the label on it, queues it too
  <pr> HELD                       it carries the hold label
  <pr> CONFLICT <sha> <files>     its head conflicts with, or shares no history with, the fresh trunk
  <pr> NOT-READY <sha> <reason>   base, mergeability, checks, or approval not there
                                  yet, conflicts-with #<queued pr> <files>,
                                  downstack #<pr> when a PR below it fails the gate or
                                  is queued, fork at #<pr> when two PRs stack on one,
                                  or held when LABEL_WATCH_HOLD lists it; above an
                                  enqueued prefix the line ends restack-after #<top>
  <pr> API-FAIL <read>            a GitHub, Graphite, or git fetch failed
  <pr> ENQUEUED <sha> [prefix]    the top PR of the passing prefix; Graphite took it
                                  and its whole downstack; prefix when open PRs stay
                                  above it, dry-run when LABEL_WATCH_DRY_RUN is set
  <pr> EVICTED <sha> <reason> <time>
                                  the queue dropped it; the gate runs on it again

watch re-gates every number in <list-file>, one per line, each interval until
the file is empty and every PR it saw queued or enqueued has closed. It deletes ENQUEUED and SKIP entries from the file, keeps
the rest, appends every other unheld PR of their stacks, and prints a timestamped line
only when a result changes. The PRs it saw queued or labelled stay conflict
bases on every sweep until they close. One ccx vcs pr status call per sweep
reads the list and those PRs; an eviction prints EVICTED once and puts the PR back
through the gate. A PR whose squash is on the trunk costs no read at all.

  LABEL_WATCH_APPROVERS  comma-separated logins that must approve the head sha
  LABEL_WATCH_REQUIRED   comma-separated checks that must have reported on the head sha
  LABEL_WATCH_REPO       owner/name, default the checkout's origin
  LABEL_WATCH_TRUNK      default the checkout's origin/HEAD
  LABEL_WATCH_CHECKOUT   local clone for the conflict check, default $PWD
  LABEL_WATCH_LABEL      queue label a PR may already carry, default merge
  LABEL_WATCH_GRAPHITE_API   default https://api.graphite.com/v1
  LABEL_WATCH_GRAPHITE_AUTH  gt's token file, default ~/.config/graphite/auth
  LABEL_WATCH_INTERVAL   seconds between watch sweeps, default 300
  LABEL_WATCH_DRY_RUN    set to print ENQUEUED without enqueueing
  LABEL_WATCH_HOLD       file of PR numbers, one per line, never enqueued, re-read each sweep
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
INTERVAL=${LABEL_WATCH_INTERVAL:-300}
REQUIRED=${LABEL_WATCH_REQUIRED:-}
DRY_RUN=${LABEL_WATCH_DRY_RUN:-}
GRAPHITE_API=${LABEL_WATCH_GRAPHITE_API:-https://api.graphite.com/v1}
GRAPHITE_AUTH=${LABEL_WATCH_GRAPHITE_AUTH:-$HOME/.config/graphite/auth}
HOLD=${LABEL_WATCH_HOLD:-}
HELD=
OWNER=${REPO%%/*}
TRUNK_REF=refs/label-watch/$TRUNK
PULL='"\(.head.sha) \(.base.ref) \(.mergeable) \(.mergeable_state) \(.head.ref) \([.labels[].name] | join(","))"'
PR='"\(.number) \(.head.sha) \(.head.ref) \(.base.ref) \([.labels[].name] | join(","))"'
QUEUED=
TRACKED=
NODES=
BRANCHES=

onto_trunk() {
  git -C "$CHECKOUT" -c user.name=label-watch -c user.email=label-watch@localhost \
    commit-tree "$1" -p "$TRUNK_REF" -p "$2" -m "trunk with #$3"
}

enqueue() {
  body=$(printf '%s\n' "$@" | jq -cs --arg owner "$OWNER" --arg name "${REPO#*/}" --arg trunk "$TRUNK" \
    '{repoOwner: $owner, repoName: $name, trunkBranchName: $trunk, prNumbers: .}')
  jq -r '"header = \"Authorization: token \(.authToken)\""' "$GRAPHITE_AUTH" \
    | curl -fsS -K - -X POST -H 'Content-Type: application/json' -d "$body" "$GRAPHITE_API/graphite/merge" >/dev/null
}

add() {
  eval "seen=\${ref_$1-}"
  [ -z "$seen" ] || return 0
  NODES="$NODES $1"
  BRANCHES=$(printf '%s\n%s %s' "$BRANCHES" "$3" "$1")
  eval "sha_$1=\$2 ref_$1=\$3 labels_$1=\$4 kids_$1="
}

link() {
  eval "linked=\${parent_$1+x}"
  [ -z "$linked" ] || return 0
  eval "parent_$1=\$2 kids_$2=\"\$kids_$2 \$1\""
}

downstack() {
  child=$1
  branch=$2
  while [ "$branch" != "$TRUNK" ]; do
    eval "linked=\${parent_$child+x}"
    [ -z "$linked" ] || return 0
    known=$(printf '%s\n' "$BRANCHES" | awk -v b="$branch" '$1 == b { print $2; exit }')
    if [ -n "$known" ]; then
      link "$child" "$known"
      return 0
    fi
    below=$(gh api "repos/$REPO/pulls?state=open&head=$OWNER:$branch" --jq ".[:1][] | $PR") || return 1
    [ -n "$below" ] || break
    read -r known psha pref branch plabels <<EOF
$below
EOF
    add "$known" "$psha" "$pref" "$plabels"
    link "$child" "$known"
    child=$known
  done
  eval "parent_$child="
}

upstack() {
  todo=$1
  while set -- $todo; [ $# -gt 0 ]; do
    up=$1
    shift
    todo=$*
    eval "walked=\${up_$up-} branch=\$ref_$up"
    [ -z "$walked" ] || continue
    eval "up_$up=1"
    above=$(gh api "repos/$REPO/pulls?state=open&base=$branch&per_page=100" --jq ".[] | $PR") || return 1
    while read -r kid ksha kref _ klabels; do
      [ -n "$kid" ] || continue
      add "$kid" "$ksha" "$kref" "$klabels"
      link "$kid" "$up"
      todo="$todo $kid"
    done <<EOF
$above
EOF
  done
}

expand() {
  eval "seen=\${ref_$1-}"
  if [ -z "$seen" ]; then
    eval "pull=\${pull_$1-}"
    [ -n "$pull" ] || pull=$(gh api "repos/$REPO/pulls/$1" --jq "$PULL") || return 1
    read -r sha base mergeable state ref labels <<EOF
$pull
EOF
    eval "pull_$1=\$pull"
    add "$1" "$sha" "$ref" "$labels"
    downstack "$1" "$base" || return 2
  fi
  upstack "$1" || return 2
}

ancestors() {
  a=$1
  while eval "a=\$parent_$a"; [ -n "$a" ]; do printf '%s ' "$a"; done
}

record() {
  while read -r v q; do
    [ -z "$v" ] || eval "queue_$v=\$q"
  done <<EOF
$(printf '%s' "$1" | jq -r '.[] | "\(.number) \(
    if .queue == "landed" then .queue
    elif .state != "OPEN" then "closed"
    elif .queue == "evicted" then "not queued"
    else .queue end)"')
EOF
}

gate() {
  n=$1
  below=$2
  eval "pull=\${pull_$n-}"
  if [ -z "$pull" ]; then
    pull=$(gh api "repos/$REPO/pulls/$n" --jq "$PULL") || {
      echo "$n API-FAIL pull"
      return 1
    }
  fi
  read -r sha base mergeable state ref labels <<EOF
$pull
EOF
  case ",$labels," in
    *,hold,*)
      echo "$n HELD"
      return 1
      ;;
  esac
  short=$(printf %.10s "$sha")

  git -C "$CHECKOUT" cat-file -e "$sha^{commit}" 2>/dev/null \
    || git -C "$CHECKOUT" fetch -q --no-prune --no-write-fetch-head origin "$sha" 2>/dev/null || {
    echo "$n API-FAIL head-fetch"
    return 1
  }
  rc=0
  merge=$(git -C "$CHECKOUT" merge-tree --write-tree --name-only --no-messages "$TRUNK_REF" "$sha" 2>/dev/null) || rc=$?
  case $rc in
    0) ;;
    1)
      echo "$n CONFLICT $short $(printf '%s\n' "$merge" | sed 1d | paste -sd ' ' -)"
      return 1
      ;;
    *)
      echo "$n CONFLICT $short no merge base with the trunk"
      return 1
      ;;
  esac

  if [ -n "$QUEUED" ]; then
    while read -r q onto; do
      case " $n $below " in *" $q "*) continue ;; esac
      rc=0
      ahead=$(git -C "$CHECKOUT" merge-tree --write-tree --name-only --no-messages "$onto" "$sha" 2>/dev/null) || rc=$?
      case $rc in
        0) ;;
        1)
          echo "$n NOT-READY $short conflicts-with #$q $(printf '%s\n' "$ahead" | sed 1d | paste -sd ' ' -)"
          return 1
          ;;
        *) continue ;;
      esac
    done <<EOF
$QUEUED
EOF
  fi

  case $base in
    graphite-base/*)
      echo "$n NOT-READY $short base $base"
      return 1
      ;;
  esac
  if [ "$mergeable" != true ]; then
    echo "$n NOT-READY $short mergeable $mergeable"
    return 1
  fi
  if [ "$base" = "$TRUNK" ] && [ "$state" != clean ]; then
    echo "$n NOT-READY $short $state"
    return 1
  fi

  statuses=$(gh api "repos/$REPO/commits/$sha/status" --jq '.statuses[] | "\(.state) \(.context)"') \
    && runs=$(gh api "repos/$REPO/commits/$sha/check-runs?per_page=100" --jq '.check_runs[] | "\(if .status == "completed" then .conclusion else "pending" end) \(.name)"') || {
    echo "$n API-FAIL checks"
    return 1
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
    return 1
  fi
  absent=$(printf '%s\n%s\n' "$statuses" "$runs" | awk -v need="$REQUIRED" '
    BEGIN { n = split(need, want, ",") }
    $0 != "" { sub(/^[^ ]+ /, ""); seen[$0] = 1 }
    END { for (i = 1; i <= n; i++) if (want[i] != "" && !(want[i] in seen)) out = out (out ? "," : "") want[i]; print out }')
  if [ -n "$absent" ]; then
    echo "$n NOT-READY $short no-run $absent"
    return 1
  fi

  reviews=$(gh api "repos/$REPO/pulls/$n/reviews?per_page=100") || {
    echo "$n API-FAIL reviews"
    return 1
  }
  missing=$(printf '%s' "$reviews" | jq -r --arg sha "$sha" --arg need "$APPROVERS" '
    [.[] | select(.commit_id == $sha and .state == "APPROVED") | .user.login] as $have
    | [$need | split(",")[] | select(IN($have[]) | not)] | join(",")')
  if [ -n "$missing" ]; then
    echo "$n NOT-READY $short awaiting $missing"
    return 1
  fi
  echo "$sha $merge"
}

climb() {
  eval "climbed_$1=1"
  order=
  todo=$1
  while set -- $todo; [ $# -gt 0 ]; do
    v=$1
    shift
    todo=$*
    order="$order $v"
    eval "p=\$parent_$v q=\$queue_$v labels=\$labels_$v todo=\"\$todo \$kids_$v\""
    pv=
    [ -z "$p" ] || eval "pv=\$verdict_$p pb=\$blocker_$p"
    blocker=
    eval "sha=\$sha_$v"
    if [ "$pv" = stop ] || [ "$pv" = wait ]; then
      verdict=stop blocker=$pb line="$v NOT-READY $(printf %.10s "$sha") downstack #$pb"
    elif case " $HELD " in *" $v "*) true ;; *) false ;; esac; then
      verdict=stop blocker=$v line="$v NOT-READY $(printf %.10s "$sha") held"
    elif [ "$q" != "not queued" ]; then
      verdict=wait blocker=$v line="$v SKIP $q"
    elif case ",$labels," in *",$LABEL,"*) true ;; *) false ;; esac; then
      verdict=through line="$v SKIP labelled"
    elif line=$(gate "$v" "$(ancestors "$v")"); then
      verdict=pass
      sleep 1
    else
      verdict=stop blocker=$v
      sleep 1
    fi
    eval "verdict_$v=\$verdict blocker_$v=\$blocker line_$v=\$line"
  done

  fork=
  tip=
  for v in $order; do
    eval "set -- \$kids_$v"
    if [ $# -gt 1 ]; then
      fork="fork at #$v"
      break
    fi
    [ $# -gt 0 ] || tip=$v
  done
  prefix=
  top=
  if [ -z "$fork" ]; then
    for v in $order; do
      eval "verdict=\$verdict_$v"
      case $verdict in pass | through) ;; *) break ;; esac
      prefix="$prefix $v" top=$v
    done
  fi

  failed=
  eval "tv=\${verdict_$top-}"
  if [ "$tv" = pass ]; then
    eval "set -- \$line_$top"
    short=$(printf %.10s "$1")
    [ "$top" = "$tip" ] || short="$short prefix"
    if [ -n "$DRY_RUN" ]; then
      eval "line_$top=\"\$top ENQUEUED \$short dry-run\""
    elif enqueue $prefix; then
      eval "line_$top=\"\$top ENQUEUED \$short\""
      QUEUED=$(printf '%s\n%s %s' "$QUEUED" "$top" "$(onto_trunk "$2" "$1" "$top")" | sed '/^$/d')
    else
      eval "line_$top=\"\$top API-FAIL enqueue\""
      failed="API-FAIL enqueue"
    fi
  fi

  for v in $order; do
    eval "verdict=\$verdict_$v sha=\$sha_$v"
    case " $prefix " in
      *" $v "*)
        [ "$verdict" = pass ] && [ "$v" != "$top" ] || continue
        if [ -n "$failed" ]; then
          eval "line_$v=\"\$v \$failed\""
        else
          eval "line_$v=\"\$v SKIP covered-by #\$top\""
        fi
        ;;
      *)
        if [ -n "$fork" ]; then
          [ "$verdict" != pass ] || eval "line_$v=\"\$v NOT-READY \$(printf %.10s \"\$sha\") \$fork\""
        elif [ "$tv" = pass ] && [ -z "$failed" ]; then
          eval "line_$v=\"\$line_$v restack-after #\$top\""
        fi
        ;;
    esac
  done
  for v in $order; do eval "printf '%s\n' \"\$line_$v\""; done
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
  landed=$(git -C "$CHECKOUT" log --format=%s -n 2000 "$TRUNK_REF" | sed -n 's/.*(#\([0-9][0-9]*\))$/\1/p')
  open=
  for n; do
    if printf '%s\n' "$landed" | grep -qx "$n"; then
      echo "$n SKIP landed"
    else
      open="$open $n"
    fi
  done
  set -- $open
  tracked=
  for t in $TRACKED; do
    case " $* " in *" $t "*) continue ;; esac
    printf '%s\n' "$landed" | grep -qx "$t" || tracked="$tracked $t"
  done
  [ $# -gt 0 ] || [ -n "$tracked" ] || return 0
  if ! queues=$(ccx vcs pr status --json -R "$REPO" "$@" $tracked); then
    for n; do echo "$n API-FAIL queue"; done
    return
  fi
  record "$queues"
  while IFS='	' read -r t why at; do
    [ -n "$t" ] || continue
    if pull=$(gh api "repos/$REPO/pulls/$t" --jq "$PULL"); then
      eval "pull_$t=\$pull"
      echo "$t EVICTED $(printf %.10s "$pull") $why $at"
    fi
    case " $* " in *" $t "*) ;; *) set -- "$@" "$t" ;; esac
  done <<EOF
$(printf '%s' "$queues" | jq -r '.[] | select(.queue == "evicted" and .state == "OPEN") | [.number, .evicted, .evicted_at] | @tsv')
EOF
  [ -z "$HOLD" ] || HELD=$(tr -s ' \n' ' ' <"$HOLD")
  for e; do
    eval "q=\$queue_$e"
    [ "$q" = "not queued" ] || continue
    rc=0
    expand "$e" || rc=$?
    case $rc in
      1) eval "line_$e=\"\$e API-FAIL pull\"" ;;
      2)
        for n; do echo "$n API-FAIL stack"; done
        return
        ;;
    esac
  done
  extra=
  for v in $NODES; do
    case " $* $tracked " in *" $v "*) ;; *) extra="$extra $v" ;; esac
  done
  if [ -n "$extra" ]; then
    if ! more=$(ccx vcs pr status --json -R "$REPO" $extra); then
      for n; do echo "$n API-FAIL queue"; done
      return
    fi
    record "$more"
    queues=$(printf '%s\n%s' "$queues" "$more" | jq -s add)
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
  for e; do
    eval "seen=\${ref_$e-}"
    if [ -z "$seen" ]; then
      eval "line=\${line_$e-\"\$e SKIP \$queue_$e\"}"
      echo "$line"
      continue
    fi
    root=$e
    while eval "up=\$parent_$root"; [ -n "$up" ]; do root=$up; done
    eval "climbed=\${climbed_$root-}"
    [ -n "$climbed" ] || climb "$root"
  done
  printf '%s' "$queues" | jq -r --arg tracked "$tracked" '.[]
    | select(.state == "OPEN" and (.number | tostring | IN($tracked | split(" ")[]))) | .number' | while read -r n; do
    eval "seen=\${ref_$n-}"
    [ -n "$seen" ] || echo "$n TRACKED"
  done
}

watch() {
  list=$1
  while [ -s "$list" ] || [ -n "$TRACKED" ]; do
    set -- $(awk '$1 != "" && !seen[$1]++ { print $1 }' "$list")
    [ $# -gt 0 ] || [ -n "$TRACKED" ] || break
    results=$(sweep "$@")
    TRACKED=
    while read -r line; do
      [ -n "$line" ] || continue
      n=${line%% *}
      rest=${line#* }
      case $rest in
        TRACKED | ENQUEUED* | "SKIP queued" | "SKIP labelled") TRACKED="$TRACKED $n" ;;
      esac
      [ "$rest" != TRACKED ] || continue
      case $rest in
        EVICTED*)
          eval "seen=\${evicted_$n-}"
          [ "$line" = "$seen" ] || echo "$(date -u +%H:%M) $line"
          eval "evicted_$n=\$line"
          grep -qx "$n" "$list" || echo "$n" >>"$list"
          continue
          ;;
      esac
      eval "last=\${last_$n-}"
      [ "$line" = "$last" ] || echo "$(date -u +%H:%M) $line"
      eval "last_$n=\$line"
      case ${rest%% *} in
        ENQUEUED | SKIP) awk -v n="$n" '$1 != n' "$list" >"$list.tmp" && mv "$list.tmp" "$list" ;;
        *) case $rest in *" held") ;; *) grep -qx "$n" "$list" || echo "$n" >>"$list" ;; esac ;;
      esac
    done <<EOF
$results
EOF
    [ ! -s "$list" ] && [ -z "$TRACKED" ] || sleep "$INTERVAL"
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
