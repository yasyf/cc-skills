set -eu
bin="$HOME/.local/bin"
plugins="$HOME/.claude/plugins"
mkdir -p "$bin"
fetch() {
  curl -fsSL "$1" -o "$3"
  if ! echo "$2  $3" | sha256sum -c --status -; then
    rm -f "$3"
    echo "refused: $1 is not the archive with SHA-256 $2"
    exit 1
  fi
}
fetch "$1" "$2" /tmp/cci.tgz
tar -xzf /tmp/cci.tgz -C "$bin" cci
fetch "$3" "$4" /tmp/lr.tgz
tar -xzOf /tmp/lr.tgz "$5" > "$bin/lr"
chmod 755 "$bin/lr"
if [ -n "$6" ]; then
  mkdir -p ~/.config/lr
  printf '%s\n' "$6" > ~/.config/lr/bk.url
fi
shift 6
while [ "$1" != -- ]; do
  kind=$1 name=$2 version=$5
  fetch "$3" "$4" /tmp/tool.tgz
  shift 5
  case $kind in
  binary)
    tar -xzOf /tmp/tool.tgz "$name" > "$bin/$name.new"
    chmod 755 "$bin/$name.new"
    said=$("$bin/$name.new" --version 2>&1 | awk 'END { print $NF }')
    if [ "${said#v}" != "$version" ]; then
      rm -f "$bin/$name.new"
      echo "refused: $name from that archive prints $said for --version, not $version"
      exit 1
    fi
    mv "$bin/$name.new" "$bin/$name"
    ;;
  plugin)
    dir="$plugins/cache/${name#*@}/${name%@*}/$version"
    if [ ! -d "$dir" ]; then
      rm -rf "$dir.new"
      mkdir -p "$dir.new"
      tar -xzf /tmp/tool.tgz -C "$dir.new"
      said=$(jq -r .version "$dir.new/.claude-plugin/plugin.json")
      if [ "$said" != "$version" ]; then
        rm -rf "$dir.new"
        echo "refused: the $name archive is version $said, not $version"
        exit 1
      fi
      mv "$dir.new" "$dir"
    fi
    jq -e --arg id "$name" --arg dir "$dir" --arg version "$version" '.plugins[$id] |= map(.installPath = $dir | .version = $version)' "$plugins/installed_plugins.json" > "$plugins/installed_plugins.json.new"
    mv "$plugins/installed_plugins.json.new" "$plugins/installed_plugins.json"
    ;;
  esac
  rm -f /tmp/tool.tgz
  echo "installed $name $version"
done
shift
export PATH="$bin:$PATH"
missing=
for tool; do command -v "$tool" > /dev/null || missing="$missing $tool"; done
if [ -n "$missing" ]; then
  echo "missing:$missing"
  exit 3
fi
