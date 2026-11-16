#!/usr/bin/env bash
set -euo pipefail
binary=$1
releases=${2:-10}
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/plugin/bin" "$root/home"
ln -s "$binary" "$root/plugin/bin/codex-ask"
git fetch -q --depth=1 origin "+refs/tags/v*:refs/tags/v*"
failed=0
checked=0
for tag in $(git tag -l 'v*' --sort=-version:refname | head -n "$releases"); do
  hooks=$(git show "$tag:plugins/codex/hooks/hooks.json" 2>/dev/null) || continue
  while IFS= read -r command; do
    [[ $command == *'${CLAUDE_PLUGIN_ROOT}/bin/codex-ask'* ]] || continue
    checked=$((checked + 1))
    if ! output=$(HOME="$root/home" CLAUDE_PLUGIN_ROOT="$root/plugin" bash -c "$command" </dev/null 2>&1); then
      echo "$tag: hook command exits non-zero on this codex-ask: $command" >&2
      [[ -n $output ]] && echo "  $output" >&2
      failed=1
    fi
  done < <(jq -r '.hooks[][].hooks[].command' <<<"$hooks")
done
[[ $failed == 0 ]] || exit 1
echo "codex-ask runs all $checked hook commands of the last $releases releases"
