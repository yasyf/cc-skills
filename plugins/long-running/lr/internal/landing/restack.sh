#!/bin/sh
set -eu
IFS= read -r GH_TOKEN
IFS= read -r GIT_COMMITTER_NAME
IFS= read -r GIT_COMMITTER_EMAIL
export GH_TOKEN GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
clone=$1 base=$2 upstream=$3
shift 3
tag=restack-$$-$(date +%s)
fetched=refs/lr/$tag
trap 'rm -f "$clone.$tag.log" "$clone.$tag.heads"; git -C "$clone" for-each-ref --format="delete %(refname)" "refs/heads/$tag/" "$fetched/" | git -C "$clone" update-ref --stdin' EXIT
cat >"$clone.$tag.heads"
auth() {
  git -C "$clone" -c credential.helper= -c 'credential.helper=!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f' "$@"
}
refspecs="+refs/heads/$base:$fetched/$base"
for branch in "$@"; do refspecs="$refspecs +refs/heads/$branch:$fetched/$branch"; done
(flock 9 && auth fetch --quiet --no-tags --refmap= origin $refspecs) 9>"$clone.fetch.lock"
tips=
for branch in "$@"; do
  git -C "$clone" update-ref "refs/heads/$tag/$branch" "$fetched/$branch"
  tips="$tips refs/heads/$tag/$branch"
done
from=${upstream:-$fetched/$base}
if git -C "$clone" merge-base --is-ancestor "$fetched/$base" "refs/heads/$tag/$1"; then
  echo "BASE $base $(git -C "$clone" rev-parse "$fetched/$base")"
  exit 0
fi
carried=$(git -C "$clone" rev-list "^$from" $tips | grep -Fxf "$clone.$tag.heads" || true)
if [ -n "$carried" ]; then
  printf 'CARRIES %s\n' $carried
  exit 1
fi
if ! moves=$(auth replay --contained --onto "$fetched/$base" "^$from" $tips 2>"$clone.$tag.log"); then
  for top in "$@"; do
    tip=$(git -C "$clone" rev-parse "$fetched/$base")
    for commit in $(git -C "$clone" rev-list --reverse --topo-order "^$from" "refs/heads/$tag/$top"); do
      if tree=$(git -C "$clone" merge-tree --write-tree --merge-base "$commit^" "$tip" "$commit" 2>/dev/null); then
        tip=$(git -C "$clone" -c user.name=remote-restack -c user.email=remote-restack@localhost commit-tree "$tree" -p "$tip" -m "$commit")
        continue
      fi
      for branch in "$@"; do git -C "$clone" merge-base --is-ancestor "$commit" "refs/heads/$tag/$branch" && break; done
      echo "CONFLICT replaying $branch onto $base at $(git -C "$clone" log -1 --format='%h %s' "$commit")"
      git -C "$clone" merge-tree --write-tree --name-only --merge-base "$commit^" "$tip" "$commit" | sed '1d; s/^/  /' | tail -20
      exit 1
    done
  done
  echo "REPLAY-FAILED $* onto $base with no conflicting commit"
  sed 's/^/  /' "$clone.$tag.log" | tail -20
  exit 1
fi
printf '%s\n' "$moves" | grep "^update refs/heads/$tag/" | git -C "$clone" update-ref --stdin
pushes="" moved=""
for branch in "$@"; do
  old=$(git -C "$clone" rev-parse "$fetched/$branch")
  new=$(git -C "$clone" rev-parse "refs/heads/$tag/$branch")
  [ "$old" = "$new" ] && continue
  pushes="$pushes --force-with-lease=refs/heads/$branch:$old refs/heads/$tag/$branch:refs/heads/$branch"
  moved="${moved}MOVED $branch $old $new
"
done
[ -z "$pushes" ] || auth push --quiet --atomic origin $pushes
printf '%s' "$moved"
echo "BASE $base $(git -C "$clone" rev-parse "$fetched/$base")"
