"""The merge queue's repository adapters, the check states its registry reads, and the ejection kinds it acts on.

STDLIB ONLY. `runner.json` names one adapter under `landing.adapter.kind`, and every queue operation
desk-runner.py's landing desk and mq.py make goes through it: `check` grades the tips read-only and names
each PR's verdict and the prefix that would enqueue, `enqueue` admits that prefix, `queued` reads which PRs
the queue holds, `dequeue` takes one back out, and `recover` frees what an earlier enqueue stranded. `trunk-stack-enqueue` and `graphite-stack-enqueue` drive the repository's own
stack-enqueue script, named relative to the landing checkout by `landing.adapter.script`, for Trunk and legacy Graphite queues; `label` drives
`ledger.py label`, for a repository whose queue takes a merge label. An adapter also reads its own verdict
text: `classify` grades the `approvals` and `required-ci` checks from a PR's verdict, and `ejection` names
why the queue dropped a PR, as `conflict`, `own-red`, `batch-mate-red`, or `flaky-check`, and `blocker`
names what in a `BLOCKED` verdict its lane must fix. `release` clears whatever keeps the adapter from taking
a pulled or ejected head again, for `mq requeue`.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import ghread

PASS = "pass"
PENDING = "pending"
FAIL = "fail"
CHECKS = ("hold", "precondition", "rules-review", "rulings", "fidelity", "finder", "merge-mode", "approvals", "required-ci")
OPT_IN_CHECKS = ("old-app", "build-failed")
CONFLICT = "conflict"
OWN_RED = "own-red"
BATCH_MATE_RED = "batch-mate-red"
FLAKY_CHECK = "flaky-check"
VERDICT_LINE = re.compile(r"^#(?P<pr>\d+) (?P<verdict>[A-Z]+) (?P<sha>[0-9a-f]{0,10}) ?(?P<detail>.*)$", re.MULTILINE)
TRUNK_WOULD_ENQUEUE = re.compile(r"^would enqueue native stack through #\d+: ((?:#\d+ ?)+)$", re.MULTILINE)
TRUNK_REQUIRED_CHECK = re.compile(r"^required check .+ is (\S+)$")
TRUNK_FAILED_CHECKS = frozenset({"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"})
WOULD_ENQUEUE = re.compile(r"^would enqueue ((?:#\d+ ?)+) in one call$", re.MULTILINE)
STATUS_LINE = re.compile(r"^#(?P<pr>\d+) (?P<status>[A-Z_]+) ", re.MULTILINE)
GRAPHITE_QUEUED = frozenset({"QUEUED_TO_MERGE", "WAITING_TO_MERGE", "FAILURE_HANDLING", "REBASING", "MERGED"})
ENQUEUE_OUTCOMES = {0: "enqueued", 1: "blocked", 2: "unsettled", 3: "stranded"}
ENQUEUE_FAILED = 5
DEQUEUE_RELEASED = (0, 1)
TRANSIENT_FAILURE = re.compile(
    r"handshake operation timed out|urlopen error|Connection reset|reset by peer|Remote end closed|Temporary failure in name resolution|nodename nor servname|Name or service not known|HTTP(?: Error)? 5\d\d|Operation timed out|Failed to connect to .* port 443|Recv failure|Could not resolve host"
)
TRANSIENT_ATTEMPTS = 3
TRANSIENT_PAUSE_SECONDS = 20
COMPUTING = re.compile(r"^#(\d+) BLOCKED \S+ GitHub still computing mergeability", re.MULTILINE)
COMPUTE_POLL_SECONDS = 10
COMPUTE_ROUNDS = 12
CLEAR = frozenset({"GREEN", "QUEUED"})
APPROVAL_BLOCKER = re.compile(r"^(?:ai-review |changes requested by |awaiting approval on head|not approved by )")
REVIEW_REFUSED = re.compile(r"^changes requested by |Held —")
CI_FAILED = re.compile(r"^graphite FAILING\w*$")
CI_PENDING = re.compile(r"^(?:CI running|graphite (?:RUNNING|PENDING\w*))")
EJECTED = re.compile(r"^ejected from the merge queue ")
DOWNSTACK_FAILURE = re.compile(r"downstack failures on PR #\[?(\d+)")
SAME_HEAD_RETRY = "retries the same head after its ejection"
DRAFT_ONLY_STACK = re.compile(r"held only this stack, so the red check is its own$")
GITHUB_CONFLICT = re.compile(r"graphite MERGE_CONFLICTS|conflicts with origin/|GitHub reads CONFLICTING")
LABEL_REFUSED = re.compile(r"^REFUSED (?P<reason>.+) \(#(?P<pr>\d+)\)$", re.MULTILINE)
LABEL_CHAIN = re.compile(r"#\d+(?: <- #\d+)*")
LABEL_WOULD = re.compile(r"^would label #(?P<tip>\d+) ", re.MULTILINE)
LABEL_APPROVAL = re.compile(r"has no approval in force|^ai-review ")
LABEL_CI_FAILED = re.compile(r"^(?:commit status (?:failure|error)|failed check runs)")
LABEL_CI_PENDING = re.compile(r"^(?:commit status pending|mergeable_state (?!dirty))")
LABEL_CONFLICT = re.compile(r" conflicts with |^mergeable_state dirty:")
LABEL_FOREIGN = re.compile(r"no drive lane registered it in the ledger, by name or by a branch prefix or stack its branch is on, and it carries no drive label")
LABEL_ROUTED = re.compile(
    r"^(?:unproven: |commit status (?:failure|error) |failed check runs |ai-review is |mergeable_state dirty:|#\d+'s branch \S+ is the base of |#\d+ is based on |#\d+ is its own ancestor )|^\S+ conflicts with \S+ on "
)
UNNAMED_RIDER = "rides the batch through"
ROUTED_BLOCKER = re.compile(
    r"^(?:graphite (?:FAILING\w*|MERGE_CONFLICTS|EVICTED)|unproven: |fidelity (?:failure|error):|conflicts with origin/|GitHub reads CONFLICTING|carries .* already squashed on|changes requested by |ejected from the merge queue)"
)
BLOCKER_HINT = re.compile(r"^(?:run ccx vcs stack submit from|read the failure |.+, so the red check is its own$)")
EVICTED_SEGMENT = "graphite EVICTED"
STACK_ENQUEUE = ".agents/skills/submit-pr/scripts/stack-enqueue"
LABEL_STATE = ("ccx", "vcs", "pr", "status")
LABEL_IN_QUEUE = frozenset({"queued", "landed"})


@dataclass(frozen=True)
class State:
    state: str
    reason: str = ""


@dataclass(frozen=True)
class Gate:
    """One read-only grading of a set of tips: each PR's verdict dict (`pr`, `verdict`, `sha`, `detail`) and the bottom prefix that would enqueue, or None."""

    code: int
    out: str
    err: str
    verdicts: dict[str, dict]
    would: list[str] | None

    @property
    def last(self) -> str:
        lines = (self.err or self.out).strip().splitlines()
        return lines[-1] if lines else f"exit {self.code}"


@dataclass(frozen=True)
class Ejection:
    kind: str
    detail: str
    blamed: str = ""


@dataclass(frozen=True)
class Blocker:
    """What a lane must fix before its PR's head can enqueue, and whether it is a conflict, which a lane with no live session cannot take."""

    text: str
    conflict: bool


def suggested_adapter(checkout: Path) -> dict:
    """The adapter a drive on `checkout` names: the repository's own stack-enqueue script when it carries one, else the merge label."""
    if (checkout / STACK_ENQUEUE).is_file():
        selected = subprocess.run(["git", "-C", str(checkout), "config", "--get", "ccx.queue"], capture_output=True, text=True)
        if selected.returncode not in (0, 1):
            raise RuntimeError(selected.stderr.strip())
        kind = TrunkStackEnqueue.kind if selected.stdout.strip() == "trunk" else GraphiteStackEnqueue.kind
        return {"kind": kind, "script": STACK_ENQUEUE}
    return {"kind": LabelEnqueue.kind}


def segments(verdict: dict) -> list[str]:
    return verdict["detail"].split("; ") if verdict["detail"] else []


def outside_failure(segment: str, stack: set[str]) -> bool:
    named = DOWNSTACK_FAILURE.search(segment)
    return bool(named and named[1] not in stack)


class GraphiteStackEnqueue:
    """The repository's own stack-enqueue script: one Graphite API call per prefix, every gate in its `--check`, and `--dequeue`, `--recover`, and `--status` for the rest."""

    kind = "graphite-stack-enqueue"

    def __init__(self, shell, checkout: Path, config: dict, ledger_py: Callable, repo: str):
        self.shell = shell
        self.script = str(checkout / config["script"])
        self.repo = repo

    def run(self, argv: list[str]):
        """Run the script, and while it reports GitHub still computing a PR's mergeability, read that PR once over REST, which starts GitHub's lazy computation that a GraphQL read alone never does, then run it again every `COMPUTE_POLL_SECONDS` for `COMPUTE_ROUNDS` rounds, so no refusal rests on a mergeability GitHub had not been asked to compute (root #192866, #45291)."""
        done = self.run_settled(argv)
        if not (computing := COMPUTING.findall(done.out)):
            return done
        for pr in dict.fromkeys(computing):
            self.shell.run(ghread.read("api", f"repos/{self.repo}/pulls/{pr}", "--jq", ".mergeable", max_age="0s"))
        for _ in range(COMPUTE_ROUNDS):
            self.shell.sleep(COMPUTE_POLL_SECONDS)
            done = self.run_settled(argv)
            if not COMPUTING.search(done.out):
                break
        return done

    def run_settled(self, argv: list[str]):
        """Run the script, repeating it after a pause while it exits with a transient network failure and no verdict; a verdict or any other failure returns at once."""
        for attempt in range(TRANSIENT_ATTEMPTS):
            if attempt:
                self.shell.sleep(TRANSIENT_PAUSE_SECONDS)
            done = self.shell.run(argv)
            if done.code != ENQUEUE_FAILED or VERDICT_LINE.search(done.out) or not TRANSIENT_FAILURE.search(done.err + done.out):
                break
        return done

    def check(self, tips: list[str], whole: bool, heads: dict[str, str], partial: bool) -> Gate:
        done = self.run([self.script, *tips, "--check", *(["--whole"] if whole else []), *(["--partial"] if partial else [])])
        would = WOULD_ENQUEUE.search(done.out)
        verdicts = {match["pr"]: match.groupdict() for match in VERDICT_LINE.finditer(done.out)}
        return Gate(done.code, done.out, done.err, verdicts, [number.lstrip("#") for number in would.group(1).split()] if would else None)

    def enqueue(self, tops: list[str], whole: bool, heads: dict[str, str]) -> tuple[str, str, str]:
        done = self.run([self.script, *tops, "--whole" if whole else "--partial"])
        outcome = ENQUEUE_OUTCOMES.get(done.code, "failed")
        if outcome == "enqueued" and "enqueue #" not in done.out:
            outcome = "noop"
        last = (done.out or done.err).strip().splitlines()
        return outcome, done.out[-1500:], last[-1] if last else f"exit {done.code}"

    def queued(self, prs: list[str]) -> dict[str, bool] | None:
        done = self.shell.run([self.script, *prs, "--status"])
        statuses = {match["pr"]: match["status"] for match in STATUS_LINE.finditer(done.out)}
        if done.code != 0 or set(statuses) != set(prs):
            return None
        return {pr: statuses[pr] in GRAPHITE_QUEUED for pr in prs}

    def dequeue(self, pr: str) -> tuple[bool, str]:
        done = self.shell.run([self.script, pr, "--dequeue"])
        return done.code in DEQUEUE_RELEASED, ((done.err or done.out).strip().splitlines() or [f"exit {done.code}"])[-1]

    def recover(self, prs: list[str]) -> tuple[int, str]:
        done = self.shell.run([self.script, *prs, "--recover"])
        return done.code, (done.out + done.err).strip()

    def release(self, pr: str, head: str) -> tuple[bool, str]:
        return True, ""

    def rider(self, verdict: dict) -> bool:
        return UNNAMED_RIDER in verdict["detail"]

    def failing_required(self, verdict: dict) -> bool:
        return any(CI_FAILED.match(segment) for segment in segments(verdict))

    def blocker(self, verdict: dict, stack: set[str], gated_red: bool) -> Blocker | None:
        """The segments of a `BLOCKED` verdict a lane must act on, with their hints; an ejection blaming a PR outside `stack` is the batch's failure, not this PR's, and is left out, and so is a failing required check when `gated_red` says only gates and the base are red."""
        kept = [segment for segment in segments(verdict) if not outside_failure(segment, stack) and not (gated_red and CI_FAILED.match(segment))]
        if not any(ROUTED_BLOCKER.match(segment) for segment in kept):
            return None
        text = "; ".join(segment for segment in kept if ROUTED_BLOCKER.match(segment) or BLOCKER_HINT.match(segment))
        return Blocker(text, bool(GITHUB_CONFLICT.search(text)))

    def evicted_only(self, verdict: dict) -> bool:
        """Whether a `BLOCKED` verdict says only that the queue dropped the head, with the submit hint stack-enqueue prints beside it, and nothing a lane must fix."""
        found = segments(verdict)
        return verdict["verdict"] == "BLOCKED" and EVICTED_SEGMENT in found and all(segment == EVICTED_SEGMENT or BLOCKER_HINT.match(segment) for segment in found)

    def classify(self, verdict: dict) -> dict[str, State]:
        if verdict["verdict"] in CLEAR:
            return {"approvals": State(PASS), "required-ci": State(PASS)}
        found = segments(verdict)
        approvals = [segment for segment in found if APPROVAL_BLOCKER.match(segment)]
        failed = [segment for segment in found if CI_FAILED.match(segment)]
        running = [segment for segment in found if CI_PENDING.match(segment)]
        return {
            "approvals": State(FAIL if any(REVIEW_REFUSED.search(segment) for segment in approvals) else PENDING, "; ".join(approvals)) if approvals else State(PASS),
            "required-ci": State(FAIL, "; ".join(failed)) if failed else State(PENDING, "; ".join(running)) if running else State(PASS),
        }

    def ejection(self, verdict: dict, stack: set[str]) -> Ejection | None:
        """Why the queue dropped a PR, read from its verdict: a same-head retry the script grants is a batch-mate's red; an ejection blaming a PR outside `stack` is too; a merge conflict is a conflict; a red check that read green on the PR's own head and failed only in a queue draft holding nothing but this stack is a flaky check; any other red check is the PR's own."""
        found = segments(verdict)
        outside = next((named[1] for named in DOWNSTACK_FAILURE.finditer(verdict["detail"]) if named[1] not in stack), "")
        if verdict["verdict"] in CLEAR:
            return Ejection(BATCH_MATE_RED, verdict["detail"], outside) if SAME_HEAD_RETRY in verdict["detail"] else None
        ejected = next((segment for segment in found if EJECTED.match(segment)), None)
        if ejected is None:
            return Ejection(CONFLICT, verdict["detail"]) if any(GITHUB_CONFLICT.search(segment) for segment in found) else None
        if outside:
            return Ejection(BATCH_MATE_RED, verdict["detail"], outside)
        if "conflict" in ejected.lower():
            return Ejection(CONFLICT, verdict["detail"])
        if any(DRAFT_ONLY_STACK.search(segment) for segment in found):
            return Ejection(FLAKY_CHECK, verdict["detail"])
        return Ejection(OWN_RED, verdict["detail"])


class TrunkStackEnqueue(GraphiteStackEnqueue):
    kind = "trunk-stack-enqueue"

    def __init__(self, shell, checkout: Path, config: dict, ledger_py: Callable, repo: str):
        super().__init__(shell, checkout, config, ledger_py, repo)
        self.ci = str(checkout / "tools/ci")

    def run_settled(self, argv: list[str]):
        return self.shell.run(argv)

    def check(self, tips: list[str], whole: bool, heads: dict[str, str], partial: bool) -> Gate:
        done = self.run([self.script, *tips, "--check", *(["--whole"] if whole else [])])
        would = TRUNK_WOULD_ENQUEUE.search(done.out)
        verdicts = {match["pr"]: match.groupdict() for match in VERDICT_LINE.finditer(done.out)}
        return Gate(done.code, done.out, done.err, verdicts, [number.lstrip("#") for number in would.group(1).split()] if would else None)

    def enqueue(self, tops: list[str], whole: bool, heads: dict[str, str]) -> tuple[str, str, str]:
        done = self.run([self.script, *tops, *(["--whole"] if whole else [])])
        outcome = ENQUEUE_OUTCOMES.get(done.code, "failed")
        if outcome == "enqueued" and not re.search(r"^#\d+ (?:QUEUED \S+ native stack covers|MERGED \S+ Trunk reports merged)", done.out, re.MULTILINE):
            outcome = "noop"
        last = (done.out or done.err).strip().splitlines()
        return outcome, done.out[-1500:], last[-1] if last else f"exit {done.code}"

    def queued(self, prs: list[str]) -> dict[str, bool] | None:
        done = self.shell.run([self.ci, "trunk", "status", *prs])
        if done.code != 0:
            return None
        statuses = json.loads(done.out)
        if set(statuses) != set(prs):
            return None
        return {pr: bool(statuses[pr] and statuses[pr]["isCurrentlySubmittedToQueue"]) for pr in prs}

    def dequeue(self, pr: str) -> tuple[bool, str]:
        done = self.shell.run([self.script, pr, "--dequeue"])
        released = done.code == 0 or (done.code == 1 and done.out.strip() == f"#{pr} NOT_QUEUED")
        return released, ((done.err or done.out).strip().splitlines() or [f"exit {done.code}"])[-1]

    def recover(self, prs: list[str]) -> tuple[int, str]:
        return 1, "Trunk recovery requires inspecting its verification run; use mq dequeue or the repository's explicit retry gate"

    def release(self, pr: str, head: str) -> tuple[bool, str]:
        return False, f"inspect Trunk's failed verification, then explicitly run {self.script} {pr} --flake; this adapter does not automatically retry a failed head"

    def classify(self, verdict: dict) -> dict[str, State]:
        states = super().classify(verdict)
        if verdict["verdict"] in CLEAR:
            return states
        checks = [(part, match[1]) for part in segments(verdict) if (match := TRUNK_REQUIRED_CHECK.match(part))]
        failed = [part for part, state in checks if state in TRUNK_FAILED_CHECKS]
        pending = [part for part, state in checks if state not in TRUNK_FAILED_CHECKS]
        absent = "GitHub returned no required checks" in segments(verdict)
        states["required-ci"] = State(FAIL, "; ".join(failed)) if failed else State(PENDING, "; ".join(pending)) if pending else State(PENDING, "GitHub returned no required checks") if absent else State(PASS)
        return states

    def failing_required(self, verdict: dict) -> bool:
        return self.classify(verdict)["required-ci"].state == FAIL

    def blocker(self, verdict: dict, stack: set[str], gated_red: bool) -> Blocker | None:
        kept = [part for part in segments(verdict) if not (gated_red and (match := TRUNK_REQUIRED_CHECK.match(part)) and match[1] in TRUNK_FAILED_CHECKS)]
        actionable = [part for part in kept if ROUTED_BLOCKER.match(part) or TRUNK_REQUIRED_CHECK.match(part) or part.startswith(("Trunk indexed ", "Trunk's last attempt failed", "GitHub mergeability ", "GitHub merge state ", "base "))]
        if not actionable:
            return None
        return Blocker("; ".join(actionable), bool(re.search(r"conflicts with origin/|GitHub mergeability CONFLICTING|GitHub merge state DIRTY", "; ".join(actionable))))

    def ejection(self, verdict: dict, stack: set[str]) -> Ejection | None:
        if any(re.search(r"conflicts with origin/|GitHub mergeability CONFLICTING|GitHub merge state DIRTY", part) for part in segments(verdict)):
            return Ejection(CONFLICT, verdict["detail"])
        return None


class LabelEnqueue:
    """`ledger.py label`, for a repository whose merge queue takes a merge label: every guard in its `--dry-run`, the label on the tip enqueues the whole downstack, and `ledger.py unlabel` pulls it."""

    kind = "label"

    def __init__(self, shell, checkout: Path, config: dict, ledger_py: Callable, repo: str):
        self.shell = shell
        self.checkout = str(checkout)
        self.ledger_py = ledger_py
        self.ledger = config["ledger"]
        self.repo = repo

    def label(self, tip: str, head: str, *flags: str):
        return self.ledger_py("label", "--ledger", self.ledger, "--repo", self.repo, "--pr", tip, *(["--expect-head", head] if head else []), "--checkout", self.checkout, *flags)

    def check(self, tips: list[str], whole: bool, heads: dict[str, str], partial: bool) -> Gate:
        verdicts: dict[str, dict] = {}
        would: list[str] = []
        outs, errs, code = [], [], 0
        for tip in tips:
            done = self.label(tip, heads.get(tip, ""), "--dry-run")
            outs.append(done.out)
            errs.append(done.err)
            code = code or done.code
            refused = {match["pr"]: match["reason"] for match in LABEL_REFUSED.finditer(done.out)}
            chain = max(LABEL_CHAIN.findall(done.out), key=len, default=f"#{tip}")
            for pr in (number.lstrip("#") for number in chain.split(" <- ")):
                verdicts[pr] = {"pr": pr, "verdict": "BLOCKED" if pr in refused else "GREEN", "sha": heads.get(pr, "")[:10], "detail": refused.get(pr, "")}
            if not refused and LABEL_WOULD.search(done.out):
                would += [pr for pr in (number.lstrip("#") for number in chain.split(" <- ")) if pr not in would]
        return Gate(code, "".join(outs), "".join(errs), verdicts, would or None)

    def enqueue(self, tops: list[str], whole: bool, heads: dict[str, str]) -> tuple[str, str, str]:
        outcome, outs = "enqueued", []
        for top in tops:
            done = self.label(top, heads.get(top, ""))
            outs.append(done.out)
            if done.code:
                outcome = "blocked" if done.code == 1 else "failed"
                break
        last = "".join(outs).strip().splitlines()
        return outcome, "".join(outs)[-1500:], last[-1] if last else outcome

    def queued(self, prs: list[str]) -> dict[str, bool] | None:
        done = self.shell.run([*LABEL_STATE, "-R", self.repo, "--json", *prs])
        if done.code != 0:
            return None
        queue = {str(entry["number"]): entry["queue"] for entry in json.loads(done.out)}
        return {pr: queue.get(pr) in LABEL_IN_QUEUE for pr in prs} if set(queue) >= set(prs) else None

    def dequeue(self, pr: str) -> tuple[bool, str]:
        done = self.ledger_py("unlabel", "--ledger", self.ledger, "--repo", self.repo, "--pr", pr, "--reason", "mq dequeue")
        return done.code == 0, ((done.err or done.out).strip().splitlines() or [f"exit {done.code}"])[-1]

    def recover(self, prs: list[str]) -> tuple[int, str]:
        return 1, "a label queue strands nothing to recover; pull a queued label with mq dequeue"

    def release(self, pr: str, head: str) -> tuple[bool, str]:
        done = self.ledger_py("requeue", "--ledger", self.ledger, "--repo", self.repo, "--pr", pr, "--expect-head", head)
        return done.code == 0, ((done.err or done.out).strip().splitlines() or [f"exit {done.code}"])[-1]

    def rider(self, verdict: dict) -> bool:
        return bool(LABEL_FOREIGN.search(verdict["detail"]))

    def failing_required(self, verdict: dict) -> bool:
        return bool(LABEL_CI_FAILED.search(verdict["detail"]))

    def evicted_only(self, verdict: dict) -> bool:
        return False

    def blocker(self, verdict: dict, stack: set[str], gated_red: bool) -> Blocker | None:
        """`ledger.py label` names one refusal per PR, and only a red check, a conflict, an ai-review finding, or a stack shape the lane must fix routes to it; a red check is left out when `gated_red` says only gates and the base are red."""
        reason = verdict["detail"]
        if not LABEL_ROUTED.search(reason) or (gated_red and LABEL_CI_FAILED.search(reason)):
            return None
        return Blocker(reason, bool(LABEL_CONFLICT.search(reason)))

    def classify(self, verdict: dict) -> dict[str, State]:
        reason = verdict["detail"]
        return {
            "approvals": State(PENDING, reason) if LABEL_APPROVAL.search(reason) else State(PASS),
            "required-ci": State(FAIL, reason) if LABEL_CI_FAILED.search(reason) else State(PENDING, reason) if LABEL_CI_PENDING.search(reason) else State(PASS),
        }

    def ejection(self, verdict: dict, stack: set[str]) -> Ejection | None:
        reason = verdict["detail"]
        if LABEL_CONFLICT.search(reason):
            return Ejection(CONFLICT, reason)
        if LABEL_CI_FAILED.search(reason):
            return Ejection(OWN_RED, reason)
        return None


def adapter(shell, checkout: Path, config: dict, ledger_py: Callable, repo: str, ledger: str):
    """The adapter `landing.adapter` names; a kind no adapter has raises KeyError, which the landing desk refuses to start on."""
    kinds = {kind.kind: kind for kind in (TrunkStackEnqueue, GraphiteStackEnqueue, LabelEnqueue)}
    return kinds[config["kind"]](shell, checkout, {**config, "ledger": ledger}, ledger_py, repo)
