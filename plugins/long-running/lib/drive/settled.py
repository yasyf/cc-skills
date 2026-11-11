import json
import subprocess
from pathlib import Path

from drive import limits, state
from drive.state import DriveError

MATCHES = 3


def kept(names: list[str], ask: str | None) -> bool:
    """An ASK that `drive job ask` kept in one of these drives, which it checked against his answers before keeping."""
    return bool(ask) and any(Path(ask).expanduser().resolve().is_relative_to(state.drive_dir(name).resolve() / "asks") for name in names)


def answers(card: str, whole: str, drives: list[str], cwd: str) -> list[dict]:
    """His durable answers that may already settle a question: those the part he reads names. TODO: once a ccn release with `answer match --nearest` is installed, add each drive's nearest to `whole` from `drives`."""
    return matched(card, cwd)


def matched(text: str, cwd: str, *flags: str) -> list[dict]:
    match = ["ccn", "answer", "match", "-", "--label", "scope:durable", *flags, "--limit", str(MATCHES), "--json"]
    try:
        done = subprocess.run(match, input=text, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DriveError(f"could not check the question against his recorded answers: {error}") from None
    if done.returncode:
        raise DriveError(f"could not check the question against his recorded answers: {' '.join(done.stderr.split())}")
    return json.loads(done.stdout)


def refusal(card: str, whole: str, drives: list[str], cwd: str, why: str | None, override: str) -> str | None:
    """Why a question may not go to him: a recorded answer of his may settle it, and no reason names that answer and says why it does not."""
    found = answers(read(card), read(whole), drives, cwd)
    ids = [answer["id"][:7] for answer in found]
    if not (unnamed := [short for short in ids if short not in (why or "")]):
        return None
    listed = "\n".join(f"- {answer['id'][:7]} {answer['title'].splitlines()[0]}" for answer in found)
    example = "; ".join(f"{short}: <why it does not settle this>" for short in ids)
    left_out = f" The reason given leaves out {', '.join(unnamed)}." if why else ""
    return (
        "refused: these recorded answers of his may already settle this question. Read each with `ccn answer show <id>`:"
        " a question one settles is never asked again, in any wording, and the work goes ahead on that answer. One that"
        " already names what you recommend, such as a service he said to build, settles what that thing does: build it."
        f' Only when none does, ask again with {override} one string that names every id below, such as {override} "{example}".'
        f"{left_out}\n{listed}"
    )


def read(text: str) -> str:
    """The words of a question as he reads them: a Slack link as its label, other links and bare URLs left out, since every PR link shares github, pull and monorepo with each old answer that names a PR."""
    return limits.URL.sub(" ", limits.TOKEN.sub(" ", limits.LABELLED.sub(r" \1 ", text)))
