source "$(dirname "$0")/../lib.sh"
checkout .ci-escalation-writer-baseline 526a1ce33fb23188ac3a1f8a238487d38dd4354f
LR_ESCALATION_OLD_SCRIPTS="$GITHUB_WORKSPACE/.ci-escalation-writer-baseline/plugins/long-running/skills/long-running/scripts" uv run --locked --project plugins/long-running pytest plugins/long-running/tests/escalation_index_old_writer_integration.py plugins/long-running/tests/test_escalation_reference_index.py plugins/long-running/tests/test_action_queries.py::test_full_snapshot_is_detached_and_corruption_is_not_hidden -q -s
