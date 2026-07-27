#!/usr/bin/env bash
# Resolves open-pr's per-repo cache directory and reports the style card's freshness.
#
#   pr-cache.sh path   <owner/repo> [--host H]   print the cache dir, creating it
#   pr-cache.sh status <owner/repo> [--host H]   print one of: missing | stale:<reason> | fresh
#   pr-cache.sh clear  <owner/repo> [--host H]   remove the repo's cache dir
#
# The dir is keyed by host so an enterprise owner/repo never collides with the
# public one, and holds style.md plus pr/<number>.json.
set -euo pipefail

SCHEMA_VERSION=1
DRIFT_LIMIT=25
AGE_LIMIT_DAYS=90

# ${CLAUDE_PLUGIN_ROOT} is wiped on plugin update, so cached cards live outside it.
DATA_DIR="${CLAUDE_PLUGIN_DATA:-${XDG_CACHE_HOME:-$HOME/.cache}/open-pr}"

usage() {
  cat >&2 <<'EOF'
usage: pr-cache.sh path|status|clear <owner/repo> [--host HOST]

  path    print the per-repo cache dir, creating it
  status  print exactly one of: missing | stale:schema | stale:drift | stale:age | fresh
  clear   remove the per-repo cache dir
EOF
  exit 2
}

command -v gh >/dev/null 2>&1 || {
  echo "pr-cache.sh: gh is required but not installed — https://cli.github.com" >&2
  exit 1
}

[ $# -ge 2 ] || usage
CMD=$1
SLUG=$2
shift 2

HOST_OPT=""
while [ $# -gt 0 ]; do
  case $1 in
    --host)
      [ $# -ge 2 ] || usage
      HOST_OPT=$2
      shift 2
      ;;
    --host=*)
      HOST_OPT=${1#--host=}
      shift
      ;;
    *) usage ;;
  esac
done

[[ $SLUG =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || usage
# clear runs rm -rf under DATA_DIR; a dotted segment must never escape it.
[[ $SLUG != *..* ]] || usage

detect_host() {
  local url
  url=$(git remote get-url origin 2>/dev/null) || return 0
  case $url in
    *://*)
      url=${url#*://}
      url=${url#*@}
      printf '%s' "${url%%/*}"
      ;;
    *@*:*)
      url=${url#*@}
      printf '%s' "${url%%:*}"
      ;;
  esac
}

HOST=${HOST_OPT:-$(detect_host)}
HOST=${HOST:-github.com}
[[ $HOST =~ ^[A-Za-z0-9.:-]+$ ]] || usage
[[ $HOST != *..* ]] || usage

REPO_DIR="$DATA_DIR/repos/$HOST/$SLUG"
CARD="$REPO_DIR/style.md"

frontmatter() {
  awk '
    { sub(/\r$/, "") }
    NR == 1 { if ($0 != "---") exit; next }
    $0 == "---" { exit }
    { print }
  ' "$1"
}

# First value for KEY in $FM, accepting it nested under sample: or flattened.
fm_get() {
  awk -v key="$1" '
    {
      line = $0
      sub(/\r$/, "", line)
      sub(/^[[:space:]]+/, "", line)
      sub(/^sample\./, "", line)
      idx = index(line, ":")
      if (idx == 0) next
      if (substr(line, 1, idx - 1) != key) next
      v = substr(line, idx + 1)
      sub(/^[[:space:]]+/, "", v)
      sub(/[[:space:]]+$/, "", v)
      gsub(/^["\047]|["\047]$/, "", v)
      print v
      exit
    }
  ' <<<"$FM"
}

# BSD date (macOS) and GNU date (Linux) take incompatible flags; probe once.
if date -u -d '2020-01-01T00:00:00Z' +%s >/dev/null 2>&1; then
  to_epoch() { date -u -d "$1" +%s; }
else
  to_epoch() { date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$1" +%s; }
fi

is_number() { [[ $1 =~ ^[0-9]+$ ]]; }

cmd_path() {
  mkdir -p "$REPO_DIR/pr"
  printf '%s\n' "$REPO_DIR"
}

cmd_clear() {
  if [ -d "$REPO_DIR" ]; then
    rm -rf "$REPO_DIR"
    echo "pr-cache.sh: removed $REPO_DIR" >&2
  else
    echo "pr-cache.sh: nothing to clear at $REPO_DIR" >&2
  fi
}

cmd_status() {
  [ -r "$CARD" ] || {
    echo missing
    return 0
  }
  FM=$(frontmatter "$CARD")
  [ -n "$FM" ] || {
    echo missing
    return 0
  }

  [ "$(fm_get schema)" = "$SCHEMA_VERSION" ] || {
    echo "stale:schema"
    return 0
  }

  local newest remote
  newest=$(fm_get newest_number)
  if is_number "$newest"; then
    remote=$(gh pr list --repo "$HOST/$SLUG" --state merged --limit 1 --json number \
      --jq '.[0].number' 2>/dev/null) || remote=""
    if is_number "$remote"; then
      if [ $((remote - newest)) -gt "$DRIFT_LIMIT" ]; then
        echo "stale:drift"
        return 0
      fi
    else
      echo "pr-cache.sh: drift check unavailable (gh pr list failed); using the age check only" >&2
    fi
  fi

  local generated epoch
  generated=$(fm_get generated_at)
  epoch=$(to_epoch "$generated" 2>/dev/null) || epoch=""
  if [ -z "$epoch" ] || [ $(($(date -u +%s) - epoch)) -gt $((AGE_LIMIT_DAYS * 86400)) ]; then
    echo "stale:age"
    return 0
  fi

  echo fresh
}

case $CMD in
  path) cmd_path ;;
  status) cmd_status ;;
  clear) cmd_clear ;;
  *) usage ;;
esac
