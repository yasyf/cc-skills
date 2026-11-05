set -eu
bin="$HOME/.local/bin"
plugins="$HOME/.claude/plugins"
mkdir -p "$bin" "$plugins"
exec 9> "$bin/.install.lock"
flock 9
[ -f "$plugins/installed_plugins.json" ] || echo '{"version":2,"plugins":{}}' > "$plugins/installed_plugins.json"
[ -f "$plugins/known_marketplaces.json" ] || echo '{}' > "$plugins/known_marketplaces.json"
said() {
  "$1" --version 2>&1 | awk 'END { for (i = 1; i <= NF; i++) if ($i ~ /^v?[0-9]+\.[0-9]/) word = $i; sub(/^v/, "", word); print word }'
}
fetch() {
  curl -fsSL "$1" -o "$3"
  if ! echo "$2  $3" | sha256sum -c --status -; then
    rm -f "$3"
    echo "refused: $1 is not the archive with SHA-256 $2"
    exit 1
  fi
}
packs=
supervise_on() {
  recipe="$HOME/.cc-remote/services/$1"
  [ -f "$recipe" ] || return 0
  tools="$HOME/.local/share/cc-remote/tools"
  dir="$tools/$1-$2"
  mkdir -p "$dir"
  cp "$bin/$1" "$dir/$1.new"
  mv "$dir/$1.new" "$dir/$1"
  printf "exec '%s' 'supervise'\n" "$dir/$1" > "$recipe.new"
  mv "$recipe.new" "$recipe"
  waited=0
  until pgrep -f "$dir/$1 supervise" > /dev/null; do
    if [ "$waited" -ge 60 ]; then
      echo "the $1 service never started $dir/$1"
      exit 1
    fi
    waited=$((waited + 1))
    sleep 1
  done
  for old in "$tools/$1"-*; do
    [ "$old" = "$dir" ] || rm -rf "$old"
  done
  echo "installed $1-supervise $2"
}
fetch "$1" "$2" /tmp/cci.tgz
staged=$(mktemp -d "$bin/.cci.XXXXXX")
tar -xzf /tmp/cci.tgz -C "$staged" --exclude='*.md'
mv -f "$staged"/* "$bin"
rmdir "$staged"
echo "installed cci $(said "$bin/cci")"
fetch "$3" "$4" /tmp/lr.tgz
tar -xzOf /tmp/lr.tgz "$5" > "$bin/lr.new"
chmod 755 "$bin/lr.new"
mv -f "$bin/lr.new" "$bin/lr"
echo "installed lr $(said "$bin/lr")"
shift 5
while [ "$1" != -- ]; do
  kind=$1 name=$2 version=$5
  fetch "$3" "$4" /tmp/tool.tgz
  shift 5
  case $kind in
  binary | raw)
    if [ "$kind" = raw ]; then
      cp /tmp/tool.tgz "$bin/$name.new"
    else
      tar -xzOf /tmp/tool.tgz "$name" > "$bin/$name.new"
    fi
    chmod 755 "$bin/$name.new"
    printed=$(said "$bin/$name.new")
    if [ "$printed" != "$version" ]; then
      rm -f "$bin/$name.new"
      echo "refused: $name from that download prints $printed for --version, not $version"
      exit 1
    fi
    mv "$bin/$name.new" "$bin/$name"
    ;;
  package)
    tar -xzOf /tmp/tool.tgz "$name" > "/tmp/$name.new"
    chmod 755 "/tmp/$name.new"
    printed=$("/tmp/$name.new" version | jq -r .build)
    if [ "$printed" != "$version" ]; then
      rm -f "/tmp/$name.new"
      echo "refused: $name from that archive reports build $printed, not $version"
      exit 1
    fi
    "/tmp/$name.new" package-install 9>&-
    rm -f "/tmp/$name.new"
    printed=$("$bin/$name" version | jq -r .build)
    if [ "$printed" != "$version" ]; then
      echo "refused: package-install left $name at build $printed, not $version"
      exit 1
    fi
    ;;
  plugin)
    dir="$plugins/cache/${name#*@}/${name%@*}/$version"
    if [ ! -d "$dir" ]; then
      rm -rf "$dir.new"
      mkdir -p "$dir.new"
      tar -xzf /tmp/tool.tgz -C "$dir.new"
      printed=$(jq -r .version "$dir.new/.claude-plugin/plugin.json")
      if [ "$printed" != "$version" ]; then
        rm -rf "$dir.new"
        echo "refused: the $name archive is version $printed, not $version"
        exit 1
      fi
      mv "$dir.new" "$dir"
    fi
    if [ -x "$dir/scripts/binrun.sh" ]; then
      packs="$packs $dir"
    fi
    now=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
    if ! jq -e --arg id "$name" '.plugins | has($id)' "$plugins/installed_plugins.json" > /dev/null; then
      plugin=${name%@*} marketplace=${name#*@}
      tree="$HOME/.local/share/cc-remote/marketplaces/$marketplace"
      if [ -L "$tree" ]; then
        baked=$(readlink "$tree")
        rm "$tree"
        cp -R "$baked" "$tree"
        chmod -R u+w "$tree"
      fi
      mkdir -p "$tree/.claude-plugin"
      rm -rf "${tree:?}/$plugin"
      cp -R "$dir" "$tree/$plugin"
      listing="$tree/.claude-plugin/marketplace.json"
      [ -f "$listing" ] || jq -n --arg marketplace "$marketplace" '{name: $marketplace, owner: {name: $marketplace}, plugins: []}' > "$listing"
      jq --arg plugin "$plugin" '.plugins = [.plugins[] | select(.name != $plugin)] + [{name: $plugin, source: ("./" + $plugin)}]' "$listing" > "$listing.new"
      mv "$listing.new" "$listing"
      jq --arg marketplace "$marketplace" --arg tree "$tree" --arg now "$now" '.[$marketplace] = {source: {source: "directory", path: $tree}, installLocation: $tree, lastUpdated: $now}' "$plugins/known_marketplaces.json" > "$plugins/known_marketplaces.json.new"
      mv "$plugins/known_marketplaces.json.new" "$plugins/known_marketplaces.json"
      jq --arg id "$name" '.enabledPlugins[$id] = true' "$HOME/.claude/settings.json" > "$HOME/.claude/settings.json.new"
      mv "$HOME/.claude/settings.json.new" "$HOME/.claude/settings.json"
    fi
    jq -e --arg id "$name" --arg dir "$dir" --arg version "$version" --arg now "$now" '.plugins[$id] = ((.plugins[$id] // [{scope: "user", installedAt: $now, lastUpdated: $now}]) | map(.installPath = $dir | .version = $version))' "$plugins/installed_plugins.json" > "$plugins/installed_plugins.json.new"
    mv "$plugins/installed_plugins.json.new" "$plugins/installed_plugins.json"
    current="$HOME/.claude/${name%@*}/current"
    if [ -L "$current" ]; then
      ln -sfn "$dir" "$current"
      if [ -e "$current/bin/lr" ]; then
        printf '#!/bin/sh\nexec "%s/bin/lr" "$@"\n' "$current" > "$bin/lr.new"
        chmod 755 "$bin/lr.new"
        mv -f "$bin/lr.new" "$bin/lr"
      fi
    fi
    ;;
  esac
  rm -f /tmp/tool.tgz
  echo "installed $name $version"
  if [ "$name" = codex-ask ]; then
    supervise_on "$name" "$version"
  fi
done
for dir in $packs; do
  for descriptor in "$dir"/bin/*.binrun; do
    if ! said=$("$dir/scripts/binrun.sh" -- fetch "$descriptor" 2>&1); then
      case $said in
      *mismatch*)
        echo "refused: $said"
        exit 1
        ;;
      esac
      echo "warning: $descriptor was not pre-fetched: $said"
    fi
  done
done
shift
grader=
if [ "${1-}" = no-cci-daemon ]; then
  grader=1
  shift
fi
if ! pgrep -f "$bin/cci supervise" > /dev/null; then
  echo "installed cci-daemon none"
elif [ -n "$grader" ]; then
  echo "warning: this grader runs a cci supervisor, which no grader should; the install left its daemon alone"
else
  "$bin/cci" serve > /dev/null
  daemon=$(pgrep -fo "$HOME/.daemonkit/bin/com.yasyf.cc-inbox daemon run")
  # Without CCI_HANDED_OFF_FROM, cci hands --version to the newest cci on the box, not the build the daemon runs.
  echo "installed cci-daemon $(CCI_HANDED_OFF_FROM=lr "/proc/$daemon/exe" --version)"
fi
mkdir -p "$HOME/.config/mise/conf.d"
printf '[settings]\ntrusted_config_paths = ["~"]\n' > "$HOME/.config/mise/conf.d/cc-remote-trust.toml"
export PATH="$bin:$PATH"
missing=
for tool; do command -v "$tool" > /dev/null || missing="$missing $tool"; done
if [ -n "$missing" ]; then
  echo "missing:$missing"
  exit 3
fi
