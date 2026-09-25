#!/usr/bin/env bash
# PreToolUse hook on Bash: inside open-pr:pr-watcher, refuses any command that
# runs pr-poll.sh. The watcher's Monitor is the only poller; a Bash poll blocks
# the agent while the Monitor's DONE line waits unread.
set -euo pipefail

input=$(cat)
[ "$(jq -r '.agent_type // ""' <<<"$input")" = open-pr:pr-watcher ] || exit 0
jq -e '(.tool_input.command // "") | contains("pr-poll.sh")' >/dev/null <<<"$input" || exit 0

echo "pr-poll.sh runs only under Monitor, on the command from poll:. The armed Monitor already polls this state file; handle its next line instead of polling from Bash." >&2
exit 2
