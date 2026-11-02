source "$(dirname "$0")/../lib.sh"
checkout .ci-merge-waits-writer-baseline 05c2ec4e237c066c76ce3f90ad1e3d2d20db3dd0
LR_BLOCKER_OLD_SCRIPTS="$GITHUB_WORKSPACE/.ci-merge-waits-writer-baseline/plugins/long-running/skills/long-running/scripts" uv run --locked --project plugins/long-running pytest plugins/long-running/tests/merge_waits_old_writer_integration.py -q -s
