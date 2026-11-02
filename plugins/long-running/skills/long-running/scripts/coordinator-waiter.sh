#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <run_id> <out-dir>" >&2
  exit 2
fi

run=$1 dir=$2
mkdir -p "$dir"
out=$dir/batches.jsonl
seen=$dir/seen.txt
touch "$out" "$seen"

while true; do
  # A failed or timed-out check must not end the loop: once no waiter is live, Orca types its mail into the coordinator's pane.
  batch=$(orca orchestration check --run "$run" --wait --timeout-ms 600000 --full --json 2>/dev/null) || true
  fresh=$(jq -c --rawfile seen "$seen" \
    '($seen | split("\n")) as $ids | .result.messages // [] | .[] | select(.id and (.id | IN($ids[]) | not))' \
    <<<"$batch" 2>/dev/null) || true
  if [ -n "$fresh" ]; then
    jq -c 'select(.type != "heartbeat") | {at: .created_at, from: .from_handle, type, subject, body: ((.body // "")[:1500])}' \
      <<<"$fresh" >>"$out"
    jq -r .id <<<"$fresh" >>"$seen"
  fi
  delivery=$(jq -r '.result.deliveryId // empty' <<<"$batch" 2>/dev/null) || true
  if [ -n "$delivery" ]; then
    orca orchestration check --run "$run" --ack "$delivery" --full --json >/dev/null 2>&1 || true
  fi
  if [ "$(jq -r '.error.code // empty' <<<"$batch" 2>/dev/null)" = waiter_exists ]; then
    sleep 5
    continue
  fi
  sleep 0.2
done
