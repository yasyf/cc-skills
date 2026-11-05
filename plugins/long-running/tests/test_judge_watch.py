"""judge_watch.py over scripted bus records and a scripted judge: what it flags, to whom, what a recheck records as a miss, and how a dispute reaches the coordinator or closes on the coordinator's ruling."""

from __future__ import annotations

import json
from pathlib import Path

from long_running import judge
import judge_watch
import pytest

ROW = {"at": "Oct 29 9:16 PM PT", "words": "1 Server shares one process", "repeats": "[]", "answer": '["a201ee6"]'}
FORWARD = {"at": "Nov 02 3:12 PM PT", "words": "dont revert capt-hook or cc-trancsript stuff fix it forward", "repeats": "[]", "answer": '["0d1102b"]'}
MISREAD = judge.Verdict("yes", "one process per destination and key", "shares one process", "He asked for one shared process, and this splits it per key.")
CARRIED = judge.Verdict("no", "", "", "It carries what he said.")


class Scripted(judge_watch.Pass):
    def __init__(self, watch: judge_watch.Watch, records: list[dict], verdicts: list[judge.Verdict | None], cited: list[dict] | None = None, rows: dict[str, dict] | None = None):
        super().__init__(watch, dry_run=False)
        self.found, self.cited, self.posts = records, cited or [], []
        self.verdicts = iter(verdicts)
        self.rows_of = {"rule/a201ee6": ROW} if rows is None else rows

    def cli(self, argv: list[str]) -> str:
        self.posts.append(argv)
        return f"#{9000 + len(self.posts)}\n"

    def rulings(self, *args: str) -> str:
        if args[0] == "rows":
            return json.dumps({"rows": self.rows_of, "state_dir": str(self.watch.drive_state)})
        if args[0] == "readback":
            return "".join(f"### {key} | order | in-force | said 1 | {row['at']}\n{judge.owner_words(key, [{'at': row['at'], 'words': row['words'], 'answered': None}])}\n\n" for key, row in self.rows_of.items())
        return ""


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


def test_a_contract_is_judged_on_every_row_it_acts_on_at_once_and_the_flag_names_the_current_row_it_breaks(watch, run):
    """Coordinator #351385: a cutover contract was judged on one older row alone, never beside his newer words on the same subject."""
    broke = judge.Verdict("yes", "revert the capt-hook hooks", "fix it forward", "He said to fix capt-hook forward, and this reverts it.")

    scripted = run(Scripted(watch, [contract(100, "Revert the capt-hook hooks, then cut over.", lane="lr-go-cutover-2")], [broke], rows={"rule/a201ee6": ROW, "rule/0d1102b": FORWARD}))

    [flag] = posted(scripted)
    assert flag["text"].startswith("JUDGE contract #100 rule/0d1102b: He said to fix capt-hook forward, and this reverts it.")
    assert [item["words"] for item in scripted.saved.flags[next(iter(scripted.saved.flags))]["said"]] == [FORWARD["words"]]


def test_a_contract_acting_on_no_row_asks_the_judge_nothing(watch, run):
    assert posted(run(Scripted(watch, [contract(100, "Order: lanes mover first. Fix forward, revert nothing.")], [], rows={}))) == []


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
    calls.write_text(json.dumps({"at": "2026-10-30T04:20:00+00:00", "kind": "contract", "subject": "cci:100", "verdict": "no", "seconds": 9}) + "\n")
    announce = {"seq": 200, "lane": "root", "kind": "decision", "text": "Register classify, 1 rows: extended rule/a201ee6.", "refs": {}}

    [flag] = posted(run(Scripted(watch, [announce], [MISREAD], cited=[contract(100)])))

    assert "after his newer words, which the judge missed before; it is now an eval case" in flag["text"]
    case = json.loads((watch.drive_state / "judge" / "misses" / "100-rule-a201ee6.json").read_text())
    assert (case["kind"], case["expect"], case["ruling"]) == ("contract", "yes", "rule/a201ee6")


def test_a_writers_answer_on_a_flag_asks_root_alone_with_both_readings_and_an_owner_pick_becomes_an_eval_case(watch, run):
    """Coordinator #263609: judge-watch ordered board-refresher lanes to post owner cards for contract-reading disputes after four coordinator rulings."""
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
    assert closed["text"] == f"JUDGE dispute on #{flag_seq} closed by coordinator #290: the port keeps the source contract (ruling #262338)"
    assert "--resolves" in second.posts[-1] and flag_seq not in second.saved.flags


def sprite_watch(tmp_path: Path) -> judge_watch.Watch:
    return judge_watch.Watch("d", "d", tmp_path / "judge-watch.json", tmp_path, "/repo", ["sprite-1", "sprite-2"], {"ANTHROPIC_API_KEY": ["printf", "x"]})


def test_a_judge_with_sprites_asks_through_a_sprite_job_and_logs_the_call_here(tmp_path, monkeypatch):
    seen = {}
    logged = []

    def job(argv, **kwargs):
        directory = Path(argv[3])
        seen.update(argv=argv, env=kwargs["env"], job=json.loads((directory / "job.json").read_text()))
        (directory / "result.json").write_text(json.dumps({"verdict": {"verdict": "yes", "artifact_phrase": "a", "evidence_phrase": "b", "sentence": "c"}, "summary": {"asks": 1}}))

    monkeypatch.setattr(judge_watch.subprocess, "run", job)
    monkeypatch.setattr(judge_watch.judge, "record", lambda *args: logged.append(args))
    monkeypatch.setattr(judge_watch.judge, "judge", lambda *args, **kwargs: pytest.fail("a model call on the Mac"))

    verdict = judge_watch.Pass(sprite_watch(tmp_path), dry_run=True).ask("contract", "his words", "the artifact", "cci:7")

    assert verdict == judge.Verdict("yes", "a", "b", "c")
    assert seen["argv"][0].endswith("sprite-job.sh") and seen["argv"][1] in ("sprite-1", "sprite-2") and seen["argv"][2].endswith("judge_job.py")
    assert json.loads(seen["env"]["SPRITE_JOB_KEYS"]) == {"ANTHROPIC_API_KEY": ["printf", "x"]}
    assert seen["job"]["drive"] == "d" and seen["job"]["checkout"] == "/repo" and seen["job"]["artifact"] == "the artifact"
    assert logged[0][3] == "yes" and logged[0][5] == "cci:7"
    assert list((tmp_path / "judge" / "jobs").iterdir()) == []


def test_a_sprite_job_that_wrote_no_result_fails_open_and_logs_the_error(tmp_path, monkeypatch):
    logged = []
    monkeypatch.setattr(judge_watch.subprocess, "run", lambda argv, **kwargs: None)
    monkeypatch.setattr(judge_watch.judge, "record", lambda *args: logged.append(args))

    assert judge_watch.Pass(sprite_watch(tmp_path), dry_run=True).ask("contract", "his words", "the artifact", "cci:7") is None
    assert logged[0][3] == "error" and "FileNotFoundError" in logged[0][4]["error"]


def test_the_job_on_the_sprite_writes_the_verdict_and_summary(tmp_path, monkeypatch):
    import judge_job

    (tmp_path / "job.json").write_text(json.dumps({"kind": "contract", "evidence": "e", "artifact": "a", "backend": "sonnet"}))
    monkeypatch.setattr(judge_job.judge, "ask", lambda *args, **kwargs: judge.Call(verdict=judge.Verdict("no", "", "", "fine")))

    assert judge_job.main(tmp_path) == 0
    written = json.loads((tmp_path / "result.json").read_text())
    assert written["verdict"]["verdict"] == "no" and written["summary"]["asks"] == 0


def test_a_pass_stopped_partway_resumes_after_the_last_record_it_judged_and_flags_nothing_twice(watch, monkeypatch):
    """Coordinator #458576: judge-watch re-flagged relays #374518 and #374519 every pass, since a pass the supervisor stopped at its time limit never saved its cursor."""
    relays = [{"seq": seq, "lane": "root", "kind": "decide", "text": "lane-a: one process per key. rule/a201ee6", "refs": {}} for seq in (101, 102, 103)]
    stops = [TimeoutError("the supervisor stopped the pass")]

    def verdict(*args, subject, **kwargs):
        if subject.startswith("cci:103") and stops:
            raise stops.pop()
        return MISREAD

    monkeypatch.setattr(judge_watch.judge, "judge", verdict)
    monkeypatch.setattr(judge_watch.judge, "said", lambda row, state, env=None: [{"at": row["at"], "words": row["words"], "answered": None}])
    monkeypatch.setattr(judge_watch.cci, "records", lambda cli, drive, *filters, since=0: [record for record in relays if record["seq"] > int(since)] if str(since).isdigit() else relays)

    with pytest.raises(TimeoutError):
        Scripted(watch, relays, []).run()
    resumed = Scripted(watch, relays, [])
    resumed.run()

    assert json.loads(watch.state.read_text())["cursor"] == 103
    assert [flag["re"] for flag in posted(resumed)] == ["103"]


def test_an_answer_on_a_relays_flag_from_the_appeals_lane_reaches_root_as_its_dispute(watch, run):
    """Coordinator #458576: grader-appeals answered judge-watch's flags on root's relays, and judge-watch read nothing, since it counted a reply only from the relay's writer."""
    first = run(Scripted(watch, [{"seq": 103, "lane": "root", "kind": "decide", "text": "lane-a: one process per key. rule/a201ee6", "refs": {}}], [MISREAD]))
    flag_seq = next(iter(first.saved.flags))
    appeal = {"seq": 300, "lane": "grader-appeals-1", "kind": "answer", "re": int(flag_seq), "text": "The override stands on his words in 28f6ade.", "refs": {}}

    [ask] = posted(run(Scripted(watch, [appeal], [])))

    assert ask["kind"] == "ask" and ask["to"] == ["root"] and ask["re"] == "300"
    card = (watch.drive_state / "judge" / "disputes" / f"{flag_seq}.md").read_text()
    assert f"The writer's reading, grader-appeals-1: {appeal['text']}" in card
