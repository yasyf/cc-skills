from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType

from long_running import board_rows, cci, ghread, grades
import pytest
import test_board_rows as legacy


@pytest.fixture(scope="module")
def oracle():
    bundle = json.loads((Path(__file__).parent / "fixtures" / "board-provider-retained845.json").read_text())
    assert hashlib.sha256(Path(ghread.__file__).read_bytes()).hexdigest() == bundle["shared_ghread_sha256"]
    name = "_board_provider_retained845"
    package = ModuleType(name)
    package.__path__ = []
    sys.modules[name] = package
    sys.modules[name + ".ghread"] = ghread
    for module in ("cci", "grades", "board_rows"):
        saved = bundle["modules"][module + ".py"]
        source = base64.b64decode(saved["source_b64"], validate=True)
        assert hashlib.sha256(source).hexdigest() == saved["sha256"]
        loaded = ModuleType(name + "." + module)
        loaded.__file__ = str(Path(__file__).parent / "retained845" / (module + ".py"))
        loaded.__package__ = name
        sys.modules[loaded.__name__] = loaded
        setattr(package, module, loaded)
        exec(compile(source, loaded.__file__, "exec"), loaded.__dict__)
    yield package
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            del sys.modules[key]


class SnapshotShell(legacy.FakeShell):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.events = []
        self.reads = []
        self.requests = []
        self.errors = {}
        self.packet = None
        self.returned_reviews = 0

    def run(self, argv, env=None):
        self.calls.append(list(argv))
        if argv[:2] == ["cci", "tail"]:
            category = "reviews" if "--kind=review" in argv else "withdrawals" if "--kind=withdraw" in argv else "rulings"
            self.events.append(category)
            if error := self.errors.get(category):
                return board_rows.Done(1, "", "cci: " + error + "\n")
        elif argv[:2] == ["cc-present", "outcomes"]:
            self.events.append("outcomes")
        return super().run(argv, env)

    def tailed(self, argv):
        found = super().tailed(argv)
        page = [record for record in found if not cci.expired(record, datetime(2026, 11, 2, tzinfo=timezone.utc))][:500]
        if "--kind=review" in argv:
            self.returned_reviews += len(page)
        return page

    def read(self, path):
        self.reads.append(path)
        try:
            return Path(path).read_text(errors="replace")
        except OSError:
            return None

    def board_records(self, request):
        self.events.append("provider")
        self.requests.append(deepcopy(request))
        if isinstance(self.packet, board_rows.ReadFailed):
            raise self.packet
        return deepcopy(self.packet)


def config():
    return {"drive": legacy.DRIVE, "landing": {"repo": legacy.REPO, "fidelity_lane": ["fidelity-desk", "fidelity-desk-2"], "ledger": legacy.LEDGER, "checkout": "/checkout", "go_lanes": ["merge-owner"]}}


def body(record):
    path = Path(record.get("refs", {}).get("path") or "")
    if not path.is_file():
        return {"body_b64": None, "is_file": False, "error": ""}
    try:
        data = path.read_bytes()
    except OSError as error:
        return {"body_b64": None, "is_file": True, "error": str(error)}
    return {"body_b64": base64.b64encode(data).decode(), "is_file": True, "error": ""}


def packet(shell):
    found = [deepcopy(record) for record in shell.records if not cci.expired(record, datetime(2026, 11, 2, tzinfo=timezone.utc))]
    rulings = [{"record": record, "body_b64": body(record)["body_b64"]} for record in found if record["kind"] in {"go", "decide", "decision", "answer"}]
    reviews = [record for record in found if record["kind"] in {"review", "head"}]
    selected = [record for record in reviews if any(pr in {"42590", "42591"} for pr in grades.about_prs(record, legacy.REPO))]
    ruling_error = shell.errors.get("rulings") or shell.errors.get("withdrawals")
    review_error = shell.errors.get("reviews")
    return {
        "contract": "board-query-v1", "scope_id": "fixture-scope", "source_revision": 42, "clock_ms": 1793577600000,
        "rulings_withdrawals": {"error": ruling_error, "rulings": [] if ruling_error else rulings, "withdrawals": [] if ruling_error else [record for record in found if record["kind"] == "withdraw" and record["lane"] == "root"]},
        "reviews": {"error": review_error, "records": [] if review_error else selected, "lanes": [] if review_error else sorted({record["lane"] for record in reviews}), "bodies": {str(record["seq"]): body(record) for record in selected}},
    }


def summary(module, shell, block, directory, provider=None):
    arguments = (shell, config(), deepcopy(block), ["cc-present", "outcomes", "--all", "--json"], directory / "finders")
    rows, found, warnings = module.check(*arguments, **({"board_provider": provider} if provider else {}))
    return [asdict(row) for row in rows], found, warnings


@pytest.mark.parametrize("case", ["ordinary", "closing", "withdraw", "delegated", "historical-heads", "lane-inventory", "expired", "one-tap", "listed", "unheaded"])
def test_retained845_full_tail_parity_and_replaced_stream_cost(oracle, tmp_path, case):
    shell = SnapshotShell()
    shell.pull("42590")
    shell.pull("42591")
    shell.graded("42590")
    shell.graded("42591")
    shell.ledger = {"42590", "42591"}
    block = legacy.card(legacy.row("42590"))
    if case == "closing":
        path = tmp_path / "ruling"
        path.write_bytes(f"closed #42590 at {legacy.HEAD}\r\n".encode())
        shell.post("decide", "root", "closed #42590…", path=str(path))
    elif case in {"withdraw", "delegated"}:
        seq = shell.post("go", "merge-owner-2" if case == "delegated" else "root", f"go #42590 at {legacy.HEAD}")
        if case == "withdraw":
            shell.post("withdraw", "root", "withdraw that go")
            shell.records[-1]["resolves"] = seq
    elif case == "historical-heads":
        shell.post("head", "builder", f"{legacy.NEW_HEAD} (was {legacy.HEAD}) patch-identical", "42590")
        shell.graded("42590", legacy.NEW_HEAD)
    elif case == "lane-inventory":
        shell.records = [record for record in shell.records if record["lane"] != "fidelity-desk"]
        shell.post("review", "fidelity-desk-7", "fidelity: ok deadbee checked rule/7", "99999")
    elif case == "expired":
        shell.post("go", "root", f"go #42590 at {legacy.HEAD}")
        shell.records[-1]["expires_at"] = "2026-11-01T00:00:00+00:00"
    elif case == "one-tap":
        block = legacy.card(legacy.row("42590", md="One tap approves both #42590 and #42591."), legacy.row("42591"))
        shell.verdicts = {"merge-triage": {"merge-row-42591": {"verdict": "rejected", "head": legacy.HEAD}}}
    elif case == "listed":
        block = {"id": "approved", "type": "markdown", "text": "- #42590: approved earlier"}
    elif case == "unheaded":
        block = legacy.card(legacy.row("42590"), legacy.row("42591", head=""))
    shell.packet = packet(shell)
    reference = deepcopy(shell)
    want = summary(oracle.board_rows, reference, block, tmp_path)
    got = summary(board_rows, shell, block, tmp_path, "board-query-v1")
    assert got == want
    assert len(shell.requests) == 1 and shell.reads == []
    assert not any(argv[:2] == ["cci", "tail"] for argv in shell.calls)
    expected = {"ruling_argv": next(argv for argv in reference.calls if "--kind=answer" in argv), "withdraw_argv": next(argv for argv in reference.calls if "--kind=withdraw" in argv), "review_argv": next(argv for argv in reference.calls if "--kind=review" in argv)}
    assert {key: shell.requests[0][key] for key in expected} == expected
    assert shell.requests[0]["row_ids"] == [row.id for row in oracle.board_rows.rows_of(block)]


@pytest.mark.parametrize("data,short", [(b"whole\r\nbody\rending", "short"), (b"", "short"), (b"   \r\n", "short"), (b"\xffbad", "short"), (b"clipped continuation", "clipped…"), (b"rewritten content", "clipped…")])
def test_strict_review_body_matches_retained845_without_live_path(oracle, tmp_path, monkeypatch, data, short):
    path = tmp_path / "review"
    path.write_bytes(data)
    record = {"text": short, "refs": {"path": str(path)}}
    want = oracle.grades.full_text(record)
    captured = body(record)
    path.write_text("changed after closing cut")
    def no_path(*args, **kwargs):
        pytest.fail("captured review re-opened a live path")
    monkeypatch.setattr(grades, "Path", no_path)
    assert grades.full_text(record | {"_captured_body": cci.CapturedBody(captured)}) == want


def test_strict_nonfile_and_open_error_timing(oracle, tmp_path, monkeypatch):
    record = {"text": "short", "refs": {"path": str(tmp_path)}}
    assert grades.full_text(record | {"_captured_body": cci.CapturedBody(body(record))}) == oracle.grades.full_text(record) == "short"
    path = tmp_path / "unreadable"
    path.write_text("private body")
    record = {"text": "short", "refs": {"path": str(path)}}
    def denied(*args, **kwargs):
        raise OSError("fixture open denied")
    monkeypatch.setattr(Path, "open", denied)
    captured = body(record)
    with pytest.raises(OSError, match="fixture open denied"):
        oracle.grades.full_text(record)
    held = record | {"_captured_body": cci.CapturedBody(captured)}
    with pytest.raises(OSError, match="fixture open denied"):
        grades.full_text(held)


@pytest.mark.parametrize("data", [None, b"", b" \r\n", b"\xffinvalid\r\n", b"full #42590 body\r\n"])
def test_ruling_body_replace_newline_and_short_fallback(oracle, tmp_path, monkeypatch, data):
    path = tmp_path if data is None else tmp_path / "ruling"
    if data is not None:
        path.write_bytes(data)
    shell = SnapshotShell()
    shell.pull("42590")
    shell.ledger = {"42590"}
    shell.post("decide", "root", "root-short #42590", path=str(path))
    shell.packet = packet(shell)
    want = [record["full"] for record in oracle.board_rows.root_rulings(deepcopy(shell), legacy.DRIVE, ["root", "merge-owner"])]
    seen = []
    closing = board_rows.closing_ruling
    def observe(records, pr, head):
        seen.extend(record["full"] for record in records)
        return closing(records, pr, head)
    monkeypatch.setattr(board_rows, "closing_ruling", observe)
    summary(board_rows, shell, legacy.card(legacy.row("42590")), tmp_path, "board-query-v1")
    assert seen == want and shell.reads == []


@pytest.mark.parametrize("category", ["rulings", "withdrawals", "reviews"])
def test_category_error_wrapper_and_warning_order(oracle, tmp_path, category):
    shell = SnapshotShell()
    shell.pull("42590")
    shell.ledger = {"42590"}
    shell.errors[category] = "read failed " + "é" * 250 + "\nsecond line"
    shell.outcomes_fail = True
    shell.packet = packet(shell)
    reference = deepcopy(shell)
    block = legacy.card(legacy.row("42590"))
    assert summary(board_rows, shell, block, tmp_path, "board-query-v1") == summary(oracle.board_rows, reference, block, tmp_path)
    assert len(shell.requests) == 1
    assert not any(argv[:2] == ["cci", "tail"] for argv in shell.calls)
    assert shell.events.index("provider") < shell.events.index("outcomes")


def test_unready_provider_never_falls_back(tmp_path):
    shell = SnapshotShell()
    shell.pull("42590")
    shell.ledger = {"42590"}
    shell.packet = board_rows.ReadFailed("board scope incomplete")
    _, _, warnings = summary(board_rows, shell, legacy.card(legacy.row("42590")), tmp_path, "board-query-v1")
    assert len(shell.requests) == 1
    assert sum("board scope incomplete" in warning for warning in warnings) == 2
    assert not any(argv[:2] == ["cci", "tail"] for argv in shell.calls)


def test_next_check_reads_new_closing_cut(tmp_path):
    shell = SnapshotShell()
    shell.pull("42590")
    shell.graded("42590")
    shell.ledger = {"42590"}
    shell.packet = packet(shell)
    block = legacy.card(legacy.row("42590"))
    first = summary(board_rows, shell, block, tmp_path, "board-query-v1")
    shell.post("go", "root", f"go #42590 at {legacy.HEAD}")
    shell.packet = packet(shell)
    shell.packet["source_revision"] = 43
    second = summary(board_rows, shell, block, tmp_path, "board-query-v1")
    assert first != second and len(shell.requests) == 2


def test_raw_request_identity_and_original_argument_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CCI_HOME", "relative-inbox")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-session")
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-session")
    monkeypatch.setenv("CCI_AGENT_ID", "private-agent")
    request = cci.board_request("drive", "Owner/Repo", ["desk-2"], ["001", "1"], ["merge-row-001", "row-without-head"])
    assert request["home"] == str(tmp_path / "relative-inbox")
    assert (request["session"], request["agent"], request["cwd"]) == ("claude-session", "private-agent", str(tmp_path))
    assert request["go_lanes"] == ["desk-2"] and request["prs"] == ["001", "1"]
    assert request["row_ids"] == ["merge-row-001", "row-without-head"]
    assert request["ruling_argv"] == ["cci", "tail", "--drive", "drive", "--since-seq", "0", "--json", "--budget", "4000000", "--kind=answer", "--kind=decide", "--kind=decision", "--kind=go"]


def test_cli_opt_in_and_uncovered_banner_tail_are_distinct(tmp_path, capsys):
    shell = SnapshotShell()
    shell.pull("42590")
    shell.graded("42590")
    shell.ledger = {"42590"}
    shell.packet = packet(shell)
    assert legacy.gate(tmp_path, shell, legacy.card(legacy.row("42590")), "--check", "--json", "--board-provider=board-query-v1") == 0
    capsys.readouterr()
    tails = [argv for argv in shell.calls if argv[:2] == ["cci", "tail"]]
    assert len(shell.requests) == 1 and len(tails) == 1 and "--topic=dev-red" in tails[0]


@pytest.mark.parametrize("n", [32, 10000, 100000])
def test_retained845_population_parity_and_fixture_call_counts(oracle, tmp_path, n):
    shell = SnapshotShell()
    shell.pull("42590")
    shell.graded("42590")
    shell.ledger = {"42590"}
    for _ in range(n):
        shell.post("review", "finder-desk", "unrelated historical review deadbee", "99999")
    shell.packet = packet(shell)
    reference = deepcopy(shell)
    block = legacy.card(legacy.row("42590"))
    assert summary(board_rows, shell, block, tmp_path, "board-query-v1") == summary(oracle.board_rows, reference, block, tmp_path)
    assert reference.returned_reviews == n + 2
    assert shell.returned_reviews == 0 and len(shell.requests) == 1
    assert not any(argv[:2] == ["cci", "tail"] for argv in shell.calls)
    print(f"LR_BOARD_PROVIDER_PYTHON_FIXTURE n={n} legacy_review_rows={reference.returned_reviews} replaced_tail_rows={shell.returned_reviews} provider_calls={len(shell.requests)}")
