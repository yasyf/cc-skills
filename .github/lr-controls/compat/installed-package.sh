source "$(dirname "$0")/../lib.sh"
uv build --project plugins/long-running --wheel --out-dir "$RUNNER_TEMP/long-running-wheel"
uv venv "$RUNNER_TEMP/long-running-venv"
uv pip install --python "$RUNNER_TEMP/long-running-venv/bin/python" "$RUNNER_TEMP"/long-running-wheel/*.whl
"$RUNNER_TEMP/long-running-venv/bin/python" -I -m long_running.actions --help
"$RUNNER_TEMP/long-running-venv/bin/python" -I -c 'from long_running.roster_service import mailbox_of; from long_running.rosters import RosterIndex'
