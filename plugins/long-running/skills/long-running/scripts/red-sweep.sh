#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: red-sweep.sh <list-file>

Sweeps every pull request number in <list-file>, one per line - the same list
label-watch.sh watches - and prints one line per head the first time it is seen
red, conflicting, or unmergeable and older than the age threshold:

  STALLED-RED <pr> <sha> <state> head <age>m old: <title>

A red head whose every failing status is a Buildkite build, each of whose failed
steps also failed on the trunk's latest finished build of that pipeline, prints
instead, once per head while the trunk stays red:

  DEV-RED <pr> <sha> <step>,<step>

That red is the trunk's, not the PR's; hold it rather than route it. Once the
trunk's step passes, the head prints STALLED-RED if it is still red.

Runs forever, re-reading the list file each pass so entries label-watch.sh
removes drop out on their own. A head prints once; a later push (a new sha)
prints again. Never edits the list file.

  RED_SWEEP_REPO           owner/name, default the checkout's origin
  RED_SWEEP_CHECKOUT       local clone, default $PWD
  RED_SWEEP_TRUNK          trunk branch, default the checkout's origin/HEAD
  RED_SWEEP_STATE          seen-heads file, default ~/.claude/scratch/red-sweep/seen.txt
  RED_SWEEP_HOLD_LIST      file of PR numbers to skip, one per line, optional
  RED_SWEEP_AGE_MINUTES    minimum head age before it counts as stalled, default 20
  RED_SWEEP_INTERVAL       seconds between sweeps, default 600
EOF
  exit 2
}

[ $# -eq 1 ] || usage
LIST=$1

CHECKOUT=${RED_SWEEP_CHECKOUT:-$PWD}
REPO=${RED_SWEEP_REPO:-$(git -C "$CHECKOUT" remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')}
TRUNK=${RED_SWEEP_TRUNK:-$(git -C "$CHECKOUT" symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##')}
STATE=${RED_SWEEP_STATE:-$HOME/.claude/scratch/red-sweep/seen.txt}
TRUNK_BUILDS=$(dirname "$STATE")/trunk-builds
HOLD_LIST=${RED_SWEEP_HOLD_LIST:-}
AGE_MINUTES=${RED_SWEEP_AGE_MINUTES:-20}
INTERVAL=${RED_SWEEP_INTERVAL:-600}

mkdir -p "$(dirname "$STATE")" "$TRUNK_BUILDS"
touch "$STATE"

held() {
  [ -n "$HOLD_LIST" ] && [ -f "$HOLD_LIST" ] && grep -qx "$1" "$HOLD_LIST"
}

failed_steps() {
  jq -r '[.jobs[] | select(.type == "script" and .state == "failed" and (.soft_failed | not)) | (.step_key // .name)] | unique | .[]'
}

trunk_failed_steps() {
  cache=$TRUNK_BUILDS/$1.json
  [ -s "$cache" ] || bk api "/pipelines/$1/builds?branch=$TRUNK&state%5B%5D=passed&state%5B%5D=failed&per_page=1" | jq '.[0]' >"$cache"
  failed_steps <"$cache"
}

dev_red() {
  urls=$(gh api "repos/$REPO/commits/$1/status" --jq '.statuses[] | select(.state == "failure") | .target_url // ""' | sort -u) || return 1
  [ -n "$urls" ] || return 1
  ! printf '%s\n' "$urls" | grep -qv '^https://buildkite\.com/[^/]*/[^/]*/builds/[0-9]' || return 1
  steps=
  for url in $urls; do
    pipeline=$(printf %s "$url" | sed -E 's#^https://buildkite\.com/[^/]+/([^/]+)/builds/[0-9]+.*#\1#')
    build=$(printf %s "$url" | sed -E 's#^https://buildkite\.com/[^/]+/[^/]+/builds/([0-9]+).*#\1#')
    mine=$(bk api "/pipelines/$pipeline/builds/$build" | failed_steps) || return 1
    [ -n "$mine" ] || return 1
    trunk=$(trunk_failed_steps "$pipeline") || return 1
    own=$(printf '%s\n' "$mine" | while read -r step; do printf '%s\n' "$trunk" | grep -qxF "$step" || echo "$step"; done)
    [ -z "$own" ] || return 1
    steps="$steps$mine
"
  done
  printf %s "$steps" | sort -u | paste -sd, -
}

check_one() {
  n=$1
  held "$n" && return 0

  pull=$(gh api "repos/$REPO/pulls/$n" --jq '"\(.head.sha)\t\(.mergeable_state)\t\(.updated_at)\t\(.title)"') || return 0
  IFS=$(printf '\t') read -r sha state updated title <<EOF
$pull
EOF

  status=$(gh api "repos/$REPO/commits/$sha/status" --jq '.state' 2>/dev/null) || status=unknown
  case "$status/$state" in
    failure/* | */dirty | */unstable | */blocked) ;;
    *) return 0 ;;
  esac

  now=$(date -u +%s)
  then=$(date -j -u -f %Y-%m-%dT%H:%M:%SZ "$updated" +%s 2>/dev/null || date -u -d "$updated" +%s)
  age=$(( (now - then) / 60 ))
  [ "$age" -ge "$AGE_MINUTES" ] || return 0

  key="$n $sha"
  grep -qxF "$key" "$STATE" && return 0
  short=$(printf %.8s "$sha")
  if [ "$status" = failure ] && steps=$(dev_red "$sha"); then
    grep -qxF "$key dev-red" "$STATE" && return 0
    echo "$key dev-red" >>"$STATE"
    echo "DEV-RED #$n $short $steps"
    return 0
  fi
  echo "$key" >>"$STATE"
  echo "STALLED-RED #$n $short $status/$state head ${age}m old: $title"
}

sweep() {
  rm -f "$TRUNK_BUILDS"/*.json
  [ -s "$LIST" ] || return 0
  while read -r n; do
    [ -n "$n" ] || continue
    check_one "$n"
  done <"$LIST"
}

while :; do
  sweep
  sleep "$INTERVAL"
done
