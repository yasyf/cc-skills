"""His Approve is the merge: which of a lane's PRs land now, read from its lane file's prs rows alone.

A PR lands on his Approve with green checks and no hold. His Approve stands at the head he approved and at every later head whose new commits our lanes' account authored, the head the rebuild carried each Approve from before his 14:13 words "all the ones I've approved you can carry over the approvals, let's just get stuff moving" included; any other author's commit needs his tap again; the dangerous verdict and a staging proof are still read at the current head. A head the dangerous reader judged safe, its verdict at that head naming no class, needs nothing more; an unread head or one read at an older head never lands. A dangerous PR lands only with its stack, once a staging proof passed at the stack's top head on the target its release reaches, and only while he is present, his word or tap within PRESENT_WITHIN. The merge queue lands a stack from its bottom, so a PR waits while any PR below it cannot land.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

PRESENT_WITHIN = timedelta(minutes=15)


@dataclass(frozen=True)
class Verdict:
    keys: tuple[str, ...]
    lands: bool
    why: str


def key(row: dict) -> str:
    return f"{row['repo']}#{row['number']}"


def judged(row: dict) -> bool:
    return all(bool(row.get(gate)) and row[gate]["head"] == row["head"] for gate in ("dangerous", "guarded"))


def guarded(row: dict) -> bool:
    """Whether the repository's broad [[dangerous]] classes mark the head, so it merges only while he is present."""
    return bool(row["guarded"]["classes"])


def dangerous(row: dict) -> bool:
    """Whether the narrow danger page holds the head for a staging pass before it merges."""
    return bool(row["dangerous"]["classes"])


def proven(stack: list[dict]) -> bool:
    proof = stack[-1].get("staging")
    return bool(proof) and proof["result"] == "pass" and proof["head"] == stack[-1]["head"]


def present(heard: datetime | None, moment: datetime) -> bool:
    return heard is not None and moment - heard < PRESENT_WITHIN


def unready(row: dict) -> str | None:
    """Why a PR cannot land on its own terms, None when it can."""
    if row.get("merged"):
        return "merged"
    if row["held"]:
        return f"he holds it: {row['held']}"
    if not row["approved_head"]:
        return "no Approve of his"
    if row["approved_head"] != row["head"] and (moved := row.get("foreign")) != []:
        return f"commits since his Approve need his tap again: {', '.join(moved)}" if moved else "his approved head is not among its recent commits, so his Approve needs his tap again"
    if row.get("checks") != "pass":
        return f"checks {row.get('checks') or 'unread'}"
    if not judged(row):
        return "the dangerous reader has not judged its head"
    if "under" not in row:
        return "the PR it sits on is unread, so what the queue would land below it is unknown"
    return None


def stack_verdict(stack: list[dict], heard: datetime | None, moment: datetime) -> Verdict:
    """What lands of one stack, bottom first: the longest run from its bottom whose PRs are each ready, cut at a PR the danger page holds until the stack's proof passed at its top head, and at a PR the repository's [[dangerous]] classes mark while he is not present; the two gates are apart, so a runtime-api change merges with no staging pass while he is present and is tested after."""
    landing: list[str] = []
    if below := stack[0].get("under"):
        return Verdict((), False, f"{key(stack[0])} sits on {below}, outside this lane, which the merge queue would land with it")
    for row in stack:
        if row.get("merged"):
            continue
        if why := unready(row):
            return Verdict(tuple(landing), bool(landing), f"{key(row)} waits: {why}")
        if dangerous(row) and not proven(stack):
            return Verdict(tuple(landing), bool(landing), f"{key(row)} waits on a staging proof at {stack[-1]['head'][:12]}, the stack's top head")
        if guarded(row) and not present(heard, moment):
            return Verdict(tuple(landing), bool(landing), f"{key(row)} changes {', '.join(row['guarded']['classes'])} and lands only while he is present")
        landing.append(key(row))
    return Verdict(tuple(landing), bool(landing), "every open PR of the stack lands" if landing else "every PR of the stack merged")


def stacks(lane: dict) -> list[list[dict]]:
    """A lane's PR rows split into stacks, bottom first, each row's `under` naming the PR it sits on and a row with none starting a stack."""
    rows = {key(row): row for row in lane["prs"]}
    above: dict[str | None, list[dict]] = {}
    for row in lane["prs"]:
        above.setdefault(row.get("under") if row.get("under") in rows else None, []).append(row)
    found = []
    for bottom in above.get(None, []):
        chain, todo = [], [bottom]
        while todo:
            row = todo.pop(0)
            chain.append(row)
            todo += above.get(key(row), [])
        found.append(chain)
    return found


def verdicts(lane: dict, heard: datetime | None, moment: datetime) -> list[Verdict]:
    return [stack_verdict(stack, heard, moment) for stack in stacks(lane)]
