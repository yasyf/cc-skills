set -euo pipefail
cd "$GITHUB_WORKSPACE"

checkout() {
  flock "$(git rev-parse --git-common-dir)/lr-controls.lock" git worktree add --detach --quiet "$1" "$2"
}
