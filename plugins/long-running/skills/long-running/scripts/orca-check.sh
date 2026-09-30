#!/bin/sh
set -eu

usage() {
  cat >&2 <<'EOF'
usage: orca-check.sh [--ack <delivery-id>] [--peek] [-- <orca check args>...]

Runs one blocking `orca orchestration check --wait` that wakes on worker_done,
escalation, or question, and prints each message of the delivered batch
except heartbeats, one per line, then the delivery to acknowledge:

  <msg id> <type> <lane> <subject>: <body on one line>
  delivery <delivery id> heartbeats=<n>

A wait that ends empty prints `timeout`. --ack <delivery-id> acknowledges the
previous batch before waiting; pass result.deliveryId, since a message id
acknowledges nothing and the Run replays an unacknowledged batch. --peek prints
the unread messages without waiting or marking them read. Arguments after --
go to `orca orchestration check`, such as --terminal <handle> or --run <id>.

<lane> is the lane whose orca-launch.sh receipt names the sender's terminal,
else the sender's handle. A lost connection, or a runtime_unavailable error,
retries once after ORCA_CHECK_RETRY_SECONDS, then prints `connection-lost`, exit
1, so the caller reads its inbox file again. Any other Orca error prints
`error <code>: <message>`, exit 1.

  ORCA_CHECK_TIMEOUT_MS     longest wait, default 60000, so the caller reads its inbox file every minute
  ORCA_CHECK_STATE          orca-launch.sh receipt directory, default ~/.claude/scratch/orca-launch/<run>
  ORCA_CHECK_RETRY_SECONDS  wait before a retry, default 30
EOF
  exit 2
}

ACK='' PEEK=''
while [ $# -gt 0 ]; do
  case $1 in
    --ack) [ $# -ge 2 ] || usage; ACK=$2; shift 2 ;;
    --peek) PEEK=1; shift ;;
    --) shift; break ;;
    *) usage ;;
  esac
done
TIMEOUT=${ORCA_CHECK_TIMEOUT_MS:-60000}
RETRY=${ORCA_CHECK_RETRY_SECONDS:-30}

if [ -n "$PEEK" ]; then
  set -- --peek "$@"
else
  set -- --wait --types worker_done,escalation,question --timeout-ms "$TIMEOUT" ${ACK:+--ack "$ACK"} "$@"
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
