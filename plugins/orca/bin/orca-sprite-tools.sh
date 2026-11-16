#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <prepared-task.json>" >&2
  exit 2
fi
task=$1
released=("ccx yasyf/cc-context checksums.txt" "cc-notes yasyf/cc-notes SHA256SUMS.txt ccn")
unfit=76

die() {
  echo "$*" >&2
  exit 1
}

forward=$(jq -er '[.forward.host, .forward.config, .projectRoot] | select(all(type == "string" and length > 0)) | @tsv' "$task") ||
  die "$task names no ssh forward host and config or no projectRoot, so no Sprite was reached"
IFS=$'\t' read -r host config root <<<"$forward"

words=()
for entry in "${released[@]}"; do
  read -r bin repo sums alias <<<"$entry"
  asset=${bin}_linux_amd64
  printed=$("$bin" --version 2>&1) || die "$bin --version failed on this Mac: ${printed##*$'\n'}, so no tool was installed"
  tag=${printed%% *}
  [[ $tag =~ ^v[0-9]+(\.[0-9]+)+$ ]] ||
    die "this Mac's $bin --version printed '$printed', not a release tag, so no tool was installed"
  release=https://github.com/$repo/releases/download/$tag
  listed=$(curl -fsSL --retry 3 "$release/$sums") || die "curl could not read $release/$sums, so no tool was installed"
  digest=$(awk -v asset="$asset" '$2 == asset { print $1 }' <<<"$listed")
  [[ $digest =~ ^[0-9a-f]{64}$ ]] ||
    die "$release/$sums names no sha256 for $asset, so no tool was installed"
  words+=("$bin" "$tag" "$release/$asset" "$digest" "$alias")
done

warn() {
  echo "$*, so the Sprite's lane reads that through this Mac's relay" >&2
}
secret() {
  aws ssm get-parameter --profile core-auto --region us-west-2 --name "$1" --with-decryption --query Parameter.Value --output text
}
slack_release() {
  local tag listed asset digest
  tag=$(curl -fsSLI --retry 3 -o /dev/null -w '%{url_effective}' https://github.com/yasyf/cc-slack/releases/latest) || return 1
  tag=${tag##*/}
  [[ $tag =~ ^v[0-9]+(\.[0-9]+)+$ ]] || return 1
  asset=cc-slack_${tag#v}_linux_amd64.tar.gz
  listed=$(curl -fsSL --retry 3 "https://github.com/yasyf/cc-slack/releases/download/$tag/checksums.txt") || return 1
  digest=$(awk -v asset="$asset" '$2 == asset { print $1 }' <<<"$listed")
  [[ $digest =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s %s %s' "${tag#v}" "https://github.com/yasyf/cc-slack/releases/download/$tag/$asset" "$digest"
}
slacked=(- - - -)
slack_token= dd_api= dd_app=
if ! release=$(slack_release); then
  warn "GitHub named no checksummed linux_amd64 asset on cc-slack's latest release"
elif ! email=$(git config --global user.email); then
  warn "this Mac's git config holds no user.email for cc-slack login"
elif ! slack_token=$(secret /ci/slack-agent/bot-token); then
  slack_token=
  warn "SSM on core-auto refused cc-slack's bot token"
else
  read -r slack url digest <<<"$release"
  slacked=("$slack" "$url" "$digest" "$email")
fi
if ! dd_api=$(secret /ci/ai-oncall/datadog-api-key) || ! dd_app=$(secret /ci/ai-oncall/datadog-app-key); then
  dd_api= dd_app=
  warn "SSM on core-auto refused ai-oncall's Datadog keys"
fi

read -ra tools <<<"${WORKER_TOOLS:-}"
placed=()
for pair in ${tools[@]+"${tools[@]}"}; do
  name=${pair%%=*} file=${pair#*=}
  [[ $pair == *=* && $name =~ ^[A-Za-z0-9._-]+$ && -f $file ]] ||
    die "WORKER_TOOLS holds $pair, and each of its words is NAME=FILE naming a file on this Mac, so no tool was installed"
  placed+=("$name" "$(base64 <"$file" | tr -d '\n')")
done

printf -v args ' %q' "$root" "$((${#placed[@]} / 2))" ${placed[@]+"${placed[@]}"} "${slacked[@]}" "${words[@]}"
status=0
printf 'slack_token=%q\ndd_api=%q\ndd_app=%q\n' "$slack_token" "$dd_api" "$dd_app" |
  ssh -F "$config" -o BatchMode=yes -o ConnectTimeout=30 "$host" 'umask 077 && mkdir -p /dev/shm/sprite-lane && cat >/dev/shm/sprite-lane/secrets' || status=$?
[ "$status" -ne 0 ] || ssh -F "$config" -o BatchMode=yes -o ConnectTimeout=30 "$host" "bash -s --$args" <<'SPRITE' || status=$?
set -eu
dest=$HOME/.local/bin
staged=$(mktemp -d)
secrets=/dev/shm/sprite-lane/secrets
trap 'rm -rf "$staged" "$secrets"' EXIT
. "$secrets"
mkdir -p "$dest"
root=$1 placed=$2
shift 2
while [ "$placed" -gt 0 ]; do
  name=$1 text=$2
  shift 2
  placed=$((placed - 1))
  printf '%s' "$text" | base64 -d >"$staged/$name"
  chmod 755 "$staged/$name"
  mv -f "$staged/$name" "$dest/$name"
  found=$(bash -lc "command -v $name" 2>/dev/null) || found=nothing
  [ "$found" = "$dest/$name" ] || { echo "a login shell runs $name from $found, not the $dest/$name placed for it" >&2; exit 1; }
  echo "$name placed"
done
slack=$1 slack_url=$2 slack_sha=$3 email=$4
shift 4
while [ $# -gt 0 ]; do
  bin=$1 tag=$2 url=$3 sha=$4 alias=$5
  shift 5
  curl -fsSL --retry 3 -o "$staged/$bin" "$url"
  echo "$sha  $staged/$bin" | sha256sum -c --status || { echo "$url does not hash to $sha" >&2; exit 1; }
  chmod 755 "$staged/$bin"
  mv -f "$staged/$bin" "$dest/$bin"
  [ -z "$alias" ] || ln -sfn "$bin" "$dest/$alias"
  for name in "$bin" $alias; do
    found=$(bash -lc "command -v $name" 2>/dev/null) || found=nothing
    printed=$(bash -lc "$name --version" 2>/dev/null) || printed=
    [ "$found" = "$dest/$name" ] && [ "${printed%% *}" = "$tag" ] ||
      { echo "a login shell runs $name from $found, printing '${printed%% *}', not $tag from $dest/$name" >&2; exit 1; }
    echo "$name $tag"
  done
done
if [ -L "$dest/gh" ]; then
  ln -sfn "$(readlink -f "$dest/gh")" "$dest/gh.real"
elif [ -f "$dest/gh" ] && ! grep -q GH_SHIM_ACTIVE "$dest/gh"; then
  mv -f "$dest/gh" "$dest/gh.real"
fi
if [ -x "$dest/gh.real" ]; then
  cat >"$staged/gh" <<'SHIM'
#!/bin/sh
real=$HOME/.local/bin/gh.real
[ -z "$CCX_CHILD$GH_SHIM_ACTIVE" ] || exec "$real" "$@"
case "$1" in
  ""|auth|config|alias|extension|completion|help|version|--version|--help|-h) exec "$real" "$@" ;;
  api) case "$2" in rate_limit|/rate_limit) exec "$real" "$@" ;; esac ;;
esac
age=60s
case "$*" in
  "pr checks"*|"pr diff"*|"pr status"*|"api graphql"*|"api /graphql"*) age=0s ;;
  *headRefOid*|*statusCheckRollup*|*mergeable*|*mergeStateStatus*|*reviewDecision*|*latestReviews*|*reviews*|*commits*|*files*|*additions*|*deletions*|*changedFiles*|*updatedAt*|*headSha*) age=0s ;;
  *check-runs*|*check-suites*|*/status*|*/branches*|*/git/*|*/compare/*|*repos/*/pulls/[0-9]*) age=0s ;;
  "pr view"*--json*) ;;
  "pr view"*) age=0s ;;
  "run list"*--branch*|"run list"*--commit*|"run list"*" -b "*|"run list"*" -c "*) age=0s ;;
esac
GH_SHIM_ACTIVE=1 CCX_GH=$real CCX_GH_RUN_GUARDED=1 exec "$HOME/.local/bin/ccx" vcs gh --max-age "$age" -- "$@"
SHIM
  chmod 755 "$staged/gh"
  mv -f "$staged/gh" "$dest/gh"
  echo "gh through ccx vcs gh"
fi
slack_setup() {
  local tries=0
  curl -fsSL --retry 3 -o "$staged/cc-slack.tar.gz" "$slack_url" || return 1
  echo "$slack_sha  $staged/cc-slack.tar.gz" | sha256sum -c --status || { echo "$slack_url does not hash to $slack_sha"; return 1; }
  tar -xzf "$staged/cc-slack.tar.gz" -C "$staged" cc-slack && chmod 755 "$staged/cc-slack" && mv -f "$staged/cc-slack" "$dest/cc-slack" || return 1
  printed=$(timeout 20 bash -lc "cc-slack --version" 2>/dev/null) || printed=
  [ "${printed##* }" = "$slack" ] || { echo "a login shell runs cc-slack printing '$printed', not $slack from $dest/cc-slack"; return 1; }
  printf "exec '%s' 'supervise'\n" "$dest/cc-slack" >"$HOME/.cc-remote/services/cc-slack" || return 1
  /.sprite/bin/sprite-env services get cc-slack >/dev/null 2>&1 ||
    /.sprite/bin/sprite-env services create cc-slack --cmd "$HOME/.cc-remote/supervise.py" --args cc-slack --needs cc-remote-payload --duration 1ms --no-stream >/dev/null || return 1
  sprite_config=$HOME/.config/cc-slack/sprite.toml
  mkdir -p "${sprite_config%/*}" && : >"$sprite_config" || return 1
  until (cd "$root" && PATH=$dest:$PATH CC_SLACK_BOT_TOKEN=$slack_token CC_SLACK_SOCKET_URL=post-only timeout 60 "$dest/cc-slack" login --post-only --config "$sprite_config" --email "$email" --repo "$root"); do
    tries=$((tries + 1))
    [ "$tries" -lt 5 ] || return 1
    sleep 3
  done
  echo "cc-slack $slack reads as $(PATH=$dest:$PATH timeout 20 cc-slack whoami 2>&1 | head -n 1)"
}
datadog_setup() {
  settings=$HOME/.claude/settings.json
  [ -s "$settings" ] || echo '{}' >"$settings" || return 1
  denied='["mcp__plugin_cc-slack_cc-slack__slack_send","mcp__plugin_cc-slack_cc-slack__slack_reply","mcp__plugin_cc-slack_cc-slack__slack_edit","mcp__plugin_cc-slack_cc-slack__slack_delete","mcp__plugin_cc-slack_cc-slack__slack_react","mcp__plugin_cc-slack_cc-slack__slack_unreact","mcp__plugin_cc-slack_cc-slack__slack_stream","mcp__plugin_cc-slack_cc-slack__slack_pickup","mcp__plugin_cc-slack_cc-slack__slack_dm_status","mcp__plugin_cc-slack_cc-slack__slack_card_answer","mcp__plugin_cc-slack_cc-slack__slack_revoke","mcp__plugin_cc-slack_cc-slack__slack_withdraw_pr_card","mcp__plugin_cc-slack_cc-slack__slack_channel_archive","mcp__plugin_cc-slack_cc-slack__slack_channel_create","mcp__plugin_cc-slack_cc-slack__slack_channel_invite","mcp__plugin_cc-slack_cc-slack__slack_channel_join","mcp__plugin_cc-slack_cc-slack__slack_channel_topic","Bash(cc-slack send:*)","Bash(cc-slack reply:*)","Bash(cc-slack edit:*)","Bash(cc-slack delete:*)","Bash(cc-slack react:*)","Bash(cc-slack unreact:*)","Bash(cc-slack pin:*)","Bash(cc-slack stream:*)","Bash(cc-slack pickup:*)","Bash(cc-slack dm-status:*)","Bash(cc-slack dm-relay:*)","Bash(cc-slack apply:*)","Bash(cc-slack canvas:*)","Bash(cc-slack card-answer:*)","Bash(cc-slack card-reopen:*)","Bash(cc-slack move-card:*)","Bash(cc-slack withdraw-pr-card:*)","Bash(cc-slack channel-archive:*)","Bash(cc-slack channel-create:*)","Bash(cc-slack channel-invite:*)","Bash(cc-slack channel-join:*)","Bash(cc-slack channel-topic:*)","mcp__plugin_datadog_mcp__execute_code_write","mcp__plugin_datadog_mcp__upsert_datadog_dashboard","mcp__plugin_datadog_mcp__create_datadog_notebook","mcp__plugin_datadog_mcp__edit_datadog_notebook","mcp__plugin_datadog_mcp__delete_datadog_notebook","mcp__plugin_datadog_mcp__recover_datadog_notebook"]'
  DD_API_KEY=$dd_api DD_APPLICATION_KEY=$dd_app jq --argjson denied "$denied" \
    '.env = (.env // {}) + {DD_API_KEY: env.DD_API_KEY, DD_APPLICATION_KEY: env.DD_APPLICATION_KEY, DD_MCP_TOOLSETS: "core,dbm"} | .permissions.deny = ((.permissions.deny // []) + $denied | unique)' \
    "$settings" >"$staged/settings.json" || return 1
  chmod 600 "$settings" && cat "$staged/settings.json" >"$settings" || return 1
  echo "ai-oncall's Datadog keys and the Slack and Datadog write denials in $settings"
}
if [ "$slack" = - ]; then
  echo "cc-slack not installed, as this Mac's warning says" >&2
elif ! said=$(slack_setup 2>&1); then
  echo "cc-slack not set up, so this Sprite's lane reads Slack through this Mac's relay: $(printf '%s' "$said" | tail -n 1)" >&2
else
  echo "$said" | tail -n 1
fi
if [ -z "$dd_api" ]; then
  echo "no Datadog keys placed, as this Mac's warning says" >&2
elif ! said=$(datadog_setup 2>&1); then
  echo "Datadog keys not placed, so this Sprite's lane reads Datadog through this Mac's relay: $(printf '%s' "$said" | tail -n 1)" >&2
else
  echo "$said"
fi
rustup=/.sprite/bin/rustup
if ! sudo -n "$rustup" target list --installed --toolchain stable 2>/dev/null | grep -qx wasm32-unknown-unknown; then
  sudo -n "$rustup" toolchain install stable --profile minimal --target wasm32-unknown-unknown --no-self-update >&2
fi
echo "rust stable wasm32-unknown-unknown"
zig=$HOME/.local/share/zig/0.14.0
if [ "$("$dest/zig" version 2>/dev/null)" != 0.14.0 ]; then
  url=https://ziglang.org/download/0.14.0/zig-linux-x86_64-0.14.0.tar.xz
  sha=473ec26806133cf4d1918caf1a410f8403a13d979726a9045b421b685031a982
  curl -fsSL --retry 3 -o "$staged/zig.tar.xz" "$url"
  echo "$sha  $staged/zig.tar.xz" | sha256sum -c --status || { echo "$url does not hash to $sha" >&2; exit 1; }
  rm -rf "$zig"
  mkdir -p "$zig"
  tar -xJf "$staged/zig.tar.xz" -C "$zig" --strip-components=1
  ln -sfn "$zig/zig" "$dest/zig"
fi
echo "zig $("$dest/zig" version)"
sccache=
export MISE_TRUSTED_CONFIG_PATHS=$root MISE_LOCKED=1
[ ! -x "$dest/mise" ] || sccache=$("$dest/mise" -C "$root" current sccache)
if [ -n "$sccache" ]; then
  if [ "$("$dest/sccache" --version 2>/dev/null)" != "sccache $sccache" ]; then
    "$dest/mise" -C "$root" install sccache >&2
    ln -sfn "$("$dest/mise" -C "$root" which sccache)" "$dest/sccache"
  fi
  found=$(bash -lc "command -v sccache" 2>/dev/null) || found=nothing
  [ "$found" = "$dest/sccache" ] || { echo "a login shell runs sccache from $found, not the $dest/sccache linked to the sccache $root pins" >&2; exit 1; }
  echo "sccache $sccache"
fi
SPRITE
[ "$status" -eq 0 ] || [ "$status" -eq 255 ] || exit "$unfit"
exit "$status"
