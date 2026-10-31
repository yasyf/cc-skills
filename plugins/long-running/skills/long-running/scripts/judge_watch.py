#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Judge posted work against the owner's words after it is posted, so no writer waits: one pass over the bus since the last.

    judge_watch.py --config RUNNER_JSON [--dry-run]

The orca runner starts a pass every `judge.every_minutes` (default 1) once the last has exited. A pass reads
every record since its cursor and asks the drive's judge (`long_running/judge.py`):

    contract  a lane's `contract`: does it misread his words on the rows it cites, or the two `rulings.py
              match` finds, narrower than he said or too literally?
    relay     a root `decide`: does it carry his pick on the rows it cites in full, and add nothing his words
              and the plan lines it shares words with never named?
    recheck   a root `decision` announcing `Register classify` with `extended <key>`: he added words to a
              row, so the ten newest contracts and relays of the last day that cite it are judged again under
              all his words, told to apply the reason behind his correction to items he did not name

A yes posts one `review` record, `JUDGE <kind> #<seq> <key>: <sentence>`, to the writer and root, in reply to
the artifact. A recheck yes whose earlier verdict on the same artifact and row was not a yes is a miss: it
says so, and the case is written to `judge/misses/` in the drive's state folder for `judge-eval.py`.
The writer disputes a flag by answering on it; the pass then asks the board lane for one owner card,
`judge-dispute-<flag seq>`, with his words, the judge's reading and the writer's, and his pick on that card
writes the case to `judge/owner/` with the verdict he chose. A failed judge call posts nothing.
State lives in `judge-watch.json` beside `orca.desk_inbox`, under a lock a second pass meets by exiting.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import judge
import cci
import drive

LANE = "judge-watch"
ROOT = "root"
STATE = "judge-watch.json"
EVERY_MINUTES = 1
FIRST_SINCE = "15m"
RECHECK = 10
RECHECK_WINDOW = timedelta(days=1)
ROWS_PER_ARTIFACT = 2
MATCH_CHARS = 2000
MATCH_BYTES = 6000
PLAN_LINES = 40
PLAN_WORD = re.compile(r"[a-z][a-z_.-]{5,}")
ROW_KEY = re.compile(r"\b(?:rule|prompt|exception|design|reserved)/[\w-]+")
ANSWER_ID = re.compile(r"^- ([0-9a-f]{7}) ", re.MULTILINE)
CLASSIFIED = re.compile(r"^Register classify, \d+ rows?: ")
EXTENDED = re.compile(r"\bextended ((?:rule|prompt|exception|design|reserved)/[\w-]+)")
PICKED = re.compile(r"\(judge-dispute-(?P<flag>\d+)\): picked “(?P<label>[^”]+)”")
JUDGE_RIGHT = "1 The judge's reading"
WRITER_RIGHT = "2 The writer's reading"
RELAY_KINDS = frozenset({"decide"})
POSTED = re.compile(r"#(\d+)")
SCRIPTS = Path(__file__).resolve().parent


@dataclass
class Watch:
    drive: str
    cci: str
    state: Path
    drive_state: Path
    board_lane: str
    repo: str

    @classmethod
    def load(cls, path: Path) -> Watch:
        config = json.loads(path.read_text())
        landing = config.get("landing") or {}
        board = landing.get("board_lane") or (config.get("stall") or {}).get("board_lane") or ROOT
        entry = drive.find(config["drive"], None)
        return cls(
            config["drive"],
            entry.get("cci_drive") or config["drive"],
            Path(config["orca"]["desk_inbox"]).expanduser().with_name(STATE),
            Path(entry["state_dir"]),
            board[0] if isinstance(board, list) else board,
            entry["checkout"],
        )


@dataclass
class Saved:
    cursor: int = 0
    flags: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Saved:
        return cls(**json.loads(path.read_text())) if path.is_file() else cls()


class Pass:
    def __init__(self, watch: Watch, dry_run: bool):
        self.watch, self.dry_run = watch, dry_run
        self.saved = Saved.load(watch.state)
        self.registered: dict | None = None
        self.backend = judge.backend_from(os.environ)

    def run(self) -> int:
        found = cci.records(self.cli, self.watch.cci, since=self.saved.cursor or FIRST_SINCE)
        for record in found:
            if record.get("lane") == LANE:
                continue
            if CLASSIFIED.match(record.get("text", "")) and record.get("lane") == ROOT:
                for key in dict.fromkeys(EXTENDED.findall(record["text"])):
                    self.recheck(key)
            elif record.get("kind") == "contract":
                self.judge_record(record, "contract", semantic=True)
            elif record.get("kind") in RELAY_KINDS and record.get("lane") == ROOT:
                self.judge_record(record, "relay", semantic=False)
            elif str(record.get("re")) in self.saved.flags and record.get("lane") == self.saved.flags[str(record["re"])]["lane"]:
                self.dispute(record)
            elif picked := PICKED.search(record.get("text", "")):
                self.owner_ruled(picked["flag"], picked["label"])
        if found:
            self.saved.cursor = found[-1]["seq"]
        if not self.dry_run:
            self.watch.state.write_text(json.dumps(self.saved.__dict__))
        return 0

    def cli(self, argv: list[str]) -> str:
        return subprocess.run(argv, capture_output=True, text=True, check=True, cwd=self.watch.drive_state).stdout

    def rulings(self, *args: str, stdin: str = "") -> str:
        argv = [sys.executable, str(SCRIPTS / "rulings.py"), *args, "--drive", self.watch.drive, "--repo", self.watch.repo]
        return subprocess.run(argv, input=stdin, capture_output=True, text=True, check=True, cwd=self.watch.repo).stdout

    def rows(self) -> dict:
        if self.registered is None:
            self.registered = json.loads(self.rulings("rows"))
        return self.registered

    def judge_record(self, record: dict, kind: str, semantic: bool, recheck: str | None = None) -> None:
        text = body(record)
        rows = self.rows()["rows"]
        keys = [recheck] if recheck else [key for key in dict.fromkeys(ROW_KEY.findall(text)) if key in rows] or (self.matched(text, rows) if semantic else [])
        for key in keys[:ROWS_PER_ARTIFACT]:
            if not rows.get(key, {}).get("words"):
                continue
            said = judge.said(rows[key], Path(self.rows()["state_dir"]))
            evidence = judge.owner_words(key, said) + (self.plan_lines(text) if kind == "relay" else "")
            subject = f"cci:{record['seq']}:{key}"
            before = earlier(self.watch.drive_state, subject) if recheck else None
            verdict = judge.judge(kind, evidence, text, backend=self.backend, drive=self.watch.drive, subject=subject)
            if verdict is None or not verdict.flagged:
                continue
            missed = recheck is not None and before != "yes"
            if missed:
                write_case(self.watch.drive_state / "judge" / "misses" / f"{record['seq']}-{key.replace('/', '-')}.json", kind, "yes", key, said, text)
            self.flag(record, kind, key, verdict, said, text, missed if recheck else None)

    def matched(self, text: str, rows: dict) -> list[str]:
        try:
            found = self.rulings("match", "-k", str(ROWS_PER_ARTIFACT), "--budget", str(MATCH_BYTES), stdin=text[:MATCH_CHARS])
        except subprocess.CalledProcessError:
            return []
        answers = tuple(ANSWER_ID.findall(found))
        return [key for key, row in rows.items() if answers and any(str(answer).startswith(answers) for answer in json.loads(row.get("answer") or "[]"))]

    def plan_lines(self, text: str) -> str:
        plan = self.rulings("plan").strip()
        if not plan or not Path(plan).expanduser().is_file():
            return ""
        words = set(PLAN_WORD.findall(text.lower()))
        lines = [line for line in Path(plan).expanduser().read_text().splitlines() if words & set(PLAN_WORD.findall(line.lower()))][:PLAN_LINES]
        return "\n\nPlan lines that share words with the relay:\n" + "\n".join(lines) if lines else ""

    def recheck(self, key: str) -> None:
        since = (datetime.now(timezone.utc) - RECHECK_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
        citing = [
            record
            for record in cci.records(self.cli, self.watch.cci, "--kind", "contract", "--kind", "decide", since=since)
            if key in body(record) and record.get("lane") != LANE and (record["kind"] == "contract" or record.get("lane") == ROOT)
        ]
        for record in citing[-RECHECK:]:
            self.judge_record(record, "contract" if record["kind"] == "contract" else "relay", semantic=False, recheck=key)

    def flag(self, record: dict, kind: str, key: str, verdict: judge.Verdict, said: list[dict], text: str, missed: bool | None) -> None:
        when = "" if missed is None else " after his newer words" + (", which the judge missed before; it is now an eval case" if missed else "")
        line = (
            f"JUDGE {kind} #{record['seq']} {key}{when}: {verdict.sentence} | artifact: \"{verdict.artifact_phrase}\" | his words: \"{verdict.evidence_phrase}\""
            " | fix it, or answer on this record with the owner words that carry it"
        )
        to = list(dict.fromkeys([record["lane"], ROOT]))
        posted = self.post("review", line, to, re=record["seq"])
        if posted is not None:
            self.saved.flags[str(posted)] = {"lane": record["lane"], "kind": kind, "key": key, "said": said, "artifact": text, "reading": verdict.sentence, "seq": record["seq"]}

    def dispute(self, reply: dict) -> None:
        flag = self.saved.flags[str(reply["re"])]
        card = self.watch.drive_state / "judge" / "disputes" / f"{reply['re']}.md"
        words = "\n".join(f"- {item['at']}: \"{item['words']}\"" for item in flag["said"])
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text(
            f"# Does this {flag['kind']} read your words the way you meant them?\n\n"
            f"Your words:\n{words}\n\n"
            f"The judge's reading: {flag['reading']}\n\n"
            f"The writer's reading, {flag['lane']}: {reply.get('text', '')}\n\n"
            f"What was posted:\n~~~~\n{flag['artifact'][: judge.ARTIFACT_CHARS // 3]}\n~~~~\n\n"
            f"Buttons: \"{JUDGE_RIGHT}\", \"{WRITER_RIGHT}\", plus a write-in box and Explain more. Neither is preselected.\n"
        )
        self.post("ask", f"{self.watch.board_lane}: post owner card judge-dispute-{reply['re']} 'Does this {flag['kind']} read your words the way you meant them?' with both readings; text, options and buttons in {card}", [self.watch.board_lane, ROOT], re=reply["seq"])

    def owner_ruled(self, flag_seq: str, label: str) -> None:
        if (flag := self.saved.flags.get(flag_seq)) is None:
            return
        expect = "yes" if label == JUDGE_RIGHT else "no" if label == WRITER_RIGHT else "any"
        write_case(self.watch.drive_state / "judge" / "owner" / f"{flag_seq}.json", flag["kind"], expect, flag["key"], flag["said"], flag["artifact"])

    def post(self, kind: str, text: str, to: list[str], re: int) -> int | None:
        if self.dry_run:
            print(f"{kind} -> {','.join(to)}: {text}")
            return None
        argv = ["cci", "post", "--drive", self.watch.cci, "--lane", LANE, "--kind", kind, "--re", str(re), "--text", text]
        for lane in to:
            argv += ["--to", lane]
        out = self.cli(argv)
        return int(match[1]) if (match := POSTED.search(out)) else None


def body(record: dict) -> str:
    attached = (record.get("refs") or {}).get("path")
    return "\n".join([record.get("text", ""), Path(attached).read_text() if attached and Path(attached).is_file() else ""]).strip()


def earlier(state: Path, subject: str) -> str | None:
    """The verdict the judge last reached on `subject` before this pass, from the calls log, or None when it never judged it."""
    found = [row["verdict"] for row in judge.calls(state, datetime.min.replace(tzinfo=timezone.utc)) if row.get("subject") == subject]
    return found[-1] if found else None


def write_case(path: Path, kind: str, expect: str, key: str, said: list[dict], artifact: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": kind, "expect": expect, "ruling": key, "words": said, "artifact": artifact}, indent=1, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="judge_watch.py", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--config", type=Path, required=True, help="the desk runner's runner.json")
    parser.add_argument("--dry-run", action="store_true", help="print the flags and asks; post and save nothing")
    args = parser.parse_args(argv)
    watch = Watch.load(args.config.expanduser().resolve())
    watch.state.parent.mkdir(parents=True, exist_ok=True)
    with watch.state.with_name(f"{STATE}.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"another judge watch pass holds {lock.name}", file=sys.stderr)
            return 0
        return Pass(watch, args.dry_run).run()


if __name__ == "__main__":
    drive.run_installed(__file__)
    sys.exit(main(sys.argv[1:]))
