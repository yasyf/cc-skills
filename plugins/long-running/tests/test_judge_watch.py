"""judge_watch.py over scripted bus records and a scripted judge: what it flags, to whom, what a recheck records as a miss, and how a dispute reaches root or closes on root's ruling."""

from __future__ import annotations

import json
from pathlib import Path

from long_running import judge
import judge_watch
import pytest

ROW = {"at": "Oct 29 9:16 PM PT", "words": "1 Server shares one process", "repeats": "[]", "answer": '["a201ee6"]'}
MISREAD = judge.Verdict("yes", "one process per destination and key", "shares one process", "He asked for one shared process, and this splits it per key.")
CARRIED = judge.Verdict("no", "", "", "It carries what he said.")


class Scripted(judge_watch.Pass):
    def __init__(self, watch: judge_watch.Watch, records: list[dict], verdicts: list[judge.Verdict | None], cited: list[dict] | None = None):
        super().__init__(watch, dry_run=False)
        self.found, self.cited, self.posts = records, cited or [], []
        self.verdicts = iter(verdicts)

    def cli(self, argv: list[str]) -> str:
        self.posts.append(argv)
        return f"#{9000 + len(self.posts)}\n"

    def rulings(self, *args: str, stdin: str = "") -> str:
        return json.dumps({"rows": {"rule/a201ee6": ROW}, "state_dir": str(self.watch.drive_state)}) if args[0] == "rows" else ""


@pytest.fixture
def watch(tmp_path: Path) -> judge_watch.Watch:
    return judge_watch.Watch("d1", "d1", tmp_path / "judge-watch.json", tmp_path, str(tmp_path))


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    def run(scripted: Scripted) -> Scripted:
        monkeypatch.setattr(judge_watch.cci, "records", lambda cli, drive, *filters, since=0: scripted.cited if filters else scripted.found)
        monkeypatch.setattr(judge_watch.judge, "judge", lambda *args, **kwargs: next(scripted.verdicts))
        monkeypatch.setattr(judge_watch.judge, "said", lambda row, state, env=None: [{"at": row["at"], "words": row["words"], "answered": None}])
        scripted.run()
        return scripted

    return run


def contract(seq: int, text: str = "One upload process per destination and key. rule/a201ee6", lane: str = "lane-a") -> dict:
    return {"seq": seq, "lane": lane, "kind": "contract", "text": text, "refs": {}}


def posted(scripted: Scripted) -> list[dict]:
    return [{"kind": argv[argv.index("--kind") + 1], "re": argv[argv.index("--re") + 1], "to": [argv[i + 1] for i, arg in enumerate(argv) if arg == "--to"], "text": argv[argv.index("--text") + 1]} for argv in scripted.posts if argv[:2] == ["cci", "post"]]


def test_a_contract_the_judge_reads_as_a_misreading_is_flagged_to_its_writer_and_root_in_reply(watch, run):
    [flag] = posted(run(Scripted(watch, [contract(100)], [MISREAD])))

    assert (flag["kind"], flag["re"], flag["to"]) == ("review", "100", ["lane-a", "root"])
    assert flag["text"].startswith("JUDGE contract #100 rule/a201ee6: He asked for one shared process")
    assert json.loads(watch.state.read_text())["cursor"] == 100


@pytest.mark.parametrize("verdict", [CARRIED, None])
def test_a_carried_contract_or_a_failed_judge_posts_nothing(watch, run, verdict):
    assert posted(run(Scripted(watch, [contract(100)], [verdict]))) == []


def test_a_relay_is_judged_only_on_the_rows_it_cites_and_only_from_root(watch, run):
    records = [
        {"seq": 101, "lane": "root", "kind": "decide", "text": "lane-a: build one process per key", "refs": {}},
        {"seq": 102, "lane": "lane-b", "kind": "decide", "text": "one process per key. rule/a201ee6", "refs": {}},
        {"seq": 103, "lane": "root", "kind": "decide", "text": "lane-a: one process per key. rule/a201ee6", "refs": {}},
    ]

    [flag] = posted(run(Scripted(watch, records, [MISREAD])))

    assert flag["re"] == "103" and flag["text"].startswith("JUDGE relay #103")


def test_new_owner_words_recheck_what_cites_the_row_and_a_new_yes_after_a_no_is_a_miss(watch, run):
    calls = watch.drive_state / judge.CALLS
    calls.parent.mkdir(parents=True)
    calls.write_text(json.dumps({"at": "2026-10-30T04:20:00+00:00", "kind": "contract", "subject": "cci:100:rule/a201ee6", "verdict": "no", "seconds": 9}) + "\n")
    announce = {"seq": 200, "lane": "root", "kind": "decision", "text": "Register classify, 1 rows: extended rule/a201ee6.", "refs": {}}

    [flag] = posted(run(Scripted(watch, [announce], [MISREAD], cited=[contract(100)])))

    assert "after his newer words, which the judge missed before; it is now an eval case" in flag["text"]
    case = json.loads((watch.drive_state / "judge" / "misses" / "100-rule-a201ee6.json").read_text())
    assert (case["kind"], case["expect"], case["ruling"]) == ("contract", "yes", "rule/a201ee6")


def test_a_writers_answer_on_a_flag_asks_root_alone_with_both_readings_and_an_owner_pick_becomes_an_eval_case(watch, run):
    """Root #263609: judge-watch ordered board-refresher lanes to post owner cards for contract-reading disputes after four root rulings."""
    first = run(Scripted(watch, [contract(100)], [MISREAD]))
    flag_seq = next(iter(first.saved.flags))
    reply = {"seq": 300, "lane": "lane-a", "kind": "answer", "re": int(flag_seq), "text": "He said one process per Mac; keys still share it.", "refs": {}}
    pick = {"seq": 301, "lane": "board-watch", "kind": "owner", "text": f"Owner on board, Does this contract read your words? (judge-dispute-{flag_seq}): picked “{judge_watch.JUDGE_RIGHT}”, at 9:30 PM", "refs": {}}

    second = run(Scripted(watch, [reply, pick], []))

    [ask] = posted(second)
    assert ask["kind"] == "ask" and ask["to"] == ["root"]
    assert ask["text"].startswith(f"Does lane-a's contract #100 read his words the way he meant them? The judge flagged it on #{flag_seq}")
    card = (watch.drive_state / "judge" / "disputes" / f"{flag_seq}.md").read_text()
    assert MISREAD.sentence in card and reply["text"] in card
    case = json.loads((watch.drive_state / "judge" / "owner" / f"{flag_seq}.json").read_text())
    assert (case["expect"], case["artifact"]) == ("yes", contract(100)["text"])


@pytest.mark.parametrize("ruling", [{"refs": {"prs": [46788]}}, {"re": 300, "refs": {}}], ids=["names-the-pr", "replies-to-the-answer"])
def test_a_dispute_root_already_ruled_on_closes_with_that_ruling_and_asks_nobody(watch, run, ruling):
    first = run(Scripted(watch, [contract(100) | {"refs": {"prs": [46788]}}], [MISREAD]))
    flag_seq = next(iter(first.saved.flags))
    reply = {"seq": 300, "lane": "lane-a", "kind": "answer", "re": int(flag_seq), "text": "The port keeps the source contract.", "refs": {}}
    ruled = {"seq": 290, "lane": "root", "kind": "decide", "text": "the port keeps the source contract (ruling #262338)"} | ruling

    second = run(Scripted(watch, [reply], [], cited=[ruled]))

    [closed] = posted(second)
    assert (closed["kind"], closed["to"]) == ("answer", ["lane-a", "root"])
    assert closed["text"] == f"JUDGE dispute on #{flag_seq} closed by root #290: the port keeps the source contract (ruling #262338)"
    assert "--resolves" in second.posts[-1] and flag_seq not in second.saved.flags
