import hashlib
import json
import os
from pathlib import Path

from native_cci_integration import native_cci, observed_commands
from test_desk_runner import config, runner_module, shell


def test_actual_landing_selected_blockers_retire_external_closures_with_indexed_book_candidates(config, tmp_path, monkeypatch, native_cci):
    settings = runner_module.Config.load(config)
    current = runner_module.Landing(runner_module.Runner(runner_module.Shell(), settings, runner_module.actions.Store(tmp_path / "selected-book")), settings.landing)
    decoded = 0
    load = runner_module.actions.Action.load

    def loaded(record):
        nonlocal decoded
        decoded += 1
        return load(record)

    try:
        with current.book.store.transaction(runner_module.LANDING) as state:
            for index in range(native_cci.population):
                key = f"unrelated:{index}"
                state.accept(key, "bus", json.dumps({"kind": "note", "text": "unrelated completed history"}), None, current.runner.now())
                state.start(key, current.runner.now())
                state.complete(key, {"posted": f"#{index + 100000}"})
        for key, head, lane in [("external", "cccc333333", "root@foreign"), ("stale", "cccc333333", "a,b"), ("current", "eeee555555", "owner@foreign")]:
            assert current.post_record("blocker", key, lane, f"#29020 {head}: pending", "29020")
        original = {key: dict(current.book.action(runner_module.LANDING, key).response) for key in ("external", "stale", "current")}

        def resolve(key):
            seq = runner_module.cci.receipt_seq(original[key]["posted"])
            result = current.runner.cci_post("decide", "external private resolution", "root", "selected-ci", f"foreign:{key}", flags=["--resolves-seq", str(seq)], drive="foreign")
            assert result.code == 0, result

        resolve("external")
        monkeypatch.setattr(runner_module.actions.Action, "load", loaded)
        costs = observed_commands(monkeypatch)
        calls = []

        def query():
            before = decoded, costs.copy(), native_cci.counts()["native_requests"]
            current.resolve_stale_blockers({"29020": {"state": "open", "head": "eeee555555"}})
            result = {"action_dtos": decoded - before[0], "commands": {key: costs[key] - before[1][key] for key in costs}, "native_requests": native_cci.counts()["native_requests"] - before[2]}
            calls.append(result)
            return result

        first = query()
        assert first["action_dtos"] == 5
        assert first["native_requests"] == 1
        assert first["commands"] == {"tail_processes": 0, "post_processes": 1, "temporary_feeds": 1}
        external = current.book.action(runner_module.LANDING, "external")
        assert external.status == "verified" and external.response == original["external"]
        assert external.verification_receipt["closed"] == original["external"]["posted"]
        stale = current.book.action(runner_module.LANDING, "stale")
        assert stale.status == "verified" and stale.response == original["stale"] and "resolved" in stale.verification_receipt
        assert current.book.action(runner_module.LANDING, "current").status == "completed"
        second = query()
        assert second == {"action_dtos": 1, "commands": {"tail_processes": 0, "post_processes": 0, "temporary_feeds": 0}, "native_requests": 1}
        resolve("current")
        third = query()
        assert third["action_dtos"] == 2 and third["native_requests"] == 1
        assert third["commands"] == {"tail_processes": 0, "post_processes": 0, "temporary_feeds": 0}
        assert current.book.action(runner_module.LANDING, "current").verification_receipt["closed"] == original["current"]["posted"]
        assert query() == {"action_dtos": 0, "commands": {"tail_processes": 0, "post_processes": 0, "temporary_feeds": 0}, "native_requests": 0}
        print(json.dumps({"population": native_cci.population, "provider_source": os.environ["CCI_NATIVE_PROVIDER_SOURCE"], "runner_sha256": hashlib.sha256(Path(runner_module.__file__).read_bytes()).hexdigest(), "native_client_sha256": hashlib.sha256(Path(runner_module.cci.__file__).read_bytes()).hexdigest(), "calls": calls, "original_receipts": original, "scope": "real Landing/Book/Store indexed local candidates and selected native Tail; private official CLI posts/global foreign resolutions; no physical IO claim"}))
    finally:
        current.close()
