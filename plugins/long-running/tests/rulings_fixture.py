import sys

from captain_hook import BaseHookEvent

from hooks.compaction_handoff import CCN_TIMEOUT_SECONDS, run_script, script


def rulings_command_fixture(cwd: str, *args: str, evt: BaseHookEvent, stdin: str = "", timeout: float = CCN_TIMEOUT_SECONDS) -> str:
    done = run_script([sys.executable, str(script("rulings.py")), *args, "--repo", cwd], timeout, cwd, stdin)
    done.check_returncode()
    return done.stdout
