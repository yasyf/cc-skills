import json
import subprocess
from pathlib import Path

from drive import state
from drive.state import DriveError

MATCHES = 3


def kept(names: list[str], ask: str | None) -> bool:
    """An ASK that `drive job ask` kept in one of these drives, which it checked against his answers before keeping."""
    return bool(ask) and any(Path(ask).expanduser().resolve().is_relative_to(state.drive_dir(name).resolve() / "asks") for name in names)


def answers(text: str, cwd: str) -> list[dict]:
    """His durable answers that may already settle a question: cc-notes' match of their rare words against what he would read."""
    match = ["ccn", "answer", "match", "-", "--label", "scope:durable", "--limit", str(MATCHES), "--json"]
    try:
        done = subprocess.run(match, input=text, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DriveError(f"could not check the question against his recorded answers: {error}") from None
    if done.returncode:
        raise DriveError(f"could not check the question against his recorded answers: {' '.join(done.stderr.split())}")
    return json.loads(done.stdout)


def refusal(text: str, cwd: str, why: str | None, override: str) -> str | None:
    """Why a question may not go to him: a recorded answer of his may settle it, and no reason names that answer and says why it does not."""
    unnamed = [answer for answer in answers(text, cwd) if answer["id"][:7] not in (why or "")]
    if not unnamed:
        return None
    found = "\n".join(f"- {answer['id'][:7]} {answer['title'].splitlines()[0]}" for answer in unnamed)
    return (
        "refused: these recorded answers of his may already settle this question. Read each with `ccn answer show <id>`:"
        " a question one settles is never asked again, in any wording, and the work goes ahead on that answer. Only when"
        f' none does, ask with {override} "<id>: <why it does not settle this>", naming every id below.\n{found}'
    )
