source "$(dirname "$0")/../lib.sh"
checkout .ci-release-generation-writer-baseline c01c7aa04559b07ad03983301609163b0a4630d0
LR_BLOCKER_OLD_SCRIPTS="$GITHUB_WORKSPACE/.ci-release-generation-writer-baseline/plugins/long-running/skills/long-running/scripts" uv run --locked --project plugins/long-running pytest plugins/long-running/tests/release_generation_old_writer_integration.py -q -s
