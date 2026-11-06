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
for stacked in "$@"; do refspecs="$refspecs +refs/heads/${stacked%%:*}:$fetched/${stacked%%:*}"; done
(flock 9 && auth fetch --quiet --no-tags --refmap= origin $refspecs) 9>"$clone.fetch.lock"
git -C "$clone" update-ref "refs/heads/$tag/$base" "$fetched/$base"
tips= stale=
for stacked in "$@"; do
  branch=${stacked%%:*} parent=${stacked#*:}
  git -C "$clone" update-ref "refs/heads/$tag/$branch" "$fetched/$branch"
  tips="$tips refs/heads/$tag/$branch"
  git -C "$clone" merge-base --is-ancestor "$fetched/$parent" "$fetched/$branch" || stale=1
done
from=${upstream:-$fetched/$base}
if [ -z "$stale" ]; then
  echo "BASE $base $(git -C "$clone" rev-parse "$fetched/$base")"
  exit 0
fi
carried=$(git -C "$clone" rev-list "^$from" $tips | grep -Fxf "$clone.$tag.heads" || true)
if [ -n "$carried" ]; then
  printf 'CARRIES %s\n' $carried
  exit 1
fi
for stacked in "$@"; do
  branch=${stacked%%:*} parent=${stacked#*:}
  onto=refs/heads/$tag/$parent
  git -C "$clone" merge-base --is-ancestor "$onto" "refs/heads/$tag/$branch" && continue
  was=$fetched/$parent
  if [ "$parent" = "$base" ]; then was=$from; fi
  if [ -z "$(git -C "$clone" rev-list -n 1 "^$was" "^$from" "^$fetched/$base" "refs/heads/$tag/$branch")" ]; then
    git -C "$clone" update-ref "refs/heads/$tag/$branch" "$onto"
    continue
  fi
  if ! moves=$(auth replay --onto "$onto" "^$was" "^$from" "^$fetched/$base" "refs/heads/$tag/$branch" 2>"$clone.$tag.log"); then
    tip=$(git -C "$clone" rev-parse "$onto")
    for commit in $(git -C "$clone" rev-list --reverse --topo-order "^$was" "^$from" "^$fetched/$base" "refs/heads/$tag/$branch"); do
      if tree=$(git -C "$clone" merge-tree --write-tree --merge-base "$commit^" "$tip" "$commit" 2>/dev/null); then
        tip=$(git -C "$clone" -c user.name=remote-restack -c user.email=remote-restack@localhost commit-tree "$tree" -p "$tip" -m "$commit")
        continue
      fi
      echo "CONFLICT replaying $branch onto $parent at $(git -C "$clone" log -1 --format='%h %s' "$commit")"
      git -C "$clone" merge-tree --write-tree --name-only --merge-base "$commit^" "$tip" "$commit" | sed '1d; s/^/  /' | tail -20
      exit 1
    done
    echo "REPLAY-FAILED $branch onto $parent with no conflicting commit"
    sed 's/^/  /' "$clone.$tag.log" | tail -20
    exit 1
  fi
  printf '%s\n' "$moves" | grep "^update refs/heads/$tag/" | git -C "$clone" update-ref --stdin
done
pushes="" moved=""
for stacked in "$@"; do
  branch=${stacked%%:*}
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
