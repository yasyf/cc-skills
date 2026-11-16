#!/usr/bin/env bash
set -euo pipefail
binary=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
releases=${2:-10}
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
git fetch -q --depth=1 origin "+refs/tags/v*:refs/tags/v*"
initialize='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"codex-ci","version":"0"}}}'
failed=0
hooks=0
servers=0
for tag in $(git tag -l 'v*' --sort=-version:refname | head -n "$releases"); do
  plugin="$root/$tag/plugins/codex"
  config="$root/$tag/config"
  mkdir -p "$config/plugins" "$root/$tag/home"
  git archive "$tag" plugins/codex | tar -x -C "$root/$tag" -f -
  mkdir -p "$plugin/bin"
  rm -f "$plugin/bin/codex-ask"
  ln -s "$binary" "$plugin/bin/codex-ask"
  jq -n --arg path "$plugin" '{plugins: {"codex@skills": [{installPath: $path, lastUpdated: "2026-01-01T00:00:00Z"}]}}' >"$config/plugins/installed_plugins.json"
  run=(env HOME="$root/$tag/home" CLAUDE_CONFIG_DIR="$config" CLAUDE_PLUGIN_ROOT="$plugin" PATH="$plugin/bin:$PATH")
  if [[ -f "$plugin/hooks/hooks.json" ]]; then
    while IFS= read -r command; do
      [[ $command == *'${CLAUDE_PLUGIN_ROOT}/bin/codex-ask'* ]] || continue
      hooks=$((hooks + 1))
      if ! output=$("${run[@]}" bash -c "$command" </dev/null 2>&1); then
        echo "$tag: hook command exits non-zero on this codex-ask: $command" >&2
        [[ -n $output ]] && echo "  $output" >&2
        failed=1
      fi
    done < <(jq -r '.hooks[][].hooks[].command' "$plugin/hooks/hooks.json")
  fi
  for manifest in "$plugin/.claude-plugin/plugin.json" "$plugin/.mcp.json"; do
    [[ -f $manifest ]] || continue
    for name in $(jq -r '(.mcpServers // {}) | keys[]' "$manifest"); do
      servers=$((servers + 1))
      argv=()
      while IFS= read -r -d '' word; do
        argv+=("$word")
      done < <(jq -j --arg name "$name" --arg root "$plugin" '.mcpServers[$name] | [.command, (.args // [])[]] | map(gsub("\\$\\{CLAUDE_PLUGIN_ROOT\\}"; $root) + "\u0000") | add' "$manifest")
      reply=$(printf '%s\n' "$initialize" | "${run[@]}" perl -e 'alarm 20; exec @ARGV' "${argv[@]}" 2>"$root/stderr" | head -n 1 || true)
      if ! jq -e '.result.protocolVersion' >/dev/null 2>&1 <<<"$reply"; then
        echo "$tag: MCP server $name answers no initialize on this codex-ask: ${argv[*]: -2}" >&2
        sed 's/^/  /' "$root/stderr" >&2
        failed=1
      fi
    done
  done
done
[[ $failed == 0 ]] || exit 1
echo "codex-ask runs all $hooks hook commands and answers initialize on all $servers MCP servers of the last $releases releases"
