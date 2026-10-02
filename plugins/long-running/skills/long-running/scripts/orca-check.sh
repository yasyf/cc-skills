#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: orca-check.sh [--peek] [-- <orca check args>...]
       orca-check.sh --stale [--inbox <inbox file>]

Runs one blocking `orca orchestration check --wait` that wakes on worker_done,
escalation, or question, and prints each message of the delivered batch
except heartbeats, one per line, then the delivery to acknowledge:

  <msg id> <type> <lane> <subject>: <body on one line>
  delivery <delivery id> heartbeats=<n>

A wait that ends empty prints `timeout`. --peek prints unread messages without
waiting or marking them read. Arguments after -- go to `orca orchestration check`,
such as --terminal <handle>. To acknowledge a previous terminal batch, pass
--ack <delivery-id> after --; a message id acknowledges nothing. The Run inbox
belongs to desk-runner.py; no other loop may check --run with --wait or --ack.

<lane> is the lane whose orca-launch.sh receipt names the sender's terminal,
else the sender's handle. A lost connection, or a runtime_unavailable error,
retries once after ORCA_CHECK_RETRY_SECONDS, then prints `connection-lost`, exit
1, so the caller reads its inbox file again. Any other Orca error prints
`error <code>: <message>`, exit 1.

--stale reads every receipt's dispatch and peeks its worker's terminal inbox,
printing one line per message nobody will read in time:

  STALE <lane> <age>m unread <msg id>       in progress, unread for ORCA_CHECK_STALE_MINUTES or more
  STALE <lane> <age>m <status> <msg id>     unread by a completed or failed dispatch
  STALE <lane> <age>m <status> R<n>         an inbox line past the cursor for that dispatch

  skip <lane>: no receipt json             a terminal receipt without <lane>.json

<age> counts from the message, or from the dispatch's completion for an inbox
line. --inbox names the desk's inbox file; its cursor is <inbox file>.cursor.

  ORCA_CHECK_STALE_MINUTES  unread age that flags an in-progress dispatch, default 10
  ORCA_CHECK_TIMEOUT_MS     longest wait, default 60000, so the caller reads its inbox file every minute
  ORCA_CHECK_STATE          orca-launch.sh receipt directory, default ~/.claude/scratch/orca-launch/<run>
  ORCA_CHECK_RETRY_SECONDS  wait before a retry, default 30
EOF
  exit 2
}

PEEK='' STALE='' INBOX=''
while [ $# -gt 0 ]; do
  case $1 in
    --peek) PEEK=1; shift ;;
    --stale) STALE=1; shift ;;
    --inbox) [ $# -ge 2 ] || usage; INBOX=$2; shift 2 ;;
    --) shift; break ;;
    *) usage ;;
  esac
done
[ -z "$INBOX" ] || [ -n "$STALE" ] || usage
TIMEOUT=${ORCA_CHECK_TIMEOUT_MS:-60000}
RETRY=${ORCA_CHECK_RETRY_SECONDS:-30}

orca_json() {
  OUT=$(orca "$@" --json 2>/dev/null) || true
  printf '%s' "$OUT" | jq -e '.ok' >/dev/null 2>&1 && return
  printf '%s' "$OUT" | jq -r '"error \(.error.code): \(.error.message)"' 2>/dev/null || echo "error $1 $2: $OUT"
  exit 1
}

if [ -n "$STALE" ]; then
  STATE=${ORCA_CHECK_STATE:-$HOME/.claude/scratch/orca-launch/$ORCA_LAUNCH_RUN}
  CURSOR=0
  [ -z "$INBOX" ] || CURSOR=$(cat "$INBOX.cursor" 2>/dev/null || echo 0)
  for receipt in "$STATE"/*.terminal; do
    [ -e "$receipt" ] || continue
    lane=$(basename "$receipt" .terminal)
    [ -e "$STATE/$lane.json" ] || { echo "skip $lane: no receipt json"; continue; }
    orca_json orchestration worker-show --dispatch "$(jq -r '.result.dispatchId' "$STATE/$lane.json")"
    SHOW=$OUT
    orca_json orchestration check --terminal "$(cat "$receipt")" --peek
    ROUTES=''
    [ -z "$INBOX" ] || ROUTES=$(awk -v lane="$lane:" -v cursor="$CURSOR" '$3 == lane && substr($1, 2) + 0 > cursor { print $1 }' "$INBOX")
    printf '%s' "$OUT" | jq -r --arg lane "$lane" --arg routes "$ROUTES" --argjson show "$SHOW" --argjson minutes "${ORCA_CHECK_STALE_MINUTES:-10}" '
      def age: (now - (sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601)) / 60 | floor;
      $show.result.dispatch as $d
      | if $d.status == "completed" or $d.status == "failed" then
          (.result.messages // [])[] | "STALE \($lane) \(.created_at | age)m \($d.status) \(.id)"
        else
          (.result.messages // [])[] | (.created_at | age) as $age | select($age >= $minutes)
          | "STALE \($lane) \($age)m unread \(.id)"
        end,
        if $d.status == "completed" or $d.status == "failed" then
          $routes | split("\n")[] | select(. != "") | "STALE \($lane) \($d.completedAt | age)m \($d.status) \(.)"
        else empty end'
  done
  exit 0
fi

if [ -n "$PEEK" ]; then
  set -- --peek "$@"
else
  set -- --wait --types worker_done,escalation,question --timeout-ms "$TIMEOUT" "$@"
fi

attempt=0
while :; do
  attempt=$((attempt + 1))
  OUT=$(orca orchestration check "$@" --json 2>/dev/null) || true
  if printf '%s' "$OUT" | jq -e '.ok == false and .error.code != "runtime_unavailable"' >/dev/null 2>&1; then
    printf '%s' "$OUT" | jq -r '"error \(.error.code): \(.error.message)"'
    exit 1
  fi
  printf '%s' "$OUT" | jq -e '.ok and (.result.connectionLost | not)' >/dev/null 2>&1 && break
  [ "$attempt" -lt 2 ] || { echo connection-lost; exit 1; }
  sleep "$RETRY"
done

STATE=${ORCA_CHECK_STATE:-$HOME/.claude/scratch/orca-launch/$(printf '%s' "$OUT" | jq -r '.result.runId')}
LANES={}
for receipt in "$STATE"/*.terminal; do
  [ -e "$receipt" ] || continue
  LANES=$(printf '%s' "$LANES" | jq -c --arg handle "$(cat "$receipt")" --arg lane "$(basename "$receipt" .terminal)" '. + {($handle): $lane}')
done

printf '%s' "$OUT" | jq -r --argjson lanes "$LANES" '
  .result as $r
  | ($r.messages // []) as $all
  | ($all | map(select(.type != "heartbeat"))[]
      | "\(.id) \(.type) \($lanes[.from_handle] // .from_handle) \(.subject): \(.body // "" | gsub("\\s+"; " "))"),
    if $r.deliveryId then "delivery \($r.deliveryId) heartbeats=\($all | map(select(.type == "heartbeat")) | length)"
    elif ($all | length) == 0 and ($r.timedOut // false) then "timeout"
    else empty end'
