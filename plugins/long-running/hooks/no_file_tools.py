import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, state
from drive.shell import runnable

MEMORY_FILE = re.compile(r"\.claude/projects/\S*/memory/")
REST_OF_BRACE_GROUP = r"(?:[^{}]|\$\{[^{}]*\})*(?<![^\s;&])\}"
REST_OF_SUBSHELL = r"(?:[^()]|\$\([^()]*\))*(?<!\\)\)"
INTO_FILE = r"\s*>>?\s*(?!&|/dev/std)\S"
WHOLE_OUTPUT = re.compile(
    rf"\bcat\s+[^|;&]*(?:/tool-results/\S*|\.output)\s*(?:$|[;&])(?!(?:{REST_OF_BRACE_GROUP}|{REST_OF_SUBSHELL}){INTO_FILE})", re.MULTILINE
)


def deny_reason(payload: dict) -> str | None:
    """Why a drive's coordinator, communicator or meta session may not open a file itself: Claude Code puts each file opened with Read, Write or Edit back into the session after every compaction."""
    if payload.get("agent_id"):
        return None
    session = payload.get("session_id") or ""
    names = state.session_drives(session)
    if not names:
        return None
    role = state.session_role(state.saved(names[0]), session)
    if payload["tool_name"] == "Bash":
        return bash_reason(role, payload["tool_input"]["command"])
    return (
        f"refused: the {role} opens no file with {payload['tool_name']}, since Claude Code puts each file opened with"
        " Read, Write or Edit back into this session after every compaction. Read a page of the skill with"
        " `drive manual <page>`, a job's return with `drive job report <job>`, a record with cci, and a saved tool result"
        " or background output with a bounded Bash slice such as `tail -c 2000 <file>`. A file to write goes to a job,"
        " or on stdin to the drive verb that keeps it; a lesson is a cc-notes note (`ccn note add`) or a defect record,"
        " which reaches the meta session, never a memory file."
    )


def bash_reason(role: str, command: str) -> str | None:
    """Why a drive role's Bash call may not run: it reaches a memory file or prints a whole saved output, the two ways around the file tools' refusal."""
    if MEMORY_FILE.search(command):
        return (
            f"refused: the {role} reads and writes no memory file, with Bash or any tool, since the drive keeps its lessons"
            " where every session and job finds them: a lesson is a cc-notes note (`ccn note add`) or a defect record,"
            " which reaches the meta session."
        )
    if WHOLE_OUTPUT.search(runnable(command)):
        return (
            f"refused: the {role} prints no whole saved tool result or background output, since all of it lands in this"
            " session's context. Read a bounded slice such as `tail -c 2000 <file>`."
        )
    return None


def main() -> None:
    payload = agents.seen(json.load(sys.stdin))
    if not (reason := deny_reason(payload)):
        agents.hold(payload)
        return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
