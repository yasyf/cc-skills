import ast
import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from test_bus import bus
from test_desk_runner import config, runner_module, shell

from long_running import ledger


@pytest.fixture
def machine_bus(tmp_path, monkeypatch):
    home = tmp_path / "machine-bus"
    monkeypatch.setenv("CCI_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")
    monkeypatch.setenv("CODEX_THREAD_ID", "")
    monkeypatch.setenv("CCI_AGENT_ID", "")
    monkeypatch.setenv("PATH", os.environ["CCI_MACHINE_BINARY_DIR"] + os.pathsep + os.environ["PATH"])

    def run(*args):
        return subprocess.run(["cci", *args], text=True, capture_output=True, check=True).stdout

    records = [json.loads(run("post", "--drive", "d1", "--lane", "root", "--kind", "note", "--text", text, "--id", key, "--json")) for text, key in [("local", "local-target"), ("owner", "fixture-duplicate"), ("remote", "remote-target")]]
    with sqlite3.connect(home / "inbox.db") as db:
        db.execute('INSERT INTO members(node,public_key,tier,via,"by",at) VALUES(?,X\'\',\'owner\',\'relay\',\'fixture\',1)', ("aaaaaaaaaaaaaaaa",))
        db.execute("UPDATE records SET origin=?,origin_seq=? WHERE seq=?", ("aaaaaaaaaaaaaaaa", 7, records[1]["seq"]))
        db.execute("UPDATE records SET origin=?,origin_seq=? WHERE seq=?", ("bbbbbbbbbbbbbbbb", 100, records[2]["seq"]))
    relay = home / "relay" / "machine"
    relay.mkdir(parents=True)
    (relay / "config.json").write_text(json.dumps({"mesh": "machine", "url": "https://relay.invalid", "tier": "service"}))
    return {"home": home, "run": run, "owner": records[1]["seq"], "remote": records[2]["seq"], "local": records[0]["seq"]}


def all_records(machine_bus):
    return [json.loads(line) for line in machine_bus["run"]("tail", "--drive", "d1", "--since", "0", "--json", "--all").splitlines()]


def test_actual_runner_posts_keep_remote_duplicate_and_pairwise_machine_sequences(config, tmp_path, machine_bus):
    runner = runner_module.Runner(runner_module.Shell(), runner_module.Config.load(config), runner_module.actions.Store(tmp_path / "post-book"))
    historical_path = Path(os.environ["CCI_MACHINE_LEGACY_RUNNER"])
    historical = ast.parse(historical_path.read_bytes())
    owner = next(node for node in historical.body if isinstance(node, ast.ClassDef) and node.name == "Runner")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "cci_post")
    namespace = vars(runner_module).copy()
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(historical_path), "exec"), namespace)
    legacy = type("LegacyRunner", (runner_module.Runner,), {"cci_post": namespace["cci_post"]})(runner_module.Shell(), runner.config, runner_module.actions.Store(tmp_path / "legacy-post-book"))
    previous = legacy.cci_post("note", "duplicate", "root", "machine", "duplicate", record_id="fixture-duplicate")
    assert previous.code == 0 and previous.out == "#7 (duplicate)\n"
    duplicate = runner.cci_post("note", "duplicate", "root", "machine", "duplicate", record_id="fixture-duplicate")
    assert duplicate == runner_module.Done(0, f"#{machine_bus['owner']}\n", "")
    owner, remote, local = (str(machine_bus[key]) for key in ("owner", "remote", "local"))
    posted = runner.cci_post("note", "pairwise", "root", "machine", "pairwise", record_id="machine-pairwise", flags=["--pairwise", "--re-seq", owner + "," + local, "--resolves-seq", remote + "," + owner])
    assert posted.code == 0, posted
    seqs = [int(line.removeprefix("#")) for line in posted.out.splitlines()]
    found = {row["seq"]: row for row in all_records(machine_bus)}
    assert len(seqs) == 2
    assert [(found[seq]["re"], found[seq]["resolves"]) for seq in seqs] == [(int(owner), int(remote)), (int(local), int(owner))]
    assert all("@" in found[seq]["num"] for seq in seqs)
    repeated = runner.cci_post("note", "pairwise", "root", "machine", "pairwise", record_id="machine-pairwise", flags=["--pairwise", "--re-seq", owner + "," + local, "--resolves-seq", remote + "," + owner])
    assert repeated.out == posted.out
    print(json.dumps({"provider_source": os.environ["CCI_MACHINE_PROVIDER_SOURCE"], "runner_sha256": hashlib.sha256(Path(runner_module.__file__).read_bytes()).hexdigest(), "legacy_duplicate_receipt": previous.out.strip(), "current_duplicate_receipt": duplicate.out.strip(), "remote_duplicate_internal_seq": machine_bus["owner"], "remote_duplicate_display_num": found[machine_bus["owner"]]["num"], "pairwise_internal_receipts": seqs, "pairwise_targets": [(found[seq]["re"], found[seq]["resolves"]) for seq in seqs]}))


def test_actual_bus_and_ledger_replies_use_internal_references(machine_bus, capsys):
    owner = str(machine_bus["owner"])
    assert bus.main(["post", "--bus", "d1", "--from", "root", "--kind", "answer", "--re", owner, "--text", "bus reply"], bus.Shell()) == 0
    bus_receipt = capsys.readouterr().out.strip()
    messages = ledger.Messages(ledger.Shell(), "d1")
    posted = messages.post({"lane": "root", "kind": "idle", "pr": "machine", "text": "ledger note", "head": ""})
    messages.answer("#" + owner, {"pr": "machine"}, "ledger reply")
    found = all_records(machine_bus)
    assert next(row for row in found if row["text"] == "bus reply")["re"] == int(owner)
    assert next(row for row in found if row["text"].startswith("ledger reply"))["re"] == int(owner)
    assert next(row for row in found if row["seq"] == int(bus_receipt.removeprefix("#")))["text"] == "bus reply"
    assert next(row for row in found if row["seq"] == int(posted.removeprefix("#")))["text"] == "ledger note"
