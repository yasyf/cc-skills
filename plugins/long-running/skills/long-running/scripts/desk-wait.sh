#!/bin/sh
set -eu

usage() {
  echo 'usage: desk-wait.sh <seconds> <file>=<cursor-file>...' >&2
  exit 2
}

[ $# -ge 2 ] || usage
case $1 in
  ''|*[!0-9]*) usage ;;
esac
deadline=$(expr "$(date +%s)" + "$1")
shift
for source in "$@"; do
  case $source in
    ?*=?*) ;;
    *) usage ;;
  esac
done

while :; do
  changed=0
  for source in "$@"; do
    watched_file=${source%%=*}
    cursor_file=${source#*=}
    cursor=0
    if [ -f "$cursor_file" ]; then
      cursor=$(cat "$cursor_file")
    fi
    lines=0
    if [ -f "$watched_file" ]; then
      lines=$(wc -l < "$watched_file" | tr -d '[:space:]')
    fi
    if [ "$lines" -gt "$cursor" ]; then
      awk -v first="$cursor" -v last="$lines" '
        NR > first && NR <= last && NF { print substr($0, 1, 2500) }
      ' "$watched_file"
      printf '%s\n' "$lines" > "$cursor_file"
      changed=1
    fi
  done
  [ "$changed" -eq 0 ] || exit 0
  remaining=$((deadline - $(date +%s)))
  [ "$remaining" -gt 0 ] || break
  if [ "$remaining" -gt 2 ]; then
    sleep 2
  else
    sleep "$remaining"
  fi
done

LC_ALL=C TZ=America/Los_Angeles date '+QUIET %I:%M %p' | sed 's/QUIET 0/QUIET /'
