import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import render, state


def instructions(payload: dict) -> str | None:
    session = payload.get("session_id")
    bound = state.session_drives(session) if session else []
    if not bound:
        return None
    data = state.load(bound[0])
    role = state.session_role(data, session)
    return render.compact_instructions(data, role) if role else None


def main() -> None:
    try:
        text = instructions(json.load(sys.stdin))
    except Exception:
        return
    if text:
        print(text)


if __name__ == "__main__":
    main()
