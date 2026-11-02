source "$(dirname "$0")/../lib.sh"
checkout .ci-release-proof-writer-baseline 82ab4e455cca7584000efc44439d74bb15f4b6b0
LR_BLOCKER_OLD_SCRIPTS="$GITHUB_WORKSPACE/.ci-release-proof-writer-baseline/plugins/long-running/skills/long-running/scripts" uv run --locked --project plugins/long-running pytest plugins/long-running/tests/release_proof_old_writer_integration.py -q -s
