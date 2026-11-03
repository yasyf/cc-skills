from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, on
from captain_hook.util import reqenv

from .compaction_handoff import bus_name

LR = Path(__file__).parents[2] / "bin" / "lr"
DRIVE = "durable-agents-rebuild"
GATE_LANE = "switch-drive-1"
ROOT_LANE = "root"
NOTE_LANE = "gate5"
OVERRIDE = "GATE5_OWNER_OVERRIDE"
OWNER_LANES = frozenset({"owner", "owner-relay"})
RELEASE_VERBS = frozenset({"start", "hotfix"})
VALUE_FLAGS = frozenset({"without", "only", "stack", "commit", "from"})
SAND_NEXT = "sand-next"
NAMES_SAND_NEXT = re.compile(r"(?:^|/)sand-next(?:/|$)")
PASS = re.compile(r"GATE5 PASS ([0-9a-fA-F]{7,40})\b")
READ_SECONDS = 20
SHA = "dc94738e5a1f0b2c3d4e5f60718293a4b5c6d7e8"
OTHER = "0eab10ae6a77c2d9e1f3a5b7c9d1e3f5a7b9c1d3"
CONFIG = {"CLAUDE_CONFIG_DIR": str(Path(__file__).parent / "tests" / "fixtures" / "gate5-config")}
ROUTE = f"{LR} route {GATE_LANE}"
GREP = "cci grep"

UNGATED = (
    "GATE5: this release carries sand-next at {sha}, and switch-drive-1 has posted no `GATE5 PASS {sha}` on drive durable-agents-rebuild"
    " in the last 12 hours. Ask switch-drive-1 for a gate run on {sha}, or start the release with `--without sand-next`."
)
UNCHECKABLE = (
    "GATE5: a {what} that carries sand-next names no commit the gate can check against switch-drive-1's `GATE5 PASS <sha>` records."
    " Start it with `--without sand-next`, or start a release that names its commit with `--from <sha>` once switch-drive-1 posts `GATE5 PASS <sha>` for it."
)
UNREAD = (
    "GATE5 fails closed: it could not read whether switch-drive-1 passed this sand-next release ({why})."
    " Fix the read and retry, or start the release with `--without sand-next`."
)
NOT_OWNER = "GATE5: {OVERRIDE}={seq} names a record from `{lane}`, and only an owner record from `owner` or `owner-relay` overrides the gate."
OVERRIDDEN = "GATE5 OVERRIDE: `{command}` carries sand-next without a gate pass, under owner record #{seq}."


class Unread(Exception):
    pass


def read(argv: list[str], cwd: str | None = None) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=READ_SECONDS, cwd=cwd, env=dict(reqenv.env_map()))
    except (OSError, subprocess.TimeoutExpired) as failed:
        raise Unread(f"`{' '.join(argv[:2])}`: {failed}") from failed
    if done.returncode:
        raise Unread(f"`{' '.join(argv[:2])}` exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def release_flags(args: tuple[str, ...]) -> dict[str, list[str]]:
    flags: dict[str, list[str]] = {}
    words = iter(args)
    for word in words:
        if not word.startswith("--"):
            continue
        name, inline, value = word[2:].partition("=")
        flags.setdefault(name, [])
        if name in VALUE_FLAGS:
            flags[name].append(value if inline else next(words, ""))
    return flags


def names(flags: dict[str, list[str]], flag: str) -> list[str]:
    return [name for value in flags.get(flag, ()) for name in (part.strip() for part in value.split(",")) if name]


def carries_sand_next(flags: dict[str, list[str]]) -> bool:
    if SAND_NEXT in names(flags, "without"):
        return False
    narrowed = names(flags, "only") + names(flags, "stack")
    return not narrowed or any(NAMES_SAND_NEXT.search(name) for name in narrowed)


def released_commit(flags: dict[str, list[str]], cwd: str) -> str:
    ref = (flags.get("commit") or flags.get("from") or [""])[-1]
    if not ref and "manual" not in flags and "local" not in flags:
        dev = read(["git", "ls-remote", "origin", "refs/heads/dev"], cwd).split()
        if not dev:
            raise Unread("origin has no dev branch")
        return dev[0].lower()
    return read(["git", "rev-parse", "--verify", f"{ref or 'HEAD'}^{{commit}}"], cwd).strip().lower()


def gate_lanes() -> list[str]:
    drives = Path(reqenv.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "long-running" / "drives"
    entry = next((entry for path in sorted(drives.glob("*.json")) if bus_name(entry := json.loads(path.read_text())) == DRIVE), None)
    if entry is None:
        raise Unread(f"no drive {DRIVE} is registered under {drives}")
    routed = json.loads(read([str(LR), "route", GATE_LANE, "--config", str(Path(entry["state_dir"]) / "lr.json")]))["route"]
    return list(dict.fromkeys(lane for lane in (GATE_LANE, routed) if lane != ROOT_LANE))


def gate_passed(sha: str) -> bool:
    lanes = [arg for lane in gate_lanes() for arg in ("--lane", lane)]
    said = read(["cci", "grep", "--drive", DRIVE, *lanes, "--since", "12h", "--json", "--budget", "262144", "-F", "--ignore-case=false", "--", "GATE5 PASS"])
    return any((found := PASS.match(json.loads(line)["text"])) and sha.startswith(found[1].lower()) for line in said.splitlines() if line.strip())


def overridden(call: Call, seq: str) -> str | None:
    record = json.loads(read(["cci", "show", seq.lstrip("#"), "--json"]).splitlines()[0])
    if record["lane"] not in OWNER_LANES:
        return NOT_OWNER.format(OVERRIDE=OVERRIDE, seq=seq, lane=record["lane"])
    read(["cci", "post", "--drive", DRIVE, "--lane", NOTE_LANE, "--kind", "note", "--to", ROOT_LANE, "--text", OVERRIDDEN.format(command=call.command.raw, seq=seq.lstrip("#"))])
    return None


def gated(call: Call, cwd: str) -> str | None:
    flags = release_flags(call.args[2:])
    if not carries_sand_next(flags):
        return None
    if seq := dict(call.command.env).get(OVERRIDE):
        return overridden(call, seq)
    if call.args[1] == "hotfix" or "green" in flags:
        return UNCHECKABLE.format(what="hotfix" if call.args[1] == "hotfix" else "`--green` release")
    sha = released_commit(flags, cwd)
    return None if gate_passed(sha) else UNGATED.format(sha=sha[:12])



def passes(sha: str, lane: str = GATE_LANE) -> str:
    return json.dumps({"seq": 357100, "lane": lane, "kind": "report", "text": f"GATE5 PASS {sha[:12]} sand-next scenarios green"})


def release(command: str, said: str | tuple[int, str] = "", routed: str = GATE_LANE, env: dict[str, str] = CONFIG, **commands: str | tuple[int, str]) -> Input:
    stubs = {
        "git ls-remote": f"{SHA}\trefs/heads/dev\n",
        "git rev-parse": f"{SHA}\n",
        ROUTE: json.dumps({"lane": GATE_LANE, "route": routed, "state": "desired", "standing": ""}),
        f"{GREP} --drive {DRIVE} --lane {GATE_LANE}" + (f" --lane {routed}" if routed not in (GATE_LANE, ROOT_LANE) else " --since"): said,
    }
    return Input(command=command, env=env, commands=stubs | commands)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command="tools/ci release start sand --without=web,sand-next,api"): Allow(),
        Input(command="tools/ci release start --groups platform --without sand-next"): Allow(),
        Input(command="tools/ci release start sand --only api,web"): Allow(),
        Input(command="tools/ci release start --manual --stack api/plat-usw2-prod --commit dc94738"): Allow(),
        Input(command="tools/ci release hotfix 48885 --without sand-next"): Allow(),
        Input(command="tools/ci release status"): Allow(),
        release("tools/ci release start sand"): Block(pattern=r"^GATE5: this release carries sand-next at dc94738e5a1f, and switch-drive-1 has posted no"),
        release("./tools/ci release start sand --without sand-next/core-usw2-auto"): Block(pattern=r"Ask switch-drive-1 for a gate run"),
        release("tools/ci release start sand", passes(SHA)): Allow(),
        release("cd /repo && ci release start --targets sand --from dc94738", passes(SHA)): Allow(),
        release("tools/ci release start sand", passes(OTHER)): Block(pattern=r"^GATE5: this release carries sand-next"),
        release("tools/ci release start sand", passes(SHA, "switch-drive-2"), routed="switch-drive-2"): Allow(),
        release("tools/ci release start sand", passes(SHA), routed=ROOT_LANE): Allow(),
        release("tools/ci release start --manual --stack sand-next/core-usw2-auto", passes(OTHER)): Block(pattern=r"^GATE5: this release carries sand-next at dc94738e5a1f,"),
        release("tools/ci release start sand", (1, "cci: daemon unavailable")): Block(pattern=r"^GATE5 fails closed: it could not read[\s\S]*daemon unavailable"),
        release("tools/ci release start sand", **{"git ls-remote": (128, "fatal: could not read from remote")}): Block(pattern=r"^GATE5 fails closed[\s\S]*could not read from remote"),
        release("tools/ci release start sand", env={"CLAUDE_CONFIG_DIR": "/nonexistent"}): Block(pattern=r"no drive durable-agents-rebuild is registered"),
        release("ci release hotfix 48885"): Block(pattern=r"^GATE5: a hotfix that carries sand-next names no commit"),
        release("tools/ci release start sand --green"): Block(pattern=r"^GATE5: a `--green` release"),
        release(
            "GATE5_OWNER_OVERRIDE=356990 tools/ci release start sand",
            **{"cci show 356990": json.dumps({"seq": 356990, "lane": "owner-relay", "kind": "answer", "text": "ship sand-next now"}), "cci post": "#357200"},
        ): Allow(),
        release("GATE5_OWNER_OVERRIDE=356990 tools/ci release start sand", **{"cci show 356990": json.dumps({"seq": 356990, "lane": "release-6", "kind": "note", "text": "x"})}): Block(
            pattern=r"names a record from `release-6`"
        ),
        release("GATE5_OWNER_OVERRIDE=356990 tools/ci release start sand", **{"cci show 356990": (1, "no record has this number")}): Block(pattern=r"could not read"),
    },
)
def sand_next_release_needs_gate5_pass(evt: BaseHookEvent) -> HookResult | None:
    for call in evt.command.calls("ci"):
        if call.args[:1] != ("release",) or call.args[1:2] == () or call.args[1] not in RELEASE_VERBS:
            continue
        try:
            refusal = gated(call, str(call.cwd or evt.cwd))
        except (Unread, ValueError, KeyError) as failed:
            refusal = UNREAD.format(why=failed)
        if refusal:
            return evt.block(refusal)
    return None
