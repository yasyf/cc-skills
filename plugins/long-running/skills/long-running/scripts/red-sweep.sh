#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: red-sweep.sh <list-file>

Sweeps every pull request number in <list-file>, one per line - the same list
label-watch.sh watches - and prints one line per head the first time it is seen
red, conflicting, or unmergeable and older than the age threshold:

  STALLED-RED <pr> <sha> <state> head <age>m old: <title>

Runs forever, re-reading the list file each pass so entries label-watch.sh
removes drop out on their own. A head prints once; a later push (a new sha)
prints again. Never edits the list file.

  RED_SWEEP_REPO           owner/name, default the checkout's origin
  RED_SWEEP_CHECKOUT       local clone, default $PWD
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
STATE=${RED_SWEEP_STATE:-$HOME/.claude/scratch/red-sweep/seen.txt}
HOLD_LIST=${RED_SWEEP_HOLD_LIST:-}
AGE_MINUTES=${RED_SWEEP_AGE_MINUTES:-20}
INTERVAL=${RED_SWEEP_INTERVAL:-600}

mkdir -p "$(dirname "$STATE")"
touch "$STATE"

held() {
  [ -n "$HOLD_LIST" ] && [ -f "$HOLD_LIST" ] && grep -qx "$1" "$HOLD_LIST"
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
  echo "$key" >>"$STATE"
  short=$(printf %.8s "$sha")
  echo "STALLED-RED #$n $short $status/$state head ${age}m old: $title"
}

sweep() {
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
