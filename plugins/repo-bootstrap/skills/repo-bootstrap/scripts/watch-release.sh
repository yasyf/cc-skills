#!/usr/bin/env bash
# Watch a GitHub Actions run to completion and report the result of a release.
#
#   watch-release.sh [RUN_ID] [--tag vX.Y.Z] [--repo OWNER/REPO]
#                    [--workflow NAME] [--pypi DIST_NAME]
#
# Resolves a run, watches it to completion, then prints per-job conclusions, the
# GitHub release URL + asset names, and (with --pypi) whether the version landed on
# PyPI. Replaces the hand-rolled poll-loop sessions otherwise re-write each release.
#
# Run resolution, in order: an explicit RUN_ID positional; else, for --tag, the run
# whose workflow name contains "release" (a tag push often fires several workflows),
# falling back to the newest run on that tag; else the newest run for the repo.
# --workflow NAME pins resolution to one workflow (file name or display name) and
# overrides the release-name heuristic.
#
# Args:
#   RUN_ID        numeric GitHub Actions run id (optional positional)
#   --tag         release tag (e.g. v0.6.0); used to find the run, the GitHub
#                 release, and (with --pypi) the PyPI version (the leading v is stripped)
#   --repo        OWNER/REPO; defaults to the repo inferred from the current directory
#   --workflow    workflow file name or display name to narrow run resolution
#   --pypi        dist name to verify on PyPI (python/pypi releases); omit for generic
#                 releases (e.g. a Homebrew formula bump)
#
# Output: a human-readable report on stdout, diagnostics on stderr. Exits non-zero
# when the watched run failed, so callers can gate on it. Needs `gh` (authenticated)
# and `curl`; uses gh's built-in `--jq` query, so no jq dependency.
set -euo pipefail

die() { echo "watch-release.sh: $*" >&2; exit 1; }

RUN_ID=""; TAG=""; REPO=""; WORKFLOW=""; PYPI=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tag)      TAG="$2"; shift 2 ;;
    --repo)     REPO="$2"; shift 2 ;;
    --workflow) WORKFLOW="$2"; shift 2 ;;
    --pypi)     PYPI="$2"; shift 2 ;;
    -h|--help)  sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2; exit 0 ;;
    -*)         die "unknown flag: $1" ;;
    *)          [ -z "$RUN_ID" ] && RUN_ID="$1" || die "unexpected argument: $1"; shift ;;
  esac
done

command -v gh >/dev/null 2>&1 || die "gh CLI not found on PATH"

# Resolve the repo so every gh call works regardless of the current directory.
if [ -z "$REPO" ]; then
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null)" \
    || die "could not infer repo — pass --repo OWNER/REPO"
fi

# Resolve the run id. A tag push often fires several workflows, so for --tag prefer
# the one whose workflow name contains "release"; --workflow pins it explicitly.
resolve_run() {
  if [ -n "$WORKFLOW" ] && [ -n "$TAG" ]; then
    gh run list --repo "$REPO" --workflow "$WORKFLOW" --branch "$TAG" \
      -L1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true
  elif [ -n "$WORKFLOW" ]; then
    gh run list --repo "$REPO" --workflow "$WORKFLOW" \
      -L1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true
  elif [ -n "$TAG" ]; then
    local id
    id="$(gh run list --repo "$REPO" --branch "$TAG" -L20 --json databaseId,workflowName \
      -q '[.[] | select(.workflowName | test("release";"i")) | .databaseId][0] // empty' 2>/dev/null || true)"
    [ -z "$id" ] && id="$(gh run list --repo "$REPO" --branch "$TAG" \
      -L1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true)"
    echo "$id"
  else
    gh run list --repo "$REPO" -L1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true
  fi
}

# Retry briefly so a just-pushed run has time to register.
if [ -z "$RUN_ID" ]; then
  for attempt in 1 2 3 4 5 6; do
    RUN_ID="$(resolve_run)"
    [ -n "$RUN_ID" ] && break
    echo "watch-release.sh: no run found yet (attempt $attempt), waiting…" >&2
    sleep 5
  done
  [ -n "$RUN_ID" ] || die "no run found for repo=$REPO${TAG:+ tag=$TAG}${WORKFLOW:+ workflow=$WORKFLOW}"
fi

echo "watch-release.sh: watching run $RUN_ID in $REPO" >&2

# Watch to completion. Don't let a failed run abort the script — we still report.
set +e
gh run watch "$RUN_ID" --repo "$REPO" --exit-status
rc=$?
set -e

echo
echo "=== run $RUN_ID ==="
gh run view "$RUN_ID" --repo "$REPO" \
  --json displayTitle,status,conclusion,url \
  -q '"\(.displayTitle)\nstatus: \(.status)  conclusion: \(.conclusion // "-")\n\(.url)"'

echo
echo "=== jobs ==="
gh run view "$RUN_ID" --repo "$REPO" \
  --json jobs -q '.jobs[] | "\(.name): \(.conclusion // .status)"'

# Derive the tag from the run when not given, so we can find the release.
if [ -z "$TAG" ]; then
  head="$(gh run view "$RUN_ID" --repo "$REPO" --json headBranch -q .headBranch 2>/dev/null || true)"
  case "$head" in v[0-9]*) TAG="$head" ;; esac
fi

if [ -n "$TAG" ]; then
  echo
  echo "=== release $TAG ==="
  if gh release view "$TAG" --repo "$REPO" \
       --json url,assets -q '"\(.url)\nassets: \([.assets[].name] | join(", "))"' 2>/dev/null; then
    :
  else
    echo "no GitHub release found for tag $TAG (yet)"
  fi
fi

# Optional: confirm the version published to PyPI (python/pypi releases).
if [ -n "$PYPI" ]; then
  [ -n "$TAG" ] || die "--pypi needs a tag (pass --tag vX.Y.Z) to know the version"
  version="${TAG#v}"
  echo
  echo "=== PyPI $PYPI $version ==="
  ok=""
  for attempt in 1 2 3; do
    if curl -fsS "https://pypi.org/pypi/$PYPI/$version/json" >/dev/null 2>&1; then
      ok=1; break
    fi
    sleep 5
  done
  if [ -n "$ok" ]; then
    echo "OK — https://pypi.org/project/$PYPI/$version/"
  else
    echo "not yet on PyPI — https://pypi.org/project/$PYPI/"
  fi
fi

exit "$rc"
