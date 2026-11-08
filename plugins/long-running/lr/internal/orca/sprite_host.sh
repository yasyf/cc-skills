set -eu
drive=$1 box=$2 machome=$3 checkout=$4 pack=$5 tmp=$6 vartmp=$7
st=$HOME/.claude/scratch/$drive-landing
current=$HOME/.claude/long-running/current
lr=$current/bin/lr
put() {
  if [ -f "$2" ] && [ ! -L "$2" ] && cmp -s "$1" "$2" && [ "$(stat -c %a "$1")" = "$(stat -c %a "$2")" ]; then
    rm "$1"
    return
  fi
  mkdir -p "$(dirname "$2")"
  mv -f "$1" "$2"
  echo "wrote $2"
}
link() {
  [ "$(readlink "$2" || true)" != "$1" ] || return 0
  mkdir -p "$(dirname "$2")"
  ln -sfn "$1" "$2"
  echo "linked $2"
}
has_service() {
  sprite-env services list | jq -r '.[].name' | grep -qx "$1"
}
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
tar -xzf - -C "$stage"
(cd "$stage" && find . -type f) | while IFS= read -r file; do
  put "$stage/$file" "$HOME/${file#./}"
done
if [ "$machome" != "$HOME" ] && [ "$(readlink "$machome" || true)" != "$HOME" ]; then
  sudo -n mkdir -p "$(dirname "$machome")"
  sudo -n ln -sfn "$HOME" "$machome"
  echo "linked $machome"
fi
[ -L "$current" ] || link "$HOME/.claude/plugins/cache/skills/long-running/$pack" "$current"
mkdir -p "$tmp/lr-landing-state" "$tmp/cc-inbox" "$vartmp/lr-landing-cache/go-build" "$vartmp/lr-landing-worktrees" "$vartmp/lr-landing-enqueue-cache"
link "$tmp/lr-landing-runner" "$checkout"
link "$tmp/lr-landing-state/lr.sqlite" "$st/lr.sqlite"
if [ -d "$HOME/.cc-inbox" ] && [ ! -L "$HOME/.cc-inbox" ]; then
  mv "$HOME/.cc-inbox" "$HOME/.cc-inbox.nbd"
fi
link "$tmp/cc-inbox" "$HOME/.cc-inbox"
helper="!$st/bin/gh auth git-credential"
if [ "$(git config --global --get-all credential.helper || true)" != "$helper" ]; then
  git config --global --unset-all credential.helper || true
  git config --global credential.helper "$helper"
fi
[ "$(git config --global user.name || true)" = "$box" ] || git config --global user.name "$box"
[ "$(git config --global user.email || true)" = noreply@sprites.dev ] || git config --global user.email noreply@sprites.dev
if ! command -v aws > /dev/null; then
  curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o "$tmp/awscliv2.zip"
  rm -rf "$tmp/aws-unpack"
  python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$tmp/awscliv2.zip" "$tmp/aws-unpack"
  chmod -R u+x "$tmp/aws-unpack/aws/install" "$tmp/aws-unpack/aws/dist"
  "$tmp/aws-unpack/aws/install" -i "$tmp/aws-cli" -b "$HOME/.local/bin" --update
  rm -rf "$tmp/aws-unpack" "$tmp/awscliv2.zip"
fi
has_service desk-creds || sprite-env services create desk-creds --cmd "$lr" --args "desk-creds,serve,--config,$st/lr.json" --no-stream > /dev/null
[ -d "$tmp/lr-landing-runner/.git" ] || echo "host: no checkout"
"$HOME/.local/bin/cci" relay status --json | jq -e '[.[] | select(.mesh == "default" and (.routes | length > 0))] | length > 0' > /dev/null || echo "host: no relay"
[ -e "$st/records-seeded" ] || has_service store-seed || echo "host: no records"
