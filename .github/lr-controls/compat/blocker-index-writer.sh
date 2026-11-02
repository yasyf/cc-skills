source "$(dirname "$0")/../lib.sh"
checkout .ci-blocker-index-writer-baseline 897a7efad74429a246c919bcaf3e91a926736389
LR_BLOCKER_OLD_SCRIPTS="$GITHUB_WORKSPACE/.ci-blocker-index-writer-baseline/plugins/long-running/skills/long-running/scripts" uv run --locked --project plugins/long-running pytest plugins/long-running/tests/blocker_index_old_writer_integration.py -q -s
