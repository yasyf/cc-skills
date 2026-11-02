#!/usr/bin/env python3
"""`replay.py`: a tooling fix proven on the records that reported it, an install refused when it would act differently on the drive's last day, and a count of how often each fixed defect came back.

    replay.py capture --drive D --class NAME --defect SEQ --fix PR --recurs REGEX [--recurs-kind KIND]... --check lanes --lane LANE... --from SEQ --upto SEQ --expect JSON --out FIXTURE
    replay.py capture --drive D --class NAME --defect SEQ --fix PR --recurs REGEX [--recurs-kind KIND]... --check test-only --repo R --pr N... --expect JSON --out FIXTURE
    replay.py run --scripts DIR --input FILE
    replay.py gate --drive D [--repo R] --old VERSION --new VERSION
    replay.py recur --drive D

STDLIB ONLY. A fixture is one JSON file under the plugin's `tests/fixtures/replay/`, named for its defect
class: the cci record that reported the defect, the PR that fixed it, the `recurs` pattern a later report of
the same defect matches, the input the check read, and what the fixed pack must decide on that input. The
input holds cci records with the `cci who` rows of their lanes for the lanes mover, and each PR's changed
files with their patches for the landing runner's class check. `capture` writes one from the drive's own
records, and CI replays every fixture through `run`.

`run` loads the lanes mover and the class check from the pack at `--scripts`, so one input runs through any
installed version. It ticks the lanes mover over a fresh in-memory store every `lr.loop.TICK` from the
first record's time to the last, feeding each record once its time has passed and reading each `cci who`
row as it stood at the tick: a session seen after the tick was live at it, one announced after it did not
exist yet, and a stand-down cci marks on a row counts only once a stand-down record names its lane. Each
spawned lane's brief is a stub, and no network is read. It prints one JSON object: the lanes the mover
relaunched and held, and the PRs whose test-only claim the class check refused.

`gate` is the install step, run by `desk-runner.py handoff --version` and by the supervisor before it
re-executes into a newer install. It replays the drive's last 24 hours, with the current `cci who` rows
and the changed files of every PR whose test-only claim the class check judged in them, through `--old`
and `--new`; the 48 hours before fold in first, so lanes spawned then are registered when the day opens. When `--new`
relaunches a lane or refuses a PR `--old` did not, it posts one `blocker` to root naming each and exits 3;
otherwise it posts one `state` record, `REPLAY-PASSED`, naming the fixture classes `--new` adds, and exits
0. Each verdict posts once per version.

`recur` is the orca desk's hourly pass. For each `REPLAY-PASSED` record of the last 72 hours whose 24 hours
have run, it posts one `report` to root per fixture class that install added: how many records in those 24
hours match the class's `recurs` pattern, and which.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from long_running import cci, drive, ghread

SCRIPTS = Path(__file__).resolve().parent
FIXTURES = drive.PLUGIN / "tests" / "fixtures" / "replay"
INSTALLED_SCRIPTS = Path("skills") / "long-running" / "scripts"
LANE = "replay"
DAY = timedelta(hours=24)
WINDOW = "24h"
HISTORY = "72h"
RECUR_WINDOW = "72h"
REFUSED = 3
PASSED = "REPLAY-PASSED"
CLASS_CHECKED = re.compile(r"\bTEST-ONLY-MISMATCH\b|\bEXEMPT-ADMITTED\b.*\b(?:test-only|ci-only|exempt: (?:tests?|ci|harness|fixtures?))\b", re.IGNORECASE)
LAUNCHED = re.compile(r"^LAUNCHED \S+ (?P<lane>[\w.-]+): dispatch (?P<dispatch>ctx_\w+)")
ENDED = re.compile(r"^OUTCOME \S+ (?P<lane>[\w.-]+): worker_done \w+ dispatch=(?P<dispatch>ctx_\w+)")
FILES_JQ = ".[] | [.filename, .patch] | @json"
READ_TEXT = (
    LAUNCHED,
    ENDED,
    re.compile(r"\bRELAUNCHED [\w.-]+ as [\w.-]+"),
    re.compile(r"\bstand[ -]?down\s+[^.(;:\n]+", re.IGNORECASE),
    re.compile(r"\b[\w.-]+ replaces [\w.-]+\b"),
    re.compile(r"\bhandoff [\w.-]+: doc [0-9a-f]{7,40}\b"),
    re.compile(r"\bowner=[\w.-]+"),
    re.compile(r"\bcard-[\w.-]*\w"),
    re.compile(r"#\d+\b"),
    re.compile(r"\bPARKED [\w.-]+:"),
)
KEPT_FIELDS = ("seq", "at", "lane", "kind", "to", "re", "resolves", "also_resolves", "topic")
KEPT_ATTRS = ("brief", "model", "effort", "role", "successor")
KEPT_ROW = ("lane", "live", "seen_at", "announced_at", "stood_down", "roster")


def at_of(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def shell(argv: list[str]) -> str:
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"{' '.join(argv[:4])} exited {done.returncode}: {(done.stderr or done.stdout).strip()[-300:]}")
    return done.stdout


def who_rows(drive_name: str) -> list[dict]:
    return [json.loads(line) for line in shell(["cci", "who", "--drive", drive_name, "--all", "--json"]).splitlines() if line.strip()]


def changed_files(repo: str, pr: int) -> dict[str, str | None]:
    return dict(json.loads(line) for line in shell(ghread.read("api", "--paginate", f"repos/{repo}/pulls/{pr}/files", "--jq", FILES_JQ)).splitlines() if line.strip())


def prs_of(record: dict) -> list[int]:
    return [int(str(ref).rsplit("#", 1)[-1]) for ref in (record.get("refs") or {}).get("prs") or []]


def lanes_of(records: list[dict]) -> set[str]:
    return {name for record in records for name in [record["lane"], *(to.split("@", 1)[0] for to in record.get("to") or [])]}


def about(record: dict, wanted: set[str]) -> bool:
    """Whether `record` was written by, is addressed to, or names one of the `wanted` lanes, as a `SPAWNED` line or a runner's `OUTCOME` names its lane."""
    return bool(lanes_of([record]) & wanted) or any(re.search(rf"(?<![\w.-]){re.escape(lane)}(?![\w.-])", record.get("text") or "") for lane in wanted)


def load_pack(scripts: Path):
    """The lanes mover, its store and tick, and the landing runner as the pack at `scripts` ships them, with every module this file loaded from its own pack dropped first, so an older pack never runs against this one's modules."""
    for name, module in list(sys.modules.items()):
        if getattr(module, "__file__", None) and Path(module.__file__).resolve().is_relative_to(SCRIPTS):
            del sys.modules[name]
    sys.path.insert(0, str(scripts))
    from lr import loop
    from lr.movers import lanes
    from lr.store import Store

    spec = importlib.util.spec_from_file_location("desk_runner", scripts / "desk-runner.py")
    runner = sys.modules[spec.name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return loop, lanes, Store, runner


def stubbed(records: list[dict], briefs: Path) -> list[dict]:
    """Each record with every `SPAWNED` brief path pointed at a stub brief under `briefs` in a folder of the same name, since a replay reads no brief the drive wrote and the mover reads a `-fix` lane's posting name from its brief's folder."""
    out = []
    for record in records:
        text = record.get("text") or ""
        if record["kind"] == "state" and text.startswith("SPAWNED "):
            name, written = text.split()[1], Path(((record.get("fields") or {}).get("attrs") or {}).get("brief") or text.split()[2])
            stub = briefs / "spawned" / written.parent.name / written.name
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text(f"# {name}\nRegister slice: lane={name} step=all\n")
            attrs = ((record.get("fields") or {}).get("attrs") or {}) | {"brief": str(stub)}
            record = record | {"text": f"SPAWNED {name} {stub}", "fields": (record.get("fields") or {}) | {"attrs": attrs}}
        out.append(record)
    return out


def dispatches(feed: list[dict]) -> list[tuple[str, datetime, datetime | None]]:
    """Each Orca dispatch the runner's `LAUNCHED` and `OUTCOME` records name: its lane, when it launched, and when its worker finished."""
    ended = {found["dispatch"]: at_of(record["at"]) for record in feed if (found := ENDED.match(record.get("text") or ""))}
    return [(found["lane"], at_of(record["at"]), ended.get(found["dispatch"])) for record in feed if (found := LAUNCHED.match(record.get("text") or ""))]


def who_at(rows: list[dict], orca: list[tuple[str, datetime, datetime | None]], stood_down: dict[str, datetime], now: datetime) -> list[dict]:
    """The `cci who` rows as they stood at `now`, read from the rows as they stand at the end of the replay, and one Orca row per dispatch launched by then, live until its worker finished, as `drive_supervisor.orca_rows` reads them."""
    out = [{"lane": lane, "live": ended is None or ended > now, "seen_at": iso(now if ended is None or ended > now else launched)} for lane, launched, ended in orca if launched <= now]
    for row in rows:
        if at_of(row.get("announced_at") or row["seen_at"]) > now:
            continue
        lane = row.get("lane") or ""
        row = row | {"stood_down": bool(row.get("stood_down")) and lane in stood_down and stood_down[lane] <= now}
        out.append(row | {"live": True, "seen_at": iso(now)} if at_of(row["seen_at"]) > now else row)
    return out


def replay_lanes(pack, records: list[dict], rows: list[dict], since: str | None) -> dict[str, list[str]]:
    """The lanes the mover relaunched, held and tried to wake, ticking from `since`, or the first record, to the last record; records before `since` fold in at the first tick, so lanes spawned earlier are registered when the compared window opens."""
    loop, lanes, Store, _ = pack
    if not records:
        return {"relaunched": [], "held": [], "woken": []}
    with tempfile.TemporaryDirectory() as scratch:
        briefs = Path(scratch)
        feed = sorted(stubbed(records, briefs), key=lambda record: record["seq"])
        stood_down = {}
        for record in feed:
            if record["kind"] == "stand-down":
                for to in record.get("to") or []:
                    stood_down.setdefault(to.split("@", 1)[0], at_of(record["at"]))
        orca = dispatches(feed)
        woken: set[str] = set()
        clock = {"now": max(at_of(feed[0]["at"]), at_of(since or feed[0]["at"])), "shown": 0}

        def visible(since: int) -> list[dict]:
            start = clock["shown"]
            while clock["shown"] < len(feed) and at_of(feed[clock["shown"]]["at"]) <= clock["now"]:
                clock["shown"] += 1
            return [record for record in feed[start : clock["shown"]] if record["seq"] > since]

        offered = {
            "drive": "replay",
            "checkout": scratch,
            "briefs": briefs / "lanes",
            "records": visible,
            "who": lambda names: who_at(rows, orca, stood_down, clock["now"]),
            "tokens": lambda row: None,
            "transcript": lambda row: None,
            "cci": lambda argv: "",
            "launch": lambda name, model, effort, brief: None,
            "withdraw": lambda name: None,
            "wake": lambda name, row, page: woken.add(name) or False,
            "pr_states": lambda prs: {},
            "rotate_at": 0,
        }
        deps = lanes.Deps(**{field.name: offered[field.name] for field in dataclasses.fields(lanes.Deps)})
        store = Store.memory()
        store.advance(lanes.CURSOR, feed[0]["seq"] - 1)
        relaunched, held = set(), set()
        end = at_of(feed[-1]["at"])
        while True:
            out = lanes.tick(store, deps, clock["now"], 1)
            relaunched |= {old for old, *_ in out.relaunched}
            held |= set(out.held)
            if clock["now"] >= end:
                break
            clock["now"] = min(clock["now"] + loop.TICK, end)
        return {"relaunched": sorted(relaunched), "held": sorted(held), "woken": sorted(woken)}


def replay_class_check(pack, prs: list[dict]) -> list[int]:
    runner = pack[3]
    return sorted(entry["pr"] for entry in prs if runner.test_only_offender(entry["patches"]))


def replay(scripts: Path, given: dict) -> dict:
    pack = load_pack(scripts)
    return replay_lanes(pack, given.get("records") or [], given.get("who") or [], given.get("since")) | {"refused": replay_class_check(pack, given.get("prs") or [])}


def run_pack(scripts: Path, given: Path) -> dict:
    return json.loads(shell([sys.executable, str(Path(__file__).resolve()), "run", "--scripts", str(scripts), "--input", str(given)]))


def installed(version: str) -> Path:
    return drive.installs() / version / INSTALLED_SCRIPTS


def classes(version: str) -> dict[str, dict]:
    folder = drive.installs() / version / "tests" / "fixtures" / "replay"
    return {path.stem: json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))} if folder.is_dir() else {}


def differences(old: dict, new: dict) -> list[str]:
    """Each decision `new` makes on the replayed day that `old` did not: a relaunch or a class-check refusal."""
    return [
        *(f"relaunches {lane}" for lane in sorted(set(new["relaunched"]) - set(old["relaunched"]))),
        *(f"refuses #{pr}'s test-only claim" for pr in sorted(set(new["refused"]) - set(old["refused"]))),
    ]


def post(drive_name: str, kind: str, key: str, text: str, *flags: str) -> None:
    shell(["cci", "post", "--drive", drive_name, "--lane", LANE, "--kind", kind, "--to", "root", "--id", key, *flags, "--stamp", "--text", text])


def brief_path(path: str) -> str:
    return f"/scratch/{Path(path).parent.name}/{Path(path).name}"


def scrubbed_text(text: str) -> str:
    """The parts of a record's text the lanes mover reads, joined by `; `, so a fixture in this public repository carries no drive prose: a `SPAWNED` line with its brief's folder and name, runner launch and outcome lines, stand-downs, replacements, handoffs, owners, cards, `#N` mentions, and a closing question mark."""
    if text.startswith("SPAWNED "):
        name, path = text.split()[1:3]
        return f"SPAWNED {name} {brief_path(path)}"
    kept = dict.fromkeys(found[0] for pattern in READ_TEXT for found in pattern.finditer(text))
    return "; ".join(kept) + ("?" if text.rstrip().endswith("?") else "")


def scrubbed(record: dict) -> dict:
    attrs = {key: brief_path(value) if key == "brief" else value for key, value in ((record.get("fields") or {}).get("attrs") or {}).items() if key in KEPT_ATTRS}
    return {key: record[key] for key in KEPT_FIELDS if record.get(key) is not None} | {"text": scrubbed_text(record.get("text") or ""), "refs": {"prs": prs_of(record)}, "fields": {"attrs": attrs}}


def scrubbed_patch(runner, name: str, patch: str | None) -> str | None:
    """What the class check reads of one file's patch: nothing for a file it reads by name alone, each changed line's sign for a lockfile, and a manifest's hunk starts and section lines with every other line's content dropped."""
    base = Path(name).name
    if patch is None or base not in runner.MANIFESTS | runner.LOCKFILES:
        return None
    sections = (runner.TOML_SECTION, runner.JSON_SECTION, runner.JSON_CLOSE)
    lines = ["@@" if line.startswith("@@") else line if base in runner.MANIFESTS and any(pattern.match(line[1:]) for pattern in sections) else f"{line[:1]}x" for line in patch.splitlines()]
    return "\n".join(lines) + "\n"


def cmd_capture(args: argparse.Namespace) -> int:
    expect = json.loads(args.expect)
    if args.check == "lanes":
        found = [scrubbed(record) for record in cci.paged(shell, args.drive, (), args.start - 1) if record["seq"] <= args.upto and about(record, set(args.lane))]
        given = {"records": found, "who": [{key: row.get(key) for key in KEPT_ROW} for row in who_rows(args.drive) if row.get("lane") in set(args.lane)]}
    else:
        runner = load_pack(SCRIPTS)[3]
        given = {"prs": [{"pr": pr, "patches": {name: scrubbed_patch(runner, name, patch) for name, patch in changed_files(args.repo, pr).items()}} for pr in args.pr]}
    fixture = {"class": args.cls, "defect": args.defect, "fix": args.fix, "recurs": {"kinds": args.recurs_kind, "text": args.recurs}, "input": given, "expect": expect}
    Path(args.out).write_text(json.dumps(fixture, indent=1, sort_keys=True) + "\n")
    print(f"replay: wrote {args.out} with {len(given.get('records') or given.get('prs') or [])} {'records' if args.check == 'lanes' else 'PRs'}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    print(json.dumps(replay(Path(args.scripts), json.loads(Path(args.input).read_text()))))
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    start = datetime.now(UTC) - DAY
    records = cci.paged(shell, args.drive, (), HISTORY)
    judged = sorted({pr for record in records if at_of(record["at"]) >= start and CLASS_CHECKED.search(record.get("text") or "") for pr in prs_of(record)}) if args.repo else []
    given = {"records": records, "since": iso(start), "who": who_rows(args.drive), "prs": [{"pr": pr, "patches": changed_files(args.repo, pr)} for pr in judged]}
    with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
        json.dump(given, handle)
        handle.flush()
        old, new = run_pack(installed(args.old), Path(handle.name)), run_pack(installed(args.new), Path(handle.name))
    replayed = f"{sum(at_of(record['at']) >= start for record in records):,} records and {len(judged)} test-only class checks of the last 24 hours"
    if found := differences(old, new):
        why = f"{args.new} replayed {replayed} against {args.old} and {'; '.join(found)}, which {args.old} did not, so it is not installed"
        post(args.drive, "blocker", f"replay-refused:{args.new}", f"REPLAY-REFUSED {why}", "--attr", f"version={args.new}")
        print(why)
        return REFUSED
    added = {name: fixture for name, fixture in classes(args.new).items() if name not in classes(args.old)}
    fixes = ", ".join(f"{name} (defect #{fixture['defect']}, {fixture['fix']})" for name, fixture in added.items()) or "no new fixture class"
    post(args.drive, "state", f"replay-passed:{args.new}", f"{PASSED} {args.new} replayed {replayed} against {args.old} with no new relaunch or refusal; it installs {fixes}", "--attr", f"version={args.new}", "--attr", f"classes={','.join(added)}")
    return 0


def matches(pattern: dict, record: dict) -> bool:
    return record["lane"] != LANE and (not pattern["kinds"] or record["kind"] in pattern["kinds"]) and re.search(pattern["text"], record.get("text") or "", re.IGNORECASE) is not None


def cmd_recur(args: argparse.Namespace) -> int:
    records = cci.paged(shell, args.drive, (), RECUR_WINDOW)
    now = datetime.now(UTC)
    for install in records:
        attrs = (install.get("fields") or {}).get("attrs") or {}
        if install["lane"] != LANE or PASSED not in (install.get("text") or "") or now - at_of(install["at"]) < DAY:
            continue
        start = at_of(install["at"])
        for name in filter(None, attrs.get("classes", "").split(",")):
            fixture = classes(attrs["version"])[name]
            seen = [record for record in records if start <= at_of(record["at"]) < start + DAY and matches(fixture["recurs"], record)]
            listed = ": " + " ".join(f"#{record['seq']}" for record in seen) if seen else ""
            post(args.drive, "report", f"recurrence:{attrs['version']}:{name}", f"RECURRENCE {name}: {len(seen)} in the 24 hours after {attrs['version']} installed (defect #{fixture['defect']}, fix {fixture['fix']}){listed}", "--attr", f"class={name}", "--count", f"recurrences={len(seen)}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="replay.py")
    verbs = parser.add_subparsers(dest="verb", required=True)
    capture = verbs.add_parser("capture", help="write a fixture from the records that reported a defect")
    capture.add_argument("--drive", required=True)
    capture.add_argument("--class", dest="cls", required=True)
    capture.add_argument("--defect", type=int, required=True)
    capture.add_argument("--fix", required=True)
    capture.add_argument("--recurs", required=True)
    capture.add_argument("--recurs-kind", action="append", default=[])
    capture.add_argument("--check", choices=("lanes", "test-only"), required=True)
    capture.add_argument("--lane", action="append", default=[])
    capture.add_argument("--from", dest="start", type=int, default=1)
    capture.add_argument("--upto", type=int, default=sys.maxsize)
    capture.add_argument("--repo", default="")
    capture.add_argument("--pr", type=int, action="append", default=[])
    capture.add_argument("--expect", required=True)
    capture.add_argument("--out", required=True)
    capture.set_defaults(cmd=cmd_capture)
    run = verbs.add_parser("run", help="replay one input through the pack at --scripts")
    run.add_argument("--scripts", required=True)
    run.add_argument("--input", required=True)
    run.set_defaults(cmd=cmd_run)
    gate = verbs.add_parser("gate", help="refuse an install that acts differently on the drive's last 24 hours")
    gate.add_argument("--drive", required=True)
    gate.add_argument("--repo", default="")
    gate.add_argument("--old", required=True)
    gate.add_argument("--new", required=True)
    gate.set_defaults(cmd=cmd_gate)
    recur = verbs.add_parser("recur", help="count each fixed defect's recurrences in the 24 hours after its install")
    recur.add_argument("--drive", required=True)
    recur.set_defaults(cmd=cmd_recur)
    args = parser.parse_args(argv)
    return args.cmd(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
