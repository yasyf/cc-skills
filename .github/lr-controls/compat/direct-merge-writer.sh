source "$(dirname "$0")/../lib.sh"
checkout .ci-direct-merge-writer-baseline cd0740efe5bd10b0402bd310126cf566bb53a2c5
LR_BLOCKER_OLD_SCRIPTS="$GITHUB_WORKSPACE/.ci-direct-merge-writer-baseline/plugins/long-running/skills/long-running/scripts" uv run --locked --project plugins/long-running pytest plugins/long-running/tests/direct_merge_old_writer_integration.py -q -s
